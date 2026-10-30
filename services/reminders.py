"""Due reminders and auto-archive (design 6 and 3, M2).

The scheduler only decides and records: which tasks to remind, where to send,
and which tasks to archive. Sending is injected by ``main.py`` so this module
uses nothing from AstrBot except the logger.
"""

from __future__ import annotations

import dataclasses as dcs
import time
from collections.abc import Awaitable, Callable

from ..core.models import Task
from ..core.store import Store

TICK_SECONDS = 60
CATCH_UP_SECONDS = 24 * 3600
"""Unreminded tasks due longer ago than this are marked silently."""


@dcs.dataclass(frozen=True)
class Reminder:
    """One reminder to send."""

    session: str
    """Target ``unified_msg_origin``."""
    space_name: str
    task: Task
    """The task as stored after being marked ``reminded``."""
    nicknames: dict[str, str]
    """Display names of the space's members (``UserRef.key`` -> nickname)."""


class ReminderService:
    """Decides reminders and auto-archives; all writes are transactional."""

    def __init__(
        self,
        store: Store,
        *,
        lead_seconds: float,
        archive_after_days: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Create the service.

        Args:
            store: Open store.
            lead_seconds: Remind this long before ``due_at`` (0 = at due time).
                Negative values are treated as 0.
            archive_after_days: Archive DONE tasks this many days after
                ``done_at``; 0 or negative disables auto-archive.
            clock: Wall-clock Unix seconds.
        """
        raise NotImplementedError

    def collect_reminders(self) -> list[Reminder]:
        """Mark due tasks as reminded and return the reminders to send.

        In one transaction, for every TODO/DOING task with ``reminded`` false
        and ``due_at - lead <= now``:

        - ``due_at < now - CATCH_UP_SECONDS``: mark reminded, no reminder;
        - otherwise pick the target session: ``created_session`` if it is
          still bound to the task's space, else the space's earliest binding
          (``Store.list_space_bindings`` order); with no binding mark reminded
          and return no reminder for it;
        - mark reminded and return a ``Reminder``.

        Returns:
            Reminders ordered by ``due_at``.
        """
        raise NotImplementedError

    def auto_archive(self) -> int:
        """Archive DONE tasks finished more than ``archive_after_days`` ago.

        In one transaction: status becomes ARCHIVED, ``done_at`` is kept, and
        an ``EventAction.ARCHIVE`` event is logged with ``actor=None``,
        ``session=None`` and detail ``{"status": ["done", "archived"],
        "auto": True}``. Does nothing when auto-archive is disabled.

        Returns:
            Number of tasks archived.
        """
        raise NotImplementedError


async def run_loop(
    service: ReminderService,
    send: Callable[[Reminder], Awaitable[None]],
    *,
    interval: float = TICK_SECONDS,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> None:
    """Run ticks forever until cancelled.

    Each tick runs ``auto_archive()``, then ``collect_reminders()`` and awaits
    ``send`` for each reminder. An exception from one ``send`` is logged and
    does not stop the other reminders; an exception from a whole tick is
    logged and the loop continues after ``interval``. ``CancelledError``
    propagates.

    Args:
        service: The reminder service.
        send: Delivers one reminder (``main.py`` builds the message chain and
            checks that the plugin is enabled for the session).
        interval: Seconds between ticks; the first tick runs immediately.
        sleep: Awaitable sleep (``asyncio.sleep`` by default; tests inject).
    """
    raise NotImplementedError
