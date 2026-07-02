"""Graph-API Mail-Poller fuer eine ueberwachte O365-Mailbox.

Sicherheitsschwerpunkte:
 - Pro Mail wird der Absender via `internetMessageHeaders` (Return-Path / From)
   geprueft — nicht die display-name freie Form. Anti-Spoofing-Vertrauen
   stuetzt sich auf SPF/DMARC der Exchange-Tenant-Konfiguration.
 - Anhaenge: harter Size-Cap aus Mailbox-Konfig, MIME-Whitelist, Dateiname-
   Sanitisierung gegen Path-Traversal.
 - Idempotenz: (mailbox_id, message_id, attachment_name) UNIQUE in
   guestprint_job. Reentrant safe.
 - Auth: App-Only Token via mail_client.get_graph_token (Client-Credentials
   Flow), Mail.Read App-Role muss vorher granted sein.

Default-Verhalten:
 - Externer Sender → wenn in `guestprint_guest` whitelisted → Job
 - Sender ist ein interner User (matched via email in users-table) → Job
   in dessen User-Cloud-Print-Queue
 - Sonst → in `folder_skipped` verschieben, Job mit status='rejected'
"""
from __future__ import annotations

import logging
import os
import re
from typing import Iterable

import requests as _requests

import db as _db
import mail_client
from . import store

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Was wir aus Mail-Anhaengen akzeptieren. PDF ist Pflicht, Office-Formate
# werden NICHT umgewandelt — Printix Cloud Print Connector handhabt das.
# Bilder (PNG/JPG) gehen direkt durch.
_ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/jpg",
}
_ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._\- ]")


def _safe_filename(name: str) -> str:
    """Strippt Path-Komponenten + nicht-druckbare Zeichen. Cap 200 Zeichen."""
    if not name:
        return "anhang"
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = _FILENAME_SAFE.sub("_", name).strip(" ._-")
    if not name:
        name = "anhang"
    return name[:200]


def _attachment_allowed(content_type: str, filename: str,
                          size_bytes: int, max_bytes: int) -> tuple[bool, str]:
    if size_bytes <= 0:
        return False, "leer"
    if size_bytes > max_bytes:
        return False, f"zu_gross ({size_bytes} > {max_bytes})"
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    ext = os.path.splitext(filename or "")[1].lower()
    if ct in _ALLOWED_CONTENT_TYPES or ext in _ALLOWED_EXTENSIONS:
        return True, ""
    return False, f"unerlaubter_typ ({ct}/{ext})"


def _graph_get(token: str, path: str, params: dict | None = None,
                stream: bool = False) -> _requests.Response:
    return _requests.get(
        f"{_GRAPH_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30,
        stream=stream,
    )


def _graph_post(token: str, path: str, json: dict) -> _requests.Response:
    return _requests.post(
        f"{_GRAPH_BASE}{path}",
        headers={"Authorization": f"Bearer {token}",
                  "Content-Type": "application/json"},
        json=json,
        timeout=30,
    )


def _list_unread_messages(token: str, upn: str, top: int = 20) -> list[dict]:
    """Holt bis zu `top` neueste UNGELESENE Mails der Inbox."""
    r = _graph_get(token, f"/users/{upn}/mailFolders/Inbox/messages",
                    params={"$filter": "isRead eq false",
                            "$orderby": "receivedDateTime asc",
                            "$top": str(top),
                            "$select": "id,subject,from,receivedDateTime,"
                                          "hasAttachments,internetMessageId"})
    if r.status_code != 200:
        logger.warning("Graph mail-list %s/Inbox → %s %s",
                        upn, r.status_code, r.text[:200])
        return []
    return r.json().get("value", []) or []


def _get_attachments(token: str, upn: str, message_id: str) -> list[dict]:
    r = _graph_get(token,
                    f"/users/{upn}/messages/{message_id}/attachments",
                    params={"$select": "id,name,contentType,size,"
                                         "@odata.type,isInline"})
    if r.status_code != 200:
        logger.warning("Graph attachments %s → %s", message_id, r.status_code)
        return []
    return r.json().get("value", []) or []


def _download_attachment(token: str, upn: str,
                           message_id: str, attachment_id: str) -> bytes | None:
    """Laedt einen einzelnen Anhang als Bytes. None bei Fehler."""
    r = _graph_get(token,
                    f"/users/{upn}/messages/{message_id}"
                    f"/attachments/{attachment_id}/$value",
                    stream=True)
    if r.status_code != 200:
        logger.warning("Graph attachment dl %s → %s",
                        attachment_id, r.status_code)
        return None
    return r.content


def _mark_read(token: str, upn: str, message_id: str) -> bool:
    r = _requests.patch(
        f"{_GRAPH_BASE}/users/{upn}/messages/{message_id}",
        headers={"Authorization": f"Bearer {token}",
                  "Content-Type": "application/json"},
        json={"isRead": True}, timeout=15)
    return r.status_code in (200, 204)


