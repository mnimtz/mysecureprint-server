"""
APNs Push-Notifications (v0.7.72)
==================================
Token-basierte Authentifizierung gegen Apples Push-Notification-Service.
HTTP/2 via httpx (+ h2 Backend).

Konfiguration (in DB-Settings):
  apns_key_id      — 10-stellige Key-ID aus Apple Developer → Certificates
  apns_team_id     — 10-stellige Team-ID aus Apple Developer → Membership
  apns_bundle_id   — Bundle-ID der iOS-App, z.B. de.nimtz.mysecureprint
  apns_private_key — Inhalt der .p8-Datei (PEM, beginnt mit -----BEGIN PRIVATE KEY-----)
  apns_sandbox     — "1" für Sandbox (Development), "0"/leer für Production

Der JWT wird für 59 Minuten gecacht und danach neuerstellt.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Optional

logger = logging.getLogger("printix.push_notify")

# ── JWT-Cache ─────────────────────────────────────────────────────────────────

_jwt_cache: dict = {}   # {"token": str, "expires_at": float}


def _make_jwt(private_key_pem: str, key_id: str, team_id: str) -> str:
    """Erstellt einen ES256-signierten JWT für APNs Token-Auth."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    header_b64 = base64.urlsafe_b64encode(
        json.dumps({"alg": "ES256", "kid": key_id}).encode()
    ).rstrip(b"=")
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps({"iss": team_id, "iat": int(time.time())}).encode()
    ).rstrip(b"=")
    signing_input = header_b64 + b"." + payload_b64

    key = load_pem_private_key(private_key_pem.encode(), password=None)
    der_sig = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))  # type: ignore[arg-type]
    r, s = decode_dss_signature(der_sig)
    raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    sig_b64 = base64.urlsafe_b64encode(raw_sig).rstrip(b"=")
    return (signing_input + b"." + sig_b64).decode()


def _get_jwt(key_id: str, team_id: str, private_key_pem: str) -> str:
    now = time.time()
    if _jwt_cache.get("expires_at", 0) > now + 60:
        return _jwt_cache["token"]
    token = _make_jwt(private_key_pem, key_id, team_id)
    _jwt_cache["token"] = token
    _jwt_cache["expires_at"] = now + 55 * 60   # 55 min < Apple's 60 min limit
    return token


# ── Einzel-Send ───────────────────────────────────────────────────────────────

def send_push(
    device_token: str,
    title: str,
    body: str,
    badge: Optional[int] = None,
    extra: Optional[dict] = None,
    collapse_id: Optional[str] = None,
) -> bool:
    """Sendet eine Push-Benachrichtigung an einen einzelnen Device-Token.

    Gibt True zurück wenn APNs 200 antwortet, False bei Fehler
    (inklusive ungültige Tokens — diese werden im Caller bereinigt).
    """
    try:
        from db import get_setting
        key_id   = get_setting("apns_key_id", "").strip()
        team_id  = get_setting("apns_team_id", "").strip()
        bundle_id = get_setting("apns_bundle_id", "de.nimtz.mysecureprint").strip()
        pem      = get_setting("apns_private_key", "").strip()
        sandbox  = get_setting("apns_sandbox", "0").strip() == "1"
    except Exception as e:
        logger.warning("push_notify: DB-Settings nicht lesbar: %s", e)
        return False

    if not all([key_id, team_id, bundle_id, pem]):
        logger.debug("push_notify: APNs nicht konfiguriert — überspringe")
        return False

    host = "api.sandbox.push.apple.com" if sandbox else "api.push.apple.com"
    url  = f"https://{host}/3/device/{device_token}"

    payload: dict = {
        "aps": {
            "alert": {"title": title, "body": body},
            "sound": "default",
        }
    }
    if badge is not None:
        payload["aps"]["badge"] = badge
    if extra:
        payload.update(extra)

    jwt_token = _get_jwt(key_id, team_id, pem)

    headers = {
        "authorization": f"bearer {jwt_token}",
        "apns-topic": bundle_id,
        "apns-push-type": "alert",
        "apns-expiration": "0",
    }
    if collapse_id:
        headers["apns-collapse-id"] = collapse_id[:64]

    try:
        import httpx
        with httpx.Client(http2=True, timeout=10) as client:
            resp = client.post(url, json=payload, headers=headers)
        if resp.status_code == 200:
            logger.info(
                "push_notify: OK token=...%s title=%r", device_token[-8:], title
            )
            return True
        reason = ""
        try:
            reason = resp.json().get("reason", "")
        except Exception:
            pass
        logger.warning(
            "push_notify: APNs %d %s token=...%s",
            resp.status_code, reason, device_token[-8:],
        )
        # 410 BadDeviceToken / Unregistered → Token löschen
        if resp.status_code in (400, 410) and reason in (
            "BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic",
        ):
            _remove_token(device_token)
        return False
    except Exception as e:
        logger.warning("push_notify: HTTP-Fehler: %s", e)
        return False


def _remove_token(device_token: str) -> None:
    try:
        from push_tokens import remove_push_token
        remove_push_token(device_token)
    except Exception as e:
        logger.debug("push_notify: Token-Bereinigung fehlgeschlagen: %s", e)


# ── Broadcast an User ─────────────────────────────────────────────────────────

def notify_user(
    user_id: str,
    title: str,
    body: str,
    badge: Optional[int] = None,
    extra: Optional[dict] = None,
    collapse_id: Optional[str] = None,
) -> int:
    """Sendet Push an alle registrierten Tokens eines Users.

    Gibt Anzahl erfolgreicher Sendungen zurück.
    """
    try:
        from push_tokens import get_push_tokens_for_user
        tokens = get_push_tokens_for_user(user_id)
    except Exception as e:
        logger.warning("push_notify: Token-Lookup fehlgeschlagen: %s", e)
        return 0

    if not tokens:
        return 0

    sent = 0
    for t in tokens:
        if send_push(t, title, body, badge=badge, extra=extra, collapse_id=collapse_id):
            sent += 1
    return sent
