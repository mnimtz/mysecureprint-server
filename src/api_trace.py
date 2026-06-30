"""
API-Trace-Logger fuer Outbound-Calls (v0.7.11).

Schreibt jeden HTTP-Call zur Printix-Cloud-API (und ggf. anderen externen
Services) in die SQLite-Tabelle `api_trace_log`. Das Feature ist explizit
fuer Admin-Debugging gedacht — beim 500 von Printix steht meist nichts
Brauchbares im Log; mit aktivem API-Trace kann der Admin im Web-UI
Request-Body und Response-Body inspizieren.

WICHTIG zur Sicherheit:
- Authorization-Header werden maskiert (Bearer-Token nur erste 4 + letzte 4 Zeichen).
- `client_secret=...` und `password=...` in URLs/Bodies werden redacted.
- Request- und Response-Body werden auf 4 KB gekuerzt.

Default ist das Logging AUS — der Admin schaltet es ueber
/admin/settings?section=advanced (Checkbox `api_trace_enabled`) gezielt
fuer eine Debug-Session ein.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("api_trace")

# Lokaler Counter — wir prunen alle ~100 Eintraege.
_insert_counter = 0
_counter_lock = threading.Lock()

MAX_BODY_BYTES = 4096
MAX_ROWS = 5000

# Regex fuer Secrets in URLs / Bodies.
_SECRET_PATTERNS = [
    (re.compile(r'(client_secret=)([^&"\s]+)'),       r'\1***'),
    (re.compile(r'(password=)([^&"\s]+)'),            r'\1***'),
    (re.compile(r'("client_secret"\s*:\s*")([^"]+)'), r'\1***'),
    (re.compile(r'("password"\s*:\s*")([^"]+)'),      r'\1***'),
    (re.compile(r'("api_key"\s*:\s*")([^"]+)'),       r'\1***'),
]


def init_schema() -> None:
    """Legt die Trace-Tabelle an (idempotent)."""
    try:
        from db import _conn
    except Exception:
        return
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS api_trace_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ts              TEXT NOT NULL,
                direction       TEXT NOT NULL DEFAULT 'outbound',
                component       TEXT NOT NULL DEFAULT '',
                method          TEXT NOT NULL,
                url             TEXT NOT NULL,
                query_string    TEXT NOT NULL DEFAULT '',
                req_headers     TEXT NOT NULL DEFAULT '',
                req_body        TEXT NOT NULL DEFAULT '',
                status_code     INTEGER NOT NULL DEFAULT 0,
                resp_headers    TEXT NOT NULL DEFAULT '',
                resp_body       TEXT NOT NULL DEFAULT '',
                duration_ms     INTEGER NOT NULL DEFAULT 0,
                error           TEXT NOT NULL DEFAULT '',
                user_id         TEXT NOT NULL DEFAULT '',
                job_id          TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_api_trace_ts
                ON api_trace_log (ts DESC);
            CREATE INDEX IF NOT EXISTS idx_api_trace_status
                ON api_trace_log (status_code, ts DESC);
        """)


def is_enabled() -> bool:
    """Cheap-Lookup — ein Setting-Read pro Call. Default OFF."""
    try:
        from db import get_setting
        return get_setting("api_trace_enabled", "0") == "1"
    except Exception:
        return False


def _mask_auth(value: str) -> str:
    if not value:
        return ""
    v = value.strip()
    if v.lower().startswith("bearer "):
        tok = v[7:]
        if len(tok) <= 10:
            return "Bearer ***"
        return f"Bearer {tok[:4]}...{tok[-4:]}"
    if len(v) > 12:
        return f"{v[:4]}...{v[-4:]}"
    return "***"


def _mask_secrets(text: str) -> str:
    if not text:
        return ""
    out = text
    for pat, repl in _SECRET_PATTERNS:
        out = pat.sub(repl, out)
    return out


