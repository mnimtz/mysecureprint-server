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
    # Office / Text — werden vor dem Print via LibreOffice zu PDF konvertiert
    "application/msword",                                            # .doc
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/vnd.ms-excel",                                      # .xls
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",        # .xlsx
    "application/vnd.ms-powerpoint",                                 # .ppt
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",# .pptx
    "application/vnd.oasis.opendocument.text",                       # .odt
    "application/vnd.oasis.opendocument.spreadsheet",                # .ods
    "application/vnd.oasis.opendocument.presentation",               # .odp
    "application/rtf", "text/rtf",                                   # .rtf
    "text/plain",                                                    # .txt
}
_ALLOWED_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp", ".rtf", ".txt",
}
# Diese Endungen sind KEIN PDF und muessen vor dem Print konvertiert werden
_OFFICE_EXTENSIONS = {
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp", ".rtf", ".txt",
}

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


def _list_unread_messages(token: str, upn: str, top: int = 20,
                           source_folder: str = "Inbox") -> list[dict]:
    """Holt bis zu `top` neueste UNGELESENE Mails aus dem konfigurierten
    Quell-Ordner. Default 'Inbox' — Admin kann z.B. 'Inbox/Druckauftraege'
    setzen, dann wird nur der Sub-Folder gepollt.

    'Inbox' ist ein Well-Known-Name in Graph (Case-sensitive, funktioniert
    ohne Lookup). Alles andere ist ein Pfad relativ zur Inbox, wird per
    _resolve_folder_id aufgeloest.
    """
    folder_path = (source_folder or "Inbox").strip()
    if folder_path.lower() == "inbox" or not folder_path:
        endpoint = f"/users/{upn}/mailFolders/Inbox/messages"
    else:
        folder_id = _resolve_folder_id(token, upn, folder_path)
        if not folder_id:
            logger.warning("Graph mail-list %s: source_folder=%r nicht "
                            "gefunden, fallback Inbox", upn, folder_path)
            endpoint = f"/users/{upn}/mailFolders/Inbox/messages"
        else:
            endpoint = f"/users/{upn}/mailFolders/{folder_id}/messages"
    r = _graph_get(token, endpoint,
                    params={"$filter": "isRead eq false",
                            "$orderby": "receivedDateTime asc",
                            "$top": str(top),
                            "$select": "id,subject,from,receivedDateTime,"
                                          "hasAttachments,internetMessageId"})
    if r.status_code != 200:
        logger.warning("Graph mail-list %s/%s → %s %s",
                        upn, folder_path, r.status_code, r.text[:200])
        return []
    return r.json().get("value", []) or []


def list_mail_folders(upn: str, mailbox_id: str = "") -> list[dict]:
    """Auflistung aller Mail-Ordner der Mailbox — flach, mit vollen Pfaden.
    Für das UI-Dropdown zum Auswählen des Poll-Quell-Ordners.

    Returns Liste von {id, path, name, message_count} — well-known Ordner
    (Inbox, Sent Items etc.) VOR den User-Sub-Ordnern, sortiert damit's
    im Dropdown lesbar bleibt.

    upn: Mailbox-UPN. mailbox_id nur zur Fehler-Persistenz optional.
    """
    from . import store
    from mail_client import get_graph_token
    try:
        token_info = get_graph_token()
        token = token_info["access_token"]
    except Exception as e:
        if mailbox_id:
            store.update_mailbox(mailbox_id, last_error=f"folder_list_token: {e}")
        return []

    def _fetch_children(parent_id: str, parent_path: str,
                         depth: int) -> list[dict]:
        if depth > 5:  # Sicherheit gegen Zyklen
            return []
        r = _graph_get(token,
                        f"/users/{upn}/mailFolders/{parent_id}/childFolders",
                        params={"$select": "id,displayName,childFolderCount,"
                                             "totalItemCount",
                                "$top": "100"})
        if r.status_code != 200:
            return []
        out = []
        for f in r.json().get("value", []):
            name = f.get("displayName") or "?"
            path = f"{parent_path}/{name}"
            out.append({
                "id": f["id"],
                "path": path,
                "name": name,
                "count": int(f.get("totalItemCount") or 0),
            })
            if int(f.get("childFolderCount") or 0) > 0:
                out.extend(_fetch_children(f["id"], path, depth + 1))
        return out

    # Top-Level Ordner
    r = _graph_get(token, f"/users/{upn}/mailFolders",
                    params={"$select": "id,displayName,childFolderCount,"
                                         "totalItemCount",
                            "$top": "100"})
    if r.status_code != 200:
        if mailbox_id:
            store.update_mailbox(
                mailbox_id,
                last_error=f"folder_list: {r.status_code} {r.text[:200]}",
            )
        return []

    results = []
    inbox_id = None
    for f in r.json().get("value", []):
        name = f.get("displayName") or "?"
        results.append({
            "id": f["id"],
            "path": name,
            "name": name,
            "count": int(f.get("totalItemCount") or 0),
        })
        if name.lower() == "inbox":
            inbox_id = f["id"]
        if int(f.get("childFolderCount") or 0) > 0:
            results.extend(_fetch_children(f["id"], name, 1))

    # Well-Known-Order: Inbox + Kinder zuerst, dann Rest
    inbox_prefix = None
    for f in results:
        if f["id"] == inbox_id:
            inbox_prefix = "Inbox"
            break
    def _sort_key(f):
        p = f["path"]
        if inbox_prefix and (p == inbox_prefix or p.startswith(inbox_prefix + "/")):
            return (0, p.lower())
        return (1, p.lower())
    results.sort(key=_sort_key)
    return results


