"""
CRUD-Helpers für iOS AirPrint-Profile (v0.8.0)
===============================================
Wraps DB-Zugriffe für `cloudprint_airprint_profiles`.

Token-Generierung: base32(sha256(user_id + queue_id + timestamp + secret))[:24]
Nur der Server sieht den vollen Token; er landet in URL des .mobileconfig.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
import uuid as _uuid
from typing import Optional

logger = logging.getLogger("printix.airprint.db")


def _server_secret() -> bytes:
    """Server-lokaler HMAC-Key für Token-Erzeugung. Persistent in DB-Settings.
    Wenn nicht vorhanden: generieren + speichern."""
    from db import get_setting as _gs, set_setting as _ss
    val = _gs("airprint_hmac_secret", "")
    if not val:
        val = secrets.token_hex(32)
        _ss("airprint_hmac_secret", val)
    return val.encode("utf-8")


def _generate_token(user_id: str, queue_id: str) -> str:
    """Erzeugt einen 24-Zeichen Token (base32, URL-safe)."""
    payload = f"{user_id}:{queue_id}:{time.time()}:{secrets.token_hex(8)}"
    mac = hmac.new(_server_secret(), payload.encode("utf-8"),
                   hashlib.sha256).digest()
    return base64.b32encode(mac).decode("ascii").rstrip("=")[:24]


# ─── CRUD ────────────────────────────────────────────────────────────────────

def create_profile(user_id: str,
                    printer_id: str,
                    queue_id: str,
                    queue_display_name: str = "",
                    display_name: str = "",
                    created_via: str = "app") -> dict:
    """Legt ein neues Profil an. Retry bei Token-Kollision (praktisch nie).

    Returns dict mit id, profile_token, ..."""
    from db import _conn
    profile_id = _uuid.uuid4().hex
    for _ in range(3):
        token = _generate_token(user_id, queue_id)
        try:
            with _conn() as conn:
                conn.execute(
                    """INSERT INTO cloudprint_airprint_profiles (
                          id, user_id, profile_token, printer_id, queue_id,
                          queue_display_name, display_name, created_via
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (profile_id, user_id, token, printer_id, queue_id,
                     queue_display_name, display_name, created_via),
                )
            logger.info(
                "AirPrint: neues Profil id=%s user=%s queue=%s via=%s",
                profile_id, user_id, queue_id, created_via,
            )
            return {
                "id": profile_id,
                "user_id": user_id,
                "profile_token": token,
                "printer_id": printer_id,
                "queue_id": queue_id,
                "queue_display_name": queue_display_name,
                "display_name": display_name,
                "created_via": created_via,
                "is_revoked": 0,
            }
        except Exception as e:
            if "UNIQUE" in str(e).upper():
                continue
            raise
    raise RuntimeError("Token-Kollision nach 3 Retries — nicht möglich")


def get_profile_by_id(profile_id: str) -> Optional[dict]:
    from db import _conn
    with _conn() as conn:
        row = conn.execute(
            """SELECT id, user_id, profile_token, printer_id, queue_id,
                       queue_display_name, display_name, created_at,
                       created_via, last_used_at, job_count, is_revoked,
                       revoked_at, revoke_reason
                 FROM cloudprint_airprint_profiles
                WHERE id = ?""",
            (profile_id,),
        ).fetchone()
    return dict(row) if row else None


def get_profile_by_token(token: str) -> Optional[dict]:
    from db import _conn
    with _conn() as conn:
        row = conn.execute(
            """SELECT id, user_id, profile_token, printer_id, queue_id,
                       queue_display_name, display_name, created_at,
                       created_via, last_used_at, job_count, is_revoked
                 FROM cloudprint_airprint_profiles
                WHERE profile_token = ?""",
            (token,),
        ).fetchone()
    return dict(row) if row else None


def list_profiles_for_user(user_id: str,
                            include_revoked: bool = False) -> list[dict]:
    from db import _conn
    with _conn() as conn:
        if include_revoked:
            rows = conn.execute(
                """SELECT id, profile_token, printer_id, queue_id,
                            queue_display_name, display_name, created_at,
                            created_via, last_used_at, job_count, is_revoked
                     FROM cloudprint_airprint_profiles
                    WHERE user_id = ?
                    ORDER BY created_at DESC""",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, profile_token, printer_id, queue_id,
                            queue_display_name, display_name, created_at,
                            created_via, last_used_at, job_count, is_revoked
                     FROM cloudprint_airprint_profiles
                    WHERE user_id = ? AND is_revoked = 0
                    ORDER BY created_at DESC""",
                (user_id,),
            ).fetchall()
    return [dict(r) for r in rows]


def list_all_profiles(include_revoked: bool = False,
                       limit: int = 500) -> list[dict]:
    """Für Admin-UI."""
    from db import _conn
    with _conn() as conn:
        where = "" if include_revoked else "WHERE is_revoked = 0"
        rows = conn.execute(
            f"""SELECT p.id, p.user_id, p.profile_token, p.printer_id,
                        p.queue_id, p.queue_display_name, p.display_name,
                        p.created_at, p.created_via, p.last_used_at,
                        p.job_count, p.is_revoked,
                        u.email    AS user_email,
                        u.username AS user_username
                  FROM cloudprint_airprint_profiles p
             LEFT JOIN users u ON u.id = p.user_id
                       {where}
                 ORDER BY p.created_at DESC
                 LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def revoke_profile(profile_id: str, reason: str = "") -> bool:
    """Markiert Profil als widerrufen. Idempotent."""
    from db import _conn
    with _conn() as conn:
        cur = conn.execute(
            """UPDATE cloudprint_airprint_profiles
                  SET is_revoked = 1,
                      revoked_at = CURRENT_TIMESTAMP,
                      revoke_reason = ?
                WHERE id = ? AND is_revoked = 0""",
            (reason[:400], profile_id),
        )
        changed = cur.rowcount > 0
    if changed:
        logger.info("AirPrint: Profil %s widerrufen (reason=%s)",
                    profile_id, reason[:100])
    return changed


def revoke_all_profiles_for_user(user_id: str, reason: str = "") -> int:
    """Alle Profile eines Users widerrufen (z.B. bei User-Löschung)."""
    from db import _conn
    with _conn() as conn:
        cur = conn.execute(
            """UPDATE cloudprint_airprint_profiles
                  SET is_revoked = 1,
                      revoked_at = CURRENT_TIMESTAMP,
                      revoke_reason = ?
                WHERE user_id = ? AND is_revoked = 0""",
            (reason[:400], user_id),
        )
        n = cur.rowcount
    if n:
        logger.info("AirPrint: %d Profile für user=%s widerrufen", n, user_id)
    return n


def count_profiles_for_user(user_id: str) -> int:
    from db import _conn
    with _conn() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS n
                 FROM cloudprint_airprint_profiles
                WHERE user_id = ? AND is_revoked = 0""",
            (user_id,),
        ).fetchone()
    return row["n"] if row else 0
