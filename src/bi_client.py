"""Printix BI-DB (Azure SQL) client — read-only queries for supply/status data.

Printix hostet pro Tenant eine Analytics-DB (Azure SQL). Der Kunde hinterlegt
im Server-Setup die Zugangsdaten (sql_server/database/username/password). Diese
Datenbank enthält Felder, die die reguläre Printix Cloud Print API nicht
ausliefert — allen voran Toner-Level pro Drucker in `device_readings`.

Dieses Modul ist bewusst schlank gehalten:
- Kurzlebiger In-Memory-Cache (5 min) pro (tenant_id, printer_id) — die BI-DB
  ist bei Azure Auto-Pause nach Idle langsam beim ersten Query, und dieselben
  Drucker-Details werden im UI häufig hintereinander geöffnet.
- Timeouts und Fehler werden geschluckt (Return None) — Toner ist ein Nice-to-
  have, ein DB-Ausfall darf den Drucker-Detail-Endpoint nicht kippen.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

_SUPPLIES_CACHE: dict[tuple[str, str], tuple[float, list]] = {}
_SUPPLIES_TTL_SEC = 300  # 5 Minuten
_CACHE_LOCK = threading.Lock()

# Mapping der Printix-BI Marker-Keys auf die von iOS erwarteten Farbnamen.
_MARKER_TO_COLOR = {
    "MARKER_BLACK":   "black",
    "MARKER_CYAN":    "cyan",
    "MARKER_MAGENTA": "magenta",
    "MARKER_YELLOW":  "yellow",
}


def _has_creds(tenant: dict) -> bool:
    return bool(tenant.get("sql_server") and tenant.get("sql_database")
                and tenant.get("sql_username") and tenant.get("sql_password"))


def fetch_printer_supplies(tenant: dict, printer_id: str) -> Optional[list[dict]]:
    """Neueste Toner-Level für einen Drucker aus der Printix BI-DB.

    Returns eine Liste im Format, das die iOS-App (PrinterSupply) erwartet:
        [{"color": "black", "level": 64, "maxLevel": 100}, ...]

    None wenn:
    - Tenant hat keine SQL-Creds hinterlegt
    - device_readings hat keinen Eintrag zu dem printer_id (unbekannter Drucker
      oder Printix hat noch keine Werte übertragen)
    - additional_readings enthält keine MARKER_*-Keys (Drucker ohne SNMP-Support)
    - DB-Fehler / Timeout

    Cache-Hit unterhalb von _SUPPLIES_TTL_SEC verpasst den DB-Roundtrip. Ein
    Cache-Hit auf None wird ebenfalls kurz gehalten (60s), damit stumme Drucker
    nicht bei jedem Detail-Load die BI-DB pingen.
    """
    if not _has_creds(tenant) or not printer_id:
        return None

    tenant_key = tenant.get("printix_tenant_id") or tenant.get("id") or ""
    key = (str(tenant_key), str(printer_id))
    now = time.time()

    with _CACHE_LOCK:
        entry = _SUPPLIES_CACHE.get(key)
        if entry and (now - entry[0]) < _SUPPLIES_TTL_SEC:
            cached = entry[1]
            return cached if cached else None

    supplies = _query_supplies(tenant, printer_id)
    with _CACHE_LOCK:
        # Positiv-Cache 5 min, Negativ-Cache nur 60s
        ttl_offset = _SUPPLIES_TTL_SEC - 60 if not supplies else 0
        _SUPPLIES_CACHE[key] = (now - ttl_offset if not supplies else now,
                                supplies or [])
    return supplies


def _query_supplies(tenant: dict, printer_id: str) -> Optional[list[dict]]:
    try:
        import pymssql  # noqa: WPS433 — lazy import, Kunden ohne BI-DB brauchen es nicht
    except ImportError:
        logger.debug("pymssql nicht verfügbar, überspringe BI-DB-Toner-Query")
        return None

    conn = None
    try:
        conn = pymssql.connect(
            server=tenant["sql_server"],
            user=tenant["sql_username"],
            password=tenant["sql_password"],
            database=tenant["sql_database"],
            port=1433,
            tds_version="7.4",
            login_timeout=8,
            timeout=8,
        )
        cur = conn.cursor(as_dict=True)
        # printer_id ist uniqueidentifier; RLS greift automatisch auf tenant_id.
        cur.execute(
            """SELECT TOP 1 additional_readings, received_time
                 FROM dbo.device_readings
                WHERE printer_id = %s
                  AND additional_readings IS NOT NULL
             ORDER BY received_time DESC""",
            (printer_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return _parse_markers(row.get("additional_readings"))
    except Exception as e:  # noqa: BLE001 — BI-DB-Fehler dürfen den Request nicht killen
        logger.info("bi_client.fetch_printer_supplies fehlgeschlagen "
                    "(printer=%s): %s", printer_id, str(e)[:200])
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _parse_markers(raw: Optional[str]) -> Optional[list[dict]]:
    """Extrahiert MARKER_* Prozent-Werte aus dem additional_readings JSON."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    supplies: list[dict] = []
    for marker_key, color in _MARKER_TO_COLOR.items():
        val = data.get(marker_key)
        if val is None:
            continue
        try:
            percent = int(str(val).strip())
        except (ValueError, TypeError):
            continue
        if percent < 0 or percent > 100:
            continue
        supplies.append({"color": color, "level": percent, "maxLevel": 100})

    # Reihenfolge: Schwarz zuerst, dann CMY — passt zur iOS-Renderreihenfolge.
    _order = ["black", "cyan", "magenta", "yellow"]
    supplies.sort(key=lambda s: _order.index(s["color"]))
    return supplies or None


