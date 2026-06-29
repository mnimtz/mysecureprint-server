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
