"""Async-Background-Runner fuer Guest-Print Polling.

Schlaeft die niedrigste `poll_interval_sec` aller aktiven Mailboxes und
ruft `poll_mailbox_once` fuer jede ausgereifte Mailbox auf. Resilient
gegen Einzel-Tick-Fehler — eine kaputte Mailbox blockt nicht den Rest.
"""
from __future__ import annotations

import asyncio
import logging
import time

from . import store
from .poller import poll_mailbox_once

logger = logging.getLogger(__name__)

_DEFAULT_TICK = 30   # Sekunden zwischen Resource-Scans
_BOOT_DELAY = 60     # Boot-Delay (db_init durch sein)


def start_runner(submit_print_job_fn=None):
    """Erzeugt einen asyncio-Task der die Polling-Schleife haelt. Wird
    typischerweise aus dem FastAPI startup-Hook gestartet.

    `submit_print_job_fn` muss thread-safe sein (wir rufen sie via
    asyncio.to_thread, da Printix-Submit blocking ist).
    """
    loop = asyncio.get_event_loop()
    task = loop.create_task(_runner(submit_print_job_fn))
    return task


async def _runner(submit_print_job_fn):
    await asyncio.sleep(_BOOT_DELAY)

    last_poll_at: dict[str, float] = {}

    while True:
        try:
            from db import get_setting
            if get_setting("guestprint_enabled", "0") != "1":
                await asyncio.sleep(_DEFAULT_TICK)
                continue
        except Exception:
            await asyncio.sleep(_DEFAULT_TICK)
            continue

        try:
            mboxes = store.list_mailboxes("")
        except Exception as e:
            logger.warning("Guest-Print: list_mailboxes failed: %s", e)
            await asyncio.sleep(_DEFAULT_TICK)
            continue

        for mb in mboxes:
            if not mb.get("enabled"):
                continue
            interval = max(15, int(mb.get("poll_interval_sec") or 60))
            # v0.7.28: DB-Lock statt prozesslokalem Cache — verhindert
            # Doppel-Polling bei Multi-Worker-Deploys (uvicorn --workers N).
            from . import store as _store
            if not _store.try_acquire_poll_lock(mb["id"], interval):
                continue
            try:
                # poller.poll_mailbox_once ist blockierend (sync requests) —
                # via to_thread off-loop.
                stats = await asyncio.to_thread(
                    poll_mailbox_once, mb["id"], submit_print_job_fn)
                if stats.get("printed") or stats.get("rejected") \
                        or stats.get("errors"):
                    logger.info("Guest-Print %s tick: %s", mb["upn"], stats)
            except Exception as e:
                logger.warning("Guest-Print poll %s failed: %s",
                                mb.get("upn"), e)
                try:
                    store.update_mailbox(mb["id"], last_error=str(e)[:300])
                except Exception:
                    pass

        await asyncio.sleep(_DEFAULT_TICK)