def _resolve_folder_id(token: str, upn: str, path: str) -> str | None:
    """Aufloesen von 'Inbox/Sub/Sub2' → mailFolders-ID. Fuer den Poll-
    Quell-Ordner. Ordner werden NICHT angelegt (im Gegensatz zu
    _ensure_folder), sondern nur gesucht — sonst wuerde man versehentlich
    einen leeren Poll-Ordner erzeugen wenn ein Tippfehler drin ist.
    """
    if not path or path.lower() == "inbox":
        return None  # caller nutzt direkt 'Inbox' well-known
    segments = [s.strip() for s in path.split("/") if s.strip()]
    if not segments:
        return None
    # Erstes Segment: absolute mailFolders (top-level) oder in Inbox suchen.
    # Konvention: fuehrendes 'Inbox' entfernen falls vorhanden, weil wir
    # unten sowieso relativ zur Inbox suchen.
    if segments[0].lower() == "inbox":
        segments = segments[1:]
        if not segments:
            return None
    parent = "Inbox"
    for seg in segments:
        r = _graph_get(token, f"/users/{upn}/mailFolders/{parent}/childFolders",
                        params={"$filter": f"displayName eq '{seg}'",
                                "$select": "id,displayName"})
        if r.status_code != 200:
            return None
        found = [f for f in r.json().get("value", [])
                 if f.get("displayName") == seg]
        if not found:
            return None
        parent = found[0]["id"]
    return parent


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

    source_folder = mb.get("source_folder") or "Inbox"
    messages = _list_unread_messages(token, upn, top=20,
                                       source_folder=source_folder)
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

            # ─── PDF-Konvertierung fuer Office/Text-Formate ─────────────
            # Printix nimmt effektiv nur PDF/Bilder ordentlich. Office-
            # Anhaenge muessen wir vor dem Submit ueber LibreOffice
            # rendern (dieselbe Pipeline wie /desktop/upload).
            print_name = name
            print_data = data
            _ext_lc = os.path.splitext(name)[1].lower()
            if _ext_lc in _OFFICE_EXTENSIONS:
                try:
                    from upload_converter import convert_to_pdf
                    print_data, _src_label = convert_to_pdf(data, name)
                    # Filename fuer Printix auf .pdf umstellen
                    print_name = os.path.splitext(name)[0] + ".pdf"
                    logger.info(
                        "GuestPrint: %s → PDF (%s, %d bytes) fuer Sender %s",
                        name, _src_label, len(print_data), sender,
                    )
                except Exception as _ce:
                    logger.warning(
                        "GuestPrint: PDF-Konvertierung %s fehlgeschlagen: %s",
                        name, _ce,
                    )
                    store.record_job(mailbox_id=mailbox_id,
                                      message_id=internet_msgid,
                                      attachment_name=name,
                                      sender_email=sender, subject=subject,
                                      attachment_bytes=len(data),
                                      guest_id=(guest or {}).get("id", ""),
                                      status="error",
                                      error=f"convert_failed: {str(_ce)[:120]}")
                    stats["errors"] += 1
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
                        attachment_name=print_name,
                        attachment_bytes=print_data,
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
                # Absender-Notification wenn Mailbox das aktiviert hat
                if mb.get("notify_sender") and sender:
                    _notify_sender_success(
                        sender_email=sender,
                        attachment_name=print_name,
                        subject=subject,
                        is_internal=bool(internal_user),
                    )

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


