"""
AirPrint Job Submit (v0.8.0)
=============================
Reicht IPP-Print-Jobs die über /airprint/{token} reingekommen sind
durch die Standard-Upload-Pipeline an Printix weiter.

Bewusste Wiederverwendung: statt einen eigenen Printix-Submit-Path zu
bauen, nutzen wir die selbe Logik wie der /desktop/upload-Endpoint —
so landen AirPrint-Jobs mit exakt derselben Metadaten-Shape in
cloudprint_jobs und tauchen im Job-Verlauf der App auf.
"""

from __future__ import annotations

import asyncio
import logging
import uuid as _uuid

logger = logging.getLogger("printix.airprint.submit")


async def submit_airprint_job(user_id: str,
                                user_email: str,
                                printer_id: str,
                                queue_id: str,
                                queue_display_name: str,
                                file_bytes: bytes,
                                job_name: str,
                                doc_format: str = "application/pdf",
                                origin_host: str = "-",
                                source: str = "airprint",
                                internal_job_id: str = "") -> str:
    """Wrapper der einen frisch empfangenen IPP-Job in cloudprint_jobs
    speichert und an Printix weiterleitet.

    Design-Entscheidung: wir schreiben direkt in cloudprint_jobs (statt
    /desktop/upload aufzurufen) weil wir hier keinen Bearer-Token haben
    — der Server ist selbst schon authenifiziert via Profile-Token.
    """
    from db import _conn, _resolve_tenant_owner_for
    from cloudprint.db_extensions import get_tenant_for_user
    from printix_client import PrintixClient

    if not internal_job_id:
        internal_job_id = _uuid.uuid4().hex[:8]

    # ─── Tenant + Printix-Credentials für den User ────────────────────
    parent_id = _resolve_tenant_owner_for(user_id) or user_id
    tenant = get_tenant_for_user(parent_id)
    if not tenant:
        logger.error(
            "AirPrint submit: kein Tenant für user=%s — Job verworfen",
            user_id,
        )
        return internal_job_id

    from db import get_tenant_full_by_user_id
    full_tenant = get_tenant_full_by_user_id(parent_id)
    if not full_tenant:
        logger.error("AirPrint submit: get_tenant_full liefert None")
        return internal_job_id

    client = PrintixClient(
        tenant_id=full_tenant["printix_tenant_id"],
        print_client_id=full_tenant.get("print_client_id", ""),
        print_client_secret=full_tenant.get("print_client_secret", ""),
        shared_client_id=full_tenant.get("shared_client_id", ""),
        shared_client_secret=full_tenant.get("shared_client_secret", ""),
        um_client_id=full_tenant.get("um_client_id", ""),
        um_client_secret=full_tenant.get("um_client_secret", ""),
    )

    # ─── DB-Row anlegen ──────────────────────────────────────────────
    import time
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    with _conn() as conn:
        conn.execute(
            """INSERT INTO cloudprint_jobs (
                    job_id, tenant_id, queue_name, username, hostname,
                    job_name, data_size, data_format,
                    detected_identity, identity_source,
                    status, received_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (internal_job_id,
             tenant["id"],
             queue_display_name,
             user_email or "",
             f"iOS AirPrint ({origin_host})",
             job_name[:200],
             len(file_bytes),
             doc_format,
             user_email or "",
             f"airprint-token",
             "queued",
             now_iso, now_iso),
        )

    # ─── Preview-PNG + AI-Analyse im Hintergrund ──────────────────────
    # (Gleicher Ablauf wie /desktop/upload — damit AirPrint-Jobs im
    # Job-Verlauf mit Thumbnail und ai_doc_type/ai_tags erscheinen.)
    asyncio.create_task(
        _run_preview_and_ai(
            internal_job_id=internal_job_id,
            file_bytes=file_bytes,
            job_name=job_name,
            full_tenant=full_tenant,
            user_id=user_id,
        )
    )

    # ─── An Printix submitten (Print API v1) ─────────────────────────
    try:
        submit_resp = await asyncio.to_thread(
            client.submit_print_job,
            printer_id=printer_id,
            queue_id=queue_id,
            title=job_name[:200],
            user_email=user_email,
            release_immediately=False,  # SecurePrint → am Drucker freigeben
        )
        px_job_id = ""
        if isinstance(submit_resp, dict):
            px_job_id = (
                submit_resp.get("jobId")
                or submit_resp.get("id")
                or (submit_resp.get("job") or {}).get("id", "")
                or ""
            )
        if not px_job_id:
            logger.error(
                "AirPrint submit: Printix submit lieferte keine job_id: %r",
                submit_resp,
            )
            _mark_error(internal_job_id, "Printix submit ohne job_id")
            return internal_job_id

        # ─── Datei an Printix uploaden ────────────────────────────
        upload_url = (
            (submit_resp.get("_links") or {}).get("upload", {}).get("href")
            or submit_resp.get("uploadUrl", "")
        )
        if not upload_url:
            _mark_error(internal_job_id, "Kein upload-Link von Printix")
            return internal_job_id

        upload_ok = await asyncio.to_thread(
            client.upload_print_data,
            upload_url=upload_url,
            file_bytes=file_bytes,
            content_type=doc_format or "application/pdf",
        )
        if not upload_ok:
            _mark_error(internal_job_id, "Datei-Upload fehlgeschlagen")
            return internal_job_id

        await asyncio.to_thread(client.complete_upload, px_job_id)

        # ─── DB-Row auf "sent" markieren ──────────────────────────
        with _conn() as conn:
            conn.execute(
                """UPDATE cloudprint_jobs
                      SET status = 'sent',
                          printix_job_id = ?,
                          forwarded_at = ?
                    WHERE job_id = ?""",
                (px_job_id, time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                 internal_job_id),
            )
        logger.info(
            "AirPrint submit OK — internal=%s printix=%s user=%s queue=%s",
            internal_job_id, px_job_id, user_email, queue_display_name,
        )

    except Exception as e:
        logger.error("AirPrint submit Exception: %s", e)
        _mark_error(internal_job_id, str(e)[:400])

    return internal_job_id


def _mark_error(job_id: str, msg: str) -> None:
    from db import _conn
    with _conn() as conn:
        conn.execute(
            """UPDATE cloudprint_jobs
                  SET status = 'error', error_message = ?
                WHERE job_id = ?""",
            (msg, job_id),
        )


async def _run_preview_and_ai(internal_job_id: str,
                                file_bytes: bytes,
                                job_name: str,
                                full_tenant: dict,
                                user_id: str) -> None:
    """Läuft im Hintergrund: Preview-PNG rendern + KI-Analyse.

    Fehler hier machen den Print-Job selbst nicht kaputt — sie
    landen nur als Log-Warnung und (bei KI) als ai_analysis_skipped
    Audit-Event. Der Job druckt trotzdem.
    """
    from db import _conn

    # ─── Preview-PNG ─────────────────────────────────────────────
    try:
        from upload_converter import (
            render_image_preview_png as _render_img_prev,
            render_preview_png as _render_prev,
        )
        _prev_png = _render_img_prev(file_bytes) or _render_prev(file_bytes)
        if _prev_png:
            with _conn() as _c:
                _c.execute(
                    "UPDATE cloudprint_jobs SET preview_png=? WHERE job_id=?",
                    (_prev_png, internal_job_id),
                )
            logger.info("AirPrint preview OK — job=%s size=%d",
                        internal_job_id, len(_prev_png))
    except Exception as _pe:
        logger.warning("AirPrint preview failed job=%s: %s",
                       internal_job_id, _pe)

    # ─── KI-Analyse (nur wenn Tenant KI eingeschaltet hat) ───────
    try:
        if (full_tenant.get("ai_enabled") or "0") != "1":
            return  # KI aus — kein Skipped-Event, wie beim regulären Upload
        provider = (full_tenant.get("ai_provider") or "").strip()
        if not provider:
            try:
                import json as _js
                from db import audit as _audit_skip
                _audit_skip(user_id, "ai_analysis_skipped",
                            details=_js.dumps({
                                "reason":   "no_provider_configured",
                                "filename": job_name,
                                "source":   "airprint",
                            }, ensure_ascii=False),
                            object_type="print_job",
                            object_id=internal_job_id)
            except Exception:
                pass
            return

        import json as _json_ai_cfg
        try:
            _custom_prompts = _json_ai_cfg.loads(
                full_tenant.get("ai_custom_prompts") or "[]"
            )
        except Exception:
            _custom_prompts = []
        ai_cfg = {
            "tenant_id":      full_tenant.get("id", "") or "",
            "provider":       provider,
            "gemini_key":     (full_tenant.get("ai_gemini_api_key") or "").strip(),
            "gemini_model":   (full_tenant.get("ai_gemini_model") or "").strip(),
            "ollama_url":     (full_tenant.get("ai_ollama_url") or "").strip(),
            "ollama_model":   (full_tenant.get("ai_ollama_model") or "").strip(),
            "openai_key":     (full_tenant.get("ai_openai_api_key") or "").strip(),
            "openai_model":   (full_tenant.get("ai_openai_model") or "").strip(),
            "fields":         (full_tenant.get("ai_fields") or "").strip(),
            "custom_prompts": _custom_prompts,
        }
        from cloudprint.ai_analysis import analyse_job
        await asyncio.to_thread(
            analyse_job,
            job_id=internal_job_id,
            file_bytes=file_bytes,
            filename=job_name,
            ai_cfg=ai_cfg,
            user_id=user_id,
            lang="en",
        )
        logger.info("AirPrint AI-Analyse fertig — job=%s", internal_job_id)
    except Exception as _ae:
        logger.warning("AirPrint AI-Analyse Exception job=%s: %s",
                       internal_job_id, _ae)
