"""mysecureprint-server Web UI (FastAPI). iOS-backend admin console."""

import os
import json
import logging
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse, Response, PlainTextResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

logger = logging.getLogger("printix.web")

# Templates-Verzeichnis (relativ zu diesem File)
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


# v6.7.5: Helper für persistenten Printix-User-Sync.
# Wird nach Credentials-Save und auf Refresh-Button-Klick getriggert.
async def _trigger_printix_user_sync(tenant: dict) -> dict:
    """Pullt alle Printix-User dieses Tenants in `cached_printix_users`.

    Läuft in einem Thread (synchroner Printix-Client + DB-Writes), die
    asyncio-Wrapper ist nur Convenience für `asyncio.create_task`.
    """
    import asyncio as _asyncio
    from printix_client import PrintixClient
    from cloudprint.printix_cache_db import sync_users_for_tenant

    def _do_sync():
        client = PrintixClient(
            tenant_id=tenant["printix_tenant_id"],
            print_client_id=tenant.get("print_client_id", ""),
            print_client_secret=tenant.get("print_client_secret", ""),
            ws_client_id=tenant.get("ws_client_id", ""),
            ws_client_secret=tenant.get("ws_client_secret", ""),
            um_client_id=tenant.get("um_client_id", ""),
            um_client_secret=tenant.get("um_client_secret", ""),
            shared_client_id=tenant.get("shared_client_id", ""),
            shared_client_secret=tenant.get("shared_client_secret", ""),
        )
        return sync_users_for_tenant(
            tenant_id=tenant["id"],
            printix_tenant_id=tenant["printix_tenant_id"],
            client=client,
        )

    return await _asyncio.to_thread(_do_sync)


# v7.2.29: Web-UI Tenant-Log-Handler.
# Der MCP-Server (server.py) hat einen _TenantDBHandler der bei
# authentifizierten Tool-Calls in tenant_logs schreibt. Die Web-UI lief
# bisher OHNE einen solchen Handler — alle Web-Aktivität (Login,
# Settings-Saves, Capture-Konfig, Admin-Aktionen) blieb daher unsichtbar
# in der /logs-Anzeige. Dieser Handler füllt die Lücke.
#
# Single-Tenant-Setup: alle Web-Aktivität gehört zum einzigen Tenant des
# Owner-Admins. Wir holen die Tenant-ID lazy beim ersten Emit und cachen
# sie — die DB-Lookups bei jedem Log-Record würden sonst spürbar Latenz
# in jeden Request einbauen.
import threading as _web_log_threading
import time as _web_log_time

class _WebTenantDBHandler(logging.Handler):
    """Schreibt Web-UI-Logs in tenant_logs.

    Tenant-ID wird einmalig pro Prozess via _find_tenant_owner_user_id
    aufgelöst. Re-Lookup mit 5-Sekunden-Cooldown nach Misserfolg, falls
    der Tenant erst nach App-Start angelegt wird (frische Installation).
    Reentrancy-Schutz via thread-local Flag — verhindert, dass
    add_tenant_log selbst Log-Records erzeugt die wieder hier landen.
    """
    _CATEGORY_MAP = {
        "printix_client": "PRINTIX_API",
        "reporting":      "SQL",
        "sql":            "SQL",
        "auth":           "AUTH",
        "oauth":          "AUTH",
        "capture":        "CAPTURE",
    }
    _emit_local = _web_log_threading.local()

    def __init__(self):
        super().__init__()
        self._cached_tid: str = ""
        self._last_attempt: float = 0.0

    def _resolve_tid(self) -> str:
        if self._cached_tid:
            return self._cached_tid
        now = _web_log_time.monotonic()
        if now - self._last_attempt < 5.0:
            return ""
        self._last_attempt = now
        try:
            from db import _find_tenant_owner_user_id, get_tenant_by_user_id
            uid = _find_tenant_owner_user_id()
            if not uid:
                return ""
            t = get_tenant_by_user_id(uid)
            if t and t.get("id"):
                self._cached_tid = t["id"]
        except Exception:
            return ""
        return self._cached_tid

    def emit(self, record: logging.LogRecord) -> None:
        # Reentrancy-Schutz: wenn add_tenant_log selbst loggt, nicht zurückkommen
        if getattr(self._emit_local, "in_emit", False):
            return
        try:
            self._emit_local.in_emit = True
            tid = self._resolve_tid()
            if not tid:
                return
            name_lower = record.name.lower()
            category = "SYSTEM"
            for key, cat in self._CATEGORY_MAP.items():
                if key in name_lower:
                    category = cat
                    break
            try:
                msg = self.format(record)
            except Exception:
                msg = record.getMessage()
            from db import add_tenant_log
            add_tenant_log(tid, record.levelname, category, msg)
        except Exception:
            pass  # niemals Server crashen wegen Logging
        finally:
            self._emit_local.in_emit = False


_web_tenant_handler = _WebTenantDBHandler()
_web_tenant_handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
_web_tenant_handler.setLevel(logging.INFO)
# An Root-Logger hängen — fängt Records aus printix.web, uvicorn,
# fastapi, capture, oauth etc. ein. add_tenant_log selbst geht aufgrund
# des Reentrancy-Schutzes nicht in Schleife.
logging.getLogger().addHandler(_web_tenant_handler)


