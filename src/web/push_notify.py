"""Push notification relay client.

Fire-and-forget helper: call notify_user() from any sync or async context.
Pushes are delivered via the central MSP Push Relay server (holds APNs key).
"""
from __future__ import annotations
import asyncio
import logging
import secrets

logger = logging.getLogger("printix.push_notify")

DEFAULT_RELAY_URL = "https://msp-push-relay.azurewebsites.net"


# ── Public API ────────────────────────────────────────────────────────────────

def notify_user(
    user_id: str,
    title: str,
    body: str,
    extra: dict | None = None,
    collapse_id: str | None = None,
) -> None:
    """Fire-and-forget push. Safe to call from sync or async context."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_send(user_id, title, body, extra, collapse_id))
    except RuntimeError:
        asyncio.run(_send(user_id, title, body, extra, collapse_id))


async def auto_register(instance_url: str, relay_url: str | None = None) -> str:
    """Register this server with the relay → returns relay_token.

    Raises httpx.HTTPStatusError on relay-side errors.
    """
    import httpx
    url = (relay_url or DEFAULT_RELAY_URL).rstrip("/")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{url}/api/register",
            json={"instance_url": instance_url},
        )
        r.raise_for_status()
        return r.json()["relay_token"]


async def send_test_push(user_id: str, relay_url: str, relay_token: str) -> dict:
    """Send a test push to all tokens for user_id. Returns result dict."""
    try:
        from push_tokens import get_tokens_for_user
        tokens = get_tokens_for_user(user_id)
    except Exception as e:
        return {"ok": False, "error": f"Token lookup failed: {e}"}

    if not tokens:
        return {"ok": False, "error": "Keine Geräte-Token registriert"}

    import httpx
    url = relay_url.rstrip("/")
    errors: list[str] = []
    sent = 0
    async with httpx.AsyncClient(timeout=10) as client:
        for t in tokens:
            try:
                r = await client.post(
                    f"{url}/api/notify",
                    headers={"Authorization": f"Bearer {relay_token}"},
                    json={
                        "device_token": t["device_token"],
                        "title": "Test-Push",
                        "body": "Push-Benachrichtigungen funktionieren.",
                        "data": {"type": "test"},
                        "environment": t.get("environment", "production"),
                    },
                )
                if r.is_success:
                    sent += 1
                else:
                    errors.append(f"{t['device_token'][:8]}…: {r.status_code} {r.text[:80]}")
            except Exception as e:
                errors.append(str(e)[:80])

    if errors:
        return {"ok": sent > 0, "sent": sent, "errors": errors}
    return {"ok": True, "sent": sent}


# ── Internal ──────────────────────────────────────────────────────────────────

async def _send(
    user_id: str,
    title: str,
    body: str,
    extra: dict | None = None,
    collapse_id: str | None = None,
) -> None:
    try:
        from db import _get_setting as gs
        from push_tokens import get_tokens_for_user

        if gs("push_enabled", "0") != "1":
            return

        relay_url = gs("push_relay_url", DEFAULT_RELAY_URL).rstrip("/")
        relay_token = gs("push_relay_token", "")
        if not relay_token:
            logger.debug("push: relay_token not configured, skipping")
            return

        tokens = get_tokens_for_user(user_id)
        if not tokens:
            return

        import httpx
        payload_base = {
            "title": title,
            "body": body,
            "data": extra or {},
        }
        if collapse_id:
            payload_base["collapse_id"] = collapse_id

        async with httpx.AsyncClient(timeout=10) as client:
            for t in tokens:
                try:
                    r = await client.post(
                        f"{relay_url}/api/notify",
                        headers={"Authorization": f"Bearer {relay_token}"},
                        json={
                            "device_token": t["device_token"],
                            "environment": t.get("environment", "production"),
                            **payload_base,
                        },
                    )
                    if not r.is_success:
                        logger.warning(
                            "push relay %s → HTTP %s %s",
                            t["device_token"][:8], r.status_code, r.text[:100],
                        )
                except Exception as exc:
                    logger.warning("push relay send exception: %s", exc)
    except Exception as exc:
        logger.debug("push._send: %s", exc)
