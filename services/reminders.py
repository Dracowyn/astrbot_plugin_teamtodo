"""Due reminders and auto-archive (design 6 and 3, M2).

The scheduler only decides and records: which tasks to remind, where to send,
and which tasks to archive. Sending is injected by ``main.py`` so this module
uses nothing from AstrBot except the logger.

Reminders are marked before delivery, including stale and unbound tasks, so
failed sends are not retried. The 24-hour catch-up boundary is inclusive;
auto-archive requires a completion time strictly before the configured cutoff.
Automatic archive events use an empty session and no actor.
"""

from __future__ import annotations

import asyncio
import dataclasses as dcs
import time
from collections.abc import Awaitable, Callable

from astrbot.api import logger

from ..core.models import EventAction, Task, TaskEventDraft, TaskStatus
from ..core.store import Store

TICK_SECONDS = 60
CATCH_UP_SECONDS = 24 * 3600
"""Unreminded tasks due longer ago than this are marked silently."""
STARTUP_DELAY_SECONDS = 120
"""Wait before the first tick: AstrBot initializes plugins before platform
adapters, and reminders are marked before sending, so sending at once would
lose the catch-up reminders."""


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
        self._store = store
        self._lead_seconds = max(0, lead_seconds)
        self._archive_after_days = archive_after_days
        self._clock = clock

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
        reminders = []
        nicknames: dict[int, dict[str, str]] = {}
        with self._store.transaction():
            now = self._clock()
            tasks = self._store.list_unreminded_due(
                now + self._lead_seconds, (TaskStatus.TODO, TaskStatus.DOING)
            )
            for task in tasks:
                task = self._store.update_task(dcs.replace(task, reminded=True))
                if task.due_at < now - CATCH_UP_SECONDS:
                    continue
                bindings = self._store.list_space_bindings(task.space_id)
                if not bindings:
                    continue
                session = next(
                    (b.session for b in bindings if b.session == task.created_session),
                    bindings[0].session,
                )
                if task.space_id not in nicknames:
                    nicknames[task.space_id] = {
                        m.user.key: m.nickname
                        for m in self._store.list_members(task.space_id)
                        if m.nickname
                    }
                space = self._store.get_space(task.space_id)
                reminders.append(
                    Reminder(
                        session=session,
                        space_name=space.name,
                        task=task,
                        nicknames=nicknames[task.space_id],
                    )
                )
        return reminders

    def auto_archive(self) -> int:
        """Archive DONE tasks finished more than ``archive_after_days`` ago.

        In one transaction: status becomes ARCHIVED, ``done_at`` is kept, and
        an ``EventAction.ARCHIVE`` event is logged with ``actor=None``,
        ``session=""`` (the model default) and detail ``{"status": ["done", "archived"],
        "auto": True}``. Does nothing when auto-archive is disabled.

        Returns:
            Number of tasks archived.
        """
        if self._archive_after_days <= 0:
            return 0
        archived = 0
        with self._store.transaction():
            cutoff = self._clock() - self._archive_after_days * 86400
            for task in self._store.list_done_before(cutoff):
                if task.done_at >= cutoff:
                    continue
                self._store.update_task(dcs.replace(task, status=TaskStatus.ARCHIVED))
                self._store.add_event(
                    TaskEventDraft(
                        space_id=task.space_id,
                        task_seq=task.seq,
                        action=EventAction.ARCHIVE,
                        detail={"status": ["done", "archived"], "auto": True},
                        actor=None,
                        session="",
                    )
                )
                archived += 1
        return archived


async def run_loop(
    service: ReminderService,
    send: Callable[[Reminder], Awaitable[None]],
    *,
    interval: float = TICK_SECONDS,
    startup_delay: float = STARTUP_DELAY_SECONDS,
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
        interval: Seconds between ticks.
        startup_delay: Seconds before the first tick (skipped when <= 0).
        sleep: Awaitable sleep (``asyncio.sleep`` by default; tests inject).
    """
    if sleep is None:
        sleep = asyncio.sleep
    if startup_delay > 0:
        await sleep(startup_delay)
    while True:
        try:
            service.auto_archive()
            pending = service.collect_reminders()
            for reminder in pending:
                try:
                    await send(reminder)
                except Exception:
                    logger.exception("[teamtodo] Failed to send due reminder")
        except Exception:
            logger.exception("[teamtodo] Reminder tick failed")
        await sleep(interval)
