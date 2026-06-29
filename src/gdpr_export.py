"""
GDPR / DSGVO Art. 15 — Data Subject Access Request (DSAR) Export
=================================================================
Sammelt alle personenbezogenen Daten, die der Controller (= dieser
Server) ueber einen Data Subject (= einen lokalen MCP-User) speichert,
und liefert sie als strukturiertes Dict zurueck. Der Aufrufer
serialisiert das Dict zu JSON und liefert es als Attachment aus.

Designprinzipien
----------------
* Defensive: jede Datenquelle in einem eigenen try/except, damit ein
  fehlendes Schema (z.B. mobile_invites in alten Deployments) nicht
  den ganzen Export torpediert. Fehler werden geloggt und als Warnung
  im `note`-Feld vermerkt.
* Redaktion: password_hash, OAuth-Secrets, Bearer-Tokens, Fernet-
  verschluesselte Refresh-Tokens, Roh-Invite-Tokens und Invite-Token-
  Hashes werden NICHT herausgegeben (Art. 15 Abs. 4 — Rechte Dritter,
  Datentraeger- und Krypto-Geheimnisse). Stattdessen steht
  ``"<redacted-for-DSAR>"`` an der Stelle, damit der Betroffene sieht,
  *dass* das Feld existiert, ohne dass das Geheimnis offengelegt wird.
* Truncation: Listen werden auf sinnvolle Obergrenzen gekappt, damit
  der Export-File nicht ins Unbegrenzte waechst. Cap-Werte stehen in
  ``DEFAULT_LIST_LIMITS``; jede tatsaechlich gekappte Liste wird im
  ``truncation``-Block dokumentiert.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("printix.gdpr_export")

SCHEMA_VERSION = 1

REDACTED = "<redacted-for-DSAR>"

DEFAULT_LIST_LIMITS = {
    "audit_log":           1000,
    "audit_log_as_target": 1000,
    "mobile_invites":       500,
    "cloudprint_jobs":     1000,
    "delegations_outgoing": 500,
    "delegations_incoming": 500,
    "desktop_tokens":       200,
    "desktop_entra_pending":          50,
    "desktop_entra_authcode_pending": 50,
    "cards":                500,
}


def _safe_dict(row: Any) -> dict:
    try:
        return dict(row)
    except Exception:
        return {}


def _scrub(d: dict, fields: tuple[str, ...]) -> dict:
    """Ersetzt sensitive Felder durch REDACTED. Mutiert NICHT das Original."""
    out = dict(d)
    for f in fields:
        if f in out and out[f] not in (None, "", 0):
            out[f] = REDACTED
    return out


def gdpr_collect_user_data(user_id: str) -> dict:
    """Sammelt alle DSGVO-Art.15-relevanten Daten ueber einen User.

    Args:
        user_id: Die interne ``users.id`` (UUID-string) des Betroffenen.

    Returns:
        Ein strukturiertes Dict (siehe Modul-Docstring). Bei vollstaendigem
        Fehlen eines Subjekts kommt ``{"error": "user_not_found", ...}``
        zurueck — sonst werden alle gefundenen Sektionen befuellt.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    out: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "exported_at":    now_iso,
        "subject":        {},
        "audit_log":             [],
        "audit_log_as_target":   [],
        "mobile_invites":        [],
        "cloudprint_jobs":       [],
        "delegations_outgoing":  [],
        "delegations_incoming":  [],
        "cloudprint_config":     {},
        "desktop_tokens":        [],
        "desktop_entra_pending":          [],
        "desktop_entra_authcode_pending": [],
        "cards":                 [],
        "truncation":            {},
        "warnings":              [],
        "skipped_sources":       [],
        "note":                  (
            "DSAR (Data Subject Access Request) export per DSGVO Art. 15. "
            "Sensitive Felder (password_hash, OAuth-Secrets, Bearer-Tokens, "
            "Entra-Refresh-Tokens, Roh-Invite-Tokens und Invite-Token-Hashes) "
            "wurden gemaess Art. 15 Abs. 4 redigiert. Den `cached_printix_users`-"
            "Cache und Tenant-Credentials Dritter haben wir bewusst ausgelassen — "
            "sie enthalten keine zusaetzlichen Daten des Betroffenen ueber die "
            "hier gelieferten Felder hinaus."
        ),
    }

    if not user_id:
        out["error"] = "user_id_missing"
        return out

    # ── 1) Subjekt-Profil aus users ─────────────────────────────────────
    try:
        from db import _conn
        with _conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        if not row:
            out["error"] = "user_not_found"
            out["subject"] = {}
            return out
        subj = _safe_dict(row)
        subj = _scrub(
            subj,
            (
                "password_hash",
                "entra_refresh_token",
            ),
        )
        out["subject"] = subj
        username = subj.get("username", "")
        printix_user_id = subj.get("printix_user_id", "")
    except Exception as e:
        logger.error("gdpr: subject lookup failed: %s", e, exc_info=True)
        out["error"] = f"subject_lookup_failed: {e}"
        return out

    # ── 2) Audit-Log: Eintraege als Akteur ───────────────────────────────
    lim = DEFAULT_LIST_LIMITS["audit_log"]
    try:
        from db import _conn
        with _conn() as conn:
            rows = conn.execute(
                "SELECT id, action, details, created_at, object_type, "
                "       object_id, tenant_id "
                "FROM audit_log WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, lim + 1),
            ).fetchall()
        items = [_safe_dict(r) for r in rows]
        if len(items) > lim:
            out["truncation"]["audit_log"] = {
                "limit": lim, "kept_newest": True,
            }
            items = items[:lim]
        out["audit_log"] = items
    except Exception as e:
        logger.warning("gdpr: audit_log (user_id) failed: %s", e)
        out["warnings"].append(f"audit_log: {e}")

    # ── 3) Audit-Log: Eintraege ueber diesen User als Ziel ──────────────
    lim = DEFAULT_LIST_LIMITS["audit_log_as_target"]
    try:
        from db import _conn
        with _conn() as conn:
            # object_type='user' + object_id=user_id ist das strukturierte
            # Pattern seit v3.9.0. Aelter: details enthielt user_id als Text;
            # das LIKE-Filter zieht solche Legacy-Eintraege auch.
            rows = conn.execute(
                "SELECT id, user_id AS actor_user_id, action, details, "
                "       created_at, object_type, object_id, tenant_id "
                "FROM audit_log "
                "WHERE (object_type = 'user' AND object_id = ?) "
                "   OR details LIKE ? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, f"%{user_id}%", lim + 1),
            ).fetchall()
        items = [_safe_dict(r) for r in rows]
        if len(items) > lim:
            out["truncation"]["audit_log_as_target"] = {
                "limit": lim, "kept_newest": True,
            }
            items = items[:lim]
        out["audit_log_as_target"] = items
    except Exception as e:
        logger.warning("gdpr: audit_log (target) failed: %s", e)
        out["warnings"].append(f"audit_log_as_target: {e}")

    # ── 4) mobile_invites — eigene Einladungs-Records ────────────────────
    lim = DEFAULT_LIST_LIMITS["mobile_invites"]
    try:
        from db import _conn
        with _conn() as conn:
            rows = conn.execute(
                "SELECT id, user_id, server_url, ttl_seconds, created_at, "
                "       expires_at, redeemed_at, redeemed_from, created_by, "
                "       channel, email_sent_at, email_recipient "
                "FROM mobile_invites WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, lim + 1),
            ).fetchall()
        items = [_safe_dict(r) for r in rows]
        # token + token_hash bewusst NICHT mit selektiert; Roh-Tokens sind
        # nach Erstellung nicht mehr abrufbar, Hashes sind keine Nutzdaten
        # fuer den Betroffenen.
        if len(items) > lim:
            out["truncation"]["mobile_invites"] = {
                "limit": lim, "kept_newest": True,
            }
            items = items[:lim]
        out["mobile_invites"] = items
    except Exception as e:
        logger.warning("gdpr: mobile_invites failed: %s", e)
        out["warnings"].append(f"mobile_invites: {e}")

    # ── 5) cloudprint_jobs — empfangene LPR-Jobs ─────────────────────────
    # Cloud-Print-Jobs sind per `username` und `detected_identity` an den
    # Endbenutzer gebunden. Wir matchen beide gegen lokale username +
    # email, damit auch Jobs aus der Pre-MCP-Identitaet enthalten sind.
    lim = DEFAULT_LIST_LIMITS["cloudprint_jobs"]
    try:
        from db import _conn
        candidates = []
        if username:
            candidates.append(username)
        email_lc = (out["subject"].get("email") or "").strip()
        if email_lc:
            candidates.append(email_lc)
        if printix_user_id:
            candidates.append(printix_user_id)
        items: list[dict] = []
        if candidates:
            placeholders = ",".join(["?"] * len(candidates))
            with _conn() as conn:
                rows = conn.execute(
                    f"SELECT id, job_id, tenant_id, queue_name, username, "
                    f"       hostname, job_name, data_size, data_format, "
                    f"       detected_identity, identity_source, status, "
                    f"       printix_job_id, target_queue, error_message, "
                    f"       received_at, forwarded_at, created_at, "
                    f"       parent_job_id, delegated_from "
                    f"FROM cloudprint_jobs "
                    f"WHERE LOWER(username) IN ({placeholders}) "
                    f"   OR LOWER(detected_identity) IN ({placeholders}) "
                    f"ORDER BY received_at DESC LIMIT ?",
                    tuple(c.lower() for c in candidates) * 2 + (lim + 1,),
                ).fetchall()
            items = [_safe_dict(r) for r in rows]
        if len(items) > lim:
            out["truncation"]["cloudprint_jobs"] = {
                "limit": lim, "kept_newest": True,
            }
            items = items[:lim]
        out["cloudprint_jobs"] = items
    except Exception as e:
        logger.warning("gdpr: cloudprint_jobs failed: %s", e)
        out["warnings"].append(f"cloudprint_jobs: {e}")

    # ── 6) delegations (outgoing) — der Subject delegiert an andere ─────
    lim = DEFAULT_LIST_LIMITS["delegations_outgoing"]
    try:
        from db import _conn
        with _conn() as conn:
            rows = conn.execute(
                "SELECT id, owner_user_id, delegate_user_id, "
                "       delegate_printix_user_id, delegate_email, "
                "       delegate_full_name, status, created_by, "
                "       created_at, updated_at "
                "FROM delegations WHERE owner_user_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, lim + 1),
            ).fetchall()
        items = [_safe_dict(r) for r in rows]
        if len(items) > lim:
            out["truncation"]["delegations_outgoing"] = {
                "limit": lim, "kept_newest": True,
            }
            items = items[:lim]
        out["delegations_outgoing"] = items
    except Exception as e:
        logger.warning("gdpr: delegations_outgoing failed: %s", e)
        out["warnings"].append(f"delegations_outgoing: {e}")

    # ── 7) delegations (incoming) — andere delegieren an den Subject ────
    lim = DEFAULT_LIST_LIMITS["delegations_incoming"]
    try:
        from db import _conn
        with _conn() as conn:
            rows = conn.execute(
                "SELECT id, owner_user_id, delegate_user_id, "
                "       delegate_printix_user_id, delegate_email, "
                "       delegate_full_name, status, created_by, "
                "       created_at, updated_at "
                "FROM delegations "
                "WHERE delegate_user_id = ? "
                "   OR (delegate_printix_user_id != '' "
                "       AND delegate_printix_user_id = ?) "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, printix_user_id or "__none__", lim + 1),
            ).fetchall()
        items = [_safe_dict(r) for r in rows]
        if len(items) > lim:
            out["truncation"]["delegations_incoming"] = {
                "limit": lim, "kept_newest": True,
            }
            items = items[:lim]
        out["delegations_incoming"] = items
    except Exception as e:
        logger.warning("gdpr: delegations_incoming failed: %s", e)
        out["warnings"].append(f"delegations_incoming: {e}")

    # ── 8) cloudprint_config — Queue-Wahl ────────────────────────────────
    # Liegt physisch in tenants (lpr_target_queue, lpr_port). Wir ziehen
    # NUR die Felder, die direkt eine Praeferenz dieses Users dokumentieren.
    try:
        from cloudprint.db_extensions import get_cloudprint_config
        cfg = get_cloudprint_config(user_id) or {}
        out["cloudprint_config"] = {
            "lpr_target_queue": cfg.get("lpr_target_queue", ""),
            "lpr_port":         cfg.get("lpr_port", 0),
        }
    except Exception as e:
        logger.warning("gdpr: cloudprint_config failed: %s", e)
        out["warnings"].append(f"cloudprint_config: {e}")

    # ── 9) desktop_tokens — aktive Desktop-Client-Sessions ───────────────
    lim = DEFAULT_LIST_LIMITS["desktop_tokens"]
    try:
        from db import _conn
        with _conn() as conn:
            rows = conn.execute(
                "SELECT device_name, created_at, last_used_at "
                "FROM desktop_tokens WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, lim + 1),
            ).fetchall()
        items = [_safe_dict(r) for r in rows]
        # `token` (Roh-Wert) bewusst NICHT mitgegeben — ist ein
        # Bearer-Credential, kein personenbezogenes Datum.
        if len(items) > lim:
            out["truncation"]["desktop_tokens"] = {
                "limit": lim, "kept_newest": True,
            }
            items = items[:lim]
        out["desktop_tokens"] = items
    except Exception as e:
        logger.warning("gdpr: desktop_tokens failed: %s", e)
        out["warnings"].append(f"desktop_tokens: {e}")

    # ── 10) desktop_entra_pending / authcode_pending ─────────────────────
    # In-flight OAuth-Records. Pro Session-ID nicht direkt mit user_id
    # verknuepft (der User entsteht ja erst beim Abschluss). Hier kann
    # nur best-effort gefiltert werden — wir liefern die Records, deren
    # device_name den Username enthaelt; in der Praxis ist die Tabelle
    # ueblicherweise leer (Eintraege werden nach erfolgreicher Anmeldung
    # geloescht und durch GC nach Ablauf entfernt).
    for table_name, key in (
        ("desktop_entra_pending",          "desktop_entra_pending"),
        ("desktop_entra_authcode_pending", "desktop_entra_authcode_pending"),
    ):
        lim = DEFAULT_LIST_LIMITS[key]
        try:
            from db import _conn
            with _conn() as conn:
                cols = {
                    r[1]
                    for r in conn.execute(
                        f"PRAGMA table_info({table_name})"
                    ).fetchall()
                }
                if not cols:
                    # Tabelle existiert nicht — kein Eintrag moeglich.
                    continue
                # Defensive Spaltenwahl
                select_cols = [
                    c for c in (
                        "session_id", "device_name", "created_at",
                        "expires_at",
                    ) if c in cols
                ]
                if not select_cols:
                    continue
                # Filter best-effort auf device_name LIKE username
                where = ""
                params: tuple = ()
                if username and "device_name" in cols:
                    where = "WHERE device_name LIKE ? "
                    params = (f"%{username}%",)
                rows = conn.execute(
                    f"SELECT {', '.join(select_cols)} FROM {table_name} "
                    f"{where}ORDER BY created_at DESC LIMIT ?",
                    params + (lim + 1,),
                ).fetchall()
            items = [_safe_dict(r) for r in rows]
            if len(items) > lim:
                out["truncation"][key] = {"limit": lim, "kept_newest": True}
                items = items[:lim]
            out[key] = items
        except Exception as e:
            logger.warning("gdpr: %s failed: %s", table_name, e)
            out["warnings"].append(f"{key}: {e}")

    # ── 11) card_mappings — Karten-Zuordnungen ───────────────────────────
    # Karten haengen am Printix-User (printix_user_id), nicht direkt am
    # MCP-User. Nur Subjects mit gesetztem printix_user_id haben Karten
    # zu exportieren.
    lim = DEFAULT_LIST_LIMITS["cards"]
    if printix_user_id:
        try:
            from db import _conn
            with _conn() as conn:
                rows = conn.execute(
                    "SELECT id, tenant_id, printix_user_id, printix_card_id, "
                    "       source, notes, profile_id, created_at, updated_at "
                    "FROM card_mappings WHERE printix_user_id = ? "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (printix_user_id, lim + 1),
                ).fetchall()
            items = [_safe_dict(r) for r in rows]
            # local_value/final_value/preview_json bewusst NICHT mit
            # exportiert — das sind Fernet-verschluesselte Roh-Karten-
            # IDs (UID-Bytes); deren Klartext ist beim Aufrufer ohnehin
            # erst nach `_dec()` lesbar und stellt einen Authentifizierungs-
            # token am Multifunktionsgeraet dar. Wer den DSAR-Export
            # bekommt, soll keinen verwendbaren Karten-UID erhalten.
            if len(items) > lim:
                out["truncation"]["cards"] = {
                    "limit": lim, "kept_newest": True,
                }
                items = items[:lim]
            out["cards"] = items
        except Exception as e:
            logger.warning("gdpr: card_mappings failed: %s", e)
            out["warnings"].append(f"cards: {e}")
    else:
        out["skipped_sources"].append(
            "cards: kein printix_user_id am Subject hinterlegt"
        )

    # ── 12) Bewusst ausgelassene Quellen dokumentieren ───────────────────
    out["skipped_sources"].extend(
        [
            "cached_printix_users: lokaler Cache der Printix-User-Management-"
            "API; alle Felder sind Spiegelungen von Daten, die der Printix-"
            "Tenant selbst kontrolliert. Anfrage an den jeweiligen Printix-"
            "Tenant-Owner stellen.",
            "tenant_logs / capture_profiles / guestprint_*: enthalten nur "
            "Mandanten- und Konfigurations-Daten ohne personenbezogene "
            "Identifikatoren des Subjects.",
        ]
    )

    return out
