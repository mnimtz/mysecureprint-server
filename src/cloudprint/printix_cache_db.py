"""Slim Printix-User-Cache-Lookup.

Restored in v0.6.3 after the slim-commit (f95afe2) accidentally deleted
the original printix_cache_db.py while keeping 5+ import sites
(`desktop_routes`, `db_extensions`, etc.) referring to
`find_printix_user_by_identity`. Without this module, every code path
that resolves a Printix user from an incoming LPR-job identity
ImportError-crashed at first call.

Only the lookup helper is restored — the sync-side of the old module
(sync_users_for_tenant, _upsert_system_manager_from_tenant) is owned
elsewhere now (see cloudprint/db_extensions.py and the printix-sync
background task).
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


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
