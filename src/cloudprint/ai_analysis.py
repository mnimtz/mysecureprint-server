"""
KI-Dokumentenanalyse (v0.7.114)
================================
Analysiert Print-Job-Dateien mit Gemini oder Ollama und speichert
strukturierte Erkenntnisse (Dokumenttyp, Farbbedarf, Vertraulichkeit,
Zusammenfassung / Bildbeschreibung) in cloudprint_jobs.

Einstiegspunkt: analyse_job(job_id, file_bytes, filename, tenant_id)
Wird als asyncio.create_task() nach dem Job-Insert aufgerufen.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import urllib.request
import urllib.error

logger = logging.getLogger("printix.ai_analysis")

_GEMINI_GENERATE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    "?key={api_key}"
)
_GEMINI_MODELS_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
)


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_gemini_models(api_key: str) -> list[str]:
    """Gibt alle Gemini-Modelle zurück, die generateContent unterstützen."""
    url = _GEMINI_MODELS_URL.format(api_key=api_key)
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        models = []
        for m in data.get("models", []):
            methods = m.get("supportedGenerationMethods", [])
            if "generateContent" in methods:
                name = m.get("name", "").replace("models/", "")
                if name:
                    models.append(name)
        return sorted(models)
    except Exception as e:
        logger.warning("fetch_gemini_models: %s", e)
        return []


def analyse_job(
    job_id: str,
    file_bytes: bytes,
    filename: str,
    tenant_id: str,
) -> None:
    """Synchron — immer in asyncio.to_thread() aufrufen."""
    try:
        import sys, os as _os
        src_dir = _os.path.dirname(_os.path.dirname(__file__))
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        from db import _conn, get_tenant_full_by_user_id, _dec, _resolve_tenant_owner_for

        with _conn() as conn:
            row = conn.execute(
                "SELECT user_id FROM tenants WHERE id=?", (tenant_id,)
            ).fetchone()
            if not row:
                return
            tenant = get_tenant_full_by_user_id(row["user_id"])
        if not tenant:
            return

        provider   = (tenant.get("ai_provider") or "").strip()
        if not provider:
            return

        gemini_key   = _dec(tenant.get("ai_gemini_api_key") or "")
        gemini_model = (tenant.get("ai_gemini_model") or "").strip()
        ollama_url   = (tenant.get("ai_ollama_url") or "").strip()
        ollama_model = (tenant.get("ai_ollama_model") or "").strip()

        mime = _guess_mime(filename)
        is_image = mime.startswith("image/")

        if provider == "gemini":
            if not gemini_key or not gemini_model:
                return
            result = _analyse_gemini(file_bytes, filename, mime, is_image,
                                     gemini_key, gemini_model)
        elif provider == "ollama":
            if not ollama_url or not ollama_model:
                return
            if is_image:
                logger.debug("ai_analysis: Ollama für Bild nicht unterstützt — überspringe %s", job_id)
                return
            result = _analyse_ollama(file_bytes, filename, mime, ollama_url, ollama_model)
        else:
            return

        if result:
            import datetime
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            with _conn() as conn:
                conn.execute(
                    """UPDATE cloudprint_jobs
                       SET ai_doc_type=?, ai_color_rec=?, ai_sensitivity=?,
                           ai_summary=?, ai_analyzed_at=?
                       WHERE job_id=?""",
                    (
                        result.get("doc_type", "")[:80],
                        result.get("color_rec", "")[:40],
                        result.get("sensitivity", "")[:40],
                        result.get("summary", "")[:1000],
                        now,
                        job_id,
                    ),
                )
            logger.info(
                "ai_analysis: job=%s provider=%s doc_type=%s color=%s sensitivity=%s",
                job_id, provider,
                result.get("doc_type", ""), result.get("color_rec", ""),
                result.get("sensitivity", ""),
            )
    except Exception as e:
        logger.warning("ai_analysis: job=%s error=%s", job_id, e)


# ── Gemini ────────────────────────────────────────────────────────────────────

_GEMINI_PROMPT_PDF = """Analysiere dieses Dokument und antworte NUR mit einem JSON-Objekt (kein Markdown, kein Text drumherum):
{
  "doc_type": "<Dokumenttyp, z.B. Rechnung, Präsentation, Vertrag, Bericht, Formular, Brief, Sonstiges>",
  "color_rec": "<Empfehlung: 'farbe' oder 'schwarzweiss'>",
  "sensitivity": "<Vertraulichkeit: 'öffentlich', 'intern' oder 'vertraulich'>",
  "summary": "<1–2 Sätze Zusammenfassung des Inhalts>"
}"""

_GEMINI_PROMPT_IMAGE = """Beschreibe dieses Bild kurz und antworte NUR mit einem JSON-Objekt (kein Markdown, kein Text drumherum):
{
  "doc_type": "Foto",
  "color_rec": "farbe",
  "sensitivity": "intern",
  "summary": "<1–2 Sätze Bildbeschreibung>"
}"""


def _analyse_gemini(
    file_bytes: bytes,
    filename: str,
    mime: str,
    is_image: bool,
    api_key: str,
    model: str,
) -> dict | None:
    import base64
    prompt = _GEMINI_PROMPT_IMAGE if is_image else _GEMINI_PROMPT_PDF
    encoded = base64.b64encode(file_bytes).decode()
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime, "data": encoded}},
            ]
        }],
        "generationConfig": {
            "maxOutputTokens": 512,
            "temperature": 0.1,
        },
    }
    url = _GEMINI_GENERATE_URL.format(model=model, api_key=api_key)
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors="replace")[:300]
        logger.warning("gemini HTTP %s: %s", e.code, err_body)
        return None
    except Exception as e:
        logger.warning("gemini request failed: %s", e)
        return None

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        if text.startswith("```"):
            text = text.split("```")[1].lstrip("json").strip()
        return json.loads(text)
    except Exception as e:
        logger.warning("gemini response parse error: %s — raw: %s", e, str(data)[:300])
        return None


# ── Ollama ────────────────────────────────────────────────────────────────────

_OLLAMA_PROMPT = """Analysiere diesen Dokumenttext und antworte NUR mit einem JSON-Objekt (kein Markdown, kein Text drumherum):
{
  "doc_type": "<Dokumenttyp, z.B. Rechnung, Präsentation, Vertrag, Bericht, Formular, Brief, Sonstiges>",
  "color_rec": "<Empfehlung: 'farbe' oder 'schwarzweiss'>",
  "sensitivity": "<Vertraulichkeit: 'öffentlich', 'intern' oder 'vertraulich'>",
  "summary": "<1–2 Sätze Zusammenfassung des Inhalts>"
}

