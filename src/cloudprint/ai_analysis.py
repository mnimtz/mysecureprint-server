"""
KI-Dokumentenanalyse (v0.7.115)
================================
Analysiert Print-Job-Dateien mit Gemini oder Ollama und speichert
strukturierte Erkenntnisse in cloudprint_jobs.

Felder nach Analyse:
  ai_doc_type    — Dokumenttyp (Rechnung, Präsentation, Foto …)
  ai_color_rec   — Druckempfehlung: 'farbe' | 'schwarzweiss'
  ai_sensitivity — Vertraulichkeit: 'öffentlich' | 'intern' | 'vertraulich'
  ai_summary     — 2–3 Sätze Zusammenfassung / Bildbeschreibung
  ai_tags        — Nur bei Fotos: 2–3 Schlagwörter, kommagetrennt
  ai_analyzed_at — ISO-8601 Zeitstempel

Einstiegspunkt: analyse_job(job_id, file_bytes, filename, ai_cfg)
Wird als asyncio.to_thread() nach dem Job-Insert aufgerufen.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
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

_MAX_BYTES_GEMINI  = 15 * 1024 * 1024   # 15 MB Limit vor Base64-Encoding
_MAX_BYTES_OLLAMA  = 2 * 1024 * 1024    # 2 MB Text-Extraktion
_MAX_TEXT_OLLAMA   = 5000               # Zeichen an Ollama-Prompt anhängen


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
    ai_cfg: dict,
) -> None:
    """Synchron — immer in asyncio.to_thread() aufrufen.

    ai_cfg wird direkt aus dem Tenant übergeben (bereits entschlüsselt):
      ai_provider, gemini_key, gemini_model, ollama_url, ollama_model
    """
    try:
        provider     = (ai_cfg.get("provider") or "").strip()
        gemini_key   = (ai_cfg.get("gemini_key") or "").strip()
        gemini_model = (ai_cfg.get("gemini_model") or "").strip()
        ollama_url   = (ai_cfg.get("ollama_url") or "").strip()
        ollama_model = (ai_cfg.get("ollama_model") or "").strip()
        tenant_id    = (ai_cfg.get("tenant_id") or "").strip()

        if not provider:
            return

        mime = _guess_mime(filename)
        is_image = mime.startswith("image/")

        if provider == "gemini":
            if not gemini_key or not gemini_model:
                return
            if len(file_bytes) > _MAX_BYTES_GEMINI:
                logger.info(
                    "ai_analysis: job=%s übersprungen — Datei zu groß (%d MB > %d MB Limit)",
                    job_id, len(file_bytes) // (1024 * 1024), _MAX_BYTES_GEMINI // (1024 * 1024),
                )
                return
            result = _analyse_gemini(file_bytes, mime, is_image, gemini_key, gemini_model)
        elif provider == "ollama":
            if not ollama_url or not ollama_model:
                return
            if is_image:
                logger.debug("ai_analysis: Ollama für Bild nicht unterstützt — überspringe %s", job_id)
                return
            if len(file_bytes) > _MAX_BYTES_OLLAMA:
                logger.info(
                    "ai_analysis: job=%s Datei zu groß für Ollama-Text-Extraktion (%d MB)",
                    job_id, len(file_bytes) // (1024 * 1024),
                )
                return
            result = _analyse_ollama(file_bytes, mime, ollama_url, ollama_model)
        else:
            return

        if not result:
            return

        import sys, os as _os
        src_dir = _os.path.dirname(_os.path.dirname(__file__))
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        from db import _conn

        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        raw_tags = result.get("tags") or []
        if isinstance(raw_tags, list):
            tags_str = ", ".join(str(t).strip() for t in raw_tags[:5] if t)[:200]
        else:
            tags_str = str(raw_tags)[:200]

        with _conn() as conn:
            conn.execute(
                """UPDATE cloudprint_jobs
                   SET ai_doc_type=?, ai_color_rec=?, ai_sensitivity=?,
                       ai_summary=?, ai_tags=?, ai_analyzed_at=?
                   WHERE job_id=?""",
                (
                    result.get("doc_type", "")[:80],
                    result.get("color_rec", "")[:40],
                    result.get("sensitivity", "")[:40],
                    result.get("summary", "")[:1500],
                    tags_str,
                    now,
                    job_id,
                ),
            )
        logger.info(
            "ai_analysis: job=%s provider=%s doc_type=%s color=%s sensitivity=%s tags=%s",
            job_id, provider,
            result.get("doc_type", ""), result.get("color_rec", ""),
            result.get("sensitivity", ""), tags_str,
        )
    except Exception as e:
        logger.warning("ai_analysis: job=%s error=%s", job_id, e)


# ── Gemini ────────────────────────────────────────────────────────────────────

_GEMINI_PROMPT_PDF = """Analysiere dieses Dokument und antworte NUR mit einem JSON-Objekt (kein Markdown, kein Text drumherum):
{
  "doc_type": "<Dokumenttyp, z.B. Rechnung, Präsentation, Vertrag, Bericht, Formular, Brief, Sonstiges>",
  "color_rec": "<'farbe' oder 'schwarzweiss'>",
  "sensitivity": "<'öffentlich', 'intern' oder 'vertraulich'>",
  "tags": [],
  "summary": "<2–3 Sätze Zusammenfassung des Inhalts>"
}"""

_GEMINI_PROMPT_IMAGE = """Beschreibe dieses Bild und antworte NUR mit einem JSON-Objekt (kein Markdown, kein Text drumherum):
{
  "doc_type": "Foto",
  "color_rec": "farbe",
  "sensitivity": "intern",
  "tags": ["<Schlagwort 1>", "<Schlagwort 2>", "<Schlagwort 3>"],
  "summary": "<2–3 Sätze ausführliche Bildbeschreibung: Was ist zu sehen, Stimmung, besondere Details>"
}
Wähle 2–3 prägnante Schlagwörter die das Bild kategorisieren (z.B. Natur, Architektur, Personen, Essen, Tier, Landschaft, Innen, Außen …)."""


def _analyse_gemini(
    file_bytes: bytes,
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
            "maxOutputTokens": 1024,
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
        return _parse_json_response(text)
    except Exception as e:
        logger.warning("gemini response parse error: %s — raw: %s", e, str(data)[:300])
        return None


# ── Ollama ────────────────────────────────────────────────────────────────────

_OLLAMA_PROMPT = """Analysiere diesen Dokumenttext und antworte NUR mit einem JSON-Objekt (kein Markdown, kein Text drumherum):
{
  "doc_type": "<Dokumenttyp, z.B. Rechnung, Präsentation, Vertrag, Bericht, Formular, Brief, Sonstiges>",
  "color_rec": "<'farbe' oder 'schwarzweiss'>",
  "sensitivity": "<'öffentlich', 'intern' oder 'vertraulich'>",
  "tags": [],
  "summary": "<2–3 Sätze Zusammenfassung des Inhalts>"
}

