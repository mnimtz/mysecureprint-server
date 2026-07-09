"""
Admin-Routen für AirPrint-Profile (v0.8.0)
============================================
Verwaltungsseite für Admins: Liste aller User mit Anzahl ihrer Profile,
Suche, Profil-Erstellung im Namen eines Users, Download als
.mobileconfig oder .zip (letzteres für strenge Mail-Gateway-Filter).

Endpoints:
  GET   /admin/airprint-users                        — Liste aller User + Profil-Anzahl
  GET   /admin/airprint-users/{user_id}              — Detail: Profile eines Users
  POST  /admin/airprint-users/{user_id}/create       — Neues Profil für User erstellen
  GET   /admin/airprint/download/{profile_id}        — .mobileconfig
  GET   /admin/airprint/download/{profile_id}.zip    — Zip mit mobileconfig + README
  POST  /admin/airprint/revoke/{profile_id}          — Widerrufen
"""

from __future__ import annotations

import io
import logging
import zipfile
from typing import Optional

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

logger = logging.getLogger("printix.airprint.admin")


def register_airprint_admin_routes(app: FastAPI,
                                     templates: Jinja2Templates,
                                     get_session_user) -> None:
    """Registriert die Admin-Verwaltungs-Endpoints."""

    # ──────────────────────────────────────────────────────────────
    # GET /admin/airprint-users
    # ──────────────────────────────────────────────────────────────
    @app.get("/admin/airprint-users", response_class=HTMLResponse)
    async def airprint_users_list(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        query = (request.query_params.get("q") or "").strip().lower()
        users = _list_users_with_profile_counts(query=query)
        # Verfügbare Queues für Dropdown
        queues = _list_queues()
        # Statistik-Karte
        stats = _airprint_stats()
        return templates.TemplateResponse("admin_airprint_users.html", {
            "request": request,
            "user": user,
            "search_query": query,
            "users_list": users,
            "queues": queues,
            "stats": stats,
            "active_page": "admin_airprint_users",
            "section": "ios_mobile",
        })

    # ──────────────────────────────────────────────────────────────
    # POST /admin/airprint-users/{user_id}/create
    # ──────────────────────────────────────────────────────────────
    @app.post("/admin/airprint-users/{target_user_id}/create")
    async def airprint_admin_create(target_user_id: str, request: Request,
                                      queue_id: str = Form(""),
                                      printer_id: str = Form(""),
                                      queue_display_name: str = Form(""),
                                      display_name: str = Form("")):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        if not queue_id or not printer_id:
            return JSONResponse(
                {"error": "queue_id + printer_id sind Pflicht"},
                status_code=400,
            )
        # Existiert der User?
        from db import _conn
        with _conn() as conn:
            row = conn.execute(
                "SELECT user_id, email, username FROM users WHERE user_id = ?",
                (target_user_id,),
            ).fetchone()
        if not row:
            return JSONResponse({"error": "User nicht gefunden"}, status_code=404)

        from cloudprint.airprint_profiles import create_profile
        profile = create_profile(
            user_id=target_user_id,
            printer_id=printer_id,
            queue_id=queue_id,
            queue_display_name=queue_display_name or "SecurePrint",
            display_name=(display_name or "").strip(),
            created_via="admin",
        )
        # Audit
        try:
            import json as _json
            from db import audit as _audit
            _audit(user["id"], "airprint_admin_created_profile",
                   details=_json.dumps({
                       "profile_id": profile["id"],
                       "for_user_id": target_user_id,
                       "for_user_email": row["email"],
                       "queue_id": queue_id,
                       "queue_display_name": queue_display_name,
                   }, ensure_ascii=False))
        except Exception:
            pass
        # → Redirect zum Detail des Users mit Success-Flag
        return RedirectResponse(
            f"/admin/airprint-users/{target_user_id}?created={profile['id']}",
            status_code=303,
        )

    # ──────────────────────────────────────────────────────────────
    # GET /admin/airprint-users/{user_id}
    # ──────────────────────────────────────────────────────────────
    @app.get("/admin/airprint-users/{target_user_id}",
              response_class=HTMLResponse)
    async def airprint_admin_user_detail(target_user_id: str, request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        from db import _conn
        with _conn() as conn:
            row = conn.execute(
                "SELECT user_id, email, username, role_type FROM users WHERE user_id = ?",
                (target_user_id,),
            ).fetchone()
        if not row:
            return HTMLResponse("<p>User nicht gefunden</p>", status_code=404)
        target = dict(row)
        # Profile des Users
        from cloudprint.airprint_profiles import list_profiles_for_user
        profiles = list_profiles_for_user(target_user_id, include_revoked=True)
        # Queues für Erstellen-Dropdown
        queues = _list_queues()
        just_created = request.query_params.get("created", "")
        return templates.TemplateResponse("admin_airprint_user_detail.html", {
            "request": request,
            "user": user,
            "target": target,
            "profiles": profiles,
            "queues": queues,
            "just_created": just_created,
            "active_page": "admin_airprint_users",
            "section": "ios_mobile",
        })

    # ──────────────────────────────────────────────────────────────
    # Download-Endpoints (Admin)
    # ──────────────────────────────────────────────────────────────
    @app.get("/admin/airprint/download/{profile_id}")
    async def airprint_admin_download(profile_id: str, request: Request):
        return _admin_download_impl(profile_id, request, as_zip=False)

    @app.get("/admin/airprint/download/{profile_id}.zip")
    async def airprint_admin_download_zip(profile_id: str, request: Request):
        return _admin_download_impl(profile_id, request, as_zip=True)

    def _admin_download_impl(profile_id: str, request: Request,
                              as_zip: bool) -> Response:
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        from cloudprint.airprint_profiles import get_profile_by_id
        profile = get_profile_by_id(profile_id)
        if not profile:
            return HTMLResponse("<p>Profil nicht gefunden</p>", status_code=404)
        if profile.get("is_revoked"):
            return HTMLResponse("<p>Profil wurde widerrufen</p>", status_code=410)
        # .mobileconfig generieren
        from db import get_setting as _gs
        from cloudprint.airprint_mobileconfig import (
            generate_mobileconfig_for_profile, suggest_filename,
        )
        server_url = _admin_server_url(request)
        cert_pem = _gs("airprint_signing_cert_pem", "")
        key_pem = _gs("airprint_signing_key_pem", "")
        organization = _gs("airprint_organization", "MySecurePrint")
        payload, mime = generate_mobileconfig_for_profile(
            profile=profile,
            server_url=server_url,
            organization=organization,
            cert_pem=cert_pem, key_pem=key_pem,
        )
        base_filename = suggest_filename(profile, organization).replace(
            ".mobileconfig", ""
        )

        if not as_zip:
            filename = f"{base_filename}.mobileconfig"
            return Response(
                content=payload, media_type=mime,
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Cache-Control": "no-store",
                },
            )

        # ZIP-Variante: mobileconfig + README.txt mit Anleitung
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{base_filename}.mobileconfig", payload)
            zf.writestr("README.txt", _readme_text(profile, organization))
        filename = f"{base_filename}.zip"
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    # ──────────────────────────────────────────────────────────────
    # POST /admin/airprint/revoke/{profile_id}
    # ──────────────────────────────────────────────────────────────
    @app.post("/admin/airprint/revoke/{profile_id}")
    async def airprint_admin_revoke(profile_id: str, request: Request,
                                      reason: str = Form("")):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        from cloudprint.airprint_profiles import (
            get_profile_by_id, revoke_profile,
        )
        profile = get_profile_by_id(profile_id)
        if not profile:
            return HTMLResponse("<p>Profil nicht gefunden</p>", status_code=404)
        revoke_profile(profile_id, reason=reason or "admin_manual")
        try:
            import json as _json
            from db import audit as _audit
            _audit(user["id"], "airprint_admin_revoked_profile",
                   details=_json.dumps({
                       "profile_id": profile_id,
                       "for_user_id": profile["user_id"],
                       "reason": reason,
                   }, ensure_ascii=False))
        except Exception:
            pass
        return RedirectResponse(
            f"/admin/airprint-users/{profile['user_id']}?revoked=1",
            status_code=303,
        )


# ─── Helpers ────────────────────────────────────────────────────────────────

def _list_users_with_profile_counts(query: str = "",
                                     limit: int = 200) -> list[dict]:
    """User-Liste mit Anzahl aktiver Profile pro User + optionalem Suchfilter."""
    from db import _conn
    with _conn() as conn:
        if query:
            q_like = f"%{query}%"
            rows = conn.execute(
                """SELECT u.user_id, u.email, u.username, u.role_type,
                            COALESCE(SUM(CASE WHEN p.is_revoked = 0 THEN 1 ELSE 0 END), 0) AS active_profiles,
                            COALESCE(COUNT(p.id), 0) AS total_profiles,
                            MAX(p.last_used_at) AS last_used_at
                     FROM users u
                LEFT JOIN cloudprint_airprint_profiles p ON p.user_id = u.user_id
                    WHERE LOWER(u.email) LIKE ? OR LOWER(u.username) LIKE ?
                 GROUP BY u.user_id
                 ORDER BY u.email
                    LIMIT ?""",
                (q_like, q_like, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT u.user_id, u.email, u.username, u.role_type,
                            COALESCE(SUM(CASE WHEN p.is_revoked = 0 THEN 1 ELSE 0 END), 0) AS active_profiles,
                            COALESCE(COUNT(p.id), 0) AS total_profiles,
                            MAX(p.last_used_at) AS last_used_at
                     FROM users u
                LEFT JOIN cloudprint_airprint_profiles p ON p.user_id = u.user_id
                 GROUP BY u.user_id
                 ORDER BY u.email
                    LIMIT ?""",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def _list_queues() -> list[dict]:
    """Verfügbare Queues aus group_queue_defaults für Dropdown."""
    from db import _conn
    with _conn() as conn:
        rows = conn.execute(
            """SELECT DISTINCT queue_id, printer_id, queue_label AS display_name
                 FROM group_queue_defaults
                WHERE queue_id != ''
                ORDER BY queue_label""",
        ).fetchall()
    return [dict(r) for r in rows]


def _airprint_stats() -> dict:
    from db import _conn
    with _conn() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS total_profiles,
                        SUM(CASE WHEN is_revoked = 0 THEN 1 ELSE 0 END) AS active_profiles,
                        COUNT(DISTINCT user_id) AS distinct_users,
                        COALESCE(SUM(job_count), 0) AS total_jobs
                 FROM cloudprint_airprint_profiles""",
        ).fetchone()
    return dict(row) if row else {}


def _admin_server_url(request: Request) -> str:
    try:
        from db import get_setting as _gs
        cfg = _gs("public_url", "")
        if cfg:
            return cfg.rstrip("/")
    except Exception:
        pass
    proto = request.headers.get("x-forwarded-proto", "https")
    host = (request.headers.get("x-forwarded-host")
            or request.headers.get("host") or "localhost")
    return f"{proto}://{host}"


def _readme_text(profile: dict, organization: str) -> str:
    """Einfacher, i18n-loser Text — der ZIP-Empfänger nutzt sein Betriebssystem
    und die Sprache des .mobileconfig steuert dort das Verhalten."""
    queue = profile.get("queue_display_name") or "SecurePrint"
    lines = [
        f"{organization} — iOS/iPadOS/macOS AirPrint Profile",
        "=" * 60,
        "",
        "This ZIP contains a native printer profile that lets you print",
        f"from any iOS, iPadOS or macOS app to '{queue}'.",
        "",
        "INSTALLATION",
        "-" * 60,
        "",
        "iPhone / iPad:",
        "  1. Open the .mobileconfig file on your device",
        "     (e.g. via AirDrop, email attachment, or Files app)",
        "  2. iOS shows a 'Profile downloaded' dialog",
        "  3. Go to Settings -> Profile Downloaded -> Install",
        "  4. Confirm with your passcode",
        "  5. Done. In any app tap Print and select 'MySecurePrint'.",
        "",
        "Mac (macOS Sequoia and later):",
        "  1. Double-click the .mobileconfig file",
        "  2. System Settings opens automatically",
        "  3. Confirm 'Install' on the profile prompt",
        "  4. The printer appears in System Settings -> Printers & Scanners",
        "",
        "SUPPORT",
        "-" * 60,
        "",
        "If the profile is rejected or the printer does not appear,",
        "contact your IT administrator or the sender of this ZIP.",
        "",
    ]
    return "\r\n".join(lines) + "\r\n"
