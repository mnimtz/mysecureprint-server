"""Admin-Notification-Helper (Event-Mails + Employee-Invite-Mails).

Ersetzt das bei v0.5.7 geloeschte `reporting.notify_helper` — vier
Call-Sites (web/app.py x2 Import-Stellen, web/employee_routes.py) haben
weiter `from reporting.notify_helper import ...` erwartet, obwohl das
Package `reporting/` beim Refactor komplett entfernt wurde (siehe
Kommentar bei mail_client.py-Nutzung in app.py: "Slim Resend-Client
statt geloeschtem reporting.mail_client"). Jede Registrierung + jede
Mitarbeiter-Bulk-Einladung endete seitdem in ModuleNotFoundError,
das aber (bis auf den Bulk-Employee-Pfad, der defensiv mit try/except
abgesichert ist) unauffaellig war weil die Aufrufer selbst mit
try/except abgesichert sind und nur eine Warnung loggen.

Baut auf dem bereits vorhandenen `mail_client.send_mail` /
`mail_client.send_report` auf (kein neuer HTTP-Code noetig) und
repliziert die 3-stufige Credential-Fallback-Kette (Tenant -> globale
Settings -> ENV), die an anderer Stelle in app.py schon inline existiert
(siehe dort: "Mail-Credentials werden in dieser Reihenfolge aufgeloest").
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger("printix.notify")

_PRODUCT_NAME = "MySecurePrint"
NAVY = "#002854"
DEEP_NAVY = "#00123B"
ACCENT = "#00A0FB"


def is_event_enabled(tenant: dict, event_name: str) -> bool:
    """True wenn `event_name` in tenant.notify_events (JSON-Array) steckt."""
    raw = (tenant or {}).get("notify_events", "") or "[]"
    try:
        events = json.loads(raw)
    except Exception:
        events = []
    return event_name in (events or [])


def resolve_mail_credentials(tenant: dict) -> dict:
    """3-stufige Mail-Credential-Aufloesung: Tenant -> globale Settings -> ENV.

    Returns dict mit: api_key, mail_from, mail_from_name, provider,
    graph_tenant_id, graph_client_id, graph_client_secret,
    graph_sender_mailbox, source (fuer Logs — "tenant"/"global"/"env"/"none").
    """
    from db import get_setting, _dec  # type: ignore

    tenant = tenant or {}
    api_key = (tenant.get("mail_api_key") or "").strip()
    mail_from = (tenant.get("mail_from") or "").strip()
    mail_from_name = (tenant.get("mail_from_name") or "") or _PRODUCT_NAME
    source = "tenant" if api_key else "none"

    if not api_key:
        enc_global = get_setting("global_mail_api_key", "")
        if enc_global:
            try:
                api_key = _dec(enc_global)
                source = "global"
            except Exception:
                api_key = ""
        mail_from = mail_from or (get_setting("global_mail_from", "") or "")
        mail_from_name = (get_setting("global_mail_from_name", "")
                          or mail_from_name)

    if not api_key:
        env_key = os.environ.get("RESEND_API_KEY", "")
        if env_key:
            api_key = env_key
            source = "env"
        mail_from = mail_from or os.environ.get("RESEND_FROM", "")

    provider = (get_setting("mail_provider", "") or "resend").strip().lower()
    graph_tid = graph_cid = graph_csec = graph_sender = ""
    if provider == "graph":
        graph_tid = (get_setting("entra_tenant_id", "") or "").strip()
        graph_cid = (get_setting("entra_client_id", "") or "").strip()
        enc_csec = get_setting("entra_client_secret", "")
        try:
            graph_csec = _dec(enc_csec) if enc_csec else ""
        except Exception:
            graph_csec = ""
        graph_sender = (get_setting("mail_graph_sender", "") or "").strip()
        if not (graph_tid and graph_cid and graph_csec and graph_sender):
            provider = "resend"

    return {
        "api_key": api_key,
        "mail_from": mail_from,
        "mail_from_name": mail_from_name,
        "provider": provider,
        "graph_tenant_id": graph_tid,
        "graph_client_id": graph_cid,
        "graph_client_secret": graph_csec,
        "graph_sender_mailbox": graph_sender,
        "source": source,
    }


def html_user_registered(username: str, email: str, company: str) -> str:
    """HTML-Body fuer die 'Neuer Benutzer wartet auf Freischaltung'-Mail
    an Admins."""
    return f"""\
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
            color: #231F20; max-width: 480px; margin: 0 auto;">
  <div style="background: {DEEP_NAVY}; padding: 20px 24px; border-radius: 12px 12px 0 0;">
    <span style="color: #fff; font-weight: 700; font-size: 16px;">{_PRODUCT_NAME}</span>
  </div>
  <div style="background: #fff; border: 1px solid #D9DFE6; border-top: none;
              border-radius: 0 0 12px 12px; padding: 28px 24px;">
    <p style="margin: 0 0 16px 0; font-size: 15px;">
      Ein neuer Benutzer hat sich registriert und wartet auf Freischaltung:
    </p>
    <table style="border-collapse: collapse; font-size: 14px; width: 100%;
                  background: #F5F7FA; border-radius: 8px; margin-bottom: 20px;">
      <tr><td style="padding: 10px 16px; color: #8094AA; width: 40%;">Benutzername</td>
          <td style="padding: 10px 16px; font-weight: 700;">{username}</td></tr>
      <tr><td style="padding: 10px 16px; color: #8094AA;">Email</td>
          <td style="padding: 10px 16px; font-weight: 700;">{email}</td></tr>
      <tr><td style="padding: 10px 16px; color: #8094AA;">Firma</td>
          <td style="padding: 10px 16px; font-weight: 700;">{company or "—"}</td></tr>
    </table>
    <p style="margin: 0; font-size: 13px; color: #8094AA;">
      Bitte im Admin-Bereich unter Benutzer freischalten.
    </p>
  </div>
