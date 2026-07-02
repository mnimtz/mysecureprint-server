"""Printix-User-Cache: Sync + Lookup.

Restored in v0.6.3 after slim-commit (f95afe2) accidentally deleted this
module. v0.7.68 restores the sync-side (sync_users_for_tenant) which was
also missing, causing ImportError in the Printix-Sync admin feature.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Sync ─────────────────────────────────────────────────────────────────────

def sync_users_for_tenant(tenant_id: str, printix_tenant_id: str, client) -> dict:
    """Pullt alle Printix-User dieses Tenants und schreibt sie in die DB.

    UPSERT-Logik via UNIQUE(tenant_id, printix_user_id).
    Returns: {"count": N, "status": "ok" | "error", "error": str}
    """
    from db import _conn

    now = datetime.now(timezone.utc).isoformat()
    try:
        users = client.list_all_users(page_size=200)
    except Exception as e:
        err = str(e)[:300]
        logger.error("Sync USERS failed for tenant %s: %s", tenant_id, e)
        _update_sync_status(tenant_id, "users", "error", error=err, count=0)
        return {"count": 0, "status": "error", "error": err}

    if not isinstance(users, list):
        users = []

    inserted = 0
    updated = 0
    with _conn() as conn:
        for u in users:
            if not isinstance(u, dict):
                continue
            pid = (u.get("id") or u.get("userId") or "").strip()
            if not pid:
                continue
            username  = (u.get("username") or u.get("userName") or "").strip()
            email     = (u.get("email") or u.get("userPrincipalName") or "").strip()
            full_name = (u.get("fullName") or u.get("name") or "").strip()
            role      = (u.get("role") or u.get("userRole") or "").strip()
            raw_json  = json.dumps(u, ensure_ascii=True, sort_keys=True)

            existing = conn.execute(
                "SELECT id FROM cached_printix_users "
                "WHERE tenant_id = ? AND printix_user_id = ?",
                (tenant_id, pid),
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE cached_printix_users
                       SET username=?, email=?, full_name=?, role=?,
                           raw_json=?, synced_at=?, printix_tenant_id=?
                       WHERE tenant_id=? AND printix_user_id=?""",
                    (username, email, full_name, role, raw_json, now,
                     printix_tenant_id, tenant_id, pid),
                )
                updated += 1
            else:
                conn.execute(
                    """INSERT INTO cached_printix_users
                       (tenant_id, printix_tenant_id, printix_user_id,
                        username, email, full_name, role, raw_json, synced_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (tenant_id, printix_tenant_id, pid,
                     username, email, full_name, role, raw_json, now),
                )
                inserted += 1

    extra = _upsert_system_manager_from_tenant(tenant_id, printix_tenant_id, now)
    total = inserted + updated + extra
    logger.info(
        "Sync USERS tenant=%s OK: %d eingefügt, %d aktualisiert, %d System-Manager (%d total)",
        tenant_id, inserted, updated, extra, total,
    )
    _update_sync_status(tenant_id, "users", "ok", count=total)
    _check_username_collisions(tenant_id)
    return {
        "count": total, "inserted": inserted, "updated": updated,
        "system_managers": extra, "status": "ok",
    }


def _upsert_system_manager_from_tenant(tenant_id: str, printix_tenant_id: str,
                                        now: str) -> int:
    from db import _conn
    import hashlib
    with _conn() as conn:
        row = conn.execute(
            """SELECT u.email, u.username, u.full_name
               FROM tenants t
               JOIN users u ON u.id = t.user_id
               WHERE t.id = ?""",
            (tenant_id,),
        ).fetchone()
        if not row or not row["email"]:
            return 0
        email = row["email"].strip()
        full_name = (row["full_name"] or email).strip()
        synth_id = f"mgr:{hashlib.sha1(email.lower().encode()).hexdigest()[:16]}"

        existing = conn.execute(
            """SELECT id, role, username FROM cached_printix_users
               WHERE tenant_id = ? AND LOWER(email) = LOWER(?)""",
            (tenant_id, email),
        ).fetchone()
        if existing:
            if (existing["role"] or "").upper() == "SYSTEM_MANAGER" and existing["username"]:
                conn.execute(
                    "UPDATE cached_printix_users SET username = '' WHERE id = ?",
                    (existing["id"],),
                )
            return 0
        conn.execute(
            """INSERT INTO cached_printix_users
               (tenant_id, printix_tenant_id, printix_user_id,
                username, email, full_name, role, raw_json, synced_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (tenant_id, printix_tenant_id, synth_id,
             "", email, full_name, "SYSTEM_MANAGER",
             '{"source":"mcp-tenant-owner","synthetic":true}', now),
        )
        logger.info("Sync: MCP-Tenant-Owner '%s' als SYSTEM_MANAGER in Cache eingefügt", email)
        return 1


def _update_sync_status(tenant_id: str, entity_type: str, status: str,
                         error: str = "", count: int = 0) -> None:
    from db import _conn
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO cached_sync_status
               (tenant_id, entity_type, last_sync_at, last_sync_status,
                last_sync_error, synced_count)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(tenant_id, entity_type) DO UPDATE SET
                 last_sync_at=excluded.last_sync_at,
                 last_sync_status=excluded.last_sync_status,
                 last_sync_error=excluded.last_sync_error,
                 synced_count=excluded.synced_count""",
            (tenant_id, entity_type, now, status, error, count),
        )


def _check_username_collisions(tenant_id: str) -> None:
    from db import _conn
    with _conn() as conn:
        rows = conn.execute(
            """SELECT LOWER(username) AS un, COUNT(DISTINCT tenant_id) AS tc
               FROM cached_printix_users
               WHERE username != ''
               GROUP BY LOWER(username)
               HAVING tc > 1
               LIMIT 20"""
        ).fetchall()
    for row in rows:
        logger.warning(
            "Cache: Username-Kollision — '%s' existiert in %d Tenants.",
            row["un"], row["tc"],
        )


def find_printix_user_by_identity(identity: str) -> Optional[dict]:
    """Suche in cached_printix_users nach Username/E-Mail/Lokal-Part.

    Identifier kann sein:
      - Username       (z.B. 'marcus.nimtz')
      - Volle E-Mail   (z.B. 'marcus@nimtz.email')
      - Lokal-Part     (z.B. 'marcus.nimtz' -> matcht 'marcus.nimtz@x.de')

    Liefert None wenn kein Match oder ambiguous (mehrere Tenants).
    Bei mehreren Treffern im selben Tenant gilt:
      1. Exact username-Match bevorzugt
      2. Reguläre Rollen (USER, GUEST_USER) vor Management-Rollen
    """
    if not identity or not identity.strip():
        return None
    identity = identity.strip()
    from db import _conn
    with _conn() as conn:
        rows = conn.execute(
            """SELECT tenant_id, printix_tenant_id, printix_user_id,
                      username, email, full_name, role, raw_json
               FROM cached_printix_users
               WHERE LOWER(username) = LOWER(?)
                  OR LOWER(email)    = LOWER(?)
                  OR LOWER(email) LIKE LOWER(?)""",
            (identity, identity, f"{identity}@%"),
        ).fetchall()

    if not rows:
        return None

    tenant_ids = {r["tenant_id"] for r in rows}
    if len(tenant_ids) > 1:
        logger.warning(
            "Printix-User-Lookup AMBIGUOUS — '%s' in %d Tenants (%s); "
            "Routing abgelehnt.",
            identity, len(tenant_ids), tenant_ids,
        )
        return None

    def _rank(r):
        role = (r["role"] or "").upper()
        role_penalty = 0 if role in ("USER", "GUEST_USER") else 1
        exact_username = 0 if (r["username"] or "").lower() == identity.lower() else 1
        return (role_penalty, exact_username)

    best = sorted(rows, key=_rank)[0]
    return dict(best)
