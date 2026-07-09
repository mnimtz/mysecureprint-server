"""
IPP/IPPS-Server für MySecurePrint AirPrint-Profile (v0.8.0)
============================================================
FastAPI-Handler der IPP-Print-Jobs von iOS-Geräten empfängt.

Architektur:
  iOS Print → POST /airprint/{profile_token}   (Content-Type: application/ipp)
    → Token-Lookup → User + Queue + Berechtigung
    → PDF extrahieren + speichern
    → an Printix Cloud-Print-API weiterleiten (via printix_client)

User-Identifikation:
  AUSSCHLIEßLICH über den Profile-Token in der URL. IPP-Attribute wie
  requesting-user-name werden NICHT für Auth verwendet (iOS liefert
  dort nur den Device-Namen, kein verlässliches Merkmal).

  Der Token wird beim Profil-Erstellen fest an user_id + queue_id
  gebunden — der Server weiß also 100% wer druckt, egal was iOS
  im IPP-Stream schickt.

Portiert aus printix-mcp-linux v6.7.x, adaptiert für Token-Auth statt
Tenant/LPR-Flow.
"""

from __future__ import annotations

import logging
import os
import time
import uuid as _uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response

from cloudprint import ipp_parser as ipp

logger = logging.getLogger("printix.airprint.ipp")

IPP_SPOOL_DIR = os.environ.get("AIRPRINT_SPOOL_DIR", "/tmp/airprint-spool")
MAX_JOB_SIZE = int(os.environ.get("AIRPRINT_MAX_JOB_SIZE", 50 * 1024 * 1024))


# ─── Registrierung in FastAPI ────────────────────────────────────────────────

def register_airprint_routes(app: FastAPI) -> None:
    """Mountet /airprint/{profile_token} als IPP-Empfangs-Endpoint."""

    @app.post("/airprint/{profile_token}")
    async def airprint_receive(profile_token: str, request: Request):
        # Feature-Flag prüfen
        from db import get_setting as _gs
        if _gs("ios_mobile_airprint_enabled", "0") != "1":
            return Response(
                content=b"AirPrint is not enabled on this server.",
                media_type="text/plain",
                status_code=404,
            )
        body = await request.body()
        return await _handle_ipp_request(profile_token, body, request)

    @app.get("/airprint/{profile_token}")
    async def airprint_info(profile_token: str, request: Request):
        """GET-Handler für Health-Checks / Browser-Zugriff."""
        peer = request.client.host if request.client else "?"
        ua = request.headers.get("user-agent", "-")
        logger.info(
            "AirPrint: GET-Probe von %s → token=%s UA=%s",
            peer, profile_token[:8] + "…", ua,
        )
        return Response(
            content=(
                b"This is an IPP endpoint for MySecurePrint AirPrint. "
                b"POST with Content-Type: application/ipp to send print jobs."
            ),
            media_type="text/plain",
            status_code=200,
        )


# ─── Core Request-Handling ───────────────────────────────────────────────────

