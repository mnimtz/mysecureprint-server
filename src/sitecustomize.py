"""sitecustomize — Python lädt dieses Modul automatisch bei jedem Start
falls es auf sys.path liegt (hier: /app im Container).

WICHTIG (v0.6.4, S-4): Top-Level-Imports von `printix_client` wurden in
die Funktionen verschoben. Ein ImportError beim Top-Level würde JEDEN
Python-Prozess im Container blockieren (auch z.B. einfache CLI-Tools
oder Health-Checks), bevor irgend ein Logging-Setup greift. Die
Monkey-Patch-Installation (am Modul-Ende) ist defensiv in try/except
gekapselt, damit ein Boot-Time-Fehler im printix_client-Modul nicht den
ganzen Container killt.

S-11 (Empfehlung, NICHT ausgefuehrt): Diese Datei enthält reine App-
Logik (PrintixClient.search_card Monkey-Patch für Card-Number-Varianten)
und keine echte site-customization. Eine Umbenennung in z.B.
`card_transform.py` mit explizitem Import aus den Aufrufstellen wäre
sauberer als der implizite sitecustomize-Hook, ist aber riskant (Such-
und Diff-Aufwand quer durch's Repo) und wurde deshalb bewusst auf später
verschoben.
"""

import base64
import logging
from typing import Optional

_logger = logging.getLogger("printix.sitecustomize")


def _is_base64(s: str) -> bool:
    try:
        return base64.b64encode(base64.b64decode(s)).decode() == s
    except Exception:
        return False


def _b64_text(s: str) -> str:
    return base64.b64encode((s or '').encode('utf-8')).decode('ascii')


def _decode_b64(s: str) -> Optional[str]:
    try:
        return base64.b64decode(s).decode('utf-8')
    except Exception:
        return None


def _candidates(value: str) -> list[str]:
    raw = (value or '').strip()
    if not raw:
        return []

    out: list[str] = []

    def add(v: Optional[str]) -> None:
        if v and v not in out:
            out.append(v)

    add(raw)
    norm = raw.replace(' ', '').replace(':', '').replace('-', '')
    add(norm)

    stripped = norm.lstrip('0') or '0'
    add(stripped)
    add('0' + stripped)

    if _is_base64(raw):
        dec = _decode_b64(raw)
        add(dec)
        if dec:
            dnorm = dec.replace(' ', '').replace(':', '').replace('-', '')
            add(dnorm)
            add(dnorm.lstrip('0') or '0')
            add('0' + (dnorm.lstrip('0') or '0'))

    for item in list(out):
        add(_b64_text(item))

    return out


def _install_card_search_patch() -> None:
    """Installiert den search_card-Monkey-Patch lazy.

    Wird beim Modul-Ende aufgerufen — Fehler werden geloggt, nicht
    propagiert, damit ein kaputter printix_client-Import nicht jeden
    Python-Start im Container killt.
    """
    # Lazy-Import: printix_client darf bei Modul-Load nicht zwingend
    # verfügbar sein (siehe Dateikopf).
    from printix_client import PrintixClient, PrintixAPIError

    original_search_card = PrintixClient.search_card

    def _patched_search_card(self, card_id=None, card_number=None):
        if card_id:
            return original_search_card(self, card_id=card_id, card_number=None)
        if not card_number:
            raise ValueError('Either card_id or card_number must be provided.')

        tried: list[str] = []
        last_error = None

        for candidate in _candidates(card_number):
            tried.append(candidate)
            try:
                result = original_search_card(self, card_id=None, card_number=candidate)
                if isinstance(result, dict):
                    result.setdefault('_lookup', {
                        'input': card_number,
                        'matched_candidate': candidate,
                        'tried_candidates': tried,
                    })
                return result
            except PrintixAPIError as e:
                if getattr(e, 'status_code', None) == 404:
                    last_error = e
                    continue
                raise

        raise PrintixAPIError(
            404,
            f"Card not found for input '{card_number}'. Tried candidates: {', '.join(tried)}",
            getattr(last_error, 'error_id', ''),
        )

    PrintixClient.search_card = _patched_search_card


try:
    _install_card_search_patch()
except Exception as _e:  # pragma: no cover — defensive bootstrapping
    # Bewusst kein raise: sitecustomize wird bei JEDEM Python-Start
    # importiert. Eine Exception hier würde z.B. den Healthcheck oder
    # CLI-Aufrufe (python -c ...) im Container vorzeitig abbrechen.
    _logger.warning(
        "sitecustomize: search_card monkey-patch not installed: %s", _e
    )
