"""
iOS Push-Token Registry (v0.7.72)
====================================
Speichert APNs Device-Tokens pro User + Gerät. Ein User kann beliebig
viele Tokens haben (mehrere Geräte/Neuinstallationen).

Tabelle push_tokens:
  device_token  TEXT PRIMARY KEY    — 64-Hex-String vom APNs
  user_id       TEXT                — wem gehört das Gerät
  desktop_token TEXT                — zugehöriger Desktop-Token (für Gerätename)
  environment   TEXT                — "production" | "sandbox"
  registered_at TEXT
  last_used_at  TEXT
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("printix.push_tokens")

_schema_ready = False


def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    try:
        init_push_schema()
    except Exception as e:
        logger.warning("push_tokens schema lazy-init failed: %s", e)


def init_push_schema() -> None:
    global _schema_ready
    from db import _conn
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS push_tokens (
                device_token   TEXT PRIMARY KEY,
                user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                desktop_token  TEXT NOT NULL DEFAULT '',
                environment    TEXT NOT NULL DEFAULT 'production',
                registered_at  TEXT NOT NULL,
                last_used_at   TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_push_tokens_user
                ON push_tokens (user_id);
        """)
    _schema_ready = True
    logger.info("push_tokens: Schema geprüft/erstellt")


def register_push_token(
    user_id: str,
    device_token: str,
    desktop_token: str = "",
    environment: str = "production",
) -> None:
    """Speichert oder aktualisiert einen APNs Device-Token."""
    _ensure_schema()
    from db import _conn
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO push_tokens
                   (device_token, user_id, desktop_token, environment,
                    registered_at, last_used_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(device_token) DO UPDATE SET
                   user_id       = excluded.user_id,
                   desktop_token = excluded.desktop_token,
                   environment   = excluded.environment,
                   last_used_at  = excluded.last_used_at""",
            (device_token, user_id, desktop_token or "", environment, now, now),
        )
    logger.info(
        "push_tokens: Token registriert user=%s token=...%s env=%s",
        user_id, device_token[-8:], environment,
    )


def remove_push_token(device_token: str) -> None:
    """Löscht einen ungültig gewordenen Token."""
    _ensure_schema()
    from db import _conn
    with _conn() as conn:
        conn.execute(
            "DELETE FROM push_tokens WHERE device_token = ?", (device_token,)
        )
    logger.info("push_tokens: Token entfernt ...%s", device_token[-8:])


def remove_push_tokens_for_desktop_token(desktop_token: str) -> None:
    """Bereinigt Push-Tokens wenn ein Desktop-Token widerrufen wird."""
    _ensure_schema()
    from db import _conn
    with _conn() as conn:
        conn.execute(
            "DELETE FROM push_tokens WHERE desktop_token = ?", (desktop_token,)
        )


def get_push_tokens_for_user(user_id: str) -> list[str]:
    """Gibt alle gültigen Device-Token-Strings für einen User zurück."""
    _ensure_schema()
    from db import _conn
    with _conn() as conn:
        rows = conn.execute(
            "SELECT device_token FROM push_tokens WHERE user_id = ?", (user_id,)
        ).fetchall()
    return [r[0] for r in rows]