</div>
"""


def send_event_notification(tenant: dict, event_name: str, subject: str,
                             html_body: str, check_enabled: bool = True) -> bool:
    """Sendet `html_body` an tenant.alert_recipients, falls das Event
    aktiviert ist (sofern check_enabled) und Mail-Credentials aufloesbar sind.

    Returns True bei Erfolg, False bei jeder Form von Fehler/Skip.
    """
    from mail_client import send_mail, MailSendError

    tenant = tenant or {}
    if check_enabled and not is_event_enabled(tenant, event_name):
        return False

    recipients_str = tenant.get("alert_recipients", "") or ""
    recipients = [r.strip() for r in recipients_str.split(",") if r.strip()]
    if not recipients:
        return False

    creds = resolve_mail_credentials(tenant)
    if creds["provider"] != "graph" and (not creds["api_key"] or not creds["mail_from"]):
        return False

    try:
        send_mail(
            recipients=recipients,
            subject=subject,
            html_body=html_body,
            provider=creds["provider"],
            api_key=creds["api_key"],
            mail_from=creds["mail_from"],
            mail_from_name=creds["mail_from_name"],
            graph_tenant_id=creds["graph_tenant_id"],
            graph_client_id=creds["graph_client_id"],
            graph_client_secret=creds["graph_client_secret"],
            graph_sender_mailbox=creds["graph_sender_mailbox"],
        )
        return True
    except MailSendError as e:
        logger.warning("send_event_notification(%s) failed: %s", event_name, e)
        return False
    except Exception as e:
        logger.warning("send_event_notification(%s) unexpected error: %s",
                        event_name, e)
        return False


def send_employee_invitation(tenant: dict, recipient_email: str,
                              full_name: str, username: str, password: str,
                              login_url: str, admin_name: str = "") -> bool:
    """Sendet die Einladungs-Mail an einen neu angelegten Mitarbeiter
    (Employee-Bulk-Import in web/employee_routes.py).

    Nutzt dieselbe Vorlage wie invite_mail.render_invitation_email fuer
    konsistentes Branding, ergaenzt optional wer eingeladen hat.
    """
    from mail_client import send_mail, MailSendError
    from invite_mail import render_invitation_email

    tenant = tenant or {}
    creds = resolve_mail_credentials(tenant)
    if creds["provider"] != "graph" and (not creds["api_key"] or not creds["mail_from"]):
        logger.info("send_employee_invitation: no mail credentials resolved — skip")
        return False

    lang = (tenant.get("default_language") or "en").strip() or "en"
    subject, html_body = render_invitation_email(
        lang=lang, full_name=full_name, username=username,
        password=password, login_url=login_url,
    )
    if admin_name:
        html_body = html_body.replace(
            "</div>\n",
            f'<p style="font-size:12px;color:#8094AA;margin-top:8px;">'
            f'Eingeladen von {admin_name}.</p></div>\n', 1,
        )

    try:
        send_mail(
            recipients=[recipient_email],
            subject=subject,
            html_body=html_body,
            provider=creds["provider"],
            api_key=creds["api_key"],
            mail_from=creds["mail_from"],
            mail_from_name=creds["mail_from_name"],
            graph_tenant_id=creds["graph_tenant_id"],
            graph_client_id=creds["graph_client_id"],
            graph_client_secret=creds["graph_client_secret"],
            graph_sender_mailbox=creds["graph_sender_mailbox"],
        )
        return True
    except MailSendError as e:
        logger.warning("send_employee_invitation to %s failed: %s",
                        recipient_email, e)
        return False
    except Exception as e:
        logger.warning("send_employee_invitation to %s unexpected error: %s",
                        recipient_email, e)
        return False
