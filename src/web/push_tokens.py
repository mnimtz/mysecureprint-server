"""APNs device-token store.

Tabelle `push_tokens` (SQLite):
  device_token   TEXT PRIMARY KEY
  user_id        TEXT NOT NULL
  environment    TEXT  ('sandbox' | 'production')
  created_at     TEXT
  updated_at     TEXT
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("printix.push_tokens")

_schema_ready = False


def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    from db import _conn
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS push_tokens (
                device_token   TEXT PRIMARY KEY,
                user_id        TEXT NOT NULL,
                environment    TEXT NOT NULL DEFAULT 'production',
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_push_tokens_user
            ON push_tokens (user_id)
        """)
    _schema_ready = True


def init_push_schema() -> None:
    _ensure_schema()


def register_push_token(user_id: str, device_token: str,
                         environment: str = "production") -> None:
    _ensure_schema()
    now = datetime.now(timezone.utc).isoformat()
    from db import _conn
    with _conn() as conn:
        conn.execute("""
            INSERT INTO push_tokens (device_token, user_id, environment, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(device_token) DO UPDATE SET
                user_id=excluded.user_id,
                environment=excluded.environment,
                updated_at=excluded.updated_at
        """, (device_token, user_id, environment, now, now))
    logger.debug("push_token registered: user=%s env=%s", user_id, environment)


def remove_push_token(device_token: str) -> None:
    _ensure_schema()
    from db import _conn
    with _conn() as conn:
        conn.execute("DELETE FROM push_tokens WHERE device_token = ?",
                     (device_token,))
    logger.debug("push_token removed: %s", device_token[:12] + "…")


def get_tokens_for_user(user_id: str) -> list[dict]:
    _ensure_schema()
    from db import _conn
    with _conn() as conn:
        rows = conn.execute(
            "SELECT device_token, environment FROM push_tokens WHERE user_id = ?",
            (user_id,)
        ).fetchall()
    return [{"device_token": r[0], "environment": r[1]} for r in rows]


def _get_push_token_count() -> int:
    try:
        _ensure_schema()
        from db import _conn
        with _conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM push_tokens").fetchone()
        return row[0] if row else 0
    except Exception:
        return 0
