"""Toner-Bestell-Tracking.

Loest das Problem "wurde fuer diesen Toner schon nachbestellt?" —
sonst wuerden Alarme jeden Tag rausgehen und mehrere Kollegen
denselben Toner nachbestellen.

Konzept:
- Eine "Bestellung" ist ein Datensatz pro (tenant, printer_id, color) mit
  status='ordered' (aktiv) oder 'installed' (abgeschlossen).
- Solange status='ordered' ist, unterdrueckt der Alert-Runner weitere
  Emails fuer genau dieses (printer, color).
- Auto-Reset: wenn der aktuelle Toner-Level wieder auf >= threshold_warn +
  hysteresis steigt (also der neue Toner eingesetzt wurde), wird die
  Bestellung automatisch auf 'installed' gesetzt und der Alarm ist wieder
  scharf.
- Manueller Cancel: Admin kann Bestellung stornieren, dann sind Alarme
  wieder scharf.

Frontend zeigt:
- Alert-Karte: "Bestellen"-Button oder — wenn schon bestellt — Badge
  mit "Am DD.MM. von XY".
- Optional: Bestell-Historie pro Drucker.
"""
from __future__ import annotations

import datetime as _dt
import logging
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)


def _db_path() -> str:
    from db import DB_PATH
    return DB_PATH


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path())
    c.row_factory = sqlite3.Row
    return c


def init_schema() -> None:
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS toner_orders (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id    TEXT NOT NULL,
            printer_id   TEXT NOT NULL,
            printer_name TEXT NOT NULL DEFAULT '',
            color        TEXT NOT NULL,
            ordered_at   TEXT NOT NULL,
            ordered_by   TEXT NOT NULL DEFAULT '',
            notes        TEXT NOT NULL DEFAULT '',
            status       TEXT NOT NULL DEFAULT 'ordered',
            installed_at TEXT NOT NULL DEFAULT '',
            level_at_order INTEGER NOT NULL DEFAULT -1
        )""")
        c.execute("""CREATE INDEX IF NOT EXISTS idx_orders_active
                       ON toner_orders(tenant_id, printer_id, color, status)""")
        c.execute("""CREATE INDEX IF NOT EXISTS idx_orders_ts
                       ON toner_orders(tenant_id, ordered_at DESC)""")


# ── CRUD ────────────────────────────────────────────────────────────

def get_active_order(tenant_id: str, printer_id: str, color: str) -> Optional[dict]:
    """Aktive (offene) Bestellung fuer (printer, color) oder None."""
    init_schema()
    with _conn() as c:
        row = c.execute("""SELECT * FROM toner_orders
                            WHERE tenant_id=? AND printer_id=? AND color=?
                              AND status='ordered'
                         ORDER BY ordered_at DESC LIMIT 1""",
                        (tenant_id, printer_id, color)).fetchone()
    return dict(row) if row else None


def get_active_orders_map(tenant_id: str) -> dict:
    """Alle aktiven Bestellungen als {(printer_id, color): order_dict}."""
    init_schema()
    with _conn() as c:
        rows = c.execute("""SELECT * FROM toner_orders
                             WHERE tenant_id=? AND status='ordered'""",
                         (tenant_id,)).fetchall()
    return {(r["printer_id"], r["color"]): dict(r) for r in rows}


def create_order(tenant_id: str, printer_id: str, printer_name: str,
                 color: str, ordered_by: str, notes: str = "",
                 level_at_order: int = -1) -> int:
    """Legt eine neue Bestellung an. Wenn es schon eine aktive gibt,
    wird sie durch die neue ueberschrieben (die alte wird auf 'installed'
    gesetzt — der Admin hat wohl vergessen)."""
    init_schema()
    now = _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with _conn() as c:
        # Existierende aktive Bestellung schliessen
        c.execute("""UPDATE toner_orders SET status='installed', installed_at=?
                     WHERE tenant_id=? AND printer_id=? AND color=?
                       AND status='ordered'""",
                  (now, tenant_id, printer_id, color))
        cur = c.execute("""INSERT INTO toner_orders
                             (tenant_id, printer_id, printer_name, color,
                              ordered_at, ordered_by, notes, status,
                              level_at_order)
                           VALUES (?, ?, ?, ?, ?, ?, ?, 'ordered', ?)""",
                        (tenant_id, printer_id, printer_name, color, now,
                         ordered_by[:200], notes[:500], int(level_at_order)))
        return cur.lastrowid


def cancel_order(order_id: int, tenant_id: str) -> bool:
    """Storniert eine Bestellung (Status → 'cancelled')."""
    init_schema()
    now = _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with _conn() as c:
        cur = c.execute("""UPDATE toner_orders
                              SET status='cancelled', installed_at=?
                            WHERE id=? AND tenant_id=? AND status='ordered'""",
                        (now, order_id, tenant_id))
        return cur.rowcount > 0


def mark_installed(tenant_id: str, printer_id: str, color: str) -> int:
    """Setzt alle aktiven Bestellungen fuer (printer, color) auf 'installed'.
    Wird vom Runner aufgerufen wenn der Level wieder oberhalb der
    Reset-Schwelle liegt. Returns Anzahl geschlossener Bestellungen."""
    init_schema()
    now = _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with _conn() as c:
        cur = c.execute("""UPDATE toner_orders SET status='installed',
                                  installed_at=?
                             WHERE tenant_id=? AND printer_id=? AND color=?
                               AND status='ordered'""",
                        (now, tenant_id, printer_id, color))
        return cur.rowcount


def list_orders(tenant_id: str, limit: int = 100,
                include_closed: bool = True) -> list[dict]:
    init_schema()
    q = "SELECT * FROM toner_orders WHERE tenant_id=?"
    args: list = [tenant_id]
    if not include_closed:
        q += " AND status='ordered'"
    q += " ORDER BY ordered_at DESC LIMIT ?"
    args.append(int(limit))
    with _conn() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]
