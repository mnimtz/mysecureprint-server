"""Admin-Routen für Toner-Alert-Konfiguration.

GET  /admin/toner              — Settings-Seite mit Live-Übersicht
POST /admin/toner/settings     — Settings speichern
POST /admin/toner/test         — Test-Mail schicken
POST /admin/toner/run          — Sofort-Prüfung anstossen (statt auf Runner warten)
GET  /admin/toner/preview      — Live-JSON der aktuellen Drucker+Levels
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)


def register_toner_alert_routes(app: FastAPI,
                                templates: Jinja2Templates,
                                get_session_user,
                                t_ctx) -> None:

    def _admin_or_login(request: Request):
        user = get_session_user(request)
        if not user or not user.get("is_admin"):
            return None
        return user

    def _load_tenant(user: dict) -> Optional[dict]:
        import sys, os
        src_dir = os.path.dirname(os.path.dirname(__file__))
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        from db import get_tenant_full_by_user_id, _resolve_tenant_owner_for
        owner = _resolve_tenant_owner_for(user["id"]) or user["id"]
        return get_tenant_full_by_user_id(owner)

    def _tenant_key(tenant: dict) -> str:
        return str(tenant.get("id") or tenant.get("user_id") or "")

    @app.get("/admin/toner", response_class=HTMLResponse)
    async def toner_page(request: Request):
        user = _admin_or_login(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        import sys, os
        src_dir = os.path.dirname(os.path.dirname(__file__))
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        import toner_alerts as ta
        from bi_client import fetch_all_printer_supplies, estimate_days_until_empty

        tenant = _load_tenant(user)
        if not tenant:
            return templates.TemplateResponse("admin_toner.html", {
                "request": request, "user": user,
                "cfg": ta.DEFAULT_SETTINGS,
                "alert_printers": [], "preview_printers": [], "log_entries": [],
                "bi_configured": False, "bi_reachable": False,
                "tenant_key": "",
                "active_page": "admin_toner",
                **t_ctx(request),
            })

        tid = _tenant_key(tenant)
        cfg = ta.get_settings(tid)

        # v0.7.267: unterscheide "keine Creds hinterlegt" (bi_configured=False)
        # von "Creds da, aber DB gerade nicht erreichbar" (bi_configured=True,
        # bi_reachable=False). Vorher haben beide Faelle den gleichen gelben
        # Banner produziert — irrefuehrend wenn die Creds eigentlich drin sind.
        bi_configured = all([
            (tenant.get("sql_server")   or "").strip(),
            (tenant.get("sql_database") or "").strip(),
            (tenant.get("sql_username") or "").strip(),
            (tenant.get("sql_password") or "").strip(),
        ])
        printers = fetch_all_printer_supplies(tenant) if bi_configured else None
        bi_reachable = printers is not None

        # v0.7.268: pro Drucker eine Karte fuer aktive Alarme. Enthaelt
        # ALLE Supplies des Druckers (auch die im gruenen Bereich) — nur
        # Drucker die mind. eine Farbe unter Schwelle haben werden gezeigt.
        alert_printers = []
        preview_printers = []
        if printers:
            warn_v = int(cfg.get("threshold_warn", 20))
            crit_v = int(cfg.get("threshold_critical", 5))
            lead = int(cfg.get("lead_time_days", 0))
            for p in printers:
                p_supplies = []
                worst_sev = "ok"
                for s in p.get("supplies", []):
                    d = (estimate_days_until_empty(
                            tenant, p["printer_id"], s["color"], s["level"])
                         if lead > 0 else None)
                    sev = ta.classify_severity(s["level"], warn_v, crit_v, d, lead)
                    p_supplies.append({
                        "color": s["color"], "level": s["level"],
                        "days_left": d, "severity": sev,
                    })
                    if sev == "critical" or (sev == "warn" and worst_sev != "critical"):
                        worst_sev = sev
                errs = p.get("error_states") or []
                # Ein Drucker landet in alert_printers wenn mind. eine Farbe
                # unter Schwelle liegt ODER ein Error-State (LOW_TONER etc)
                # gemeldet ist.
                if worst_sev != "ok" or errs:
                    alert_printers.append({
                        "printer_id":   p["printer_id"],
                        "printer_name": p.get("printer_name") or p["printer_id"][:8],
                        "location":     p.get("location") or "",
                        "state":        p.get("reported_state") or "",
                        "error_states": errs,
                        "supplies":     p_supplies,
                        "worst_sev":    "critical" if errs and worst_sev == "ok" else worst_sev,
                    })
                # Auch Drucker ohne Toner-Daten in den Picker aufnehmen
                preview_printers.append({
                    "printer_id":   p["printer_id"],
                    "printer_name": p.get("printer_name") or p["printer_id"][:8],
                    "location":     p.get("location") or "",
                    "state":        p.get("reported_state") or "",
                    "error_states": errs,
                    "supplies":     p_supplies,
                })
            # Kritische Drucker nach oben sortieren
            alert_printers.sort(
                key=lambda a: (0 if a["worst_sev"] == "critical" else 1,
                               a["printer_name"].lower()))

        log_entries = ta.recent_log(tid, limit=30)
        return templates.TemplateResponse("admin_toner.html", {
            "request": request, "user": user,
            "cfg": cfg,
            "alert_printers": alert_printers,
            "preview_printers": preview_printers,
            "log_entries": log_entries,
            "bi_configured": bi_configured,
            "bi_reachable":  bi_reachable,
            "tenant_key": tid,
            "active_page": "admin_toner",
            **t_ctx(request),
        })

    @app.post("/admin/toner/settings")
    async def toner_save(request: Request,
                         enabled: str = Form(default=""),
                         threshold_warn: int = Form(default=20),
                         threshold_critical: int = Form(default=5),
                         hysteresis_percent: int = Form(default=10),
                         recipients: str = Form(default=""),
                         check_interval_min: int = Form(default=60),
                         digest_mode: str = Form(default=""),
                         digest_hour_utc: int = Form(default=7),
                         quiet_hours_start: int = Form(default=-1),
                         quiet_hours_end: int = Form(default=-1),
                         lead_time_days: int = Form(default=0),
                         include_error_states: str = Form(default="")):
        user = _admin_or_login(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        tenant = _load_tenant(user)
        if not tenant:
            return RedirectResponse("/admin/toner?err=no_tenant",
                                    status_code=303)
        import sys, os
        src_dir = os.path.dirname(os.path.dirname(__file__))
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        import toner_alerts as ta

        # Grenzen erzwingen
        threshold_warn = max(0, min(99, int(threshold_warn)))
        threshold_critical = max(0, min(threshold_warn - 1, int(threshold_critical)))
        if threshold_critical < 0:
            threshold_critical = 0
        hysteresis_percent = max(0, min(50, int(hysteresis_percent)))
        check_interval_min = max(15, min(1440, int(check_interval_min)))
        digest_hour_utc = max(0, min(23, int(digest_hour_utc)))
        quiet_hours_start = max(-1, min(23, int(quiet_hours_start)))
        quiet_hours_end = max(-1, min(23, int(quiet_hours_end)))
        lead_time_days = max(0, min(30, int(lead_time_days)))

        ta.upsert_settings(
            _tenant_key(tenant),
            enabled=1 if enabled else 0,
            threshold_warn=threshold_warn,
            threshold_critical=threshold_critical,
            hysteresis_percent=hysteresis_percent,
            recipients=(recipients or "").strip()[:1000],
            check_interval_min=check_interval_min,
            digest_mode=1 if digest_mode else 0,
            digest_hour_utc=digest_hour_utc,
            quiet_hours_start=quiet_hours_start,
            quiet_hours_end=quiet_hours_end,
            lead_time_days=lead_time_days,
            include_error_states=1 if include_error_states else 0,
        )
        return RedirectResponse("/admin/toner?ok=1", status_code=303)

    @app.post("/admin/toner/test", response_class=JSONResponse)
    async def toner_test(request: Request):
        user = _admin_or_login(request)
        if not user:
            return JSONResponse({"error": "auth"}, status_code=403)
        tenant = _load_tenant(user)
        if not tenant:
            return JSONResponse({"error": "no_tenant"}, status_code=404)
        import sys, os, asyncio
        src_dir = os.path.dirname(os.path.dirname(__file__))
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        import toner_alerts as ta
        try:
            report = await asyncio.to_thread(
                ta.evaluate_and_notify, tenant, force_send=True)
            return JSONResponse({"ok": True, "report": report})
        except Exception as e:  # noqa: BLE001
            logger.warning("toner test failed: %s", e)
            return JSONResponse({"ok": False, "error": str(e)[:200]},
                                status_code=500)

    @app.post("/admin/toner/run", response_class=JSONResponse)
    async def toner_run_now(request: Request):
        user = _admin_or_login(request)
        if not user:
            return JSONResponse({"error": "auth"}, status_code=403)
        tenant = _load_tenant(user)
        if not tenant:
            return JSONResponse({"error": "no_tenant"}, status_code=404)
        import sys, os, asyncio
        src_dir = os.path.dirname(os.path.dirname(__file__))
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        import toner_alerts as ta
        try:
            report = await asyncio.to_thread(ta.evaluate_and_notify, tenant)
            return JSONResponse({"ok": True, "report": report})
        except Exception as e:  # noqa: BLE001
            logger.warning("toner run failed: %s", e)
            return JSONResponse({"ok": False, "error": str(e)[:200]},
                                status_code=500)