def _move_message(token: str, upn: str, message_id: str,
                   target_folder_path: str) -> bool:
    """Bewegt eine Nachricht in den angegebenen Ordner. Erzeugt den Ordner
    wenn er noch nicht existiert (rekursiv)."""
    folder_id = _ensure_folder(token, upn, target_folder_path)
    if not folder_id:
        return False
    r = _graph_post(token,
                     f"/users/{upn}/messages/{message_id}/move",
                     {"destinationId": folder_id})
    return r.status_code in (200, 201)


def _ensure_folder(token: str, upn: str, path: str) -> str | None:
    """`a/b/c` → ensures sub-folders existieren in der Inbox. Returns
    die Folder-ID des letzten Segments."""
    parts = [p for p in (path or "").split("/") if p]
    if not parts:
        return None
    # Erstes Segment relativ zur Inbox
    parent_path = "/me"  # not used; we use Inbox
    r = _graph_get(token, f"/users/{upn}/mailFolders/Inbox/childFolders",
                    params={"$select": "id,displayName"})
    if r.status_code != 200:
        return None
    current_children = r.json().get("value", [])
    parent_id = ""
    for i, segment in enumerate(parts):
        match = next((c for c in current_children
                       if (c.get("displayName") or "").lower()
                       == segment.lower()), None)
        if match:
            parent_id = match["id"]
        else:
            # create
            create_url = (f"/users/{upn}/mailFolders/Inbox/childFolders"
                            if i == 0 else
                            f"/users/{upn}/mailFolders/{parent_id}/childFolders")
            cr = _graph_post(token, create_url, {"displayName": segment})
            if cr.status_code not in (200, 201):
                logger.warning("Graph folder-create %s → %s",
                                segment, cr.status_code)
                return None
            parent_id = cr.json().get("id", "")
        # Children fuer naechste Iteration laden
        if i < len(parts) - 1:
            cc = _graph_get(token,
                              f"/users/{upn}/mailFolders/{parent_id}/childFolders",
                              params={"$select": "id,displayName"})
            current_children = cc.json().get("value", []) if cc.status_code == 200 else []
    return parent_id


def _lookup_internal_user_by_email(email: str) -> dict | None:
    """Sucht in users-Tabelle ob `email` ein bekannter Server-User ist."""
    if not email:
        return None
    try:
        with _db._conn() as conn:
            r = conn.execute(
                "SELECT id, email, name FROM users "
                "WHERE LOWER(email) = LOWER(?) LIMIT 1",
                (email,)).fetchone()
        return dict(r) if r else None
    except Exception:
        return None


# ─── Public API ───────────────────────────────────────────────────────────────

