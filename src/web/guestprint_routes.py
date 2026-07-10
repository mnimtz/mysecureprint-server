"""Admin-Routes fuer Guest-Print / Email-to-Print.

Mountet:
  GET  /admin/guestprint                — Mailbox-Liste + globaler Toggle
  POST /admin/guestprint/toggle         — globalen Schalter setzen
  POST /admin/guestprint/mailbox        — Mailbox anlegen
  POST /admin/guestprint/mailbox/{mid}/update    — Felder aendern
  POST /admin/guestprint/mailbox/{mid}/delete    — Mailbox loeschen
  POST /admin/guestprint/mailbox/{mid}/poll-now  — manueller Tick
  GET  /admin/guestprint/mailbox/{mid}            — Detail (Whitelist + Jobs)
  POST /admin/guestprint/mailbox/{mid}/guest      — Whitelist-Eintrag anlegen
  POST /admin/guestprint/guest/{gid}/update       — Eintrag aendern
  POST /admin/guestprint/guest/{gid}/delete       — Eintrag loeschen

Zugriff: nur Admin (is_admin=1).
Schutz: alle POST-Routen sind tenant-scoped (Mailbox/Guest gehoeren zu
einem Tenant); URL-Injection mit fremden mailbox_ids landet auf 404 weil
get_mailbox + tenant-check.
"""
from __future__ import annotations

import logging
import urllib.parse
from fastapi import Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

logger = logging.getLogger(__name__)