def _cap(body: Any) -> str:
    if body is None:
        return ""
    if isinstance(body, (bytes, bytearray)):
        try:
            s = body.decode("utf-8", errors="replace")
        except Exception:
            s = f"<{len(body)} bytes binary>"
    elif isinstance(body, (dict, list)):
        try:
            s = json.dumps(body, ensure_ascii=False, default=str)
        except Exception:
            s = str(body)
    else:
        s = str(body)
    s = _mask_secrets(s)
    if len(s) > MAX_BODY_BYTES:
        return s[:MAX_BODY_BYTES] + f"\n... [truncated {len(s) - MAX_BODY_BYTES} bytes]"
    return s


def _headers_to_json(headers: Optional[dict]) -> str:
    if not headers:
        return ""
    safe: dict = {}
    for k, v in headers.items():
        sv = str(v)
        kl = k.lower()
        if kl in ("authorization", "proxy-authorization", "x-api-key", "x-auth-token"):
            sv = _mask_auth(sv)
        elif kl == "cookie":
            sv = "***"
        safe[k] = sv
    try:
        return json.dumps(safe, ensure_ascii=False)
    except Exception:
        return str(safe)


def _prune_if_needed() -> None:
    """Alle ~100 Inserts: alles ueber MAX_ROWS hinaus loeschen."""
    global _insert_counter
    with _counter_lock:
        _insert_counter += 1
        do_prune = (_insert_counter % 100 == 0)
    if not do_prune:
        return
    try:
        from db import _conn
        with _conn() as conn:
            conn.execute(
                "DELETE FROM api_trace_log "
                "WHERE id NOT IN ("
                "  SELECT id FROM api_trace_log ORDER BY id DESC LIMIT ?"
                ")",
                (MAX_ROWS,),
            )
    except Exception as e:
        logger.debug("api_trace prune failed: %s", e)


def log_api_call(
    *,
    component: str,
    method: str,
    url: str,
    query_string: str = "",
    req_headers: Optional[dict] = None,
    req_body: Any = None,
    status_code: int = 0,
    resp_headers: Optional[dict] = None,
    resp_body: Any = None,
    duration_ms: int = 0,
    error: str = "",
    user_id: str = "",
    job_id: str = "",
) -> None:
    """Schreibt einen Trace-Eintrag. No-Op wenn Trace deaktiviert ist."""
    if not is_enabled():
        return
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        safe_url = _mask_secrets(url or "")
        safe_qs  = _mask_secrets(query_string or "")
        from db import _conn
        with _conn() as conn:
            conn.execute(
                "INSERT INTO api_trace_log "
                "(ts, direction, component, method, url, query_string, "
                " req_headers, req_body, status_code, resp_headers, resp_body, "
                " duration_ms, error, user_id, job_id) "
                "VALUES (?, 'outbound', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    (component or "")[:64],
                    (method or "")[:16].upper(),
                    safe_url[:2048],
                    safe_qs[:2048],
                    _headers_to_json(req_headers),
                    _cap(req_body),
                    int(status_code or 0),
                    _headers_to_json(resp_headers),
                    _cap(resp_body),
                    int(duration_ms or 0),
                    (error or "")[:1024],
                    (user_id or "")[:64],
                    (job_id or "")[:64],
                ),
            )
        _prune_if_needed()
    except Exception as e:
        # Trace darf NIE den eigentlichen Call brechen.
        logger.debug("api_trace insert failed: %s", e)


