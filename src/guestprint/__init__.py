"""Guest-Print / Email-to-Print Gateway.

Eine ueberwachte O365-Mailbox empfaengt Emails. Anhaenge (PDF, gaengige
Druckformate) werden ausgepackt und im Namen des Absenders gedruckt:

 - **Email-to-Print**: Wenn der Sender ein bekannter Server-User ist, wird
   der Anhang direkt in dessen Cloud-Print-Queue eingestellt.
 - **Guest-Print**: Externe Sender werden nur akzeptiert wenn ihre Email
   in `guestprint_guest` whitelisted ist. Der Admin trägt sie dort ein
   mit Default-Drucker + TTL.

Architektur:
 - `store.py`  — DB-CRUD (mailbox, guest-whitelist, jobs)
 - `poller.py` — Graph Mail.Read Polling + Verarbeitung einer Mailbox
 - `runner.py` — Async-Scheduler-Hook (asyncio-Loop, gemountet via FastAPI startup)
"""

from .store import (  # noqa: F401
    create_mailbox, get_mailbox, list_mailboxes, update_mailbox, delete_mailbox,
    add_guest, get_guest, list_guests, update_guest, delete_guest,
    is_email_whitelisted,
    record_job, list_jobs,
    try_acquire_poll_lock,
)
from .poller import poll_mailbox_once, list_mail_folders  # noqa: F401
from .runner import start_runner       # noqa: F401
