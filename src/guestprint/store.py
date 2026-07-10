"""DB-Layer fuer Guest-Print / Email-to-Print.

Wraps die `guestprint_mailbox`, `guestprint_guest`, `guestprint_job` Tabellen
(initialisiert in db.init_db).
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import db as _db

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_email(addr: str) -> str:
    """Normalisiert eine Email-Adresse (lowercase, strip).

    Wirft KEINEN ValueError — dem Aufrufer obliegt die echte Validierung
    (`validate_email_address`)."""
    if not addr:
        return ""
    return addr.strip().lower()


def validate_email_address(addr: str) -> bool:
    """Strenge Validierung: lowercase normalisierbar, ein einzelnes `@`,
    keine Whitespaces, lokaler Part >=1 + Domain mit mindestens einem
    Punkt. Keine RFC-perfekte Validierung — Ziel ist Defense-in-Depth
    gegen Whitespace-/Header-Injection."""
    if not addr or not isinstance(addr, str):
        return False
    if any(c in addr for c in (" ", "\r", "\n", "\t", "\x00")):
        return False
    if addr.count("@") != 1:
        return False
    local, _, domain = addr.partition("@")
    if not local or not domain:
        return False
    if "." not in domain:
        return False
    if len(addr) > 254:
        return False
    return True


# ─── Mailbox-Konfiguration ───────────────────────────────────────────────────

def create_mailbox(*,
                     tenant_id: str,
                     upn: str,
                     name: str = "",
                     default_printer_id: str = "",
                     default_queue_id: str = "",
                     poll_interval_sec: int = 60,
                     source_folder: str = "Inbox",
                     folder_processed: str = "GuestPrint/Processed",
                     folder_skipped: str = "GuestPrint/Skipped",
                     on_success: str = "move",
                     max_attachment_bytes: int = 26214400,
                     enabled: bool = True) -> str:
    """Legt eine neue ueberwachte Mailbox an. Returns die UUID."""
    if not validate_email_address(upn):
        raise ValueError("invalid mailbox UPN")
    if on_success not in ("move", "keep", "delete"):
        raise ValueError("on_success must be move|keep|delete")
    poll_interval_sec = max(15, min(3600, int(poll_interval_sec)))
    max_attachment_bytes = max(1024, min(100 * 1024 * 1024,
                                            int(max_attachment_bytes)))
    mid = uuid.uuid4().hex
    now = _now()
    with _db._conn() as conn:
        conn.execute("""
            INSERT INTO guestprint_mailbox
              (id, tenant_id, name, upn, default_printer_id, default_queue_id,
               poll_interval_sec, source_folder,
               folder_processed, folder_skipped,
               on_success, max_attachment_bytes, enabled, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (mid, tenant_id, name.strip(), _norm_email(upn),
                default_printer_id, default_queue_id,
                poll_interval_sec,
                (source_folder or "Inbox").strip() or "Inbox",
                folder_processed.strip(), folder_skipped.strip(),
                on_success, max_attachment_bytes,
                1 if enabled else 0, now, now))
    logger.info("Guest-Print mailbox %s angelegt (upn=%s)", mid, upn)
    return mid


def get_mailbox(mailbox_id: str) -> dict | None:
    if not mailbox_id:
        return None
    with _db._conn() as conn:
        r = conn.execute(
            "SELECT * FROM guestprint_mailbox WHERE id = ?",
            (mailbox_id,)).fetchone()
    if not r:
        return None
    return _row_to_dict(r, conn=None, table="guestprint_mailbox")