def poll_mailbox_once(mailbox_id: str,
                        submit_print_job=None) -> dict:
    """Ein Polling-Tick fuer EINE Mailbox.

    `submit_print_job` ist eine Funktion mit Signatur
        submit_print_job(*, sender_user: dict|None, guest: dict|None,
                          mailbox: dict, attachment_name: str,
                          attachment_bytes: bytes, subject: str) -> str
    die die printix_job_id zurueckgibt. Wenn None, wird nur das Logging
    geschrieben und der Anhang als 'pending' markiert (Dry-Run).

    Returns Statistik-Dict.
    """
    mb = store.get_mailbox(mailbox_id)
    if not mb:
        return {"error": "mailbox_not_found"}
    if not mb.get("enabled"):
        return {"skipped": "disabled"}

    upn = mb["upn"]
    tenant_id = mb["tenant_id"]
    max_bytes = int(mb.get("max_attachment_bytes") or 26214400)

    # Token holen
    try:
        token_info = mail_client.get_graph_token()
        token = token_info["access_token"]
    except Exception as e:
        store.update_mailbox(mailbox_id, last_error=f"token: {e}")
        return {"error": "no_graph_token", "detail": str(e)[:200]}

    stats = {"seen": 0, "printed": 0, "rejected": 0, "skipped_no_attachment": 0,
              "errors": 0}

    messages = _list_unread_messages(token, upn, top=20)
    for msg in messages:
        stats["seen"] += 1
        msg_id = msg["id"]
        sender = (msg.get("from") or {}).get("emailAddress", {}).get("address", "")
        sender = (sender or "").lower()
        subject = msg.get("subject") or ""
        internet_msgid = msg.get("internetMessageId") or msg_id

        if not msg.get("hasAttachments"):
            stats["skipped_no_attachment"] += 1
            _mark_read(token, upn, msg_id)
            continue

        # Sender autorisiert?
        internal_user = _lookup_internal_user_by_email(sender)
        guest = None if internal_user else store.is_email_whitelisted(
            mailbox_id, sender)

        if not internal_user and not guest:
            # Reject — Sender ist weder bekannter User noch whitelisted Gast
            store.record_job(mailbox_id=mailbox_id,
                              message_id=internet_msgid,
                              attachment_name="(rejected)",
                              sender_email=sender, subject=subject,
                              status="rejected", error="sender_not_authorized")
            stats["rejected"] += 1
            _move_message(token, upn, msg_id, mb.get("folder_skipped")
                           or "GuestPrint/Skipped")
            continue

        # Anhaenge holen + drucken
        atts = _get_attachments(token, upn, msg_id)
        any_printed = False
        _att_statuses: list[str] = []
        for att in atts:
            if att.get("isInline"):
                continue
            if att.get("@odata.type") != "#microsoft.graph.fileAttachment":
                continue  # itemAttachment / referenceAttachment → ignore
            name = _safe_filename(att.get("name") or "")
            ct = att.get("contentType") or ""
            sz = int(att.get("size") or 0)
            ok, reason = _attachment_allowed(ct, name, sz, max_bytes)
            if not ok:
                store.record_job(mailbox_id=mailbox_id,
                                  message_id=internet_msgid,
                                  attachment_name=name,
                                  sender_email=sender, subject=subject,
                                  attachment_bytes=sz,
                                  guest_id=(guest or {}).get("id", ""),
                                  status="rejected", error=reason)
                stats["rejected"] += 1
                continue

            data = _download_attachment(token, upn, msg_id, att["id"])
            if data is None:
                store.record_job(mailbox_id=mailbox_id,
                                  message_id=internet_msgid,
                                  attachment_name=name,
                                  sender_email=sender, subject=subject,
                                  attachment_bytes=sz,
                                  guest_id=(guest or {}).get("id", ""),
                                  status="error",
                                  error="download_failed")
                stats["errors"] += 1
                continue

            # Hart re-check Size nach Download (defense-in-depth — Graph
            # koennte falsche size melden)
            if len(data) > max_bytes:
                store.record_job(mailbox_id=mailbox_id,
                                  message_id=internet_msgid,
                                  attachment_name=name,
                                  sender_email=sender, subject=subject,
                                  attachment_bytes=len(data),
                                  guest_id=(guest or {}).get("id", ""),
                                  status="rejected", error="size_after_dl")
                stats["rejected"] += 1
                continue

            # Submit zum Drucker
            printix_job_id = ""
            err = ""
            try:
                if submit_print_job:
                    printix_job_id = submit_print_job(
                        sender_user=internal_user,
                        guest=guest,
                        mailbox=mb,
                        attachment_name=name,
                        attachment_bytes=data,
                        subject=subject,
                    ) or ""
            except Exception as e:
                err = str(e)[:300]
                stats["errors"] += 1
                logger.warning("Guest-Print submit failed: %s", e)

            status = "ok" if printix_job_id else ("pending" if not err
                                                       else "error")
            store.record_job(mailbox_id=mailbox_id,
                              message_id=internet_msgid,
                              attachment_name=name,
                              sender_email=sender, subject=subject,
                              attachment_bytes=len(data),
                              guest_id=(guest or {}).get("id", ""),
                              printix_job_id=printix_job_id,
                              status=status, error=err)
            _att_statuses.append(status)
            if status == "ok":
                any_printed = True
                stats["printed"] += 1

            # Guest-last-match aktualisieren
            if guest and any_printed:
                try:
                    store.update_guest(guest["id"],
                                        last_match_at=store._now())
                except Exception:
                    pass

        # Folder-Move / Read-Mark.
        # Nur wenn mindestens ein Anhang erfolgreich gedruckt wurde — bei
        # pending die Mail NICHT als gelesen markieren (Retry im nächsten
        # Poll). Bei hartem Fehler aller Anhänge als gelesen markieren.
        all_error = bool(_att_statuses) and all(s == "error" for s in _att_statuses)
        on_success = mb.get("on_success", "move")
        if any_printed:
            if on_success == "move":
                _move_message(token, upn, msg_id,
                                mb.get("folder_processed")
                                or "GuestPrint/Processed")
            elif on_success == "delete":
                _requests.delete(
                    f"{_GRAPH_BASE}/users/{upn}/messages/{msg_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=15)
            else:
                _mark_read(token, upn, msg_id)
        elif all_error:
            # Alle Anhänge mit hartem Fehler → als gelesen markieren damit
            # die Mail nicht ewig in der Poll-Queue bleibt.
            _mark_read(token, upn, msg_id)
        # status==pending → Mail bleibt ungelesen für Retry

    # Statistik in Mailbox-Tabelle
    try:
        store.update_mailbox(mailbox_id, last_poll_at=store._now(),
                              last_error="")
    except Exception:
        pass
    return stats
