"""
AirPrint Profile Management Routes (v0.8.0)
=============================================
Endpoints für die iOS-App und Web-UI zum Erstellen, Auflisten,
Herunterladen und Widerrufen von AirPrint-Profilen.

Endpoints:
  POST   /desktop/me/airprint/create
  GET    /desktop/me/airprint
  GET    /desktop/me/airprint/{profile_id}/download
  DELETE /desktop/me/airprint/{profile_id}

  GET    /admin/airprint/profiles           (Admin, listet alle)
  POST   /admin/airprint/settings           (Admin, Feature-Flag + Default-Queue)
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response

from web.desktop_routes import _require_token, _json_error, _log_req

logger = logging.getLogger("printix.airprint.routes")


def register_airprint_management_routes(app: FastAPI) -> None:
    """Registriert alle Profil-Verwaltungs-Endpoints."""

    # ──────────────────────────────────────────────────────────────
    # POST /desktop/me/airprint/create
    # Body: {queue_id, printer_id, queue_display_name?, display_name?}
    # ──────────────────────────────────────────────────────────────
    @app.post("/desktop/me/airprint/create")
    async def airprint_create(request: Request,
                                authorization: str = Header(default="")):
        _log_req(request, "POST /me/airprint/create")
        user = _require_token(authorization)
        if not user:
            return _json_error("token invalid", code="auth_required", status=401)

        if not _feature_enabled():
            return _json_error(
                "AirPrint ist auf diesem Server nicht aktiviert",
                code="feature_disabled", status=403,
            )

        try:
            body = await request.json()
        except Exception:
            return _json_error("bad body", code="bad_request", status=400)

        queue_id = (body.get("queue_id") or "").strip()
        printer_id = (body.get("printer_id") or "").strip()
        queue_display_name = (body.get("queue_display_name") or "").strip()
        display_name = (body.get("display_name") or "").strip()

        if not queue_id or not printer_id:
            return _json_error(
                "queue_id + printer_id sind Pflicht",
                code="bad_request", status=400,
            )

        from cloudprint.airprint_profiles import create_profile
        try:
            profile = create_profile(
                user_id=user["user_id"],
                printer_id=printer_id,
                queue_id=queue_id,
                queue_display_name=queue_display_name,
                display_name=display_name,
                created_via="app",
            )
        except Exception as e:
            logger.error("AirPrint create: %s", e)
            return _json_error(str(e)[:200], code="create_failed", status=500)

        # Audit
        try:
            import json as _json
            from db import audit as _audit
            _audit(user["user_id"], "airprint_profile_created",
                   details=_json.dumps({
                       "profile_id": profile["id"],
                       "queue_id": queue_id,
                       "queue_display_name": queue_display_name,
                       "via": "app",
                   }, ensure_ascii=False))
        except Exception:
            pass

        return JSONResponse({
            "profile_id": profile["id"],
            "download_url": f"/desktop/me/airprint/{profile['id']}/download",
            "queue_display_name": queue_display_name,
        })

    # ──────────────────────────────────────────────────────────────
    # GET /desktop/me/airprint/company-default
    # Liefert die Admin-vordefinierte Firmen-Queue (falls konfiguriert),
    # so dass die iOS-App einen One-Tap-„installieren"-Button anbieten
    # kann. Signalisiert zusätzlich ob der User bereits ein Profil für
    # diese Queue hat.
    # ──────────────────────────────────────────────────────────────
    @app.get("/desktop/me/airprint/company-default")
    async def airprint_company_default(request: Request,
                                          authorization: str = Header(default="")):
        _log_req(request, "GET /me/airprint/company-default")
        user = _require_token(authorization)
        if not user:
            return _json_error("token invalid", code="auth_required", status=401)

        from db import get_setting as _gs
        queue_id = _gs("ios_mobile_airprint_default_queue_id", "")
        printer_id = _gs("ios_mobile_airprint_default_printer_id", "")
        queue_name = _gs("ios_mobile_airprint_default_queue_name", "")
        configured = bool(queue_id and printer_id)

        existing_profile_id = ""
        if configured:
            from cloudprint.airprint_profiles import list_profiles_for_user
            for p in list_profiles_for_user(user["user_id"], include_revoked=False):
                if p.get("queue_id") == queue_id:
                    existing_profile_id = p["id"]
                    break

        return JSONResponse({
            "configured":              configured,
            "feature_enabled":         _feature_enabled(),
            "queue_id":                queue_id,
            "printer_id":              printer_id,
            "queue_display_name":      queue_name or "SecurePrint",
            "existing_profile_id":     existing_profile_id,
        })

    # ──────────────────────────────────────────────────────────────
    # GET /desktop/me/airprint
    # ──────────────────────────────────────────────────────────────
    @app.get("/desktop/me/airprint")
    async def airprint_list(request: Request,
                              authorization: str = Header(default="")):
        _log_req(request, "GET /me/airprint")
        user = _require_token(authorization)
        if not user:
            return _json_error("token invalid", code="auth_required", status=401)

        from cloudprint.airprint_profiles import list_profiles_for_user
        profiles = list_profiles_for_user(user["user_id"], include_revoked=False)
        # profile_token NICHT ans Frontend leaken — nur Metadaten
        safe = [{
            "id": p["id"],
            "queue_id": p["queue_id"],
            "queue_display_name": p["queue_display_name"],
            "display_name": p.get("display_name") or "",
            "created_at": p["created_at"],
            "created_via": p.get("created_via") or "app",
            "last_used_at": p.get("last_used_at"),
            "job_count": p.get("job_count") or 0,
        } for p in profiles]
        return JSONResponse({"profiles": safe})

    # ──────────────────────────────────────────────────────────────
    # GET /desktop/me/airprint/{profile_id}/download
    # Response: application/x-apple-aspen-config
    # ──────────────────────────────────────────────────────────────
    @app.get("/desktop/me/airprint/{profile_id}/download")
    async def airprint_download(profile_id: str, request: Request,
                                  authorization: str = Header(default="")):
        _log_req(request, f"GET /me/airprint/{profile_id}/download")
        user = _require_token(authorization)
        if not user:
            return _json_error("token invalid", code="auth_required", status=401)

        from cloudprint.airprint_profiles import get_profile_by_id
        profile = get_profile_by_id(profile_id)
        if not profile:
            return _json_error("profile not found", code="not_found", status=404)
        if profile["user_id"] != user["user_id"]:
            return _json_error("forbidden", code="forbidden", status=403)
        if profile.get("is_revoked"):
            return _json_error("profile revoked", code="revoked", status=410)

        # Server-URL zusammensetzen (aus Request oder Setting)
        server_url = _server_public_url(request)

        # Optional Signing-Cert aus Settings
        from db import get_setting as _gs
        cert_pem = _gs("airprint_signing_cert_pem", "")
        key_pem = _gs("airprint_signing_key_pem", "")

        from cloudprint.airprint_mobileconfig import (
            generate_mobileconfig_for_profile, suggest_filename,
        )
        payload, mime = generate_mobileconfig_for_profile(
            profile=profile,
            server_url=server_url,
            organization=_gs("airprint_organization", "MySecurePrint"),
            cert_pem=cert_pem,
            key_pem=key_pem,
        )
        filename = suggest_filename(profile)

        # Audit — Download der tokenhaltigen .mobileconfig loggen
        try:
            import json as _json
            from db import audit as _audit
            _audit(user["user_id"], "airprint_profile_downloaded",
                   details=_json.dumps({
                       "profile_id":         profile["id"],
                       "queue_id":           profile.get("queue_id"),
                       "queue_display_name": profile.get("queue_display_name"),
                       "via":                "app",
                   }, ensure_ascii=False))
        except Exception:
            pass

        return Response(
            content=payload,
            media_type=mime,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    # ──────────────────────────────────────────────────────────────
    # DELETE /desktop/me/airprint/{profile_id}
    # ──────────────────────────────────────────────────────────────
    @app.delete("/desktop/me/airprint/{profile_id}")
    async def airprint_revoke(profile_id: str, request: Request,
                                authorization: str = Header(default="")):
        _log_req(request, f"DELETE /me/airprint/{profile_id}")
        user = _require_token(authorization)
        if not user:
            return _json_error("token invalid", code="auth_required", status=401)

        from cloudprint.airprint_profiles import get_profile_by_id, revoke_profile
        profile = get_profile_by_id(profile_id)
        if not profile:
            return _json_error("profile not found", code="not_found", status=404)
        if profile["user_id"] != user["user_id"]:
            return _json_error("forbidden", code="forbidden", status=403)

        revoked = revoke_profile(profile_id, reason="user_deleted_via_app")

        try:
            import json as _json
            from db import audit as _audit
            _audit(user["user_id"], "airprint_profile_revoked",
                   details=_json.dumps({
                       "profile_id": profile_id,
                       "via": "app",
                   }, ensure_ascii=False))
        except Exception:
            pass

        return JSONResponse({"revoked": revoked, "profile_id": profile_id})


# ─── Helpers ────────────────────────────────────────────────────────────────

def _feature_enabled() -> bool:
    try:
        from db import get_setting as _gs
        return _gs("ios_mobile_airprint_enabled", "0") == "1"
    except Exception:
        return False


def _server_public_url(request: Request) -> str:
    """Aus Request oder DB-Setting die public Base-URL bauen."""
    try:
        from db import get_setting as _gs
        cfg = _gs("public_url", "")
        if cfg:
            return cfg.rstrip("/")
    except Exception:
        pass
    proto = request.headers.get("x-forwarded-proto", "https")
    host = request.headers.get("x-forwarded-host") \
        or request.headers.get("host") \
        or "localhost"
    return f"{proto}://{host}"
