"""
KI-Dokumentenanalyse (v0.7.117)
================================
Analysiert Print-Job-Dateien mit Gemini oder Ollama und speichert
strukturierte Erkenntnisse in cloudprint_jobs.

Standardfelder (konfigurierbar):
  ai_doc_type    — Dokumenttyp (Rechnung, Präsentation, Foto …)
  ai_color_rec   — Druckempfehlung: 'farbe' | 'schwarzweiss'
  ai_sensitivity — Vertraulichkeit: 'öffentlich' | 'intern' | 'vertraulich'
  ai_summary     — 2–3 Sätze Zusammenfassung / Bildbeschreibung
  ai_tags        — Nur bei Fotos: 2–3 Schlagwörter, kommagetrennt
  ai_analyzed_at — ISO-8601 Zeitstempel

Zusatzfelder (Admin-konfiguriert):
  ai_extra       — JSON-Dict mit custom-Feldern z.B. {"rechnungsnummer": "12345"}

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

_MAX_BYTES_GEMINI  = 15 * 1024 * 1024
_MAX_BYTES_OLLAMA  = 2 * 1024 * 1024
_MAX_TEXT_OLLAMA   = 5000

_ALL_STANDARD_FIELDS = {"doc_type", "color_rec", "sensitivity", "summary", "tags"}

_GEMINI_ALLOWED_MIMES = {
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/heic",
    "application/pdf", "text/plain",
    # Microsoft Office
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
}


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
    user_id: str = "",
) -> None:
    """Synchron — immer in asyncio.to_thread() aufrufen.

    ai_cfg wird direkt aus dem Tenant übergeben (bereits entschlüsselt):
      provider, gemini_key, gemini_model, ollama_url, ollama_model,
      fields (kommagetrennt, leer = alle), custom_prompts (list[dict])
    """
    import sys, os as _os
    src_dir = _os.path.dirname(_os.path.dirname(__file__))
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    def _audit_ai(action: str, details: dict) -> None:
        try:
            from db import audit as _db_audit
            import json as _j
            _db_audit(
                user_id or None,
                action,
                details=_j.dumps(details, ensure_ascii=False)[:2000],
                object_type="print_job",
                object_id=job_id,
                tenant_id=ai_cfg.get("tenant_id", ""),
            )
        except Exception as _ae:
            logger.debug("audit(%s) failed: %s", action, _ae)

    try:
        provider     = (ai_cfg.get("provider") or "").strip()
        gemini_key   = (ai_cfg.get("gemini_key") or "").strip()
        gemini_model = (ai_cfg.get("gemini_model") or "").strip()
        ollama_url   = (ai_cfg.get("ollama_url") or "").strip()
        ollama_model = (ai_cfg.get("ollama_model") or "").strip()

        raw_fields = (ai_cfg.get("fields") or "").strip()
        enabled_fields = (
            set(raw_fields.split(",")) & _ALL_STANDARD_FIELDS if raw_fields else _ALL_STANDARD_FIELDS
        )
        custom_prompts: list[dict] = ai_cfg.get("custom_prompts") or []

        if not provider:
            return

        mime = _guess_mime(filename)
        is_image = mime.startswith("image/")

        if provider == "gemini":
            if not gemini_key or not gemini_model:
                return
            if mime not in _GEMINI_ALLOWED_MIMES:
                logger.info("ai_analysis: job=%s übersprungen — MIME '%s' nicht unterstützt", job_id, mime)
                _audit_ai("ai_analysis_skipped", {"reason": "unsupported_mime", "mime": mime,
                                                   "provider": provider, "filename": filename})
                return
            if len(file_bytes) > _MAX_BYTES_GEMINI:
                mb = len(file_bytes) // (1024 * 1024)
                logger.info(
                    "ai_analysis: job=%s übersprungen — Datei zu groß (%d MB > %d MB Limit)",
                    job_id, mb, _MAX_BYTES_GEMINI // (1024 * 1024),
                )
                _audit_ai("ai_analysis_skipped", {"reason": "file_too_large", "size_mb": mb,
                                                   "provider": provider, "filename": filename})
                return
            prompt = _build_prompt(is_image, enabled_fields, custom_prompts)
            result = _analyse_gemini(file_bytes, mime, prompt, gemini_key, gemini_model)
        elif provider == "ollama":
            if not ollama_url or not ollama_model:
                return
            if is_image:
                logger.debug("ai_analysis: Ollama für Bild nicht unterstützt — überspringe %s", job_id)
                return
            if len(file_bytes) > _MAX_BYTES_OLLAMA:
                mb = len(file_bytes) // (1024 * 1024)
                logger.info(
                    "ai_analysis: job=%s Datei zu groß für Ollama-Text-Extraktion (%d MB)",
                    job_id, mb,
                )
                _audit_ai("ai_analysis_skipped", {"reason": "file_too_large", "size_mb": mb,
                                                   "provider": provider, "filename": filename})
                return
            prompt = _build_prompt(is_image=False, enabled_fields=enabled_fields,
                                   custom_prompts=custom_prompts, for_ollama=True)
            result = _analyse_ollama(file_bytes, mime, prompt, ollama_url, ollama_model)
        else:
            return

        if not result:
            _audit_ai("ai_analysis_failed", {"reason": "empty_result", "provider": provider,
                                              "filename": filename, "mime": mime})
            return

        from db import _conn

        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        raw_tags = result.get("tags") or []
        if isinstance(raw_tags, list):
            tags_str = ", ".join(str(t).strip() for t in raw_tags[:5] if t)[:200]
        else:
            tags_str = str(raw_tags)[:200]

        # Custom-Felder aus result extrahieren → ai_extra JSON
        extra: dict[str, str] = {}
        for cp in custom_prompts:
            name = (cp.get("name") or "").strip()
            if name and name in result:
                extra[name] = str(result[name])[:500]
        extra_json = json.dumps(extra, ensure_ascii=False)

        with _conn() as conn:
            conn.execute(
                """UPDATE cloudprint_jobs
                   SET ai_doc_type=?, ai_color_rec=?, ai_sensitivity=?,
                       ai_summary=?, ai_tags=?, ai_extra=?, ai_analyzed_at=?
                   WHERE job_id=?""",
                (
                    result.get("doc_type", "")[:80]   if "doc_type"    in enabled_fields else "",
                    result.get("color_rec", "")[:40]  if "color_rec"   in enabled_fields else "",
                    result.get("sensitivity", "")[:40] if "sensitivity" in enabled_fields else "",
                    result.get("summary", "")[:1500]  if "summary"     in enabled_fields else "",
                    tags_str                           if "tags"        in enabled_fields else "",
                    extra_json,
                    now,
                    job_id,
                ),
            )
        logger.info(
            "ai_analysis: job=%s provider=%s fields=%s custom=%d doc_type=%s",
            job_id, provider, ",".join(sorted(enabled_fields)), len(custom_prompts),
            result.get("doc_type", ""),
        )
        _audit_ai("ai_analysis_completed", {
            "provider":   provider,
            "model":      gemini_model or ollama_model,
            "filename":   filename,
            "doc_type":   result.get("doc_type", ""),
            "sensitivity":result.get("sensitivity", ""),
            "color_rec":  result.get("color_rec", ""),
            "tags":       tags_str,
            "fields":     ",".join(sorted(enabled_fields)),
            "custom_fields": len(custom_prompts),
        })
    except Exception as e:
        logger.warning("ai_analysis: job=%s error=%s", job_id, e)
        _audit_ai("ai_analysis_failed", {"reason": str(e)[:300], "filename": filename})


# ── Prompt-Builder ─────────────────────────────────────────────────────────────

def _build_prompt(
    is_image: bool,
    enabled_fields: set[str],
    custom_prompts: list[dict],
    for_ollama: bool = False,
) -> str:
    """Baut einen dynamischen Prompt basierend auf aktivierten Feldern."""
    fields: dict[str, str] = {}

    if is_image and not for_ollama:
        # Foto-Prompt
        if "doc_type" in enabled_fields:
            fields["doc_type"] = '"Foto"'
        if "color_rec" in enabled_fields:
            fields["color_rec"] = '"farbe"'
        if "sensitivity" in enabled_fields:
            fields["sensitivity"] = (
                '"<Wähle eine Kategorie: \'privat\' (persönliches Foto: Essen, Familie, '
                'Freizeit, alltägliche Szenen), \'öffentlich\' (öffentliche Veranstaltung, '
                'Natur, allgemeine Szene ohne Personenbezug), \'intern\' (Berufliches, '
                'Büro, Arbeitsplatz, Geschäftstreffen), \'vertraulich\' '
                '(sensible Geschäftsdaten, vertrauliche Inhalte)>"'
            )
        if "tags" in enabled_fields:
            fields["tags"] = '["<Schlagwort 1>", "<Schlagwort 2>", "<Schlagwort 3>"]'
        if "summary" in enabled_fields:
            fields["summary"] = '"<2–3 Sätze ausführliche Bildbeschreibung: Was ist zu sehen, Stimmung, besondere Details>"'
    else:
        # Dokument-Prompt
        if "doc_type" in enabled_fields:
            fields["doc_type"] = '"<Dokumenttyp, z.B. Rechnung, Präsentation, Vertrag, Bericht, Formular, Brief, Sonstiges>"'
        if "color_rec" in enabled_fields:
            fields["color_rec"] = '"<\'farbe\' oder \'schwarzweiss\'>"'
        if "sensitivity" in enabled_fields:
            fields["sensitivity"] = '"<\'öffentlich\', \'privat\', \'intern\' oder \'vertraulich\'>"'
        if "tags" in enabled_fields:
            fields["tags"] = "[]"
        if "summary" in enabled_fields:
            fields["summary"] = '"<2–3 Sätze Zusammenfassung des Inhalts>"'

    # Custom-Felder anhängen
    for cp in custom_prompts:
        name = (cp.get("name") or "").strip()
        cp_prompt = (cp.get("prompt") or "").strip()
        if name and cp_prompt:
            fields[name] = json.dumps(f"<{cp_prompt}>")

    # JSON-Schema-String bauen
    lines = []
    for k, v in fields.items():
        lines.append(f'  "{k}": {v}')
    schema = "{\n" + ",\n".join(lines) + "\n}"

    intro = (
        "Beschreibe dieses Bild" if (is_image and not for_ollama)
        else "Analysiere dieses Dokument" if not for_ollama
        else "Analysiere den folgenden Dokumenttext"
    )
    hint = ""
    if is_image and "tags" in enabled_fields and not for_ollama:
        hint = "\nWähle 2–3 prägnante Schlagwörter die das Bild kategorisieren (z.B. Natur, Architektur, Personen, Essen, Tier, Landschaft, Innen, Außen …)."

    base = f"{intro} und antworte NUR mit einem JSON-Objekt (kein Markdown, kein Text drumherum):\n{schema}{hint}"
    if for_ollama:
        base += "\n\nDokumenttext:\n"
    return base


# ── Gemini ────────────────────────────────────────────────────────────────────

def _analyse_gemini(
    file_bytes: bytes,
    mime: str,
    prompt: str,
    api_key: str,
    model: str,
) -> dict | None:
    import base64
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

def _analyse_ollama(
    file_bytes: bytes,
    mime: str,
    prompt: str,
    base_url: str,
    model: str,
) -> dict | None:
    text = _extract_text(file_bytes, mime)
    if not text:
        return None
    full_prompt = prompt + text[:_MAX_TEXT_OLLAMA]
    payload = {
        "model": model,
        "prompt": full_prompt,
        "stream": False,
        "options": {"temperature": 0.1},
    }
    from urllib.parse import urlparse as _urlparse
    _parsed = _urlparse(base_url)
    if _parsed.scheme not in ("http", "https"):
        logger.warning("ollama: ungültiges URL-Schema '%s' — abgebrochen", _parsed.scheme)
        return None
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
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        return json.loads(fence_match.group(1))
    if text.startswith("{"):
        return json.loads(text)
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