async def _handle_ipp_request(profile_token: str, body: bytes,
                                request: Request) -> Response:
    """Parst IPP-Request und dispatched nach Operation."""
    peer = request.client.host if request.client else "?"
    ua = request.headers.get("user-agent", "-")

    try:
        req = ipp.parse_request(body)
    except Exception as e:
        logger.warning("AirPrint: IPP-Parse-Fehler von %s: %s", peer, e)
        return _ipp_response(
            ipp.build_response(request_id=0,
                               status_code=ipp.STATUS_CLIENT_ERROR_BAD),
        )

    op_name = _ipp_op_name(req.operation_id)
    logger.info(
        "AirPrint: IPP-Request %s (op=0x%04x) von %s UA=%s token=%s…",
        op_name, req.operation_id, peer, ua, profile_token[:8],
    )

    # Print-Job = 0x0002, Get-Printer-Attributes = 0x000B, Validate-Job = 0x0004
    if req.operation_id == 0x0002:  # Print-Job
        return await _handle_print_job(profile_token, req, request,
                                        peer=peer, ua=ua)
    elif req.operation_id == 0x000B:  # Get-Printer-Attributes
        return _handle_get_printer_attributes(profile_token, req, request)
    elif req.operation_id == 0x0004:  # Validate-Job
        return _ipp_response(
            ipp.build_response(req.request_id, status_code=ipp.STATUS_OK),
        )
    elif req.operation_id == 0x000A:  # Get-Jobs
        # iOS pollt das im Sekundentakt zum Status-Check. Wir tracken die
        # Jobs nach dem Print-Job-Ack nicht mehr per IPP-Job-ID (Printix
        # ist die Autoritaet); leere Liste zurueck heisst fuer iOS
        # "keine offenen Jobs" -> haken dran, fertig.
        return _ipp_response(
            ipp.build_response(req.request_id, status_code=ipp.STATUS_OK),
        )
    elif req.operation_id == 0x0009:  # Get-Job-Attributes
        # Falls iOS gezielt nach einem bestimmten Job fragt: Dummy
        # "completed" damit iOS die Status-Anzeige beendet.
        printer_uri = _derive_printer_uri(request, profile_token)
        job_id = int(req.attr("job-id", 1) or 1)
        return _ipp_response(
            ipp.build_get_job_attributes_response(
                request_id=req.request_id, job_id=job_id,
                printer_uri=printer_uri,
                job_state=ipp.JOB_STATE_COMPLETED,
            ),
        )
    elif req.operation_id == 0x0008:  # Cancel-Job
        # Jobs sind im Moment des Ack schon "abgeschickt" — Cancel ist
        # ein No-op auf unserer Seite, aber wir muessen OK antworten
        # sonst haengt iOS im "Abbrechen"-Zustand.
        return _ipp_response(
            ipp.build_response(req.request_id, status_code=ipp.STATUS_OK),
        )

    logger.warning("AirPrint: nicht unterstützte IPP-Operation 0x%04x", req.operation_id)
    return _ipp_response(
        ipp.build_response(req.request_id,
                           status_code=ipp.STATUS_SERVER_ERROR_OPERATION_NOT_SUPPORTED),
    )


def _handle_get_printer_attributes(profile_token: str, req: ipp.IppRequest,
                                    request: Request) -> Response:
    """Antwortet auf Get-Printer-Attributes — nötig für iOS-Handshake."""
    # Token existiert? Sonst 404-ähnlicher IPP-Fehler
    profile = _lookup_profile(profile_token)
    if not profile:
        return _ipp_response(
            ipp.build_response(req.request_id,
                               status_code=ipp.STATUS_CLIENT_ERROR_NOT_FOUND),
        )
    printer_uri = _derive_printer_uri(request, profile_token)
    # Präfix aus dem Admin-Setting (Konfiguration → iOS Mobile → Organisation)
    from db import get_setting as _gs
    _org = _gs("airprint_organization", "") or "MySecurePrint"
    _queue = profile.get("queue_display_name") or "SecurePrint"
    printer_name = f"{_org} — {_queue}"
    body = ipp.build_get_printer_attributes_response(
        request_id=req.request_id,
        printer_uri=printer_uri,
        printer_name=printer_name,
    )
    return _ipp_response(body)