# ─── Batch-Queries für Toner-Alerts ───────────────────────────────────────────

_ALL_SUPPLIES_CACHE: dict[str, tuple[float, list]] = {}
_ALL_SUPPLIES_TTL_SEC = 180  # 3 min — der Alert-Runner ruft eh nur alle 15+ min


def fetch_all_printer_supplies(tenant: dict) -> Optional[list[dict]]:
    """Jüngstes Reading pro Drucker im Tenant mit Toner-Levels + Name.

    Returns:
        [{"printer_id": "...", "printer_name": "Kyocera-EG", "location": "...",
          "received_time": datetime, "supplies": [{"color":"black","level":26,...}, ...],
          "error_states": ["LOW_TONER"], "reported_state": "IDLE"}, ...]

    None wenn keine SQL-Creds oder DB-Fehler.
    """
    if not _has_creds(tenant):
        return None

    tenant_key = str(tenant.get("printix_tenant_id") or tenant.get("id") or "")
    now = time.time()
    with _CACHE_LOCK:
        entry = _ALL_SUPPLIES_CACHE.get(tenant_key)
        if entry and (now - entry[0]) < _ALL_SUPPLIES_TTL_SEC:
            return entry[1]

    result = _query_all_supplies(tenant)
    if result is not None:
        with _CACHE_LOCK:
            _ALL_SUPPLIES_CACHE[tenant_key] = (now, result)
    return result


