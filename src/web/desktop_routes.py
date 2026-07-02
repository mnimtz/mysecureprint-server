"""
Desktop-API-Routen (v6.7.31)
=============================
Endpunkte für den „Printix Send"-Windows-Client (und spätere Desktop-
Clients). Alle Routen sind Token-basiert authentifiziert via
`Authorization: Bearer <token>`.

Endpoints:
  POST /desktop/auth/login            — Credentials → Token
  POST /desktop/auth/logout           — Widerruft aktuellen Token
  GET  /desktop/me                    — Kurze User-Info für Token-Validation
  GET  /desktop/targets               — Zielliste für den aktuellen User
  POST /desktop/send                  — Datei-Upload + Dispatching
  GET  /desktop/client/latest-version — Update-Check (self-describing)

Response-Format: immer JSON. Fehler als `{"error": "…", "code": "…"}`
mit passendem HTTP-Status.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, FastAPI, File, Form, Header, Request, UploadFile
from fastapi.responses import JSONResponse

from desktop_auth import (
    create_token, validate_token, revoke_token, list_tokens_for_user,
)

logger = logging.getLogger("printix.desktop")


# ─── Auth-Helper ─────────────────────────────────────────────────────────────

def _require_token(authorization: Optional[str]) -> Optional[dict]:
    """Extrahiert Token aus Auth-Header und validiert gegen DB."""
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return validate_token(parts[1].strip())


def _json_error(msg: str, code: str = "error", status: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg, "code": code}, status_code=status)


# ─── Logging-Helpers ─────────────────────────────────────────────────────────

def _client_info(request: Request) -> dict:
    """Strukturierte Info über den Request-Absender für Log-Zeilen."""
    peer = request.client.host if request.client else "?"
    ua   = request.headers.get("user-agent", "-")[:120]
    host = request.headers.get("host", "-")
    return {"peer": peer, "ua": ua, "host": host}


# v0.7.30: Rate-Limiter fuer /desktop/auth/login. Bare-Metal Token-
# Bucket: pro Key erlauben wir MAX_TRIES in WINDOW Sekunden, danach 429
# mit Retry-After. Reset bei erfolgreichem Login. Thread-safe.
import threading as _t_threading
import time as _t_time

_AUTH_RL_LOCK = _t_threading.Lock()
_AUTH_RL_BUCKETS: dict[str, list[float]] = {}
_AUTH_RL_WINDOW = 300.0   # 5 min
_AUTH_RL_MAX = 8           # 8 Versuche pro Fenster pro Bucket


def _auth_rl_key(request: Request, username: str) -> tuple[str, str]:
    """Zwei Keys — pro-IP und pro-Username. Angreifer muss beide
    Buckets fuellen um weiterzumachen."""
    peer = (request.client.host if request.client else "") or "unknown"
    uname = (username or "").strip().lower() or "-"
    return f"ip:{peer}", f"user:{uname}"


def _auth_rl_check(request: Request, username: str) -> Optional[float]:
    """Returns None wenn der Login erlaubt ist, sonst die Anzahl Sekunden
    bis der naechste Versuch OK ist (Retry-After)."""
    now = _t_time.monotonic()
    horizon = now - _AUTH_RL_WINDOW
    keys = _auth_rl_key(request, username)
    with _AUTH_RL_LOCK:
        wait_max: float = 0.0
        for k in keys:
            arr = _AUTH_RL_BUCKETS.get(k, [])
            # abgelaufene Eintraege aufraeumen
            arr = [t for t in arr if t > horizon]
            _AUTH_RL_BUCKETS[k] = arr
            if len(arr) >= _AUTH_RL_MAX:
                # oldest fell out of window when?
                wait = arr[0] - horizon
                if wait > wait_max:
                    wait_max = wait
        if wait_max > 0:
            return wait_max
        for k in keys:
            _AUTH_RL_BUCKETS[k].append(now)
    return None


def _auth_rl_reset(request: Request, username: str) -> None:
    """Bei erfolgreichem Login das User-Bucket leeren. IP-Bucket
    bleibt — sonst kann man mit einem gueltigen Account das IP-Limit
    fuer andere Accounts sabotieren."""
    _, user_key = _auth_rl_key(request, username)
    with _AUTH_RL_LOCK:
        _AUTH_RL_BUCKETS.pop(user_key, None)


def _log_req(request: Request, endpoint: str, extra: str = "") -> dict:
    """Einzeiler pro Request — Format analog zu IPP-HTTP.
    Returns ci-dict für späteren Gebrauch (z.B. in Fehler-Logs)."""
    ci = _client_info(request)
    logger.info(
        "Desktop: %s peer=%s host=%s UA=%s%s",
        endpoint, ci["peer"], ci["host"], ci["ua"],
        f" {extra}" if extra else "",
    )
    return ci



def _hsid(session_id: str) -> str:
    """v0.7.32: kurzer SHA256-Prefix statt raw-Substring. Damit ist ein
    geleaktes Log-Fragment kein brute-force-Enabler mehr."""
    import hashlib as _hl
    if not session_id:
        return "-"
    return _hl.sha256(session_id.encode()).hexdigest()[:8]


def _mask_token(token: Optional[str]) -> str:
    """Zeigt nur die letzten 8 Zeichen — nie den vollen Token im Log."""
    if not token:
        return "-"
    return f"…{token[-8:]}" if len(token) > 10 else "…"


def _user_descr(user: dict) -> str:
    """v0.7.5: lesbare User-Beschreibung fuers Log — full_name (username,
    email) [px:short_printix_id]. Wenn full_name leer, fallback auf
    username. Hilft bei Diagnose statt nur 'user=Marcus'."""
    if not user:
        return "<none>"
    full = (user.get("full_name") or "").strip()
    uname = (user.get("username") or "").strip()
    email = (user.get("email") or "").strip()
    pxid = (user.get("printix_user_id") or "").strip()
    parts = []
    parts.append(full or uname or email or "?")
    extra = []
    if full and uname and uname != full:
        extra.append(uname)
    if email:
        extra.append(email)
    if extra:
        parts.append(f"({', '.join(extra)})")
    if pxid:
        parts.append(f"[px:{pxid[:8]}]")
    return " ".join(parts)


# ─── Registrierung ───────────────────────────────────────────────────────────

async def _process_desktop_send_bg(
    user: dict,
    target_id: str,
    data: bytes,
    filename: str,
    copies: int,
    color: str,
    duplex: str,
    internal_id: str,
    t_start: float,
) -> None:
    """
    Background-Worker für /desktop/send (v6.7.43).

    Läuft als asyncio.Task, nachdem der HTTP-Handler bereits 202 Accepted
    zurückgegeben hat. Hintergrund: Cloudflare kappt jede HTTP-Verbindung
    nach 100 s (HTTP 524), aber die Printix-Pipeline (LibreOffice-Konvertierung
    + 5-Stage-Submit) braucht regelmäßig 90–180 s. Fire-and-forget umgeht
    diese Architektur-Grenze; Fehler landen im cloudprint_jobs-Eintrag
    und werden dort über die Web-UI sichtbar.
    """
    import time as _t
    try:
        import sys as _sys, os as _os
        src_dir = _os.path.dirname(_os.path.dirname(__file__))
        if src_dir not in _sys.path:
            _sys.path.insert(0, src_dir)
        from upload_converter import convert_to_pdf, ConversionError
        from db import get_tenant_full_by_user_id, _resolve_tenant_owner_for
        from cloudprint.db_extensions import (
            get_cloudprint_config,
            get_delegations_for_owner, create_cloudprint_job,
            update_cloudprint_job_status,
        )
        from cloudprint.printix_cache_db import find_printix_user_by_identity

        def _fail(msg: str, code: str = "error") -> None:
            try:
                update_cloudprint_job_status(
                    internal_id, "error",
                    error_message=f"{code}: {msg}"[:500],
                )
            except Exception:
                pass
            logger.warning(
                "Desktop-Send BG-FAIL — user='%s' target=%s job_id=%s "
                "code=%s msg=%s",
                _user_descr(user), target_id, internal_id, code, msg,
            )
            # v6.7.115: Audit-Trail für fehlgeschlagene iOS-Print-Jobs.
            try:
                import json as _json
                from db import audit as _audit
                _details = _json.dumps({
                    "target_id": target_id,
                    "job_filename": filename,
                    "job_size_bytes": len(data) if isinstance(data, (bytes, bytearray)) else None,
                    "source": "ios_app",
                    "error_code": code,
                    "error": (msg or "")[:300],
                }, ensure_ascii=False)
                _audit(
                    user.get("user_id"),
                    "print_job_failed",
                    details=_details,
                    object_type="print_job",
                    object_id=internal_id,
                )
            except Exception as _ae:
                logger.debug("audit(print_job_failed) failed: %s", _ae)
            # v0.7.72: Push-Benachrichtigung — Job fehlgeschlagen.
            try:
                from push_notify import notify_user as _push_fail
                _push_fail(
                    user_id=user.get("user_id", ""),
                    title="Druckauftrag fehlgeschlagen",
                    body=f"„{filename}" konnte nicht gesendet werden: {msg[:80]}",
                    extra={"job_id": internal_id, "error_code": code},
                    collapse_id=f"job:{internal_id}",
                )
            except Exception as _pfe:
                logger.debug("push(print_job_failed) failed: %s", _pfe)

        # === Stage 1: Format-Erkennung + Konvertierung =====================
        t_convert_start = _t.monotonic()
        try:
            pdf_data, conv_label = convert_to_pdf(data, filename)
            if pdf_data is not data:
                base = filename.rsplit(".", 1)[0] if "." in filename else filename
                display_filename = f"{base}.pdf"
                data = pdf_data
            else:
                display_filename = filename
            dt_conv = _t.monotonic() - t_convert_start
            logger.info(
                "Desktop-Send [1/5] convert OK — user='%s' conv='%s' "
                "out_size=%d dt=%.2fs job_id=%s",
                _user_descr(user), conv_label, len(data), dt_conv, internal_id,
            )
        except ConversionError as ce:
            logger.warning(
                "Desktop-Send [1/5] convert FAIL — user='%s' file='%s' err=%s",
                _user_descr(user), filename, ce,
            )
            _fail(str(ce), code="convert_failed")
            return
        except Exception as e:
            logger.error(
                "Desktop-Send [1/5] convert EXCEPTION — user='%s' file='%s' err=%s",
                _user_descr(user), filename, e,
            )
            _fail(str(e)[:200], code="convert_error")
            return

        # === Stage 2: Tenant + Queue + Owner-Email =========================
        parent_id = _resolve_tenant_owner_for(user["user_id"])
        tenant = get_tenant_full_by_user_id(parent_id) if parent_id else None
        config = get_cloudprint_config(user["user_id"])

        if not tenant or not config or not config.get("lpr_target_queue"):
            fallback_source = ""
            try:
                from cloudprint.db_extensions import (
                    get_default_single_tenant, get_admin_tenant_with_queue,
                )
                fallback = get_default_single_tenant()
                if fallback and fallback.get("lpr_target_queue"):
                    fallback_source = "single-tenant"
                else:
                    fallback = get_admin_tenant_with_queue()
                    if fallback:
                        fallback_source = "admin-tenant"
                if fallback and fallback.get("lpr_target_queue"):
                    tenant = get_tenant_full_by_user_id(fallback["user_id"])
                    config = fallback
                    logger.info(
                        "Desktop-Send [2/5] fallback-tenant (%s) — user='%s' "
                        "→ tenant.user_id=%s queue=%s",
                        fallback_source, _user_descr(user),
                        fallback.get("user_id"),
                        fallback.get("lpr_target_queue"),
                    )
            except Exception as _fb:
                logger.debug("Tenant-Fallback failed: %s", _fb)

        # v0.7.2: 3-Tier-Resolver als letzte Stufe konsultieren bevor wir
        # 'no_queue' werfen. /desktop/targets nutzt resolve_user_queue()
        # bereits — die iOS-App zeigt also die korrekte Default-Queue an,
        # aber /desktop/send checkte vorher nur die Legacy-Spalte
        # tenants.lpr_target_queue und brach mit no_queue ab obwohl ein
        # globaler Default per allow_user_queue_override + default_lpr_target_queue
        # konfiguriert war. Jetzt: wenn config kein lpr_target_queue hat,
        # versuche den 3-Tier-Resolver (user_override → group → global).
        if not config or not config.get("lpr_target_queue"):
            try:
                from cloudprint.db_extensions import resolve_user_queue
                rq_id, rq_label, rq_source = resolve_user_queue(user["user_id"])
                if rq_id:
                    if not config:
                        config = {}
                    config["lpr_target_queue"] = rq_id
                    if rq_label:
                        config["lpr_target_queue_label"] = rq_label
                    # tenant kann hier noch None sein — get_tenant_full_by_user_id
                    # mit dem User selbst nochmal versuchen, sonst Admin-Tenant
                    if not tenant:
                        tenant = (get_tenant_full_by_user_id(user["user_id"])
                                  or get_tenant_full_by_user_id(parent_id))
                    logger.info(
                        "Desktop-Send [2/5] 3-tier resolver hit — user='%s' "
                        "source=%s queue=%s",
                        _user_descr(user), rq_source, rq_id,
                    )
            except Exception as _re:
                logger.debug("3-tier resolver failed in send-path: %s", _re)

        if not tenant or not config or not config.get("lpr_target_queue"):
            # v0.7.5: detaillierte Diagnose — welche der 3 Quellen war leer?
            _debug_lines = []
            try:
                from cloudprint.db_extensions import (
                    is_user_queue_override_allowed, get_cloudprint_config,
                    get_global_default_queue, get_user_printix_group_ids,
                    get_group_queue_default,
                )
                _override_allowed = is_user_queue_override_allowed()
                _user_cfg = get_cloudprint_config(user["user_id"]) or {}
                _user_q = _user_cfg.get("lpr_target_queue", "")
                _debug_lines.append(
                    f"override_allowed={_override_allowed} user_q='{_user_q}'"
                )
                if tenant:
                    _tid = tenant.get("id") or tenant.get("tenant_id") or ""
                    _grp_ids = get_user_printix_group_ids(user["user_id"]) or []
                    _grp_qs = []
                    for _gid in _grp_ids:
                        _gd = get_group_queue_default(_tid, _gid)
                        if _gd:
                            _grp_qs.append(f"{_gid}={_gd.get('queue_id','')}")
                    _debug_lines.append(
                        f"group_ids={len(_grp_ids)} group_qs=[{', '.join(_grp_qs) or '<none>'}]"
                    )
                _g_id, _g_lbl = get_global_default_queue()
                _debug_lines.append(f"global_q='{_g_id}' global_lbl='{_g_lbl}'")
            except Exception as _de:
                _debug_lines.append(f"diag_failed={_de}")
            logger.warning(
                "Desktop-Send [2/5] no queue — user=%s parent_id=%s tenant=%s "
                "queue=%s | diag: %s",
                _user_descr(user), parent_id, bool(tenant),
                (config or {}).get("lpr_target_queue"),
                "; ".join(_debug_lines),
            )
            _fail("no secure print queue configured", code="no_queue")
            return

        # Owner-Email ermitteln (für Default: den User selbst).
        # v0.7.22: Email WIEDER lowercase. Direkter Test gegen Printix
        # Cloud Print API bestaetigt: changeOwner/submit Endpoints sind
        # case-sensitive. `marcus@nimtz.email` → 200 OK,
        # `Marcus@nimtz.email` → 404 USER_NOT_FOUND. Der Print-Portal-
        # Display-Name in der User-Liste war eine UI-Sache, nicht die
        # canonical email. Mein v0.7.10-Revert war falsch.
        user_email = (user.get("email") or "").strip().lower()
        owner_email = user_email
        try:
            px_id = (user.get("printix_user_id") or "").strip()
            if px_id:
                from db import _conn as _dbconn
                with _dbconn() as _c:
                    row = _c.execute(
                        "SELECT email FROM cached_printix_users "
                        "WHERE printix_user_id=?", (px_id,),
                    ).fetchone()
                if row and row["email"]:
                    owner_email = (row["email"] or "").strip().lower()
            if not owner_email or "@" not in owner_email:
                pxu = find_printix_user_by_identity(user_email)
                if pxu and pxu.get("email"):
                    owner_email = (pxu["email"] or "").strip().lower()
        except Exception:
            pass

        target_id = (target_id or "").strip()
        target_type = ""
        if target_id == "print:self":
            submit_user_email = owner_email
            target_type = "print_secure"
        elif target_id.startswith("capture:"):
            # Capture-Targets sind in mysecureprint-server entfernt.
            _fail("capture targets are not supported in mysecureprint-server", code="target_unsupported")
            return
            # ------------- unreachable legacy code below ------------------
            profile_id = target_id.split(":", 1)[1].strip()
            from db import get_capture_profile, add_capture_log  # noqa: F401

            profile = get_capture_profile(profile_id)
            if not profile:
                _fail("capture profile not found", code="target_not_found")
                return
            if not profile.get("is_active"):
                _fail("capture profile is disabled", code="target_disabled")
                return
            if tenant and profile.get("tenant_id") != tenant.get("id"):
                _fail("capture profile not accessible", code="target_forbidden")
                return

            plugin = create_plugin_instance(
                profile.get("plugin_type", ""),
                profile.get("config_json", "{}"),
            )
            if not plugin:
                _fail(
                    f"unknown capture plugin: {profile.get('plugin_type')}",
                    code="plugin_unknown",
                )
                return

            plugin_metadata = {
                "_source":       "desktop-send",
                "_user_name":    user.get("username", ""),
                "_user_email":   owner_email,
                "_device_name":  user.get("device_name", ""),
                "_filename":     display_filename,
                "title":         display_filename.rsplit(".", 1)[0]
                                 if "." in display_filename else display_filename,
            }

            t_up = _t.monotonic()
            try:
                ok, msg = await plugin.ingest_bytes(data, display_filename, plugin_metadata)
            except NotImplementedError as _ne:
                _fail(
                    f"Plugin '{profile.get('plugin_type')}' unterstützt keinen Direkt-Upload.",
                    code="plugin_no_ingest",
                )
                return
            except Exception as _pe:
                logger.exception(
                    "Desktop-Send [4/5] capture-plugin EXCEPTION — user='%s' "
                    "plugin=%s err=%s",
                    _user_descr(user), profile.get("plugin_type"), _pe,
                )
                _fail(str(_pe)[:200], code="plugin_error")
                return
            dt_up = _t.monotonic() - t_up

            try:
                add_capture_log(
                    profile["tenant_id"], profile_id, profile.get("name", ""),
                    "DesktopSend", "ok" if ok else "error", msg or "",
                    details=f"user={user.get('username')}, "
                            f"file={display_filename}, size={len(data)}",
                )
            except Exception as _le:
                logger.debug("capture-log write failed: %s", _le)

            dt_total = _t.monotonic() - t_start
            if ok:
                try:
                    update_cloudprint_job_status(
                        internal_id, "forwarded",
                        target_queue=f"capture:{profile.get('plugin_type', '')}",
                    )
                except Exception:
                    pass
                logger.info(
                    "Desktop-Send COMPLETE (capture) — user='%s' target=%s "
                    "profile='%s' plugin=%s file='%s' size=%d dt_plugin=%.2fs "
                    "total_dt=%.2fs job_id=%s",
                    user["username"], target_id, profile.get("name", ""),
                    profile.get("plugin_type"), display_filename, len(data),
                    dt_up, dt_total, internal_id,
                )
            else:
                _fail(msg or "capture plugin returned failure", code="capture_failed")
            return
        elif target_id.startswith("print:delegate:"):
            # v0.7.26: Admin-Flag-Check (defense-in-depth — iOS rendert
            # die Targets ohnehin nicht, aber Direct-API-Calls gehen sonst durch)
            try:
                from db import get_setting as _gs_d
                _del_ok = ((_gs_d("delegation_print_allowed", "0") or "0").strip()
                           in ("1","true","yes","on"))
            except Exception:
                _del_ok = False
            if not _del_ok:
                _fail("delegation print disabled by admin",
                      code="delegation_disabled")
                return
            deleg_id = target_id.split(":", 2)[2]
            try:
                delegs = get_delegations_for_owner(user["user_id"])
                delegate = next((d for d in delegs if str(d.get("id")) == str(deleg_id)), None)
            except Exception as _e:
                logger.warning(
                    "Desktop-Send [2/5] delegate-lookup err — user='%s' "
                    "deleg_id=%s: %s",
                    _user_descr(user), deleg_id, _e,
                )
                delegate = None
            if not delegate or not delegate.get("delegate_email"):
                _fail("delegate target not found", code="target_not_found")
                return
            # v0.7.23: lowercase — Printix changeOwner ist case-sensitive
            submit_user_email = (delegate["delegate_email"] or "").strip().lower()
            target_type = "print_delegate"
        elif target_id.startswith("print:user:"):
            # v0.7.26: Admin-Flag-Check fuer print:user:* (gleiche Logik
            # wie print:delegate:* — sonst Workaround moeglich).
            try:
                from db import get_setting as _gs_pu
                _del_ok_pu = ((_gs_pu("delegation_print_allowed", "0") or "0").strip()
                              in ("1","true","yes","on"))
            except Exception:
                _del_ok_pu = False
            if not _del_ok_pu:
                _fail("delegation print disabled by admin",
                      code="delegation_disabled")
                return
            # v0.5.4: Delegation-Print an einen beliebigen Printix-User.
            # iOS-Picker auf dem Ziele-Tab erzeugt diese IDs wenn der
            # Toggle „Delegation-Druck erlauben" an ist. Wir loesen den
            # User ueber die cached_printix_users-Tabelle (gleicher Tenant)
            # und nutzen seine Email als submitUserEmail — der Job landet
            # in SEINER SecurePrint-Queue mit ihm als Job-Owner.
            target_printix_id = target_id.split(":", 2)[2].strip()
            target_user_email = ""
            target_user_full_name = ""
            try:
                from db import _conn as _db_conn_dgu
                tenant_local_id = (tenant or {}).get("id", "")
                with _db_conn_dgu() as _c:
                    row = _c.execute(
                        """SELECT email, full_name FROM cached_printix_users
                           WHERE printix_user_id = ?
                             AND (? = '' OR tenant_id = ?)
                           LIMIT 1""",
                        (target_printix_id, tenant_local_id, tenant_local_id),
                    ).fetchone()
                if row:
                    # v0.7.23: lowercase — Printix changeOwner case-sensitive
                    target_user_email = (row["email"] or "").strip().lower()
                    target_user_full_name = (row["full_name"] or "").strip()
            except Exception as _du:
                logger.warning(
                    "Desktop-Send [2/5] delegation-user-lookup err — "
                    "user='%s' target_id=%s err=%s",
                    _user_descr(user), target_printix_id, _du,
                )
            # v0.7.24: Cache-Miss-Fallback. Wenn der User nicht in
            # cached_printix_users liegt (Cache leer/stale), holen wir
            # ihn LIVE von der Printix-User-Management-API.
            if not target_user_email and tenant:
                try:
                    logger.info(
                        "Desktop-Send [2/5] delegation-user cache-miss — "
                        "trying live Printix lookup for printix_user_id=%s",
                        target_printix_id,
                    )
                    from printix_client import PrintixClient as _PxClient
                    _lookup_client = _PxClient(
                        tenant_id=tenant.get("printix_tenant_id", ""),
                        print_client_id=tenant.get("print_client_id", ""),
                        print_client_secret=tenant.get("print_client_secret", ""),
                        card_client_id=tenant.get("card_client_id", ""),
                        card_client_secret=tenant.get("card_client_secret", ""),
                        um_client_id=tenant.get("um_client_id", ""),
                        um_client_secret=tenant.get("um_client_secret", ""),
                        shared_client_id=tenant.get("shared_client_id", ""),
                        shared_client_secret=tenant.get("shared_client_secret", ""),
                    )
                    live = _lookup_client.get_user(target_printix_id)
                    # v0.7.25: Printix wrappt user in {"user": {...}, "success":...}
                    if isinstance(live, dict):
                        u = live.get("user", live) if isinstance(live.get("user"), dict) else live
                        target_user_email = (u.get("email")
                            or u.get("username") or "").strip().lower()
                        target_user_full_name = (u.get("fullName")
                            or u.get("name")
                            or u.get("displayName") or "").strip()
                except Exception as _le:
                    logger.warning(
                        "Desktop-Send [2/5] live-lookup auch failed — "
                        "user='%s' target_id=%s err=%s",
                        _user_descr(user), target_printix_id, _le,
                    )
            if not target_user_email or "@" not in target_user_email:
                _fail("delegation user not found or has no email",
                      code="target_not_found")
                return
            # Audit: Wer-an-Wen delegiert hat (Compliance).
            try:
                from db import audit as _audit_dgu
                _audit_dgu(
                    user.get("user_id"),
                    "print_job_delegated",
                    details=(f"to_printix_user={target_printix_id} "
                             f"to_email={target_user_email} "
                             f"to_name={target_user_full_name} "
                             f"job_id={internal_id}"),
                    object_type="print_job",
                    object_id=internal_id,
                )
            except Exception:
                pass
            submit_user_email = target_user_email
            target_type = "print_user_delegation"
            logger.info(
                "Desktop-Send [2/5] delegation-user — sender='%s' → "
                "target='%s' (%s) target_id=%s job_id=%s",
                _user_descr(user), target_user_email,
                target_user_full_name, target_id, internal_id,
            )
        elif target_id.startswith("print:queue:"):
            # v0.6.0: Spezifische Printix-Queue-Auswahl (vs. default-resolution).
            # User picks aus /desktop/queues eine konkrete Queue um den Job
            # NICHT in die persoenliche SecurePrint-Queue zu legen sondern
            # direkt z.B. an einen Drucker im Konferenzraum zu schicken.
            chosen_queue_id = target_id.split(":", 2)[2].strip()
            # submit_user_email bleibt = owner_email (User ist Job-Owner),
            # aber wir ueberschreiben die Ziel-Queue beim Submit unten.
            submit_user_email = owner_email
            target_type = "print_queue_specific"
            # Trick: config["lpr_target_queue"] wird unten beim Submit
            # benutzt — wir patchen es lokal damit die Stage-3-Submit-Logik
            # die richtige Queue trifft.
            try:
                if isinstance(config, dict):
                    config = dict(config)  # shallow copy, mutate ist legitim
                    config["lpr_target_queue"] = chosen_queue_id
            except Exception:
                pass
            logger.info(
                "Desktop-Send [2/5] queue-specific — user='%s' → "
                "queue=%s target_id=%s job_id=%s",
                _user_descr(user), chosen_queue_id, target_id, internal_id,
            )
        else:
            _fail(f"unsupported target: {target_id}", code="target_unsupported")
            return

        logger.info(
            "Desktop-Send [2/5] resolved — user='%s' target=%s type=%s "
            "submit_to='%s' queue=%s job_id=%s",
            _user_descr(user), target_id, target_type,
            submit_user_email, config["lpr_target_queue"], internal_id,
        )

        # === Stage 3-5: Printix Secure Print Submit ========================
        try:
            import re as _re
            from printix_client import PrintixClient
            client = PrintixClient(
                tenant_id=tenant["printix_tenant_id"],
                print_client_id=tenant.get("print_client_id", ""),
                print_client_secret=tenant.get("print_client_secret", ""),
                shared_client_id=tenant.get("shared_client_id", ""),
                shared_client_secret=tenant.get("shared_client_secret", ""),
                um_client_id=tenant.get("um_client_id", ""),
                um_client_secret=tenant.get("um_client_secret", ""),
            )
            printers_data = client.list_printers(size=200)
            raw_list = printers_data.get("printers", []) if isinstance(printers_data, dict) else []
            if not raw_list and isinstance(printers_data, dict):
                raw_list = (printers_data.get("_embedded") or {}).get("printers", [])
            printer_id = ""
            target_queue = config["lpr_target_queue"]
            for p in raw_list:
                href = (p.get("_links") or {}).get("self", {}).get("href", "")
                m = _re.search(r"/printers/([^/]+)/queues/([^/?]+)", href)
                if m and m.group(2) == target_queue:
                    printer_id = m.group(1)
                    break
            if not printer_id:
                logger.error(
                    "Desktop-Send [3/5] printer-id-lookup FAIL — user='%s' "
                    "queue=%s scanned_printers=%d",
                    _user_descr(user), target_queue, len(raw_list),
                )
                _fail("target queue not found in Printix", code="queue_missing")
                return
            logger.info(
                "Desktop-Send [3/5] printer resolved — user='%s' printer_id=%s "
                "queue=%s job_id=%s",
                _user_descr(user), printer_id, target_queue, internal_id,
            )

            # Jetzt (nachdem Tenant bekannt ist) den Tracking-Eintrag
            # auf "forwarding" updaten bzw. anlegen. create_cloudprint_job
            # ist idempotent genug: wir haben den Eintrag in desktop_send
            # bereits angelegt und aktualisieren hier nur das Ziel.
            try:
                update_cloudprint_job_status(
                    internal_id, "forwarding",
                    target_queue=target_queue,
                    detected_identity=submit_user_email,
                    identity_source="desktop-send",
                )
            except Exception:
                pass

            # v0.7.18: Args 1:1 wie printix-mcp-linux/employee_routes.py:752.
            # v0.7.20: Wenn der Submit mit allen Body-Feldern 500 zurueckgibt,
            # retry mit MINIMALEM Body (kein color/duplex/copies). Manche
            # Printix-Tenant-Konfigs reagieren auf bestimmte Body-Felder
            # mit UNKNOWN_ERROR — z.B. wenn duplex=NONE serverseitig
            # disallowed ist. Mit reduziertem Body sehen wir ob ein Feld
            # der Schuldige war.
            from printix_client import PrintixAPIError as _PxErr
            def _do_submit(**extra):
                return client.submit_print_job(
                    printer_id=printer_id,
                    queue_id=target_queue,
                    title=display_filename,
                    user=submit_user_email,
                    pdl="PDF",
                    release_immediately=False,
                    **extra,
                )
            def _truthy(s: str) -> bool:
                return s.strip().lower() in ("1", "true", "yes", "on")
            _full_kwargs = dict(
                color=_truthy(color),
                duplex=("LONG_EDGE" if _truthy(duplex) else "NONE"),
                copies=max(1, min(99, int(copies or 1))),
            )
            try:
                result = _do_submit(**_full_kwargs)
                logger.info(
                    "Desktop-Send [4/5] submit OK — user='%s' (full body)",
                    submit_user_email,
                )
            except _PxErr as _px_e:
                if _px_e.status_code == 500:
                    logger.warning(
                        "Desktop-Send [4/5] submit 500 mit full body "
                        "(ErrorID=%s) — Retry mit minimal body…",
                        getattr(_px_e, 'error_id', ''),
                    )
                    try:
                        result = _do_submit()
                        logger.info(
                            "Desktop-Send [4/5] submit OK — user='%s' (minimal body)",
                            submit_user_email,
                        )
                    except _PxErr as _px_e2:
                        # Auch minimal failed -> Bug ist nicht im Body
                        logger.error(
                            "Desktop-Send [4/5] submit 500 auch mit minimal body "
                            "(ErrorID=%s)", getattr(_px_e2, 'error_id', ''),
                        )
                        raise
                else:
                    raise
            result_job = result.get("job", result) if isinstance(result, dict) else {}
            px_job_id = result_job.get("id", "") if isinstance(result_job, dict) else ""
            upload_url = ""
            upload_headers = {}
            if isinstance(result, dict):
                upload_url = result.get("uploadUrl", "") or ""
                links = result.get("uploadLinks") or []
                if not upload_url and links and isinstance(links[0], dict):
                    upload_url = links[0].get("url", "") or ""
                    upload_headers = links[0].get("headers") or {}

            if not px_job_id or not upload_url:
                logger.error(
                    "Desktop-Send [3/5] submit FAIL (no job-id/upload-url) — "
                    "user='%s' result_keys=%s",
                    _user_descr(user),
                    list(result.keys()) if isinstance(result, dict) else "?",
                )
                _fail("Printix accepted no job", code="printix_no_job")
                return
            logger.info(
                "Desktop-Send [3/5] submit OK — user='%s' printix_job=%s job_id=%s",
                _user_descr(user), px_job_id, internal_id,
            )

            t_upload = _t.monotonic()
            client.upload_file_to_url(upload_url, data, "application/pdf", upload_headers)
            dt_upload = _t.monotonic() - t_upload
            logger.info(
                "Desktop-Send [4a/5] blob-upload OK — user='%s' size=%d dt=%.2fs",
                _user_descr(user), len(data), dt_upload,
            )
            client.complete_upload(px_job_id)
            logger.info(
                "Desktop-Send [4b/5] completeUpload OK — user='%s' printix_job=%s",
                _user_descr(user), px_job_id,
            )

            if "@" in submit_user_email:
                try:
                    client.change_job_owner(px_job_id, submit_user_email)
                    logger.info(
                        "Desktop-Send [5/5] changeOwner OK — user='%s' "
                        "printix_job=%s owner='%s'",
                        _user_descr(user), px_job_id, submit_user_email,
                    )

                    # Auto-Register printix_user_id: wenn der angemeldete User
                    # (user_id im lokalen Portal) noch keine echte Printix-UUID
                    # gespeichert hat — oder eine ungueltige mgr:-Manager-ID —
                    # dann holen wir sie jetzt aus dem Job, den wir gerade
                    # gesubmittet haben. Nach changeOwner ist ownerId die
                    # echte UUID des Ziel-Users. Kostet 1 zusaetzlichen
                    # list_print_jobs-Call (size=10) — laeuft nur wenn noetig.
                    try:
                        current_pxid = (user.get("printix_user_id") or "").strip()
                        needs_update = (
                            not current_pxid
                            or current_pxid.startswith("mgr:")
                            or ":" in current_pxid  # jede andere Prefix-Form auch
                        )
                        # Nur fuer den submittenden User selbst — nicht fuer
                        # Delegates (submit_user_email waere dann die Delegate-
                        # Email, wir wollen aber die UUID des Owners, also des
                        # eingeloggten Desktop-User).
                        own_email = (user.get("email") or "").strip().lower()
                        if (needs_update and own_email
                                and own_email == submit_user_email.lower()):
                            from db import _conn as _dbc
                            jobs_resp = client.list_print_jobs(size=10)
                            jobs = []
                            if isinstance(jobs_resp, dict):
                                jobs = (jobs_resp.get("jobs")
                                        or jobs_resp.get("content") or [])
                            elif isinstance(jobs_resp, list):
                                jobs = jobs_resp
                            new_uuid = ""
                            for j in jobs:
                                if j.get("id") == px_job_id:
                                    candidate = (j.get("ownerId") or "").strip()
                                    # Nur echte UUIDs akzeptieren — die haben
                                    # 36 Zeichen mit 4 Bindestrichen. mgr:-Praefix
                                    # ausschliessen (die Card-API lehnt die ab).
                                    if (candidate
                                            and not candidate.startswith("mgr:")
                                            and ":" not in candidate
                                            and len(candidate) >= 30):
                                        new_uuid = candidate
                                    break
                            if new_uuid and new_uuid != current_pxid:
                                with _dbc() as _c:
                                    _c.execute(
                                        "UPDATE users SET printix_user_id=? WHERE id=?",
                                        (new_uuid, user["user_id"]),
                                    )
                                logger.info(
                                    "Desktop-Send: auto-registered printix_user_id=%s "
                                    "fuer user='%s' (old='%s')",
                                    new_uuid, _user_descr(user), current_pxid or "-",
                                )
                    except Exception as _ar:
                        logger.warning(
                            "Desktop-Send: auto-register printix_user_id "
                            "fehlgeschlagen fuer user='%s' err=%s",
                            _user_descr(user), _ar,
                        )
                except Exception as _co:
                    logger.warning(
                        "Desktop-Send [5/5] changeOwner FAIL — user='%s' "
                        "printix_job=%s owner='%s' err=%s",
                        _user_descr(user), px_job_id, submit_user_email, _co,
                    )
            else:
                logger.warning(
                    "Desktop-Send [5/5] changeOwner skip — submit_user_email "
                    "hat kein @: '%s'", submit_user_email,
                )

            update_cloudprint_job_status(
                internal_id, "forwarded",
                printix_job_id=px_job_id, target_queue=target_queue,
                detected_identity=submit_user_email,
                identity_source="desktop-send",
            )

            # v6.7.115: Audit-Trail für erfolgreich weitergeleitete iOS-Print-Jobs.
            try:
                import json as _json
                from db import audit as _audit
                _details = _json.dumps({
                    "target_id": target_id,
                    "target_type": target_type,
                    "queue_name": target_queue,
                    "job_filename": display_filename,
                    "job_size_bytes": len(data),
                    "printix_job_id": px_job_id,
                    "owner": submit_user_email,
                    "identity_source": "desktop-send",
                    "source": "ios_app",
                }, ensure_ascii=False)
                _audit(
                    user.get("user_id"),
                    "print_job_submitted",
                    details=_details,
                    object_type="print_job",
                    object_id=internal_id,
                )
            except Exception as _ae:
                logger.debug("audit(print_job_submitted) failed: %s", _ae)

            # v0.7.72: Push-Benachrichtigung — Job erfolgreich gesendet.
            try:
                from push_notify import notify_user as _push
                _push(
                    user_id=user.get("user_id", ""),
                    title="Druckauftrag gesendet",
                    body=f"„{display_filename}" wurde an {target_queue or target_id} übermittelt.",
                    extra={"job_id": internal_id, "printix_job_id": px_job_id},
                    collapse_id=f"job:{internal_id}",
                )
            except Exception as _pe:
                logger.debug("push(print_job_submitted) failed: %s", _pe)

            dt_total = _t.monotonic() - t_start
            logger.info(
                "Desktop-Send COMPLETE — user='%s' target=%s type=%s file='%s' "
                "size=%d printix_job=%s owner='%s' total_dt=%.2fs job_id=%s",
                user["username"], target_id, target_type, display_filename,
                len(data), px_job_id, submit_user_email, dt_total, internal_id,
            )
        except Exception as e:
            logger.exception(
                "Desktop-Send BG EXCEPTION — user='%s' target=%s file='%s' err=%s",
                _user_descr(user), target_id, filename, e,
            )
            _fail(str(e)[:300], code="send_failed")
    except Exception as outer:
        # v0.7.5: Outer Fallback — damit die Task nicht stumm stirbt UND der
        # cloudprint_jobs-Eintrag nicht ewig auf 'queued' haengt.
        logger.exception(
            "Desktop-Send BG OUTER EXCEPTION — user='%s' job_id=%s err=%s",
            _user_descr(user) if isinstance(user, dict) else "?",
            internal_id, outer,
        )
        try:
            from cloudprint.db_extensions import update_cloudprint_job_status
            update_cloudprint_job_status(
                internal_id, "error",
                error_message=f"bg_task_crashed: {str(outer)[:300]}",
            )
        except Exception as _outer_status:
            logger.error(
                "Desktop-Send BG OUTER STATUS UPDATE FAILED — job_id=%s err=%s",
                internal_id, _outer_status,
            )


def register_desktop_routes(app: FastAPI, get_app_version) -> None:
    """Registriert alle /desktop/*-Routen in der FastAPI-App.

    `get_app_version` ist eine Callable die die Addon-Version als String
    zurückgibt (aus `app_version.APP_VERSION`).
    """

    # ── Auth ──────────────────────────────────────────────────────────────
    @app.post("/desktop/auth/login")
    async def desktop_login(request: Request):
        """Login-Endpoint — akzeptiert sowohl JSON als auch Form-Body.

        Der Windows-Client (PrintixSend) schickt JSON via PostAsJsonAsync.
        Ältere Aufrufe (z.B. curl, Postman-Form) funktionieren weiterhin
        als multipart/x-www-form-urlencoded.
        """
        ct_header = request.headers.get("content-type", "").lower()
        username = ""
        password = ""
        device_name = ""
        if "application/json" in ct_header:
            try:
                body = await request.json()
            except Exception:
                body = {}
            username = (body.get("username") or "").strip()
            password = body.get("password") or ""
            device_name = (body.get("device_name") or "").strip()
        else:
            form = await request.form()
            username = (form.get("username") or "").strip()
            password = form.get("password") or ""
            device_name = (form.get("device_name") or "").strip()

        if not username or not password:
            return _json_error("username and password required",
                               code="auth_missing_fields", status=422)

        # v0.7.30: Rate-Limit VOR bcrypt-verify — sonst haemmert der
        # Angreifer den CPU-teuren Password-Hash.
        wait = _auth_rl_check(request, username)
        if wait is not None:
            logger.warning(
                "Desktop-Login RATE-LIMITED — user='%s' peer=%s retry_in=%.0fs",
                username, (request.client.host if request.client else "-"), wait,
            )
            resp = _json_error("too many login attempts",
                                 code="auth_rate_limited",
                                 status=429)
            resp.headers["Retry-After"] = str(int(wait) + 1)
            return resp

        ci = _log_req(request, "POST /auth/login",
                      f"username='{username}' device='{device_name or '-'}'")
        from db import authenticate_user
        user = authenticate_user(username.strip(), password)
        if not user:
            logger.warning(
                "Desktop-Login FAIL (invalid credentials) — user='%s' peer=%s",
                username, ci["peer"],
            )
            return _json_error("invalid credentials", code="auth_invalid", status=401)
        if user.get("status") and user.get("status") != "approved":
            logger.warning(
                "Desktop-Login FAIL (not approved) — user='%s' status=%s peer=%s",
                username, user.get("status"), ci["peer"],
            )
            return _json_error("account not approved", code="auth_pending", status=403)

        # v0.7.30: erfolgreicher Login raeumt das User-Bucket auf (User
        # koennte sich sofort mit anderem Passwort wieder anmelden ohne
        # Rate-Limit-Sperre).
        _auth_rl_reset(request, username)

        token = create_token(user["id"], device_name=device_name)
        logger.info(
            "Desktop-Login OK (local) — user='%s' uid=%s role=%s device='%s' "
            "token=%s peer=%s",
            user["username"], user["id"], user.get("role_type", "user"),
            device_name or "-", _mask_token(token), ci["peer"],
        )
        return JSONResponse({
            "token": token,
            "user": {
                "id": user["id"],
                "username": user["username"],
                "email": user.get("email", ""),
                "full_name": user.get("full_name", ""),
                "role_type": user.get("role_type", "user"),
            },
        })

    @app.post("/desktop/auth/logout")
    async def desktop_logout(request: Request,
                               authorization: str = Header(default="")):
        ci = _log_req(request, "POST /auth/logout")
        # Token aus Header extrahieren und widerrufen (auch bei ungültigem Token OK)
        token_value = ""
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token_value = parts[1].strip()
            revoked = revoke_token(token_value)
            logger.info("Desktop-Logout — token=%s revoked=%s peer=%s",
                        _mask_token(token_value), revoked, ci["peer"])
        else:
            logger.debug("Desktop-Logout — kein Token im Header (peer=%s)", ci["peer"])
        return JSONResponse({"ok": True})

    @app.get("/desktop/queues")
    async def desktop_queues(request: Request,
                                authorization: str = Header(default="")):
        """v0.6.0: Liste aller Printix-Queues des Tenants fuer iOS-Queue-
        Browser. iOS-User kann eine beliebige Queue zusaetzlich zur
        Default-Queue als Druck-Ziel anvisieren. Anywhere-Queues sind
        in der Antwort markiert + nach oben sortiert.
        """
        _log_req(request, "GET /desktop/queues")
        user = _require_token(authorization)
        if not user:
            return _json_error("token invalid", code="auth_required", status=401)
        try:
            import sys as _s, os as _o, re as _re
            src_dir = _o.path.dirname(_o.path.dirname(__file__))
            if src_dir not in _s.path:
                _s.path.insert(0, src_dir)
            from db import get_tenant_full_by_user_id, _resolve_tenant_owner_for
            from printix_client import PrintixClient
            parent_id = _resolve_tenant_owner_for(user["user_id"])
            tenant = get_tenant_full_by_user_id(parent_id) if parent_id else None
            if not tenant or not (tenant.get("print_client_id")
                                  or tenant.get("shared_client_id")):
                return JSONResponse({"queues": [], "available": False})
            client = PrintixClient(
                tenant_id=tenant["printix_tenant_id"],
                print_client_id=tenant.get("print_client_id", ""),
                print_client_secret=tenant.get("print_client_secret", ""),
                shared_client_id=tenant.get("shared_client_id", ""),
                shared_client_secret=tenant.get("shared_client_secret", ""),
            )
            data = client.list_printers(size=200)
            raw = data.get("printers", []) if isinstance(data, dict) else []
            if not raw:
                raw = (data.get("_embedded") or {}).get("printers", []) if isinstance(data, dict) else []
            def _is_any(it: dict, n: str) -> bool:
                # v0.6.0: Multi-Signal-Detection — vendor/manufacturer=Printix,
                # Type=anywhere/virtual, model enthaelt anywhere, oder
                # name-fallback. Vorher: nur name.contains("anywhere").
                if not isinstance(it, dict):
                    return bool(n and "anywhere" in n.lower())
                def _lc(v) -> str:
                    return str(v or "").strip().lower()
                for k in ("isAnywhere", "is_anywhere", "anywhere"):
                    v = it.get(k)
                    if isinstance(v, bool) and v:
                        return True
                if _lc(it.get("vendor")) == "printix": return True
                if _lc(it.get("manufacturer")) == "printix": return True
                if _lc(it.get("brand")) == "printix": return True
                for k in ("printerType", "type", "queueType"):
                    s = _lc(it.get(k))
                    if "anywhere" in s or "virtual" in s:
                        return True
                if "anywhere" in _lc(it.get("model")): return True
                if n and "anywhere" in n.lower(): return True
                return False

            queues = []
            for item in raw:
                href = (item.get("_links") or {}).get("self", {}).get("href", "")
                m = _re.search(r"/printers/([^/]+)/queues/([^/?]+)", href)
                if m:
                    name = item.get("name", "") or m.group(2)
                    is_any = _is_any(item, name)
                    queues.append({
                        "id":           f"print:queue:{m.group(2)}",
                        "queue_id":     m.group(2),
                        "queue_name":   name,
                        "printer_id":   m.group(1),
                        "printer_name": item.get("name", ""),
                        "vendor":       item.get("vendor", "") or item.get("manufacturer", ""),
                        "model":        item.get("model", ""),
                        "is_anywhere":  is_any,
                        "location":     item.get("location", ""),
                    })
            queues.sort(key=lambda q: (not q["is_anywhere"], q["queue_name"].lower()))
            return JSONResponse({"queues": queues, "available": True,
                                  "count": len(queues)})
        except Exception as e:
            logger.warning("desktop_queues: %s", e)
            return _json_error(str(e)[:200], code="queues_query_failed", status=502)

    @app.get("/desktop/me/jobs")
    async def desktop_me_jobs(request: Request,
                                 limit: int = 20,
                                 authorization: str = Header(default="")):
        """v0.5.8: Job-History fuer den eingeloggten User.
        Liefert die letzten N Print-Jobs aus cloudprint_jobs gefiltert
        nach username / email / printix_user_id (gleiche Logik wie /admin/audit
        Filter, aber per-User). Pro Job:
            { id, filename, status, queue, created_at, forwarded_at,
              error_message, source }
        Wird vom iOS-App-„Jobs"-Tab benutzt fuer Send-Feedback + History.
        """
        _log_req(request, "GET /me/jobs")
        user = _require_token(authorization)
        if not user:
            return _json_error("token invalid", code="auth_required", status=401)
        try:
            from db import _conn, _resolve_tenant_owner_for
            from cloudprint.db_extensions import get_tenant_for_user
            parent_id = _resolve_tenant_owner_for(user["user_id"]) or user["user_id"]
            tenant = get_tenant_for_user(parent_id)
            tid = (tenant or {}).get("id", "")
            limit_int = max(1, min(int(limit or 20), 200))
            uname = (_user_descr(user) or "").lower()
            uemail = (user.get("email") or "").lower()
            pxid = (user.get("printix_user_id") or "").lower()
            with _conn() as conn:
                # v0.7.3: tenant_id-Filter gelockert auf (tenant_id=?
                # OR tenant_id=''). Vorher waren ALLE iOS-Print-Jobs
                # unsichtbar weil sie historisch mit tenant_id="" angelegt
                # wurden (jetzt gefixt, aber alte Daten brauchen den OR).
                rows = conn.execute(
                    """SELECT job_id, job_name AS filename, status,
                              queue_name AS queue,
                              created_at, forwarded_at, error_message,
                              detected_identity AS source_identity
                       FROM cloudprint_jobs
                       WHERE (tenant_id = ? OR IFNULL(tenant_id,'') = '')
                         AND (
                           LOWER(IFNULL(username,'')) = ?
                           OR LOWER(IFNULL(username,'')) = ?
                           OR LOWER(IFNULL(detected_identity,'')) = ?
                           OR LOWER(IFNULL(detected_identity,'')) = ?
                           OR LOWER(IFNULL(detected_identity,'')) = ?
                         )
                       ORDER BY COALESCE(forwarded_at, created_at) DESC
                       LIMIT ?""",
                    (tid, uname, uemail, uname, uemail, pxid, limit_int),
                ).fetchall()
            items = [dict(r) for r in rows]
            return JSONResponse({"jobs": items, "count": len(items)})
        except Exception as e:
            logger.warning("desktop_me_jobs: %s", e)
            return _json_error(str(e)[:200], code="jobs_query_failed", status=500)

    @app.get("/desktop/me")
    async def desktop_me(request: Request,
                           authorization: str = Header(default="")):
        ci = _log_req(request, "GET /me")
        user = _require_token(authorization)
        if not user:
            logger.warning("Desktop-Me FAIL (token invalid) — peer=%s", ci["peer"])
            return _json_error("token invalid", code="auth_required", status=401)
        # v0.7.45: Admin-Flag `delegation_print_allowed` mit ausliefern.
        # iOS-App braucht das um zu wissen ob sie den Delegate-Toggle in
        # Settings ueberhaupt aktivieren duerfen soll — bisher wurde er
        # immer angezeigt, User war irritiert warum er ihn einschalten
        # konnte obwohl Server dann sowieso alle delegate-Prints ablehnt.
        try:
            from db import get_setting as _gs
            delegation_allowed = (
                (_gs("delegation_print_allowed", "0") or "0").strip()
                in ("1", "true", "yes", "on")
            )
        except Exception:
            delegation_allowed = False

        logger.info(
            "Desktop-Me OK — user='%s' uid=%s device='%s' peer=%s",
            _user_descr(user), user.get("user_id"),
            user.get("device_name", "-"), ci["peer"],
        )
        try:
            from db import get_setting as _gs2
            employees_can_manage_cards = (
                (_gs2("employees_can_manage_cards", "0") or "0").strip()
                in ("1", "true", "yes", "on")
            )
        except Exception:
            employees_can_manage_cards = False

        return JSONResponse({
            "user": {
                "id": user["user_id"],
                "username": user["username"],
                "email": user.get("email", ""),
                "full_name": user.get("full_name", ""),
                "role_type": user.get("role_type", "user"),
                "device_name": user.get("device_name", ""),
            },
            "delegation_allowed": delegation_allowed,
            "employees_can_manage_cards": employees_can_manage_cards,
        })

    # ── Targets ───────────────────────────────────────────────────────────
    @app.get("/desktop/targets")
    async def desktop_targets(request: Request,
                                authorization: str = Header(default="")):
        """Liefert eine Zielliste für den Desktop-Client.

        MVP-Zieltypen:
          - print_secure    → eigene Secure-Print-Queue
          - print_delegate  → Delegate-Print an eine konfigurierte Person
          - capture_profile → (Phase 4) Capture-Profile

        Aufbau pro Ziel: {id, type, label, icon, is_default, description}
        """
        ci = _log_req(request, "GET /targets")
        user = _require_token(authorization)
        if not user:
            logger.warning("Desktop-Targets FAIL (no token) — peer=%s", ci["peer"])
            return _json_error("token invalid", code="auth_required", status=401)

        from db import get_tenant_full_by_user_id, _resolve_tenant_owner_for
        from cloudprint.db_extensions import (
            get_delegations_for_owner, get_cloudprint_config,
        )

        parent_id = _resolve_tenant_owner_for(user["user_id"])
        tenant = get_tenant_full_by_user_id(parent_id) if parent_id else None

        # v0.5.0: 3-Tier Queue-Resolution.
        # User-Override → Group-Default → Global-Default → leer.
        # Ersetzt die alte fallback-Kette die nur den Admin-Tenant ueberprueft hat.
        try:
            from cloudprint.db_extensions import resolve_user_queue
            queue_id, queue_label, source = resolve_user_queue(user["user_id"])
        except Exception as _re:
            logger.debug("resolve_user_queue failed: %s", _re)
            queue_id, queue_label, source = ("", "", "error")

        # v0.6.7: Effektives "User darf andere Queue waehlen"-Flag fuer den
        # iOS-Client. Quelle ist der globale Admin-Toggle
        # (allow_user_queue_override). Damit kann die App entscheiden, ob
        # sie einen Queue-Picker (via /desktop/queues) anzeigt.
        try:
            from cloudprint.db_extensions import is_user_queue_override_allowed
            user_can_choose = bool(is_user_queue_override_allowed())
        except Exception as _ucc:
            logger.debug("is_user_queue_override_allowed failed: %s", _ucc)
            user_can_choose = False

        # v0.7.26: Admin-Flag „Delegation-Druck erlauben". Wenn aus,
        # liefert /desktop/targets keine Delegate/User-Targets aus,
        # auch wenn der User local enabled hat. iOS rendert dann den
        # Delegate-Toggle ausgegraut mit Hinweis "Admin hat deaktiviert".
        try:
            from db import get_setting as _gs
            delegation_allowed = (
                (_gs("delegation_print_allowed", "0") or "0").strip()
                in ("1", "true", "yes", "on")
            )
        except Exception:
            delegation_allowed = False

        targets: list[dict] = []
        breakdown = {"self": 0, "delegates": 0, "capture": 0}

        # 1) Eigene Secure-Print-Queue (Quelle entsprechend dem Resolver)
        if queue_id:
            description = {
                "user_override": "Eigene Queue-Auswahl",
                "global":        "Vom Admin festgelegte Standard-Queue",
            }.get(source, source if source.startswith("group:") else "Default-Queue")
            if source.startswith("group:"):
                description = f"Über Sync-Gruppe „{source[6:]}“"
            targets.append({
                "id": "print:self",
                "type": "print_secure",
                "label": queue_label or "Mein Secure Print",
                "description": description,
                "icon": "printer",
                "is_default": True,
                "_source": source,
                "_queue_id": queue_id,
            })
            breakdown["self"] = 1
        else:
            logger.debug(
                "Desktop-Targets: kein Secure-Print — tenant=%s user='%s' source=%s",
                bool(tenant), _user_descr(user), source,
            )

        # 2) Delegates (jede aktive Delegation = 1 Ziel)
        # v0.7.26: nur wenn Admin Delegation-Druck global erlaubt hat.
        try:
            delegations = (get_delegations_for_owner(user["user_id"])
                           if delegation_allowed else [])
            for d in delegations:
                if d.get("status") != "active":
                    continue
                email = d.get("delegate_email", "")
                name  = d.get("delegate_full_name") or d.get("delegate_username") or email
                if not email:
                    continue
                targets.append({
                    "id": f"print:delegate:{d['id']}",
                    "type": "print_delegate",
                    "label": f"Delegate: {name}",
                    "description": email,
                    "icon": "user",
                    "is_default": False,
                    "delegate_email": email,
                })
                breakdown["delegates"] += 1
        except Exception as _e:
            logger.warning(
                "Desktop-Targets: Delegate-Lookup failed — user='%s' err=%s",
                _user_descr(user), _e,
            )

        # 3) Capture-Profile — alle aktiven Profile des Tenants als Send-To-Ziel.
        #    Client zeigt sie automatisch als eigenen "Senden an"-Eintrag
        #    (Send2Printix — Capture: <Name>).
        #
        #    Routing in /desktop/send ist noch Stub (liefert einen klaren
        #    Hinweis) — das eigentliche Capture-Dispatching folgt in einer
        #    späteren Version. Die Einträge erscheinen aber bereits im
        #    Explorer-"Senden an"-Menü.
        try:
            if tenant and tenant.get("id"):
                from db import get_capture_profiles_by_tenant
                profiles = get_capture_profiles_by_tenant(tenant["id"])
                for p in profiles:
                    if not p.get("is_active"):
                        continue
                    name = (p.get("name") or "").strip() or "Capture"
                    plugin_type = (p.get("plugin_type") or "").strip()
                    targets.append({
                        "id": f"capture:{p['id']}",
                        "type": "capture_profile",
                        "label": f"Capture: {name}",
                        "description": plugin_type or "Capture-Ziel",
                        "icon": "archive",
                        "is_default": False,
                    })
                    breakdown["capture"] += 1
        except Exception as _e:
            logger.warning(
                "Desktop-Targets: Capture-Lookup failed — user='%s' err=%s",
                _user_descr(user), _e,
            )

        logger.info(
            "Desktop-Targets OK — user='%s' targets=%d (self=%d delegates=%d "
            "capture=%d) peer=%s",
            _user_descr(user), len(targets),
            breakdown["self"], breakdown["delegates"], breakdown["capture"],
            ci["peer"],
        )
        return JSONResponse({
            "targets": targets,
            "user_can_choose": user_can_choose,
            "delegation_allowed": delegation_allowed,
        })

    # ── Send (Datei-Upload + Dispatch) ────────────────────────────────────
    @app.post("/desktop/send")
    async def desktop_send(
        request: Request,
        authorization: str = Header(default=""),
        target_id: str = Form(...),
        file: UploadFile = File(...),
        copies: int = Form(1),
        color: str = Form(""),
        duplex: str = Form(""),
    ):
        import time as _t
        t_start = _t.monotonic()
        # v0.7.17: Wir loggen IMMER (ungated) wenn der Request den Handler
        # erreicht. So sehen wir bei „Upload haengt seit Minuten" ob das
        # Problem netzwerk-seitig (Body kommt noch nicht an) oder
        # server-seitig (Body angekommen, processing langsam) ist.
        logger.info("Desktop-Send INGRESS — target=%s peer=%s",
                    target_id,
                    (request.client.host if request.client else '?'))
        ci = _log_req(request, "POST /send",
                      f"target_id='{target_id}' filename='{file.filename if file else '-'}'")
        user = _require_token(authorization)
        if not user:
            logger.warning("Desktop-Send FAIL (no token) — peer=%s target=%s",
                           ci["peer"], target_id)
            return _json_error("token invalid", code="auth_required", status=401)

        if not file or not file.filename:
            logger.warning("Desktop-Send FAIL (no file) — user='%s' peer=%s",
                           _user_descr(user), ci["peer"])
            return _json_error("no file", code="no_file", status=400)

        MAX = 50 * 1024 * 1024
        _t_before_read = _t.monotonic()
        data = await file.read()
        _dt_read_ms = (_t.monotonic() - _t_before_read) * 1000.0
        logger.info(
            "Desktop-Send BODY-RECEIVED — target=%s size=%d dt_read=%.0fms",
            target_id, len(data) if data else 0, _dt_read_ms,
        )
        if not data:
            logger.warning("Desktop-Send FAIL (empty file) — user='%s' peer=%s",
                           _user_descr(user), ci["peer"])
            return _json_error("empty file", code="empty_file", status=400)
        if len(data) > MAX:
            logger.warning(
                "Desktop-Send FAIL (too large) — user='%s' size=%d peer=%s",
                _user_descr(user), len(data), ci["peer"],
            )
            return _json_error("file too large (max 50 MB)",
                               code="too_large", status=413)
        logger.info(
            "Desktop-Send START — user='%s' device='%s' target=%s filename='%s' "
            "size=%d copies=%s color=%s duplex=%s peer=%s",
            _user_descr(user), user.get("device_name", "-"), target_id,
            file.filename, len(data), copies,
            bool(color), bool(duplex), ci["peer"],
        )

        # v6.7.43: Fire-and-forget — Cloudflare kappt jede HTTP-Verbindung
        # nach 100 s (HTTP 524), aber unsere Pipeline (LibreOffice-Konvertierung
        # + 5-Stage-Printix-Submit) braucht regelmäßig 90–180 s. Daher:
        # jetzt nur noch validieren, Job-Tracking-Eintrag anlegen, 202 Accepted
        # zurück und die eigentliche Verarbeitung in asyncio.create_task().
        # Der Windows-Client sieht damit innerhalb weniger Sekunden "queued"
        # und der Server arbeitet in Ruhe weiter. Fehler landen im
        # cloudprint_jobs-Eintrag (Status=error) und sind in der Web-UI
        # unter „Meine Druckjobs" einsehbar.
        import asyncio
        import uuid as _uuid
        internal_id = _uuid.uuid4().hex[:10]

        # v0.7.14: Tracking-Insert in die BG-Task verschoben. Auf Azure-Files
        # (SMB) braucht jeder INSERT auf cloudprint_jobs 200-600 ms — bisher
        # haben iOS-Uploads das VOR dem 202 gemacht, plus tenant-Lookup
        # (weitere ~200 ms). Bei einem 300-KB-JPG ergab das 2-3 s Spinner
        # vor dem ersten Byte Response. Jetzt: 202 sofort, BG-Task schreibt
        # den ersten Tracking-Eintrag. update_cloudprint_job_status() ist
        # robust gegen "Row gibt's noch nicht" (UPDATE ohne Treffer ist No-op),
        # daher ist die Reihenfolge BG-INSERT vor BG-Pipeline-Stages OK.
        import time as _ptime
        _t_after_read = _ptime.monotonic()
        try:
            from db import perf_logs_enabled as _perf_on
            _perf_send = _perf_on()
        except Exception:
            _perf_send = False

        async def _bg_create_tracking():
            def _do():
                try:
                    import sys as _sys, os as _os
                    src_dir = _os.path.dirname(_os.path.dirname(__file__))
                    if src_dir not in _sys.path:
                        _sys.path.insert(0, src_dir)
                    from cloudprint.db_extensions import (
                        create_cloudprint_job,
                    )
                    _tid = ""
                    try:
                        from db import get_tenant_full_by_user_id, _resolve_tenant_owner_for
                        _pid = _resolve_tenant_owner_for(user["user_id"])
                        _tnt = get_tenant_full_by_user_id(_pid) if _pid else None
                        if _tnt:
                            _tid = _tnt.get("id", "") or ""
                    except Exception:
                        pass
                    create_cloudprint_job(
                        job_id=internal_id,
                        tenant_id=_tid,
                        queue_name="",
                        username=(user.get("email") or _user_descr(user) or "")[:120],
                        hostname=f"desktop:{user.get('device_name', '')}"[:80],
                        job_name=file.filename,
                        data_size=len(data),
                        data_format="application/octet-stream",
                        detected_identity=(user.get("email") or ""),
                        identity_source="desktop-send",
                        status="queued",
                    )
                except Exception as _cj:
                    logger.debug("initial cloudprint_job insert failed: %s", _cj)
            try:
                await asyncio.to_thread(_do)
            except Exception:
                pass
        asyncio.create_task(_bg_create_tracking())

        # v0.7.5: Wrapper mit 5-Min-Watchdog. Wenn die BG-Task laenger als
        # 300s laeuft (z.B. Printix-Submit haengt im Network-Wait), wird
        # die Task gecancelled und der Job auf 'error' gesetzt — so haengt
        # nichts ewig auf 'queued'.
        async def _watched_bg():
            import asyncio as _aio
            try:
                await _aio.wait_for(
                    _process_desktop_send_bg(
                        user=user, target_id=target_id, data=data,
                        filename=file.filename, copies=copies,
                        color=color, duplex=duplex,
                        internal_id=internal_id, t_start=t_start,
                    ),
                    timeout=300,
                )
            except _aio.TimeoutError:
                logger.error(
                    "Desktop-Send BG WATCHDOG TIMEOUT — user='%s' job_id=%s "
                    "(>300s — Status auf 'error' gesetzt)",
                    _user_descr(user), internal_id,
                )
                try:
                    from cloudprint.db_extensions import update_cloudprint_job_status
                    update_cloudprint_job_status(
                        internal_id, "error",
                        error_message="bg_task_timeout: keine Antwort nach 300s",
                    )
                except Exception:
                    pass
        asyncio.create_task(_watched_bg())

        logger.info(
            "Desktop-Send QUEUED — user='%s' target=%s job_id=%s size=%d "
            "— 202 Accepted, Verarbeitung läuft asynchron",
            _user_descr(user), target_id, internal_id, len(data),
        )
        if _perf_send:
            _t_resp = _ptime.monotonic()
            logger.info(
                "perf desktop_send dt_total=%.0fms dt_read=%.0fms dt_post_read=%.0fms "
                "size=%d job=%s",
                (_t_resp - t_start) * 1000.0,
                (_t_after_read - t_start) * 1000.0,
                (_t_resp - _t_after_read) * 1000.0,
                len(data), internal_id,
            )
        return JSONResponse({
            "ok": True,
            "status": "queued",
            "job_id": internal_id,
            "target": target_id,
            "filename": file.filename,
            "size": len(data),
            "message": "Job angenommen — Verarbeitung läuft im Hintergrund.",
        }, status_code=202)

    # ── Entra SSO via Device Code Flow (v6.7.32) ──────────────────────────
    # Der Desktop-Client startet den Flow, zeigt dem User einen Code und die
    # Microsoft-URL an; User öffnet die URL im Browser, gibt den Code ein,
    # meldet sich mit Entra an. Der Client pollt derweil unseren poll-Endpoint
    # bis Microsoft den Access-Token zurückgibt — dann mappen wir den Entra-
    # User auf unseren MCP-User und geben einen Desktop-Token zurück.
    #
    # Im Gegensatz zum Web-Flow gibt's hier keine Session — der Device-Code
    # wird in einer Pending-Tabelle zwischengespeichert und nach Abschluss
    # gelöscht.

    @app.post("/desktop/auth/entra/start")
    async def desktop_entra_start(request: Request,
                                    device_name: str = Form("")):
        ci = _log_req(request, "POST /auth/entra/start",
                      f"device='{device_name or '-'}'")
        from db import get_setting
        if (get_setting("entra_enabled", "0") or "0") != "1":
            logger.warning("Desktop-Entra-Start FAIL (entra disabled) — peer=%s",
                           ci["peer"])
            return _json_error("Entra SSO not enabled on this server",
                               code="entra_disabled", status=400)
        try:
            from entra import start_device_code_flow
        except ImportError:
            logger.error("Desktop-Entra-Start EXC (entra module missing)")
            return _json_error("Entra module not available",
                               code="entra_unavailable", status=500)

        # v6.7.33-fix: Für Desktop-Login brauchen wir User.Read um nach dem
        # Auth-Flow via /me das Profil (oid + email) abzurufen — NICHT die
        # Application-Scopes aus dem Admin-Setup-Flow (der war für
        # App-Registration gedacht).
        _user_read_scope = (
            "https://graph.microsoft.com/User.Read "
            "offline_access openid email profile"
        )
        result = start_device_code_flow(scopes=_user_read_scope)
        if not result or not result.get("device_code"):
            logger.error(
                "Desktop-Entra-Start FAIL (Microsoft refused) — peer=%s result=%s",
                ci["peer"], result,
            )
            return _json_error("Microsoft refused device-code start",
                               code="entra_start_failed", status=502)

        # Device-Code in Pending-Tabelle cachen, keyed by session_id
        import secrets, json as _json
        from datetime import datetime, timezone
        from db import _conn
        session_id = secrets.token_urlsafe(24)
        now = datetime.now(timezone.utc).isoformat()
        with _conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS desktop_entra_pending (
                    session_id   TEXT PRIMARY KEY,
                    device_code  TEXT NOT NULL,
                    device_name  TEXT NOT NULL DEFAULT '',
                    created_at   TEXT NOT NULL,
                    expires_at   TEXT NOT NULL
                );
            """)
            from datetime import timedelta
            expires = (datetime.now(timezone.utc) +
                       timedelta(seconds=int(result.get("expires_in", 900)))).isoformat()
            conn.execute(
                "INSERT INTO desktop_entra_pending "
                "(session_id, device_code, device_name, created_at, expires_at) "
                "VALUES (?,?,?,?,?)",
                (session_id, result["device_code"],
                 (device_name or "").strip(), now, expires),
            )
        logger.info(
            "Desktop-Entra-Start OK — session=%s… user_code=%s expires_in=%ss "
            "interval=%ss device='%s' peer=%s",
            _hsid(session_id), result.get("user_code", ""),
            result.get("expires_in", 900), result.get("interval", 5),
            device_name or "-", ci["peer"],
        )

        return JSONResponse({
            "session_id":        session_id,
            "user_code":         result.get("user_code", ""),
            "verification_uri":  result.get("verification_uri", "https://microsoft.com/devicelogin"),
            "expires_in":        result.get("expires_in", 900),
            "interval":          result.get("interval", 5),
            "message":           result.get("message", ""),
        })

    @app.post("/desktop/auth/entra/poll")
    async def desktop_entra_poll(request: Request,
                                   session_id: str = Form(...)):
        """Vom Desktop-Client im Interval aufgerufen. Status:
           - pending:      User hat noch nicht im Browser abgeschlossen
           - ok:           Anmeldung erfolgreich — Token zurück
           - expired:      Device-Code abgelaufen
           - error:        technischer Fehler
           - no_match:     Entra-User konnte keinem MCP-User zugeordnet werden
        """
        ci = _log_req(request, "POST /auth/entra/poll",
                      f"session={_hsid(session_id)}…")
        from db import _conn
        with _conn() as conn:
            row = conn.execute(
                "SELECT device_code, device_name FROM desktop_entra_pending "
                "WHERE session_id = ?", (session_id,),
            ).fetchone()
        if not row:
            logger.warning(
                "Desktop-Entra-Poll FAIL (session unknown) — session=%s… peer=%s",
                _hsid(session_id), ci["peer"],
            )
            return _json_error("unknown session", code="session_unknown", status=404)

        device_code = row["device_code"]
        device_name = row["device_name"]

        try:
            from entra import poll_device_code_token
            result = poll_device_code_token(device_code)
        except ImportError:
            logger.error("Desktop-Entra-Poll EXC — entra module missing")
            return _json_error("Entra module not available",
                               code="entra_unavailable", status=500)

        status = result.get("status", "pending")
        logger.debug(
            "Desktop-Entra-Poll — session=%s… status=%s device='%s'",
            _hsid(session_id), status, device_name or "-",
        )
        if status == "pending":
            return JSONResponse({"status": "pending"})
        if status == "expired":
            with _conn() as conn:
                conn.execute("DELETE FROM desktop_entra_pending WHERE session_id = ?",
                             (session_id,))
            logger.info(
                "Desktop-Entra-Poll EXPIRED — session=%s… (cleaned up)",
                _hsid(session_id),
            )
            return JSONResponse({"status": "expired"})
        if status == "error":
            logger.warning(
                "Desktop-Entra-Poll ERROR — session=%s… err=%s",
                _hsid(session_id), result.get("error", ""),
            )
            return JSONResponse({"status": "error",
                                 "error": result.get("error", "")})

        # status == "success" — Access-Token holen, Userprofil abrufen, mappen
        if status != "success" or not result.get("access_token"):
            logger.warning(
                "Desktop-Entra-Poll unexpected state — session=%s… status=%s",
                _hsid(session_id), status,
            )
            return JSONResponse({"status": "error", "error": "unexpected_state"})

        # Profil von Microsoft Graph holen (/me Endpoint).
        # Alternativ kann man `id_token` aus dem Response decoden — wir
        # holen aber direkt über das access_token um sicher zu sein die
        # richtige "oid" zu kriegen.
        import requests as _requests
        try:
            me = _requests.get(
                "https://graph.microsoft.com/v1.0/me",
                headers={"Authorization": f"Bearer {result['access_token']}"},
                timeout=15,
            )
            me.raise_for_status()
            me_data = me.json()
            profile = {
                "oid":   me_data.get("id", ""),
                "email": (me_data.get("mail") or
                          me_data.get("userPrincipalName") or ""),
                "name":  (me_data.get("displayName") or
                          me_data.get("givenName") or ""),
            }
            from db import get_or_create_entra_user
        except Exception as e:
            logger.error("Desktop-Entra: Profil-Abruf fehlgeschlagen: %s", e)
            return JSONResponse({"status": "error", "error": str(e)[:200]})

        if not profile or not profile.get("oid"):
            logger.warning(
                "Desktop-Entra-Poll NO_MATCH (no profile) — session=%s…",
                _hsid(session_id),
            )
            return JSONResponse({"status": "no_match",
                                 "error": "no user profile from Microsoft"})
        logger.info(
            "Desktop-Entra-Poll: Profil abgerufen — oid=%s… email='%s' name='%s'",
            profile["oid"][:10], profile.get("email", ""), profile.get("name", ""),
        )

        try:
            user = get_or_create_entra_user(
                entra_oid=profile["oid"],
                email=profile.get("email", ""),
                display_name=profile.get("name", ""),
            )
        except Exception as e:
            logger.error(
                "Desktop-Entra-Poll: get_or_create_entra_user FAIL — "
                "oid=%s… email='%s' err=%s",
                profile["oid"][:10], profile.get("email", ""), e,
            )
            return JSONResponse({"status": "error", "error": str(e)[:200]})

        if not user or user.get("status") in ("disabled", "suspended"):
            logger.warning(
                "Desktop-Entra-Poll NO_MATCH — user-lookup returned %s "
                "(status=%s) for email='%s'",
                "None" if not user else "user",
                (user or {}).get("status"),
                profile.get("email", ""),
            )
            return JSONResponse({"status": "no_match",
                                 "error": "user not approved"})

        # Desktop-Token anlegen + Pending-Eintrag löschen
        token = create_token(user["id"], device_name=device_name or "Entra-Desktop")
        with _conn() as conn:
            conn.execute("DELETE FROM desktop_entra_pending WHERE session_id = ?",
                         (session_id,))
        logger.info(
            "Desktop-Entra-Login OK — user='%s' uid=%s email='%s' oid=%s… "
            "token=%s device='%s'",
            _user_descr(user), user.get("id"), user.get("email", ""),
            profile.get("oid", "")[:10], _mask_token(token),
            device_name or "Entra-Desktop",
        )
        return JSONResponse({
            "status": "ok",
            "token": token,
            "user": {
                "id": user["id"],
                "username": user.get("username", ""),
                "email": user.get("email", ""),
                "full_name": user.get("full_name", ""),
                "role_type": user.get("role_type", "user"),
            },
        })

    # ── Authorization Code Flow + PKCE (für iOS-App, v7.1.4+) ─────────────
    #
    # Im Gegensatz zum Device-Code-Flow oeffnet die iOS-App eine
    # ASWebAuthenticationSession (in-app Safari-Sheet), MS redirected per
    # Custom-URL-Scheme zurueck, App schickt code+state hier her, wir
    # tauschen code+verifier gegen einen Token. Der `code_verifier` bleibt
    # die ganze Zeit auf dem Server (Pending-Tabelle), der Client sieht
    # ihn nie.
    #
    # Voraussetzung in der Entra App-Registration:
    #   Authentication → Mobile and desktop applications → Add URI
    #   z.B. mysecureprint://oauth/callback

    @app.post("/desktop/auth/entra/authcode/start")
    async def desktop_entra_authcode_start(request: Request,
                                            device_name: str = Form(""),
                                            redirect_uri: str = Form(...)):
        ci = _log_req(request, "POST /auth/entra/authcode/start",
                      f"device='{device_name or '-'}' redirect='{redirect_uri}'")
        from db import get_setting
        if (get_setting("entra_enabled", "0") or "0") != "1":
            logger.warning("Desktop-Entra-AuthCode-Start FAIL (entra disabled) — peer=%s",
                           ci["peer"])
            return _json_error("Entra SSO not enabled on this server",
                               code="entra_disabled", status=400)
        try:
            from entra import generate_pkce_pair, build_authorize_url_pkce, generate_state
        except ImportError:
            logger.error("Desktop-Entra-AuthCode-Start EXC (entra module missing)")
            return _json_error("Entra module not available",
                               code="entra_unavailable", status=500)

        # PKCE-Paar + State erzeugen, alles serverseitig persistieren
        verifier, challenge = generate_pkce_pair()
        state = generate_state()

        import secrets
        from datetime import datetime, timezone, timedelta
        from db import _conn
        session_id = secrets.token_urlsafe(24)
        now = datetime.now(timezone.utc).isoformat()
        # 10 Minuten Default — typischer Login dauert <2 Min
        expires_in_s = 600
        expires = (datetime.now(timezone.utc) +
                   timedelta(seconds=expires_in_s)).isoformat()
        with _conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS desktop_entra_authcode_pending (
                    session_id    TEXT PRIMARY KEY,
                    code_verifier TEXT NOT NULL,
                    state         TEXT NOT NULL,
                    redirect_uri  TEXT NOT NULL,
                    device_name   TEXT NOT NULL DEFAULT '',
                    created_at    TEXT NOT NULL,
                    expires_at    TEXT NOT NULL
                );
            """)
            conn.execute(
                "INSERT INTO desktop_entra_authcode_pending "
                "(session_id, code_verifier, state, redirect_uri, "
                " device_name, created_at, expires_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (session_id, verifier, state, redirect_uri,
                 (device_name or "").strip(), now, expires),
            )

        auth_url = build_authorize_url_pkce(redirect_uri, state, challenge)
        logger.info(
            "Desktop-Entra-AuthCode-Start OK — session=%s… state=%s… "
            "redirect=%s device='%s' peer=%s",
            _hsid(session_id), state[:8], redirect_uri,
            device_name or "-", ci["peer"],
        )

        return JSONResponse({
            "session_id": session_id,
            "auth_url":   auth_url,
            "state":      state,
            "expires_in": expires_in_s,
        })

    @app.post("/desktop/auth/entra/authcode/exchange")
    async def desktop_entra_authcode_exchange(request: Request,
                                                session_id: str = Form(...),
                                                code: str = Form(...),
                                                state: str = Form(...)):
        ci = _log_req(request, "POST /auth/entra/authcode/exchange",
                      f"session={_hsid(session_id)}…")
        from db import _conn
        # v0.1.2 (CRITICAL #3 aus ENTRA_REVIEW.md):
        # Pending-Row SOFORT loeschen, sobald wir sie gefunden haben —
        # noch BEVOR wir Microsoft kontaktieren. Sonst bleibt der State
        # bei einem fehlgeschlagenen Token-Exchange (Netzwerk-Fehler,
        # MS-502, etc.) bis zu 10 Minuten replay-faehig. Mit dem
        # Sofort-Delete ist jeder session_id strikt one-shot.
        with _conn() as conn:
            row = conn.execute(
                "SELECT code_verifier, state, redirect_uri, device_name "
                "FROM desktop_entra_authcode_pending WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row:
                conn.execute(
                    "DELETE FROM desktop_entra_authcode_pending "
                    "WHERE session_id = ?",
                    (session_id,),
                )
            # Bonus: opportunistisches Aufraeumen abgelaufener Eintraege
            try:
                from datetime import datetime, timezone
                _now_iso = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "DELETE FROM desktop_entra_authcode_pending "
                    "WHERE expires_at < ?",
                    (_now_iso,),
                )
            except Exception:
                pass
        if not row:
            logger.warning(
                "Desktop-Entra-AuthCode-Exchange FAIL (session unknown) — "
                "session=%s… peer=%s",
                _hsid(session_id) if session_id else "-", ci["peer"],
            )
            return _json_error("unknown session", code="session_unknown",
                               status=404)

        # CSRF-Schutz: state aus dem Callback muss zu unserem gespeicherten
        # state passen. Wenn nicht → Angriffsversuch oder kaputter Client.
        # (constant-time compare als defence-in-depth)
        import secrets as _secrets
        if not _secrets.compare_digest(state or "", row["state"] or ""):
            logger.warning(
                "Desktop-Entra-AuthCode-Exchange FAIL (state mismatch) — "
                "session=%s… got=%s… expected=%s… peer=%s",
                _hsid(session_id), (state or "")[:8], (row["state"] or "")[:8],
                ci["peer"],
            )
            return _json_error("state mismatch", code="state_mismatch",
                               status=400)

        verifier     = row["code_verifier"]
        redirect_uri = row["redirect_uri"]
        device_name  = row["device_name"]

        try:
            from entra import exchange_code_pkce
        except ImportError:
            logger.error("Desktop-Entra-AuthCode-Exchange EXC (entra missing)")
            return _json_error("Entra module not available",
                               code="entra_unavailable", status=500)

        profile = exchange_code_pkce(code, redirect_uri, verifier)
        if not profile or not profile.get("oid"):
            logger.warning(
                "Desktop-Entra-AuthCode-Exchange FAIL (token exchange failed) — "
                "session=%s…",
                _hsid(session_id),
            )
            return _json_error("token exchange failed",
                               code="exchange_failed", status=502)

        try:
            from db import get_or_create_entra_user
            user = get_or_create_entra_user(
                entra_oid=profile["oid"],
                email=profile.get("email", ""),
                display_name=profile.get("name", ""),
            )
        except Exception as e:
            logger.error(
                "Desktop-Entra-AuthCode-Exchange: get_or_create_entra_user "
                "FAIL — oid=%s… email='%s' err=%s",
                profile["oid"][:10], profile.get("email", ""), e,
            )
            return _json_error(str(e)[:200], code="user_lookup_failed",
                               status=500)

        if not user or user.get("status") in ("disabled", "suspended"):
            logger.warning(
                "Desktop-Entra-AuthCode-Exchange NO_MATCH — user-lookup "
                "returned %s (status=%s) for email='%s'",
                "None" if not user else "user",
                (user or {}).get("status"),
                profile.get("email", ""),
            )
            return JSONResponse({"status": "no_match",
                                 "error": "user not approved"})

        # v0.1.3: refresh_token Fernet-verschluesselt speichern, wenn
        # Continuous Evaluation aktiviert ist. Andernfalls verwerfen.
        try:
            from db import get_setting, _enc, _conn as _db_conn
            ce_enabled = (get_setting("entra_continuous_eval_enabled", "0")
                          == "1")
            rt_plain = (profile or {}).get("refresh_token", "") or ""
            if ce_enabled and rt_plain:
                with _db_conn() as conn:
                    conn.execute(
                        "UPDATE users SET entra_refresh_token = ? "
                        "WHERE id = ?",
                        (_enc(rt_plain), user["id"]),
                    )
        except Exception as _ce_err:
            logger.debug("continuous-eval refresh_token store failed: %s",
                         _ce_err)

        # Desktop-Token anlegen. Pending-Row wurde bereits ganz am
        # Anfang dieser Funktion geloescht (v0.1.2 single-use state).
        token = create_token(user["id"],
                             device_name=device_name or "Entra-Mobile")
        logger.info(
            "Desktop-Entra-AuthCode-Exchange OK — user='%s' uid=%s email='%s' "
            "oid=%s… token=%s device='%s'",
            _user_descr(user), user.get("id"), user.get("email", ""),
            profile.get("oid", "")[:10], _mask_token(token),
            device_name or "Entra-Mobile",
        )
        return JSONResponse({
            "status": "ok",
            "token":  token,
            "user": {
                "id":        user["id"],
                "username":  user.get("username", ""),
                "email":     user.get("email", ""),
                "full_name": user.get("full_name", ""),
                "role_type": user.get("role_type", "user"),
            },
        })

    # ── Update-Check ──────────────────────────────────────────────────────
    @app.get("/desktop/client/latest-version")
    async def desktop_client_version(request: Request):
        """Self-describing Version-Endpoint. Der Client pingt das beim Start
        und zeigt ggf. einen Update-Hinweis an.

        Aktuell: die Addon-Version ist zugleich die minimale Server-Version.
        Der Client hat seine eigene Version — `required_client_version` kann
        der Admin später als Setting pflegen (global_min_client_version).
        """
        from db import get_setting
        required = (get_setting("min_client_version", "") or "").strip()
        download_url = (get_setting("client_download_url", "") or "").strip()
        return JSONResponse({
            "server_version": get_app_version(),
            "min_client_version": required or None,
            "download_url": download_url or None,
            # Endpoint-Versionen damit Client bei Breaking-Changes migrieren kann:
            "api_version": "1.0",
        })

    # ── Push-Notification-Tokens (v0.7.72) ────────────────────────────────────

    @app.post("/desktop/push/register")
    async def desktop_push_register(
        request: Request,
        authorization: Optional[str] = Header(None),
    ):
        """Registriert einen APNs Device-Token für Push-Benachrichtigungen.

        Request JSON:
          { "device_token": "<64-Hex>", "environment": "production"|"sandbox" }
        """
        user = _require_token(authorization)
        if not user:
            return _json_error("unauthorized", code="auth_required", status=401)
        try:
            body = await request.json()
        except Exception:
            body = {}
        device_token = (body.get("device_token") or "").strip()
        environment  = (body.get("environment") or "production").strip()
        if not device_token:
            return _json_error("device_token required", code="missing_field", status=422)
        # Sanitize: APNs tokens sind 64 Hex-Chars (32 Bytes)
        device_token = device_token.replace(" ", "").replace("<", "").replace(">", "")
        if len(device_token) < 32 or len(device_token) > 200:
            return _json_error("invalid device_token", code="invalid_token", status=422)
        if environment not in ("production", "sandbox"):
            environment = "production"
        try:
            from push_tokens import register_push_token
            register_push_token(
                user_id=user["user_id"],
                device_token=device_token,
                desktop_token=user.get("token", ""),
                environment=environment,
            )
        except Exception as e:
            logger.error("push/register failed: %s", e)
            return _json_error("registration failed", code="server_error", status=500)
        return JSONResponse({"status": "ok"})

    @app.delete("/desktop/push/unregister")
    async def desktop_push_unregister(
        request: Request,
        authorization: Optional[str] = Header(None),
    ):
        """Entfernt einen APNs Device-Token (z.B. bei Logout).

        Request JSON: { "device_token": "<64-Hex>" }
        """
        user = _require_token(authorization)
        if not user:
            return _json_error("unauthorized", code="auth_required", status=401)
        try:
            body = await request.json()
        except Exception:
            body = {}
        device_token = (body.get("device_token") or "").strip()
        if not device_token:
            return _json_error("device_token required", code="missing_field", status=422)
        device_token = device_token.replace(" ", "").replace("<", "").replace(">", "")
        try:
            from push_tokens import remove_push_token
            remove_push_token(device_token)
        except Exception as e:
            logger.warning("push/unregister failed: %s", e)
        return JSONResponse({"status": "ok"})