async def _handle_print_job(profile_token: str, req: ipp.IppRequest,
                             request: Request,
                             peer: str = "?", ua: str = "-") -> Response:
    """Empfängt einen Print-Job und leitet ihn an Printix weiter."""
    import asyncio

    # ─── 1. Token → User + Queue ──────────────────────────────────────
    profile = _lookup_profile(profile_token)
    if not profile:
        logger.warning(
            "AirPrint: PRINT-JOB mit ungültigem/widerrufenem Token von %s "
            "(token=%s…)",
            peer, profile_token[:8],
        )
        return _ipp_response(
            ipp.build_response(req.request_id,
                               status_code=ipp.STATUS_CLIENT_ERROR_NOT_AUTHORIZED),
        )

    # ─── 2. IPP-Attribute (nur als Metadaten, nicht für Auth) ────────
    meta = ipp.extract_job_metadata(req)
    job_name = meta.get("job_name") or meta.get("document_name") or "Untitled"
    doc_format = meta.get("document_format") or "application/pdf"
    origin_host = meta.get("job_originating_host_name", "") or "-"

    # ─── 3. PDF-Payload extrahieren ──────────────────────────────────
    data = req.data or b""
    if len(data) == 0:
        logger.warning(
            "AirPrint: leerer Print-Job — token=%s… peer=%s",
            profile_token[:8], peer,
        )
        return _ipp_response(
            ipp.build_response(req.request_id,
                               status_code=ipp.STATUS_CLIENT_ERROR_BAD),
        )
    if len(data) > MAX_JOB_SIZE:
        logger.warning(
            "AirPrint: Print-Job zu groß (%d > %d Bytes) — token=%s…",
            len(data), MAX_JOB_SIZE, profile_token[:8],
        )
        return _ipp_response(
            ipp.build_response(req.request_id,
                               status_code=ipp.STATUS_CLIENT_ERROR_BAD),
        )

    # ─── 4. Auf Disk spoolen (für async Weiterleitung + Debug) ────────
    Path(IPP_SPOOL_DIR).mkdir(parents=True, exist_ok=True)
    internal_job_id = _uuid.uuid4().hex[:8]
    file_path = Path(IPP_SPOOL_DIR) / f"{internal_job_id}.pdf"
    with open(file_path, "wb") as f:
        f.write(data)

    logger.info(
        "AirPrint: PRINT-JOB angenommen — id=%s user=%s queue=%s "
        "job='%s' size=%d bytes format=%s origin=%s peer=%s",
        internal_job_id,
        profile["user_email"],
        profile["queue_display_name"],
        job_name, len(data), doc_format, origin_host, peer,
    )

    # ─── 5. Nutzung tracken (last_used_at, job_count) ────────────────
    _touch_profile_usage(profile["id"])

    # ─── 5b. Audit: jeder Druck via AirPrint muss im Log erscheinen ──
    try:
        import json as _json
        from db import audit as _audit
        _audit(profile["user_id"], "airprint_print_job", details=_json.dumps({
            "internal_job_id":     internal_job_id,
            "profile_id":          profile["id"],
            "queue_id":            profile.get("queue_id"),
            "queue_display_name":  profile.get("queue_display_name"),
            "printer_id":          profile.get("printer_id"),
            "job_name":            job_name,
            "size_bytes":          len(data),
            "doc_format":          doc_format,
            "origin_host":         origin_host,
            "peer":                peer,
            "user_agent":          ua,
        }, ensure_ascii=False))
    except Exception as _e:
        logger.warning("AirPrint audit-log failed for job %s: %s",
                       internal_job_id, _e)

    # ─── 6. Async an Printix weiterleiten ────────────────────────────
    asyncio.create_task(
        _forward_airprint_job(
            profile=profile,
            internal_job_id=internal_job_id,
            file_path=str(file_path),
            data_size=len(data),
            job_name=job_name,
            doc_format=doc_format,
            origin_host=origin_host,
        )
    )

    # ─── 7. IPP-Erfolgs-Response direkt zurück an iOS ─────────────────
    numeric_job_id = int(time.time() * 1000) & 0x7FFFFFFF
    printer_uri = _derive_printer_uri(request, profile_token)
    return _ipp_response(
        ipp.build_print_job_response(
            request_id=req.request_id,
            job_id=numeric_job_id,
            printer_uri=printer_uri,
            job_state=ipp.JOB_STATE_PENDING,
        ),
    )


# ─── DB-Zugriffe ─────────────────────────────────────────────────────────────

