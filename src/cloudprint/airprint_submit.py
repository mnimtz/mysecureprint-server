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
    from db import (_conn, _resolve_tenant_owner_for,
                    _find_tenant_owner_user_id, get_tenant_by_user_id,
                    get_tenant_full_by_user_id)
    from printix_client import PrintixClient

    if not internal_job_id:
        internal_job_id = _uuid.uuid4().hex[:8]

    # ─── Owner-Email aufloesen ────────────────────────────────────────
    # Der /desktop/upload-Trick: die Session-Email ist oft anders
    # kapitalisiert (Marcus@…) als die Email die Printix beim User-Sync
    # gespeichert hat (marcus@…). Wir haben lokal cached_printix_users
    # das beim Sync mit den echten Printix-Emails befuellt wird — nutze
    # die als Ground-Truth wenn die printix_user_id des Users bekannt ist.
    owner_email = (user_email or "").strip().lower()
    try:
        with _conn() as conn:
            _urow = conn.execute(
                "SELECT printix_user_id FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        _px_id = (_urow["printix_user_id"] or "").strip() if _urow else ""
        if _px_id and not _px_id.startswith("mgr:"):
            with _conn() as conn:
                _cprow = conn.execute(
                    "SELECT email FROM cached_printix_users "
                    "WHERE printix_user_id = ?",
                    (_px_id,),
                ).fetchone()
            if _cprow and _cprow["email"]:
                owner_email = (_cprow["email"] or "").strip().lower()
                logger.info(
                    "AirPrint submit: owner via cached_printix_users "
                    "px_id=%s email=%s (Session-Email war: %s)",
                    _px_id, owner_email, user_email,
                )
    except Exception as _e:
        logger.warning("AirPrint submit: owner-email lookup fail: %s", _e)

    # ─── Tenant + Printix-Credentials für den User ────────────────────
    # Kaskade: (1) via _resolve_tenant_owner_for direkt auf die Owner-
    # user_id, (2) als Fallback den globalen Tenant-Owner. In beiden
    # Faellen versuchen wir zuerst mit dem gefundenen parent_id.
    candidates = []
    resolved = _resolve_tenant_owner_for(user_id)
    if resolved:
        candidates.append(resolved)
    if user_id and user_id not in candidates:
        candidates.append(user_id)
    global_owner = _find_tenant_owner_user_id()
    if global_owner and global_owner not in candidates:
        candidates.append(global_owner)

    tenant = None
    parent_id = None
    for cand in candidates:
        t = get_tenant_by_user_id(cand)
        if t:
            tenant = t
            parent_id = cand
            break
    if not tenant:
        logger.error(
            "AirPrint submit: kein Tenant für user=%s — Job verworfen "
            "(candidates=%s)", user_id, candidates,
        )
        return internal_job_id

    full_tenant = get_tenant_full_by_user_id(parent_id)
    if not full_tenant:
        logger.error("AirPrint submit: get_tenant_full liefert None "
                     "für parent=%s", parent_id)
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
    # Bewaehrter Weg (identisch zum /desktop/upload-Pfad in
    # desktop_routes.py):
    #   1) submit_print_job mit user=<email> als Legacy-Query-Param.
    #      Printix akzeptiert das immer, weist den Job aber initial
    #      dem OAuth-App-Owner (System-Manager) zu, nicht dem echten User.
    #   2) Nach complete_upload: change_job_owner(px_job_id, email)
    #      setzt den echten Besitzer.
    # user_mapping wollten wir nicht — funktioniert nur wenn Printix
    # den User genau so gemappt hat, was in vielen Tenants fehlschlaegt
    # (Log: "Cannot find appropriate user ... for tenant").
    try:
        from printix_client import PrintixAPIError as _PxErr
        def _do_submit():
            return client.submit_print_job(
                printer_id=printer_id,
                queue_id=queue_id,
                title=job_name[:200],
                user=owner_email or "",
                pdl="PDF",
                release_immediately=False,
            )
        try:
            submit_resp = await asyncio.to_thread(_do_submit)
        except _PxErr as _pe:
            # Retry ohne user-Param (kommt manchmal bei sensitivem Tenant)
            if _pe.status_code in (400, 422):
                logger.warning(
                    "AirPrint submit: %s bei user=%s (id=%s), retry ohne user",
                    _pe.status_code, user_email,
                    getattr(_pe, "error_id", "-"),
                )
                submit_resp = await asyncio.to_thread(
                    client.submit_print_job,
                    printer_id=printer_id,
                    queue_id=queue_id,
                    title=job_name[:200],
                    pdl="PDF",
                    release_immediately=False,
                )
            else:
                raise

        result_job = (submit_resp.get("job", submit_resp)
                      if isinstance(submit_resp, dict) else {})
        px_job_id = (result_job.get("id", "")
                     if isinstance(result_job, dict) else "")

        upload_url = ""
        upload_headers = {}
        if isinstance(submit_resp, dict):
            upload_url = submit_resp.get("uploadUrl", "") or ""
            links = submit_resp.get("uploadLinks") or []
            if not upload_url and links and isinstance(links[0], dict):
                upload_url = links[0].get("url", "") or ""
                upload_headers = links[0].get("headers") or {}

        if not px_job_id or not upload_url:
            logger.error(
                "AirPrint submit: Printix lieferte keine job_id oder upload-URL: "
                "keys=%s",
                list(submit_resp.keys()) if isinstance(submit_resp, dict) else "?",
            )
            _mark_error(internal_job_id, "Printix submit ohne job_id/upload-URL")
            return internal_job_id

        # ─── Datei an Printix uploaden ────────────────────────────
        await asyncio.to_thread(
            client.upload_file_to_url,
            upload_url,
            file_bytes,
            doc_format or "application/pdf",
            upload_headers,
        )

        await asyncio.to_thread(client.complete_upload, px_job_id)

        # ─── Ownership auf echten User uebertragen ────────────────
        # Der App-Owner-Trick: submit weist den Job initial dem
        # System-Manager zu, jetzt via change_job_owner an den echten
        # User uebertragen (SecurePrint findet Job dann bei ihm).
        # Kandidaten-Kaskade fuer den Email-Match — Printix's changeOwner
        # akzeptiert NUR eine Email die exakt einem User im Tenant matcht.
        # Deshalb probieren wir mehrere Varianten:
        #   1) Die lokal gespeicherte Email (Session-User)
        #   2) Wenn users.printix_user_id gesetzt: Printix-User via
        #      get_user() abfragen und deren echte Email nehmen
        #   3) Lowercase-Variante (Printix mixt Case oft)
        email_candidates = []
        # Bevorzugt: die owner_email aus cached_printix_users (die "wahre"
        # Printix-Email die Printix zuverlaessig findet).
        if owner_email and "@" in owner_email:
            email_candidates.append(owner_email)
        # Fallback: Session-Email
        if user_email and "@" in user_email and user_email not in email_candidates:
            email_candidates.append(user_email)
        # Aus Printix via user_id nachschlagen — das gibt die "kanonische" Email
        try:
            with _conn() as conn:
                _row = conn.execute(
                    "SELECT printix_user_id, email FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
            _px_uid = (_row["printix_user_id"] or "").strip() if _row else ""
            if _px_uid and not _px_uid.startswith("mgr:"):
                try:
                    _px_user = await asyncio.to_thread(client.get_user, _px_uid)
                    if isinstance(_px_user, dict):
                        _px_email = (_px_user.get("email")
                                     or _px_user.get("mailAddress")
                                     or "").strip()
                        if _px_email and _px_email not in email_candidates:
                            email_candidates.append(_px_email)
                except Exception as _lookup_e:
                    logger.info(
                        "AirPrint submit: Printix-User %s lookup fail: %s",
                        _px_uid, _lookup_e,
                    )
        except Exception:
            pass
        # Lowercase-Variante
        for c in list(email_candidates):
            lc = c.lower()
            if lc not in email_candidates:
                email_candidates.append(lc)

        owner_set = False
        for _try_email in email_candidates:
            try:
                await asyncio.to_thread(
                    client.change_job_owner, px_job_id, _try_email,
                )
                logger.info(
                    "AirPrint submit changeOwner OK — printix_job=%s owner=%s",
                    px_job_id, _try_email,
                )
                owner_set = True
                break
            except Exception as _co_e:
                logger.info(
                    "AirPrint submit changeOwner attempt fail job=%s owner=%s: %s",
                    px_job_id, _try_email, _co_e,
                )
        if not owner_set:
            logger.warning(
                "AirPrint submit changeOwner FAIL komplett job=%s — "
                "kein Kandidat matched. Kandidaten: %s. Job bleibt beim "
                "System-Manager — Nutzer muss Job dort suchen.",
                px_job_id, email_candidates,
            )

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
