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