def list_mailboxes(tenant_id: str = "") -> list[dict]:
    with _db._conn() as conn:
        if tenant_id:
            rows = conn.execute(
                "SELECT * FROM guestprint_mailbox WHERE tenant_id = ? "
                "ORDER BY upn",
                (tenant_id,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM guestprint_mailbox ORDER BY tenant_id, upn"
            ).fetchall()
    return [dict(r) for r in rows]


def update_mailbox(mailbox_id: str, **fields) -> bool:
    """Updated Felder einer Mailbox. Schreibt nur whitelisted columns."""
    if not mailbox_id:
        return False
    allowed = {"name", "default_printer_id", "default_queue_id",
                "poll_interval_sec", "source_folder",
                "folder_processed", "folder_skipped",
                "on_success", "max_attachment_bytes", "enabled",
                "last_poll_at", "last_error"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "poll_interval_sec":
            v = max(15, min(3600, int(v)))
        elif k == "max_attachment_bytes":
            v = max(1024, min(100 * 1024 * 1024, int(v)))
        elif k == "enabled":
            v = 1 if v else 0
        elif k == "on_success" and v not in ("move", "keep", "delete"):
            continue
        sets.append(f"{k} = ?")
        args.append(v)
    if not sets:
        return False
    sets.append("updated_at = ?")
    args.append(_now())
    args.append(mailbox_id)
    with _db._conn() as conn:
        cur = conn.execute(
            f"UPDATE guestprint_mailbox SET {', '.join(sets)} WHERE id = ?",
            args)
    return cur.rowcount > 0


def try_acquire_poll_lock(mailbox_id: str, interval_sec: int) -> bool:
    """Atomarer Multi-Worker-Lock: setzt `last_poll_at` nur dann, wenn der
    Wert mehr als `interval_sec` zurueckliegt — als single SQL-UPDATE.
    Returns True wenn DIESER Worker den Tick uebernehmen darf.

    Defense gegen das Mehr-Worker-Doppeldruck-Szenario (Worker A und B
    lesen beide die gleichen unread mails bevor A `_mark_read` schickt).
    Mit dem Lock erwischt nur ein Worker den Tick — der andere sieht
    rowcount=0 und uebersrpingt.
    """
    if not mailbox_id:
        return False
    interval_sec = max(15, int(interval_sec))
    now_iso = _now()
    # Schwelle: jetzt MINUS interval_sec, in ISO-8601 vergleichbar weil
    # alle `last_poll_at`-Werte das gleiche UTC-Format haben.
    threshold = (datetime.now(timezone.utc) -
                  timedelta(seconds=interval_sec)
                  ).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _db._conn() as conn:
        cur = conn.execute(
            "UPDATE guestprint_mailbox "
            "SET last_poll_at = ?, updated_at = ? "
            "WHERE id = ? AND enabled = 1 AND "
            "(last_poll_at = '' OR last_poll_at < ?)",
            (now_iso, now_iso, mailbox_id, threshold))
    return cur.rowcount > 0


def delete_mailbox(mailbox_id: str) -> bool:
    if not mailbox_id:
        return False
    with _db._conn() as conn:
        # Gaeste werden via ON DELETE CASCADE entfernt
        cur = conn.execute(
            "DELETE FROM guestprint_mailbox WHERE id = ?", (mailbox_id,))
    return cur.rowcount > 0


# ─── Guest-Whitelist ─────────────────────────────────────────────────────────

def add_guest(*,
                 mailbox_id: str,
                 sender_email: str,
                 full_name: str = "",
                 printix_user_id: str = "",
                 printix_guest_email: str = "",
                 printer_id: str = "",
                 queue_id: str = "",
                 expiration_days: int = 7,
                 enabled: bool = True) -> str:
    """Legt eine Whitelist-Eintragung an oder ersetzt sie (mailbox_id+email
    ist UNIQUE). Returns Guest-UUID."""
    if not validate_email_address(sender_email):
        raise ValueError("invalid sender email")
    if not mailbox_id:
        raise ValueError("mailbox_id required")
    expiration_days = max(0, min(3650, int(expiration_days)))
    expires_at = ""
    if expiration_days > 0:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=expiration_days)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    gid = uuid.uuid4().hex
    now = _now()
    with _db._conn() as conn:
        try:
            conn.execute("""
                INSERT INTO guestprint_guest
                  (id, mailbox_id, sender_email, full_name,
                   printix_user_id, printix_guest_email,
                   printer_id, queue_id,
                   expiration_days, expires_at, enabled,
                   created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (gid, mailbox_id, _norm_email(sender_email), full_name.strip(),
                    printix_user_id, printix_guest_email,
                    printer_id, queue_id,
                    expiration_days, expires_at,
                    1 if enabled else 0, now, now))
        except sqlite3.IntegrityError:
            # Update existing
            conn.execute("""
                UPDATE guestprint_guest SET
                  full_name=?, printix_user_id=?, printix_guest_email=?,
                  printer_id=?, queue_id=?,
                  expiration_days=?, expires_at=?, enabled=?, updated_at=?
                WHERE mailbox_id = ? AND sender_email = ?
            """, (full_name.strip(), printix_user_id, printix_guest_email,
                    printer_id, queue_id,
                    expiration_days, expires_at,
                    1 if enabled else 0, now,
                    mailbox_id, _norm_email(sender_email)))
            r = conn.execute(
                "SELECT id FROM guestprint_guest "
                "WHERE mailbox_id = ? AND sender_email = ?",
                (mailbox_id, _norm_email(sender_email))).fetchone()
            if r:
                gid = r["id"]
    return gid


def get_guest(guest_id: str) -> dict | None:
    if not guest_id:
        return None
    with _db._conn() as conn:
        r = conn.execute(
            "SELECT * FROM guestprint_guest WHERE id = ?",
            (guest_id,)).fetchone()
    return dict(r) if r else None


def list_guests(mailbox_id: str) -> list[dict]:
    if not mailbox_id:
        return []
    with _db._conn() as conn:
        rows = conn.execute(
            "SELECT * FROM guestprint_guest "
            "WHERE mailbox_id = ? ORDER BY sender_email",
            (mailbox_id,)).fetchall()
    return [dict(r) for r in rows]


def update_guest(guest_id: str, **fields) -> bool:
    if not guest_id:
        return False
    allowed = {"full_name", "printix_user_id", "printix_guest_email",
                "printer_id", "queue_id", "expiration_days", "expires_at",
                "enabled", "last_match_at"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "expiration_days":
            v = max(0, min(3650, int(v)))
        elif k == "enabled":
            v = 1 if v else 0
        sets.append(f"{k} = ?")
        args.append(v)
    if not sets:
        return False
    sets.append("updated_at = ?")
    args.append(_now())
    args.append(guest_id)
    with _db._conn() as conn:
        cur = conn.execute(
            f"UPDATE guestprint_guest SET {', '.join(sets)} WHERE id = ?",
            args)
    return cur.rowcount > 0


def delete_guest(guest_id: str) -> bool:
    if not guest_id:
        return False
    with _db._conn() as conn:
        cur = conn.execute(
            "DELETE FROM guestprint_guest WHERE id = ?", (guest_id,))
    return cur.rowcount > 0


def is_email_whitelisted(mailbox_id: str, sender_email: str) -> dict | None:
    """Sicherheitskritischer Pfad: prueft ob eine eingehende Email-Adresse
    in der Whitelist steht **UND** noch gueltig ist (enabled=1, expires_at
    in der Zukunft oder leer = nie abgelaufen).

    Returns den Guest-Record (dict) bei Erfolg, sonst None.
    """
    if not mailbox_id or not validate_email_address(sender_email):
        return None
    with _db._conn() as conn:
        r = conn.execute(
            "SELECT * FROM guestprint_guest "
            "WHERE mailbox_id = ? AND sender_email = ? AND enabled = 1",
            (mailbox_id, _norm_email(sender_email))).fetchone()
    if not r:
        return None
    g = dict(r)
    exp = (g.get("expires_at") or "").strip()
    if exp:
        try:
            # ISO-8601 mit Z-Suffix
            iso = exp.rstrip("Z")
            dt = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
            if dt < datetime.now(timezone.utc):
                return None
        except Exception:
            # Defensiv: kaputtes Datum → kein Match
            return None
    return g


# ─── Job-Log ─────────────────────────────────────────────────────────────────

def record_job(*,
                 mailbox_id: str,
                 message_id: str,
                 attachment_name: str,
                 sender_email: str = "",
                 subject: str = "",
                 attachment_bytes: int = 0,
                 guest_id: str = "",
                 printix_job_id: str = "",
                 status: str = "pending",
                 error: str = "") -> int | None:
    """Schreibt einen Job-Eintrag. Idempotent ueber UNIQUE
    (mailbox_id, message_id, attachment_name)."""
    if not (mailbox_id and message_id and attachment_name):
        return None
    now = _now()
    with _db._conn() as conn:
        try:
            cur = conn.execute("""
                INSERT INTO guestprint_job
                  (mailbox_id, guest_id, message_id, sender_email, subject,
                   attachment_name, attachment_bytes, printix_job_id,
                   status, error, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (mailbox_id, guest_id, message_id,
                    _norm_email(sender_email), subject[:500],
                    attachment_name[:200], int(attachment_bytes),
                    printix_job_id, status, error[:500], now, now))
            return cur.lastrowid
        except sqlite3.IntegrityError:
            # Schon vorhanden — Status updaten (z.B. von pending → ok)
            conn.execute("""
                UPDATE guestprint_job SET
                  printix_job_id = COALESCE(NULLIF(?, ''), printix_job_id),
                  status = ?, error = ?, updated_at = ?
                WHERE mailbox_id = ? AND message_id = ? AND attachment_name = ?
            """, (printix_job_id, status, error[:500], now,
                    mailbox_id, message_id, attachment_name[:200]))
            r = conn.execute("""
                SELECT id FROM guestprint_job
                WHERE mailbox_id = ? AND message_id = ? AND attachment_name = ?
            """, (mailbox_id, message_id, attachment_name[:200])).fetchone()
            return r["id"] if r else None


def list_jobs(mailbox_id: str, limit: int = 50) -> list[dict]:
    limit = max(1, min(500, int(limit)))
    if not mailbox_id:
        return []
    with _db._conn() as conn:
        rows = conn.execute("""
            SELECT * FROM guestprint_job
            WHERE mailbox_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (mailbox_id, limit)).fetchall()
    return [dict(r) for r in rows]


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _row_to_dict(row, conn=None, table=""):
    return dict(row) if row else {}