Dokumenttext:
"""


def _analyse_ollama(
    file_bytes: bytes,
    filename: str,
    mime: str,
    base_url: str,
    model: str,
) -> dict | None:
    text = _extract_text(file_bytes, mime)
    if not text:
        logger.debug("ollama: kein Text extrahierbar aus %s", filename)
        return None
    prompt = _OLLAMA_PROMPT + text[:4000]
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1},
    }
    url = base_url.rstrip("/") + "/api/generate"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        logger.warning("ollama request failed: %s", e)
        return None
    try:
        text_out = (data.get("response") or "").strip()
        if text_out.startswith("```"):
            text_out = text_out.split("```")[1].lstrip("json").strip()
        return json.loads(text_out)
    except Exception as e:
        logger.warning("ollama parse error: %s", e)
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _guess_mime(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename or "")
    if guessed:
        return guessed
    ext = (filename or "").lower().rsplit(".", 1)[-1]
    return {
        "pdf": "application/pdf",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "heic": "image/heic",
        "heif": "image/heif",
        "gif": "image/gif",
        "webp": "image/webp",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(ext, "application/octet-stream")


def _extract_text(file_bytes: bytes, mime: str) -> str:
    """Einfache Text-Extraktion für Ollama (kein Gemini-Multimodal nötig)."""
    if mime == "application/pdf":
        try:
            import io
            text_parts = []
            i = 0
            raw = file_bytes
            while i < len(raw) - 2:
                if raw[i:i+3] == b"BT " or raw[i:i+3] == b"BT\n":
                    end = raw.find(b" ET", i)
                    if end == -1:
                        break
                    chunk = raw[i:end].decode("latin-1", errors="replace")
                    for part in chunk.split("(")[1:]:
                        close = part.find(")")
                        if close > 0:
                            text_parts.append(part[:close])
                    i = end + 3
                else:
                    i += 1
            return " ".join(text_parts)[:6000]
        except Exception:
            return ""
    if mime.startswith("text/"):
        return file_bytes.decode("utf-8", errors="replace")[:6000]
    return ""