def create_app(session_secret: str) -> FastAPI:
    app = FastAPI(title="Printix Management Console", docs_url=None, redoc_url=None)

    templates = Jinja2Templates(directory=TEMPLATES_DIR)

    # v7.2.19: Jinja-Filter `from_json` — settings.html nutzt
    # `{{ tenant.notify_events | from_json }}` um den im DB-Feld als JSON-
    # Array gespeicherten Wert zu Python-Listen zu parsen. Ohne diesen
    # Filter scheitert die Settings-Seite mit
    # `TemplateRuntimeError: No filter named 'from_json' found.`
    # (Bug shipped in v7.2.17 — CHANGELOG behauptete den Fix, der Code
    # wurde aber nur im HA-Addon-Schwesterprojekt registriert, nie hier.)
    def _from_json_filter(value):
        import json as _json
        if value in (None, "", b""):
            return []
        if isinstance(value, (list, dict)):
            return value
        try:
            return _json.loads(value)
        except Exception:
            return []
    templates.env.filters["from_json"] = _from_json_filter

    def current_app_version() -> str:
        version_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "VERSION")
        try:
            with open(version_path, "r", encoding="utf-8") as fh:
                return fh.read().strip() or "?"
        except Exception:
            return "?"

    # ── i18n ──────────────────────────────────────────────────────────────────

    from i18n import (
        detect_language, make_translator,
        SUPPORTED_LANGUAGES, LANGUAGE_NAMES, DEFAULT_LANGUAGE,
    )

    def get_lang(request: Request) -> str:
        """Gibt den aktiven Sprachcode zurück (Session → Accept-Language → Default)."""
        lang = request.session.get("lang")
        if lang in SUPPORTED_LANGUAGES:
            return lang
        return detect_language(request.headers.get("accept-language"))

    # ─── Display Timezone (v7.2.48) ───────────────────────────────────────
    # Container läuft intern in UTC (Best Practice für Storage).
    # Anzeige im Web-UI: konfigurierbar über `display_timezone` Setting.
    # Resolution: DB-Setting → TZ Env-Var → Default 'Europe/Berlin'.

    def _resolve_display_tz_name() -> str:
        try:
            from db import get_setting
            v = (get_setting("display_timezone", "") or "").strip()
            if v:
                return v
        except Exception:
            pass
        return (os.environ.get("TZ", "") or "Europe/Berlin").strip()

    def _resolve_display_tz():
        """Returns a ZoneInfo instance for the configured display TZ.
        Falls back to UTC if everything fails."""
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(_resolve_display_tz_name())
        except Exception:
            try:
                from zoneinfo import ZoneInfo
                return ZoneInfo("UTC")
            except Exception:
                from datetime import timezone as _tz
                return _tz.utc

    def _localtime_filter(value):
        """Jinja-Filter: konvertiert UTC-ISO-String oder datetime zur
        konfigurierten Display-Zeitzone und formatiert als
        'YYYY-MM-DD HH:MM:SS TZ'."""
        if not value:
            return ""
        try:
            from datetime import datetime as _dt
            tz = _resolve_display_tz()
            if isinstance(value, _dt):
                d = value
            else:
                # ISO-String parse — Z-Suffix für UTC unterstützen
                s = str(value).strip()
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                d = _dt.fromisoformat(s)
            if d.tzinfo is None:
                from datetime import timezone as _utc
                d = d.replace(tzinfo=_utc.utc)
            local = d.astimezone(tz)
            return local.strftime("%Y-%m-%d %H:%M:%S %Z")
        except Exception:
            return str(value)

    templates.env.filters["localtime"] = _localtime_filter

    def t_ctx(request: Request) -> dict:
        """Gibt den i18n-Kontext für Templates zurück."""
        lang = get_lang(request)
        ctx = {
            "_":             make_translator(lang),
            "lang":          lang,
            "lang_names":    LANGUAGE_NAMES,
            "supported_langs": SUPPORTED_LANGUAGES,
        }
        # v7.2.39: Pro-Feature-Flags für Templates (Nav-Hiding etc.)
        try:
            import sys as _ls
            _ls.path.insert(0, "/app")
            from license import is_feature_enabled as _ife
            ctx["pro_capture_enabled"]    = _ife("capture_store")
            ctx["pro_guestprint_enabled"] = _ife("guest_print")
            ctx["pro_print_job_mgmt_enabled"] = _ife("print_job_mgmt")
        except Exception:
            ctx["pro_capture_enabled"]    = False
            ctx["pro_guestprint_enabled"] = False
            ctx["pro_print_job_mgmt_enabled"] = False
        # v3.9.0 — Badge "offene Tickets" im Nav (nur für Admins relevant)
        try:
            from db import count_feature_requests_by_status
            counts = count_feature_requests_by_status()
            ctx["feedback_new_count"] = counts.get("new", 0)
        except Exception:
            ctx["feedback_new_count"] = 0
        # v7.7.8: App-Version in allen Templates verfügbar (Login-Seite etc.)
        ctx["app_version"] = current_app_version()
        # v7.9.0: Sidebar — active_page aus URL-Pfad ableiten
        _path = str(request.url.path).rstrip("/") or "/"
        _page_map = {
            "/admin/audit": "audit",
            "/my": "my_portal",
            "/admin": "admin_dashboard",
            "/admin/users": "admin_users",
            "/admin/users/invite": "admin_invite",
            "/admin/users/bulk-import": "admin_bulk",
            "/admin/users/create": "admin_create_user",
            "/admin/ssl": "admin_ssl",
            "/admin/ssl/diagnose": "admin_ssl_diagnose",
            "/admin/tls": "admin_tls",
            "/admin/auto-tls": "admin_auto_tls",
            "/admin/tunnel": "admin_tunnel",
            "/admin/settings": "admin_settings",
            "/admin/blob-backup": "admin_blob_backup",
            "/admin/mcp-access": "admin_mcp_access",
            "/admin/mcp-permissions": "admin_rbac",
            "/admin/gdpr": "admin_gdpr",
            "/admin/groups": "admin_groups",
            "/account": "account",
            "/admin/printix-sync": "admin_printix_sync",
            "/admin/email-templates": "admin_email_templates",
            "/my/cloud-print": "my_cloud_print",
            "/my/mobile-app": "my_mobile_app",
        }
        _active = _page_map.get(_path, "")
        if not _active:
            # Prefix-Matching fuer Detail-Seiten mit dynamischen IDs
            for _prefix, _val in [
                ("/admin/users/", "admin_users"),
                ("/my/", "my_portal"),
            ]:
                if _path.startswith(_prefix):
                    _active = _val
                    break
        ctx["active_page"] = _active
        return ctx

    # ── Helpers ────────────────────────────────────────────────────────────────

    def get_session_user(request: Request) -> Optional[dict]:
        user_id = request.session.get("user_id")
        if not user_id:
            return None
        try:
            from db import get_user_by_id
            return get_user_by_id(user_id)
        except Exception:
            return None

    def _generate_temp_password(length: int = 14) -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@$%?"
        return "".join(secrets.choice(alphabet) for _ in range(length))

    def require_login(request: Request) -> Optional[dict]:
        user = get_session_user(request)
        if not user:
            return None
        if user.get("status") != "approved":
            return None
        return user

    def _user_home_target(user: Optional[dict]) -> str:
        if not user:
            return "/login"
        if user.get("is_admin"):
            return "/admin"
        if user.get("status") == "pending":
            return "/pending"
        # v0.6.6: ALLE Nicht-Admin-User (employee + user) landen auf /account
        # (Info-Seite mit QR, MCP-Credentials, OAuth-Daten, GDPR).
        # /my (Mitarbeiter-Portal) bleibt als Route erhalten und ist
        # weiterhin ueber die Sidebar/Navigation erreichbar.
        return "/account"

    @app.middleware("http")
    async def invitation_activation_guard(request: Request, call_next):
        allowed_paths = {
            "/login",
            "/logout",
            "/pending",
            "/account/activate",
        }
        path = request.url.path or "/"
        if not path.startswith("/auth/entra") and not path.startswith("/lang/") and path not in allowed_paths:
            session = request.scope.get("session") or {}
            user_id = session.get("user_id")
            if user_id:
                try:
                    from db import get_user_by_id
                    active_user = get_user_by_id(user_id)
                except Exception:
                    active_user = None
                if active_user and active_user.get("must_change_password"):
                    return RedirectResponse("/account/activate", status_code=302)
                if active_user and active_user.get("role_type") == "employee":
                    # v0.6.6: Employees duerfen jetzt auch /account (Default-Landing)
                    # sehen — Info-Seite mit QR/MCP/GDPR.
                    employee_allowed_prefixes = (
                        "/my",
                        "/account",
                        "/logout",
                        "/lang/",
                        "/auth/entra",
                    )
                    employee_allowed_paths = {
                        "/",
                        "/login",
                        "/pending",
                    }
                    if path not in employee_allowed_paths and not any(path.startswith(prefix) for prefix in employee_allowed_prefixes):
                        return RedirectResponse("/account", status_code=302)
        return await call_next(request)

    # v0.7.29: Session-Cookie haerten.
    # - https_only blockt HTTP-Klartext (Azure-WebApp ist immer TLS).
    # - same_site=lax = Starlette-Default, hier explizit gemacht damit
    #   spaetere Upgrades nicht versehentlich auf "none" springen.
    # Local-Dev (kein TLS) muss SESSION_COOKIE_INSECURE=1 setzen.
    _https_only = (os.environ.get("SESSION_COOKIE_INSECURE", "") != "1")
    app.add_middleware(SessionMiddleware,
                         secret_key=session_secret,
                         max_age=3600 * 8,
                         https_only=_https_only,
                         same_site="lax")

    def mcp_base_url() -> str:
        """Gibt die öffentliche MCP-Basis-URL zurück.

        Auflösung (2-stufig, v7.0.0):
          1. DB-Setting ``public_url`` (Admin-UI, Laufzeit)
          2. Env ``MCP_PUBLIC_URL`` (Deploy-Default)

        Ist nichts gesetzt, wird ein Leerstring zurückgegeben — Aufrufer
        müssen das behandeln (oder ``mcp_base_url_or(request)`` verwenden).
        """
        try:
            from db import get_setting
            db_url = (get_setting("public_url", "") or "").strip().rstrip("/")
            if db_url:
                return db_url
        except Exception:
            pass
        return os.environ.get("MCP_PUBLIC_URL", "").strip().rstrip("/")

    def mcp_base_url_or(request: Request) -> str:
        """Wie ``mcp_base_url()``, fällt aber auf den Request-abgeleiteten
        Host zurück wenn nichts konfiguriert ist. Damit funktioniert die
        Admin-UI auch ohne explizite ``public_url``-Konfiguration im
        ersten Boot."""
        url = mcp_base_url()
        if url:
            return url
        return _get_base_url(request)

    # ── Sprach-Route ──────────────────────────────────────────────────────────

    @app.get("/lang/{code}", response_class=RedirectResponse)
    async def switch_language(code: str, request: Request):
        if code in SUPPORTED_LANGUAGES:
            request.session["lang"] = code
        # Open-Redirect-Schutz: Referer-Header darf nur zurückführen, wenn er
        # same-origin ist. Andernfalls fallen wir auf "/" zurück.
        referer = request.headers.get("referer", "")
        safe_target = "/"
        if referer:
            try:
                from urllib.parse import urlparse
                ref = urlparse(referer)
                if not ref.netloc or ref.netloc == request.url.netloc:
                    # Relative Pfade oder gleiche Origin akzeptieren
                    safe_target = referer
            except Exception:
                safe_target = "/"
        return RedirectResponse(safe_target, status_code=302)

    # ── Root ──────────────────────────────────────────────────────────────────

    @app.get("/", response_class=RedirectResponse)
    async def root(request: Request):
        # v0.3.1: Fresh deploy → register (first-admin onboarding); else
        # role-based home target. Anonymous visitors land on /login and
        # see the Microsoft SSO button if Entra is configured. The
        # public /welcome page with config-status indicators was leaking
        # operational info to unauthenticated visitors — it's now
        # admin-only and reached via the admin nav.
        try:
            from db import has_users
            if not has_users():
                return RedirectResponse("/register", status_code=302)
        except Exception:
            return RedirectResponse("/register", status_code=302)
        user = get_session_user(request)
        if user:
            return RedirectResponse(_user_home_target(user), status_code=302)
        return RedirectResponse("/login", status_code=302)

    # ── Public Welcome Page (v0.1.1) ──────────────────────────────────────────

    def _get_printix_status() -> tuple[bool, str]:
        """True wenn mindestens ein Tenant Printix-Credentials hat."""
        try:
            from db import _conn
            with _conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM tenants "
                    "WHERE (print_client_id IS NOT NULL AND print_client_id != '') "
                    "   OR (card_client_id IS NOT NULL AND card_client_id != '') "
                    "   OR (shared_client_id IS NOT NULL AND shared_client_id != '')"
                ).fetchone()
                count = int(row[0]) if row else 0
            if count > 0:
                return True, "configured"
            return False, "missing"
        except Exception as e:
            logger.debug("printix_status check failed: %s", e)
            return False, "missing"

    def _get_entra_status() -> tuple[bool, str]:
        """True wenn Entra-ID Client + Tenant gesetzt sind.

        v0.1.3: liefert zusaetzlich `"warning"` als zweiter Wert, wenn das
        gespeicherte Client-Secret in weniger als 60 Tagen ablaeuft. Das
        Welcome-Template kann den Indikator dann gelb statt gruen rendern.
        """
        try:
            from db import get_setting
            cid = (get_setting("entra_client_id", "") or "").strip()
            tid = (get_setting("entra_tenant_id", "") or "").strip()
            if not (cid and tid):
                return False, "missing"
            # Secret-Ablauf pruefen — leerer Wert = unbekannt, kein Warn.
            exp = (get_setting("entra_secret_expires_at", "") or "").strip()
            if exp:
                try:
                    from datetime import datetime, timezone
                    # MS liefert ISO-8601 mit Z; sicherheitshalber tolerant parsen
                    norm = exp.replace("Z", "+00:00")
                    when = datetime.fromisoformat(norm)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    days = (when - datetime.now(timezone.utc)).days
                    if days < 60:
                        return True, "warning"
                except Exception:
                    pass
            return True, "configured"
        except Exception:
            return False, "missing"

    def _get_legal_status() -> tuple[bool, str]:
        """True wenn legal_operator_name gepflegt ist (Pflicht fuer /imprint)."""
        try:
            from db import get_setting
            op = (get_setting("legal_operator_name", "") or "").strip()
            if op:
                return True, "configured"
            return False, "missing"
        except Exception:
            return False, "missing"

    def _get_admin_status() -> tuple[bool, str]:
        """True wenn mindestens ein approved Admin existiert."""
        try:
            from db import get_all_users
            for u in get_all_users():
                if u.get("is_admin") and u.get("status") == "approved":
                    return True, "configured"
            return False, "missing"
        except Exception:
            return False, "missing"

    def _get_mcp_status() -> tuple[bool, str]:
        """True wenn der MCP-Server admin-seitig aktiviert wurde."""
        try:
            from db import get_setting
            return (get_setting("mcp_enabled", "0") == "1"), "configured"
        except Exception:
            return False, "missing"

    def _make_welcome_qr_svg(payload: str) -> str:
        """Erzeugt einen inline PNG-data-URI QR fuer den Welcome-Screen.

        v0.7.1: SVG-Output von segno ignoriert `scale` und liefert
        width=20, was zu klein zum Scannen ist (weder iPhone-Kamera
        noch unsere App erkennen das). PNG-Output respektiert scale
        zuverlaessig. Wir liefern jetzt ein PNG-data-URI img-Tag
        zurueck — Template-Variable bleibt unveraendert, das HTML
        rendert das Tag direkt.
        """
        try:
            import segno
            import io
            import base64
            qr = segno.make(payload, error="m")
            buf = io.BytesIO()
            qr.save(buf, kind="png", scale=8, border=2,
                    dark="#002854", light="#ffffff")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return (
                f'<img src="data:image/png;base64,{b64}" '
                f'alt="Setup-QR" '
                f'style="width:260px;height:260px;display:block;'
                f'margin:0 auto;image-rendering:pixelated;">'
            )
        except Exception as e:
            logger.warning("welcome QR generation failed: %s", e)
            return ""

    @app.get("/welcome", response_class=HTMLResponse)
    async def welcome_page(request: Request):
        """Admin-Dashboard mit Konfigurations-Status, Server-URL und
        iOS-Setup-QR. v0.3.1: Nicht mehr oeffentlich — die Setup-Status-
        Indikatoren leakten vorher Betriebsdetails (welche Module
        unkonfiguriert sind) an jeden anonymen Besucher. Anon-Besucher
        landen seit /-Redirect-Refactor auf /login (mit MS-SSO-Button
        falls Entra konfiguriert ist), End-User auf /my."""
        user = get_session_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        if not user.get("is_admin"):
            return RedirectResponse(_user_home_target(user), status_code=302)
        base_url = mcp_base_url_or(request)
        # iOS-Deep-Link-Payload — der App-URL-Scheme ist `mysecureprint`,
        # der Setup-Pfad ist eine v0.2.0-Planung. Der QR ist heute schon
        # forward-kompatibel.
        qr_payload = f"mysecureprint://setup?server={base_url}/"
        qr_svg = _make_welcome_qr_svg(qr_payload)

        printix_ok, _   = _get_printix_status()
        entra_ok, _     = _get_entra_status()
        legal_ok, _     = _get_legal_status()
        admin_ok, _     = _get_admin_status()
        mcp_ok, _       = _get_mcp_status()

        user = get_session_user(request)

        return templates.TemplateResponse("welcome.html", {
            "request":      request,
            "user":         user,
            "base_url":     base_url,
            "qr_payload":   qr_payload,
            "qr_svg":       qr_svg,
            "printix_ok":   printix_ok,
            "entra_ok":     entra_ok,
            "legal_ok":     legal_ok,
            "admin_ok":     admin_ok,
            "mcp_ok":       mcp_ok,
            "active_page":  "welcome",
            "version":      current_app_version(),
            **t_ctx(request),
        })

    # ── Registrierung ─────────────────────────────────────────────────────────

    def _notify_admins_of_user_registered(new_user: dict) -> int:
        """Benachrichtigt alle Admins (is_admin=1, status='approved') deren
        Tenant das Event `user_registered` in `notify_events` aktiviert hat.

        Effekt-Funnel pro Admin (alle 5 muessen erfuellt sein, sonst kein Mail):
          1. is_admin = 1 + status = 'approved'
          2. Tenant-Lookup via get_tenant_full_by_user_id liefert was
          3. is_event_enabled(tenant, 'user_registered') == True
             (Toggle in Settings → Benachrichtigungen)
          4. tenant.alert_recipients ist nicht leer (Empfaenger-CSV)
          5. Mail-Credentials via 3-stufige Fallback-Resolution
             (tenant.mail_api_key + mail_from → global Resend → ENV)

        Pro Admin der NICHT in den Mail-Versand kommt, wird ein INFO-Log
        geschrieben mit dem konkreten Grund — sodass der User im Container-
        Log sofort sieht WARUM keine Mail rausging. Vorher haben wir nur
        ein zusammenfassendes "0 Mails versendet" gelogt, aber nicht den Grund.

        Returns: Anzahl tatsaechlich versendeter Mails.
        """
        from db import get_all_users, get_tenant_full_by_user_id
        from reporting.notify_helper import (
            send_event_notification, html_user_registered,
            is_event_enabled, resolve_mail_credentials,
        )
        sent = 0
        new_username = new_user.get("username", "?")
        admins = [
            u for u in get_all_users()
            if u.get("is_admin") and u.get("status") == "approved"
        ]
        if not admins:
            logger.info(
                "user_registered: keine approved Admins gefunden — keine "
                "Mail moeglich (neuer User: %s)", new_username,
            )
            return 0
        subject = (
            f"🔔 Neuer Printix-MCP-Benutzer wartet auf Freischaltung: "
            f"{new_username}"
        )
        html = html_user_registered(
            username=new_username,
            email=new_user.get("email", ""),
            company=new_user.get("company", ""),
        )
        for admin in admins:
            admin_id = admin.get("email") or admin.get("id")
            try:
                admin_tenant = get_tenant_full_by_user_id(admin["id"])
                if not admin_tenant:
                    logger.info(
                        "user_registered: Admin '%s' hat keinen Tenant — skip",
                        admin_id,
                    )
                    continue

                # Pre-Flight-Diagnose mit klaren INFO-Logs (nicht DEBUG),
                # damit User die Ursache OHNE log_level=debug sehen.
                if not is_event_enabled(admin_tenant, "user_registered"):
                    logger.info(
                        "user_registered: Admin '%s' hat 'user_registered' NICHT in "
                        "notify_events aktiv — Toggle in Settings → Benachrichtigungen → "
                        "'🔔 Neuer MCP-Benutzer registriert' anhaken (skip)",
                        admin_id,
                    )
                    continue

                recipients_str = admin_tenant.get("alert_recipients", "") or ""
                recipients = [r.strip() for r in recipients_str.split(",") if r.strip()]
                if not recipients:
                    logger.info(
                        "user_registered: Admin '%s' hat den Toggle aktiv, ABER "
                        "'alert_recipients' ist LEER — bitte Empfaenger-Email(s) "
                        "in Settings → Benachrichtigungen → Empfaenger (CSV) "
                        "eintragen (skip)",
                        admin_id,
                    )
                    continue

                creds = resolve_mail_credentials(admin_tenant)
                if not creds.get("api_key") or not creds.get("mail_from"):
                    logger.info(
                        "user_registered: Admin '%s' — keine Mail-Credentials gefunden "
                        "(Reihenfolge: Tenant-Settings, Global-Fallback unter "
                        "/admin/settings, ENV MAIL_API_KEY/MAIL_FROM). Bitte einen "
                        "der drei konfigurieren (skip)",
                        admin_id,
                    )
                    continue

                # Alle Pre-Flight-Checks ok — Mail tatsaechlich senden.
                # check_enabled=False weil wir oben schon gecheckt haben
                # (sparen einen redundanten DB-Hit + klareres Log).
                ok = send_event_notification(
                    admin_tenant,
                    "user_registered",
                    subject,
                    html,
                    check_enabled=False,
                )
                if ok:
                    sent += 1
                    logger.info(
                        "user_registered: Mail an Admin '%s' gesendet "
                        "(Empfaenger: %s, Mail-Source: %s)",
                        admin_id, ", ".join(recipients), creds.get("source", "?"),
                    )
                else:
                    logger.warning(
                        "user_registered: send_event_notification fuer Admin '%s' "
                        "lieferte False — Mail-Versand-Fehler (siehe vorhergehende "
                        "Log-Zeile)",
                        admin_id,
                    )
            except Exception as e:
                logger.warning(
                    "user_registered: notify for admin '%s' failed: %s",
                    admin_id, e,
                )
        logger.info(
            "user_registered: %d/%d Mail(s) an Admins versendet (neuer User: %s)",
            sent, len(admins), new_username,
        )
        return sent

    @app.get("/register", response_class=HTMLResponse)
    async def register_step1_get(request: Request):
        return templates.TemplateResponse("register_step1.html", {
            "request": request, "step": 1, "error": None, **t_ctx(request)
        })

    @app.post("/register", response_class=HTMLResponse)
    async def register_step1_post(
        request: Request,
        username:  str = Form(...),
        password:  str = Form(...),
        password2: str = Form(...),
        email:     str = Form(default=""),
        full_name: str = Form(default=""),
        company:   str = Form(default=""),
    ):
        tc = t_ctx(request)
        _  = tc["_"]
        error = None
        if len(username) < 3:
            error = _("reg_username_too_short")
        elif len(password) < 8:
            error = _("reg_password_too_short")
        elif password != password2:
            error = _("reg_pw_mismatch")
        else:
            try:
                from db import username_exists
                if username_exists(username):
                    error = _("reg_user_exists")
            except Exception as e:
                error = _("err_database") + f": {e}"

        if error:
            return templates.TemplateResponse("register_step1.html", {
                "request": request, "step": 1, "error": error,
                "username": username, "email": email,
                "full_name": full_name, "company": company, **tc,
            })

        request.session["reg_username"]  = username
        request.session["reg_password"]  = password
        request.session["reg_email"]     = email
        request.session["reg_full_name"] = full_name
        request.session["reg_company"]   = company
        return RedirectResponse("/register/api", status_code=302)

    @app.get("/register/api", response_class=HTMLResponse)
    async def register_step2_get(request: Request):
        if "reg_username" not in request.session:
            return RedirectResponse("/register", status_code=302)
        return templates.TemplateResponse("register_step2.html", {
            "request": request, "step": 2, "error": None, **t_ctx(request)
        })

    @app.post("/register/api", response_class=HTMLResponse)
    async def register_step2_post(
        request: Request,
        printix_tenant_id:     str = Form(...),
        print_client_id:       str = Form(default=""),
        print_client_secret:   str = Form(default=""),
        card_client_id:        str = Form(default=""),
        card_client_secret:    str = Form(default=""),
        ws_client_id:          str = Form(default=""),
        ws_client_secret:      str = Form(default=""),
        um_client_id:          str = Form(default=""),
        um_client_secret:      str = Form(default=""),
        shared_client_id:      str = Form(default=""),
        shared_client_secret:  str = Form(default=""),
        tenant_name:           str = Form(default=""),
    ):
        if "reg_username" not in request.session:
            return RedirectResponse("/register", status_code=302)
        tc = t_ctx(request)

        if not printix_tenant_id.strip():
            return templates.TemplateResponse("register_step2.html", {
                "request": request, "step": 2,
                "error": "Printix Tenant-ID ist Pflichtfeld.", **tc,
            })

        has_creds = any([
            print_client_id and print_client_secret,
            card_client_id and card_client_secret,
            ws_client_id and ws_client_secret,
            um_client_id and um_client_secret,
            shared_client_id and shared_client_secret,
        ])
        if not has_creds:
            return templates.TemplateResponse("register_step2.html", {
                "request": request, "step": 2,
                "error": "Mindestens ein vollständiges API-Credentials-Paar wird benötigt.",
                "printix_tenant_id": printix_tenant_id, "tenant_name": tenant_name, **tc,
            })

        request.session["reg_tenant_id"]           = printix_tenant_id.strip()
        request.session["reg_tenant_name"]          = tenant_name.strip() or printix_tenant_id.strip()
        request.session["reg_print_client_id"]      = print_client_id.strip()
        request.session["reg_print_client_secret"]  = print_client_secret.strip()
        request.session["reg_card_client_id"]       = card_client_id.strip()
        request.session["reg_card_client_secret"]   = card_client_secret.strip()
        request.session["reg_ws_client_id"]         = ws_client_id.strip()
        request.session["reg_ws_client_secret"]     = ws_client_secret.strip()
        request.session["reg_um_client_id"]         = um_client_id.strip()
        request.session["reg_um_client_secret"]     = um_client_secret.strip()
        request.session["reg_shared_client_id"]     = shared_client_id.strip()
        request.session["reg_shared_client_secret"] = shared_client_secret.strip()
        return RedirectResponse("/register/optional", status_code=302)

    @app.get("/register/optional", response_class=HTMLResponse)
    async def register_step3_get(request: Request):
        if "reg_tenant_id" not in request.session:
            return RedirectResponse("/register", status_code=302)
        return templates.TemplateResponse("register_step3.html", {
            "request": request, "step": 3, "error": None, **t_ctx(request)
        })

    @app.post("/register/optional", response_class=HTMLResponse)
    async def register_step3_post(
        request: Request,
        sql_server:   str = Form(default=""),
        sql_database: str = Form(default="printix_bi_data_2_1"),
        sql_username: str = Form(default=""),
        sql_password: str = Form(default=""),
        mail_api_key: str = Form(default=""),
        mail_from:    str = Form(default=""),
    ):
        if "reg_tenant_id" not in request.session:
            return RedirectResponse("/register", status_code=302)

        request.session["reg_sql_server"]   = sql_server.strip()
        request.session["reg_sql_database"] = sql_database.strip()
        request.session["reg_sql_username"] = sql_username.strip()
        request.session["reg_sql_password"] = sql_password.strip()
        request.session["reg_mail_api_key"] = mail_api_key.strip()
        request.session["reg_mail_from"]    = mail_from.strip()
        return RedirectResponse("/register/summary", status_code=302)

    @app.get("/register/summary", response_class=HTMLResponse)
    async def register_step4_get(request: Request):
        if "reg_tenant_id" not in request.session:
            return RedirectResponse("/register", status_code=302)

        base = mcp_base_url_or(request)
        return templates.TemplateResponse("register_step4.html", {
            "request": request, "step": 4,
            "username":       request.session.get("reg_username", ""),
            "email":          request.session.get("reg_email", ""),
            "tenant_id":      request.session.get("reg_tenant_id", ""),
            "tenant_name":    request.session.get("reg_tenant_name", ""),
            "sql_configured": bool(request.session.get("reg_sql_server")),
            "mail_configured":bool(request.session.get("reg_mail_api_key")),
            "base_url": base,
            "error": None, **t_ctx(request),
        })

    @app.post("/register/summary", response_class=HTMLResponse)
    async def register_step4_post(request: Request):
        if "reg_tenant_id" not in request.session:
            return RedirectResponse("/register", status_code=302)

        base = mcp_base_url_or(request)
        tc   = t_ctx(request)

        try:
            from db import create_user, create_tenant, has_users, audit

            is_first = not has_users()

            user = create_user(
                username=request.session["reg_username"],
                password=request.session["reg_password"],
                email=request.session.get("reg_email", ""),
                is_first=is_first,
                full_name=request.session.get("reg_full_name", ""),
                company=request.session.get("reg_company", ""),
            )

            tenant = create_tenant(
                user_id=user["id"],
                printix_tenant_id=request.session["reg_tenant_id"],
                name=request.session.get("reg_tenant_name", ""),
                print_client_id=request.session.get("reg_print_client_id", ""),
                print_client_secret=request.session.get("reg_print_client_secret", ""),
                card_client_id=request.session.get("reg_card_client_id", ""),
                card_client_secret=request.session.get("reg_card_client_secret", ""),
                ws_client_id=request.session.get("reg_ws_client_id", ""),
                ws_client_secret=request.session.get("reg_ws_client_secret", ""),
                um_client_id=request.session.get("reg_um_client_id", ""),
                um_client_secret=request.session.get("reg_um_client_secret", ""),
                shared_client_id=request.session.get("reg_shared_client_id", ""),
                shared_client_secret=request.session.get("reg_shared_client_secret", ""),
                sql_server=request.session.get("reg_sql_server", ""),
                sql_database=request.session.get("reg_sql_database", ""),
                sql_username=request.session.get("reg_sql_username", ""),
                sql_password=request.session.get("reg_sql_password", ""),
                mail_api_key=request.session.get("reg_mail_api_key", ""),
                mail_from=request.session.get("reg_mail_from", ""),
            )

            audit(user["id"], "register", f"Tenant '{tenant['name']}' registriert")

            # v7.2.12: Admin-Benachrichtigung wenn ein neuer User auf Pending
            # landet. Erster User (is_first=True) wird auto-Admin → keine Mail.
            # Alle anderen → status="pending", die Admins die `user_registered`
            # in ihren `notify_events` aktiviert haben werden via Resend
            # benachrichtigt. Helper-Code (`notify_helper.send_event_notification`)
            # + HTML-Template (`html_user_registered`) waren schon da, der
            # Aufruf-Trigger fehlte aber im Registrierungs-Flow.
            if not is_first:
                try:
                    _notify_admins_of_user_registered(user)
                except Exception as e:
                    # nicht blockieren — Registrierung war erfolgreich,
                    # Mail ist best-effort
                    logger.warning("Admin-Notification fuer 'user_registered' "
                                    "fehlgeschlagen: %s", e)

            for key in list(request.session.keys()):
                if key.startswith("reg_"):
                    del request.session[key]

        except Exception as e:
            logger.error("Registrierung fehlgeschlagen: %s", e)
            return templates.TemplateResponse("register_step4.html", {
                "request": request, "step": 4, "error": str(e),
                "username":    request.session.get("reg_username", ""),
                "tenant_id":   request.session.get("reg_tenant_id", ""),
                "tenant_name": request.session.get("reg_tenant_name", ""),
                "base_url": base, "sql_configured": False, "mail_configured": False, **tc,
            })

        # v0.3.2: register_success.html no longer shows MCP/OAuth/SSE
        # credentials — the MCP server was dropped in v0.1.0, so those
        # URLs (and the corresponding tenant bearer/oauth secrets) are
        # meaningless to the admin. Page now shows a clean next-steps
        # checklist with deep-links into /admin/settings sub-sections.
        return templates.TemplateResponse("register_success.html", {
            "request": request,
            "username":  user["username"],
            "is_admin":  user.get("is_admin", False),
            "base_url":  base,
            **tc,
        })

    # ── Login ──────────────────────────────────────────────────────────────────

    def _entra_login_enabled() -> bool:
        """Prüft ob Entra-Login für die Login-Seite angezeigt werden soll."""
        try:
            from entra import is_enabled
            return is_enabled()
        except Exception:
            return False

    @app.get("/login", response_class=HTMLResponse)
    async def login_get(request: Request):
        if get_session_user(request):
            return RedirectResponse("/", status_code=302)
        # v0.7.34/37: post-merge info wenn der User seinen Session-Account
        # gerade in einen anderen gemerged hat. `_` ist nur ein Template-
        # Kontext-Var — hier verwenden wir direkt den TRANSLATIONS-Lookup.
        info = None
        if request.query_params.get("merged") == "1":
            from web.i18n import TRANSLATIONS as _TR
            _lang = (request.session.get("lang")
                       or request.query_params.get("lang")
                       or "de")
            info = ((_TR.get(_lang) or _TR.get("en") or {})
                     .get("login_after_merge_info", ""))
        return templates.TemplateResponse("login.html", {
            "request": request, "error": None, "info": info,
            "entra_enabled": _entra_login_enabled(),
            **t_ctx(request),
        })

    @app.post("/login", response_class=HTMLResponse)
    async def login_post(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
    ):
        tc = t_ctx(request)
        _  = tc["_"]
        entra_on = _entra_login_enabled()
        try:
            from db import authenticate_user, audit
            user = authenticate_user(username, password)
        except Exception as e:
            return templates.TemplateResponse("login.html", {
                "request": request, "error": _("err_database") + f": {e}",
                "username": username, "entra_enabled": entra_on, **tc,
            })

        if not user:
            return templates.TemplateResponse("login.html", {
                "request": request, "error": _("login_error"),
                "username": username, "entra_enabled": entra_on, **tc,
            })

        status = user.get("status", "")
        if status == "disabled" or status == "suspended":
            return templates.TemplateResponse("login.html", {
                "request": request, "error": _("login_suspended"),
                "username": username, "entra_enabled": entra_on, **tc,
            })

        # v7.2.41: Pro-Feature-Gate auf Login-Ebene.
        # Free-Tier ist Admin-only WebUI. Nicht-Admin-User existieren nur als
        # MCP-Permission-Subjects, kein Web-UI-Zugriff. Pro-Feature
        # `print_job_mgmt` schaltet das Employee-Portal /my frei.
        if not user.get("is_admin"):
            try:
                import sys as _ls
                _ls.path.insert(0, "/app")
                from license import is_feature_enabled
                if not is_feature_enabled("print_job_mgmt"):
                    return templates.TemplateResponse("login.html", {
                        "request": request,
                        "error": _("login_employee_locked"),
                        "username": username, "entra_enabled": entra_on, **tc,
                    })
            except Exception as _le:
                logger.warning("login pro-gate check failed: %s", _le)
                # bei Fehler: durchlassen, lieber als Lockout

        # v0.7.29: Session-Fixation-Schutz — vor dem Schreiben der
        # authentifizierten user_id pre-auth Cookie-Inhalt verwerfen.
        # Sprach-Praeferenz bleibt erhalten.
        _sess_lang = request.session.get("lang")
        request.session.clear()
        if _sess_lang:
            request.session["lang"] = _sess_lang
        request.session["user_id"] = user["id"]
        try:
            audit(user["id"], "login", "Eingeloggt")
        except Exception:
            pass
        try:
            from db import record_user_login
            record_user_login(user["id"], "password")
        except Exception:
            pass

        # v6.2.0: Background-Prefetch — Tenant-Daten werden parallel
        # geladen, damit die ersten Seiten nach dem Login sofort da sind.
        # v7.6.0: Tenant für den Periodic Refresher registrieren — der
        # frischt Topics auf bevor sie ablaufen, sodass nach dem ersten
        # Login NIE wieder ein Cache-Miss-Hänger auftritt.
        try:
            from db import get_tenant_full_by_user_id as _gt
            t = _gt(user["id"])
            if t:
                from cache import schedule_prefetch, register_tenant_for_refresh
                schedule_prefetch(t, lambda tt=t: _make_printix_client(tt))
                register_tenant_for_refresh(
                    t, lambda tt=t: (tt, _make_printix_client(tt))
                )
        except Exception as _pe:
            logger.debug("Login-Prefetch skip: %s", _pe)

        if user.get("must_change_password"):
            return RedirectResponse("/account/activate", status_code=302)
        return RedirectResponse(_user_home_target(user), status_code=302)

    @app.get("/logout", response_class=RedirectResponse)
    async def logout(request: Request):
        lang = request.session.get("lang")
        # v6.1.0: Beim Logout den Tenant-Cache dieses Users invalidieren,
        # damit beim nächsten Login frische Daten geladen werden.
        try:
            user_id = request.session.get("user_id")
            if user_id:
                from db import get_tenant_by_user_id as _gt
                t = _gt(user_id)
                if t:
                    import sys as _s, os as _o
                    _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
                    from cache import tenant_cache as _tc
                    _tc.clear_tenant(t.get("id", ""))
        except Exception:
            pass
        request.session.clear()
        if lang:
            request.session["lang"] = lang
        return RedirectResponse("/login", status_code=302)

    # ── Entra ID (Azure AD) SSO ────────────────────────────────────────────────

    @app.get("/auth/entra/login")
    async def entra_login(request: Request):
        """Leitet den Benutzer zur Microsoft-Anmeldeseite weiter."""
        try:
            from entra import is_enabled, build_authorize_url, generate_state
        except ImportError:
            return RedirectResponse("/login", status_code=302)

        if not is_enabled():
            return RedirectResponse("/login", status_code=302)

        state = generate_state()
        request.session["entra_state"] = state
        # Gespeicherte Redirect URI verwenden (konsistent mit App-Registrierung)
        try:
            from db import get_setting
            saved_uri = get_setting("entra_redirect_uri", "")
        except Exception:
            saved_uri = ""
        if not saved_uri:
            base = _get_base_url(request)
            saved_uri = f"{base}/auth/entra/callback"
        redirect_uri = saved_uri
        url = build_authorize_url(redirect_uri, state)
        return RedirectResponse(url, status_code=302)

    @app.get("/auth/entra/callback")
    async def entra_callback(request: Request):
        """Callback von Microsoft nach erfolgreicher Anmeldung."""
        tc = t_ctx(request)
        _ = tc["_"]
        _e = {"entra_enabled": True}  # Entra ist aktiv (wir sind im Callback)

        code = request.query_params.get("code", "")
        state = request.query_params.get("state", "")
        error = request.query_params.get("error", "")
        error_desc = request.query_params.get("error_description", "")

        if error:
            logger.warning("Entra callback error: %s — %s", error, error_desc)
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": _("entra_err_signin_failed") + f": {error_desc or error}",
                **_e, **tc,
            })

        # CSRF-State prüfen
        expected_state = request.session.pop("entra_state", "")
        if not state or state != expected_state:
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": _("entra_err_invalid_state"),
                **_e, **tc,
            })

        if not code:
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": _("entra_err_no_code"),
                **_e, **tc,
            })

        # Code gegen Token tauschen
        try:
            from entra import exchange_code_for_user
        except ImportError:
            return templates.TemplateResponse("login.html", {
                "request": request, "error": _("entra_err_module_unavailable"),
                **_e, **tc,
            })

        # Gespeicherte Redirect URI verwenden (muss mit Login-Request übereinstimmen)
        try:
            from db import get_setting as _gs
            saved_uri = _gs("entra_redirect_uri", "")
        except Exception:
            saved_uri = ""
        if not saved_uri:
            base = _get_base_url(request)
            saved_uri = f"{base}/auth/entra/callback"
        redirect_uri = saved_uri
        user_info = exchange_code_for_user(code, redirect_uri)

        if not user_info or not user_info.get("oid"):
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": _("entra_err_no_profile"),
                **_e, **tc,
            })

        # User finden oder erstellen
        try:
            from db import get_or_create_entra_user, audit
            user = get_or_create_entra_user(
                entra_oid=user_info["oid"],
                email=user_info.get("email", ""),
                display_name=user_info.get("name", ""),
                entra_tid=user_info.get("tid", ""),
            )
        except Exception as e:
            logger.error("Entra user lookup/create Fehler: %s", e)
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": _("err_database") + f": {e}",
                **_e, **tc,
            })

        if not user:
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": _("entra_err_user_not_created"),
                **_e, **tc,
            })

        # Status prüfen
        status = user.get("status", "")
        if status in ("disabled", "suspended"):
            return templates.TemplateResponse("login.html", {
                "request": request,
                "error": _("login_suspended"),
                **_e, **tc,
            })

        # Session setzen — mit Session-Fixation-Schutz (v0.7.29).
        _sess_lang = request.session.get("lang")
        request.session.clear()
        if _sess_lang:
            request.session["lang"] = _sess_lang
        request.session["user_id"] = user["id"]
        try:
            audit(user["id"], "login", f"Entra-Login ({user_info.get('email', '')})")
        except Exception:
            pass
        try:
            from db import record_user_login
            record_user_login(user["id"], "entra")
        except Exception:
            pass

        # v0.7.6: Wenn der User bereits gelinkt ist (printix_user_id gesetzt),
        # aber username/full_name nicht mit Printix matched — einmaliger
        # Backfill bei diesem Login. Behebt z.B. Bootstrap-Admins die mit
        # username='Marcus' angelegt wurden bevor printix_user_id verknuepft
        # war. Idempotent: laeuft jeden Login, macht aber nur was wenn was
        # zu tun ist.
        try:
            _pxid_existing = (user.get("printix_user_id") or "").strip()
            if _pxid_existing:
                from db import _conn as _bconn, username_exists, update_user as _bupd
                with _bconn() as _bc:
                    _px_row = _bc.execute(
                        "SELECT username, full_name FROM cached_printix_users "
                        "WHERE printix_user_id = ?", (_pxid_existing,),
                    ).fetchone()
                if _px_row:
                    _px_uname = (_px_row["username"] or "").strip()
                    _px_full = (_px_row["full_name"] or "").strip()
                    _cu = (user.get("username") or "").strip()
                    _cf = (user.get("full_name") or "").strip()
                    _ce = (user.get("email") or "").strip()
                    _bkw = {}
                    # v0.7.8: Email lowercase damit Printix-Submit matched
                    # (Entra liefert 'Marcus@x.de', Printix kennt
                    # 'marcus@x.de').
                    if _ce and _ce != _ce.lower():
                        _bkw["email"] = _ce.lower()
                    if (_px_uname and _px_uname != _cu
                            and not username_exists(_px_uname)):
                        _bkw["username"] = _px_uname
                    if _px_full and _px_full != _cf:
                        _bkw["full_name"] = _px_full
                    if _bkw:
                        _bupd(user["id"], **_bkw)
                        try:
                            audit(user["id"], "entra_printix_backfill",
                                  f"px_id={_pxid_existing} updates={_bkw}")
                        except Exception:
                            pass
                        if "username" in _bkw:
                            user["username"] = _px_uname
                        if "full_name" in _bkw:
                            user["full_name"] = _px_full
                        if "email" in _bkw:
                            user["email"] = _bkw["email"]
        except Exception as _bf:
            logger.debug("Entra->Printix backfill skip: %s", _bf)

        # v0.6.6: Auto-Link Entra-User zu printix_user_id ueber Email-Match
        # in cached_printix_users. So muss der Admin den Link nicht manuell
        # ueber Printix-User-Auswahl setzen.
        try:
            if not (user.get("printix_user_id") or "").strip():
                _entra_email = (user_info.get("email") or user.get("email") or "").strip()
                if _entra_email:
                    from cloudprint.printix_cache_db import find_printix_user_by_identity
                    _match = find_printix_user_by_identity(_entra_email)
                    if _match and _match.get("printix_user_id"):
                        # v0.7.6: zusaetzlich zum printix_user_id-Link auch
                        # users.username + users.full_name an die Printix-
                        # Werte angleichen, FALLS der lokale Wert ein
                        # Display-Name-Fragment war (z.B. 'Marcus' statt
                        # 'marcus.nimtz'). Damit zeigen Logs + UI ueberall
                        # konsistent den selben Identifier wie Printix.
                        _new_username = (_match.get("username") or "").strip()
                        _new_full = (_match.get("full_name") or "").strip()
                        _current_username = (user.get("username") or "").strip()
                        _current_full = (user.get("full_name") or "").strip()
                        _update_kwargs = {
                            "printix_user_id": _match["printix_user_id"],
                        }
                        # Nur ueberschreiben wenn (a) Printix einen Wert
                        # hat UND (b) der lokale Wert sich davon unter-
                        # scheidet. Username-Kollision: wenn schon vergeben,
                        # nicht setzen — sonst fliegt der UNIQUE-constraint.
                        if _new_username and _new_username != _current_username:
                            try:
                                from db import username_exists
                                if not username_exists(_new_username):
                                    _update_kwargs["username"] = _new_username
                            except Exception:
                                pass
                        if _new_full and _new_full != _current_full:
                            _update_kwargs["full_name"] = _new_full
                        from db import update_user as _upd
                        _upd(user["id"], **_update_kwargs)
                        try:
                            _details = (
                                f"auto-link via email={_entra_email} "
                                f"-> printix_user_id={_match['printix_user_id']}"
                            )
                            if "username" in _update_kwargs:
                                _details += (
                                    f"; username '{_current_username}' "
                                    f"-> '{_new_username}'"
                                )
                            if "full_name" in _update_kwargs:
                                _details += (
                                    f"; full_name '{_current_full}' "
                                    f"-> '{_new_full}'"
                                )
                            audit(
                                user["id"],
                                "entra_printix_linked",
                                _details,
                            )
                        except Exception:
                            pass
                        # User-Dict aktualisieren fuer downstream-Verwendung
                        user["printix_user_id"] = _match["printix_user_id"]
                        if "username" in _update_kwargs:
                            user["username"] = _new_username
                        if "full_name" in _update_kwargs:
                            user["full_name"] = _new_full
        except Exception as _link_err:
            logger.debug("Entra->Printix auto-link skip: %s", _link_err)

        # v6.2.0: Background-Prefetch auch beim Entra-SSO-Login
        # v7.6.0: + Periodic-Refresher-Registrierung
        try:
            from db import get_tenant_full_by_user_id as _gt
            t = _gt(user["id"])
            if t:
                from cache import schedule_prefetch, register_tenant_for_refresh
                schedule_prefetch(t, lambda tt=t: _make_printix_client(tt))
                register_tenant_for_refresh(
                    t, lambda tt=t: (tt, _make_printix_client(tt))
                )
        except Exception as _pe:
            logger.debug("Entra-Login-Prefetch skip: %s", _pe)

        if user.get("must_change_password"):
            return RedirectResponse("/account/activate", status_code=302)
        return RedirectResponse(_user_home_target(user), status_code=302)

    @app.get("/account/activate", response_class=HTMLResponse)
    async def account_activate_get(request: Request):
        user = get_session_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        if not user.get("must_change_password"):
            return RedirectResponse(_user_home_target(user), status_code=302)
        return templates.TemplateResponse("account_activate.html", {
            "request": request,
            "user": user,
            "saved": False,
            "error": None,
            **t_ctx(request),
        })

    @app.post("/account/activate", response_class=HTMLResponse)
    async def account_activate_post(
        request: Request,
        new_password: str = Form(...),
        new_password2: str = Form(...),
    ):
        user = get_session_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        if not user.get("must_change_password"):
            return RedirectResponse(_user_home_target(user), status_code=302)
        tc = t_ctx(request)
        _ = tc["_"]
        error = None
        if new_password != new_password2:
            error = _("reg_pw_mismatch")
        elif len(new_password) < 8:
            error = _("invite_pw_length_error")
        else:
            try:
                from db import complete_invitation_password_change, audit
                complete_invitation_password_change(user["id"], new_password)
                audit(user["id"], "accept_invitation", "Einladung angenommen und Passwort gesetzt", object_type="user", object_id=user["id"])
            except Exception as e:
                error = str(e)
        if error:
            return templates.TemplateResponse("account_activate.html", {
                "request": request,
                "user": user,
                "saved": False,
                "error": error,
                **tc,
            })
        refreshed = get_session_user(request)
        target = _user_home_target(refreshed or user)
        return templates.TemplateResponse("account_activate.html", {
            "request": request,
            "user": refreshed or user,
            "saved": True,
            "error": None,
            "redirect_target": target,
            **tc,
        })

    # ── Entra Auto-Setup (Ein-Klick via Bootstrap-App) ─────────────────────
    #
    # ─── Device Code Flow: Admin klickt Button → Code anzeigen → automatische
    # App-Registration via Graph API. Keine Bootstrap-App nötig.

    @app.post("/admin/entra/device-code")
    async def entra_device_code_start(request: Request):
        """Startet den Device Code Flow fuer Entra Auto-Setup."""
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        try:
            from entra import start_device_code_flow
        except ImportError:
            return JSONResponse({"error": "entra module not available"}, status_code=500)

        # v0.7.0: Optional Mail.Send / Mail.Read App-Permissions in der
        # zu erstellenden App mitregistrieren. Wir merken uns die Wahl
        # in der Session bis der Poll-Endpoint die App tatsaechlich
        # anlegt — die Form-Felder kommen als Query-Parameter mit dem
        # Start-Klick.
        try:
            qp = dict(request.query_params)
        except Exception:
            qp = {}
        request.session["entra_setup_include_mail_send"] = (
            qp.get("include_mail_send", "") in ("1", "true", "on", "yes")
        )
        request.session["entra_setup_include_mail_read"] = (
            qp.get("include_mail_read", "") in ("1", "true", "on", "yes")
        )

        result = start_device_code_flow()
        # v0.4.8: Microsoft-Fehler weiterreichen falls vorhanden, damit der
        # Admin im UI was Verwertbares sieht statt nur „device_code_failed".
        if not result or not result.get("device_code"):
            ms_error = (result or {}).get("error", "device_code_failed")
            return JSONResponse(
                {"error": ms_error,
                 "hint": "Mögliche Ursachen: Tenant-Policy blockt Device-Code-Flow "
                         "(Azure → Authentication Methods Policy), Netzwerk "
                         "blockt login.microsoftonline.com, oder Microsoft "
                         "ist gerade nicht erreichbar."},
                status_code=502,
            )

        # v0.5.5: Device-Code in DB statt nur Session speichern.
        # Vorher: starlette session-cookie verlor das device_code auf
        # Azure App Service zwischen Start- und Poll-Request → User sah
        # „no_device_code". DB-Persistierung pro user_id ist robuster
        # (kein Cookie-Roundtrip noetig). Session bleibt als Backup.
        request.session["entra_device_code"] = result["device_code"]
        request.session["entra_device_interval"] = result.get("interval", 5)
        try:
            import json as _j_dc
            from datetime import datetime as _dt_dc, timezone as _tz_dc
            from db import set_setting as _ss_dc
            _payload_dc = _j_dc.dumps({
                "device_code": result["device_code"],
                "interval":    result.get("interval", 5),
                "created_at":  _dt_dc.now(_tz_dc.utc).isoformat(),
            })
            _ss_dc(f"entra_dc_pending_{user['id']}", _payload_dc)
        except Exception as _dc_e:
            logger.warning("Entra device-code DB persist failed: %s", _dc_e)
        logger.info(
            "Entra device-code START OK: user=%s dc_tail=...%s user_code=%s",
            user.get("username"), result["device_code"][-8:],
            result.get("user_code", ""),
        )

        return JSONResponse({
            "user_code":        result["user_code"],
            "verification_uri": result["verification_uri"],
            "expires_in":       result["expires_in"],
            "interval":         result.get("interval", 5),
            "message":          result.get("message", ""),
        })

    @app.get("/admin/entra/device-poll")
    async def entra_device_code_poll(request: Request):
        """Pollt den Token-Status des laufenden Device Code Flows."""
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        # v0.5.5: DB-Persistenz zuerst probieren (robuster auf Azure),
        # Session als Fallback.
        device_code = ""
        try:
            import json as _j_dcp
            from db import get_setting as _gs_dcp
            _raw = _gs_dcp(f"entra_dc_pending_{user['id']}", "")
            if _raw:
                _d = _j_dcp.loads(_raw)
                device_code = _d.get("device_code", "")
        except Exception:
            pass
        if not device_code:
            device_code = request.session.get("entra_device_code", "")
        if not device_code:
            logger.warning(
                "Entra device-code POLL: kein device_code in DB+Session — "
                "user=%s",
                user.get("username"),
            )
            return JSONResponse({"status": "error", "error": "no_device_code",
                                  "hint": "Bitte „Auto-Setup starten“ erneut klicken — der Flow ist abgelaufen oder verloren gegangen."})

        try:
            from entra import poll_device_code_token, auto_register_app
        except ImportError:
            return JSONResponse({"status": "error", "error": "entra module not available"})

        poll_result = poll_device_code_token(device_code)

        def _clear_dc_state():
            request.session.pop("entra_device_code", None)
            try:
                from db import set_setting as _ss_cl
                _ss_cl(f"entra_dc_pending_{user['id']}", "")
            except Exception:
                pass

        if poll_result["status"] == "pending":
            return JSONResponse({"status": "pending"})

        if poll_result["status"] == "expired":
            _clear_dc_state()
            return JSONResponse({"status": "expired"})

        if poll_result["status"] == "error":
            _clear_dc_state()
            return JSONResponse({"status": "error", "error": poll_result.get("error", "")})

        # status == "success" — Token erhalten, App erstellen
        access_token = poll_result["access_token"]
        _clear_dc_state()

        base = _get_base_url(request)
        sso_redirect_uri = f"{base}/auth/entra/callback"
        # v0.7.0: Optional Mail.Send / Mail.Read App-Permissions in der
        # neuen App registrieren (vom Setup-Toggle im UI gesteuert).
        _inc_send = bool(request.session.pop(
            "entra_setup_include_mail_send", False))
        _inc_read = bool(request.session.pop(
            "entra_setup_include_mail_read", False))
        result = auto_register_app(
            access_token, sso_redirect_uri,
            include_mail_send=_inc_send,
            include_mail_read=_inc_read,
        )

        if not result or not result.get("client_id"):
            return JSONResponse({
                "status": "error",
                "error": "app_creation_failed",
            })

        # Credentials in Settings speichern
        try:
            from db import set_setting, _enc, audit, get_setting
            set_setting("entra_enabled", "1")
            set_setting("entra_client_id", result["client_id"])
            if result.get("client_secret"):
                set_setting("entra_client_secret", _enc(result["client_secret"]))
            if result.get("tenant_id"):
                set_setting("entra_tenant_id", result["tenant_id"])
            set_setting("entra_auto_approve", "0")

            set_setting("entra_redirect_uri", sso_redirect_uri)
            # v0.1.3: Audience + Secret-Ablauf + Object-Id speichern, damit
            # das Admin-UI ein Warn-Banner anzeigen und das Secret rotieren
            # kann, ohne erneut durch den Device-Code-Flow zu laufen.
            if result.get("audience"):
                set_setting("entra_app_audience", result["audience"])
            if result.get("secret_expires_at"):
                set_setting("entra_secret_expires_at",
                            result["secret_expires_at"])
            if result.get("object_id"):
                set_setting("entra_app_object_id", result["object_id"])
            # v0.7.0: Mail-Permissions-Flags persistieren, damit das
            # Admin-UI weiss ob die App ueberhaupt Graph-Mail darf.
            set_setting("entra_mail_send_enabled",
                        "1" if _inc_send else "0")
            set_setting("entra_mail_read_enabled",
                        "1" if _inc_read else "0")
            # E-Mail-to-Print bleibt v0.7.0 noch deaktiviert — Feature
            # kommt erst in v0.8.0; wir registrieren nur die Permission.
            if _inc_read and not get_setting("email_to_print_enabled", ""):
                set_setting("email_to_print_enabled", "0")

            audit(user["id"], "entra_auto_setup",
                  f"SSO-App via Device Code Flow erstellt (client_id={result['client_id']}, redirect_uri={sso_redirect_uri}, audience={result.get('audience','?')}, secret_expires={result.get('secret_expires_at','?')})")
            logger.info("Entra Auto-Setup erfolgreich: client_id=%s, redirect_uri=%s, audience=%s, secret_expires=%s",
                        result["client_id"], sso_redirect_uri,
                        result.get("audience", "?"),
                        result.get("secret_expires_at", "?"))
        except Exception as e:
            logger.error("Entra Auto-Setup DB-Fehler: %s", e)
            return JSONResponse({
                "status": "error",
                "error": f"App erstellt, aber Speichern fehlgeschlagen: {e}",
            })

        return JSONResponse({
            "status": "success",
            "client_id": result["client_id"],
            "tenant_id": result.get("tenant_id", ""),
        })

    def _get_base_url(request: Request) -> str:
        """Ermittelt die Base-URL der Web-UI aus dem eingehenden Request.

        v0.7.32: **Bevorzugt** das DB-Setting `public_url` wenn gesetzt —
        ein Angreifer, der einen `X-Forwarded-Host: evil.com` Header
        einschleust, konnte sonst die Entra-Redirect-URI vergiften und
        den Auth-Code klauen. Der proxy-header-Pfad bleibt als Fallback
        fuer Reverse-Proxy-Setups ohne explizite public_url-Config.
        """
        try:
            from db import get_setting as _gs
            configured = (_gs("public_url", "") or "").strip().rstrip("/")
            if configured and configured.startswith(("http://", "https://")):
                return configured
        except Exception:
            pass
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = (
            request.headers.get("x-forwarded-host")
            or request.headers.get("host", "")
        )
        if host:
            # Host-Header enthaelt bereits den externen Port (z.B. "192.168.1.100:8010")
            return f"{scheme}://{host}".rstrip("/")
        # Fallback: aus request.url
        hostname = request.url.hostname
        port = request.url.port
        if port and port not in (80, 443):
            return f"{scheme}://{hostname}:{port}"
        return f"{scheme}://{hostname}"

    # ── Warteseite ────────────────────────────────────────────────────────────

    @app.get("/pending", response_class=HTMLResponse)
    async def pending(request: Request):
        user = get_session_user(request)
        return templates.TemplateResponse("pending.html", {
            "request": request, "user": user, **t_ctx(request)
        })

    # v0.3.2: /my/connect (MCP-Verbindungsanleitung für AI-Assistants) +
    # /help (Alias) wurden entfernt — der MCP-Server existiert nicht mehr
    # in mysecureprint-server. End-User finden ihre iOS-App-Setup-Infos
    # unter /my/mobile-app.
    @app.get("/help", response_class=HTMLResponse)
    @app.get("/my/connect", response_class=HTMLResponse)
    async def help_page_redirect(request: Request):
        user = get_session_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        if user.get("role_type") == "employee":
            return RedirectResponse("/my/mobile-app", status_code=302)
        return RedirectResponse("/admin", status_code=302)

    # ─── SSL & Domain Overview (v7.2.49) ──────────────────────────────────
    # Konsolidiert die drei HTTPS-Strategien (Cloudflare Tunnel, eigenes
    # Cert, Auto-HTTPS sslip.io) auf einer Übersichts-Seite mit Live-
    # Status pro Option. Admin sieht auf einen Blick was aktiv ist und
    # springt von dort in die jeweilige Detail-Konfiguration.

    @app.get("/admin/ssl", response_class=HTMLResponse)
    async def admin_ssl(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)

        # Status pro Option ermitteln
        try:
            from db import get_setting
        except Exception:
            get_setting = lambda *_a, **_kw: ""

        # Cloudflare Tunnel
        tunnel_status = {"active": False, "mode": "off", "url": ""}
        try:
            from tunnel import get_manager as _tm
            st = _tm().status() or {}
            tunnel_status = {
                "active": bool(st.get("running")),
                "mode":   st.get("mode") or "off",
                "url":    st.get("url") or "",
            }
        except Exception as e:
            logger.debug("ssl overview tunnel status: %s", e)

        # TLS-Import: tls_enabled=1 + Cert-Datei vorhanden
        tls_status = {"active": False, "expires": "", "subject": "", "days_remaining": 0}
        try:
            tls_enabled = (get_setting("tls_enabled", "0") or "0").strip() == "1"
            cert_path = "/data/tls/cert.pem"
            if tls_enabled and os.path.isfile(cert_path):
                from cryptography import x509
                from cryptography.hazmat.backends import default_backend
                with open(cert_path, "rb") as fh:
                    cert = x509.load_pem_x509_certificate(fh.read(), default_backend())
                from datetime import datetime, timezone as _tz
                now = datetime.now(_tz.utc)
                expires = cert.not_valid_after_utc
                # Vorsicht: das könnte auch durch Auto-TLS aktiviert sein —
                # für die "TLS-Import"-Tile wollen wir aber nur den Fall
                # zeigen, wo der User MANUELL einen Cert hochgeladen hat.
                # Heuristik: wenn auto_tls_enabled=1, gehört's zu Auto-TLS.
                if (get_setting("auto_tls_enabled", "0") or "0").strip() != "1":
                    tls_status = {
                        "active":         expires > now,
                        "subject":        cert.subject.rfc4514_string(),
                        "expires":        expires.strftime("%Y-%m-%d"),
                        "days_remaining": max(0, (expires - now).days),
                    }
        except Exception as e:
            logger.debug("ssl overview tls status: %s", e)

        # Auto-HTTPS sslip.io
        atls_status = {"active": False, "hostname": "", "expires": "", "days_remaining": 0}
        try:
            atls_enabled = (get_setting("auto_tls_enabled", "0") or "0").strip() == "1"
            if atls_enabled:
                hostname = get_setting("auto_tls_hostname", "") or ""
                cert_path = "/data/tls/cert.pem"
                if os.path.isfile(cert_path):
                    from cryptography import x509
                    from cryptography.hazmat.backends import default_backend
                    with open(cert_path, "rb") as fh:
                        cert = x509.load_pem_x509_certificate(fh.read(), default_backend())
                    from datetime import datetime, timezone as _tz
                    now = datetime.now(_tz.utc)
                    expires = cert.not_valid_after_utc
                    atls_status = {
                        "active":         expires > now,
                        "hostname":       hostname,
                        "expires":        expires.strftime("%Y-%m-%d"),
                        "days_remaining": max(0, (expires - now).days),
                    }
                else:
                    atls_status = {
                        "active": False,
                        "hostname": hostname,
                        "expires": "",
                        "days_remaining": 0,
                    }
        except Exception as e:
            logger.debug("ssl overview auto_tls status: %s", e)

        # Public-URL ist eine der drei aktiv?
        any_active = tunnel_status["active"] or tls_status["active"] or atls_status["active"]
        try:
            public_url = (get_setting("public_url", "") or "").strip()
        except Exception:
            public_url = ""

        return templates.TemplateResponse("admin_ssl.html", {
            "request": request, "user": user,
            "tunnel_status": tunnel_status,
            "tls_status":    tls_status,
            "atls_status":   atls_status,
            "any_active":    any_active,
            "public_url":    public_url,
            **t_ctx(request),
        })

    # ─── SSL Network Diagnostics (v7.2.49) ─────────────────────────────────
    @app.get("/admin/ssl/diagnose", response_class=HTMLResponse)
    async def admin_ssl_diagnose(request: Request):
        """Pre-flight check für die HTTPS-Setup-Entscheidung. Sammelt
        was wir vom Container aus überhaupt sehen können (Public-IP,
        Outbound-Erreichbarkeit relevanter Services, lokale Listener,
        DNS-Resolve), plus generiert Copy-Paste curl-Befehle für die
        externe Port-Verifizierung. Liefert am Ende eine konkrete
        Empfehlung welche der drei HTTPS-Strategien zur Lage passt."""
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)

        import asyncio as _aio_diag
        import socket as _sock
        from urllib.parse import urlparse

        diag: dict = {
            "checks": [],
            "public_ip": "",
            "suggested_hostname": "",
            "open_ports_internal": [],
            "public_url": "",
            "public_url_resolves": None,
            "recommendation": "",
        }

        # v7.2.50: Each check now carries `name` only — labels + explanations
        # come from i18n keys (`ssld_chk_<name>_label` / `_explain`) so the
        # template renders them in the user's language with the project's
        # standard EN-fallback. Inline label_de/label_en removed.

        # 1. Public IP via api.ipify.org
        try:
            import urllib.request as _ur
            with _ur.urlopen("https://api.ipify.org", timeout=5) as resp:
                ip = resp.read().decode("ascii", errors="ignore").strip()
                _sock.inet_aton(ip)
                diag["public_ip"] = ip
                diag["suggested_hostname"] = ip.replace(".", "-") + ".sslip.io"
                diag["checks"].append({"name": "public_ip", "status": "ok", "value": ip})
        except Exception as e:
            diag["checks"].append({"name": "public_ip", "status": "error", "value": str(e)})

        # 2. Outbound — Cloudflare API
        try:
            import urllib.request as _ur
            with _ur.urlopen("https://api.cloudflare.com/client/v4/", timeout=5) as resp:
                code = resp.getcode()
            diag["checks"].append({
                "name": "outbound_cloudflare", "status": "ok", "value": f"HTTP {code}",
            })
        except Exception as e:
            diag["checks"].append({
                "name": "outbound_cloudflare", "status": "warn", "value": str(e)[:80],
            })

        # 3. Outbound — Let's Encrypt ACME directory
        try:
            import urllib.request as _ur
            with _ur.urlopen("https://acme-v02.api.letsencrypt.org/directory", timeout=5) as resp:
                code = resp.getcode()
            diag["checks"].append({
                "name": "outbound_letsencrypt", "status": "ok", "value": f"HTTP {code}",
            })
        except Exception as e:
            diag["checks"].append({
                "name": "outbound_letsencrypt", "status": "warn", "value": str(e)[:80],
            })

        # 4. Lokale Listener — `/proc/net/tcp(6)` (no `ss` binary required;
        #    works even in the slim base image we ship). Each line in the
        #    proc files has `local_address` as `<HEX_IP>:<HEX_PORT>` with
        #    state `0A` meaning LISTEN.
        try:
            ports: set[int] = set()
            for proc_path in ("/proc/net/tcp", "/proc/net/tcp6"):
                try:
                    with open(proc_path, "r") as fh:
                        next(fh, None)  # header
                        for line in fh:
                            parts = line.split()
                            if len(parts) < 4:
                                continue
                            local_addr, state = parts[1], parts[3]
                            if state != "0A":
                                continue
                            if ":" in local_addr:
                                port_hex = local_addr.rsplit(":", 1)[-1]
                                try:
                                    ports.add(int(port_hex, 16))
                                except ValueError:
                                    pass
                except FileNotFoundError:
                    continue
            diag["open_ports_internal"] = sorted(ports)
            diag["checks"].append({
                "name": "internal_listeners", "status": "ok",
                "value": ", ".join(str(p) for p in sorted(ports)) or "—",
            })
        except Exception as e:
            diag["checks"].append({
                "name": "internal_listeners", "status": "warn", "value": str(e)[:80],
            })

        # 5. DNS-Resolve von public_url (wenn gesetzt)
        try:
            from db import get_setting
            pu = (get_setting("public_url", "") or "").strip()
        except Exception:
            pu = ""
        diag["public_url"] = pu
        if pu:
            try:
                host = urlparse(pu).hostname or ""
                if host:
                    addr = _sock.gethostbyname(host)
                    diag["public_url_resolves"] = True
                    diag["checks"].append({
                        "name": "dns_resolve", "status": "ok",
                        "value": f"{host} → {addr}",
                    })
                    if diag["public_ip"] and addr == diag["public_ip"]:
                        diag["checks"].append({
                            "name": "dns_matches_ip", "status": "ok", "value": "matches",
                        })
                    elif diag["public_ip"]:
                        diag["checks"].append({
                            "name": "dns_matches_ip", "status": "warn",
                            "value": f"{addr} ≠ {diag['public_ip']}",
                        })
            except Exception as e:
                diag["public_url_resolves"] = False
                diag["checks"].append({
                    "name": "dns_resolve", "status": "error", "value": str(e)[:80],
                })

        # 6. Empfehlung basierend auf den Befunden
        outbound_ok = any(c["name"] == "outbound_cloudflare" and c["status"] == "ok"
                          for c in diag["checks"])
        ip_ok = bool(diag["public_ip"])
        le_ok = any(c["name"] == "outbound_letsencrypt" and c["status"] == "ok"
                    for c in diag["checks"])

        if outbound_ok and not ip_ok:
            diag["recommendation"] = "tunnel"  # outbound only — perfekt für Tunnel
        elif ip_ok and le_ok:
            diag["recommendation"] = "atls"    # Public-IP + LE erreichbar — Auto-TLS funktioniert
        elif outbound_ok:
            diag["recommendation"] = "tunnel"
        else:
            diag["recommendation"] = "tls"     # nur manueller Cert-Import übrig

        # v7.6.0: Test-Targets dynamisch — bevorzugt Tunnel-URL, dann
        # konfigurierte public_url, dann Fallback Public-IP. Damit
        # testen die Buttons das was der User wirklich nutzt, nicht
        # nur die WAN-IP der Maschine.
        # v7.6.1: tunnel-Singleton heißt get_manager(), nicht _manager.
        tunnel_url = ""
        try:
            from tunnel import get_manager as _get_tm
            _ts = _get_tm().status()
            if _ts.get("running"):
                tunnel_url = (_ts.get("url") or "").strip().rstrip("/")
        except Exception as _te:
            logger.debug("diagnose: tunnel status not available: %s", _te)
        diag["test_base"] = (
            tunnel_url or
            (diag.get("public_url") or "").rstrip("/") or
            (f"http://{diag['public_ip']}" if diag["public_ip"] else "")
        )
        diag["test_base_kind"] = (
            "tunnel" if tunnel_url else
            ("public_url" if diag.get("public_url") else
             ("public_ip" if diag["public_ip"] else "none"))
        )

        return templates.TemplateResponse("admin_ssl_diagnose.html", {
            "request": request, "user": user,
            "diag": diag,
            **t_ctx(request),
        })

    @app.post("/admin/ssl/diagnose/test-port")
    async def admin_ssl_diagnose_test_port(request: Request):
        """v7.2.50/v7.6.0: Server-side curl probe against either a Public-IP
        port (legacy `{ip, port}` payload) or a fully-qualified URL
        (`{url}` payload — used by the Tunnel/Public-URL/Health buttons).
        Returns a structured verdict the UI maps to a localised
        explanation."""
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            body = {}

        import socket as _sock
        from urllib.parse import urlparse

        url = (body.get("url") or "").strip()
        if url:
            # URL-Mode: validate against allowlist (tunnel, public_url, public_ip).
            # Otherwise this admin-gated endpoint becomes an SSRF tool.
            try:
                from db import get_setting
                pu_setting = (get_setting("public_url", "") or "").strip().rstrip("/")
            except Exception:
                pu_setting = ""
            tunnel_url = ""
            try:
                from tunnel import get_manager as _get_tm
                _ts = _get_tm().status()
                if _ts.get("running"):
                    tunnel_url = (_ts.get("url") or "").strip().rstrip("/")
            except Exception:
                pass
            try:
                import urllib.request as _ur
                with _ur.urlopen("https://api.ipify.org", timeout=4) as _r:
                    detected_ip = _r.read().decode("ascii", errors="ignore").strip()
            except Exception:
                detected_ip = ""

            allowed_hosts = set()
            for base in (tunnel_url, pu_setting):
                if base:
                    try:
                        allowed_hosts.add((urlparse(base).hostname or "").lower())
                    except Exception:
                        pass
            if detected_ip:
                allowed_hosts.add(detected_ip)

            try:
                parsed = urlparse(url)
            except Exception:
                return JSONResponse({"error": "bad_url"}, status_code=400)
            host = (parsed.hostname or "").lower()
            scheme = (parsed.scheme or "").lower()
            if scheme not in ("http", "https") or not host:
                return JSONResponse({"error": "bad_url"}, status_code=400)
            if host not in allowed_hosts:
                return JSONResponse({"error": "host_not_allowed",
                                      "host": host}, status_code=400)
            # Use parsed pieces; final URL stays as user-supplied (after validation).
            port = parsed.port or (443 if scheme == "https" else 80)
            ip = host
        else:
            # Legacy IP+port mode (still used by the per-port buttons).
            ip = (body.get("ip") or "").strip()
            try:
                port = int(body.get("port") or 0)
            except (TypeError, ValueError):
                port = 0
            if not ip or port <= 0 or port > 65535:
                return JSONResponse({"error": "bad_request"}, status_code=400)
            try:
                _sock.inet_aton(ip)
            except OSError:
                return JSONResponse({"error": "bad_ip"}, status_code=400)
            scheme = "https" if port == 443 else "http"
            host_with_port = ip if port in (80, 443) else f"{ip}:{port}"
            url = f"{scheme}://{host_with_port}/"

        verdict_kind = "other"
        http_code = None
        raw_short = ""
        try:
            import socket as _sk
            _sk.setdefaulttimeout(5)
            sock = _sk.socket(_sk.AF_INET, _sk.SOCK_STREAM)
            try:
                sock.connect((ip, port))
                sock.close()
                # TCP open — try minimal HTTP probe so we report HTTP code
                try:
                    import urllib.request as _ur
                    req = _ur.Request(url, method="HEAD")
                    with _ur.urlopen(req, timeout=5) as resp:  # nosec — admin-only, IP-locked
                        http_code = resp.getcode()
                        verdict_kind = "open_http"
                        raw_short = f"HTTP {http_code}"
                except Exception as he:
                    msg = str(he)[:120]
                    if "ssl" in msg.lower() or "certificate" in msg.lower():
                        verdict_kind = "open_tls_error"
                        raw_short = msg
                    else:
                        verdict_kind = "open_no_http"
                        raw_short = msg
            except (TimeoutError, _sk.timeout):
                verdict_kind = "timeout"
                raw_short = "Connection timed out"
            except ConnectionRefusedError:
                verdict_kind = "refused"
                raw_short = "Connection refused"
            except OSError as oe:
                verdict_kind = "other"
                raw_short = str(oe)[:120]
            finally:
                try: sock.close()
                except Exception: pass
        except Exception as e:
            verdict_kind = "other"
            raw_short = str(e)[:120]

        return JSONResponse({
            "ok":            verdict_kind == "open_http",
            "verdict":       verdict_kind,
            "http_code":     http_code,
            "raw":           raw_short,
            "url":           url,
        })

    # ─── Auto-HTTPS via sslip.io + Let's Encrypt (v7.2.36) ───────────────
    # 1-Klick HTTPS für Public-IP-only Setups. Kein Cloudflare-Account,
    # keine Domain, keine manuelle Cert-Generierung — komplett kostenlos.

    @app.get("/admin/auto-tls", response_class=HTMLResponse)
    async def admin_auto_tls(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            import sys as _ssys
            if "/app" not in _ssys.path:
                _ssys.path.insert(0, "/app")
            from acme_auto import status as _acme_status, detect_public_ip, hostname_for_ip
        except Exception as e:
            logger.error("auto-tls import: %s", e)
            return RedirectResponse(f"/admin?err={quote_plus(str(e))}", status_code=302)
        st = _acme_status()
        # If not yet configured, try to detect IP for the suggestion box
        suggested_ip = ""
        suggested_host = ""
        if not st.get("hostname"):
            suggested_ip = detect_public_ip()
            suggested_host = hostname_for_ip(suggested_ip)
        flash_ok = (request.query_params.get("ok") or "").strip() or None
        flash_err = (request.query_params.get("err") or "").strip() or None
        return templates.TemplateResponse("admin_auto_tls.html", {
            "request": request, "user": user,
            "st": st,
            "suggested_ip": suggested_ip,
            "suggested_host": suggested_host,
            "flash_ok": flash_ok, "flash_err": flash_err,
            **t_ctx(request),
        })

    @app.post("/admin/auto-tls/request")
    async def admin_auto_tls_request(
        request: Request,
        email: str = Form(...),
    ):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            import sys as _ssys
            if "/app" not in _ssys.path:
                _ssys.path.insert(0, "/app")
            from acme_auto import request_cert
            from db import audit
            import asyncio as _aio_ssl
            # certbot blocks for ~30 s — run in thread to avoid stalling event loop
            result = await _aio_ssl.to_thread(request_cert, email.strip())
            if result.get("ok"):
                audit(user["id"], "auto_tls_acquired",
                      f"Let's Encrypt cert acquired for {result.get('hostname')}",
                      object_type="auto_tls", object_id=result.get("hostname", ""))
                return RedirectResponse(
                    f"/admin/auto-tls?ok={quote_plus('cert_acquired:' + result.get('hostname',''))}",
                    status_code=302)
            err = result.get("error", "unknown error")
            details = result.get("details", "")
            return RedirectResponse(
                f"/admin/auto-tls?err={quote_plus(err + (' — ' + details if details else ''))}",
                status_code=302)
        except Exception as e:
            logger.exception("auto-tls request")
            return RedirectResponse(
                f"/admin/auto-tls?err={quote_plus(str(e))}", status_code=302)

    @app.post("/admin/auto-tls/renew")
    async def admin_auto_tls_renew(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            import sys as _ssys
            if "/app" not in _ssys.path:
                _ssys.path.insert(0, "/app")
            from acme_auto import renew_if_due
            from db import audit
            import asyncio as _aio_ssl
            result = await _aio_ssl.to_thread(renew_if_due, True)  # force=True for manual
            if result.get("ok"):
                audit(user["id"], "auto_tls_renewed", "manual renewal triggered",
                      object_type="auto_tls", object_id="renew")
                return RedirectResponse("/admin/auto-tls?ok=renewed", status_code=302)
            return RedirectResponse(
                f"/admin/auto-tls?err={quote_plus(result.get('error','unknown'))}",
                status_code=302)
        except Exception as e:
            return RedirectResponse(
                f"/admin/auto-tls?err={quote_plus(str(e))}", status_code=302)

    # ─── TLS Certificate Import (v7.2.35) ────────────────────────────────
    # Bring-your-own-certificate als Alternative zu Cloudflare Tunnel:
    # User lädt eigenes Cert + Key hoch, web-UI startet auf HTTPS.
    # Persistiert unter /data/tls/{cert,key}.pem; uvicorn liest sie beim
    # nächsten Start. Container-Restart erforderlich.

    @app.get("/admin/tls", response_class=HTMLResponse)
    async def admin_tls(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        cert_path = "/data/tls/cert.pem"
        key_path  = "/data/tls/key.pem"
        cert_info: dict = {}
        if os.path.isfile(cert_path):
            try:
                from cryptography import x509
                from cryptography.hazmat.backends import default_backend
                with open(cert_path, "rb") as fh:
                    cert = x509.load_pem_x509_certificate(fh.read(), default_backend())
                cert_info = {
                    "subject":     cert.subject.rfc4514_string(),
                    "issuer":      cert.issuer.rfc4514_string(),
                    "not_before":  cert.not_valid_before_utc.strftime("%Y-%m-%d %H:%M UTC"),
                    "not_after":   cert.not_valid_after_utc.strftime("%Y-%m-%d %H:%M UTC"),
                    "san":         [],
                }
                try:
                    ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
                    cert_info["san"] = [str(n.value) for n in ext.value]
                except Exception:
                    pass
                from datetime import datetime, timezone as _tz
                now = datetime.now(_tz.utc)
                expiry = cert.not_valid_after_utc
                cert_info["days_remaining"] = (expiry - now).days
                cert_info["expired"]        = (expiry < now)
            except Exception as e:
                cert_info = {"parse_error": str(e)}
        try:
            from db import get_setting
            tls_enabled = get_setting("tls_enabled", "0") == "1"
        except Exception:
            tls_enabled = False
        flash_ok = (request.query_params.get("ok") or "").strip() or None
        flash_err = (request.query_params.get("err") or "").strip() or None
        return templates.TemplateResponse("admin_tls.html", {
            "request": request, "user": user,
            "cert_info": cert_info,
            "tls_enabled": tls_enabled,
            "flash_ok": flash_ok, "flash_err": flash_err,
            **t_ctx(request),
        })

    @app.post("/admin/tls/save")
    async def admin_tls_save(
        request: Request,
        cert_pem: str = Form(...),
        key_pem: str = Form(...),
    ):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            cert_pem = (cert_pem or "").strip()
            key_pem  = (key_pem or "").strip()

            # PEM-Format Sanity-Check
            if "-----BEGIN CERTIFICATE-----" not in cert_pem:
                return RedirectResponse(
                    f"/admin/tls?err={quote_plus('Cert ist kein PEM (BEGIN CERTIFICATE Header fehlt)')}",
                    status_code=302)
            if not any(h in key_pem for h in (
                "-----BEGIN PRIVATE KEY-----",
                "-----BEGIN RSA PRIVATE KEY-----",
                "-----BEGIN EC PRIVATE KEY-----",
            )):
                return RedirectResponse(
                    f"/admin/tls?err={quote_plus('Key ist kein PEM (BEGIN PRIVATE KEY Header fehlt)')}",
                    status_code=302)

            # Cert + Key parsen — schlechte Inputs jetzt rauswerfen,
            # nicht erst wenn uvicorn beim Restart crashed
            from cryptography import x509
            from cryptography.hazmat.backends import default_backend
            from cryptography.hazmat.primitives import serialization
            try:
                cert_obj = x509.load_pem_x509_certificate(cert_pem.encode(), default_backend())
            except Exception as e:
                return RedirectResponse(
                    f"/admin/tls?err={quote_plus(f'Cert kann nicht geparst werden: {e}')}",
                    status_code=302)
            try:
                key_obj = serialization.load_pem_private_key(
                    key_pem.encode(), password=None, backend=default_backend(),
                )
            except Exception as e:
                return RedirectResponse(
                    f"/admin/tls?err={quote_plus(f'Key kann nicht geparst werden: {e}')}",
                    status_code=302)

            # Cert/Key passen zusammen?
            try:
                cert_pubkey = cert_obj.public_key().public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo,
                )
                key_pubkey = key_obj.public_key().public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo,
                )
                if cert_pubkey != key_pubkey:
                    return RedirectResponse(
                        f"/admin/tls?err={quote_plus('Cert und Key passen nicht zusammen (Public Keys unterschiedlich)')}",
                        status_code=302)
            except Exception:
                pass  # Pairing-Check ist best-effort

            # Persistieren
            tls_dir = "/data/tls"
            os.makedirs(tls_dir, exist_ok=True)
            cert_path = os.path.join(tls_dir, "cert.pem")
            key_path  = os.path.join(tls_dir, "key.pem")
            with open(cert_path, "w", encoding="utf-8") as fh:
                fh.write(cert_pem if cert_pem.endswith("\n") else cert_pem + "\n")
            with open(key_path, "w", encoding="utf-8") as fh:
                fh.write(key_pem if key_pem.endswith("\n") else key_pem + "\n")
            os.chmod(key_path, 0o600)
            os.chmod(cert_path, 0o644)

            from db import set_setting, audit
            set_setting("tls_enabled", "1")
            audit(user["id"], "tls_cert_uploaded",
                  f"TLS cert imported: subject={cert_obj.subject.rfc4514_string()}",
                  object_type="tls_cert", object_id=cert_path)
            return RedirectResponse("/admin/tls?ok=cert_saved", status_code=302)
        except Exception as e:
            logger.exception("admin_tls_save")
            return RedirectResponse(
                f"/admin/tls?err={quote_plus(str(e))}", status_code=302)

    @app.post("/admin/tls/disable")
    async def admin_tls_disable(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            from db import set_setting, audit
            set_setting("tls_enabled", "0")
            audit(user["id"], "tls_cert_disabled",
                  "TLS disabled — falling back to HTTP after restart",
                  object_type="tls_cert", object_id="any")
        except Exception as e:
            logger.warning("tls disable: %s", e)
        return RedirectResponse("/admin/tls?ok=disabled", status_code=302)

    # ─── Cloudflare Tunnel (v7.2.32) ──────────────────────────────────────
    # Ein-Klick HTTPS für Azure/Hetzner/Selbst-Hoster ohne eigene Domain.
    # Quick Tunnel = anonym *.trycloudflare.com, Named Tunnel = eigene
    # Domain + CF-Account-Token.

    @app.get("/admin/tunnel", response_class=HTMLResponse)
    async def admin_tunnel(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            from tunnel import get_manager, SETTING_NAMED_HOST
            from db import get_setting
            mgr = get_manager()
            status = mgr.status()
            saved_host = get_setting(SETTING_NAMED_HOST, "")
        except Exception as e:
            logger.error("admin_tunnel: %s", e)
            status = {"error": str(e)}
            saved_host = ""
        flash_ok = (request.query_params.get("ok") or "").strip() or None
        flash_err = (request.query_params.get("err") or "").strip() or None
        return templates.TemplateResponse("admin_tunnel.html", {
            "request": request, "user": user,
            "status": status,
            "saved_host": saved_host,
            "flash_ok": flash_ok, "flash_err": flash_err,
            **t_ctx(request),
        })

    @app.get("/admin/tunnel/status", response_class=JSONResponse)
    async def admin_tunnel_status(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return JSONResponse({"error": "auth"}, status_code=401)
        try:
            from tunnel import get_manager
            return JSONResponse(get_manager().status())
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/admin/tunnel/start-quick")
    async def admin_tunnel_start_quick(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            from tunnel import get_manager
            from db import audit
            target_port = int(os.environ.get("WEB_PORT", "8080"))
            result = get_manager().start_quick(target_port)
            audit(user["id"], "tunnel_start_quick",
                  f"Cloudflare Quick Tunnel started → {result.get('url') or '(URL pending)'}",
                  object_type="tunnel", object_id="quick")
            if result.get("error"):
                return RedirectResponse(
                    f"/admin/tunnel?err={quote_plus(result['error'])}", status_code=302)
            return RedirectResponse("/admin/tunnel?ok=quick_started", status_code=302)
        except Exception as e:
            logger.error("tunnel start-quick: %s", e)
            return RedirectResponse(
                f"/admin/tunnel?err={quote_plus(str(e))}", status_code=302)

    @app.post("/admin/tunnel/start-named")
    async def admin_tunnel_start_named(
        request: Request,
        token: str = Form(...),
        public_host: str = Form(""),
    ):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            from tunnel import get_manager
            from db import audit
            result = get_manager().start_named(token, public_host)
            audit(user["id"], "tunnel_start_named",
                  f"Cloudflare Named Tunnel started → {public_host or '(no host)'}",
                  object_type="tunnel", object_id="named")
            if result.get("error"):
                return RedirectResponse(
                    f"/admin/tunnel?err={quote_plus(result['error'])}", status_code=302)
            return RedirectResponse("/admin/tunnel?ok=named_started", status_code=302)
        except Exception as e:
            logger.error("tunnel start-named: %s", e)
            return RedirectResponse(
                f"/admin/tunnel?err={quote_plus(str(e))}", status_code=302)

    @app.post("/admin/tunnel/stop")
    async def admin_tunnel_stop(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            from tunnel import get_manager
            from db import audit
            get_manager().stop()
            audit(user["id"], "tunnel_stop", "Cloudflare tunnel stopped",
                  object_type="tunnel", object_id="any")
            return RedirectResponse("/admin/tunnel?ok=stopped", status_code=302)
        except Exception as e:
            return RedirectResponse(
                f"/admin/tunnel?err={quote_plus(str(e))}", status_code=302)

    # ─── Health & Status (v7.2.31) ────────────────────────────────────────
    # Login-free endpoints for uptime monitoring (Docker healthcheck,
    # Cloudflare Tunnel, Pingdom, …). The MCP server has its own /health
    # on port 8765; this is the equivalent for the web UI port 8080.

    @app.get("/health/perf", response_class=JSONResponse)
    async def health_perf(request: Request):
        """v0.7.19: Diagnose-Endpoint — misst pro DB-Operation die Latenz.
        Unauthentifiziert (bewusst), gibt nur Timing-Zahlen zurueck.
        Zeigt sofort ob das Performance-Problem an der DB-Latenz liegt."""
        import time as _ht
        results: dict = {}
        try:
            from db import _conn, DB_PATH
        except Exception as e:
            return JSONResponse({"error": f"db import failed: {e}"}, status_code=500)
        results["db_path"] = DB_PATH
        # 1. Connection-Open
        t = _ht.monotonic()
        try:
            with _conn() as c:
                results["t_conn_open_ms"] = round((_ht.monotonic() - t) * 1000, 1)
                # 2. SELECT 1
                t2 = _ht.monotonic()
                c.execute("SELECT 1").fetchone()
                results["t_select1_ms"] = round((_ht.monotonic() - t2) * 1000, 1)
                # 3. COUNT(*) audit_log
                try:
                    t3 = _ht.monotonic()
                    n = c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
                    results["t_count_audit_ms"] = round((_ht.monotonic() - t3) * 1000, 1)
                    results["audit_rows"] = n
                except Exception as e:
                    results["t_count_audit_err"] = str(e)
                # 4. PRAGMA journal_mode
                try:
                    t4 = _ht.monotonic()
                    jm = c.execute("PRAGMA journal_mode").fetchone()[0]
                    sy = c.execute("PRAGMA synchronous").fetchone()[0]
                    cs = c.execute("PRAGMA cache_size").fetchone()[0]
                    results["t_pragmas_ms"] = round((_ht.monotonic() - t4) * 1000, 1)
                    results["pragma_journal_mode"] = jm
                    results["pragma_synchronous"] = sy
                    results["pragma_cache_size"] = cs
                except Exception as e:
                    results["pragma_err"] = str(e)
            results["t_total_ms"] = round((_ht.monotonic() - t) * 1000, 1)
        except Exception as e:
            results["err"] = f"{type(e).__name__}: {e}"
            results["t_total_ms"] = round((_ht.monotonic() - t) * 1000, 1)
        # Erwartung: alles <50ms bei lokalem Disk; >500ms = SMB-Mount Latenz
        return JSONResponse(results)

    @app.get("/health", response_class=JSONResponse)
    async def health_json(request: Request):
        """Liefert JSON-Status. 200 OK wenn alles erreichbar ist,
        503 Service Unavailable bei kritischen Fehlern. Kein Login.
        """
        import time as _hm_time
        checks: dict = {}
        ok = True

        # DB-Check: simpler SELECT 1 — Schreibrechte werden absichtlich nicht
        # getestet, weil Health-Probes idempotent sein sollen.
        try:
            from db import _conn
            with _conn() as conn:
                row = conn.execute("SELECT 1").fetchone()
            checks["db"] = "ok" if row else "empty"
        except Exception as e:
            checks["db"] = f"error: {e.__class__.__name__}"
            ok = False

        # Tenant-Konfiguration
        try:
            from db import _find_tenant_owner_user_id, get_tenant_by_user_id
            owner_uid = _find_tenant_owner_user_id()
            if not owner_uid:
                checks["tenant"] = "no_owner_admin"
            else:
                t = get_tenant_by_user_id(owner_uid)
                if t and t.get("printix_tenant_id"):
                    checks["tenant"] = "configured"
                elif t:
                    checks["tenant"] = "owner_admin_without_credentials"
                else:
                    checks["tenant"] = "owner_admin_without_tenant_row"
        except Exception as e:
            checks["tenant"] = f"error: {e.__class__.__name__}"

        # RBAC-Modus (informativ)
        checks["rbac_enabled"] = (os.getenv("MCP_RBAC_ENABLED", "0").strip().lower()
                                  in ("1", "true", "yes", "on"))

        body = {
            "status": "ok" if ok else "degraded",
            "service": "printix-mcp-web",
            "version": current_app_version(),
            "checks": checks,
            "timestamp": _hm_time.time(),
        }
        return JSONResponse(body, status_code=200 if ok else 503)

    @app.get("/status", response_class=HTMLResponse)
    async def status_page(request: Request):
        """Hübsche HTML-Status-Seite für Browser. Kein Login — anders als
        /admin/* und /dashboard zeigt sie keine Tenant-Daten, nur Health-
        Indikatoren."""
        import time as _hm_time
        from db import _conn, _find_tenant_owner_user_id, get_tenant_by_user_id
        checks: dict = {}
        try:
            with _conn() as conn:
                conn.execute("SELECT 1").fetchone()
            checks["DB Verbindung"] = ("ok", "SQLite reachable")
        except Exception as e:
            checks["DB Verbindung"] = ("error", str(e))

        try:
            owner_uid = _find_tenant_owner_user_id()
            if owner_uid:
                t = get_tenant_by_user_id(owner_uid)
                if t and t.get("printix_tenant_id"):
                    checks["Printix Tenant"] = ("ok", t.get("name") or t.get("printix_tenant_id"))
                else:
                    checks["Printix Tenant"] = ("warn", "Owner ohne Printix-Credentials")
            else:
                checks["Printix Tenant"] = ("warn", "Kein Owner-Admin gefunden")
        except Exception as e:
            checks["Printix Tenant"] = ("error", str(e))

        rbac = (os.getenv("MCP_RBAC_ENABLED", "0").strip().lower()
                in ("1", "true", "yes", "on"))
        checks["MCP RBAC"] = ("ok" if rbac else "info",
                              "aktiv" if rbac else "inaktiv (Pass-Through)")

        version = current_app_version()
        rows_html = ""
        for label, (state, msg) in checks.items():
            color = {"ok": "#16a34a", "warn": "#f59e0b",
                     "error": "#dc2626", "info": "#3b82f6"}.get(state, "#888")
            icon = {"ok": "✓", "warn": "⚠", "error": "✕", "info": "ℹ"}.get(state, "•")
            rows_html += (
                f'<tr><td style="padding:10px 14px;font-weight:500;color:#003366;">{label}</td>'
                f'<td style="padding:10px 14px;"><span style="color:{color};font-weight:700;">{icon} {state.upper()}</span></td>'
                f'<td style="padding:10px 14px;color:#444;font-size:.92em;">{msg}</td></tr>'
            )

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Printix MCP — Status</title>
<style>
  body {{ font-family: -apple-system, Helvetica, Arial, sans-serif;
         max-width: 720px; margin: 3em auto; padding: 0 1.5em;
         color: #1a1a1a; background: #f7f9fb; }}
  h1 {{ color: #003366; border-bottom: 2px solid #003366;
        padding-bottom: 0.3em; }}
  .card {{ background: #fff; border-radius: 12px;
           box-shadow: 0 2px 12px rgba(0,0,0,0.06); padding: 1.6em;
           margin-bottom: 1.5em; }}
  table {{ width: 100%; border-collapse: collapse; }}
  table tr:not(:last-child) td {{ border-bottom: 1px solid #eee; }}
  .footer {{ font-size: 0.85em; color: #888; text-align: center;
             margin-top: 2em; }}
  a {{ color: #003366; }}
</style></head>
<body>
  <h1>🔌 Printix MCP — Status</h1>
  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1em;">
      <div><strong>Version:</strong> v{version}</div>
      <div style="font-size:.85em;color:#888;">aktualisiert: {_hm_time.strftime('%H:%M:%S', _hm_time.gmtime())} UTC</div>
    </div>
    <table>{rows_html}</table>
  </div>
  <div class="footer">
    JSON-Endpoint: <a href="/health">/health</a> &nbsp;·&nbsp;
    Login-Bereich: <a href="/login">/login</a>
  </div>
</body></html>"""
        return HTMLResponse(html)

    # ─── Public Legal Pages (v7.9.4) ─────────────────────────────────────
    # Privacy policy + Imprint required for App-Store review of the
    # MySecurePrint iOS companion app (Apple Guideline 5.1.1) and the
    # German § 5 TMG / § 18 MStV imprint duty for self-hosted instances.
    # All five routes work without a session.

    _LEGAL_SETTING_KEYS = (
        ("operator_name",            "legal_operator_name"),
        ("operator_address",         "legal_operator_address"),
        ("operator_email",           "legal_operator_email"),
        ("operator_phone",           "legal_operator_phone"),
        ("operator_country",         "legal_operator_country"),
        ("vat_id",                   "legal_operator_vat_id"),
        ("data_protection_officer",  "legal_data_protection_officer"),
        ("hosting_provider",         "legal_hosting_provider"),
        ("supervisory_authority",    "legal_supervisory_authority"),
    )

    def _legal_settings() -> dict:
        """Reads the legal operator block from DB settings. All values
        are plain strings; missing keys become ''."""
        try:
            from db import get_setting
            out = {tmpl_key: (get_setting(db_key, "") or "")
                   for tmpl_key, db_key in _LEGAL_SETTING_KEYS}
        except Exception:
            out = {tmpl_key: "" for tmpl_key, _ in _LEGAL_SETTING_KEYS}
        # Default country = Germany when unset (matches the operator's
        # most likely scenario — the bundled iOS app is German-targeted).
        if not out.get("operator_country"):
            out["operator_country"] = "Germany"
        return out

    def _legal_configured(legal: dict) -> bool:
        return bool(
            (legal.get("operator_name")    or "").strip()
            and (legal.get("operator_address") or "").strip()
            and (legal.get("operator_email")   or "").strip()
        )

    def _legal_last_updated() -> str:
        """Date of last change — file mtime of this app.py serves as a
        sensible auto-tracker (touched on every server release)."""
        try:
            import datetime as _dt
            mtime = os.path.getmtime(__file__)
            return _dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
        except Exception:
            return "2026-06-27"

    def _legal_lang(request: Request) -> str:
        """Resolves the active language for legal pages — explicit ?lang= param
        wins, then session, then Accept-Language. Persists the override in the
        session so the rest of the navigation stays in the chosen language."""
        q = (request.query_params.get("lang") or "").strip().lower()
        if q in SUPPORTED_LANGUAGES:
            request.session["lang"] = q
            return q
        return get_lang(request)

    def _legal_ctx(request: Request) -> dict:
        _legal_lang(request)  # honour ?lang=
        legal = _legal_settings()
        ctx = t_ctx(request)
        ctx.update({
            "request": request,
            "user": get_session_user(request),
            "legal": legal,
            "legal_configured": _legal_configured(legal),
            "legal_last_updated": _legal_last_updated(),
            "is_germany": (legal.get("operator_country") or "").strip().lower()
                          in ("germany", "de", "deutschland"),
        })
        return ctx

    _LEGAL_CACHE_HEADERS = {"Cache-Control": "public, max-age=3600"}

    @app.get("/legal", response_class=HTMLResponse)
    async def legal_index(request: Request):
        return templates.TemplateResponse(
            "legal_index.html", _legal_ctx(request),
            headers=_LEGAL_CACHE_HEADERS,
        )

    @app.get("/privacy", response_class=HTMLResponse)
    async def legal_privacy(request: Request):
        return templates.TemplateResponse(
            "legal_privacy.html", _legal_ctx(request),
            headers=_LEGAL_CACHE_HEADERS,
        )

    @app.get("/datenschutz", response_class=HTMLResponse)
    async def legal_privacy_de(request: Request):
        # German alias — force DE for first-time visitors that haven't
        # picked a language yet.
        if "lang" not in request.session and not request.query_params.get("lang"):
            request.session["lang"] = "de"
        return templates.TemplateResponse(
            "legal_privacy.html", _legal_ctx(request),
            headers=_LEGAL_CACHE_HEADERS,
        )

    @app.get("/imprint", response_class=HTMLResponse)
    async def legal_imprint(request: Request):
        return templates.TemplateResponse(
            "legal_imprint.html", _legal_ctx(request),
            headers=_LEGAL_CACHE_HEADERS,
        )

    @app.get("/impressum", response_class=HTMLResponse)
    async def legal_imprint_de(request: Request):
        if "lang" not in request.session and not request.query_params.get("lang"):
            request.session["lang"] = "de"
        return templates.TemplateResponse(
            "legal_imprint.html", _legal_ctx(request),
            headers=_LEGAL_CACHE_HEADERS,
        )

    @app.get("/manuals/gdpr-compliance.pdf")
    async def download_gdpr_compliance(request: Request):
        """v7.2.25: Download the GDPR Compliance Guide.

        Single English-language PDF that explains the role model, scopes,
        audit posture, and Article-by-Article coverage. Linked from
        /admin/mcp-permissions for procurement and DPO review.
        """
        user = require_login(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        path = os.path.join(
            os.path.dirname(__file__), "assets", "manuals",
            "MCP_GDPR_COMPLIANCE_GUIDE.pdf",
        )
        if not os.path.isfile(path):
            return JSONResponse(
                {"detail": "GDPR Compliance Guide not bundled in this image."},
                status_code=404,
            )
        return FileResponse(
            path,
            filename="Printix_MCP_GDPR_Compliance_Guide.pdf",
            media_type="application/pdf",
        )

    # ── Admin ──────────────────────────────────────────────────────────────────

    @app.get("/admin")
    async def admin_dashboard(request: Request):
        # v0.3.3: /admin redirects to /welcome — the proper admin dashboard
        # with config-status panel, server URL, and iOS-setup QR. The
        # original admin_dashboard.html template was dropped in the slim-
        # down (it showed MCP/SSE/Tunnel info that's irrelevant to
        # mysecureprint-server) and rendering it 500'd. /welcome already
        # is admin-only since v0.3.1, so this redirect is safe.
        user = get_session_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        if not user.get("is_admin"):
            return RedirectResponse(_user_home_target(user), status_code=302)
        return RedirectResponse("/welcome", status_code=302)

    @app.get("/admin/users", response_class=HTMLResponse)
    async def admin_users(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            from db import get_all_users
            users = get_all_users()
            user_map = {u.get("id", ""): u for u in users}
            for entry in users:
                parent_id = (entry.get("parent_user_id") or "").strip()
                if entry.get("is_admin"):
                    entry["relationship_kind"] = "global_admin"
                    entry["relationship_name"] = ""
                    entry["relationship_email"] = ""
                elif entry.get("role_type") == "employee":
                    parent = user_map.get(parent_id, {})
                    entry["relationship_kind"] = "employee_of"
                    entry["relationship_name"] = parent.get("full_name") or parent.get("username") or ""
                    entry["relationship_email"] = parent.get("email") or ""
                else:
                    entry["relationship_kind"] = "tenant_admin"
                    entry["relationship_name"] = entry.get("full_name") or entry.get("username") or ""
                    entry["relationship_email"] = entry.get("email") or ""
        except Exception:
            users = []
        err_msg = (request.query_params.get("err") or "").strip() or None
        return templates.TemplateResponse("admin_users.html", {
            "request": request, "user": user, "users": users,
            "error": err_msg, **t_ctx(request)
        })

    # v0.7.32: Admin-Merge-Tool fuer Duplikat-User (gleiche Email,
    # zwei Records — z.B. lokaler Account + Entra-Auto-Create). GET
    # listet alle Duplikate; POST fuehrt zwei explizit ausgewaehlte
    # User zusammen.
    @app.get("/admin/users/merge", response_class=HTMLResponse)
    async def admin_users_merge_get(request: Request):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        from db import find_duplicate_users_by_email
        duplicates = find_duplicate_users_by_email()
        return templates.TemplateResponse(
            "admin_users_merge.html",
            {"request": request, "user": admin,
              "duplicates": duplicates,
              "success": request.query_params.get("ok"),
              "error": request.query_params.get("err"),
              **t_ctx(request)})

    @app.post("/admin/users/merge", response_class=RedirectResponse)
    async def admin_users_merge_post(
        request: Request,
        source_id: str = Form(...),
        target_id: str = Form(...),
    ):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        # v0.7.34: Wenn der Admin gerade als der Source-Account eingeloggt
        # ist, ist das der typische Duplikat-Consolidation-Fall (z.B. via
        # Entra eingeloggt, will Entra-Duplikat in lokalen Account
        # mergen). Wir erlauben's — machen die Session danach aber sauber
        # kaputt und schicken den Admin auf /login zurueck, damit er als
        # Target-Account weitermacht.
        session_uid = admin.get("id") or admin.get("user_id")
        session_will_break = (session_uid == source_id)

        try:
            from db import merge_users, MergeError
            merge_users(source_id, target_id,
                          initiated_by=admin.get("id"))
        except MergeError as e:
            from urllib.parse import quote_plus
            return RedirectResponse(
                f"/admin/users/merge?err={quote_plus(str(e))}",
                status_code=303)
        except Exception as e:
            logger.error("user_merge failed: %s", e)
            from urllib.parse import quote_plus
            return RedirectResponse(
                f"/admin/users/merge?err={quote_plus('unexpected: ' + str(e)[:200])}",
                status_code=303)
        if session_will_break:
            # Session zeigt jetzt auf einen geloeschten User -> Session
            # verwerfen, Login-Screen mit klarer Erklaerung anzeigen.
            request.session.clear()
            return RedirectResponse(
                "/login?merged=1", status_code=303)
        return RedirectResponse("/admin/users/merge?ok=1",
                                  status_code=303)

    @app.post("/admin/users/{user_id}/approve")
    async def admin_approve(user_id: str, request: Request):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            from db import set_user_status, audit
            set_user_status(user_id, "approved")
            audit(admin["id"], "approve_user", f"User {user_id} genehmigt", object_type="user", object_id=user_id)
        except Exception as e:
            logger.error("Approve-Fehler: %s", e)
        return RedirectResponse("/admin/users", status_code=302)

    @app.get("/admin/users/invite", response_class=HTMLResponse)
    async def admin_invite_user_get(request: Request):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        return templates.TemplateResponse("admin_user_invite.html", {
            "request": request,
            "user": admin,
            "saved": False,
            "error": None,
            **t_ctx(request),
        })

    @app.post("/admin/users/invite", response_class=HTMLResponse)
    async def admin_invite_user_post(
        request: Request,
        username: str = Form(...),
        email: str = Form(...),
        full_name: str = Form(default=""),
        company: str = Form(default=""),
        invite_lang: str = Form(default="de"),
        role_type: str = Form(default="employee"),
        also_create_mobile_invite: str = Form(default="on"),
    ):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        tc = t_ctx(request)
        _ = tc["_"]
        error = None
        if len(username.strip()) < 3:
            error = _("invite_username_length_error")
        elif "@" not in email or "." not in email:
            error = _("invite_email_required_error")
        else:
            try:
                from db import username_exists
                if username_exists(username.strip()):
                    error = _("reg_user_exists")
            except Exception as e:
                error = str(e)
        if error:
            return templates.TemplateResponse("admin_user_invite.html", {
                "request": request,
                "user": admin,
                "saved": False,
                "error": error,
                "f_username": username,
                "f_email": email,
                "f_full_name": full_name,
                "f_company": company,
                "f_invite_lang": invite_lang,
                "f_role_type": role_type,
                **tc,
            })

        temp_password = _generate_temp_password()
        created_user = None
        try:
            from db import create_invited_user, get_tenant_full_by_user_id, delete_user, audit
            tenant = get_tenant_full_by_user_id(admin["id"]) or {}
            if not tenant.get("mail_api_key") or not tenant.get("mail_from"):
                raise RuntimeError(_("invite_mail_not_configured"))

            created_user = create_invited_user(
                username=username.strip(),
                password=temp_password,
                email=email.strip(),
                full_name=full_name.strip(),
                company=company.strip(),
                invited_by_user_id=admin["id"],
                invitation_language=invite_lang.strip(),
                role_type=role_type.strip(),
            )

            from invite_mail import render_invitation_email
            from reporting.mail_client import send_report
            login_url = f"{_get_base_url(request)}/login"
            subject, html_body = render_invitation_email(
                lang=invite_lang.strip(),
                full_name=full_name.strip(),
                username=username.strip(),
                password=temp_password,
                login_url=login_url,
            )
            send_report(
                recipients=[email.strip()],
                subject=subject,
                html_body=html_body,
                api_key=tenant.get("mail_api_key", ""),
                mail_from=tenant.get("mail_from", ""),
                mail_from_name=tenant.get("mail_from_name", "") or "Printix Management Console",
            )
            audit(
                admin["id"],
                "invite_user",
                f"Benutzer '{username.strip()}' eingeladen ({email.strip()}, lang={invite_lang.strip()})",
                object_type="user",
                object_id=created_user["id"],
            )
        except Exception as e:
            logger.error("Invite-User-Fehler: %s", e)
            if created_user:
                try:
                    from db import delete_user
                    delete_user(created_user["id"])
                except Exception:
                    pass
            return templates.TemplateResponse("admin_user_invite.html", {
                "request": request,
                "user": admin,
                "saved": False,
                "error": str(e),
                "f_username": username,
                "f_email": email,
                "f_full_name": full_name,
                "f_company": company,
                "f_invite_lang": invite_lang,
                "f_role_type": role_type,
                **tc,
            })

        # v0.2.0: optional auch eine Mobile-Setup-Einladung anlegen.
        # Default: ON (Checkbox standardmaessig aktiviert).
        mobile_invite_url = ""
        if also_create_mobile_invite and created_user:
            try:
                from db import create_mobile_invite as _cmi
                base_url = mcp_base_url_or(request)
                inv = _cmi(
                    user_id=created_user["id"],
                    server_url=base_url,
                    ttl_seconds=7 * 24 * 3600,
                    created_by_id=admin["id"],
                    channel="email",
                    email_recipient=email.strip(),
                )
                mobile_invite_url = f"{base_url}/m/setup?i={inv['token']}"
                try:
                    from db import audit
                    audit(
                        admin["id"],
                        "mobile_invite_created",
                        f"Mobile-Invite fuer '{username.strip()}' (TTL 7d, "
                        f"channel=email) waehrend Account-Invite",
                        object_type="mobile_invite",
                        object_id=inv["id"],
                    )
                except Exception:
                    pass
            except Exception as _mi_err:
                logger.warning("combined mobile invite failed: %s", _mi_err)

        return templates.TemplateResponse("admin_user_invite.html", {
            "request": request,
            "user": admin,
            "saved": True,
            "error": None,
            "created_username": username.strip(),
            "created_email": email.strip(),
            "mobile_invite_url": mobile_invite_url,
            **tc,
        })

    # ── v0.2.0: Mobile Invites (iOS-Onboarding) ──────────────────────────────

    def _is_smtp_configured_for(user_dict: dict) -> bool:
        """True wenn Tenant des Users mail_api_key + mail_from gesetzt hat."""
        try:
            from db import get_tenant_full_by_user_id
            tenant = get_tenant_full_by_user_id(user_dict["id"]) or {}
            return bool(tenant.get("mail_api_key")) and bool(tenant.get("mail_from"))
        except Exception:
            return False

    def _build_mobile_invite_email_html(
        *,
        recipient: str,
        full_name: str,
        invite_url: str,
        expires_at: str,
        lang: str,
        server_url: str = "",
        admin_name: str = "",
    ) -> tuple[str, str]:
        """Liefert (subject, html_body) für die Mobile-Invite-Mail.

        v0.5.2: Wenn `mobile_invite_email_subject` / `_body` in den Settings
        gesetzt sind, werden diese mit str.format_map (defaultdict) und den
        unten dokumentierten Platzhaltern substituiert:
        `{full_name}`, `{server_url}`, `{invite_url}`, `{expires_at}`,
        `{admin_name}`. Fehlt ein Platzhalter, bleibt er als Literal
        stehen — kein KeyError. Wenn die Settings leer sind, fällt der
        Helper auf die i18n-basierten Default-Bausteine zurück.
        """
        from collections import defaultdict
        try:
            from db import get_setting
        except Exception:
            get_setting = None  # type: ignore
        from web.i18n import TRANSLATIONS
        tr = TRANSLATIONS.get(lang or "en") or TRANSLATIONS.get("en", {})

        display_name = (full_name or "").strip() or recipient
        # v0.7.28: App-Store-URL als Platzhalter — Admin kann ihn frei platzieren.
        # Hartkodiert auf die Marketing-URL der App. Sobald die App Store-Link
        # verfuegbar ist (post Apple-Review), via Setting ueberschreibbar.
        app_store_url_default = (
            "https://apps.apple.com/de/app/mysecureprint/id6785880823"
        )
        app_store_url = ""
        if get_setting is not None:
            try:
                app_store_url = (get_setting(
                    "ios_app_store_url", "") or "").strip()
            except Exception:
                app_store_url = ""
        if not app_store_url:
            app_store_url = app_store_url_default

        ctx = defaultdict(str, {
            "full_name": display_name,
            "server_url": server_url or "",
            "invite_url": invite_url,
            "expires_at": (expires_at or "")[:19],
            "admin_name": admin_name or "",
            "app_store_url": app_store_url,
            "lang": lang or "en",
            # `{qr_code}` wird unten nach QR-Generierung gesetzt, vorerst leer
            "qr_code": "",
        })

        # Configurable subject/body (optional)
        subject_tpl = ""
        body_tpl = ""
        tpl_lang = ""
        if get_setting is not None:
            try:
                subject_tpl = get_setting("mobile_invite_email_subject", "")
                body_tpl = get_setting("mobile_invite_email_body", "")
                tpl_lang = (get_setting(
                    "mobile_invite_email_lang", "") or "").strip()
            except Exception:
                subject_tpl = ""
                body_tpl = ""
                tpl_lang = ""

        # v0.7.28: Wenn der Admin einen eigenen Body in einer Sprache
        # hinterlegt hat, ziehen wir die i18n-Bausteine (QR-Label,
        # Footer-Default) auch in DIESER Sprache — sonst gibt es z.B.
        # englischen Body mit deutschem QR-Label. Bei reinen Fallback-
        # Templates (kein body_tpl) bleibt lang die caller-gewuenschte.
        if body_tpl.strip() and tpl_lang:
            tr = (TRANSLATIONS.get(tpl_lang)
                    or TRANSLATIONS.get("en", {}))

        # Fallback subject (i18n)
        if not subject_tpl.strip():
            subject_tpl = tr.get(
                "mobile_invite_email_subject",
                "MySecurePrint — set up the app on your iPhone",
            )

        try:
            subject = subject_tpl.format_map(ctx)
        except Exception:
            subject = subject_tpl

        # v0.6.1: QR-Code inline einbetten — Nutzer kann ihn vom Bildschirm
        # mit der iPhone-Kamera scannen (alternative zum Klick auf den Link).
        # PNG-Bytes als base64 data:URI — funktioniert in allen modernen
        # Mail-Clients ohne Anhang-Roundtrip.
        qr_data_uri = ""
        try:
            import segno as _segno
            import io as _io
            import base64 as _b64
            _qr = _segno.make(invite_url, error="m")
            _buf = _io.BytesIO()
            _qr.save(_buf, kind="png", scale=6, border=2, dark="#002854")
            qr_data_uri = "data:image/png;base64," + _b64.b64encode(_buf.getvalue()).decode("ascii")
        except Exception:
            qr_data_uri = ""

        qr_html_block = ""
        if qr_data_uri:
            qr_label = tr.get("mobile_invite_email_qr_label",
                              "Oder scanne diesen QR-Code mit der iPhone-Kamera:")
            qr_html_block = (
                "<div style=\"text-align:center;margin:24px 0;padding:18px;"
                "background:#f8fafc;border-radius:10px;\">"
                f"<p style=\"font-size:.88em;color:#475569;margin-bottom:10px;\">{qr_label}</p>"
                f"<img src=\"{qr_data_uri}\" alt=\"QR-Code\" "
                "style=\"max-width:200px;height:auto;border-radius:6px;\">"
                "</div>"
            )

        if body_tpl.strip():
            # v0.7.28: {qr_code} ist ein platzierbarer Platzhalter im
            # Body — wenn der Admin ihn benutzt, wird die QR-HTML-Block
            # dort eingesetzt; sonst (wie frueher) am Ende angefuegt.
            qr_inline_used = "{qr_code}" in body_tpl
            # Wrap-Erkennung MUSS vor der Substitution passieren — sonst
            # detected ein injizierter <img> (aus {qr_code}) als „HTML"
            # und der Rest des Plaintext-Bodys wird nicht in <p> gewickelt.
            looks_plaintext = "<" not in body_tpl
            ctx["qr_code"] = qr_html_block if qr_inline_used else ""
            try:
                body_text = body_tpl.format_map(ctx)
            except Exception:
                body_text = body_tpl
            if looks_plaintext:
                # Plaintext-Body: split am QR-Block damit wir nicht den
                # injizierten <img> in <p>…</p> einwickeln.
                if qr_inline_used and qr_html_block:
                    parts = body_text.split(qr_html_block, 1)
                    pre = "<p>" + parts[0].replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
                    post = ""
                    if len(parts) == 2:
                        post = "<p>" + parts[1].replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
                    body_text = pre + qr_html_block + post
                else:
                    body_text = "<p>" + body_text.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
            html_body = (
                "<div style=\"font-family:Helvetica,Arial,sans-serif;"
                "color:#1a1a1a;line-height:1.55;max-width:560px;\">"
                + body_text
                + ("" if qr_inline_used else qr_html_block)
                + "</div>"
            )
        else:
            greeting = tr.get("mobile_invite_email_greeting", "Hi")
            intro = tr.get("mobile_invite_email_intro", "")
            open_link = tr.get("mobile_invite_email_open_link", "Open app setup")
            fallback = tr.get("mobile_invite_email_fallback", "")
            signin = tr.get("mobile_invite_email_signin_note", "")
            footer_tpl = tr.get(
                "mobile_invite_email_footer",
                "This invite expires on {expires_at}.",
            )
            footer = footer_tpl.replace("{expires_at}", ctx["expires_at"])
            html_body = (
                "<div style=\"font-family:Helvetica,Arial,sans-serif;"
                "color:#1a1a1a;line-height:1.55;max-width:560px;\">"
                f"<p>{greeting} {display_name},</p>"
                f"<p>{intro}</p>"
                "<p style=\"margin:28px 0;text-align:center;\">"
                f"<a href=\"{invite_url}\" "
                "style=\"display:inline-block;padding:14px 28px;"
                "background:#002854;color:#fff;border-radius:8px;"
                "text-decoration:none;font-weight:600;\">"
                f"{open_link}</a></p>"
                + qr_html_block +
                f"<p style=\"font-size:.92em;color:#555;\">{fallback}</p>"
                f"<p style=\"font-size:.85em;color:#666;word-break:break-all;\">"
                f"<a href=\"{invite_url}\">{invite_url}</a></p>"
                f"<p style=\"font-size:.88em;color:#555;\">{signin}</p>"
                "<hr style=\"border:none;border-top:1px solid #e2e8f0;"
                "margin:24px 0;\">"
                f"<p style=\"font-size:.78em;color:#888;\">{footer}</p>"
                "</div>"
            )
        return subject, html_body

    def _send_mobile_invite_email(
        admin: dict,
        recipient: str,
        full_name: str,
        invite_url: str,
        expires_at: str,
        lang: str,
    ) -> bool:
        """Versendet die Mobile-Invite-Mail über die existierende SMTP-Helper.

        Returns True bei Erfolg, False bei jeder Form von Fehler. Der Caller
        zeigt dann die Copy-Link-Fallback-UI.
        """
        try:
            from db import get_tenant_full_by_user_id, get_setting, _dec
            # v0.5.7: Slim Resend-Client statt geloeschtem reporting.mail_client.
            # Mail-Credentials werden in dieser Reihenfolge aufgeloest:
            #   1. tenant.mail_api_key + tenant.mail_from (per-Tenant)
            #   2. global_mail_api_key + global_mail_from (DB-Settings)
            #   3. ENV-Variablen RESEND_API_KEY + RESEND_FROM (deployment)
            from mail_client import send_mail, MailSendError
            tenant = get_tenant_full_by_user_id(admin["id"]) or {}
            api_key = (tenant.get("mail_api_key") or "").strip()
            mail_from = (tenant.get("mail_from") or "").strip()
            mail_from_name = tenant.get("mail_from_name", "") or "MySecurePrint"
            if not api_key:
                _enc_global = get_setting("global_mail_api_key", "")
                if _enc_global:
                    try:
                        api_key = _dec(_enc_global)
                    except Exception:
                        api_key = ""
                mail_from = mail_from or (get_setting("global_mail_from", "") or "")
                mail_from_name = (get_setting("global_mail_from_name", "")
                                  or mail_from_name)
            if not api_key:
                api_key = os.environ.get("RESEND_API_KEY", "")
                mail_from = mail_from or os.environ.get("RESEND_FROM", "")

            # v0.7.0: Provider-Auswahl. Bei "graph" laden wir Entra-
            # Credentials + Service-Mailbox aus den Settings. Resend
            # bleibt Fallback (siehe send_mail).
            provider = (get_setting("mail_provider", "") or "resend").strip().lower()
            graph_tid = graph_cid = graph_csec = graph_sender = ""
            if provider == "graph":
                graph_tid = (get_setting("entra_tenant_id", "") or "").strip()
                graph_cid = (get_setting("entra_client_id", "") or "").strip()
                _g_csec_enc = get_setting("entra_client_secret", "")
                try:
                    graph_csec = _dec(_g_csec_enc) if _g_csec_enc else ""
                except Exception:
                    graph_csec = ""
                graph_sender = (get_setting("mail_graph_sender", "") or "").strip()
                if not (graph_tid and graph_cid and graph_csec and graph_sender):
                    logger.warning(
                        "mobile-invite: provider=graph aber Setup unvollstaendig "
                        "(tid=%s cid=%s sec=%s sender=%s) — fallback auf Resend",
                        bool(graph_tid), bool(graph_cid), bool(graph_csec),
                        bool(graph_sender),
                    )
                    provider = "resend"

            if provider == "resend" and (not api_key or not mail_from):
                logger.warning(
                    "mobile-invite email skipped — no mail provider configured "
                    "(Resend tenant/global/env all empty, Graph not selected)."
                )
                return False

            subject, html_body = _build_mobile_invite_email_html(
                recipient=recipient,
                full_name=full_name,
                invite_url=invite_url,
                expires_at=expires_at,
                lang=lang,
                server_url=tenant.get("tenant_url", "") or "",
                admin_name=(admin or {}).get("full_name", "")
                    or (admin or {}).get("username", ""),
            )
            send_mail(
                recipients=[recipient],
                subject=subject,
                html_body=html_body,
                provider=provider,
                api_key=api_key,
                mail_from=mail_from,
                mail_from_name=mail_from_name,
                graph_tenant_id=graph_tid,
                graph_client_id=graph_cid,
                graph_client_secret=graph_csec,
                graph_sender_mailbox=graph_sender,
            )
            return True
        except MailSendError as me:
            logger.warning("mobile-invite email failed: %s", me)
            return False
        except Exception as e:
            logger.warning("mobile-invite email unexpected error: %s", e)
            return False

    def _make_mobile_invite_qr_svg(payload: str) -> str:
        """SVG-QR fuer Mobile-Invites (wiederverwendet welcome-QR-Style)."""
        try:
            import segno
            import io
            qr = segno.make(payload, error="m")
            # v0.4.1: segno schreibt Bytes — siehe _make_welcome_qr_svg.
            buf = io.BytesIO()
            qr.save(
                buf, kind="svg", scale=8, border=2,
                dark="#002854", light="#ffffff", xmldecl=False, svgns=True,
            )
            return buf.getvalue().decode("utf-8")
        except Exception as e:
            logger.warning("mobile-invite QR generation failed: %s", e)
            return ""

    @app.get("/admin/users/{user_id}/mobile-invite", response_class=HTMLResponse)
    async def admin_user_mobile_invite_get(user_id: str, request: Request):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        from db import get_user_by_id, list_mobile_invites_for_user
        target = get_user_by_id(user_id)
        if not target:
            return RedirectResponse("/admin/users", status_code=302)
        invites = list_mobile_invites_for_user(user_id)
        return templates.TemplateResponse(
            "admin_user_mobile_invite.html",
            {
                "request": request,
                "user": admin,
                "target_user": target,
                "invites": invites,
                "fresh_invite_url": "",
                "fresh_invite_qr_svg": "",
                "fresh_invite_id": "",
                "smtp_configured": _is_smtp_configured_for(admin),
                "flash_ok": request.query_params.get("ok", ""),
                "flash_err": request.query_params.get("err", ""),
                **t_ctx(request),
            },
        )

    @app.post(
        "/admin/users/{user_id}/mobile-invite/create",
        response_class=HTMLResponse,
    )
    async def admin_user_mobile_invite_create(
        user_id: str,
        request: Request,
        ttl_seconds: str = Form(default="604800"),
        channel: str = Form(default="email"),
        recipient_email: str = Form(default=""),
        send_email_now: str = Form(default=""),
    ):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        from db import (
            get_user_by_id, create_mobile_invite, list_mobile_invites_for_user,
            audit, mark_mobile_invite_email_sent,
        )
        target = get_user_by_id(user_id)
        if not target:
            return RedirectResponse("/admin/users", status_code=302)
        try:
            ttl_int = int(ttl_seconds)
        except Exception:
            ttl_int = 7 * 24 * 3600
        if ttl_int not in (24 * 3600, 7 * 24 * 3600, 30 * 24 * 3600):
            ttl_int = 7 * 24 * 3600
        ch = (channel or "email").strip().lower()
        if ch not in ("email", "qr", "both"):
            ch = "email"
        recipient = (recipient_email or target.get("email", "") or "").strip()
        base_url = mcp_base_url_or(request)
        inv = create_mobile_invite(
            user_id=user_id,
            server_url=base_url,
            ttl_seconds=ttl_int,
            created_by_id=admin["id"],
            channel=ch,
            email_recipient=recipient,
        )
        invite_url = f"{base_url}/m/setup?i={inv['token']}"
        try:
            audit(
                admin["id"],
                "mobile_invite_created",
                f"Mobile-Invite fuer user_id={user_id} channel={ch} "
                f"ttl={ttl_int}s",
                object_type="mobile_invite",
                object_id=inv["id"],
            )
        except Exception:
            pass

        email_ok = False
        email_err = ""
        if ch in ("email", "both") and bool(send_email_now) and recipient:
            email_ok = _send_mobile_invite_email(
                admin=admin,
                recipient=recipient,
                full_name=target.get("full_name", "") or target.get("username", ""),
                invite_url=invite_url,
                expires_at=inv["expires_at"],
                lang=(target.get("invitation_language") or "en"),
            )
            if email_ok:
                try:
                    mark_mobile_invite_email_sent(inv["id"])
                    audit(
                        admin["id"],
                        "mobile_invite_sent_email",
                        f"recipient={recipient}",
                        object_type="mobile_invite",
                        object_id=inv["id"],
                    )
                except Exception:
                    pass
            else:
                email_err = "smtp_failed"

        qr_svg = ""
        if ch in ("qr", "both"):
            qr_svg = _make_mobile_invite_qr_svg(invite_url)

        invites = list_mobile_invites_for_user(user_id)
        flash_ok = ""
        flash_err = ""
        if email_ok:
            flash_ok = "mobile_invite_created_email_sent"
        elif email_err:
            flash_err = "mobile_invite_email_failed"
        elif ch == "qr":
            flash_ok = "mobile_invite_created_qr_only"
        else:
            flash_ok = "mobile_invite_created_no_email"
        return templates.TemplateResponse(
            "admin_user_mobile_invite.html",
            {
                "request": request,
                "user": admin,
                "target_user": target,
                "invites": invites,
                "fresh_invite_url": invite_url,
                "fresh_invite_qr_svg": qr_svg,
                "fresh_invite_id": inv["id"],
                "fresh_invite_expires_at": inv["expires_at"],
                "smtp_configured": _is_smtp_configured_for(admin),
                "flash_ok": flash_ok,
                "flash_err": flash_err,
                **t_ctx(request),
            },
        )

    @app.post(
        "/admin/users/{user_id}/mobile-invite/{invite_id}/email",
        response_class=HTMLResponse,
    )
    async def admin_user_mobile_invite_email(
        user_id: str,
        invite_id: str,
        request: Request,
    ):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        from db import (
            get_user_by_id, get_mobile_invite_by_id,
            mark_mobile_invite_email_sent, audit,
        )
        target = get_user_by_id(user_id)
        inv = get_mobile_invite_by_id(invite_id)
        if not target or not inv or inv["user_id"] != user_id:
            return RedirectResponse(
                f"/admin/users/{user_id}/mobile-invite?err=mobile_invite_invalid_token",
                status_code=302,
            )
        # Roh-Token ist nicht mehr abrufbar — wir koennen die URL aber
        # rekonstruieren, indem wir die token-Spalte direkt lesen (nur fuer
        # admin-resend-Funktionalitaet; nicht via API exposiert).
        from db import _conn as _db_conn
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT token FROM mobile_invites WHERE id = ?", (invite_id,)
            ).fetchone()
        if not row:
            return RedirectResponse(
                f"/admin/users/{user_id}/mobile-invite?err=mobile_invite_invalid_token",
                status_code=302,
            )
        invite_url = f"{inv['server_url']}/m/setup?i={row['token']}"
        recipient = inv.get("email_recipient") or target.get("email", "")
        ok = _send_mobile_invite_email(
            admin=admin,
            recipient=recipient,
            full_name=target.get("full_name") or target.get("username", ""),
            invite_url=invite_url,
            expires_at=inv["expires_at"],
            lang=(target.get("invitation_language") or "en"),
        )
        if ok:
            mark_mobile_invite_email_sent(invite_id)
            try:
                audit(
                    admin["id"],
                    "mobile_invite_sent_email",
                    f"resend recipient={recipient}",
                    object_type="mobile_invite",
                    object_id=invite_id,
                )
            except Exception:
                pass
            return RedirectResponse(
                f"/admin/users/{user_id}/mobile-invite?ok=mobile_invite_email_sent_ok",
                status_code=302,
            )
        return RedirectResponse(
            f"/admin/users/{user_id}/mobile-invite?err=mobile_invite_email_failed",
            status_code=302,
        )

    @app.post(
        "/admin/users/{user_id}/mobile-invite/{invite_id}/revoke",
        response_class=HTMLResponse,
    )
    async def admin_user_mobile_invite_revoke(
        user_id: str,
        invite_id: str,
        request: Request,
    ):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        from db import revoke_mobile_invite, audit
        revoked = revoke_mobile_invite(invite_id)
        if revoked:
            try:
                audit(
                    admin["id"],
                    "mobile_invite_revoked",
                    f"invite_id={invite_id}",
                    object_type="mobile_invite",
                    object_id=invite_id,
                )
            except Exception:
                pass
        return RedirectResponse(
            f"/admin/users/{user_id}/mobile-invite?ok=mobile_invite_revoked_ok",
            status_code=302,
        )

    @app.get("/admin/users/{user_id}/mobile-invite/{invite_id}/qr.png")
    async def admin_user_mobile_invite_qr(
        user_id: str,
        invite_id: str,
        request: Request,
    ):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        from db import get_mobile_invite_by_id, _conn as _db_conn
        inv = get_mobile_invite_by_id(invite_id)
        if not inv or inv["user_id"] != user_id:
            return Response(status_code=404)
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT token FROM mobile_invites WHERE id = ?", (invite_id,)
            ).fetchone()
        if not row:
            return Response(status_code=404)
        invite_url = f"{inv['server_url']}/m/setup?i={row['token']}"
        try:
            import segno
            import io
            qr = segno.make(invite_url, error="m")
            buf = io.BytesIO()
            qr.save(buf, kind="png", scale=10, border=2, dark="#002854")
            return Response(content=buf.getvalue(), media_type="image/png")
        except Exception as e:
            logger.warning("mobile-invite qr.png failed: %s", e)
            return Response(status_code=500)

    # ── v0.5.2: Bulk Mobile-Invite ───────────────────────────────────────────

    @app.post("/admin/users/bulk-mobile-invite")
    async def admin_users_bulk_mobile_invite(request: Request):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            form = await request.form()
            ids = form.getlist("user_ids")
        except Exception:
            try:
                body = await request.json()
                ids = body.get("user_ids") or []
            except Exception:
                ids = []
        ids = [str(x).strip() for x in (ids or []) if str(x).strip()]
        from db import (
            get_user_by_id, create_mobile_invite, audit,
            mark_mobile_invite_email_sent,
        )
        base_url = mcp_base_url_or(request)
        ok = 0
        failed = 0
        errors: list[str] = []
        for uid in ids:
            try:
                target = get_user_by_id(uid)
                if not target:
                    failed += 1
                    errors.append(f"{uid}: not_found")
                    continue
                recipient = (target.get("email") or "").strip()
                inv = create_mobile_invite(
                    user_id=uid,
                    server_url=base_url,
                    ttl_seconds=7 * 24 * 3600,
                    created_by_id=admin["id"],
                    channel="email",
                    email_recipient=recipient,
                )
                invite_url = f"{base_url}/m/setup?i={inv['token']}"
                try:
                    audit(
                        admin["id"],
                        "mobile_invite_created",
                        f"Bulk-Invite fuer user_id={uid}",
                        object_type="mobile_invite",
                        object_id=inv["id"],
                    )
                except Exception:
                    pass
                if recipient:
                    email_ok = _send_mobile_invite_email(
                        admin=admin,
                        recipient=recipient,
                        full_name=target.get("full_name", "")
                            or target.get("username", ""),
                        invite_url=invite_url,
                        expires_at=inv["expires_at"],
                        lang=(target.get("invitation_language") or "en"),
                    )
                    if email_ok:
                        try:
                            mark_mobile_invite_email_sent(inv["id"])
                            audit(
                                admin["id"],
                                "mobile_invite_sent_email",
                                f"recipient={recipient} (bulk)",
                                object_type="mobile_invite",
                                object_id=inv["id"],
                            )
                        except Exception:
                            pass
                        ok += 1
                    else:
                        failed += 1
                        errors.append(f"{uid}: smtp_failed")
                else:
                    ok += 1
            except Exception as e:
                failed += 1
                errors.append(f"{uid}: {str(e)[:120]}")
        accept = (request.headers.get("accept") or "").lower()
        if "application/json" in accept:
            return JSONResponse({"ok": ok, "failed": failed, "errors": errors})
        return RedirectResponse(
            f"/admin/users?bulk_ok={ok}&bulk_failed={failed}",
            status_code=302,
        )

    # ── v0.5.2: Email-Template Editor ────────────────────────────────────────

    # v0.7.32: Placeholder-Default in der Template-Sprache — vorher stand
    # immer der englische Text da, egal welche Sprache der Admin gewaehlt hatte.
    _EMAIL_TPL_DEFAULTS = {
        "en": {
            "subject": "MySecurePrint — set up the app on your iPhone",
            "body": ("Hi {full_name},\n\n"
                       "Your mobile setup link is ready: {invite_url}\n\n"
                       "This invite expires on {expires_at}.\n\n"
                       "— {admin_name}"),
        },
        "de": {
            "subject": "MySecurePrint — App auf dem iPhone einrichten",
            "body": ("Hallo {full_name},\n\n"
                       "Dein Setup-Link für die App: {invite_url}\n\n"
                       "Die Einladung läuft am {expires_at} ab.\n\n"
                       "— {admin_name}"),
        },
        "fr": {
            "subject": "MySecurePrint — configurer l'app sur ton iPhone",
            "body": ("Bonjour {full_name},\n\n"
                       "Ton lien d'installation : {invite_url}\n\n"
                       "L'invitation expire le {expires_at}.\n\n"
                       "— {admin_name}"),
        },
        "es": {
            "subject": "MySecurePrint — configura la app en tu iPhone",
            "body": ("Hola {full_name},\n\n"
                       "Tu enlace de configuración: {invite_url}\n\n"
                       "La invitación caduca el {expires_at}.\n\n"
                       "— {admin_name}"),
        },
        "it": {
            "subject": "MySecurePrint — configura l'app sul tuo iPhone",
            "body": ("Ciao {full_name},\n\n"
                       "Il tuo link di configurazione: {invite_url}\n\n"
                       "L'invito scade il {expires_at}.\n\n"
                       "— {admin_name}"),
        },
        "nl": {
            "subject": "MySecurePrint — de app instellen op je iPhone",
            "body": ("Hoi {full_name},\n\n"
                       "Je installatielink: {invite_url}\n\n"
                       "De uitnodiging verloopt op {expires_at}.\n\n"
                       "— {admin_name}"),
        },
        "nb": {
            "subject": "MySecurePrint — sett opp appen på iPhone-en din",
            "body": ("Hei {full_name},\n\n"
                       "Din oppsettlenke: {invite_url}\n\n"
                       "Invitasjonen utløper {expires_at}.\n\n"
                       "— {admin_name}"),
        },
        "sv": {
            "subject": "MySecurePrint — konfigurera appen på din iPhone",
            "body": ("Hej {full_name},\n\n"
                       "Din installationslänk: {invite_url}\n\n"
                       "Inbjudan går ut den {expires_at}.\n\n"
                       "— {admin_name}"),
        },
    }

    def _email_tpl_default(lang: str, field: str) -> str:
        return (_EMAIL_TPL_DEFAULTS.get(lang, {}).get(field)
                or _EMAIL_TPL_DEFAULTS["en"][field])

    # Backward-compat exports fuer bestehende Aufrufer/Preview-Kontext.
    _EMAIL_TPL_DEFAULT_SUBJECT = _EMAIL_TPL_DEFAULTS["en"]["subject"]
    _EMAIL_TPL_DEFAULT_BODY = _EMAIL_TPL_DEFAULTS["en"]["body"]

    @app.get("/admin/email-templates", response_class=HTMLResponse)
    async def admin_email_templates_get(request: Request):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        from db import get_setting
        subject_val = get_setting("mobile_invite_email_subject", "")
        body_val = get_setting("mobile_invite_email_body", "")
        tpl_lang = (get_setting("mobile_invite_email_lang", "")
                      or admin.get("invitation_language") or "de").strip()
        return templates.TemplateResponse(
            "admin_email_templates.html",
            {
                "request": request,
                "user": admin,
                "saved": False,
                "subject_val": subject_val,
                "body_val": body_val,
                "tpl_lang": tpl_lang,
                "default_subject": _email_tpl_default(tpl_lang, "subject"),
                "default_body": _email_tpl_default(tpl_lang, "body"),
                "preview_html": "",
                "preview_subject": "",
                **t_ctx(request),
            },
        )

    @app.post("/admin/email-templates", response_class=HTMLResponse)
    async def admin_email_templates_post(
        request: Request,
        subject: str = Form(default=""),
        body: str = Form(default=""),
        preview: str = Form(default=""),
        tpl_lang: str = Form(default="de"),
    ):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        from db import set_setting, audit
        preview_only = bool(preview)
        # Whitelist allowed langs — verhindert dass willkuerliche Strings
        # in den `lang`-Lookup von TRANSLATIONS reinfliegen.
        if tpl_lang not in ("de", "en", "fr", "es", "it", "nl", "sv", "nb"):
            tpl_lang = "de"
        # Save (or preview-write so preview reflects the would-be state)
        set_setting("mobile_invite_email_subject", subject.strip())
        set_setting("mobile_invite_email_body", body)
        set_setting("mobile_invite_email_lang", tpl_lang)
        if not preview_only:
            try:
                audit(
                    admin["id"],
                    "mobile_invite_email_template_saved",
                    "Mobile-Invite Email-Template aktualisiert",
                    object_type="setting",
                    object_id="mobile_invite_email_body",
                )
            except Exception:
                pass
        preview_subject, preview_html = ("", "")
        try:
            preview_subject, preview_html = _build_mobile_invite_email_html(
                recipient="user@example.com",
                full_name="Erika Mustermann",
                invite_url=(
                    f"{mcp_base_url_or(request)}/m/setup?i=PREVIEW_TOKEN"
                ),
                expires_at="2026-12-31T23:59:59",
                lang=tpl_lang,
                server_url=mcp_base_url_or(request),
                admin_name=admin.get("full_name", "")
                    or admin.get("username", ""),
            )
        except Exception as e:
            logger.warning("email-template preview failed: %s", e)
        return templates.TemplateResponse(
            "admin_email_templates.html",
            {
                "request": request,
                "user": admin,
                "saved": (not preview_only),
                "subject_val": subject,
                "body_val": body,
                "tpl_lang": tpl_lang,
                "default_subject": _email_tpl_default(tpl_lang, "subject"),
                "default_body": _email_tpl_default(tpl_lang, "body"),
                "preview_html": preview_html,
                "preview_subject": preview_subject,
                **t_ctx(request),
            },
        )

    # ── v0.5.2: Printix Auto-User-Sync ───────────────────────────────────────

    def _get_printix_sync_cfg() -> dict:
        from db import get_setting
        def _int(key: str, default: int) -> int:
            try:
                return int(get_setting(key, str(default)) or default)
            except Exception:
                return default
        return {
            "enabled": get_setting("printix_user_sync_enabled", "0") == "1",
            "interval_minutes": max(5, min(1440, _int(
                "printix_user_sync_interval_minutes", 60))),
            "auto_invite":
                get_setting("printix_user_sync_auto_invite", "0") == "1",
            "last_run_at": get_setting(
                "printix_user_sync_last_run_at", ""),
        }

    def _get_printix_sync_last_result() -> dict:
        from db import get_setting
        raw = get_setting("printix_user_sync_last_result", "")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def _persist_sync_result(admin_user_id: str, result: dict) -> None:
        from db import set_setting, audit
        from datetime import datetime, timezone
        try:
            set_setting(
                "printix_user_sync_last_run_at",
                datetime.now(timezone.utc).isoformat(),
            )
            set_setting(
                "printix_user_sync_last_result",
                json.dumps(result)[:8000],
            )
        except Exception:
            pass
        try:
            audit(
                admin_user_id or "system",
                "printix_sync_run",
                f"new={result.get('new_users_count', 0)} "
                f"errors={len(result.get('errors', []))}",
                object_type="setting",
                object_id="printix_user_sync",
            )
        except Exception:
            pass

    def _run_printix_user_sync_once(admin_user_id: str = "") -> dict:
        """Eine Sync-Iteration: cached_printix_users auffrischen + neue
        Printix-User als lokale `users` anlegen. Optional pro neuem User
        einen Mobile-Invite erzeugen.

        Synchron — Caller wickelt asyncio.to_thread ab.
        """
        from db import (
            get_tenant_full_by_user_id,
            create_user_admin, update_user, get_all_users, audit,
            create_mobile_invite,
        )
        result: dict = {"ok": False, "new_users_count": 0, "errors": []}
        try:
            tenant = None
            if admin_user_id:
                tenant = get_tenant_full_by_user_id(admin_user_id) or None
            if not tenant:
                try:
                    from db import _find_tenant_owner_user_id
                    owner_id = _find_tenant_owner_user_id() or ""
                except Exception:
                    owner_id = ""
                if owner_id:
                    tenant = get_tenant_full_by_user_id(owner_id) or None
            if not tenant or not tenant.get("printix_tenant_id"):
                result["errors"].append("printix_tenant_not_configured")
                _persist_sync_result(admin_user_id, result)
                return result

            # 1) Refresh cached_printix_users via Printix-API
            try:
                from printix_client import PrintixClient
                from cloudprint.printix_cache_db import sync_users_for_tenant
                client = PrintixClient(
                    tenant_id=tenant["printix_tenant_id"],
                    print_client_id=tenant.get("print_client_id", ""),
                    print_client_secret=tenant.get("print_client_secret", ""),
                    ws_client_id=tenant.get("ws_client_id", ""),
                    ws_client_secret=tenant.get("ws_client_secret", ""),
                    um_client_id=tenant.get("um_client_id", ""),
                    um_client_secret=tenant.get("um_client_secret", ""),
                    shared_client_id=tenant.get("shared_client_id", ""),
                    shared_client_secret=tenant.get(
                        "shared_client_secret", ""),
                )
                sync_stats = sync_users_for_tenant(
                    tenant_id=tenant["id"],
                    printix_tenant_id=tenant["printix_tenant_id"],
                    client=client,
                )
                if isinstance(sync_stats, dict) and sync_stats.get("error"):
                    result["errors"].append(
                        f"printix_sync: {sync_stats.get('error')}"
                    )
            except Exception as e:
                result["errors"].append(f"printix_sync: {str(e)[:160]}")

            # 2) Read cached_printix_users
            from db import _conn as _db_conn
            with _db_conn() as conn:
                rows = conn.execute(
                    "SELECT printix_user_id, username, email, full_name, role "
                    "FROM cached_printix_users WHERE tenant_id = ?",
                    (tenant["id"],),
                ).fetchall()
            cached = [dict(r) for r in rows]

            local_users = get_all_users()
            local_by_pxid = {
                (u.get("printix_user_id") or ""): u for u in local_users
                if u.get("printix_user_id")
            }
            local_by_email = {
                (u.get("email") or "").strip().lower(): u
                for u in local_users if u.get("email")
            }
            local_by_username = {
                (u.get("username") or "").strip().lower(): u
                for u in local_users if u.get("username")
            }

            cfg = _get_printix_sync_cfg()
            base_url = (tenant.get("tenant_url") or "").strip()

            new_count = 0
            for px in cached:
                pxid = (px.get("printix_user_id") or "").strip()
                if not pxid or pxid.startswith("mgr:") or ":" in pxid:
                    continue
                email = (px.get("email") or "").strip()
                username = (px.get("username") or email or pxid).strip()
                if pxid in local_by_pxid:
                    existing = local_by_pxid[pxid]
                    # Feature 1A: Email aus Printix nachpflegen
                    if email and not (existing.get("email") or "").strip():
                        try:
                            update_user(user_id=existing["id"], email=email)
                        except Exception as e:
                            result["errors"].append(
                                f"update_email {pxid}: {str(e)[:80]}"
                            )
                    continue
                if email and email.lower() in local_by_email:
                    existing = local_by_email[email.lower()]
                    try:
                        update_user(
                            user_id=existing["id"], printix_user_id=pxid)
                    except Exception:
                        pass
                    continue
                if username and username.lower() in local_by_username:
                    existing = local_by_username[username.lower()]
                    try:
                        kwargs = {"printix_user_id": pxid}
                        if email:
                            kwargs["email"] = email
                        update_user(user_id=existing["id"], **kwargs)
                    except Exception:
                        pass
                    continue
                # Neu anlegen
                try:
                    import secrets as _sec
                    temp_pw = _sec.token_urlsafe(24)
                    created = create_user_admin(
                        username=(username[:64] or pxid[:32]),
                        password=temp_pw,
                        email=email,
                        role_type="employee",
                        status="approved",
                        full_name=(px.get("full_name") or "").strip(),
                    )
                    if created and pxid:
                        try:
                            update_user(
                                user_id=created["id"],
                                printix_user_id=pxid,
                            )
                        except Exception:
                            pass
                    new_count += 1
                    try:
                        audit(
                            admin_user_id or "system",
                            "printix_sync_user_imported",
                            f"Printix-User {username} ({email}) "
                            f"importiert (pxid={pxid})",
                            object_type="user",
                            object_id=(created or {}).get("id", ""),
                        )
                    except Exception:
                        pass
                    if cfg.get("auto_invite") and created and email:
                        try:
                            srv = base_url or ""
                            inv = create_mobile_invite(
                                user_id=created["id"],
                                server_url=srv,
                                ttl_seconds=7 * 24 * 3600,
                                created_by_id=admin_user_id or "system",
                                channel="email",
                                email_recipient=email,
                            )
                            if admin_user_id:
                                try:
                                    from db import get_user_by_id as _gub
                                    _admin = _gub(admin_user_id) or {}
                                    invite_url = (
                                        f"{srv}/m/setup?i={inv['token']}"
                                    )
                                    _send_mobile_invite_email(
                                        admin=_admin,
                                        recipient=email,
                                        full_name=created.get(
                                            "full_name", "")
                                            or created.get("username", ""),
                                        invite_url=invite_url,
                                        expires_at=inv["expires_at"],
                                        lang="de",
                                    )
                                except Exception:
                                    pass
                        except Exception as e:
                            result["errors"].append(
                                f"auto_invite {username}: {str(e)[:80]}"
                            )
                except Exception as e:
                    result["errors"].append(
                        f"create_user {username}: {str(e)[:120]}"
                    )

            result["new_users_count"] = new_count
            result["ok"] = True
        except Exception as e:
            result["errors"].append(f"fatal: {str(e)[:200]}")
        _persist_sync_result(admin_user_id, result)
        return result

    @app.get("/admin/printix-sync", response_class=HTMLResponse)
    async def admin_printix_sync_get(request: Request):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        cfg = _get_printix_sync_cfg()
        run_result = _get_printix_sync_last_result()
        saved = request.query_params.get("saved") == "1"
        return templates.TemplateResponse(
            "admin_printix_sync.html",
            {
                "request": request,
                "user": admin,
                "cfg": cfg,
                "run_result": run_result,
                "saved": saved,
                **t_ctx(request),
            },
        )

    @app.post("/admin/printix-sync")
    async def admin_printix_sync_post(
        request: Request,
        enabled: str = Form(default=""),
        interval_minutes: str = Form(default="60"),
        auto_invite: str = Form(default=""),
    ):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        from db import set_setting, audit
        try:
            iv = int(interval_minutes)
        except Exception:
            iv = 60
        iv = max(5, min(1440, iv))
        set_setting("printix_user_sync_enabled", "1" if enabled else "0")
        set_setting("printix_user_sync_interval_minutes", str(iv))
        set_setting("printix_user_sync_auto_invite",
                    "1" if auto_invite else "0")
        try:
            audit(
                admin["id"],
                "printix_sync_settings_saved",
                f"enabled={'1' if enabled else '0'} "
                f"interval={iv}min "
                f"auto_invite={'1' if auto_invite else '0'}",
                object_type="setting",
                object_id="printix_user_sync",
            )
        except Exception:
            pass
        return RedirectResponse("/admin/printix-sync?saved=1", status_code=302)

    @app.post("/admin/printix-sync/run-now")
    async def admin_printix_sync_run_now(request: Request):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        import asyncio as _asyncio
        try:
            await _asyncio.to_thread(_run_printix_user_sync_once, admin["id"])
        except Exception as e:
            logger.warning("printix-sync run-now failed: %s", e)
        return RedirectResponse("/admin/printix-sync?saved=1", status_code=302)

    # ── v0.2.0: Public Redemption Endpoints ──────────────────────────────────

    @app.get("/m/setup", response_class=HTMLResponse)
    async def m_setup(request: Request, i: str = ""):
        """Public explainer page for the iOS app deep-link.

        Detects iOS Safari and exposes a "Open MySecurePrint" button that
        triggers the `mysecureprint://setup?...` custom-scheme handover.
        On all platforms it also shows a QR fallback + the raw URL so the
        user can copy/paste it into a future iOS app build.
        """
        from db import get_mobile_invite_by_token
        ua = request.headers.get("user-agent", "")
        is_ios = ("iPhone" in ua) or ("iPad" in ua) or ("iPod" in ua)
        inv = get_mobile_invite_by_token(i) if i else None
        status = "ok"
        deep_link = ""
        qr_svg = ""
        server_url_for_app = ""
        if not inv:
            status = "invalid"
        elif inv.get("redeemed_at"):
            status = "redeemed"
        else:
            try:
                from datetime import datetime, timezone
                exp = inv.get("expires_at", "")
                if exp:
                    when = datetime.fromisoformat(exp.replace("Z", "+00:00"))
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=__import__(
                            "datetime"
                        ).timezone.utc)
                    if when < datetime.now(timezone.utc):
                        status = "expired"
            except Exception:
                pass
        if status == "ok" and inv:
            server_url_for_app = inv["server_url"]
            deep_link = (
                "mysecureprint://setup?server="
                f"{quote_plus(inv['server_url'])}&token={quote_plus(i)}"
            )
            qr_svg = _make_mobile_invite_qr_svg(deep_link)
        return templates.TemplateResponse(
            "m_setup.html",
            {
                "request": request,
                "user": None,
                "status": status,
                "is_ios": is_ios,
                "deep_link": deep_link,
                "qr_svg": qr_svg,
                "server_url": server_url_for_app,
                **t_ctx(request),
            },
        )

    @app.post("/api/v1/mobile-invite/redeem")
    async def api_mobile_invite_redeem(request: Request):
        """iOS app exchanges (token + verified MS identity) for a Bearer token.

        Request body (JSON):
            {
              "token": "<raw invite token from /m/setup>",
              "entra_oid": "<MS oid the app got from PKCE>",
              "email": "<MS email (optional)>",
              "display_name": "<MS display name (optional)>",
              "device_name": "<optional, for desktop_tokens.device_name>"
            }

        Response: { bearer_token, server_url, user: {...} }
        """
        try:
            body = await request.json()
        except Exception:
            body = {}
        token = (body.get("token") or "").strip()
        entra_oid = (body.get("entra_oid") or "").strip()
        email = (body.get("email") or "").strip()
        display_name = (body.get("display_name") or "").strip()
        device_name = (body.get("device_name") or "iOS-Mobile").strip()
        if not token:
            return JSONResponse(
                {"error": "missing token", "code": "missing_token"},
                status_code=400,
            )
        # v0.6.5: entra_oid ist optional — der Invite-Token ist bereits
        # one-shot + admin-issued + Expiry-gebunden und damit ausreichend
        # als Auth-Proof fuer Mobile-Setup. Wenn der Client einen oid
        # mitliefert (Future: PKCE-Flow extrahiert ihn aus ID-Token),
        # wird er fuer Erst-Linking + Mismatch-Check verwendet. Sonst
        # wird der Bearer-Token rein gegen den Invite-Token getauscht.
        # SECURITY-Hinweis: Tradeoff bewusst — ohne diese Lockerung war
        # der Mobile-Invite-Flow End-to-End unbenutzbar weil iOS den oid
        # nicht durchreicht.
        from db import (
            get_mobile_invite_by_token, get_user_by_id, get_or_create_entra_user,
            redeem_mobile_invite, audit, _conn as _db_conn,
        )
        inv = get_mobile_invite_by_token(token)
        if not inv:
            return JSONResponse(
                {"error": "unknown invite", "code": "invalid_token"},
                status_code=404,
            )
        if inv.get("redeemed_at"):
            return JSONResponse(
                {
                    "error": "invite already redeemed",
                    "code": "already_redeemed",
                },
                status_code=410,
            )
        # Expiry-Check
        try:
            from datetime import datetime, timezone
            exp = inv.get("expires_at", "")
            if exp:
                when = datetime.fromisoformat(exp.replace("Z", "+00:00"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                if when < datetime.now(timezone.utc):
                    return JSONResponse(
                        {"error": "invite expired", "code": "expired"},
                        status_code=410,
                    )
        except Exception:
            pass

        target = get_user_by_id(inv["user_id"])
        if not target:
            return JSONResponse(
                {"error": "user not found", "code": "user_missing"},
                status_code=404,
            )

        # entra_oid muss matchen ODER User hat noch keinen oid (Erst-Linking).
        # Wenn der Client keinen oid liefert (Mobile-Invite-Default-Flow),
        # ueberspringen wir Linking und Mismatch-Check ganz.
        existing_oid = (target.get("entra_oid") or "").strip()
        if entra_oid and existing_oid and existing_oid != entra_oid:
            return JSONResponse(
                {
                    "error": "Entra identity mismatch",
                    "code": "oid_mismatch",
                },
                status_code=403,
            )
        if entra_oid and not existing_oid:
            # Erst-Linking — entra_oid + ggf. email/full_name nachtragen.
            try:
                with _db_conn() as conn:
                    parts = ["entra_oid = ?"]
                    params: list = [entra_oid]
                    if email and not (target.get("email") or "").strip():
                        parts.append("email = ?")
                        params.append(email)
                    if display_name and not (target.get("full_name") or "").strip():
                        parts.append("full_name = ?")
                        params.append(display_name)
                    params.append(target["id"])
                    conn.execute(
                        f"UPDATE users SET {', '.join(parts)} WHERE id = ?",
                        params,
                    )
                target = get_user_by_id(target["id"])
            except Exception as link_err:
                logger.warning(
                    "mobile-invite redeem: oid linking failed: %s", link_err
                )

        # Atomar redeem markieren.
        peer_ip = (
            request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "")
        )
        ok = redeem_mobile_invite(inv["token_hash"], redeemed_from=peer_ip)
        if not ok:
            # Race: jemand anders war schneller, oder TTL exakt jetzt abgelaufen.
            return JSONResponse(
                {
                    "error": "could not redeem invite",
                    "code": "redeem_conflict",
                },
                status_code=410,
            )

        # Bearer Token ausstellen — gleiche Funktion wie die Desktop-Flows.
        try:
            from desktop_auth import create_token
            bearer = create_token(target["id"], device_name=device_name or "iOS-Mobile")
        except Exception as ct_err:
            logger.error("mobile-invite redeem: create_token failed: %s", ct_err)
            return JSONResponse(
                {"error": "token issue failed", "code": "token_failed"},
                status_code=500,
            )

        try:
            audit(
                target["id"],
                "mobile_invite_redeemed",
                f"invite_id={inv['id']} from={peer_ip} device='{device_name}'",
                object_type="mobile_invite",
                object_id=inv["id"],
            )
        except Exception:
            pass

        return JSONResponse(
            {
                "bearer_token": bearer,
                "server_url": inv["server_url"],
                "user": {
                    "id":        target["id"],
                    "username":  target.get("username", ""),
                    "email":     target.get("email", ""),
                    "full_name": target.get("full_name", ""),
                    "role_type": target.get("role_type", "employee"),
                },
            }
        )

    @app.get("/admin/users/bulk-import", response_class=HTMLResponse)
    async def admin_bulk_import_get(request: Request):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        return templates.TemplateResponse("admin_user_bulk.html", {
            "request": request, "user": admin,
            "error": None, "results": None, "summary": None,
            **t_ctx(request),
        })

    @app.post("/admin/users/bulk-import", response_class=HTMLResponse)
    async def admin_bulk_import_post(
        request:              Request,
        csv_file:             Optional[UploadFile] = File(default=None),
        csv_text:             str  = Form(default=""),
        default_local_role:   str  = Form(default="employee"),
        default_printix_role: str  = Form(default="GUEST_USER"),
        send_invitation:      str  = Form(default=""),
        create_printix:       str  = Form(default=""),
        invite_lang:          str  = Form(default="de"),
    ):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        tc = t_ctx(request)

        want_invite  = bool(send_invitation)
        want_printix = bool(create_printix)

        # 1) CSV-Rohdaten einsammeln (Upload hat Vorrang)
        raw_bytes: bytes = b""
        if csv_file is not None:
            try:
                raw_bytes = await csv_file.read()
            except Exception:
                raw_bytes = b""
        raw_text = raw_bytes.decode("utf-8-sig", errors="replace") if raw_bytes else (csv_text or "")
        raw_text = raw_text.strip()
        if not raw_text:
            return templates.TemplateResponse("admin_user_bulk.html", {
                "request": request, "user": admin,
                "error": "Keine CSV-Daten übermittelt (Datei oder Text).",
                "results": None, "summary": None, **tc,
            })

        import csv as _csv
        import io as _io
        # Delimiter autodetect (fallback Komma)
        try:
            dialect = _csv.Sniffer().sniff(raw_text.splitlines()[0] + "\n", delimiters=",;\t")
        except Exception:
            dialect = _csv.excel
        reader = _csv.DictReader(_io.StringIO(raw_text), dialect=dialect)
        if not reader.fieldnames or "email" not in [(f or "").strip().lower() for f in reader.fieldnames]:
            return templates.TemplateResponse("admin_user_bulk.html", {
                "request": request, "user": admin,
                "error": "CSV-Header fehlt oder enthält keine Spalte 'email'.",
                "results": None, "summary": None, **tc,
            })

        # Header normalisieren (lowercase)
        def _norm(row: dict) -> dict:
            return {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}

        # 2) Tenant + Printix-Vorbereitung (nur einmal laden)
        tenant_full = None
        px_client = None
        mail_ready = False
        if want_invite or want_printix:
            try:
                from db import get_tenant_full_by_user_id
                tenant_full = get_tenant_full_by_user_id(admin["id"]) or {}
            except Exception:
                tenant_full = {}
            if want_invite:
                mail_ready = bool(tenant_full.get("mail_api_key") and tenant_full.get("mail_from"))
            if want_printix:
                try:
                    px_client = _make_printix_client(tenant_full)
                except Exception as e:
                    logger.error("Bulk-Import: Printix-Client init failed: %s", e)
                    px_client = None

        from db import (
            create_invited_user, username_exists, audit,
            LastAdminError,  # noqa: F401
        )
        results: list[dict] = []
        summary = {"ok": 0, "skipped": 0, "failed": 0, "mail_sent": 0, "printix_ok": 0}

        for idx, raw_row in enumerate(reader, start=2):  # Zeile 1 = Header
            row = _norm(raw_row)
            email = row.get("email", "")
            if not email or "@" not in email:
                results.append({"row": idx, "email": email, "username": "",
                                "status": "failed", "detail": tc["_"]("err_invalid_email")})
                summary["failed"] += 1
                continue

            username = row.get("username", "") or email.split("@", 1)[0]
            full_name = row.get("full_name", "")
            company   = row.get("company", "")
            local_role = (row.get("local_role", "") or default_local_role).lower().strip()
            if local_role not in ("admin", "employee", "user"):
                local_role = default_local_role
            if local_role == "user":
                local_role = "employee"
            px_role = (row.get("printix_role", "") or default_printix_role).upper().strip()
            if px_role not in ("USER", "GUEST_USER"):
                px_role = default_printix_role

            # Duplikat-Check
            try:
                if username_exists(username):
                    results.append({"row": idx, "email": email, "username": username,
                                    "status": "skipped", "detail": "Benutzername existiert bereits"})
                    summary["skipped"] += 1
                    continue
            except Exception as e:
                results.append({"row": idx, "email": email, "username": username,
                                "status": "failed", "detail": f"DB-Check: {e}"})
                summary["failed"] += 1
                continue

            temp_password = _generate_temp_password()
            created_user = None
            row_notes: list[str] = []
            try:
                created_user = create_invited_user(
                    username=username,
                    password=temp_password,
                    email=email,
                    full_name=full_name,
                    company=company,
                    invited_by_user_id=admin["id"],
                    invitation_language=invite_lang.strip(),
                    role_type=local_role,
                )
            except Exception as e:
                results.append({"row": idx, "email": email, "username": username,
                                "status": "failed", "detail": f"Anlage fehlgeschlagen: {e}"})
                summary["failed"] += 1
                continue

            # Einladungs-Mail (best-effort)
            if want_invite:
                if not mail_ready:
                    row_notes.append("Mail übersprungen (nicht konfiguriert)")
                else:
                    try:
                        from invite_mail import render_invitation_email
                        from reporting.mail_client import send_report
                        login_url = f"{_get_base_url(request)}/login"
                        subject, html_body = render_invitation_email(
                            lang=invite_lang.strip(),
                            full_name=full_name,
                            username=username,
                            password=temp_password,
                            login_url=login_url,
                        )
                        send_report(
                            recipients=[email],
                            subject=subject,
                            html_body=html_body,
                            api_key=tenant_full.get("mail_api_key", ""),
                            mail_from=tenant_full.get("mail_from", ""),
                            mail_from_name=tenant_full.get("mail_from_name", "") or "Printix Management Console",
                        )
                        summary["mail_sent"] += 1
                        row_notes.append("Mail gesendet")
                    except Exception as e:
                        logger.error("Bulk-Import Mail-Fehler für %s: %s", email, e)
                        row_notes.append(f"Mail-Fehler: {e}")

            # Printix-User anlegen (best-effort)
            if want_printix:
                if px_client is None:
                    row_notes.append("Printix übersprungen (kein Client)")
                else:
                    try:
                        resp = px_client.create_user(
                            email=email,
                            display_name=full_name or username,
                            role=px_role,
                        )
                        px_id = ""
                        users_block = (resp or {}).get("users") or []
                        if users_block:
                            px_id = (users_block[0] or {}).get("id", "") or ""
                        if px_id:
                            try:
                                from db import update_user
                                update_user(user_id=created_user["id"], printix_user_id=px_id)
                            except Exception as e:
                                logger.warning("Printix-ID Update fehlgeschlagen für %s: %s", email, e)
                        summary["printix_ok"] += 1
                        row_notes.append(f"Printix: {px_role}{' #' + px_id if px_id else ''}")
                    except Exception as e:
                        logger.error("Bulk-Import Printix-Fehler für %s: %s", email, e)
                        row_notes.append(f"Printix-Fehler: {e}")

            results.append({"row": idx, "email": email, "username": username,
                            "status": "ok",
                            "detail": "; ".join(row_notes) if row_notes else "angelegt"})
            summary["ok"] += 1

        try:
            audit(admin["id"], "bulk_import_users",
                  f"CSV-Import: {summary['ok']} ok, {summary['skipped']} skipped, {summary['failed']} failed",
                  object_type="user", object_id="")
        except Exception:
            pass

        return templates.TemplateResponse("admin_user_bulk.html", {
            "request": request, "user": admin,
            "error": None, "results": results, "summary": summary,
            **tc,
        })


    @app.post("/admin/users/{user_id}/disable")
    async def admin_disable(user_id: str, request: Request):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        if user_id == admin["id"]:
            return RedirectResponse("/admin/users", status_code=302)
        from db import LastAdminError
        from urllib.parse import quote_plus as _qp
        try:
            from db import set_user_status, audit
            set_user_status(user_id, "disabled")
            audit(admin["id"], "disable_user", f"User {user_id} deaktiviert", object_type="user", object_id=user_id)
        except LastAdminError as e:
            return RedirectResponse(f"/admin/users?err={_qp(str(e))}", status_code=302)
        except Exception as e:
            logger.error("Disable-Fehler: %s", e)
        return RedirectResponse("/admin/users", status_code=302)

    @app.post("/admin/users/{user_id}/delete")
    async def admin_delete_user(user_id: str, request: Request):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        if user_id == admin["id"]:
            return RedirectResponse("/admin/users", status_code=302)
        from db import LastAdminError
        from urllib.parse import quote_plus as _qp
        try:
            from db import delete_user, audit
            delete_user(user_id)
            audit(admin["id"], "delete_user", f"User {user_id} gelöscht", object_type="user", object_id=user_id)
        except LastAdminError as e:
            return RedirectResponse(f"/admin/users?err={_qp(str(e))}", status_code=302)
        except Exception as e:
            logger.error("Delete-Fehler: %s", e)
        return RedirectResponse("/admin/users", status_code=302)

    @app.get("/admin/users/create", response_class=HTMLResponse)
    async def admin_create_user_get(request: Request):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        return templates.TemplateResponse("admin_user_create.html", {
            "request": request, "user": admin,
            "saved": False, "error": None, **t_ctx(request),
        })

    @app.post("/admin/users/create", response_class=HTMLResponse)
    async def admin_create_user_post(
        request:   Request,
        username:  str = Form(...),
        password:  str = Form(...),
        password2: str = Form(...),
        email:     str = Form(default=""),
        full_name: str = Form(default=""),
        company:   str = Form(default=""),
        role_type: str = Form(default="employee"),
        status:    str = Form(default="approved"),
    ):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        tc = t_ctx(request)
        _  = tc["_"]
        error = None

        if len(username) < 3:
            error = _("reg_username_too_short")
        elif len(password) < 8:
            error = _("reg_password_too_short")
        elif password != password2:
            error = _("reg_pw_mismatch")
        else:
            try:
                from db import username_exists
                if username_exists(username):
                    error = _("reg_user_exists")
            except Exception as e:
                error = str(e)

        if error:
            return templates.TemplateResponse("admin_user_create.html", {
                "request": request, "user": admin,
                "saved": False, "error": error,
                "f_username": username, "f_email": email,
                "f_full_name": full_name, "f_company": company,
                "f_role_type": role_type, "f_status": status, **tc,
            })

        try:
            from db import create_user_admin, audit
            new_user = create_user_admin(
                username=username.strip(),
                password=password,
                email=email.strip(),
                role_type=role_type.strip(),
                status=status,
                full_name=full_name.strip(),
                company=company.strip(),
            )
            audit(admin["id"], "create_user", f"User '{username}' direkt angelegt (Status: {status})")
        except Exception as e:
            logger.error("Create-User-Fehler: %s", e)
            return templates.TemplateResponse("admin_user_create.html", {
                "request": request, "user": admin,
                "saved": False, "error": str(e), **tc,
            })

        return templates.TemplateResponse("admin_user_create.html", {
            "request": request, "user": admin,
            "saved": True, "error": None,
            "created_username": new_user["username"], **tc,
        })

    @app.get("/admin/users/{user_id}/edit", response_class=HTMLResponse)
    async def admin_edit_user_get(user_id: str, request: Request):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            from db import get_user_by_id
            target = get_user_by_id(user_id)
        except Exception:
            target = None
        if not target:
            return RedirectResponse("/admin/users", status_code=302)
        return templates.TemplateResponse("admin_user_edit.html", {
            "request": request, "user": admin, "target": target,
            "saved": False, "error": None, **t_ctx(request),
        })

    @app.post("/admin/users/{user_id}/edit", response_class=HTMLResponse)
    async def admin_edit_user_post(
        user_id:         str,
        request:         Request,
        username:        str = Form(...),
        email:           str = Form(default=""),
        full_name:       str = Form(default=""),
        company:         str = Form(default=""),
        role_type:       str = Form(default="employee"),
        status:          str = Form(default="approved"),
        printix_user_id: str = Form(default=""),
    ):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        tc = t_ctx(request)

        from db import LastAdminError
        try:
            from db import update_user, username_exists, get_user_by_id, audit
            if username_exists(username, exclude_id=user_id):
                target = get_user_by_id(user_id)
                return templates.TemplateResponse("admin_user_edit.html", {
                    "request": request, "user": admin, "target": target,
                    "saved": False, "error": tc["_"]("reg_user_exists"), **tc,
                })
            update_user(
                user_id=user_id,
                username=username.strip(),
                email=email.strip(),
                full_name=full_name.strip(),
                company=company.strip(),
                role_type=role_type.strip(),
                status=status,
                printix_user_id=printix_user_id.strip(),
            )
            audit(admin["id"], "edit_user", f"User {user_id} bearbeitet", object_type="user", object_id=user_id)
            target = get_user_by_id(user_id)
        except LastAdminError as e:
            from db import get_user_by_id
            target = get_user_by_id(user_id)
            return templates.TemplateResponse("admin_user_edit.html", {
                "request": request, "user": admin, "target": target,
                "saved": False, "error": str(e), **tc,
            })
        except Exception as e:
            logger.error("Edit-Fehler: %s", e)
            return templates.TemplateResponse("admin_user_edit.html", {
                "request": request, "user": admin,
                "target": {"id": user_id, "username": username, "email": email},
                "saved": False, "error": str(e), **tc,
            })

        return templates.TemplateResponse("admin_user_edit.html", {
            "request": request, "user": admin, "target": target,
            "saved": True, "error": None, **tc,
        })

    @app.post("/admin/users/{user_id}/resolve-printix-id", response_class=JSONResponse)
    async def admin_resolve_printix_id(user_id: str, request: Request):
        """Versucht die Printix-User-ID für einen lokalen User automatisch zu
        ermitteln — durchsucht die jüngsten Printix-Jobs nach einem Match auf
        die E-Mail-Adresse. Nützlich wenn der User ein System-Manager ist,
        der über list_users?role=USER,GUEST_USER nicht sichtbar ist.
        """
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return JSONResponse({"ok": False, "error": "not_authorized"}, status_code=401)
        try:
            from db import get_user_by_id, get_tenant_full_by_user_id, update_user, audit
            target = get_user_by_id(user_id)
            if not target:
                return JSONResponse({"ok": False, "error": "user_not_found"}, status_code=404)
            target_email = (target.get("email") or "").strip().lower()
            if not target_email:
                return JSONResponse({"ok": False, "error": "no_email",
                    "message": "Lokaler User hat keine E-Mail-Adresse."})

            # Tenant-Credentials des Admin-Users nehmen (derzeit eingeloggt)
            tenant = get_tenant_full_by_user_id(admin["id"])
            if not tenant or not (tenant.get("print_client_id") or tenant.get("shared_client_id")):
                return JSONResponse({"ok": False, "error": "no_print_creds",
                    "message": "Print-API-Credentials nicht konfiguriert."})

            client = _make_printix_client(tenant)
            # Strategie 1: list_users?query=<email> — erfasst normale USER/GUEST.
            # Printix liefert die Liste mal unter "users", mal unter "content"
            # (je nach API-Version/Endpoint-Variante) — beide Keys pruefen,
            # sonst uebersieht der Match alle Treffer in neueren Instanzen.
            try:
                search_result = client.list_users(role="USER,GUEST_USER",
                                                   query=target_email, page_size=20)
                users = []
                if isinstance(search_result, dict):
                    users = (search_result.get("users")
                             or search_result.get("content")
                             or [])
                elif isinstance(search_result, list):
                    users = search_result
                logger.info("Auto-resolve: list_users query=%s → %d Treffer",
                            target_email, len(users))
                for u in users:
                    if (u.get("email") or "").lower() == target_email:
                        found_id = u.get("id", "") or u.get("userId", "")
                        if not found_id:
                            # Fallback: ID aus _links.self.href ziehen
                            href = (((u.get("_links") or {}).get("self") or {})
                                    .get("href", ""))
                            if href:
                                found_id = href.rstrip("/").split("/")[-1]
                        if found_id:
                            update_user(user_id=user_id, printix_user_id=found_id)
                            audit(admin["id"], "resolve_printix_id",
                                  f"Printix-User-ID {found_id} für {target.get('username')}",
                                  object_type="user", object_id=user_id)
                            return JSONResponse({"ok": True, "printix_user_id": found_id,
                                "source": "list_users", "email": target_email})
            except Exception as e1:
                logger.warning("Auto-resolve list_users fehlgeschlagen für %s: %s",
                               target_email, e1)

            # Strategie 1b: Full-Scan via list_all_users — kein Query, aber
            # garantiert alle Seiten. Deckt den Fall ab, dass Printix den
            # query-Parameter ignoriert (manche API-Versionen) oder nur
            # Name-Substrings matcht und nicht E-Mail.
            try:
                all_users = client.list_all_users()
                logger.info("Auto-resolve: list_all_users → %d Eintraege", len(all_users))
                for u in all_users:
                    if (u.get("email") or "").lower() == target_email:
                        found_id = u.get("id", "") or u.get("userId", "")
                        if not found_id:
                            href = (((u.get("_links") or {}).get("self") or {})
                                    .get("href", ""))
                            if href:
                                found_id = href.rstrip("/").split("/")[-1]
                        if found_id:
                            update_user(user_id=user_id, printix_user_id=found_id)
                            audit(admin["id"], "resolve_printix_id",
                                  f"Printix-User-ID {found_id} für {target.get('username')} "
                                  f"(via full scan)",
                                  object_type="user", object_id=user_id)
                            return JSONResponse({"ok": True, "printix_user_id": found_id,
                                "source": "list_all_users", "email": target_email})
            except Exception as e1b:
                logger.warning("Auto-resolve list_all_users fehlgeschlagen für %s: %s",
                               target_email, e1b)

            # Strategie 2: jüngste Print-Jobs durchsuchen nach ownerEmail match
            try:
                jobs_data = client.list_print_jobs(size=100)
                jobs = []
                if isinstance(jobs_data, dict):
                    jobs = jobs_data.get("jobs") or jobs_data.get("content") or []
                elif isinstance(jobs_data, list):
                    jobs = jobs_data
                for j in jobs:
                    oe = (j.get("ownerEmail") or "").lower()
                    if oe == target_email:
                        oid = j.get("ownerId") or ""
                        if oid:
                            update_user(user_id=user_id, printix_user_id=oid)
                            audit(admin["id"], "resolve_printix_id",
                                  f"Printix-User-ID {oid} für {target.get('username')} "
                                  f"aus Print-Job ermittelt",
                                  object_type="user", object_id=user_id)
                            return JSONResponse({"ok": True, "printix_user_id": oid,
                                "source": "print_jobs", "email": target_email})
            except Exception as e2:
                logger.debug("print_jobs-Strategie: %s", e2)

            # Strategie 3: cached_printix_users — deckt Manager-Rollen ab,
            # die die list_users-API nicht zurueckgibt (System/Site/Kiosk
            # Manager). Tenant-Owner werden beim Setup synthetisch mit ihrer
            # echten Printix-UUID eingetragen.
            try:
                from cloudprint.printix_cache_db import find_printix_user_by_identity
                px_user = find_printix_user_by_identity(target_email)
                if not px_user:
                    # Fallback: nur der Local-Part (vor dem @)
                    local_part = target_email.split("@")[0] if "@" in target_email else ""
                    if local_part:
                        px_user = find_printix_user_by_identity(local_part)
                if px_user and px_user.get("printix_user_id"):
                    found_id = px_user["printix_user_id"]
                    # mgr:-Praefix = synthetischer SYSTEM_MANAGER-Eintrag.
                    # Solche IDs werden von der Printix Card-API abgelehnt
                    # ("Failed to convert 'user' with value: 'mgr:...'").
                    # Fuer Cards-Funktionalitaet braucht der User eine echte
                    # User-UUID — Manager koennen aktuell keine Karten anlegen.
                    if found_id.startswith("mgr:") or ":" in found_id:
                        logger.warning(
                            "Auto-resolve: cache hit für %s liefert Manager-ID %s — "
                            "nicht fuer Cards geeignet, ueberspringe",
                            target_email, found_id,
                        )
                        # Nicht speichern, nicht returnen — zu den finalen
                        # not_found-Fehlermeldung durchfallen lassen.
                    else:
                        logger.info("Auto-resolve: cache hit für %s → %s (role=%s)",
                                    target_email, found_id, px_user.get("role"))
                        update_user(user_id=user_id, printix_user_id=found_id)
                        audit(admin["id"], "resolve_printix_id",
                              f"Printix-User-ID {found_id} für {target.get('username')} "
                              f"(aus cached_printix_users)",
                              object_type="user", object_id=user_id)
                        return JSONResponse({"ok": True, "printix_user_id": found_id,
                            "source": "cached_printix_users", "email": target_email})
            except Exception as e3:
                logger.warning("Auto-resolve cache-Lookup fehlgeschlagen für %s: %s",
                               target_email, e3)

            return JSONResponse({"ok": False, "error": "not_found",
                "message": (
                    "UUID konnte nicht automatisch ermittelt werden. "
                    f"Suche mit E-Mail '{target_email}' fand keine Treffer in "
                    "list_users(), den letzten 100 Print-Jobs oder im lokalen "
                    "Printix-User-Cache. Manager-Rollen (System/Site/Kiosk) "
                    "sind über die Printix-API nicht abrufbar — bitte UUID "
                    "manuell aus der Printix-Admin-URL kopieren "
                    "(https://manager.printix.net/users/<UUID>)."
                )})
        except Exception as e:
            logger.error("resolve_printix_id error: %s", e)
            return JSONResponse({"ok": False, "error": "internal", "message": str(e)[:200]},
                                status_code=500)

    @app.post("/admin/users/{user_id}/reset-password")
    async def admin_reset_password(
        user_id:      str,
        request:      Request,
        new_password: str = Form(...),
    ):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            from db import reset_user_password, audit
            reset_user_password(user_id, new_password)
            audit(admin["id"], "reset_password", f"Passwort für User {user_id} zurückgesetzt", object_type="user", object_id=user_id)
        except Exception as e:
            logger.error("Reset-PW-Fehler: %s", e)
        return RedirectResponse(f"/admin/users/{user_id}/edit?pw_saved=1", status_code=302)


    @app.get("/admin/audit", response_class=HTMLResponse)
    async def admin_audit(request: Request):
        # v0.7.14: Perf-Instrumentierung — Setting `perf_logs_enabled=1` schaltet
        # `dt_total=Xms dt_db=Xms` Logs scharf. Default off.
        import time as _t
        _t0 = _t.monotonic()
        _dt_db_ms = 0.0
        try:
            from db import perf_logs_enabled as _perf_on
            _perf = _perf_on()
        except Exception:
            _perf = False
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)

        # v6.7.115: Filter-Controls + Pagination.
        qp = request.query_params
        f_user = (qp.get("user") or "").strip()
        f_action = (qp.get("action") or "").strip()
        f_from_date = (qp.get("from_date") or "").strip()
        f_to_date = (qp.get("to_date") or "").strip()
        f_from_time = (qp.get("from_time") or "").strip()
        f_to_time = (qp.get("to_time") or "").strip()
        f_severity = (qp.get("severity") or "").strip().lower()
        if f_severity not in ("info", "warning", "error"):
            f_severity = ""
        f_source = (qp.get("source") or "").strip()
        try:
            page = max(1, int(qp.get("page") or "1"))
        except Exception:
            page = 1
        PAGE_SIZE = 200

        # Datum/Zeit-Fenster bauen (ISO-Strings, UTC-agnostisch — created_at
        # ist als ISO-String gespeichert, lexikographisch sortierbar).
        def _combine(date_s: str, time_s: str, default_time: str) -> str:
            if not date_s:
                return ""
            t = time_s if time_s else default_time
            # Sicherheits-Trim auf erwartetes Format
            return f"{date_s}T{t}:00"

        start_iso = _combine(f_from_date, f_from_time, "00:00")
        end_iso = _combine(f_to_date, f_to_time, "23:59")

        entries = []
        distinct_actions = []
        distinct_sources: list = []
        total_count = 0
        total_is_approx = False
        try:
            from db import _conn
            where = []
            params: list = []
            if f_user:
                where.append("(LOWER(IFNULL(u.username,'')) LIKE ? OR LOWER(IFNULL(u.email,'')) LIKE ?)")
                like = f"%{f_user.lower()}%"
                params.extend([like, like])
            if f_action:
                where.append("a.action = ?")
                params.append(f_action)
            if start_iso:
                where.append("a.created_at >= ?")
                params.append(start_iso)
            if end_iso:
                where.append("a.created_at <= ?")
                params.append(end_iso + ":59")
            if f_source:
                # v0.7.4: Source-Filter — bevorzugt json_extract (SQLite ≥3.38),
                # Fallback LIKE auf das details-Feld.
                where.append(
                    "(json_extract(a.details, '$.source') = ? "
                    "OR a.details LIKE ?)"
                )
                params.extend([f_source, f'%"source": "{f_source}"%'])
            where_sql = (" WHERE " + " AND ".join(where)) if where else ""
            offset = (page - 1) * PAGE_SIZE
            # v0.7.14: COUNT(*) auf grossem audit_log (mit LEFT JOIN users +
            # ggf. LIKE und json_extract) war auf Azure-Files-SMB der
            # /admin/audit-Bottleneck (~2 min). Strategie:
            #   1) Den JOIN nur bauen, wenn der User-Filter ihn braucht
            #      (alle anderen Filter laufen auf audit_log alleine).
            #   2) COUNT auf 1001 begrenzen via Subquery — der Template
            #      zeigt "1000+" statt einer harten Zahl. Bei Filter mit
            #      wenigen Treffern bleibt die Zahl exakt.
            need_join = bool(f_user)
            join_sql = " LEFT JOIN users u ON u.id = a.user_id" if need_join else ""
            COUNT_CAP = 1000
            _t_db0 = _t.monotonic()
            with _conn() as conn:
                try:
                    distinct_actions = [
                        r["action"] for r in conn.execute(
                            "SELECT DISTINCT action FROM audit_log "
                            "WHERE action <> '' ORDER BY action ASC"
                        ).fetchall()
                    ]
                except Exception:
                    distinct_actions = []
                # v0.7.14: distinct_sources via json_extract scannt die ganze
                # Tabelle. Auf einer grossen audit_log dauert das viele
                # Sekunden — und das Drop-Down hat in der Praxis nur eine
                # Handvoll Werte. Hartkodierter Fallback ist sauber genug.
                distinct_sources = ["ios_app", "web", "email", "desktop", "mcp"]
                try:
                    cap_row = conn.execute(
                        f"SELECT COUNT(*) AS c FROM ("
                        f"  SELECT 1 FROM audit_log a{join_sql}{where_sql} "
                        f"  LIMIT ?"
                        f")",
                        tuple(params) + (COUNT_CAP + 1,),
                    ).fetchone()
                    total_count = int(cap_row["c"]) if cap_row else 0
                except Exception:
                    total_count = 0
                total_is_approx = total_count > COUNT_CAP
                rows = conn.execute(
                    f"SELECT a.*, u.username, u.email, u.full_name "
                    f"FROM audit_log a "
                    f"LEFT JOIN users u ON u.id = a.user_id{where_sql} "
                    f"ORDER BY a.created_at DESC LIMIT ? OFFSET ?",
                    tuple(params) + (PAGE_SIZE, offset),
                ).fetchall()
                entries = [dict(r) for r in rows]
            _dt_db_ms = (_t.monotonic() - _t_db0) * 1000.0
        except Exception as e:
            logger.warning("admin_audit query failed: %s", e)
            entries = []
            total_is_approx = False

        # v0.7.4: Severity-Derivation + Display-User-Aufbereitung pro Entry.
        _ERROR_TOKENS = ("_failed", "_error", "denied", "revoked",
                         "oid_mismatch", "unauthorized")
        _WARN_TOKENS  = ("_warning", "_expired", "disabled", "removed")
        for e in entries:
            act = (e.get("action") or "").lower()
            if any(tok in act for tok in _ERROR_TOKENS):
                e["severity"] = "error"
            elif any(tok in act for tok in _WARN_TOKENS):
                e["severity"] = "warning"
            else:
                e["severity"] = "info"
            fn    = (e.get("full_name") or "").strip()
            uname = (e.get("username") or "").strip()
            email = (e.get("email") or "").strip()
            uid   = (e.get("user_id") or "")
            e["display_user"] = fn or uname or email or (uid[:8] + "…" if uid else "—")
            e["filter_user"]  = uname or email or fn  # Klick-Filter-Wert
            # Source aus JSON-Details extrahieren (best-effort).
            src = ""
            det = e.get("details") or ""
            if det and '"source"' in det:
                try:
                    import json as _json
                    parsed = _json.loads(det)
                    if isinstance(parsed, dict):
                        src = str(parsed.get("source") or "").strip()
                except Exception:
                    # Regex-Fallback fuer nicht-JSON-Details
                    import re as _re
                    m = _re.search(r'"source"\s*:\s*"([^"]+)"', det)
                    if m:
                        src = m.group(1).strip()
            e["source"] = src
        if f_severity:
            entries = [e for e in entries if e.get("severity") == f_severity]

        has_prev = page > 1
        has_next = (page * PAGE_SIZE) < total_count

        # Query-String für Pagination-Links rekonstruieren (ohne page).
        from urllib.parse import urlencode
        base_qs = {
            "user": f_user, "action": f_action,
            "from_date": f_from_date, "to_date": f_to_date,
            "from_time": f_from_time, "to_time": f_to_time,
            "severity": f_severity,
            "source": f_source,
        }
        base_qs = {k: v for k, v in base_qs.items() if v}

        import math as _math
        total_pages = max(1, _math.ceil(total_count / PAGE_SIZE)) if total_count else 1

        _dt_total_ms = (_t.monotonic() - _t0) * 1000.0
        # v0.7.19: SLOW-LOG immer aktiv. Wenn ein Request laenger als 500ms
        # dauert, loggen wir die Breakdown unconditional. Damit sehen wir
        # bei 2-Min-Hangs sofort welche DB-Query der Buegel ist.
        if _perf or _dt_total_ms > 500:
            logger.warning(
                "perf admin_audit dt_total=%.0fms dt_db=%.0fms rows=%d "
                "total=%s%d filters=user=%s action=%s severity=%s source=%s",
                _dt_total_ms, _dt_db_ms, len(entries),
                ">=" if total_is_approx else "", total_count,
                bool(f_user), bool(f_action), bool(f_severity), bool(f_source),
            )
        return templates.TemplateResponse("admin_audit.html", {
            "request": request, "user": user, "entries": entries,
            "distinct_actions": distinct_actions,
            "distinct_sources": distinct_sources,
            "total_is_approx": total_is_approx,
            "filter_user": f_user, "filter_action": f_action,
            "filter_from_date": f_from_date, "filter_to_date": f_to_date,
            "filter_from_time": f_from_time, "filter_to_time": f_to_time,
            "filter_severity": f_severity,
            "filter_source": f_source,
            "total_pages": total_pages,
            "page": page, "page_size": PAGE_SIZE, "total_count": total_count,
            "has_prev": has_prev, "has_next": has_next,
            "prev_qs": urlencode({**base_qs, "page": page - 1}) if has_prev else "",
            "next_qs": urlencode({**base_qs, "page": page + 1}) if has_next else "",
            **t_ctx(request)
        })


    # ── v0.7.11: API-Trace (Outbound-API-Call-Debugger) ───────────────────
    @app.get("/admin/api-trace", response_class=HTMLResponse)
    async def admin_api_trace(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        qp = request.query_params
        f_component = (qp.get("component") or "").strip()
        f_method = (qp.get("method") or "").strip().upper()
        f_status = (qp.get("status") or "").strip().lower()
        if f_status not in ("", "2xx", "3xx", "4xx", "5xx", "err"):
            f_status = ""
        f_search = (qp.get("q") or "").strip()
        try:
            page = max(1, int(qp.get("page") or "1"))
        except Exception:
            page = 1
        PAGE_SIZE = 100
        # v0.7.13: trace_active separat berechnen — vorher hat ein
        # Exception in list_trace_entries (z.B. leere Tabelle / fehlender
        # Spalten-Mismatch) trace_active auf False gesetzt, OBWOHL das
        # Setting in der DB sauber auf '1' stand. Das war der Grund warum
        # der Toggle scheinbar nichts tat.
        try:
            from api_trace import is_enabled
            trace_active = is_enabled()
        except Exception as e:
            logger.warning("api_trace.is_enabled() failed: %s", e)
            trace_active = False
        try:
            from api_trace import (
                list_trace_entries, list_distinct_components,
            )
            entries, total_count = list_trace_entries(
                component=f_component, method=f_method,
                status_class=f_status, search=f_search,
                page=page, page_size=PAGE_SIZE,
            )
            distinct_components = list_distinct_components()
        except Exception as e:
            logger.warning("admin_api_trace query failed: %s", e)
            entries, total_count = [], 0
            distinct_components = []

        # Severity-Klasse aus Status ableiten (fuer Tabellenfarbe).
        for e in entries:
            sc = int(e.get("status_code") or 0)
            if sc == 0:
                e["sev"] = "err"
            elif 200 <= sc < 300:
                e["sev"] = "ok"
            elif 300 <= sc < 400:
                e["sev"] = "info"
            elif 400 <= sc < 500:
                e["sev"] = "warn"
            else:
                e["sev"] = "err"

        import math as _math
        total_pages = max(1, _math.ceil(total_count / PAGE_SIZE)) if total_count else 1
        has_prev = page > 1
        has_next = (page * PAGE_SIZE) < total_count
        from urllib.parse import urlencode
        base_qs = {
            "component": f_component, "method": f_method,
            "status": f_status, "q": f_search,
        }
        base_qs = {k: v for k, v in base_qs.items() if v}
        return templates.TemplateResponse("admin_api_trace.html", {
            "request": request, "user": user,
            "entries": entries,
            "distinct_components": distinct_components,
            "trace_active": trace_active,
            "filter_component": f_component, "filter_method": f_method,
            "filter_status": f_status, "filter_search": f_search,
            "page": page, "page_size": PAGE_SIZE,
            "total_count": total_count, "total_pages": total_pages,
            "has_prev": has_prev, "has_next": has_next,
            "prev_qs": urlencode({**base_qs, "page": page - 1}) if has_prev else "",
            "next_qs": urlencode({**base_qs, "page": page + 1}) if has_next else "",
            "active_page": "admin_api_trace",
            **t_ctx(request),
        })

    @app.get("/admin/api-trace/{entry_id}.json")
    async def admin_api_trace_detail(entry_id: int, request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            from api_trace import get_trace_entry
            row = get_trace_entry(entry_id)
            if not row:
                return JSONResponse({"error": "not_found"}, status_code=404)
            return JSONResponse(row)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/admin/api-trace/clear")
    async def admin_api_trace_clear(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            from api_trace import clear_all
            n = clear_all()
            try:
                from db import audit
                audit(user["id"], "admin_api_trace_clear", f"deleted={n}")
            except Exception:
                pass
        except Exception as e:
            logger.warning("admin_api_trace_clear failed: %s", e)
        return RedirectResponse("/admin/api-trace", status_code=302)

    # v0.7.29: GET entfernt — Toggle-Endpoint war via Img-Tag CSRF-fest
    # nur durch SameSite=Lax. Jetzt strikt POST.
    @app.post("/admin/api-trace/toggle")
    async def admin_api_trace_toggle(
        request: Request,
        enabled: str = Form(default=""),
    ):
        # v0.7.12: GET-Fallback fuer Debugging + klares Logging damit
        # wir sehen ob der Toggle ankommt; plus visuelles ?ok=/?err=
        # Feedback in der URL.
        user = get_session_user(request)
        peer = (request.client.host if request.client else "?")
        if not user:
            logger.warning("api_trace_toggle DENY: no session — peer=%s", peer)
            return RedirectResponse("/login", status_code=303)
        if not user.get("is_admin"):
            logger.warning(
                "api_trace_toggle DENY: user=%s not admin (is_admin=%s)",
                user.get("username"), user.get("is_admin"),
            )
            return RedirectResponse(
                "/admin/api-trace?err=not_admin", status_code=303,
            )
        if request.method == "GET":
            enabled = request.query_params.get("enabled", "")
        try:
            from db import set_setting, get_setting, audit
            new_val = "1" if enabled else "0"
            set_setting("api_trace_enabled", new_val)
            check = get_setting("api_trace_enabled", "0")
            audit(user["id"], "admin_api_trace_toggle", f"enabled={new_val}")
            logger.info(
                "api_trace_toggle OK: user=%s set=%s read_back=%s",
                user.get("username"), new_val, check,
            )
            ok = "trace_on" if new_val == "1" else "trace_off"
            return RedirectResponse(
                f"/admin/api-trace?ok={ok}", status_code=303,
            )
        except Exception as e:
            logger.exception(
                "api_trace_toggle FAIL: user=%s err=%s",
                user.get("username"), e,
            )
            return RedirectResponse(
                "/admin/api-trace?err=save_failed", status_code=303,
            )

    # v0.7.29: strikt POST — siehe api-trace/toggle.
    @app.post("/admin/perf-logs/toggle")
    async def admin_perf_logs_toggle(
        request: Request,
        enabled: str = Form(default=""),
    ):
        """v0.7.16: 1-Klick-Toggle fuer perf_logs_enabled Setting.

        Aus dem Browser oder einem Bookmark:
            /admin/perf-logs/toggle?enabled=1   (an)
            /admin/perf-logs/toggle?enabled=0   (aus)
            /admin/perf-logs/toggle             (auch aus)

        Damit wird der dt_*-Marker-Output in Admin-Handler-Logs an/aus
        geschaltet (perf_logs_enabled Setting → db.perf_logs_enabled()).
        """
        user = get_session_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        if not user.get("is_admin"):
            return RedirectResponse("/admin?err=not_admin", status_code=303)
        if request.method == "GET":
            enabled = request.query_params.get("enabled", "")
        try:
            from db import set_setting, get_setting, audit
            new_val = "1" if enabled else "0"
            set_setting("perf_logs_enabled", new_val)
            check = get_setting("perf_logs_enabled", "0")
            audit(user["id"], "admin_perf_logs_toggle", f"enabled={new_val}")
            logger.info(
                "perf_logs_toggle OK: user=%s set=%s read_back=%s",
                user.get("username"), new_val, check,
            )
            return PlainTextResponse(
                f"Performance-Logs jetzt {'AN' if new_val == '1' else 'AUS'} "
                f"(read_back={check}). "
                f"Toggle erneut: /admin/perf-logs/toggle?enabled="
                f"{'0' if new_val == '1' else '1'}\n",
            )
        except Exception as e:
            logger.exception("perf_logs_toggle FAIL: %s", e)
            return PlainTextResponse(
                f"FEHLER beim Speichern: {e}\n", status_code=500,
            )

    @app.get("/admin/printix-submit-selftest", response_class=JSONResponse)
    async def admin_printix_submit_selftest(request: Request):
        """v0.7.21: Self-Test fuer Printix-Submit. Probiert mehrere Body-
        Varianten gegen den echten Printix-Endpoint und gibt die Antworten
        side-by-side zurueck.

        Nutzt die Server-Credentials (kein Bearer-Token im URL noetig).
        Antwort ist JSON mit fuer jede Variante: HTTP-Status, ErrorID,
        Response-Body (gekuerzt) und URL.
        """
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return JSONResponse({"error": "admin_required"}, status_code=403)

        # Optional: Queue-ID per Query-Param override; sonst Default-Queue.
        target_queue = (request.query_params.get("queue") or "").strip()
        owner_email_override = (request.query_params.get("email") or "").strip()

        from db import get_tenant_full_by_user_id, _find_tenant_owner_user_id
        from cloudprint.db_extensions import get_global_default_queue
        owner_uid = _find_tenant_owner_user_id()
        tenant = (get_tenant_full_by_user_id(user["id"])
                  or (get_tenant_full_by_user_id(owner_uid) if owner_uid else None))
        if not tenant:
            return JSONResponse(
                {"error": "no_tenant_configured"}, status_code=400,
            )

        if not target_queue:
            qid, _ = get_global_default_queue()
            target_queue = qid
        if not target_queue:
            return JSONResponse({
                "error": "no_target_queue",
                "hint": "Setze ?queue=<uuid> oder konfiguriere Default-Queue",
            }, status_code=400)

        owner_email = owner_email_override or (user.get("email") or "")
        if not owner_email:
            owner_email = "selftest@example.com"

        # Printix-Client aus Tenant-Config
        try:
            from printix_client import PrintixClient, PrintixAPIError
            client = PrintixClient(
                tenant_id=tenant["printix_tenant_id"],
                print_client_id=tenant.get("print_client_id", ""),
                print_client_secret=tenant.get("print_client_secret", ""),
                shared_client_id=tenant.get("shared_client_id", ""),
                shared_client_secret=tenant.get("shared_client_secret", ""),
            )
        except Exception as e:
            return JSONResponse(
                {"error": f"client_init_failed: {e}"}, status_code=500,
            )

        # Printer-ID resolver
        try:
            import re as _re
            printers_data = client.list_printers(size=200)
            raw_list = printers_data.get("printers", []) if isinstance(printers_data, dict) else []
            if not raw_list and isinstance(printers_data, dict):
                raw_list = (printers_data.get("_embedded") or {}).get("printers", [])
            printer_id = ""
            for p in raw_list:
                href = (p.get("_links") or {}).get("self", {}).get("href", "")
                m = _re.search(r"/printers/([^/]+)/queues/([^/?]+)", href)
                if m and m.group(2) == target_queue:
                    printer_id = m.group(1)
                    break
            if not printer_id:
                return JSONResponse({
                    "error": "queue_not_found_in_printix",
                    "queue": target_queue,
                    "scanned_printers": len(raw_list),
                }, status_code=400)
        except Exception as e:
            return JSONResponse(
                {"error": f"list_printers_failed: {e}"}, status_code=500,
            )

        # 6 Submit-Varianten testen
        variants = [
            ("v1_full_body_release_false", dict(
                user=owner_email, pdl="PDF", release_immediately=False,
                color=False, duplex="NONE", copies=1,
            )),
            ("v2_full_body_release_true", dict(
                user=owner_email, pdl="PDF", release_immediately=True,
                color=False, duplex="NONE", copies=1,
            )),
            ("v3_minimal_release_false", dict(
                user=owner_email, pdl="PDF", release_immediately=False,
            )),
            ("v4_minimal_release_true", dict(
                user=owner_email, pdl="PDF", release_immediately=True,
            )),
            ("v5_no_user_no_pdl", dict(
                release_immediately=False,
            )),
            ("v6_no_user_full_body", dict(
                pdl="PDF", release_immediately=False,
                color=False, duplex="NONE", copies=1,
            )),
        ]

        results = []
        from printix_client import PrintixAPIError as _PxErr
        for name, kwargs in variants:
            entry = {"variant": name, "kwargs": kwargs}
            try:
                r = client.submit_print_job(
                    printer_id=printer_id,
                    queue_id=target_queue,
                    title=f"selftest-{name}.pdf",
                    **kwargs,
                )
                entry["status"] = "ok"
                entry["job_id"] = (r.get("job", r) if isinstance(r, dict) else {}).get("id", "")
                entry["has_upload_url"] = bool(r.get("uploadUrl") if isinstance(r, dict) else False)
            except _PxErr as pe:
                entry["status"] = "error"
                entry["http"] = pe.status_code
                entry["error_id"] = getattr(pe, "error_id", "")
                entry["message"] = str(pe.message)[:300]
            except Exception as e:
                entry["status"] = "exception"
                entry["error"] = f"{type(e).__name__}: {e}"[:300]
            results.append(entry)

        return JSONResponse({
            "tenant_printix_id": tenant["printix_tenant_id"],
            "printer_id": printer_id,
            "queue_id": target_queue,
            "owner_email": owner_email,
            "variants_tested": len(results),
            "results": results,
            "hint": "Variante mit status=ok ist die richtige Combo. "
                    "Wenn ALLE failen -> Auth/Tenant/Lizenz-Problem.",
        })


    def _admin_settings_ctx(
        request,
        user,
        saved=False,
        error=None,
        auto_setup_success=False,
        backup_success=None,
        backup_error=None,
        restore_success=None,
        license_status=None,
    ):
        """Baut den Template-Kontext für admin_settings.html."""
        from urllib.parse import urlparse
        try:
            from backup_manager import list_backups
            backups = list_backups()
        except Exception:
            backups = []
        try:
            from db import get_setting
            public_url = get_setting("public_url", "")
        except Exception:
            public_url = ""
        if not public_url:
            public_url = os.environ.get("MCP_PUBLIC_URL", "")
        # v0.5.0: Queue-Defaults laden (Global-Default + Override-Toggle +
        # Queue-Picker-Optionen). Queue-Picker zeigt nur an wenn Printix
        # konfiguriert ist (sonst leeres Text-Input als Fallback).
        try:
            from cloudprint.db_extensions import (
                get_global_default_queue, is_user_queue_override_allowed,
            )
            _gq_id, _gq_label = get_global_default_queue()
            current_global_default_queue_id    = _gq_id
            current_global_default_queue_label = _gq_label
            current_allow_user_override        = is_user_queue_override_allowed()
        except Exception:
            current_global_default_queue_id    = ""
            current_global_default_queue_label = ""
            current_allow_user_override        = False
        # v0.7.26: Delegation-Druck-Toggle aus Settings.
        try:
            from db import get_setting as _gs_del
            current_delegation_allowed = (
                (_gs_del("delegation_print_allowed", "0") or "0").strip()
                in ("1", "true", "yes", "on")
            )
        except Exception:
            current_delegation_allowed = False
        queues_for_picker: list = []

        # v0.4.5: Tenant-Record laden fuer Printix-Credentials-Editor.
        # Wir zeigen die Tenant-ID + Client-IDs im Klartext (sind keine
        # Secrets — die Tenant-ID hat eh jeder im Printix-Admin der die
        # Domain kennt). Die Secret-Felder bleiben leer; ein leer-gelassenes
        # Feld bedeutet im POST-Handler "unveraendert lassen".
        try:
            from db import get_tenant_full_by_user_id, _find_tenant_owner_user_id
            tenant_full = get_tenant_full_by_user_id(user["id"])
            if not tenant_full:
                oid = _find_tenant_owner_user_id()
                if oid:
                    tenant_full = get_tenant_full_by_user_id(oid)
        except Exception:
            tenant_full = None
        # v4.5.0: Capture-spezifische URL (optional — nur wenn eigene Domain)
        try:
            from db import get_setting as _gs
            capture_public_url = _gs("capture_public_url", "")
        except Exception:
            capture_public_url = ""
        # v6.5.0: IPPS (Cloud Print über HTTPS/IPP-Protokoll)
        # v6.6.0: LPR komplett entfernt — IPPS ist der einzige Cloud-Print-Eingang.
        try:
            from db import get_setting as _gs2
            ipps_public_url = _gs2("ipps_public_url", "")
            ipps_port       = _gs2("ipps_port", "") or os.environ.get("IPP_PORT", "8080")
        except Exception:
            ipps_public_url = ""
            ipps_port       = os.environ.get("IPP_PORT", "8080")
        parsed_ipps = urlparse(ipps_public_url) if ipps_public_url else None
        ipps_public_host = (parsed_ipps.hostname if parsed_ipps else "") or ""

        # v6.7.25: Globales Mail-Fallback. API-Key wird verschlüsselt
        # gespeichert — wir geben nur ein has_key-Flag ans Template, nie den
        # Klartext (gleicher Schutz wie beim entra_client_secret).
        try:
            has_global_mail_key   = bool(_gs2("global_mail_api_key", ""))
            global_mail_from      = _gs2("global_mail_from", "")
            global_mail_from_name = _gs2("global_mail_from_name", "")
        except Exception:
            has_global_mail_key = False
            global_mail_from = global_mail_from_name = ""
        # v0.7.0: Mail-Provider-Auswahl + Graph-Sender + Permission-Flags
        try:
            mail_provider           = _gs2("mail_provider", "") or "resend"
            mail_graph_sender       = _gs2("mail_graph_sender", "")
            entra_mail_send_enabled = _gs2("entra_mail_send_enabled", "0") == "1"
            entra_mail_read_enabled = _gs2("entra_mail_read_enabled", "0") == "1"
            email_to_print_enabled  = _gs2("email_to_print_enabled", "0") == "1"
        except Exception:
            mail_provider = "resend"
            mail_graph_sender = ""
            entra_mail_send_enabled = entra_mail_read_enabled = False
            email_to_print_enabled = False
        # Entra-Konfiguration
        try:
            from db import get_setting as gs
            entra_cfg = {
                "enabled":      gs("entra_enabled", "0") == "1",
                "tenant_id":    gs("entra_tenant_id", ""),
                "client_id":    gs("entra_client_id", ""),
                "has_secret":   bool(gs("entra_client_secret", "")),
                "auto_approve": gs("entra_auto_approve", "0") == "1",
            }
        except Exception:
            entra_cfg = {"enabled": False, "tenant_id": "", "client_id": "",
                         "has_secret": False, "auto_approve": False}
        # Gespeicherte Redirect URI (aus Auto-Setup oder manuell gesetzt)
        try:
            saved_redirect = gs("entra_redirect_uri", "")
        except Exception:
            saved_redirect = ""
        if not saved_redirect:
            base = _get_base_url(request)
            saved_redirect = f"{base}/auth/entra/callback"
        return {
            "request": request, "user": user,
            "public_url": public_url,
            "base_url": _get_base_url(request),
            "tenant_full": tenant_full or {},
            "current_global_default_queue_id":    current_global_default_queue_id,
            "current_global_default_queue_label": current_global_default_queue_label,
            "current_allow_user_override":        current_allow_user_override,
            "current_delegation_allowed":         current_delegation_allowed,
            "queues_for_picker":                  _load_printix_queues_for_admin(tenant_full) if tenant_full else [],
            "capture_public_url": capture_public_url,
            "ipps_public_url": ipps_public_url,
            "ipps_public_host": ipps_public_host,
            "ipps_port": str(ipps_port),
            "has_global_mail_key": has_global_mail_key,
            "global_mail_from": global_mail_from,
            "global_mail_from_name": global_mail_from_name,
            # v0.7.0: Microsoft-Graph Mail-Provider
            "mail_provider":           mail_provider,
            "mail_graph_sender":       mail_graph_sender,
            "entra_mail_send_enabled": entra_mail_send_enabled,
            "entra_mail_read_enabled": entra_mail_read_enabled,
            "email_to_print_enabled":  email_to_print_enabled,
            "entra": entra_cfg,
            "entra_redirect_uri": saved_redirect,
            "auto_setup_success": auto_setup_success,
            "backups": backups,
            "backup_success": backup_success,
            "backup_error": backup_error,
            "restore_success": restore_success,
            "saved": saved, "error": error,
            "license_status": license_status,
            # v7.9.4: Legal-Block für die neue Karte in admin_settings.html
            "legal":             _legal_settings(),
            "legal_configured":  _legal_configured(_legal_settings()),
            **_license_context(),
            **_timezone_context(),
            **t_ctx(request),
        }

    def _timezone_context() -> dict:
        """v7.2.48: Daten für die Timezone-Karte unter /admin/settings."""
        try:
            from datetime import datetime as _dt, timezone as _utc
            from zoneinfo import ZoneInfo
            tz_name = _resolve_display_tz_name()
            tz = ZoneInfo(tz_name)
            now_utc = _dt.now(_utc.utc)
            now_local = now_utc.astimezone(tz)
            # Curated Liste der häufigsten Zeitzonen — vollständige
            # zoneinfo.available_timezones() hätte 600+ Einträge.
            common = [
                "UTC",
                "Europe/Berlin", "Europe/Vienna", "Europe/Zurich",
                "Europe/Amsterdam", "Europe/Brussels", "Europe/Paris",
                "Europe/London", "Europe/Madrid", "Europe/Rome",
                "Europe/Stockholm", "Europe/Oslo", "Europe/Copenhagen",
                "Europe/Helsinki", "Europe/Warsaw", "Europe/Prague",
                "America/New_York", "America/Los_Angeles", "America/Chicago",
                "America/Denver", "America/Phoenix", "America/Toronto",
                "America/Vancouver", "America/Sao_Paulo", "America/Mexico_City",
                "Asia/Tokyo", "Asia/Shanghai", "Asia/Hong_Kong",
                "Asia/Singapore", "Asia/Dubai", "Asia/Kolkata",
                "Asia/Seoul", "Asia/Bangkok", "Asia/Jerusalem",
                "Australia/Sydney", "Australia/Melbourne", "Australia/Perth",
                "Pacific/Auckland", "Africa/Johannesburg", "Africa/Cairo",
            ]
            return {
                "tz_current_name":    tz_name,
                "tz_current_utc":     now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
                "tz_current_local":   now_local.strftime("%Y-%m-%d %H:%M:%S %Z (UTC%z)"),
                "tz_common_zones":    common,
            }
        except Exception as e:
            logger.warning("timezone context: %s", e)
            return {"tz_current_name": "Europe/Berlin",
                    "tz_current_utc": "?",
                    "tz_current_local": "?",
                    "tz_common_zones": []}

    def _license_context() -> dict:
        """v7.2.39: Pro-Feature-Lizenz-Status für admin_settings.html."""
        try:
            import sys as _ls
            _ls.path.insert(0, "/app")
            from license import (
                PRO_FEATURES, get_active_features, is_feature_enabled,
            )
            active = sorted(get_active_features())
            features_view = []
            for fid, info in PRO_FEATURES.items():
                features_view.append({
                    "id":     fid,
                    "icon":   info.get("icon", ""),
                    "label":  info.get("label_de", fid),
                    "label_en": info.get("label_en", fid),
                    "label_no": info.get("label_no", fid),
                    "desc":   info.get("description_de", ""),
                    "desc_en": info.get("description_en", ""),
                    "desc_no": info.get("description_no", ""),
                    "enabled": is_feature_enabled(fid),
                })
            return {
                "license_features": features_view,
                "license_active_count": len(active),
                "license_total_count":  len(PRO_FEATURES),
                "license_all_active":   len(active) == len(PRO_FEATURES),
            }
        except Exception as e:
            logger.warning("license context: %s", e)
            return {
                "license_features": [],
                "license_active_count": 0,
                "license_total_count":  0,
                "license_all_active":   False,
            }

    @app.get("/admin/settings", response_class=HTMLResponse)
    async def admin_settings_get(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        return templates.TemplateResponse("admin_settings.html",
            _admin_settings_ctx(request, user))

    @app.post("/admin/settings", response_class=HTMLResponse)
    async def admin_settings_post(request: Request):
        """v0.7.42: Section-safe save — nur Felder speichern die tatsächlich
        im submitteten Form-Body waren.

        Vorher-Bug: die Section-Filter (`?section=printix`) blendeten die
        anderen Sections aus dem DOM. Beim POST wurden aber via
        `Form(default="")` alle Felder als "" gelesen — der Handler
        überschrieb dann Entra-Enabled/Tenant/Client-ID + Mail-Absender
        etc. mit Leerstrings, obwohl der Admin nur die Printix-Section
        gespeichert hatte. Resultat: Entra sah nach jedem Save als
        „deaktiviert" aus, Mail-Absender-Feld leer, etc.

        Fix: manuelles Form-Parsing mit `.get()` und Section-Markern.
        """
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)

        form = await request.form()

        def has(field: str) -> bool:
            return field in form

        def val(field: str, default: str = "") -> str:
            v = form.get(field, default)
            return v.strip() if isinstance(v, str) else default

        try:
            from db import set_setting, _enc, audit
            changes: list[str] = []

            # v6.5.0: Cloud Print / IPPS — nur wenn Section aktiv
            if has("public_url"):
                url = val("public_url").rstrip("/")
                set_setting("public_url", url)
                changes.append(f"public_url={url}")
            if has("capture_public_url"):
                capture_url = val("capture_public_url").rstrip("/")
                set_setting("capture_public_url", capture_url)
                if capture_url:
                    changes.append(f"capture_public_url={capture_url}")
            if has("ipps_public_url"):
                set_setting("ipps_public_url", val("ipps_public_url").rstrip("/"))
            if has("ipps_port"):
                set_setting("ipps_port", val("ipps_port"))

            # v6.7.25: Globales Mail-Fallback — nur wenn Section aktiv
            if has("global_mail_api_key") and val("global_mail_api_key"):
                set_setting("global_mail_api_key",
                              _enc(val("global_mail_api_key")))
            if has("global_mail_from"):
                set_setting("global_mail_from", val("global_mail_from"))
            if has("global_mail_from_name"):
                set_setting("global_mail_from_name",
                              val("global_mail_from_name"))

            # v0.7.0: Mail-Provider-Auswahl (resend/graph) + Graph-Sender
            if has("mail_provider"):
                _mp = val("mail_provider").lower()
                if _mp in ("resend", "graph"):
                    set_setting("mail_provider", _mp)
            if has("mail_graph_sender"):
                set_setting("mail_graph_sender", val("mail_graph_sender"))

            # Entra-Settings — nur wenn Section aktiv war (entra_enabled
            # Checkbox ist Marker; unchecked = "" wenn im Form drin).
            # Wichtig: wenn Entra-Section GAR NICHT gerendert war, fehlt
            # entra_enabled komplett — dann bleibt der DB-Wert wie er ist.
            if has("entra_enabled") or has("entra_tenant_id") or has("entra_client_id"):
                set_setting("entra_enabled",
                              "1" if has("entra_enabled") and val("entra_enabled") else "0")
                if has("entra_tenant_id"):
                    set_setting("entra_tenant_id", val("entra_tenant_id"))
                if has("entra_client_id"):
                    set_setting("entra_client_id", val("entra_client_id"))
                if has("entra_auto_approve"):
                    set_setting("entra_auto_approve",
                                  "1" if val("entra_auto_approve") else "0")
                if has("entra_client_secret") and val("entra_client_secret"):
                    set_setting("entra_client_secret",
                                  _enc(val("entra_client_secret")))
                if has("entra_redirect_uri") and val("entra_redirect_uri"):
                    set_setting("entra_redirect_uri",
                                  val("entra_redirect_uri").rstrip("/"))
                changes.append("entra=" +
                                 ("aktiviert" if val("entra_enabled") else "deaktiviert"))

            if not changes:
                changes.append("keine Änderungen erkannt")
            audit(user["id"], "admin_settings", ", ".join(changes))
        except Exception as e:
            logger.error("Admin-Settings-Fehler: %s", e)
            return templates.TemplateResponse("admin_settings.html",
                _admin_settings_ctx(request, user, error=str(e)))

        return templates.TemplateResponse("admin_settings.html",
            _admin_settings_ctx(request, user, saved=True))

    # v0.4.5: Printix-Zugangsdaten editieren. Vorher nur ueber den
    # Register-Wizard setzbar — Rotation/Update der API-Secrets im
    # laufenden Betrieb war nicht moeglich. Aktualisiert den Tenant-
    # Record des aktuellen Owner-Admins. Leere Felder lassen den
    # bestehenden Wert unveraendert (entspricht der Semantik bei den
    # anderen Secret-Feldern, z.B. entra_client_secret).
    @app.post("/admin/settings/printix", response_class=HTMLResponse)
    async def admin_settings_printix_save(
        request: Request,
        printix_tenant_id:    str = Form(default=""),
        tenant_name:          str = Form(default=""),
        print_client_id:      str = Form(default=""),
        print_client_secret:  str = Form(default=""),
        card_client_id:       str = Form(default=""),
        card_client_secret:   str = Form(default=""),
        ws_client_id:         str = Form(default=""),
        ws_client_secret:     str = Form(default=""),
        um_client_id:         str = Form(default=""),
        um_client_secret:     str = Form(default=""),
        shared_client_id:     str = Form(default=""),
        shared_client_secret: str = Form(default=""),
    ):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            from db import (
                update_tenant_credentials, get_tenant_full_by_user_id,
                _find_tenant_owner_user_id, audit,
            )
            owner_id = user["id"]
            tenant = get_tenant_full_by_user_id(owner_id)
            if not tenant:
                # Fallback: irgendein Admin-Tenant — fuer Single-Tenant-Setups
                oid = _find_tenant_owner_user_id()
                if oid:
                    owner_id = oid
            kwargs = {
                "name":                 tenant_name.strip() or None,
                "printix_tenant_id":    printix_tenant_id.strip() or None,
                "print_client_id":      print_client_id.strip() or None,
                "card_client_id":       card_client_id.strip() or None,
                "ws_client_id":         ws_client_id.strip() or None,
                "um_client_id":         um_client_id.strip() or None,
                "shared_client_id":     shared_client_id.strip() or None,
            }
            # Secrets nur ueberschreiben wenn neuer Wert eingegeben
            if print_client_secret.strip():
                kwargs["print_client_secret"] = print_client_secret.strip()
            if card_client_secret.strip():
                kwargs["card_client_secret"] = card_client_secret.strip()
            if ws_client_secret.strip():
                kwargs["ws_client_secret"] = ws_client_secret.strip()
            if um_client_secret.strip():
                kwargs["um_client_secret"] = um_client_secret.strip()
            if shared_client_secret.strip():
                kwargs["shared_client_secret"] = shared_client_secret.strip()
            update_tenant_credentials(owner_id, **kwargs)
            audit(user["id"], "printix_credentials_updated",
                  f"tenant={tenant.get('id') if tenant else owner_id}")
        except Exception as e:
            logger.error("Printix-Credentials-Save fehlgeschlagen: %s",
                         e, exc_info=True)
            return RedirectResponse(
                f"/admin/settings?err={quote_plus(str(e))}#printix",
                status_code=302,
            )
        return RedirectResponse(
            "/admin/settings?ok=printix_saved#printix", status_code=302,
        )

    # v7.2.48: Display-Timezone speichern
    @app.post("/admin/settings/timezone")
    async def admin_settings_timezone(
        request: Request,
        timezone: str = Form(...),
    ):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        tz_name = (timezone or "").strip()
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(tz_name)  # validiert — wirft bei unbekannter Zone
            from db import set_setting, audit
            set_setting("display_timezone", tz_name)
            audit(user["id"], "admin_set_timezone",
                  f"Display timezone → {tz_name}",
                  object_type="setting", object_id="display_timezone")
            # v7.2.48: tzset() auf Web-Prozess-Ebene — danach erscheinen
            # neue Log-Zeilen mit %(asctime)s in der neuen Zeitzone (für
            # diesen Prozess). Der MCP-Server-Prozess (Port 8765) ist
            # separat — voller Effekt auf stdout/docker-logs erfordert
            # Container-Restart, ggf. zusätzlich TZ env in compose.
            try:
                import time as _time_mod
                os.environ["TZ"] = tz_name
                _time_mod.tzset()
                logger.info("Display timezone changed to %s (this process)", tz_name)
            except Exception as _tz_err:
                logger.warning("tzset() failed: %s", _tz_err)
            return RedirectResponse(
                "/admin/settings?ok=tz_saved#timezone", status_code=302,
            )
        except Exception as e:
            logger.warning("invalid timezone '%s': %s", tz_name, e)
            return RedirectResponse(
                f"/admin/settings?err=tz_invalid#timezone", status_code=302,
            )

    # v7.9.4: Legal information (operator name/address/email + DPO, hosting, etc.)
    # POST target for the new "Legal Information" card in admin_settings.html.
    @app.post("/admin/settings/legal/save")
    async def admin_settings_legal_save(
        request: Request,
        legal_operator_name:        str = Form(""),
        legal_operator_address:     str = Form(""),
        legal_operator_email:       str = Form(""),
        legal_operator_phone:       str = Form(""),
        legal_operator_country:     str = Form(""),
        legal_operator_vat_id:      str = Form(""),
        legal_data_protection_officer: str = Form(""),
        legal_hosting_provider:     str = Form(""),
        legal_supervisory_authority: str = Form(""),
    ):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        from db import set_setting, audit
        values = {
            "legal_operator_name":           (legal_operator_name or "").strip(),
            "legal_operator_address":        (legal_operator_address or "").strip(),
            "legal_operator_email":          (legal_operator_email or "").strip(),
            "legal_operator_phone":          (legal_operator_phone or "").strip(),
            "legal_operator_country":        (legal_operator_country or "").strip(),
            "legal_operator_vat_id":         (legal_operator_vat_id or "").strip(),
            "legal_data_protection_officer": (legal_data_protection_officer or "").strip(),
            "legal_hosting_provider":        (legal_hosting_provider or "").strip(),
            "legal_supervisory_authority":   (legal_supervisory_authority or "").strip(),
        }
        for k, v in values.items():
            set_setting(k, v)
        audit(user["id"], "admin_set_legal",
              f"Legal info updated (operator={values['legal_operator_name'] or '?'})",
              object_type="setting", object_id="legal_info")
        return RedirectResponse(
            "/admin/settings?ok=legal_saved#legal", status_code=302,
        )

    @app.post("/admin/settings/backup/create", response_class=HTMLResponse)
    async def admin_backup_create(request: Request,
                                    passphrase: str = Form("")):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            from backup_manager import create_backup
            from db import audit
            # v7.6.6: leere Passphrase = unverschlüsseltes Backup (legacy
            # Verhalten); nicht-leer = AES-verschlüsselt mit PBKDF2-Key.
            pp = (passphrase or "").strip() or None
            result = create_backup(passphrase=pp)
            audit(user["id"], "backup_create",
                  f"Backup erstellt: {result['filename']} "
                  f"(encrypted={result.get('encrypted', False)})")
            return templates.TemplateResponse(
                "admin_settings.html",
                _admin_settings_ctx(request, user, backup_success=result),
            )
        except Exception as e:
            logger.error("Backup-Erstellung fehlgeschlagen: %s", e, exc_info=True)
            return templates.TemplateResponse(
                "admin_settings.html",
                _admin_settings_ctx(request, user, backup_error=str(e)),
            )

    @app.get("/admin/settings/backups/{filename}")
    async def admin_backup_download(filename: str, request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            from backup_manager import resolve_backup_path
            path = resolve_backup_path(filename)
        except Exception:
            return RedirectResponse("/admin/settings", status_code=302)
        return FileResponse(path, filename=path.name, media_type="application/zip")

    @app.post("/admin/settings/backup/restore", response_class=HTMLResponse)
    async def admin_backup_restore(
        request: Request,
        backup_zip: UploadFile = File(...),
        passphrase: str = Form(""),
    ):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        tc = t_ctx(request)
        _ = tc["_"]
        if not backup_zip.filename or not backup_zip.filename.lower().endswith(".zip"):
            return templates.TemplateResponse(
                "admin_settings.html",
                _admin_settings_ctx(request, user, backup_error=_("backup_restore_invalid_file")),
            )
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(prefix="printix-restore-", suffix=".zip", delete=False) as tmp:
                tmp_path = tmp.name
                tmp.write(await backup_zip.read())
            from backup_manager import restore_backup
            pp = (passphrase or "").strip() or None
            result = restore_backup(tmp_path, passphrase=pp)
            return templates.TemplateResponse(
                "admin_settings.html",
                _admin_settings_ctx(request, user, restore_success=result),
            )
        except Exception as e:
            logger.error("Backup-Restore fehlgeschlagen: %s", e, exc_info=True)
            return templates.TemplateResponse(
                "admin_settings.html",
                _admin_settings_ctx(request, user, backup_error=str(e)),
            )
        finally:
            try:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except Exception:
                pass


    # ─── Blob Auto-Backup (v0.3.0) ───────────────────────────────────────────
    # Daily background task that creates an encrypted backup via
    # backup_manager.create_backup() and uploads it to Azure Blob Storage.
    # The container survives even if the App Service's mounted Azure Files
    # share gets wiped (storage-account-level disaster). Configured under
    # /admin/blob-backup; off by default.

    def _blob_backup_ctx(request: Request, user: dict, **extra) -> dict:
        tc = t_ctx(request)
        from db import get_setting
        from crypto import encrypt as _enc, decrypt as _dec  # noqa: F401
        try:
            from blob_backup import (
                list_blobs as _lb, is_configured as _ic, is_enabled as _ie,
                DEFAULT_CONTAINER, DEFAULT_RETENTION_DAYS,
            )
        except Exception:
            _lb = lambda: []  # noqa: E731
            _ic = lambda: False  # noqa: E731
            _ie = lambda: False  # noqa: E731
            DEFAULT_CONTAINER = "mysecureprint-backups"
            DEFAULT_RETENTION_DAYS = 30
        last_run_at = get_setting("blob_backup_last_run_at", "")
        last_raw    = get_setting("blob_backup_last_result", "")
        try:
            import json as _j
            last_result = _j.loads(last_raw) if last_raw else None
        except Exception:
            last_result = None
        has_conn_setting = bool(get_setting("blob_backup_connection_string", ""))
        has_pp_setting   = bool(get_setting("blob_backup_passphrase", ""))
        env_conn_present = bool(os.environ.get("AZURE_STORAGE_CONNECTION_STRING", ""))
        ctx = {
            "request": request,
            "user": user,
            "enabled":          _ie(),
            "configured":       _ic(),
            "container":        get_setting("blob_backup_container", "") or DEFAULT_CONTAINER,
            "retention_days":   get_setting("blob_backup_retention_days", str(DEFAULT_RETENTION_DAYS)),
            "has_conn_setting": has_conn_setting,
            "has_pp_setting":   has_pp_setting,
            "env_conn_present": env_conn_present,
            "last_run_at":      last_run_at,
            "last_result":      last_result,
            "blobs":            _lb(),
            **tc, **extra,
        }
        return ctx

    @app.get("/admin/blob-backup", response_class=HTMLResponse)
    async def admin_blob_backup_page(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        return templates.TemplateResponse(
            "admin_blob_backup.html",
            _blob_backup_ctx(request, user),
        )

    @app.post("/admin/blob-backup/save", response_class=HTMLResponse)
    async def admin_blob_backup_save(
        request: Request,
        enabled:           str = Form(""),
        connection_string: str = Form(""),
        container:         str = Form(""),
        passphrase:        str = Form(""),
        retention_days:    str = Form(""),
    ):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        from db import set_setting, audit
        from crypto import encrypt as _enc

        set_setting("blob_backup_enabled", "1" if enabled in ("1", "on", "true") else "0")
        # Only overwrite the encrypted secret-fields if the admin actually typed
        # something — empty form means "keep the existing value".
        if connection_string.strip():
            set_setting("blob_backup_connection_string", _enc(connection_string.strip()))
        if passphrase.strip():
            set_setting("blob_backup_passphrase", _enc(passphrase.strip()))
        if container.strip():
            set_setting("blob_backup_container", container.strip())
        rd = retention_days.strip()
        if rd:
            try:
                set_setting("blob_backup_retention_days", str(max(0, int(rd))))
            except ValueError:
                pass
        audit(user["id"], "blob_backup_settings_saved",
              f"enabled={enabled in ('1','on','true')} container={container or '-'}")
        return RedirectResponse("/admin/blob-backup?ok=saved", status_code=302)

    @app.post("/admin/blob-backup/run", response_class=HTMLResponse)
    async def admin_blob_backup_run_now(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            from blob_backup import run_once
            from db import audit
            r = run_once()
            audit(user["id"], "blob_backup_run_manual",
                  f"ok={r.get('ok')} blob={r.get('blob_name','-')} "
                  f"size={r.get('size',0)} error={r.get('error','-')}")
        except Exception as e:
            logger.error("blob backup manual run failed: %s", e, exc_info=True)
        return RedirectResponse("/admin/blob-backup?ok=ran", status_code=302)

    @app.post("/admin/blob-backup/restore", response_class=HTMLResponse)
    async def admin_blob_backup_restore(
        request: Request,
        blob_name:  str = Form(...),
        passphrase: str = Form(""),
    ):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        tmp_path = None
        try:
            from blob_backup import download_blob
            from backup_manager import restore_backup
            from db import audit
            with tempfile.NamedTemporaryFile(prefix="printix-blob-restore-",
                                              suffix=".zip", delete=False) as tmp:
                tmp_path = tmp.name
            download_blob(blob_name, Path(tmp_path))
            pp = (passphrase or "").strip() or None
            restore_backup(tmp_path, passphrase=pp)
            audit(user["id"], "blob_backup_restored",
                  f"blob={blob_name}")
            return RedirectResponse("/admin/blob-backup?ok=restored", status_code=302)
        except Exception as e:
            logger.error("blob backup restore failed: %s", e, exc_info=True)
            return RedirectResponse(f"/admin/blob-backup?err={quote_plus(str(e))}",
                                    status_code=302)
        finally:
            try:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except Exception:
                pass

    # ─── Queue-Defaults Admin (v0.5.0) ────────────────────────────────────
    # 3-Tier-Hierarchie: Global → Sync-Gruppe → User-Override.
    # Globaler Default + Override-Toggle leben in /admin/settings#queue;
    # per-Gruppe-Defaults haben ihre eigene Seite /admin/groups.

    def _is_anywhere_queue(printer_item: dict, name: str = "") -> bool:
        """v0.6.0: Multi-Signal-Detection ob ein Drucker eine
        Printix-Anywhere-Virtual-Queue ist.

        Vorher: nur `"anywhere" in name.lower()` — viel zu schmal, viele
        Tenants haben Anywhere-Queues NICHT „Anywhere…" benannt.

        Neue Logik (OR — irgendein Signal reicht):
        - `vendor` oder `manufacturer` = „Printix" (User-Hinweis)
        - `model` enthaelt „anywhere"
        - `printerType` / `type` = „anywhere" / „virtual"
        - `isAnywhere` Boolean-Feld (falls Printix API es liefert)
        - `name` enthaelt „anywhere" (Fallback fuer alte Tenants)
        """
        if not isinstance(printer_item, dict):
            return bool(name and "anywhere" in name.lower())

        def _lc(v) -> str:
            return str(v or "").strip().lower()

        # Signal 1: explizites Boolean-Feld
        for k in ("isAnywhere", "is_anywhere", "anywhere"):
            v = printer_item.get(k)
            if isinstance(v, bool) and v:
                return True

        # Signal 2: Hersteller / Vendor = Printix
        if _lc(printer_item.get("vendor")) == "printix":
            return True
        if _lc(printer_item.get("manufacturer")) == "printix":
            return True
        if _lc(printer_item.get("brand")) == "printix":
            return True

        # Signal 3: Type / printerType-Feld
        for k in ("printerType", "type", "queueType"):
            if "anywhere" in _lc(printer_item.get(k)):
                return True
            if "virtual" in _lc(printer_item.get(k)):
                return True

        # Signal 4: Model enthaelt anywhere
        if "anywhere" in _lc(printer_item.get("model")):
            return True

        # Signal 5: Name-Fallback (Legacy)
        if name and "anywhere" in name.lower():
            return True
        if "anywhere" in _lc(printer_item.get("name")):
            return True

        return False

    def _load_printix_queues_for_admin(tenant) -> list[dict]:
        """Lädt alle Queues des Printix-Tenants für Dropdowns."""
        if not tenant or not (tenant.get("print_client_id")
                              or tenant.get("shared_client_id")):
            return []
        try:
            import sys as _s, os as _o, re as _re
            _s.path.insert(0, _o.path.dirname(_o.path.dirname(__file__)))
            from printix_client import PrintixClient
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
            queues = []
            for item in raw:
                href = (item.get("_links") or {}).get("self", {}).get("href", "")
                m = _re.search(r"/printers/([^/]+)/queues/([^/?]+)", href)
                if m:
                    name = item.get("name", "") or m.group(2)
                    is_any = _is_anywhere_queue(item, name)
                    queues.append({
                        "queue_id":      m.group(2),
                        "queue_name":    name,
                        "printer_id":    m.group(1),
                        "printer_name":  item.get("name", ""),
                        "vendor":        item.get("vendor", "") or item.get("manufacturer", ""),
                        "model":         item.get("model", ""),
                        "is_anywhere":   is_any,
                    })
            # Anywhere-Queues nach oben sortieren
            queues.sort(key=lambda q: (not q["is_anywhere"], q["queue_name"].lower()))
            return queues
        except Exception as e:
            logger.warning("Printix-Queues-Abruf fehlgeschlagen: %s", e)
            return []

    @app.post("/admin/settings/queue-defaults/save", response_class=HTMLResponse)
    async def admin_queue_defaults_save(
        request: Request,
        default_queue_id:           str = Form(default=""),
        default_queue_label:        str = Form(default=""),
        allow_user_queue_override:  str = Form(default=""),
        delegation_print_allowed:   str = Form(default=""),
    ):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        from cloudprint.db_extensions import (
            set_global_default_queue, set_user_queue_override_allowed,
        )
        from db import audit, set_setting
        set_global_default_queue(default_queue_id.strip(), default_queue_label.strip())
        set_user_queue_override_allowed(
            allow_user_queue_override in ("1", "on", "true")
        )
        # v0.7.26: Delegation-Druck-Toggle persistieren
        _del_new = "1" if delegation_print_allowed in ("1","on","true") else "0"
        set_setting("delegation_print_allowed", _del_new)
        audit(user["id"], "queue_defaults_saved",
              f"global={default_queue_id} override={allow_user_queue_override or 'off'} "
              f"delegation={_del_new}")
        return RedirectResponse(
            "/admin/settings?ok=queue_saved#queue", status_code=302,
        )

    @app.get("/admin/groups", response_class=HTMLResponse)
    async def admin_groups_page(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        from db import (
            get_tenant_full_by_user_id, _find_tenant_owner_user_id,
        )
        from cloudprint.db_extensions import list_group_queue_defaults
        tenant = get_tenant_full_by_user_id(user["id"])
        if not tenant:
            oid = _find_tenant_owner_user_id()
            tenant = get_tenant_full_by_user_id(oid) if oid else None

        # v0.7.14: Printix-Groups + Queues parallel im Thread holen, statt
        # synchron hintereinander den Event-Loop zu blockieren. Beide Calls
        # zusammen waren der Haupt-Bottleneck auf /admin/groups (bis zu
        # 30+ Sekunden bei lahmer Printix-API-Latenz).
        import time as _t_g
        _t_g0 = _t_g.monotonic()
        try:
            from db import perf_logs_enabled as _perf_on_g
            _perf_g = _perf_on_g()
        except Exception:
            _perf_g = False

        def _fetch_printix_groups(_tenant):
            if not _tenant or not (_tenant.get("print_client_id")
                                   or _tenant.get("shared_client_id")):
                return []
            try:
                from printix_client import PrintixClient
                client = PrintixClient(
                    tenant_id=_tenant["printix_tenant_id"],
                    print_client_id=_tenant.get("print_client_id", ""),
                    print_client_secret=_tenant.get("print_client_secret", ""),
                    shared_client_id=_tenant.get("shared_client_id", ""),
                    shared_client_secret=_tenant.get("shared_client_secret", ""),
                )
                data = client.list_groups(size=200)
                raw = (data.get("_embedded") or {}).get("groups", []) if isinstance(data, dict) else []
                if not raw:
                    raw = data.get("groups", []) if isinstance(data, dict) else []
                out = []
                for g in raw:
                    href = (g.get("_links") or {}).get("self", {}).get("href", "")
                    gid = href.rstrip("/").split("/")[-1] if href else g.get("id", "")
                    out.append({
                        "id":   gid,
                        "name": g.get("name", ""),
                        "description": g.get("description", ""),
                    })
                return out
            except Exception as e:
                logger.warning("Printix-Groups-Abruf fehlgeschlagen: %s", e)
                return []

        import asyncio as _aio_g
        printix_groups, queues_pre = await _aio_g.gather(
            _aio_g.to_thread(_fetch_printix_groups, tenant),
            _aio_g.to_thread(_load_printix_queues_for_admin, tenant) if tenant else _aio_g.to_thread(lambda: []),
        )

        # Bestehende Group-Defaults laden — defensive: wenn die
        # Migration aus irgend einem Grund nicht durchgelaufen ist
        # (group_queue_defaults-Tabelle fehlt), liefern wir eine leere
        # Liste statt zu 500-en.
        tid = (tenant or {}).get("id", "")
        try:
            existing_defaults = list_group_queue_defaults(tid) if tid else []
        except Exception as _e:
            logger.warning("list_group_queue_defaults(%s) failed: %s", tid, _e)
            existing_defaults = []
        existing_map = {d["printix_group_id"]: d for d in existing_defaults}

        # v0.7.14: queues bereits oben parallel mit groups geholt.
        queues = queues_pre or []
        if _perf_g:
            logger.info(
                "perf admin_groups dt_total=%.0fms groups=%d queues=%d",
                (_t_g.monotonic() - _t_g0) * 1000.0,
                len(printix_groups), len(queues),
            )

        return templates.TemplateResponse("admin_groups.html", {
            "request": request, "user": user,
            "tenant": tenant or {},
            "printix_groups": printix_groups,
            "existing_defaults_map": existing_map,
            "queues": queues,
            "active_page": "admin_groups",
            **t_ctx(request),
        })

    @app.post("/admin/groups/set-queue", response_class=HTMLResponse)
    async def admin_groups_set_queue(
        request: Request,
        printix_group_id:   str = Form(...),
        printix_group_name: str = Form(default=""),
        queue_id:           str = Form(default=""),
        queue_label:        str = Form(default=""),
        printer_id:         str = Form(default=""),
    ):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        from db import (
            get_tenant_full_by_user_id, _find_tenant_owner_user_id, audit,
        )
        from cloudprint.db_extensions import (
            set_group_queue_default, delete_group_queue_default,
        )
        tenant = get_tenant_full_by_user_id(user["id"])
        if not tenant:
            oid = _find_tenant_owner_user_id()
            tenant = get_tenant_full_by_user_id(oid) if oid else None
        if not tenant:
            return RedirectResponse("/admin/groups?err=no_tenant", status_code=302)
        tid = tenant["id"]
        if not queue_id.strip():
            # Löschen
            delete_group_queue_default(tid, printix_group_id)
            audit(user["id"], "group_queue_cleared",
                  f"group={printix_group_id}")
        else:
            set_group_queue_default(
                tid, printix_group_id, printix_group_name,
                queue_id.strip(), queue_label.strip(),
                printer_id.strip(), created_by=user.get("username", ""),
            )
            audit(user["id"], "group_queue_set",
                  f"group={printix_group_id} → queue={queue_id}")
        return RedirectResponse("/admin/groups?ok=saved", status_code=302)

    # ─── User-Account-Seite (v0.5.6) ─────────────────────────────────────
    # Regulaere User (nicht Admin, nicht Employee) landeten vorher auf
    # /admin und bekamen eine ungeeignete Seite. Jetzt: eigene
    # /account-Seite mit User-Info, iOS-App-QR, eigenen MCP-Credentials
    # (Bearer fuer API-Direkt-Zugriff + tenant OAuth fuer claude.ai/
    # ChatGPT-Connector-Setup) und GDPR-Daten-Export-Link.
    @app.get("/account", response_class=HTMLResponse)
    async def account_page(request: Request):
        user = get_session_user(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        # Admin → eigentlich /admin, aber wenn jemand explizit /account
        # ansurft, soll er trotzdem die Seite sehen koennen (gleicher
        # Inhalt + zusaetzlicher Admin-Badge).
        base_url = mcp_base_url_or(request)
        qr_payload = f"mysecureprint://setup?server={base_url}/"
        qr_svg = _make_welcome_qr_svg(qr_payload)

        # Tenant-OAuth-Credentials laden (per-user-tenant). Sind formal
        # Tenant-weit, aber jeder User braucht sie um z.B. claude.ai
        # einzurichten — der OAuth-Flow ist user-spezifisch (eigener
        # Bearer-Token), die Client-ID/Secret ist tenant-weit.
        tenant = None
        try:
            from db import (
                get_tenant_full_by_user_id, _find_tenant_owner_user_id,
            )
            tenant = get_tenant_full_by_user_id(user["id"])
            if not tenant:
                oid = _find_tenant_owner_user_id()
                tenant = get_tenant_full_by_user_id(oid) if oid else None
        except Exception:
            tenant = None

        # User-spezifischen Bearer (fuer MCP-API-Direktzugriff) anzeigen
        # — nur wenn explizit gewuenscht (per Klick auf „Anzeigen"-Button
        # via JS-toggle, im Template umgesetzt).
        try:
            from desktop_auth import list_tokens_for_user
            user_tokens = list_tokens_for_user(user["id"])
        except Exception:
            user_tokens = []

        # MCP-Aktiv-Status fuer den Hinweis-Block
        try:
            from db import get_setting
            mcp_enabled = get_setting("mcp_enabled", "0") == "1"
        except Exception:
            mcp_enabled = False

        return templates.TemplateResponse("account.html", {
            "request": request,
            "user": user,
            "base_url": base_url,
            "qr_payload": qr_payload,
            "qr_svg": qr_svg,
            "tenant": tenant or {},
            "mcp_enabled": mcp_enabled,
            "mcp_url": f"{base_url}/mcp",
            "sse_url": f"{base_url}/sse",
            "oauth_authorize_url": f"{base_url}/oauth/authorize",
            "oauth_token_url": f"{base_url}/oauth/token",
            "user_token_count": len(user_tokens),
            "active_page": "account",
            **t_ctx(request),
        })

    # ─── GDPR / Datenschutz Admin Page (v0.4.6) ──────────────────────────
    # Zentralisiert die DSGVO-relevanten Settings: Daten-Retention (wie
    # lange Audit-Logs, Mobile-Invites und gelaufene Backups gespeichert
    # werden), Self-Service-Export pro User (DSAR), und Right-to-be-
    # forgotten (User-Purge). Public Privacy-Page bleibt unter /privacy
    # — die hier sind die ADMIN-seitigen Steuerungen.

    @app.get("/admin/gdpr", response_class=HTMLResponse)
    async def admin_gdpr_page(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        from db import get_setting
        ctx = {
            "request": request,
            "user": user,
            "active_page": "admin_gdpr",
            "audit_retention_days":      get_setting("gdpr_audit_retention_days", "365"),
            "invite_retention_days":     get_setting("gdpr_invite_retention_days", "30"),
            "blob_retention_days":       get_setting("blob_backup_retention_days", "30"),
            "session_max_age_hours":     get_setting("gdpr_session_max_age_hours", "168"),
            "auto_purge_disabled_users": get_setting("gdpr_auto_purge_disabled_users", "0") == "1",
            "auto_purge_after_days":     get_setting("gdpr_auto_purge_after_days", "90"),
            **t_ctx(request),
        }
        return templates.TemplateResponse("admin_gdpr.html", ctx)

    @app.post("/admin/gdpr/save", response_class=HTMLResponse)
    async def admin_gdpr_save(
        request: Request,
        audit_retention_days:      str = Form(default="365"),
        invite_retention_days:     str = Form(default="30"),
        session_max_age_hours:     str = Form(default="168"),
        auto_purge_disabled_users: str = Form(default=""),
        auto_purge_after_days:     str = Form(default="90"),
    ):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        from db import set_setting, audit
        def _clamp(s: str, lo: int, hi: int, default: int) -> int:
            try: v = int(s)
            except Exception: v = default
            return max(lo, min(hi, v))
        set_setting("gdpr_audit_retention_days",  str(_clamp(audit_retention_days, 7, 3650, 365)))
        set_setting("gdpr_invite_retention_days", str(_clamp(invite_retention_days, 1, 365, 30)))
        set_setting("gdpr_session_max_age_hours", str(_clamp(session_max_age_hours, 1, 8760, 168)))
        set_setting("gdpr_auto_purge_disabled_users",
                    "1" if auto_purge_disabled_users in ("1", "on", "true") else "0")
        set_setting("gdpr_auto_purge_after_days",
                    str(_clamp(auto_purge_after_days, 7, 3650, 90)))
        audit(user["id"], "gdpr_settings_saved", "retention+purge config updated")
        return RedirectResponse("/admin/gdpr?ok=saved", status_code=302)

    @app.post("/admin/gdpr/export-user", response_class=HTMLResponse)
    async def admin_gdpr_export_user(request: Request,
                                       email_or_username: str = Form(...)):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            from db import get_all_users, audit
            from gdpr_export import gdpr_collect_user_data
            target_key = email_or_username.strip().lower()
            target = None
            for u in get_all_users():
                if (u.get("email", "").lower() == target_key
                        or u.get("username", "").lower() == target_key):
                    target = u
                    break
            if not target:
                return RedirectResponse(
                    "/admin/gdpr?err=user_not_found", status_code=302)
            audit(user["id"], "gdpr_export_user",
                  f"target={target.get('username')}",
                  object_type="user", object_id=target.get("id", ""))
            export = gdpr_collect_user_data(target["id"])
            # Wer den Export gemacht hat dokumentieren (Art. 5 Abs. 2 — Accountability).
            export["exported_by"] = user.get("username")
            uname = target.get("username") or "unknown"
            return Response(
                content=json.dumps(export, indent=2, default=str),
                media_type="application/json",
                headers={
                    "Content-Disposition":
                    f'attachment; filename="dsar_{uname}.json"',
                },
            )
        except Exception as e:
            logger.error("gdpr export failed: %s", e, exc_info=True)
            return RedirectResponse(
                f"/admin/gdpr?err={quote_plus(str(e))}", status_code=302)

    # ─── MCP Permissions Admin Page (GDPR / RBAC) ────────────────────────
    # Manages the role-based permission model for MCP tool calls. Roles are
    # set per-Printix-group (highest wins) or per-user (explicit override).
    # The RBAC enforcement gate lives in src/server.py — it consults the
    # rbac_enabled DB setting (toggled here) and rejects calls that don't
    # match the caller's resolved role's scope.
    #
    # Privacy guards on this page:
    #   - rbac_enabled         → master switch for enforcement
    #   - group_peer_reports_enabled → opt-in for cross-employee printing
    #     comparisons (off by default; Works-Council approval required in
    #     Germany before enabling).
    @app.get("/admin/mcp-permissions", response_class=HTMLResponse)
    async def admin_mcp_permissions(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        import asyncio as _aio
        try:
            from db import (
                get_all_users, get_tenant_full_by_user_id,
                list_group_mcp_roles, get_setting,
            )
            import permissions as _perm
        except Exception as e:
            logger.error("MCP-Permissions: import error: %s", e)
            return RedirectResponse("/admin?err=import_error", status_code=302)

        all_users = get_all_users() or []
        group_role_rows = list_group_mcp_roles() or []
        group_role_map = {r["group_id"]: r for r in group_role_rows}

        live_groups: list[dict] = []
        groups_error = ""
        try:
            tenant_full = get_tenant_full_by_user_id(user["id"])
            if tenant_full and (tenant_full.get("print_client_id")
                                or tenant_full.get("shared_client_id")):
                px = _make_printix_client(tenant_full)

                def _fetch_groups():
                    raw = px.list_groups(page=0, size=200)
                    if isinstance(raw, dict):
                        return raw.get("groups", raw.get("content", []))
                    return raw if isinstance(raw, list) else []

                fetched = await _aio.to_thread(_fetch_groups)

                stub_groups: list[dict] = []
                for g in fetched or []:
                    if not isinstance(g, dict):
                        continue
                    gid = ""
                    links = g.get("_links") or {}
                    self_link = (links.get("self") or {}).get("href") or ""
                    if self_link:
                        gid = self_link.rstrip("/").split("/")[-1]
                    if not gid:
                        gid = str(g.get("id") or g.get("groupId") or "")
                    if not gid:
                        continue
                    stub_groups.append({
                        "id": gid,
                        "name": g.get("name") or g.get("displayName") or gid,
                        "raw": g,
                    })

                async def _resolve_member_count(gid: str, raw_g: dict) -> int:
                    for key in ("memberCount", "userCount", "numMembers",
                                "numUsers", "size", "totalMembers"):
                        v = raw_g.get(key)
                        if isinstance(v, (int, float)):
                            return int(v)
                    members_field = raw_g.get("members")
                    if isinstance(members_field, list):
                        return len(members_field)
                    try:
                        gobj = await _aio.to_thread(lambda: px.get_group(gid))
                    except Exception as exc:
                        logger.debug("get_group(%s) failed: %s", gid, exc)
                        return 0
                    if not isinstance(gobj, dict):
                        return 0
                    for key in ("members", "users", "memberUsers"):
                        v = gobj.get(key)
                        if isinstance(v, list):
                            return len(v)
                    for key in ("memberCount", "userCount", "numMembers",
                                "numUsers", "size", "totalMembers"):
                        v = gobj.get(key)
                        if isinstance(v, (int, float)):
                            return int(v)
                    ul = ((gobj.get("_links") or {}).get("users") or {}).get("href")
                    if ul:
                        return -1
                    return 0

                counts = await _aio.gather(
                    *[_resolve_member_count(s["id"], s["raw"]) for s in stub_groups],
                    return_exceptions=True,
                )

                for stub, mc in zip(stub_groups, counts):
                    gid = stub["id"]
                    if isinstance(mc, Exception):
                        mc_val = 0
                    elif isinstance(mc, (int, float)):
                        mc_val = int(mc)
                    else:
                        mc_val = 0
                    live_groups.append({
                        "id": gid,
                        "name": stub["name"],
                        "member_count": mc_val,
                        "current_role": (group_role_map.get(gid) or {}).get("mcp_role", ""),
                    })
            else:
                groups_error = "no_credentials"
        except Exception as e:
            logger.warning("MCP-Permissions: list_groups failed: %s", e)
            groups_error = "api_error"

        show_all = (request.query_params.get("show_all") or "").strip() in ("1", "true", "yes")
        total_live = len(live_groups)
        if not show_all:
            live_groups = [
                g for g in live_groups
                if (isinstance(g["member_count"], int) and g["member_count"] != 0)
                or g["current_role"]
            ]
        hidden_count = total_live - len(live_groups)

        live_ids = {g["id"] for g in live_groups}
        orphan_groups = [
            {"id": r["group_id"], "name": r["group_name"],
             "member_count": 0, "current_role": r["mcp_role"], "_orphan": True}
            for r in group_role_rows
            if r["group_id"] not in live_ids
        ]

        users_view = []
        for u in all_users:
            override = (u.get("mcp_role") or "").strip().lower()
            users_view.append({
                "id": u.get("id", ""),
                "username": u.get("username", ""),
                "email": u.get("email", ""),
                "full_name": u.get("full_name", ""),
                "is_admin": bool(u.get("is_admin")),
                "status": u.get("status", ""),
                "mcp_role_override": override,
                "mcp_role_resolved": override or "end_user",
                "mcp_role_source": "override" if override else "default",
            })

        flash_ok = (request.query_params.get("ok") or "").strip() or None
        flash_err = (request.query_params.get("err") or "").strip() or None

        ctx = t_ctx(request)
        if ctx.get("lang") == "de":
            role_labels = _perm.ROLE_LABELS_DE
            role_descriptions = _perm.ROLE_DESCRIPTIONS_DE
        else:
            role_labels = _perm.ROLE_LABELS_EN
            role_descriptions = _perm.ROLE_DESCRIPTIONS_EN

        try:
            db_val = (get_setting("rbac_enabled", "") or "").strip().lower()
        except Exception:
            db_val = ""
        if db_val:
            rbac_enabled = db_val in ("1", "true", "yes", "on")
            rbac_source = "db"
        else:
            rbac_enabled = (os.getenv("MCP_RBAC_ENABLED", "0").strip().lower()
                            in ("1", "true", "yes", "on"))
            rbac_source = "env"

        return templates.TemplateResponse("admin_mcp_permissions.html", {
            "request": request, "user": user,
            "users": users_view,
            "live_groups": live_groups,
            "orphan_groups": orphan_groups,
            "groups_error": groups_error,
            "show_all_groups": show_all,
            "hidden_inactive_count": hidden_count,
            "all_roles": _perm.ALL_ROLES,
            "group_assignable_roles": _perm.GROUP_ASSIGNABLE_ROLES,
            "role_labels": role_labels,
            "role_descriptions": role_descriptions,
            "rbac_enabled": rbac_enabled,
            "group_peer_reports_enabled": (
                (get_setting("group_peer_reports_enabled", "0") or "0").strip().lower()
                in ("1", "true", "yes", "on")
            ),
            "rbac_source": rbac_source,
            "flash_ok": flash_ok, "flash_err": flash_err,
            "active_page": "admin_rbac",
            **ctx,
        })

    @app.post("/admin/mcp-permissions/rbac-toggle")
    async def admin_mcp_rbac_toggle(request: Request, action: str = Form("")):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            from db import set_setting, audit
            new_state = "1" if action == "enable" else "0"
            set_setting("rbac_enabled", new_state)
            audit(
                admin["id"],
                "rbac_enabled_set" if new_state == "1" else "rbac_disabled_set",
                f"RBAC {'enabled' if new_state == '1' else 'disabled'} via Admin-UI",
                object_type="setting", object_id="rbac_enabled",
            )
            return RedirectResponse(
                "/admin/mcp-permissions?ok=" + ("rbac_enabled" if new_state == "1" else "rbac_disabled"),
                status_code=302,
            )
        except Exception as e:
            logger.error("rbac toggle: %s", e)
            return RedirectResponse(
                f"/admin/mcp-permissions?err={quote_plus(str(e))}",
                status_code=302,
            )

    @app.post("/admin/mcp-permissions/group-peer-toggle")
    async def admin_group_peer_toggle(request: Request, action: str = Form("")):
        """Toggle for `group_peer_reports_enabled` — when on, end-users may
        compare their own print activity to anonymized peers in the same
        Printix group. Default OFF for GDPR / employee privacy."""
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            from db import set_setting, audit
            new_state = "1" if action == "enable" else "0"
            set_setting("group_peer_reports_enabled", new_state)
            audit(
                admin["id"],
                "group_peer_reports_set",
                f"Group peer reports {'enabled' if new_state == '1' else 'disabled'} via Admin-UI",
                object_type="setting", object_id="group_peer_reports_enabled",
            )
            return RedirectResponse(
                "/admin/mcp-permissions?ok=" + ("group_peer_enabled" if new_state == "1" else "group_peer_disabled"),
                status_code=302,
            )
        except Exception as e:
            logger.error("group peer toggle: %s", e)
            return RedirectResponse(
                f"/admin/mcp-permissions?err={quote_plus(str(e))}",
                status_code=302,
            )

    @app.post("/admin/mcp-permissions/user-role")
    async def admin_mcp_set_user_role(
        request: Request,
        user_id: str = Form(...),
        mcp_role: str = Form(""),
    ):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            from db import set_user_mcp_role, audit
            ok = set_user_mcp_role(user_id, mcp_role)
            if ok:
                audit(
                    admin["id"], "mcp_set_user_role",
                    f"User {user_id} → mcp_role='{mcp_role or '(cleared)'}'",
                    object_type="user", object_id=user_id,
                )
                return RedirectResponse(
                    "/admin/mcp-permissions?ok=user_role_updated",
                    status_code=302,
                )
            return RedirectResponse(
                "/admin/mcp-permissions?err=user_not_found", status_code=302,
            )
        except Exception as e:
            logger.error("MCP-Permissions user-role POST: %s", e)
            return RedirectResponse(
                "/admin/mcp-permissions?err=update_failed", status_code=302,
            )

    @app.post("/admin/mcp-permissions/group-role")
    async def admin_mcp_set_group_role(
        request: Request,
        group_id: str = Form(...),
        group_name: str = Form(""),
        mcp_role: str = Form(""),
    ):
        admin = get_session_user(request)
        if not admin or not admin.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            from db import set_group_mcp_role, audit
            ok = set_group_mcp_role(
                group_id=group_id,
                group_name=group_name or group_id,
                mcp_role=mcp_role,
                assigned_by=admin.get("id", ""),
            )
            if ok:
                action = "mcp_clear_group_role" if not mcp_role else "mcp_set_group_role"
                audit(
                    admin["id"], action,
                    f"Group {group_name or group_id} → '{mcp_role or '(cleared)'}'",
                    object_type="printix_group", object_id=group_id,
                )
                return RedirectResponse(
                    "/admin/mcp-permissions?ok=group_role_updated",
                    status_code=302,
                )
            return RedirectResponse(
                "/admin/mcp-permissions?err=invalid_role", status_code=302,
            )
        except Exception as e:
            logger.error("MCP-Permissions group-role POST: %s", e)
            return RedirectResponse(
                "/admin/mcp-permissions?err=update_failed", status_code=302,
            )

    # ─── MCP Access Admin Page (v0.4.0) ──────────────────────────────────
    # Opt-in MCP exposure. The MCP server itself runs in the same container
    # (entrypoint.sh starts it on port 8765 — internal only). Setting
    # mcp_enabled=1 unlocks the proxy routes /mcp /sse /oauth/* etc.
    # Without this, claude.ai / ChatGPT can't connect — even though the
    # endpoints exist, they return 503.

    def _mcp_admin_ctx(request: Request, user: dict, **extra) -> dict:
        tc = t_ctx(request)
        from db import get_setting
        try:
            from db import get_tenant_full_by_user_id, _find_tenant_owner_user_id
            t = get_tenant_full_by_user_id(user["id"])
            if not t:
                oid = _find_tenant_owner_user_id()
                if oid:
                    t = get_tenant_full_by_user_id(oid)
        except Exception:
            t = None
        base = mcp_base_url_or(request)
        return {
            "request": request,
            "user":    user,
            "tenant":  t or {},
            "enabled": get_setting("mcp_enabled", "0") == "1",
            "base_url": base,
            "mcp_url":            f"{base}/mcp",
            "sse_url":            f"{base}/sse",
            "oauth_authorize_url": f"{base}/oauth/authorize",
            "oauth_token_url":     f"{base}/oauth/token",
            "wellknown_url":       f"{base}/.well-known/oauth-authorization-server",
            "active_page": "admin_mcp_access",
            **tc, **extra,
        }

    @app.get("/admin/mcp-access", response_class=HTMLResponse)
    async def admin_mcp_access_page(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        return templates.TemplateResponse(
            "admin_mcp_access.html", _mcp_admin_ctx(request, user),
        )

    @app.post("/admin/mcp-access/toggle", response_class=HTMLResponse)
    async def admin_mcp_access_toggle(request: Request,
                                       enabled: str = Form("")):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        from db import set_setting, audit
        new_state = "1" if enabled in ("1", "on", "true") else "0"
        set_setting("mcp_enabled", new_state)
        audit(user["id"], "mcp_enabled_changed", f"value={new_state}")
        return RedirectResponse("/admin/mcp-access?ok=saved", status_code=302)

    @app.post("/admin/mcp-access/rotate-bearer", response_class=HTMLResponse)
    async def admin_mcp_access_rotate_bearer(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            import secrets as _secrets
            from db import (_conn, get_tenant_full_by_user_id,
                            _find_tenant_owner_user_id, audit)
            t = get_tenant_full_by_user_id(user["id"])
            if not t:
                oid = _find_tenant_owner_user_id()
                t = get_tenant_full_by_user_id(oid) if oid else None
            if not t:
                return RedirectResponse(
                    "/admin/mcp-access?err=no_tenant", status_code=302)
            new_token = _secrets.token_urlsafe(32)
            with _conn() as conn:
                conn.execute(
                    "UPDATE tenants SET bearer_token=? WHERE id=?",
                    (new_token, t["id"]),
                )
                conn.commit()
            audit(user["id"], "mcp_bearer_rotated", f"tenant={t['id']}")
        except Exception as e:
            logger.error("rotate bearer failed: %s", e, exc_info=True)
            return RedirectResponse(
                f"/admin/mcp-access?err={quote_plus(str(e))}", status_code=302)
        return RedirectResponse("/admin/mcp-access?ok=bearer_rotated",
                                status_code=302)

    @app.post("/admin/mcp-access/rotate-oauth", response_class=HTMLResponse)
    async def admin_mcp_access_rotate_oauth(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return RedirectResponse("/login", status_code=302)
        try:
            import secrets as _secrets
            from db import (_conn, get_tenant_full_by_user_id,
                            _find_tenant_owner_user_id, audit)
            t = get_tenant_full_by_user_id(user["id"])
            if not t:
                oid = _find_tenant_owner_user_id()
                t = get_tenant_full_by_user_id(oid) if oid else None
            if not t:
                return RedirectResponse(
                    "/admin/mcp-access?err=no_tenant", status_code=302)
            new_id     = "px-" + _secrets.token_hex(8)
            new_secret = _secrets.token_urlsafe(32)
            with _conn() as conn:
                conn.execute(
                    "UPDATE tenants SET oauth_client_id=?, oauth_client_secret=? "
                    "WHERE id=?",
                    (new_id, new_secret, t["id"]),
                )
                conn.commit()
            audit(user["id"], "mcp_oauth_rotated", f"tenant={t['id']}")
        except Exception as e:
            logger.error("rotate oauth failed: %s", e, exc_info=True)
            return RedirectResponse(
                f"/admin/mcp-access?err={quote_plus(str(e))}", status_code=302)
        return RedirectResponse("/admin/mcp-access?ok=oauth_rotated",
                                status_code=302)

    # ─── Tenant: Printers / Queues / Users+Cards ─────────────────────────────────

    def _make_printix_client(tenant: dict):
        """Erstellt einen PrintixClient aus Tenant-Credentials (Full-Record mit Secrets)."""
        import sys, os
        _src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _src_dir not in sys.path:
            sys.path.insert(0, _src_dir)
        from printix_client import PrintixClient
        return PrintixClient(
            tenant_id=tenant.get("printix_tenant_id", ""),
            print_client_id=tenant.get("print_client_id") or None,
            print_client_secret=tenant.get("print_client_secret") or None,
            card_client_id=tenant.get("card_client_id") or None,
            card_client_secret=tenant.get("card_client_secret") or None,
            ws_client_id=tenant.get("ws_client_id") or None,
            ws_client_secret=tenant.get("ws_client_secret") or None,
            um_client_id=tenant.get("um_client_id") or None,
            um_client_secret=tenant.get("um_client_secret") or None,
            shared_client_id=tenant.get("shared_client_id") or None,
            shared_client_secret=tenant.get("shared_client_secret") or None,
        )


    # ── Slim variant ───────────────────────────────────────────────────────
    # Reports, Capture, Guest-Print, Roadmap submodules are NOT shipped in
    # mysecureprint-server (deleted from src/). The iOS app does not need
    # them. See README "What this is / isn't" for the full diff vs.
    # printix-mcp-docker.

    # ── Cloud Print Port — Employee & Print (v5.12.0) ────────────────────────
    try:
        from cloudprint.db_extensions import init_cloudprint_schema
        init_cloudprint_schema()
        from web.employee_routes import register_employee_routes
        register_employee_routes(app, templates, t_ctx, require_login)
    except Exception as _ep:
        logger.error("Employee-Routen konnten nicht registriert werden: %s", _ep)

    # v0.7.29: Hintergrund-Tasks haengen sich am Event-Loop nur ueber eine
    # weak ref auf — wird die nicht gehalten kann der GC den Task einfach
    # killen. Wir halten sie hier in einem Modul-Set; done-callback raeumt
    # sauber auf.
    _BG_TASKS: set = set()

    def _track_bg(task):
        _BG_TASKS.add(task)
        task.add_done_callback(_BG_TASKS.discard)

    # ── Desktop-Client-API (v6.7.31) ─────────────────────────────────────────
    # v0.6.1: User berichtet /desktop/targets → 404. Step-by-step
    # Diagnose: jeden Sub-Schritt einzeln logging damit wir genau sehen
    # wo's haengt. Auch wenn alles still durchlaeuft sieht man hier ob's
    # bis zum Ende kam.
    # v0.6.8: Root-Cause des /desktop/* 404: init_desktop_schema() crasht
    # bei Azure-Boot mit "unable to open database file" — der Azure-Files-
    # Mount /data ist noch nicht bereit wenn create_app() laeuft. Vorher
    # brach der ganze try-Block ab und KEINE Desktop-Routen wurden
    # registriert; spaetere Requests scheiterten dann mit 404. Fix:
    # Schema-Init und Routen-Registrierung entkoppeln. Routen MUESSEN
    # immer registriert werden. Schema-Init ist idempotent + lazy beim
    # ersten DB-Zugriff (Schema wird beim 1. Token-Insert sowieso
    # erzeugt durch andere Routen, die spaeter laufen).
    logger.info("Desktop-Init: starting…")
    # Schritt 1: Schema lazy versuchen — Fehler nicht fatal
    try:
        from desktop_auth import init_desktop_schema
        init_desktop_schema()
        logger.info("Desktop-Init: init_desktop_schema() OK")
    except Exception as _se:
        logger.warning(
            "Desktop-Init: init_desktop_schema() failed (will retry on first "
            "request) — %s", _se,
        )
    # Schritt 2: Routen registrieren — DAS MUSS klappen, sonst 404
    try:
        from web.desktop_routes import register_desktop_routes
        from app_version import APP_VERSION as _APP_V
        register_desktop_routes(app, lambda: _APP_V)
        logger.info("Desktop-Init: register_desktop_routes() COMPLETED — "
                    "alle /desktop/* sind registriert")
    except Exception as _dp:
        logger.exception("Desktop-Init: register_desktop_routes FAILED:")
        logger.error("Desktop-Routen Fehler: %s (alle /desktop/* werden 404 zurueckgeben!)", _dp)

    # ── Desktop Management (iOS Mgmt-Tab, v6.7.66) ──────────────────────────
    try:
        from web.desktop_management_routes import register_desktop_management_routes
        register_desktop_management_routes(app)
        logger.info("Desktop-Management-Routen registriert: /desktop/management/*")
    except Exception as _dmp:
        logger.error("Desktop-Management-Routen konnten nicht registriert werden: %s", _dmp)

    # ── Desktop Cards (iOS Karten-Tab, v6.7.90) ─────────────────────────────
    try:
        from web.desktop_cards_routes import register_desktop_cards_routes
        register_desktop_cards_routes(app)
        logger.info("Desktop-Cards-Routen registriert: /desktop/cards/*")
    except Exception as _dcp:
        logger.error("Desktop-Cards-Routen konnten nicht registriert werden: %s", _dcp)

    # IPP/IPPS endpoint + listener entfernt (mysecureprint-server v0.1.0).
    # update-check entfernt (kein Pro-License-/Roadmap-UI).

    # TODO (v0.6.4, S-6): migrate to FastAPI lifespan context manager.
    # @app.on_event("startup") ist in FastAPI deprecated. Konservativ
    # belassen, weil alle 5 Startup-Bodies hier auf Closure-Variablen aus
    # create_app() (u.a. _run_printix_user_sync_once) zugreifen — eine
    # saubere Migration erfordert Restrukturierung des Bodies oder den
    # Umzug der lifespan-Funktion ans Ende von create_app() mit
    # `app.router.lifespan_context = lifespan` (FastAPI erlaubt das).
    @app.on_event("startup")
    async def _start_periodic_refresher():
        """Periodic Cache Refresher fuer Printix-API-Antworten."""
        try:
            from cache import start_background_refresher
            start_background_refresher()
        except Exception as e:
            logger.warning("Periodic refresher startup failed: %s", e)

    # v0.1.3: Entra Pending-Tables GC — alle 5 Minuten abgelaufene
    # Eintraege aus desktop_entra_pending + desktop_entra_authcode_pending
    # entfernen. Fail-soft: jede Iteration in try/except, ein DB-Fehler
    # killt den Task nicht.
    # TODO (v0.6.4, S-6): migrate to FastAPI lifespan (s. Hinweis oben).
    @app.on_event("startup")
    async def _start_entra_pending_gc():
        import asyncio as _asyncio

        async def _loop():
            from db import cleanup_expired_pending
            while True:
                try:
                    n = cleanup_expired_pending()
                    if n:
                        logger.debug("Entra pending GC: %d row(s) removed", n)
                except Exception as exc:
                    logger.debug("Entra pending GC tick failed: %s", exc)
                await _asyncio.sleep(300)

        try:
            _track_bg(_asyncio.create_task(_loop()))
            logger.info("Entra pending-tables GC sweep gestartet (5min interval)")
        except Exception as e:
            logger.warning("Entra pending GC startup failed: %s", e)

    # v0.1.3: Entra Continuous-Evaluation Sweep — opt-in. Wenn
    # `entra_continuous_eval_enabled=1`, geht alle 24h durch die
    # gespeicherten refresh_tokens und revoked Server-Bearer-Tokens
    # falls Microsoft den User als deaktiviert meldet.
    # TODO (v0.6.4, S-6): migrate to FastAPI lifespan (s. Hinweis oben).
    @app.on_event("startup")
    async def _start_entra_continuous_eval():
        import asyncio as _asyncio

        async def _loop():
            while True:
                # 24h Pause — der erste Lauf erfolgt mit kurzer Verzoegerung
                # nach dem Start, damit DB sicher initialisiert ist.
                await _asyncio.sleep(60)
                try:
                    from db import get_setting
                    if (get_setting("entra_continuous_eval_enabled", "0")
                            != "1"):
                        # Nicht aktiv — naechsten Tag erneut pruefen
                        await _asyncio.sleep(86400 - 60)
                        continue
                    from entra import run_continuous_evaluation_sweep
                    stats = run_continuous_evaluation_sweep()
                    logger.info(
                        "Entra continuous-eval sweep: checked=%d revoked=%d "
                        "rotated=%d errors=%d",
                        stats.get("checked", 0), stats.get("revoked", 0),
                        stats.get("rotated", 0), stats.get("errors", 0),
                    )
                except Exception as exc:
                    logger.debug("continuous-eval sweep failed: %s", exc)
                await _asyncio.sleep(86400 - 60)

        try:
            _track_bg(_asyncio.create_task(_loop()))
            logger.info("Entra continuous-eval scheduler bereit (opt-in)")
        except Exception as e:
            logger.warning("Entra continuous-eval startup failed: %s", e)


    # v0.5.2: Printix Auto-User-Sync scheduler — opt-in.
    # Sleeps `printix_user_sync_interval_minutes` between iterations,
    # re-reads settings each tick. Sync helper runs via asyncio.to_thread
    # so the synchronous Printix-API calls + DB writes stay off the loop.
    # TODO (v0.6.4, S-6): migrate to FastAPI lifespan (s. Hinweis oben).
    @app.on_event("startup")
    async def _start_printix_user_sync_scheduler():
        import asyncio as _asyncio

        async def _loop():
            # Kurzer Boot-Delay damit init_db sicher durch ist
            await _asyncio.sleep(60)
            while True:
                try:
                    from db import get_setting
                    if get_setting(
                            "printix_user_sync_enabled", "0") != "1":
                        # Disabled: in 5min erneut prüfen
                        await _asyncio.sleep(300)
                        continue
                    try:
                        iv = int(get_setting(
                            "printix_user_sync_interval_minutes", "60") or 60)
                    except Exception:
                        iv = 60
                    iv = max(5, min(1440, iv))
                    try:
                        await _asyncio.to_thread(
                            _run_printix_user_sync_once, "")
                    except Exception as e:
                        logger.warning(
                            "printix-sync iteration failed: %s", e)
                    await _asyncio.sleep(iv * 60)
                except Exception as exc:
                    logger.debug(
                        "printix-sync scheduler tick failed: %s", exc)
                    await _asyncio.sleep(300)

        try:
            _track_bg(_asyncio.create_task(_loop()))
            logger.info("Printix user-sync scheduler bereit (opt-in)")
        except Exception as e:
            logger.warning(
                "Printix user-sync scheduler startup failed: %s", e)


    # v0.3.0: Blob auto-backup — daily scheduler. Opt-in via
    # blob_backup_enabled=1. First tick fires 60s after startup so the DB
    # is initialised; subsequent ticks are 24h apart. Errors are logged
    # but don't crash the loop.
    # TODO (v0.6.4, S-6): migrate to FastAPI lifespan (s. Hinweis oben).
    @app.on_event("startup")
    async def _start_blob_backup_scheduler():
        import asyncio as _asyncio

        async def _loop():
            while True:
                await _asyncio.sleep(60)
                try:
                    from db import get_setting, set_setting
                    if get_setting("blob_backup_enabled", "0") != "1":
                        await _asyncio.sleep(86400 - 60)
                        continue
                    last = get_setting("blob_backup_last_run_at", "")
                    if last:
                        from datetime import datetime as _dt, timezone as _tz
                        try:
                            last_dt = _dt.fromisoformat(last)
                            if last_dt.tzinfo is None:
                                last_dt = last_dt.replace(tzinfo=_tz.utc)
                            age = (_dt.now(_tz.utc) - last_dt).total_seconds()
                            if age < 86000:  # already ran in the last ~24h
                                await _asyncio.sleep(86400 - 60)
                                continue
                        except Exception:
                            pass
                    from blob_backup import run_once
                    # run_once does its own DB+upload work; offload to a
                    # thread so we don't block the event loop.
                    r = await _asyncio.to_thread(run_once)
                    logger.info(
                        "blob backup daily run: ok=%s blob=%s size=%d err=%s",
                        r.get("ok"), r.get("blob_name", "-"),
                        r.get("size", 0), r.get("error", "-"),
                    )
                except Exception as exc:
                    logger.warning("blob backup tick failed: %s", exc)
                await _asyncio.sleep(86400 - 60)

        try:
            _track_bg(_asyncio.create_task(_loop()))
            logger.info("Blob-Backup-Scheduler bereit (täglich, opt-in)")
        except Exception as e:
            logger.warning("blob backup scheduler startup failed: %s", e)

    # ─── MCP Proxy (v0.4.0, opt-in) ───────────────────────────────────────
    # Reicht /mcp, /sse, /oauth, /messages, /register und /.well-known vom
    # Web-Port (8080) an den lokalen MCP-Server (default 127.0.0.1:8765)
    # durch. Azure App Service exposed nur 8080 — der MCP-Server ist also
    # nicht direkt erreichbar; ausschliesslich diese Proxy-Routen geben
    # Aussenwelt-Zugriff. Wenn `mcp_enabled` Setting = 0 (Default), gibt
    # die Route 503 zurueck und der MCP-Sub-Prozess wird gar nicht erst
    # gestartet (siehe entrypoint.sh).
    #
    # Streaming-by-default — Streamable-HTTP-Transport haengt sonst 300s
    # bevor httpx den Body komplett puffert. SSE-Verbindungen bleiben
    # solange offen wie der Client sie offen haelt.

    def _mcp_proxy_enabled() -> bool:
        try:
            from db import get_setting
            return get_setting("mcp_enabled", "0") == "1"
        except Exception:
            return False

    @app.api_route("/mcp", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    @app.api_route("/mcp/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def mcp_proxy(request: Request, rest: str = ""):
        if not _mcp_proxy_enabled():
            return JSONResponse({"detail": "MCP server disabled"}, status_code=503)
        return await _proxy_to_mcp(request, "/mcp" + (("/" + rest) if rest else ""))

    @app.api_route("/sse", methods=["GET", "POST"])
    @app.api_route("/sse/{rest:path}", methods=["GET", "POST"])
    async def sse_proxy(request: Request, rest: str = ""):
        if not _mcp_proxy_enabled():
            return JSONResponse({"detail": "MCP server disabled"}, status_code=503)
        return await _proxy_to_mcp(request, "/sse" + (("/" + rest) if rest else ""))

    @app.api_route("/messages", methods=["GET", "POST"])
    @app.api_route("/messages/", methods=["GET", "POST"])
    @app.api_route("/messages/{rest:path}", methods=["GET", "POST"])
    async def messages_proxy(request: Request, rest: str = ""):
        if not _mcp_proxy_enabled():
            return JSONResponse({"detail": "MCP server disabled"}, status_code=503)
        return await _proxy_to_mcp(request, "/messages" + (("/" + rest) if rest else "/"))

    @app.api_route("/oauth/{rest:path}", methods=["GET", "POST"])
    async def oauth_proxy(request: Request, rest: str):
        if not _mcp_proxy_enabled():
            return JSONResponse({"detail": "MCP server disabled"}, status_code=503)
        return await _proxy_to_mcp(request, "/oauth/" + rest)

    @app.api_route("/.well-known/{rest:path}", methods=["GET"])
    async def wellknown_proxy(request: Request, rest: str):
        if not _mcp_proxy_enabled():
            return JSONResponse({"detail": "MCP server disabled"}, status_code=503)
        return await _proxy_to_mcp(request, "/.well-known/" + rest)

    async def _proxy_to_mcp(request: Request, path: str):
        try:
            import httpx as _httpx
        except Exception as e:
            return JSONResponse(
                {"detail": f"MCP proxy unavailable: httpx not installed ({e})"},
                status_code=503,
            )
        from fastapi.responses import StreamingResponse

        mcp_port = (os.environ.get("MCP_PORT", "") or "8765").strip()
        target = f"http://127.0.0.1:{mcp_port}{path}"

        excluded_request = {
            "host", "content-length", "connection", "keep-alive", "transfer-encoding",
            "upgrade", "proxy-authenticate", "proxy-authorization", "te", "trailers",
        }
        forward_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in excluded_request
        }
        body = await request.body() if request.method in ("POST", "PUT", "PATCH") else None

        # v0.7.29: kein timeout=None mehr — ein haengender MCP-Prozess
        # blockierte sonst Worker-Slots fuer immer. read=None ist OK weil
        # MCP SSE-streamed, aber connect/write/pool brauchen feste Caps.
        client = _httpx.AsyncClient(
            timeout=_httpx.Timeout(connect=5.0, read=None,
                                       write=30.0, pool=5.0))
        try:
            req = client.build_request(
                request.method, target,
                params=request.query_params,
                content=body,
                headers=forward_headers,
            )
            resp = await client.send(req, stream=True)
        except _httpx.ConnectError as ce:
            try: await client.aclose()
            except Exception: pass
            return JSONResponse(
                {"detail": f"MCP server not reachable on localhost:{mcp_port} ({ce})"},
                status_code=502,
            )
        except Exception as e:
            try: await client.aclose()
            except Exception: pass
            # v0.7.29: Exception-Text nicht roh nach aussen — interne
            # Pfade/Versionen leakten an MCP-Clients.
            logger.warning("MCP proxy error: %s", e)
            return JSONResponse(
                {"detail": "MCP proxy error (see server logs)"},
                status_code=502,
            )

        excluded_response = {
            "content-length", "connection", "keep-alive", "transfer-encoding",
            "upgrade", "proxy-authenticate", "te", "trailers",
        }
        out_headers = {
            k: v for k, v in resp.headers.items()
            if k.lower() not in excluded_response
        }

        async def _stream():
            try:
                async for chunk in resp.aiter_raw():
                    yield chunk
            finally:
                try: await resp.aclose()
                except Exception: pass
                try: await client.aclose()
                except Exception: pass

        return StreamingResponse(
            _stream(),
            status_code=resp.status_code,
            headers=out_headers,
            media_type=resp.headers.get("content-type"),
        )

    # v7.2.36: Auto-TLS (sslip.io + Let's Encrypt) — Renewal-Scheduler
    # starten. Daemon-Thread, weckt alle 24h und ruft certbot renew wenn
    # auto_tls_enabled=1 ist. Bei kürzlich-acquirierten Certs (>60 days
    # remaining) ist das idempotent — keine Action.
    try:
        import sys as _acme_sys
        if "/app" not in _acme_sys.path:
            _acme_sys.path.insert(0, "/app")
        from acme_auto import start_renewal_scheduler as _acme_start
        _acme_start()
    except Exception as e:
        logger.warning("auto-tls renewal scheduler not started: %s", e)

    # v7.2.32: persistierter Tunnel-Mode → bei App-Start ggf. wiederherstellen.
    # In einem separaten Thread, damit ein nicht-erreichbarer Cloudflare-
    # Endpoint den Boot der Web-UI nicht blockiert.
    try:
        import threading as _tunnel_threading
        from tunnel import auto_start_from_settings

        def _delayed_start():
            import time as _t
            _t.sleep(2)  # DB sollte bereit sein, init_db ist vor uns gelaufen
            try:
                auto_start_from_settings()
            except Exception as exc:
                logger.warning("tunnel auto-start failed: %s", exc)

        _tunnel_threading.Thread(target=_delayed_start, daemon=True).start()
    except Exception as e:
        logger.warning("tunnel auto-start scheduling failed: %s", e)

    # v0.7.27: Guest-Print / Email-to-Print Gateway. Routes + Polling-Task.
    # Opt-in via `guestprint_enabled` Setting + min. 1 konfigurierter
    # Mailbox; sonst still idle. Polling-Schleife sleept und re-prueft
    # Setting jeden Tick, so dass Toggle ohne App-Restart funktioniert.
    try:
        from web.guestprint_routes import register as _gp_register

        def _gp_resolve_tenant_id(user):
            if not user:
                return ""
            try:
                from db import get_tenant_full_by_user_id
                t = get_tenant_full_by_user_id(user.get("user_id") or user.get("id"))
                return (t or {}).get("id", "") if t else ""
            except Exception:
                return ""

        _gp_register(
            app,
            require_login_fn=require_login,
            get_active_tenant_id_fn=_gp_resolve_tenant_id,
            templates=templates,
            set_setting_fn=set_setting,
            get_setting_fn=get_setting,
            audit_fn=audit,
        )
        logger.info("Guest-Print routes registriert")
    except Exception as e:
        logger.warning("Guest-Print routes registration failed: %s", e)

    @app.on_event("startup")
    async def _start_guestprint_runner():
        try:
            import guestprint as _gp
            # submit_print_job_fn=None → Jobs werden nur loggend erfasst.
            # Tatsaechliches Drucken erfolgt in v0.8.x via cloudprint-Bridge.
            _gp.start_runner(submit_print_job_fn=None)
            logger.info("Guest-Print runner bereit (opt-in via guestprint_enabled)")
        except Exception as e:
            logger.warning("Guest-Print runner startup failed: %s", e)

    return app
