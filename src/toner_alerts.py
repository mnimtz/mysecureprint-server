"""Toner-Alert-System — beobachtet Toner-Level via Printix BI-DB und schickt
Emails bevor Toner leer ist.

Konzept:
- Pro Tenant (aktuell: Single-Tenant → Settings global gehalten) eine
  Konfiguration mit Schwellwerten und Empfängern.
- Runner iteriert alle Tenants mit aktiviertem Alerting, holt aus der BI-DB
  alle Drucker + Level, vergleicht mit last_notified_state je (printer, color).
- Bei Neuunterschreitung: Email. Bei erneutem Anstieg über Hysterese-Schwelle:
  State zurücksetzen (armed).
- Zwei Stufen: WARN (Standard 20%) und CRITICAL (Standard 5%). CRITICAL
  überstimmt den WARN-Cooldown — man wird immer sofort informiert wenn ein
  Drucker in den kritischen Bereich rutscht.
- Digest-Mode: statt pro Drop einzeln, einmal am Tag ein Sammelmail mit allen
  aktuell unterschrittenen Levels (Standard: aus).
- Quiet Hours: Zeitfenster in dem Emails eingesammelt und beim ersten "aktiven"
  Tick nachgeholt werden.
- Predictive: aus device_readings-Historie geschätzte "Tage bis leer" — wenn
  gesetzt und Prognose <= lead_time_days, gilt der Drucker als low_toner
  UNABHÄNGIG vom Prozentwert (rechtzeitig-vor-Leerung-Alarm).
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)

# ─── DB-Schema ────────────────────────────────────────────────────────────────


def _db_path() -> str:
    """Verwendet dieselbe SQLite wie der Rest des Servers."""
    from db import DB_PATH
    return DB_PATH


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path())
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def init_schema() -> None:
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS toner_alert_settings (
            tenant_id            TEXT PRIMARY KEY,
            enabled              INTEGER NOT NULL DEFAULT 0,
            threshold_warn       INTEGER NOT NULL DEFAULT 20,
            threshold_critical   INTEGER NOT NULL DEFAULT 5,
            hysteresis_percent   INTEGER NOT NULL DEFAULT 10,
            recipients           TEXT NOT NULL DEFAULT '',
            check_interval_min   INTEGER NOT NULL DEFAULT 60,
            digest_mode          INTEGER NOT NULL DEFAULT 0,
            digest_hour_utc      INTEGER NOT NULL DEFAULT 7,
            quiet_hours_start    INTEGER NOT NULL DEFAULT -1,
            quiet_hours_end      INTEGER NOT NULL DEFAULT -1,
            lead_time_days       INTEGER NOT NULL DEFAULT 0,
            include_error_states INTEGER NOT NULL DEFAULT 1,
            updated_at           TEXT NOT NULL DEFAULT '',
            last_check_at        TEXT NOT NULL DEFAULT '',
            last_digest_at       TEXT NOT NULL DEFAULT ''
        )""")
        # v0.7.269 — Email-Template-Editor: 2 zusaetzliche Spalten falls die
        # Tabelle schon existiert. IF-NOT-EXISTS gibt es nicht, deshalb
        # try/except pro ALTER.
        for col in ("email_subject_template", "email_body_template"):
            try:
                c.execute(f"ALTER TABLE toner_alert_settings "
                          f"ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
            except Exception:
                pass  # existiert schon
        c.execute("""CREATE TABLE IF NOT EXISTS toner_alert_state (
            tenant_id     TEXT NOT NULL,
            printer_id    TEXT NOT NULL,
            color         TEXT NOT NULL,
            level         INTEGER NOT NULL,
            severity      TEXT NOT NULL DEFAULT 'ok',
            armed         INTEGER NOT NULL DEFAULT 1,
            last_notified TEXT NOT NULL DEFAULT '',
            updated_at    TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (tenant_id, printer_id, color)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS toner_alert_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id    TEXT NOT NULL,
            ts           TEXT NOT NULL,
            kind         TEXT NOT NULL,
            printer_id   TEXT NOT NULL DEFAULT '',
            printer_name TEXT NOT NULL DEFAULT '',
            color        TEXT NOT NULL DEFAULT '',
            level        INTEGER NOT NULL DEFAULT -1,
            severity     TEXT NOT NULL DEFAULT '',
            recipients   TEXT NOT NULL DEFAULT '',
            note         TEXT NOT NULL DEFAULT ''
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_talog_tenant_ts "
                  "ON toner_alert_log(tenant_id, ts DESC)")


# ─── Settings CRUD ────────────────────────────────────────────────────────────


DEFAULT_SETTINGS = {
    "enabled": 0,
    "threshold_warn": 20,
    "threshold_critical": 5,
    "hysteresis_percent": 10,
    "recipients": "",
    "check_interval_min": 60,
    "digest_mode": 0,
    "digest_hour_utc": 7,
    "quiet_hours_start": -1,
    "quiet_hours_end": -1,
    "lead_time_days": 0,
    "include_error_states": 1,
    "email_subject_template": "",
    "email_body_template": "",
}


# Default-Vorlagen fuer den Editor-Vorschlag. Kunden koennen sie in der UI
# uebernehmen und anpassen. Variablen werden per _render_template ersetzt.
DEFAULT_EMAIL_SUBJECT = (
    "{{ severity_icon }} MySecurePrint — {{ printer_name }}: "
    "{{ color_label }} {{ level }}%"
)
DEFAULT_EMAIL_BODY_HTML = """\
<div style="font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Arial, sans-serif;
            color: #111; max-width: 520px;">
  <p><span style="background:{{ severity_bg }};color:#fff;padding:2px 8px;
                   border-radius:10px;font-size:12px;font-weight:600;">
     {{ severity_label }}
  </span> &nbsp; Toner-Nachbestellung empfohlen.</p>
  <h2 style="margin:12px 0 4px 0;">{{ printer_name }}</h2>
  <p style="color:#666;margin:0 0 16px 0;">{{ location }}</p>
  <table style="border-collapse:collapse; font-size:14px;">
    <tr>
      <td style="padding:6px 12px 6px 0;color:#666;">Farbe:</td>
      <td><b>{{ color_label }}</b></td>
    </tr>
    <tr>
      <td style="padding:6px 12px 6px 0;color:#666;">Aktueller Stand:</td>
      <td>{{ level }}%</td>
    </tr>
    {{ days_left_row }}
  </table>
  <hr style="border:0;border-top:1px solid #eee;margin:20px 0;">
  <p style="color:#888;font-size:12px;">
    Diese Nachricht wurde automatisch von MySecurePrint versendet.
  </p>
</div>
"""


AVAILABLE_TEMPLATE_VARS = [
    ("printer_name",   "Name des Druckers"),
    ("location",       "Standort"),
    ("state",          "Drucker-Status (IDLE/PRINTING/OTHER)"),
    ("color",          "Toner-Farbe (black/cyan/magenta/yellow)"),
    ("color_label",    "Farbe lokalisiert (Schwarz/Cyan/Magenta/Gelb)"),
    ("level",          "Aktueller Prozent-Stand"),
    ("severity",       "Severity (critical/warn)"),
    ("severity_label", "Severity lokalisiert (Kritisch/Warnung)"),
    ("severity_icon",  "Icon: 🚨 fuer critical, ⚠️ fuer warn"),
    ("severity_bg",    "Hex-Farbe fuer Severity-Badge (#dc2626/#f59e0b)"),
    ("days_left",      "Geschaetzte Tage bis leer (leer wenn Prognose aus)"),
    ("days_left_row", "Vorgefertigte HTML-Zeile fuer Prognose, oder leer"),
]


def _render_template(text: str, context: dict) -> str:
    """Einfacher {{ var }}-Substitutor. Kein Logic, kein Escaping —
    Templates sind Admin-Content."""
    if not text:
        return text
    out = text
    for key, value in context.items():
        needle = "{{ " + key + " }}"
        out = out.replace(needle, str(value or ""))
        # Auch ohne Spaces akzeptieren
        out = out.replace("{{" + key + "}}", str(value or ""))
    return out


def get_settings(tenant_id: str) -> dict:
    init_schema()
    with _conn() as c:
        row = c.execute("SELECT * FROM toner_alert_settings WHERE tenant_id=?",
                        (tenant_id,)).fetchone()
    if row:
        d = dict(row)
    else:
        d = {**DEFAULT_SETTINGS, "tenant_id": tenant_id,
             "updated_at": "", "last_check_at": "", "last_digest_at": ""}
    return d


def upsert_settings(tenant_id: str, **fields) -> None:
    init_schema()
    now = _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with _conn() as c:
        existing = c.execute("SELECT tenant_id FROM toner_alert_settings "
                             "WHERE tenant_id=?", (tenant_id,)).fetchone()
        if existing:
            allowed = set(DEFAULT_SETTINGS.keys())
            fields = {k: v for k, v in fields.items() if k in allowed}
            if not fields:
                return
            cols = ", ".join(f"{k}=?" for k in fields)
            args = list(fields.values()) + [now, tenant_id]
            c.execute(f"UPDATE toner_alert_settings SET {cols}, updated_at=? "
                      f"WHERE tenant_id=?", args)
        else:
            merged = {**DEFAULT_SETTINGS, **{
                k: v for k, v in fields.items() if k in DEFAULT_SETTINGS}}
            merged["tenant_id"] = tenant_id
            merged["updated_at"] = now
            merged["last_check_at"] = ""
            merged["last_digest_at"] = ""
            cols = ", ".join(merged.keys())
            placeholders = ", ".join("?" for _ in merged)
            c.execute(f"INSERT INTO toner_alert_settings ({cols}) "
                      f"VALUES ({placeholders})", tuple(merged.values()))


def _mark_check(tenant_id: str, field: str) -> None:
    init_schema()
    now = _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with _conn() as c:
        c.execute(f"UPDATE toner_alert_settings SET {field}=? "
                  f"WHERE tenant_id=?", (now, tenant_id))


# ─── State + Log ──────────────────────────────────────────────────────────────


def get_state(tenant_id: str, printer_id: str, color: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("""SELECT * FROM toner_alert_state
                            WHERE tenant_id=? AND printer_id=? AND color=?""",
                        (tenant_id, printer_id, color)).fetchone()
    return dict(row) if row else None


def upsert_state(tenant_id: str, printer_id: str, color: str,
                 level: int, severity: str, armed: bool,
                 last_notified: Optional[str] = None) -> None:
    now = _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with _conn() as c:
        prev = c.execute("""SELECT last_notified FROM toner_alert_state
                             WHERE tenant_id=? AND printer_id=? AND color=?""",
                         (tenant_id, printer_id, color)).fetchone()
        ln = last_notified if last_notified is not None else (
            prev["last_notified"] if prev else "")
        c.execute("""INSERT INTO toner_alert_state
                       (tenant_id, printer_id, color, level, severity, armed,
                        last_notified, updated_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                     ON CONFLICT(tenant_id, printer_id, color) DO UPDATE SET
                       level=excluded.level,
                       severity=excluded.severity,
                       armed=excluded.armed,
                       last_notified=excluded.last_notified,
                       updated_at=excluded.updated_at""",
                  (tenant_id, printer_id, color, int(level), severity,
                   1 if armed else 0, ln, now))


def log_event(tenant_id: str, kind: str, **fields) -> None:
    now = _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    row = {
        "tenant_id": tenant_id, "ts": now, "kind": kind,
        "printer_id":   fields.get("printer_id", ""),
        "printer_name": fields.get("printer_name", ""),
        "color":        fields.get("color", ""),
        "level":        int(fields.get("level", -1)),
        "severity":     fields.get("severity", ""),
        "recipients":   fields.get("recipients", ""),
        "note":         fields.get("note", ""),
    }
    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    with _conn() as c:
        c.execute(f"INSERT INTO toner_alert_log ({cols}) VALUES ({placeholders})",
                  tuple(row.values()))


def recent_log(tenant_id: str, limit: int = 50) -> list[dict]:
    init_schema()
    with _conn() as c:
        rows = c.execute("""SELECT * FROM toner_alert_log
                             WHERE tenant_id=? ORDER BY ts DESC LIMIT ?""",
                         (tenant_id, int(limit))).fetchall()
    return [dict(r) for r in rows]


# ─── Threshold-Logik ──────────────────────────────────────────────────────────


def classify_severity(level: int, warn: int, critical: int,
                      days_left: Optional[float] = None,
                      lead_time_days: int = 0) -> str:
    """Gibt 'critical' / 'warn' / 'ok' zurück.

    Predictive: wenn days_left und lead_time_days > 0 und days_left <=
    lead_time_days → mindestens 'warn'. Wird noch von tatsächlichem Level
    überstimmt wenn der Prozentwert schon in critical ist.
    """
    if level <= critical:
        return "critical"
    if level <= warn:
        return "warn"
    if lead_time_days > 0 and days_left is not None and days_left <= lead_time_days:
        return "warn"
    return "ok"


def _parse_recipients(csv: str) -> list[str]:
    if not csv:
        return []
    out: list[str] = []
    for token in csv.replace(";", ",").split(","):
        t = token.strip()
        if t and "@" in t and t not in out:
            out.append(t)
    return out


def in_quiet_hours(cfg: dict, now_utc: Optional[_dt.datetime] = None) -> bool:
    start = int(cfg.get("quiet_hours_start", -1) or -1)
    end = int(cfg.get("quiet_hours_end", -1) or -1)
    if start < 0 or end < 0 or start == end:
        return False
    now = now_utc or _dt.datetime.utcnow()
    h = now.hour
    if start < end:
        return start <= h < end
    # Wrap-around (z.B. 22 → 6)
    return h >= start or h < end


# ─── Mail-Versand ─────────────────────────────────────────────────────────────


def _load_mail_config(tenant: dict) -> Optional[dict]:
    """Baut die Mail-Provider-Config nach der bekannten Server-Kaskade:
    tenant.mail_* > global_mail_* Setting > ENV. Optional Graph.
    """
    from db import get_setting, _dec

    api_key = (tenant.get("mail_api_key") or "").strip()
    mail_from = (tenant.get("mail_from") or "").strip()
    mail_from_name = (tenant.get("mail_from_name") or "MySecurePrint").strip()
    if not api_key:
        enc = get_setting("global_mail_api_key", "")
        if enc:
            try:
                api_key = _dec(enc)
            except Exception:
                api_key = ""
        mail_from = mail_from or (get_setting("global_mail_from", "") or "")
        mail_from_name = (get_setting("global_mail_from_name", "")
                          or mail_from_name)
    if not api_key:
        api_key = os.environ.get("RESEND_API_KEY", "")
        mail_from = mail_from or os.environ.get("RESEND_FROM", "")

    provider = (get_setting("mail_provider", "") or "resend").strip().lower()
    graph_tid = graph_cid = graph_csec = graph_sender = ""
    if provider == "graph":
        graph_tid = (get_setting("entra_tenant_id", "") or "").strip()
        graph_cid = (get_setting("entra_client_id", "") or "").strip()
        enc = get_setting("entra_client_secret", "")
        try:
            graph_csec = _dec(enc) if enc else ""
        except Exception:
            graph_csec = ""
        graph_sender = (get_setting("mail_graph_sender", "") or "").strip()
        if not (graph_tid and graph_cid and graph_csec and graph_sender):
            provider = "resend"

    if provider == "resend" and (not api_key or not mail_from):
        return None
    return {
        "provider": provider,
        "api_key": api_key,
        "mail_from": mail_from,
        "mail_from_name": mail_from_name,
        "graph_tenant_id": graph_tid,
        "graph_client_id": graph_cid,
        "graph_client_secret": graph_csec,
        "graph_sender_mailbox": graph_sender,
    }


def send_alert_mail(tenant: dict, recipients: list[str], subject: str,
                    html_body: str) -> tuple[bool, str]:
    cfg = _load_mail_config(tenant)
    if not cfg:
        return False, "no_mail_provider"
    try:
        from mail_client import send_mail
        send_mail(recipients=recipients, subject=subject, html_body=html_body,
                  provider=cfg["provider"], api_key=cfg["api_key"],
                  mail_from=cfg["mail_from"], mail_from_name=cfg["mail_from_name"],
                  graph_tenant_id=cfg["graph_tenant_id"],
                  graph_client_id=cfg["graph_client_id"],
                  graph_client_secret=cfg["graph_client_secret"],
                  graph_sender_mailbox=cfg["graph_sender_mailbox"])
        return True, "sent"
    except Exception as e:  # noqa: BLE001
        logger.warning("toner alert mail failed: %s", e)
        return False, str(e)[:200]


# ─── Email-Template ───────────────────────────────────────────────────────────


_COLOR_LABEL_DE = {"black": "Schwarz", "cyan": "Cyan",
                   "magenta": "Magenta", "yellow": "Gelb"}
_COLOR_HEX = {"black": "#222", "cyan": "#0088cc",
              "magenta": "#c6238a", "yellow": "#e0a800"}


def _severity_chip(sev: str) -> str:
    if sev == "critical":
        return ('<span style="background:#dc2626;color:#fff;padding:2px 8px;'
                'border-radius:10px;font-size:12px;font-weight:600;">Kritisch</span>')
    return ('<span style="background:#f59e0b;color:#fff;padding:2px 8px;'
            'border-radius:10px;font-size:12px;font-weight:600;">Warnung</span>')


def _bar(level: int, color: str) -> str:
    pct = max(0, min(100, level))
    hex_c = _COLOR_HEX.get(color, "#555")
    return (f'<div style="background:#eee;height:8px;border-radius:4px;'
            f'width:180px;overflow:hidden;">'
            f'<div style="background:{hex_c};height:100%;width:{pct}%;"></div>'
            f'</div>')


def render_alert_email(cfg: dict, item: dict) -> tuple[str, str]:
    """Rendert Subject + HTML fuer eine Alert-Mail.

    Nutzt Kunden-Template aus cfg falls gesetzt, sonst DEFAULT_EMAIL_*.
    item enthaelt printer_name, location, state, color, level, severity,
    days_left (Optional), error_code (Optional).
    """
    color = item.get("color", "black")
    level = int(item.get("level", 0))
    sev = item.get("severity", "warn")

    # Kontext fuer Substitution
    label = _COLOR_LABEL_DE.get(color, color.title())
    if color.startswith("_error_"):
        label = item.get("error_code", "").replace("_", " ").title()
    sev_label = "Kritisch" if sev == "critical" else "Warnung"
    sev_icon = "🚨" if sev == "critical" else "⚠️"
    sev_bg = "#dc2626" if sev == "critical" else "#f59e0b"
    d = item.get("days_left")
    days_left_str = str(int(round(d))) if d is not None else ""
    days_left_row = ""
    if d is not None:
        days_left_row = (
            f'<tr><td style="padding:6px 12px 6px 0;color:#666;">Prognose:</td>'
            f'<td>~{int(round(d))} Tage bis leer</td></tr>'
        )

    ctx = {
        "printer_name":   item.get("printer_name", ""),
        "location":       item.get("location", "") or "Standort unbekannt",
        "state":          item.get("state", ""),
        "color":          color,
        "color_label":    label,
        "level":          str(level),
        "severity":       sev,
        "severity_label": sev_label,
        "severity_icon":  sev_icon,
        "severity_bg":    sev_bg,
        "days_left":      days_left_str,
        "days_left_row":  days_left_row,
    }

    subj_tmpl = (cfg.get("email_subject_template") or "").strip()
    body_tmpl = (cfg.get("email_body_template") or "").strip()
    subject = _render_template(subj_tmpl or DEFAULT_EMAIL_SUBJECT, ctx)
    body    = _render_template(body_tmpl or DEFAULT_EMAIL_BODY_HTML, ctx)
    return subject, body


def build_single_alert_html(printer_name: str, location: str, color: str,
                            level: int, severity: str,
                            days_left: Optional[float]) -> str:
    label = _COLOR_LABEL_DE.get(color, color.title())
    days_txt = ""
    if days_left is not None:
        d = int(round(days_left))
        days_txt = (f'<p style="color:#666;font-size:14px;">Prognose: '
                    f'voraussichtlich in <b>~{d} Tag{"en" if d != 1 else ""}</b> '
                    f'leer.</p>')
    return f"""\
<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif;
            color:#111;max-width:520px;">
  <p>{_severity_chip(severity)} &nbsp; Toner-Nachbestellung empfohlen.</p>
  <h2 style="margin:12px 0 4px 0;">{printer_name}</h2>
  <p style="color:#666;margin:0 0 16px 0;">{location or "Standort unbekannt"}</p>
  <table style="border-collapse:collapse;font-size:14px;">
    <tr>
      <td style="padding:6px 12px 6px 0;color:#666;">Farbe:</td>
      <td><b>{label}</b></td>
    </tr>
    <tr>
      <td style="padding:6px 12px 6px 0;color:#666;">Aktueller Stand:</td>
      <td>{_bar(level, color)} <span style="margin-left:8px;">{level}%</span></td>
    </tr>
  </table>
  {days_txt}
  <hr style="border:0;border-top:1px solid #eee;margin:20px 0;">
  <p style="color:#888;font-size:12px;">
    Diese Nachricht wurde automatisch von MySecurePrint versendet, weil ein
    Toner-Level unter deiner Schwelle liegt. Empfänger + Schwellen kannst du
    im Admin-Bereich unter „Toner-Alerts" ändern.
  </p>
</div>"""


def build_digest_html(items: list[dict]) -> str:
    rows = []
    for it in items:
        label = _COLOR_LABEL_DE.get(it["color"], it["color"].title())
        d = it.get("days_left")
        d_txt = f"~{int(round(d))} Tage" if d is not None else "—"
        rows.append(f"""
    <tr>
      <td style="padding:8px 12px 8px 0;">{_severity_chip(it["severity"])}</td>
      <td style="padding:8px 12px 8px 0;"><b>{it["printer_name"]}</b><br>
        <span style="color:#666;font-size:12px;">{it.get("location","")}</span></td>
      <td style="padding:8px 12px 8px 0;">{label}</td>
      <td style="padding:8px 12px 8px 0;">{_bar(it["level"], it["color"])}
        <span style="margin-left:6px;">{it["level"]}%</span></td>
      <td style="padding:8px 0;">{d_txt}</td>
    </tr>""")
    return f"""\
<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif;
            color:#111;max-width:720px;">
  <h2 style="margin:0 0 4px 0;">Toner-Übersicht</h2>
  <p style="color:#666;">Folgende Drucker liegen aktuell unter deiner
     Nachbestell-Schwelle:</p>
  <table style="border-collapse:collapse;font-size:14px;margin-top:8px;">
    <thead><tr style="text-align:left;color:#666;font-size:12px;">
      <th style="padding-bottom:8px;">Status</th>
      <th style="padding-bottom:8px;">Drucker</th>
      <th style="padding-bottom:8px;">Farbe</th>
      <th style="padding-bottom:8px;">Stand</th>
      <th style="padding-bottom:8px;">Prognose</th>
    </tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  <hr style="border:0;border-top:1px solid #eee;margin:20px 0;">
  <p style="color:#888;font-size:12px;">
    Tages-Digest von MySecurePrint. Einstellungen unter „Toner-Alerts".
  </p>
</div>"""


# ─── Kern-Runner-Logik ────────────────────────────────────────────────────────


def evaluate_and_notify(tenant: dict, *, force_send: bool = False,
                        dry_run: bool = False) -> dict:
    """Prüft einen Tenant. Wenn `force_send`: schickt Test-Mail unabhängig
    von Cooldowns (für den "Testmail senden"-Button).

    Returns Report-Dict für UI.
    """
    from bi_client import fetch_all_printer_supplies, estimate_days_until_empty

    tenant_id = str(tenant.get("id") or tenant.get("user_id") or "")
    cfg = get_settings(tenant_id)
    report = {"tenant_id": tenant_id, "actions": [], "checked_printers": 0,
              "low_items": [], "cfg": cfg, "skipped_reason": ""}

    if not cfg.get("enabled") and not force_send:
        report["skipped_reason"] = "disabled"
        return report

    recipients = _parse_recipients(cfg.get("recipients", ""))
    if not recipients:
        report["skipped_reason"] = "no_recipients"
        return report

    printers = fetch_all_printer_supplies(tenant)
    if printers is None:
        report["skipped_reason"] = "bi_db_unavailable"
        return report
    report["checked_printers"] = len(printers)

    warn = int(cfg.get("threshold_warn", 20))
    crit = int(cfg.get("threshold_critical", 5))
    hyst = int(cfg.get("hysteresis_percent", 10))
    lead = int(cfg.get("lead_time_days", 0))
    include_errs = bool(cfg.get("include_error_states", 1))

    quiet = in_quiet_hours(cfg)
    digest_mode = bool(cfg.get("digest_mode", 0))

    now = _dt.datetime.utcnow()
    now_iso = now.isoformat(timespec="seconds") + "Z"

    # 1) Alle Low-Items einsammeln (unabhängig von Cooldown)
    low_items: list[dict] = []
    for p in printers:
        pid = p["printer_id"]
        for s in p.get("supplies", []):
            color = s["color"]
            level = int(s["level"])
            days_left = None
            if lead > 0:
                days_left = estimate_days_until_empty(tenant, pid, color, level)
            sev = classify_severity(level, warn, crit, days_left, lead)
            if sev == "ok":
                # State ggf. zurücksetzen wenn wieder oberhalb hyst-Schwelle
                st = get_state(tenant_id, pid, color)
                if st and st.get("armed", 1) == 0 and level >= warn + hyst:
                    upsert_state(tenant_id, pid, color, level, "ok", armed=True)
                continue
            low_items.append({
                "printer_id":   pid,
                "printer_name": p.get("printer_name") or pid[:8],
                "location":     p.get("location") or "",
                "color":        color,
                "level":        level,
                "severity":     sev,
                "days_left":    days_left,
            })
        # detected_error_states (LOW_TONER / NO_PAPER) fließt separat als "warn"
        if include_errs:
            for err in p.get("error_states", []):
                if err not in ("LOW_TONER", "NO_PAPER"):
                    continue
                low_items.append({
                    "printer_id":   p["printer_id"],
                    "printer_name": p.get("printer_name") or "",
                    "location":     p.get("location") or "",
                    "color":        "_error_" + err.lower(),
                    "level":        -1,
                    "severity":     "warn",
                    "days_left":    None,
                    "error_code":   err,
                })

    report["low_items"] = low_items

    if force_send:
        # Test-Mail: erster Low-Item, oder Dummy-Item wenn alles OK
        if low_items:
            it = low_items[0]
        else:
            it = {"printer_id": "test", "printer_name": "Test-Drucker",
                  "location": "Testlabor", "state": "IDLE",
                  "color": "black", "level": 42, "severity": "warn",
                  "days_left": 5.0}
        subj, html = render_alert_email(cfg, it)
        subj = "[TEST] " + subj
        ok, msg = send_alert_mail(tenant, recipients, subj, html)
        report["actions"].append({"kind": "test_mail",
                                  "ok": ok, "detail": msg,
                                  "recipients": recipients})
        log_event(tenant_id, "test_mail" if ok else "test_mail_failed",
                  recipients=", ".join(recipients), note=msg)
        return report

    _mark_check(tenant_id, "last_check_at")

    if quiet:
        # In Quiet-Hours nichts schicken, aber State aktualisieren (damit später
        # nicht "old" state auftaucht)
        for it in low_items:
            upsert_state(tenant_id, it["printer_id"], it["color"],
                         it["level"], it["severity"], armed=False)
        report["actions"].append({"kind": "quiet_hours"})
        return report

    # 2) Digest vs. Instant
    if digest_mode:
        # Einmal pro Tag um digest_hour_utc
        digest_hour = int(cfg.get("digest_hour_utc", 7))
        last = cfg.get("last_digest_at", "")
        already_today = False
        try:
            if last:
                last_dt = _dt.datetime.fromisoformat(last.rstrip("Z"))
                already_today = last_dt.date() == now.date()
        except Exception:
            pass
        if now.hour < digest_hour or already_today:
            # State aktualisieren, aber noch keine Mail
            for it in low_items:
                upsert_state(tenant_id, it["printer_id"], it["color"],
                             it["level"], it["severity"], armed=False)
            report["actions"].append({"kind": "digest_waiting"})
            return report
        if low_items:
            html = build_digest_html(low_items)
            ok, msg = send_alert_mail(
                tenant, recipients,
                f"MySecurePrint — Toner-Übersicht ({len(low_items)} Drucker)",
                html)
            report["actions"].append({"kind": "digest",
                                      "count": len(low_items),
                                      "ok": ok, "detail": msg,
                                      "recipients": recipients})
            log_event(tenant_id, "digest" if ok else "digest_failed",
                      recipients=", ".join(recipients),
                      note=f"{len(low_items)} items: {msg}")
        _mark_check(tenant_id, "last_digest_at")
        for it in low_items:
            upsert_state(tenant_id, it["printer_id"], it["color"],
                         it["level"], it["severity"], armed=False,
                         last_notified=now_iso)
        return report

    # 3) Instant-Mode: einzelne Mails mit Cooldown pro (printer, color)
    COOLDOWN_HOURS = 24
    for it in low_items:
        pid, color = it["printer_id"], it["color"]
        st = get_state(tenant_id, pid, color)
        armed = (st is None) or bool(st.get("armed", 1))
        last_notified = (st or {}).get("last_notified", "")
        needs_send = False
        if it["severity"] == "critical":
            # Critical überstimmt Cooldown teilweise: nur alle 6h wiederholen
            if _hours_since(last_notified) >= 6 or armed:
                needs_send = True
        else:
            if armed or _hours_since(last_notified) >= COOLDOWN_HOURS:
                needs_send = True
        if not needs_send:
            upsert_state(tenant_id, pid, color, it["level"], it["severity"],
                         armed=False)
            continue

        subj, html = render_alert_email(cfg, it)
        ok, msg = send_alert_mail(tenant, recipients, subj, html)
        report["actions"].append({"kind": "alert",
                                  "printer": it["printer_name"],
                                  "color": color, "level": it["level"],
                                  "severity": it["severity"],
                                  "ok": ok, "detail": msg,
                                  "recipients": recipients})
        log_event(tenant_id, "alert" if ok else "alert_failed",
                  printer_id=pid, printer_name=it["printer_name"],
                  color=color, level=it["level"], severity=it["severity"],
                  recipients=", ".join(recipients), note=msg)
        upsert_state(tenant_id, pid, color, it["level"], it["severity"],
                     armed=False,
                     last_notified=now_iso if ok else last_notified)

    return report


def _format_subject(item: dict) -> str:
    label = _COLOR_LABEL_DE.get(item["color"], item["color"].title())
    if item["color"].startswith("_error_"):
        code = item.get("error_code", "").replace("_", " ").title()
        return f"MySecurePrint — {item['printer_name']}: {code}"
    prefix = "🚨" if item["severity"] == "critical" else "⚠️"
    return (f"{prefix} MySecurePrint — {item['printer_name']}: "
            f"{label} {item['level']}%")


def _hours_since(iso_ts: str) -> float:
    if not iso_ts:
        return 1e9
    try:
        t = _dt.datetime.fromisoformat(iso_ts.rstrip("Z"))
    except ValueError:
        return 1e9
    return (_dt.datetime.utcnow() - t).total_seconds() / 3600.0


# ─── Background-Runner ────────────────────────────────────────────────────────


_DEFAULT_TICK = 60      # Sekunden zwischen Resource-Scans
_BOOT_DELAY = 90        # Boot-Delay


def start_runner():
    import asyncio
    loop = asyncio.get_event_loop()
    return loop.create_task(_runner_loop())


async def _runner_loop():
    import asyncio
    await asyncio.sleep(_BOOT_DELAY)
    logger.info("toner_alerts runner started")

    while True:
        try:
            await asyncio.to_thread(_tick_all_tenants)
        except Exception as e:  # noqa: BLE001
            logger.warning("toner_alerts tick failed: %s", e)
        await asyncio.sleep(_DEFAULT_TICK)


def _tick_all_tenants() -> None:
    """Iteriert alle Tenants und ruft für die fälligen evaluate_and_notify."""
    from db import _conn as db_conn
    now = _dt.datetime.utcnow()
    with db_conn() as c:
        rows = c.execute("SELECT user_id FROM tenants").fetchall()
    for row in rows:
        try:
            from db import get_tenant_full_by_user_id
            tenant = get_tenant_full_by_user_id(row["user_id"])
            if not tenant:
                continue
            tenant_id = str(tenant.get("id") or tenant.get("user_id"))
            cfg = get_settings(tenant_id)
            if not cfg.get("enabled"):
                continue
            interval = max(15, int(cfg.get("check_interval_min", 60)))
            last = cfg.get("last_check_at", "")
            if last:
                try:
                    t = _dt.datetime.fromisoformat(last.rstrip("Z"))
                    if (now - t).total_seconds() < interval * 60:
                        continue
                except Exception:
                    pass
            evaluate_and_notify(tenant)
        except Exception as e:  # noqa: BLE001
            logger.info("toner_alerts tick tenant %s failed: %s",
                        row.get("user_id"), e)