def _query_all_supplies(tenant: dict) -> Optional[list[dict]]:
    try:
        import pymssql
    except ImportError:
        return None

    conn = None
    try:
        conn = pymssql.connect(
            server=tenant["sql_server"],
            user=tenant["sql_username"],
            password=tenant["sql_password"],
            database=tenant["sql_database"],
            port=1433,
            tds_version="7.4",
            login_timeout=30,  # Azure Auto-Pause: erster Connect kann 15-25s dauern
            timeout=60,
        )
        cur = conn.cursor(as_dict=True)
        # Zweistufig — vermeidet OUTER APPLY über 143k Printer-Rows global:
        # 1) alle aktiven Drucker (RLS-scoped, wenige pro Tenant)
        # 2) pro Drucker einzeln TOP 1 device_reading (nutzt den
        #    (printer_id, received_time)-Index)
        cur.execute("""SELECT id AS printer_id, name AS printer_name, location
                         FROM dbo.printers
                        WHERE meta_status = 'ACTIVE'""")
        printers = cur.fetchall()

        rows: list[dict] = []
        for p in printers:
            pid = p["printer_id"]
            try:
                cur.execute("""SELECT TOP 1 additional_readings,
                                      detected_error_states,
                                      printer_reported_state,
                                      received_time
                                 FROM dbo.device_readings
                                WHERE printer_id = %s
                             ORDER BY received_time DESC""", (pid,))
                reading = cur.fetchone()
            except Exception:
                reading = None
            rows.append({
                "printer_id":   pid,
                "printer_name": p.get("printer_name"),
                "location":     p.get("location"),
                "additional_readings":    reading.get("additional_readings") if reading else None,
                "detected_error_states":  reading.get("detected_error_states") if reading else None,
                "printer_reported_state": reading.get("printer_reported_state") if reading else None,
                "received_time":          reading.get("received_time") if reading else None,
            })
    except Exception as e:  # noqa: BLE001
        logger.info("bi_client.fetch_all_printer_supplies fehlgeschlagen: %s",
                    str(e)[:200])
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    out: list[dict] = []
    for r in rows:
        supplies = _parse_markers(r.get("additional_readings")) or []
        errs = _parse_error_states(r.get("detected_error_states"))
        out.append({
            "printer_id":     str(r["printer_id"]),
            "printer_name":   r.get("printer_name") or "",
            "location":       r.get("location") or "",
            "supplies":       supplies,
            "error_states":   errs,
            "reported_state": r.get("printer_reported_state") or "",
            "received_time":  r.get("received_time"),
        })
    return out


def _parse_error_states(raw) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x) for x in data if x]
    except (ValueError, TypeError):
        pass
    return []


def estimate_days_until_empty(tenant: dict, printer_id: str, color: str,
                              current_level: int) -> Optional[float]:
    """Grobe Prognose "Tage bis leer" basierend auf letzten 14 Tagen Verbrauch.

    Nimmt zwei device_readings — das älteste innerhalb der letzten 14 Tage und
    das neueste — und berechnet die Verbrauchsrate. Sehr defensive Heuristik:
    - Wenn Level in dem Zeitraum ohnehin gestiegen ist (Toner gewechselt), None
    - Wenn Zeitspanne < 24h, None (nicht aussagekräftig)
    - Bei rate = 0 (kein Verbrauch), None
    - Cap bei 999 Tagen
    """
    if current_level is None or current_level <= 0 or not _has_creds(tenant):
        return None
    marker_key = f"MARKER_{color.upper()}"
    try:
        import pymssql
    except ImportError:
        return None

    conn = None
    try:
        conn = pymssql.connect(
            server=tenant["sql_server"], user=tenant["sql_username"],
            password=tenant["sql_password"], database=tenant["sql_database"],
            port=1433, tds_version="7.4", login_timeout=6, timeout=10,
        )
        cur = conn.cursor(as_dict=True)
        cur.execute("""
            SELECT TOP 1 additional_readings, received_time
              FROM dbo.device_readings
             WHERE printer_id = %s
               AND additional_readings LIKE %s
               AND received_time <= DATEADD(day, -1, SYSUTCDATETIME())
               AND received_time >= DATEADD(day, -14, SYSUTCDATETIME())
          ORDER BY received_time ASC
        """, (printer_id, f"%{marker_key}%"))
        row = cur.fetchone()
        if not row:
            return None
        try:
            old = json.loads(row["additional_readings"])
            old_level = int(str(old.get(marker_key, "")).strip())
        except (ValueError, TypeError, KeyError):
            return None
        delta_days = (
            (__import__("datetime").datetime.utcnow() - row["received_time"])
            .total_seconds() / 86400.0
        )
        if delta_days < 1.0:
            return None
        consumed = old_level - current_level  # positive Zahl = Verbrauch
        if consumed <= 0:
            return None
        rate_per_day = consumed / delta_days
        if rate_per_day <= 0:
            return None
        days_left = current_level / rate_per_day
        return min(999.0, max(0.0, days_left))
    except Exception as e:  # noqa: BLE001
        logger.debug("estimate_days_until_empty failed: %s", str(e)[:200])
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