def trace_request(
    session,
    method: str,
    url: str,
    *,
    component: str = "printix",
    user_id: str = "",
    job_id: str = "",
    **kwargs,
):
    """Wrapper um session.request(). Misst Dauer, loggt nach DB,
    gibt die Response (oder reraised) wie ein normaler `requests`-Aufruf zurueck.

    Ist API-Trace aus, faellt die Funktion auf einen einfachen
    session.request()-Call zurueck — kein Overhead.
    """
    enabled = is_enabled()
    if not enabled:
        return session.request(method, url, **kwargs)

    # Body fuer Logging extrahieren BEVOR requests ihn konsumiert/serialisiert.
    req_body_for_log: Any = None
    if "json" in kwargs and kwargs["json"] is not None:
        req_body_for_log = kwargs["json"]
    elif "data" in kwargs and kwargs["data"] is not None:
        req_body_for_log = kwargs["data"]

    req_headers_for_log = kwargs.get("headers")
    params = kwargs.get("params")
    qs = ""
    if isinstance(params, dict):
        try:
            from urllib.parse import urlencode
            qs = urlencode({k: v for k, v in params.items() if v is not None})
        except Exception:
            qs = str(params)

    t0 = time.time()
    status = 0
    resp_headers = None
    resp_body_for_log: Any = None
    err = ""
    try:
        resp = session.request(method, url, **kwargs)
        status = resp.status_code
        try:
            resp_headers = dict(resp.headers)
        except Exception:
            resp_headers = None
        try:
            resp_body_for_log = resp.text
        except Exception:
            resp_body_for_log = "<unreadable body>"
        return resp
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        raise
    finally:
        dur_ms = int((time.time() - t0) * 1000)
        try:
            log_api_call(
                component=component,
                method=method,
                url=url,
                query_string=qs,
                req_headers=req_headers_for_log,
                req_body=req_body_for_log,
                status_code=status,
                resp_headers=resp_headers,
                resp_body=resp_body_for_log,
                duration_ms=dur_ms,
                error=err,
                user_id=user_id,
                job_id=job_id,
            )
        except Exception:
            pass


# ─── Abfragen fuer Admin-UI ───────────────────────────────────────────────────

def list_trace_entries(
    *,
    component: str = "",
    method: str = "",
    status_class: str = "",   # '', '2xx', '3xx', '4xx', '5xx', 'err'
    search: str = "",
    page: int = 1,
    page_size: int = 100,
) -> tuple[list[dict], int]:
    """Liefert (rows, total_count) fuer das Admin-Trace-UI."""
    from db import _conn
    where = []
    params: list = []
    if component:
        where.append("component = ?")
        params.append(component)
    if method:
        where.append("method = ?")
        params.append(method.upper())
    if status_class == "2xx":
        where.append("status_code >= 200 AND status_code < 300")
    elif status_class == "3xx":
        where.append("status_code >= 300 AND status_code < 400")
    elif status_class == "4xx":
        where.append("status_code >= 400 AND status_code < 500")
    elif status_class == "5xx":
        where.append("status_code >= 500 AND status_code < 600")
    elif status_class == "err":
        where.append("(status_code = 0 OR error <> '')")
    if search:
        where.append("(url LIKE ? OR req_body LIKE ? OR resp_body LIKE ? OR error LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like, like])
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    offset = max(0, (page - 1) * page_size)
    with _conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM api_trace_log{where_sql}",
            tuple(params),
        ).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM api_trace_log{where_sql} "
            f"ORDER BY id DESC LIMIT ? OFFSET ?",
            tuple(params) + (page_size, offset),
        ).fetchall()
        return [dict(r) for r in rows], total


def get_trace_entry(entry_id: int) -> Optional[dict]:
    from db import _conn
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM api_trace_log WHERE id = ?", (entry_id,)
        ).fetchone()
        return dict(row) if row else None


def list_distinct_components() -> list[str]:
    try:
        from db import _conn
        with _conn() as conn:
            return [
                r["component"] for r in conn.execute(
                    "SELECT DISTINCT component FROM api_trace_log "
                    "WHERE component <> '' ORDER BY component ASC"
                ).fetchall()
            ]
    except Exception:
        return []


def clear_all() -> int:
    """Loescht alle Trace-Eintraege (Admin-Action)."""
    try:
        from db import _conn
        with _conn() as conn:
            cur = conn.execute("DELETE FROM api_trace_log")
            return cur.rowcount or 0
    except Exception:
        return 0