def _lookup_profile(profile_token: str) -> dict | None:
    """Token → Profil-Row inkl. User-Email. Gibt None wenn nicht gefunden
    oder widerrufen."""
    try:
        from db import _conn
    except ImportError:
        return None
    with _conn() as conn:
        row = conn.execute(
            """SELECT p.id, p.user_id, p.profile_token, p.printer_id,
                       p.queue_id, p.queue_display_name, p.display_name,
                       p.is_revoked,
                       u.email       AS user_email,
                       u.username    AS user_username
                 FROM cloudprint_airprint_profiles p
            LEFT JOIN users u ON u.id = p.user_id
                WHERE p.profile_token = ?""",
            (profile_token,),
        ).fetchone()
    if not row:
        return None
    if row["is_revoked"]:
        logger.warning(
            "AirPrint: Zugriff auf widerrufenes Profil abgelehnt — token=%s…",
            profile_token[:8],
        )
        return None
    return dict(row)


def _touch_profile_usage(profile_id: str) -> None:
    """Aktualisiert last_used_at + job_count."""
    try:
        from db import _conn
        with _conn() as conn:
            conn.execute(
                """UPDATE cloudprint_airprint_profiles
                      SET last_used_at = CURRENT_TIMESTAMP,
                          job_count = job_count + 1
                    WHERE id = ?""",
                (profile_id,),
            )
    except Exception as e:
        logger.warning("AirPrint: usage-Tracking fehlgeschlagen: %s", e)


# ─── Forwarder ────────────────────────────────────────────────────────────────

async def _forward_airprint_job(profile: dict, internal_job_id: str,
                                  file_path: str, data_size: int,
                                  job_name: str, doc_format: str,
                                  origin_host: str) -> None:
    """Reicht den Job durch die Standard-Upload-Pipeline an Printix weiter.

    Wir nutzen den bestehenden `submit_and_track` Flow aus desktop_routes
    damit AirPrint-Jobs identisch zu App-Uploads in der DB landen und
    im Job-Verlauf auftauchen.
    """
    try:
        # Import lokal weil sonst Zirkel-Import (desktop_routes importiert
        # ipp_server nicht, aber ipp_server importiert desktop_routes-Helper)
        from cloudprint.airprint_submit import submit_airprint_job

        with open(file_path, "rb") as f:
            pdf_bytes = f.read()

        await submit_airprint_job(
            user_id=profile["user_id"],
            user_email=profile["user_email"],
            printer_id=profile["printer_id"],
            queue_id=profile["queue_id"],
            queue_display_name=profile["queue_display_name"],
            file_bytes=pdf_bytes,
            job_name=job_name,
            doc_format=doc_format,
            origin_host=origin_host,
            source="airprint",
            internal_job_id=internal_job_id,
        )

        # Spool-Datei aufräumen — der Job ist bei Printix
        try:
            os.remove(file_path)
        except Exception:
            pass

    except Exception as e:
        logger.error(
            "AirPrint: Weiterleitung fehlgeschlagen — job=%s user=%s: %s",
            internal_job_id, profile.get("user_email", "?"), e,
        )
        # Datei bleibt für Debug im Spool


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _ipp_op_name(op_id: int) -> str:
    return ipp.OPERATION_NAMES.get(op_id, f"0x{op_id:04x}")


def _ipp_response(body: bytes, status: int = 200) -> Response:
    return Response(content=body, media_type="application/ipp",
                    status_code=status)


def _derive_printer_uri(request: Request, profile_token: str) -> str:
    """Baut die printer-uri aus dem eingehenden Request. Nutzt X-Forwarded-*
    wenn ein Reverse Proxy davor läuft."""
    fwd_proto = request.headers.get("x-forwarded-proto", "https")
    host_hdr = request.headers.get("x-forwarded-host") \
        or request.headers.get("host") \
        or "localhost"
    return f"ipp://{host_hdr}/airprint/{profile_token}"