def register(app, *, require_login_fn, get_active_tenant_id_fn,
              templates, set_setting_fn, get_setting_fn,
              audit_fn=None):
    """Mountet die Routen am FastAPI-App. Wird aus web/app.py beim
    Boot aufgerufen, damit alle Dependencies (Templates, Auth-Helper,
    Settings) verbunden sind."""
    import guestprint as gp

    def _admin_or_403(request: Request):
        user = require_login_fn(request)
        if not user:
            return None, RedirectResponse("/login", status_code=302)
        if not user.get("is_admin"):
            return None, JSONResponse({"detail": "admin only"},
                                         status_code=403)
        return user, None

    def _audit(user, action, **kw):
        if not audit_fn:
            return
        try:
            import json as _json
            audit_fn(user.get("user_id") if user else None,
                       action,
                       details=_json.dumps(kw, ensure_ascii=False)[:1000])
        except Exception as e:
            logger.debug("audit %s failed: %s", action, e)

    # ── Mailbox-Liste + Toggle ──────────────────────────────────────────────

    @app.get("/admin/guestprint/mail-folders", response_class=JSONResponse)
    async def gp_list_folders(request: Request, upn: str = ""):
        """Liest die Ordnerstruktur einer Mailbox live via Graph aus.
        Wird vom UI-Dropdown genutzt (Quell-Ordner auswaehlen)."""
        user, redirect = _admin_or_403(request)
        if redirect:
            return redirect
        upn = (upn or "").strip()
        if "@" not in upn:
            return JSONResponse({"error": "upn required"}, status_code=400)
        try:
            folders = gp.list_mail_folders(upn)
            return JSONResponse({"folders": folders})
        except Exception as e:
            logger.warning("mail-folders fetch %s: %s", upn, e)
            return JSONResponse({"error": str(e)[:200], "folders": []},
                                  status_code=500)

    @app.get("/admin/guestprint", response_class=HTMLResponse)
    async def gp_list(request: Request):
        user, redirect = _admin_or_403(request)
        if redirect:
            return redirect
        tid = get_active_tenant_id_fn(user) or ""
        mboxes = gp.list_mailboxes(tid)
        enabled = get_setting_fn("guestprint_enabled", "0") == "1"
        return templates.TemplateResponse(
            "admin_guestprint.html",
            {"request": request, "user": user, "mboxes": mboxes,
              "enabled": enabled,
              "view": "list", "selected": None, "guests": [], "jobs": []})

    @app.post("/admin/guestprint/toggle", response_class=JSONResponse)
    async def gp_toggle(request: Request,
                          enabled: str = Form(default="0")):
        user, redirect = _admin_or_403(request)
        if redirect:
            return redirect
        v = "1" if (enabled or "").strip() in ("1", "on", "true") else "0"
        set_setting_fn("guestprint_enabled", v)
        _audit(user, "guestprint_toggled", value=v)
        return JSONResponse({"ok": True, "enabled": v == "1"})

    # ── Mailbox CRUD ────────────────────────────────────────────────────────

    @app.post("/admin/guestprint/mailbox", response_class=RedirectResponse)
    async def gp_mb_create(request: Request,
                              upn: str = Form(...),
                              name: str = Form(default=""),
                              default_printer_id: str = Form(default=""),
                              default_queue_id: str = Form(default=""),
                              poll_interval_sec: int = Form(default=60),
                              source_folder: str = Form(default="Inbox"),
                              folder_processed: str = Form(default="GuestPrint/Processed"),
                              folder_skipped: str = Form(default="GuestPrint/Skipped"),
                              on_success: str = Form(default="move"),
                              max_attachment_bytes: int = Form(default=26214400),
                              notify_sender: str = Form(default=""),
                              enabled: str = Form(default="1")):
        user, redirect = _admin_or_403(request)
        if redirect:
            return redirect
        tid = get_active_tenant_id_fn(user) or ""
        try:
            mid = gp.create_mailbox(
                tenant_id=tid, upn=upn, name=name,
                default_printer_id=default_printer_id,
                default_queue_id=default_queue_id,
                poll_interval_sec=poll_interval_sec,
                source_folder=source_folder,
                folder_processed=folder_processed,
                folder_skipped=folder_skipped,
                on_success=on_success,
                max_attachment_bytes=max_attachment_bytes,
                notify_sender=(notify_sender in ("1", "on", "true")),
                enabled=(enabled in ("1", "on", "true")))
        except ValueError as e:
            return RedirectResponse(
                f"/admin/guestprint?error={urllib.parse.quote_plus(str(e))}",
                status_code=303)
        _audit(user, "guestprint_mailbox_created", mailbox_id=mid, upn=upn)
        return RedirectResponse(f"/admin/guestprint/mailbox/{mid}",
                                  status_code=303)

    @app.get("/admin/guestprint/mailbox/{mid}", response_class=HTMLResponse)
    async def gp_mb_detail(request: Request, mid: str):
        user, redirect = _admin_or_403(request)
        if redirect:
            return redirect
        tid = get_active_tenant_id_fn(user) or ""
        mb = gp.get_mailbox(mid)
        if not mb or mb.get("tenant_id") != tid:
            return JSONResponse({"detail": "not found"}, status_code=404)
        return templates.TemplateResponse(
            "admin_guestprint.html",
            {"request": request, "user": user,
              "mboxes": gp.list_mailboxes(tid),
              "enabled": get_setting_fn("guestprint_enabled", "0") == "1",
              "view": "detail", "selected": mb,
              "guests": gp.list_guests(mid),
              "jobs": gp.list_jobs(mid, limit=50)})

    @app.post("/admin/guestprint/mailbox/{mid}/update",
                response_class=RedirectResponse)
    async def gp_mb_update(request: Request, mid: str,
                              name: str = Form(default=None),
                              default_printer_id: str = Form(default=None),
                              default_queue_id: str = Form(default=None),
                              poll_interval_sec: int = Form(default=None),
                              source_folder: str = Form(default=None),
                              folder_processed: str = Form(default=None),
                              folder_skipped: str = Form(default=None),
                              on_success: str = Form(default=None),
                              max_attachment_bytes: int = Form(default=None),
                              notify_sender: str = Form(default=None),
                              enabled: str = Form(default=None)):
        user, redirect = _admin_or_403(request)
        if redirect:
            return redirect
        tid = get_active_tenant_id_fn(user) or ""
        mb = gp.get_mailbox(mid)
        if not mb or mb.get("tenant_id") != tid:
            return JSONResponse({"detail": "not found"}, status_code=404)
        fields = {}
        for k, v in dict(
                name=name, default_printer_id=default_printer_id,
                default_queue_id=default_queue_id,
                poll_interval_sec=poll_interval_sec,
                source_folder=source_folder,
                folder_processed=folder_processed,
                folder_skipped=folder_skipped,
                on_success=on_success,
                max_attachment_bytes=max_attachment_bytes).items():
            if v is not None:
                fields[k] = v
        if enabled is not None:
            fields["enabled"] = enabled in ("1", "on", "true")
        if notify_sender is not None:
            fields["notify_sender"] = notify_sender in ("1", "on", "true")
        gp.update_mailbox(mid, **fields)
        _audit(user, "guestprint_mailbox_updated",
                 mailbox_id=mid, fields=list(fields.keys()))
        return RedirectResponse(f"/admin/guestprint/mailbox/{mid}",
                                  status_code=303)

    @app.post("/admin/guestprint/mailbox/{mid}/delete",
                response_class=RedirectResponse)
    async def gp_mb_delete(request: Request, mid: str):
        user, redirect = _admin_or_403(request)
        if redirect:
            return redirect
        tid = get_active_tenant_id_fn(user) or ""
        mb = gp.get_mailbox(mid)
        if not mb or mb.get("tenant_id") != tid:
            return JSONResponse({"detail": "not found"}, status_code=404)
        gp.delete_mailbox(mid)
        _audit(user, "guestprint_mailbox_deleted", mailbox_id=mid)
        return RedirectResponse("/admin/guestprint", status_code=303)

    @app.post("/admin/guestprint/mailbox/{mid}/poll-now",
                response_class=JSONResponse)
    async def gp_mb_poll_now(request: Request, mid: str):
        user, redirect = _admin_or_403(request)
        if redirect:
            return redirect
        tid = get_active_tenant_id_fn(user) or ""
        mb = gp.get_mailbox(mid)
        if not mb or mb.get("tenant_id") != tid:
            return JSONResponse({"detail": "not found"}, status_code=404)
        import asyncio as _asyncio
        try:
            stats = await _asyncio.to_thread(gp.poll_mailbox_once, mid, None)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)[:300]},
                                   status_code=500)
        _audit(user, "guestprint_manual_poll", mailbox_id=mid, stats=stats)
        return JSONResponse({"ok": True, "stats": stats})

    # ── Guest-Whitelist CRUD ────────────────────────────────────────────────

    @app.post("/admin/guestprint/mailbox/{mid}/guest",
                response_class=RedirectResponse)
    async def gp_guest_create(request: Request, mid: str,
                                 sender_email: str = Form(...),
                                 full_name: str = Form(default=""),
                                 printer_id: str = Form(default=""),
                                 queue_id: str = Form(default=""),
                                 printix_user_id: str = Form(default=""),
                                 expiration_days: int = Form(default=7),
                                 enabled: str = Form(default="1")):
        user, redirect = _admin_or_403(request)
        if redirect:
            return redirect
        tid = get_active_tenant_id_fn(user) or ""
        mb = gp.get_mailbox(mid)
        if not mb or mb.get("tenant_id") != tid:
            return JSONResponse({"detail": "not found"}, status_code=404)
        try:
            gid = gp.add_guest(
                mailbox_id=mid, sender_email=sender_email,
                full_name=full_name, printer_id=printer_id,
                queue_id=queue_id, printix_user_id=printix_user_id,
                expiration_days=expiration_days,
                enabled=(enabled in ("1", "on", "true")))
        except ValueError as e:
            return RedirectResponse(
                f"/admin/guestprint/mailbox/{mid}?error="
                f"{urllib.parse.quote_plus(str(e))}", status_code=303)
        _audit(user, "guestprint_guest_added",
                 mailbox_id=mid, guest_id=gid, sender=sender_email)
        return RedirectResponse(f"/admin/guestprint/mailbox/{mid}",
                                  status_code=303)

    @app.post("/admin/guestprint/guest/{gid}/update",
                response_class=RedirectResponse)
    async def gp_guest_update(request: Request, gid: str,
                                 full_name: str = Form(default=None),
                                 printer_id: str = Form(default=None),
                                 queue_id: str = Form(default=None),
                                 expiration_days: int = Form(default=None),
                                 enabled: str = Form(default=None)):
        user, redirect = _admin_or_403(request)
        if redirect:
            return redirect
        tid = get_active_tenant_id_fn(user) or ""
        g = gp.get_guest(gid)
        if not g:
            return JSONResponse({"detail": "not found"}, status_code=404)
        mb = gp.get_mailbox(g["mailbox_id"])
        if not mb or mb.get("tenant_id") != tid:
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        fields = {}
        for k, v in dict(
                full_name=full_name, printer_id=printer_id,
                queue_id=queue_id, expiration_days=expiration_days).items():
            if v is not None:
                fields[k] = v
        if enabled is not None:
            fields["enabled"] = enabled in ("1", "on", "true")
        gp.update_guest(gid, **fields)
        _audit(user, "guestprint_guest_updated",
                 guest_id=gid, fields=list(fields.keys()))
        return RedirectResponse(
            f"/admin/guestprint/mailbox/{g['mailbox_id']}", status_code=303)

    @app.post("/admin/guestprint/guest/{gid}/delete",
                response_class=RedirectResponse)
    async def gp_guest_delete(request: Request, gid: str):
        user, redirect = _admin_or_403(request)
        if redirect:
            return redirect
        tid = get_active_tenant_id_fn(user) or ""
        g = gp.get_guest(gid)
        if not g:
            return JSONResponse({"detail": "not found"}, status_code=404)
        mb = gp.get_mailbox(g["mailbox_id"])
        if not mb or mb.get("tenant_id") != tid:
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        gp.delete_guest(gid)
        _audit(user, "guestprint_guest_deleted", guest_id=gid)
        return RedirectResponse(
            f"/admin/guestprint/mailbox/{g['mailbox_id']}", status_code=303)
