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
_ALL_SUPPLIES_TTL_SEC = 600  # 10 min — UI-Sichten erwarten schnelle Antwort;
                              # Runner refresht ohnehin eigenstaendig
_TOPOLOGY_CACHE: dict[str, tuple[float, dict]] = {}
_TOPOLOGY_TTL_SEC = 600  # 10 min — Netzwerk-Struktur aendert sich selten


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


def fetch_network_topology(tenant: dict, force_refresh: bool = False) -> Optional[dict]:
    """Kompletter Tenant-Baum: Sites → Networks → Printers + Workstations + Users.

    Returns:
        {
          "sites": [{"id","name","type","networks":[
              {"id","name","mobile_print","printers":[...],"workstations":[...]}
          ]}],
          "unassigned": {"printers":[...], "workstations":[...]},
          "counts": {"sites":N, "networks":N, "printers":N, "workstations":N, "users":N},
        }

    Workstations bringen eingeloggte User in `users:[]` mit.
    None wenn keine Creds oder DB-Fehler.
    """
    if not _has_creds(tenant):
        return None
    tenant_key = str(tenant.get("printix_tenant_id") or tenant.get("id") or "")
    now = time.time()
    if not force_refresh:
        with _CACHE_LOCK:
            entry = _TOPOLOGY_CACHE.get(tenant_key)
            if entry and (now - entry[0]) < _TOPOLOGY_TTL_SEC:
                return entry[1]
    try:
        import pymssql
    except ImportError:
        return None
    conn = None
    try:
        conn = pymssql.connect(
            server=tenant["sql_server"], user=tenant["sql_username"],
            password=tenant["sql_password"], database=tenant["sql_database"],
            port=1433, tds_version="7.4",
            login_timeout=30, timeout=60,
        )
        cur = conn.cursor(as_dict=True)

        cur.execute("""SELECT id, name, type FROM dbo.sites
                        WHERE meta_status = 'ACTIVE'
                     ORDER BY name""")
        sites = [{"id": str(r["id"]), "name": r["name"] or "",
                  "type": r.get("type") or "", "networks": []}
                 for r in cur.fetchall()]

        cur.execute("""SELECT sn.site_id, sn.network_id, n.name, n.mobile_print
                         FROM dbo.site_networks sn
                         JOIN dbo.networks n ON n.id = sn.network_id
                        WHERE n.meta_status = 'ACTIVE'""")
        site_networks_map = {}
        network_to_site = {}
        for r in cur.fetchall():
            nid = str(r["network_id"])
            sid = str(r["site_id"])
            site_networks_map.setdefault(sid, []).append({
                "id": nid, "name": r["name"] or "",
                "mobile_print": bool(r.get("mobile_print", 0)),
                "printers": [], "workstations": [],
            })
            network_to_site[nid] = sid

        cur.execute("""SELECT id, name, model_name, vendor_name, location, network_id
                         FROM dbo.printers
                        WHERE meta_status = 'ACTIVE'""")
        printers_by_network: dict = {}
        printers_unassigned = []
        for r in cur.fetchall():
            p = {
                "id":     str(r["id"]),
                "name":   r["name"] or "",
                "model":  r.get("model_name") or "",
                "vendor": r.get("vendor_name") or "",
                "location": r.get("location") or "",
            }
            nid = str(r.get("network_id") or "")
            if nid and nid != "None":
                printers_by_network.setdefault(nid, []).append(p)
            else:
                printers_unassigned.append(p)

        # Workstations + Users (v0.7.276: erweitert um IP/Host/Client-Version
        # fuer den Netzwerk-Details-Toggle im Netzwerk-Plan)
        cur.execute("""SELECT id, name, os, ws_type, network_ssid,
                              network_extenral_address_ip AS ext_ip,
                              network_external_address_name AS ext_host,
                              client_version
                         FROM dbo.workstations
                        WHERE meta_status = 'ACTIVE'""")
        workstations = [{
            "id": str(r["id"]),
            "name": r["name"] or "",
            "os": r.get("os") or "",
            "type": r.get("ws_type") or "",
            "ssid": r.get("network_ssid") or "",
            "ip": (r.get("ext_ip") or "").strip(),
            "host": (r.get("ext_host") or "").strip(),
            "client_version": (r.get("client_version") or "").strip(),
            "users": [],
        } for r in cur.fetchall()]
        ws_by_id = {w["id"]: w for w in workstations}

        cur.execute("""SELECT wu.workstation_id, wu.user_id,
                              u.name AS user_name, u.email, u.department
                         FROM dbo.workstation_users wu
                         JOIN dbo.users u ON u.id = wu.user_id
                        WHERE u.meta_status = 'ACTIVE'""")
        for r in cur.fetchall():
            wid = str(r["workstation_id"])
            if wid in ws_by_id:
                ws_by_id[wid]["users"].append({
                    "id":         str(r["user_id"]),
                    "name":       r.get("user_name") or "",
                    "email":      r.get("email") or "",
                    "department": r.get("department") or "",
                })

        # Workstations mappen sich per SSID auf Network — wir sortieren sie
        # dem passenden Netzwerk zu falls SSID matches, sonst "unassigned".
        network_ssid_map = {}
        for site in sites:
            for net in site_networks_map.get(site["id"], []):
                network_ssid_map[net["name"].lower()] = net["id"]
        ws_by_network: dict = {}
        ws_unassigned = []
        for w in workstations:
            ssid = (w.get("ssid") or "").lower().strip()
            nid = network_ssid_map.get(ssid, "")
            if nid:
                ws_by_network.setdefault(nid, []).append(w)
            else:
                ws_unassigned.append(w)

        # Baum zusammenbauen
        user_ids_seen = set()
        printer_count = 0
        ws_count = 0
        for site in sites:
            site["networks"] = site_networks_map.get(site["id"], [])
            for net in site["networks"]:
                net["printers"]     = printers_by_network.get(net["id"], [])
                net["workstations"] = ws_by_network.get(net["id"], [])
                printer_count += len(net["printers"])
                ws_count += len(net["workstations"])
                for w in net["workstations"]:
                    for u in w["users"]:
                        user_ids_seen.add(u["id"])

        result = {
            "sites": sites,
            "unassigned": {
                "printers": printers_unassigned,
                "workstations": ws_unassigned,
            },
            "counts": {
                "sites": len(sites),
                "networks": sum(len(s["networks"]) for s in sites),
                "printers": printer_count + len(printers_unassigned),
                "workstations": ws_count + len(ws_unassigned),
                "users": len(user_ids_seen),
            },
        }
        with _CACHE_LOCK:
            _TOPOLOGY_CACHE[tenant_key] = (now, result)
        return result
    except Exception as e:  # noqa: BLE001
        logger.info("bi_client.fetch_network_topology failed: %s", str(e)[:200])
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def fetch_raw_reading(tenant: dict, printer_id: str) -> Optional[dict]:
    """Roh-Datensatz vom neuesten device_reading fuer einen Drucker.

    Gibt alle Felder + parsed JSON zurueck, so wie sie im Printix BI-DB
    liegen. Nuetzlich fuer Diagnose wenn die Marker-Werte nicht plausibel
    sind (Brother-Trommel-vs-Toner, WASTE-Container, unbekannte Keys).
    """
    if not _has_creds(tenant) or not printer_id:
        return None
    try:
        import pymssql
    except ImportError:
        return None
    conn = None
    try:
        conn = pymssql.connect(
            server=tenant["sql_server"], user=tenant["sql_username"],
            password=tenant["sql_password"], database=tenant["sql_database"],
            port=1433, tds_version="7.4",
            login_timeout=15, timeout=20,
        )
        cur = conn.cursor(as_dict=True)
        cur.execute("""
            SELECT TOP 5 additional_readings, detected_error_states,
                         printer_reported_state, received_time, meta_status
              FROM dbo.device_readings
             WHERE printer_id = %s
          ORDER BY received_time DESC
        """, (printer_id,))
        rows = cur.fetchall()
        if not rows:
            return None
        parsed_history = []
        for r in rows:
            try:
                parsed = json.loads(r.get("additional_readings") or "{}")
            except (ValueError, TypeError):
                parsed = {}
            try:
                errs = json.loads(r.get("detected_error_states") or "[]")
            except (ValueError, TypeError):
                errs = []
            parsed_history.append({
                "received_time":   str(r.get("received_time") or ""),
                "meta_status":     r.get("meta_status") or "",
                "reported_state":  r.get("printer_reported_state") or "",
                "error_states":    errs,
                "readings":        parsed,
            })
        return {"printer_id": str(printer_id), "history": parsed_history}
    except Exception as e:  # noqa: BLE001
        logger.info("bi_client.fetch_raw_reading failed: %s", str(e)[:200])
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


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
