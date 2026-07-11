"""Mapping von Printix detected_error_states Codes zu i18n-Keys und
Kategorisierung Toner-vs-sonstiges.

Die Codes kommen 1:1 aus den SNMP-Meldungen der Drucker, die Printix in
`device_readings.detected_error_states` (JSON-Array) speichert. Ohne Mapping
zeigt die UI kryptische Codes wie "LOW_TONER" — mit Mapping ein sauberes
"Toner geht zur Neige".
"""
from __future__ import annotations

# Toner-relevante Error-Codes. Alles andere wird auf /admin/toner
# ausgeblendet — Papier-Meldungen, offene Klappen, Offline etc. gehoeren
# nicht auf die Toner-Alert-Seite.
TONER_ERROR_CODES = frozenset({
    "LOW_TONER",
    "TONER_LOW",
    "TONER_EMPTY",
    "MARKER_SUPPLY_LOW",
    "MARKER_SUPPLY_EMPTY",
    "MARKER_WASTE_ALMOST_FULL",
    "MARKER_WASTE_FULL",
})

# Vollstaendige i18n-Key-Map. UI ruft _(ERROR_LABEL_KEYS.get(code, "err_generic"))
ERROR_LABEL_KEYS = {
    "LOW_TONER":                "err_low_toner",
    "TONER_LOW":                "err_low_toner",
    "TONER_EMPTY":              "err_toner_empty",
    "MARKER_SUPPLY_LOW":        "err_low_toner",
    "MARKER_SUPPLY_EMPTY":      "err_toner_empty",
    "MARKER_WASTE_ALMOST_FULL": "err_waste_almost_full",
    "MARKER_WASTE_FULL":        "err_waste_full",
    "NO_PAPER":                 "err_no_paper",
    "PAPER_LOW":                "err_paper_low",
    "PAPER_JAM":                "err_paper_jam",
    "DOOR_OPEN":                "err_door_open",
    "COVER_OPEN":               "err_cover_open",
    "INPUT_TRAY_MISSING":       "err_tray_missing",
    "OUTPUT_AREA_ALMOST_FULL":  "err_output_almost_full",
    "OUTPUT_AREA_FULL":         "err_output_full",
    "OFFLINE":                  "err_offline",
    "SHUTDOWN":                 "err_shutdown",
    "WARMUP":                   "err_warmup",
    "OTHER":                    "err_other",
}


def toner_only(codes) -> list[str]:
    """Filtert eine Liste von Error-Codes auf die toner-relevanten."""
    if not codes:
        return []
    return [c for c in codes if c in TONER_ERROR_CODES]


def label_key(code: str) -> str:
    """i18n-Key fuer einen einzelnen Code (fallback: err_generic)."""
    return ERROR_LABEL_KEYS.get(code, "err_generic")