def _notify_sender_success(sender_email: str, attachment_name: str,
                             subject: str, is_internal: bool) -> None:
    """Bestaetigungs-Email an den Absender wenn Mailbox das aktiviert hat.

    Fehler beim Notification-Versand werden NUR geloggt — sie duerfen den
    Print-Job nicht beeinflussen (der Job liegt zu diesem Zeitpunkt schon
    in Printix, das ist unabhaengig).
    """
    try:
        # Provider + Credentials aus Settings laden (dieselbe Kaskade wie
        # in web/app.py Mail-Versand-Aufrufer).
        import os as _os
        from db import get_setting as _gs, _dec as _dec_settings
        from mail_client import send_mail, MailSendError

        provider = (_gs("mail_provider", "") or "resend").strip().lower()
        api_key = (_gs("mail_resend_api_key", "")
                   or _os.environ.get("RESEND_API_KEY", "")).strip()
        mail_from = (_gs("mail_from", "")
                     or _os.environ.get("RESEND_FROM", "")).strip()
        mail_from_name = (_gs("mail_from_name", "") or "MySecurePrint").strip()
        graph_tid = graph_cid = graph_csec = graph_sender = ""
        if provider == "graph":
            graph_tid = (_gs("entra_tenant_id", "") or "").strip()
            graph_cid = (_gs("entra_client_id", "") or "").strip()
            _enc = _gs("entra_client_secret", "")
            try:
                graph_csec = _dec_settings(_enc) if _enc else ""
            except Exception:
                graph_csec = ""
            graph_sender = (_gs("mail_graph_sender", "") or "").strip()

        if not api_key and not (provider == "graph" and graph_sender):
            logger.info(
                "GuestPrint notify_sender: kein Mail-Provider konfiguriert "
                "— Skip fuer %s", sender_email)
            return

        # Sinnvoller Betreff — greift Original-Betreff auf wenn vorhanden
        _sub_orig = (subject or "").strip()
        subj = (f"Dein Druckauftrag ist bereit: {_sub_orig[:60]}"
                if _sub_orig else "Dein Druckauftrag ist bereit")

        # Simpler HTML-Body. Interne User bekommen Hinweis auf ihre eigene
        # SecurePrint-Queue, Gaeste bekommen generischen "am Multifunktions-
        # geraet abholen"-Text.
        who_line = (
            "Der Druckauftrag liegt in <b>deiner SecurePrint-Queue</b> "
            "und wartet auf Freigabe am Drucker."
            if is_internal else
            "Der Druckauftrag liegt am Firmen-Multifunktionsgeraet "
            "bereit zur Freigabe."
        )
        html = f"""\
<p>Hallo,</p>
<p>dein per Email eingereichter Druckauftrag wurde erfolgreich verarbeitet
und in die SecurePrint-Queue eingereiht.</p>
<table style="margin:12px 0;font-size:14px;">
  <tr><td style="padding:4px 12px 4px 0;color:#666;">Datei:</td>
      <td><code>{attachment_name}</code></td></tr>
  <tr><td style="padding:4px 12px 4px 0;color:#666;">Betreff:</td>
      <td>{_sub_orig or '(leer)'}</td></tr>
</table>
<p>{who_line}</p>
<p style="color:#888;font-size:12px;margin-top:24px;">
  Automatische Nachricht vom Email-to-Print-Gateway. Kein Antwort noetig.
</p>"""

        send_mail(
            recipients=[sender_email], subject=subj, html_body=html,
            provider=provider, api_key=api_key,
            mail_from=mail_from, mail_from_name=mail_from_name,
            graph_tenant_id=graph_tid, graph_client_id=graph_cid,
            graph_client_secret=graph_csec,
            graph_sender_mailbox=graph_sender,
        )
        logger.info("GuestPrint notify_sender OK — to=%s file=%s",
                    sender_email, attachment_name)
    except Exception as e:
        logger.warning("GuestPrint notify_sender failed to=%s: %s",
                       sender_email, e)