Dokumenttext:
"""


def _analyse_ollama(
    file_bytes: bytes,
    mime: str,
    base_url: str,
    model: str,
) -> dict | None:
    text = _extract_text(file_bytes, mime)
    if not text:
        return None
    prompt = _OLLAMA_PROMPT + text[:_MAX_TEXT_OLLAMA]
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
        return _parse_json_response((data.get("response") or "").strip())
    except Exception as e:
        logger.warning("ollama parse error: %s", e)
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_json_response(text: str) -> dict:
    """Parst eine LLM-Antwort die entweder reines JSON oder Markdown-umhülltes JSON enthält."""
    text = text.strip()
    # Markdown-Fence entfernen: ```json\n{...}\n``` oder ```\n{...}\n```
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        return json.loads(fence_match.group(1))
    # Reines JSON
    if text.startswith("{"):
        return json.loads(text)
    # JSON irgendwo im Text suchen
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        return json.loads(brace_match.group(0))
    raise ValueError(f"Kein JSON-Objekt in Antwort gefunden: {text[:100]!r}")


def _guess_mime(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename or "")
    if guessed:
        return guessed
    ext = (filename or "").lower().rsplit(".", 1)[-1]
    return {
        "pdf":  "application/pdf",
        "png":  "image/png",
        "jpg":  "image/jpeg",
        "jpeg": "image/jpeg",
        "heic": "image/heic",
        "heif": "image/heif",
        "gif":  "image/gif",
        "webp": "image/webp",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(ext, "application/octet-stream")


def _extract_text(file_bytes: bytes, mime: str) -> str:
    """Text-Extraktion für Ollama (PDFs ohne externe Bibliotheken)."""
    if mime == "application/pdf":
        try:
            text_parts: list[str] = []
            pos = 0
            raw = file_bytes
            limit = len(raw)
            while pos < limit:
                bt = raw.find(b"BT", pos)
                if bt == -1:
                    break
                et = raw.find(b"ET", bt + 2)
                if et == -1:
                    break
                chunk = raw[bt:et].decode("latin-1", errors="replace")
                for part in chunk.split("(")[1:]:
                    close = part.find(")")
                    if close > 0:
                        inner = part[:close]
                        # Escape-Sequenzen \n \r \t → Leerzeichen
                        inner = re.sub(r"\\[nrt]", " ", inner)
                        text_parts.append(inner)
                pos = et + 2
                if len(" ".join(text_parts)) >= _MAX_TEXT_OLLAMA:
                    break
            return " ".join(text_parts)[:_MAX_TEXT_OLLAMA]
        except Exception:
            return ""
    if mime.startswith("text/"):
        return file_bytes.decode("utf-8", errors="replace")[:_MAX_TEXT_OLLAMA]
    return ""
