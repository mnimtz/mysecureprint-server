"""Slim Resend mail sender for mysecureprint-server.

Replaces the deleted `reporting.mail_client` module from the slim-down.
Resend (https://resend.com) is a HTTP-API-based provider — POST to
`https://api.resend.com/emails` with `Authorization: Bearer <api_key>`,
JSON body `{from, to, subject, html}`. Returns 200 on success.

No SMTP, no port-25-egress required (Azure App Service blocks SMTP
outbound by default). Pure HTTPS to api.resend.com:443.
"""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from typing import Sequence

logger = logging.getLogger("printix.mail")


class MailSendError(Exception):
    pass


def send_report(
    recipients: Sequence[str],
    subject: str,
    html_body: str,
    api_key: str,
    mail_from: str,
    mail_from_name: str = "",
    timeout: int = 15,
) -> dict:
    """Sende eine HTML-Mail über die Resend-API.

    Args:
        recipients: Liste von Email-Adressen.
        subject: Betreff.
        html_body: HTML-Body.
        api_key: Resend API-Key (re_...).
        mail_from: Absender-Email (muss bei Resend einer verifizierten
            Domain entstammen — sonst 403).
        mail_from_name: Optionaler Absender-Name (zeigt in der Mail).
        timeout: HTTP-Timeout in Sekunden.

    Returns dict mit der Resend-Antwort `{id: "..."}`.
    Raises MailSendError bei Fehler — Caller fängt + loggt.
    """
    if not api_key or not api_key.strip():
        raise MailSendError(
            "No Resend API key configured. Set tenant.mail_api_key or "
            "global_mail_api_key in /admin/settings."
        )
    if not mail_from or "@" not in mail_from:
        raise MailSendError(
            "Invalid sender address. Set tenant.mail_from or "
            "global_mail_from in /admin/settings."
        )
    if not recipients:
        raise MailSendError("No recipients given.")

    from_header = (
        f"{mail_from_name.strip()} <{mail_from.strip()}>"
        if mail_from_name.strip() else mail_from.strip()
    )
    payload = json.dumps({
        "from":    from_header,
        "to":      list(recipients),
        "subject": subject,
        "html":    html_body,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "User-Agent":    "mysecureprint-server/0.5.7",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(body)
            except Exception:
                data = {"raw": body}
            logger.info("mail: sent OK to %d recipient(s), id=%s",
                         len(recipients), data.get("id", "?"))
            return data
    except urllib.error.HTTPError as he:
        err_body = ""
        try:
            err_body = he.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        msg = (f"Resend API HTTP {he.code}: {err_body}"
               if err_body else f"Resend API HTTP {he.code}")
        logger.warning("mail: send failed — %s", msg)
        raise MailSendError(msg) from he
    except urllib.error.URLError as ue:
        logger.warning("mail: network error — %s", ue)
        raise MailSendError(f"network error: {ue.reason}") from ue
    except Exception as e:
        logger.warning("mail: unexpected error — %s", e)
        raise MailSendError(str(e)) from e


# ─── Microsoft Graph (O365) Mail-Sender ─────────────────────────────
# Alternative zu Resend: nutzt die im Tenant ohnehin registrierte
# Entra-App (per auto_register_app `include_mail_send=True`) und
# verschickt ueber /users/{from}/sendMail mit Application-Permission
# `Mail.Send`. Vorteil: keine Resend-Subscription noetig, eigene
# Domain als Absender, Auditierbar in Exchange Online.
#
# WICHTIG: in Exchange Online sollte zusaetzlich eine
# `New-ApplicationAccessPolicy` mit Scope=`RestrictAccess` auf genau
# die Service-Mailbox angewendet werden — sonst kann die App-Identitaet
# theoretisch von jeder Mailbox aus senden.

_GRAPH_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


def _graph_app_token(
    tenant_id: str,
    client_id: str,
    client_secret: str,
    timeout: int = 15,
) -> str:
    """Holt ein App-Only-Token via Client-Credentials-Flow.

    Cached pro (tenant, client) ~50 Minuten (Standard-Lifetime ist
    60min — wir sparen uns 10min Sicherheitsmarge).
    """
    import time
    key = f"{tenant_id}|{client_id}"
    now = time.time()
    cached = _GRAPH_TOKEN_CACHE.get(key)
    if cached and cached[1] > now + 60:
        return cached[0]

    import urllib.parse as _up
    body = _up.urlencode({
        "client_id":     client_id,
        "client_secret": client_secret,
        "grant_type":    "client_credentials",
        "scope":         "https://graph.microsoft.com/.default",
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept":       "application/json",
            "User-Agent":   "mysecureprint-server/0.7.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as he:
        err_body = ""
        try:
            err_body = he.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        raise MailSendError(
            f"Graph token HTTP {he.code}: {err_body or '(no body)'}"
        ) from he
    except urllib.error.URLError as ue:
        raise MailSendError(f"Graph token network error: {ue.reason}") from ue

    token = data.get("access_token", "")
    if not token:
        raise MailSendError(f"Graph token response without access_token: {data}")
    expires_in = int(data.get("expires_in", 3600) or 3600)
    _GRAPH_TOKEN_CACHE[key] = (token, now + max(expires_in - 600, 60))
    return token


def send_via_graph(
    recipients: Sequence[str],
    subject: str,
    html_body: str,
    *,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    sender_mailbox: str,
    timeout: int = 20,
) -> dict:
    """Sendet eine HTML-Mail ueber Microsoft Graph /users/{from}/sendMail.

    Verwendet Application-Permission `Mail.Send` (Admin-Consent noetig).
    NICHT On-Behalf-Of — der Absender muss eine echte Mailbox im Tenant
    sein. Erfolgreicher Versand: HTTP 202 Accepted (leerer Body).

    Args:
        recipients:     Liste von Empfaenger-Email-Adressen.
        subject:        Betreff.
        html_body:      HTML-Body.
        tenant_id:      Entra Tenant-ID (GUID).
        client_id:      App-Client-ID (GUID).
        client_secret:  App-Secret.
        sender_mailbox: UPN oder Adresse der Sende-Mailbox
                        (z.B. `noreply@firma.onmicrosoft.com`).
                        Muss eine Exchange-Online-Mailbox sein.
        timeout:        HTTP-Timeout pro Call.

    Returns: dict — leer bei Erfolg, ggf. `{status: 202}`.
    Raises MailSendError bei jedem Fehler — Caller faengt + loggt.
    """
    if not tenant_id or not client_id or not client_secret:
        raise MailSendError(
            "Graph mail: tenant_id, client_id and client_secret are required."
        )
    if not sender_mailbox or "@" not in sender_mailbox:
        raise MailSendError(
            "Graph mail: invalid sender_mailbox — must be a real mailbox "
            "address (e.g. noreply@firma.onmicrosoft.com)."
        )
    if not recipients:
        raise MailSendError("No recipients given.")

    token = _graph_app_token(tenant_id, client_id, client_secret,
                              timeout=timeout)

    payload = json.dumps({
        "message": {
            "subject": subject,
            "body":    {"contentType": "HTML", "content": html_body},
            "toRecipients": [
                {"emailAddress": {"address": r}} for r in recipients
            ],
        },
        "saveToSentItems": "true",
    }).encode("utf-8")

    import urllib.parse as _up
    url = (f"https://graph.microsoft.com/v1.0/users/"
           f"{_up.quote(sender_mailbox.strip())}/sendMail")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "User-Agent":    "mysecureprint-server/0.7.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            logger.info(
                "mail(graph): sent OK to %d recipient(s) via %s, status=%s",
                len(recipients), sender_mailbox, status,
            )
            return {"status": status}
    except urllib.error.HTTPError as he:
        err_body = ""
        try:
            err_body = he.read().decode("utf-8", errors="replace")[:600]
        except Exception:
            pass
        hint = ""
        if he.code == 401:
            hint = " (401 = Token-Reject; Client-Secret abgelaufen?)"
        elif he.code == 403:
            hint = (" (403 = Mail.Send Admin-Consent fehlt ODER "
                    "ApplicationAccessPolicy verweigert diese Mailbox)")
        elif he.code == 404:
            hint = " (404 = Sender-Mailbox existiert nicht im Tenant)"
        msg = f"Graph sendMail HTTP {he.code}{hint}: {err_body}"
        logger.warning("mail(graph): send failed — %s", msg)
        raise MailSendError(msg) from he
    except urllib.error.URLError as ue:
        logger.warning("mail(graph): network error — %s", ue)
        raise MailSendError(f"Graph network error: {ue.reason}") from ue
    except Exception as e:
        logger.warning("mail(graph): unexpected error — %s", e)
        raise MailSendError(str(e)) from e


def send_mail(
    recipients: Sequence[str],
    subject: str,
    html_body: str,
    *,
    provider: str = "resend",
    # Resend-Pfad
    api_key: str = "",
    mail_from: str = "",
    mail_from_name: str = "",
    # Graph-Pfad
    graph_tenant_id: str = "",
    graph_client_id: str = "",
    graph_client_secret: str = "",
    graph_sender_mailbox: str = "",
    # Fallback-Verhalten
    allow_resend_fallback: bool = True,
    timeout: int = 20,
) -> dict:
    """Provider-agnostischer Mail-Versand.

    `provider` = `"resend"` (Default) oder `"graph"`. Bei `graph` und
    Versand-Fehler wird — falls `allow_resend_fallback=True` und gueltige
    Resend-Credentials uebergeben sind — automatisch ueber Resend
    nachgereicht. Eine Warnung wird geloggt.
    """
    prov = (provider or "resend").strip().lower()
    if prov == "graph":
        try:
            return send_via_graph(
                recipients=recipients,
                subject=subject,
                html_body=html_body,
                tenant_id=graph_tenant_id,
                client_id=graph_client_id,
                client_secret=graph_client_secret,
                sender_mailbox=graph_sender_mailbox,
                timeout=timeout,
            )
        except MailSendError as ge:
            if allow_resend_fallback and api_key and mail_from:
                logger.warning(
                    "mail: Graph-Versand fehlgeschlagen (%s) — Fallback "
                    "auf Resend.", ge,
                )
                return send_report(
                    recipients=recipients,
                    subject=subject,
                    html_body=html_body,
                    api_key=api_key,
                    mail_from=mail_from,
                    mail_from_name=mail_from_name,
                    timeout=timeout,
                )
            raise

    # Default = Resend
    return send_report(
        recipients=recipients,
        subject=subject,
        html_body=html_body,
        api_key=api_key,
        mail_from=mail_from,
        mail_from_name=mail_from_name,
        timeout=timeout,
    )
