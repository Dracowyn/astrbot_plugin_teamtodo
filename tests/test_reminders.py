"""Reminder selection, transactional archiving and scheduler behavior."""

import asyncio
import dataclasses as dcs
from unittest.mock import Mock

import pytest
from astrbot_plugin_teamtodo.core.models import (
    EventAction,
    TaskDraft,
    TaskStatus,
    UserRef,
)
from astrbot_plugin_teamtodo.core.store import Store
from astrbot_plugin_teamtodo.services import reminders
from astrbot_plugin_teamtodo.services.reminders import (
    CATCH_UP_SECONDS,
    ReminderService,
    run_loop,
)

CREATOR = UserRef(platform_id="qq", user_id="creator")
GROUP = "qq:GroupMessage:1000"
OTHER = "qq:GroupMessage:2000"
DAY = 86400


@pytest.fixture
def clock():
    return Mock(return_value=1_800_000_000.0)


@pytest.fixture
def store(clock):
    database = Store(":memory:", clock=clock)
    yield database
    database.close()


@pytest.fixture
def space(store):
    with store.transaction():
        space = store.create_space("Team", None)
        store.bind(GROUP, space.id)
    return space


@pytest.fixture
def create_task(store, space, clock):
    def create(*, status=TaskStatus.TODO, done_at=None, reminded=False, **fields):
        with store.transaction():
            task = store.create_task(
                TaskDraft(
                    **{
                        "space_id": space.id,
                        "title": "Release",
                        "creator": CREATOR,
                        "created_session": GROUP,
                        "due_at": clock(),
                        **fields,
                    }
                )
            )
            return store.update_task(
                dcs.replace(task, status=status, done_at=done_at, reminded=reminded)
            )

    return create


@pytest.fixture
def service(store, clock):
    return ReminderService(store, lead_seconds=3600, archive_after_days=7, clock=clock)


def test_collect_lead_boundary_order_and_once(service, store, clock, create_task):
    edge = create_task(due_at=clock() + 3600)
    beyond = create_task(due_at=clock() + 3600.001)
    early = create_task(due_at=clock() - 1, status=TaskStatus.DOING)
    no_due = create_task(due_at=None)
    clock.return_value += 0.0001
    result = service.collect_reminders()
    assert [r.task.id for r in result] == [early.id, edge.id]
    for reminder in result:
        assert reminder.task == store.get_task(
            reminder.task.space_id, reminder.task.seq
        )
        assert reminder.task.reminded is True
        assert reminder.task.updated_at == clock()
        assert reminder.space_name == "Team"
    assert store.get_task(beyond.space_id, beyond.seq) == beyond
    assert store.get_task(no_due.space_id, no_due.seq) == no_due
    assert service.collect_reminders() == []


@pytest.mark.parametrize("lead", [0, -3600])
def test_collect_zero_and_negative_lead(store, clock, create_task, lead):
    service = ReminderService(
        store, lead_seconds=lead, archive_after_days=0, clock=clock
    )
    future = create_task(due_at=clock() + 0.001)
    due = create_task()
    assert [r.task.id for r in service.collect_reminders()] == [due.id]
    assert store.get_task(future.space_id, future.seq) == future
    clock.return_value += 0.001
    assert [r.task.id for r in service.collect_reminders()] == [future.id]


def test_collect_catch_up_boundary(service, store, clock, create_task):
    stale = create_task(due_at=clock() - CATCH_UP_SECONDS - 0.001)
    boundary = create_task(due_at=clock() - CATCH_UP_SECONDS)
    recent = create_task(due_at=clock() - CATCH_UP_SECONDS + 0.001)
    assert [r.task.id for r in service.collect_reminders()] == [boundary.id, recent.id]
    assert store.get_task(stale.space_id, stale.seq).reminded is True
    assert service.collect_reminders() == []


@pytest.mark.parametrize("still_bound", [True, False])
def test_collect_prefers_created_session_else_earliest_binding(
    service, store, space, clock, create_task, still_bound
):
    with store.transaction():
        store.unbind(GROUP, space.id)
        store.bind(OTHER, space.id)
        clock.return_value += 1
        store.bind("qq:GroupMessage:0000", space.id)
        clock.return_value += 1
        if still_bound:
            store.bind(GROUP, space.id)
    task = create_task()
    result = service.collect_reminders()
    assert [(r.task.id, r.session) for r in result] == [
        (task.id, GROUP if still_bound else OTHER)
    ]


def test_collect_no_binding_marks_without_sending(service, store, space, create_task):
    task = create_task()
    with store.transaction():
        store.unbind(GROUP, space.id)
    assert service.collect_reminders() == []
    assert store.get_task(space.id, task.seq).reminded is True
    with store.transaction():
        store.bind(OTHER, space.id)
    assert service.collect_reminders() == []


@pytest.mark.parametrize("status", [TaskStatus.DONE, TaskStatus.ARCHIVED])
def test_collect_skips_finished_tasks(service, store, create_task, status):
    task = create_task(status=status)
    assert service.collect_reminders() == []
    assert store.get_task(task.space_id, task.seq) == task


def test_collect_skips_already_reminded(service, store, create_task):
    task = create_task(reminded=True)
    assert service.collect_reminders() == []
    assert store.get_task(task.space_id, task.seq) == task


def test_collect_nicknames_are_space_specific_and_refreshed(
    service, store, space, create_task
):
    empty = UserRef(platform_id="qq", user_id="empty")
    with store.transaction():
        second_space = store.create_space("Other", None)
        store.bind(OTHER, second_space.id)
        store.upsert_member(space.id, CREATOR, "Alice")
        store.upsert_member(space.id, empty, "")
        store.upsert_member(second_space.id, CREATOR, "Bob")
    first = create_task()
    second = create_task()
    third = create_task(space_id=second_space.id)
    result = service.collect_reminders()
    assert [r.task.id for r in result] == [first.id, second.id, third.id]
    assert [r.nicknames for r in result] == [
        {CREATOR.key: "Alice"},
        {CREATOR.key: "Alice"},
        {CREATOR.key: "Bob"},
    ]
    with store.transaction():
        store.upsert_member(space.id, CREATOR, "Renamed")
    create_task()
    assert service.collect_reminders()[0].nicknames == {CREATOR.key: "Renamed"}


def test_collect_rolls_back_all_markers_on_failure(
    service, store, create_task, monkeypatch
):
    first = create_task()
    second = create_task()
    update = store.update_task

    def fail_second(task):
        if task.id == second.id:
            raise RuntimeError("write failed")
        return update(task)

    monkeypatch.setattr(store, "update_task", fail_second)
    with pytest.raises(RuntimeError, match="write failed"):
        service.collect_reminders()
    assert store.get_task(first.space_id, first.seq) == first
    assert store.get_task(second.space_id, second.seq) == second


@pytest.mark.parametrize("days", [0, -1])
def test_auto_archive_disabled(store, clock, create_task, days):
    task = create_task(status=TaskStatus.DONE, done_at=clock() - 100 * DAY)
    service = ReminderService(
        store, lead_seconds=0, archive_after_days=days, clock=clock
    )
    assert service.auto_archive() == 0
    assert store.get_task(task.space_id, task.seq) == task
    assert store.list_events(task.space_id) == []


def test_auto_archive_strict_boundary_keeps_done_at_and_logs(
    service, store, clock, create_task
):
    old = create_task(status=TaskStatus.DONE, done_at=clock() - 7 * DAY - 0.001)
    boundary = create_task(status=TaskStatus.DONE, done_at=clock() - 7 * DAY)
    recent = create_task(status=TaskStatus.DONE, done_at=clock() - 7 * DAY + 1)
    assert service.auto_archive() == 1
    assert store.get_task(old.space_id, old.seq) == dcs.replace(
        old, status=TaskStatus.ARCHIVED
    )
    assert store.get_task(boundary.space_id, boundary.seq) == boundary
    assert store.get_task(recent.space_id, recent.seq) == recent
    (event,) = store.list_events(old.space_id)
    assert event.task_seq == old.seq
    assert event.action is EventAction.ARCHIVE
    assert event.detail == {"status": ["done", "archived"], "auto": True}
    assert event.actor is None
    assert event.actor_label == ""
    assert event.session == ""
    assert event.created_at == clock()
    assert service.auto_archive() == 0
    clock.return_value += 0.001
    assert service.auto_archive() == 1
    assert store.get_task(boundary.space_id, boundary.seq).done_at == boundary.done_at


@pytest.mark.parametrize(
    "status", [TaskStatus.TODO, TaskStatus.DOING, TaskStatus.ARCHIVED, TaskStatus.DONE]
)
def test_auto_archive_leaves_other_statuses_and_missing_done_at(
    service, store, clock, create_task, status
):
    task = create_task(
        status=status, done_at=None if status is TaskStatus.DONE else clock() - 8 * DAY
    )
    assert service.auto_archive() == 0
    assert store.get_task(task.space_id, task.seq) == task
    assert store.list_events(task.space_id) == []


def test_auto_archive_rolls_back_status_if_event_fails(
    service, store, clock, create_task, monkeypatch
):
    task = create_task(status=TaskStatus.DONE, done_at=clock() - 8 * DAY)
    monkeypatch.setattr(
        store, "add_event", Mock(side_effect=RuntimeError("event failed"))
    )
    with pytest.raises(RuntimeError, match="event failed"):
        service.auto_archive()
    assert store.get_task(task.space_id, task.seq) == task
    assert store.list_events(task.space_id) == []


@pytest.mark.asyncio
async def test_loop_first_tick_immediate_send_failure_isolated(
    service, store, clock, create_task, monkeypatch
):
    done = create_task(status=TaskStatus.DONE, done_at=clock() - 8 * DAY)
    first = create_task()
    second = create_task()
    sent = []
    sleeps = []
    logger = Mock()
    monkeypatch.setattr(reminders, "logger", logger)

    async def send(reminder):
        assert sleeps == []
        assert store.get_task(done.space_id, done.seq).status is TaskStatus.ARCHIVED
        assert (
            store.get_task(reminder.task.space_id, reminder.task.seq).reminded is True
        )
        sent.append(reminder.task.id)
        if reminder.task.id == first.id:
            raise RuntimeError("send failed")

    async def sleep(interval):
        sleeps.append(interval)
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_loop(service, send, interval=12.5, startup_delay=0, sleep=sleep)
    assert sent == [first.id, second.id]
    assert sleeps == [12.5]
    assert logger.exception.call_count == 1
    assert logger.exception.call_args.args[0].startswith("[teamtodo]")
    assert service.collect_reminders() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_method", ["auto_archive", "collect_reminders"])
async def test_loop_tick_failure_does_not_stop_next_tick(
    service, create_task, monkeypatch, failure_method
):
    task = create_task()
    original = getattr(service, failure_method)
    calls = 0
    sleeps = []
    sent = []
    logger = Mock()
    monkeypatch.setattr(reminders, "logger", logger)

    def fail_first():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("tick failed")
        return original()

    async def send(reminder):
        sent.append(reminder.task.id)

    async def sleep(interval):
        sleeps.append(interval)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(service, failure_method, fail_first)
    with pytest.raises(asyncio.CancelledError):
        await run_loop(service, send, startup_delay=0, sleep=sleep)
    assert calls == 2
    assert sent == [task.id]
    assert sleeps == [60, 60]
    assert logger.exception.call_count == 1
    assert logger.exception.call_args.args[0].startswith("[teamtodo]")


@pytest.mark.asyncio
async def test_loop_send_cancellation_propagates(service, create_task):
    create_task()
    create_task()
    sent = []

    async def send(reminder):
        sent.append(reminder.task.id)
        raise asyncio.CancelledError

    async def sleep(interval):
        pytest.fail("Cancellation must not reach sleep")

    with pytest.raises(asyncio.CancelledError):
        await run_loop(service, send, startup_delay=0, sleep=sleep)
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_loop_waits_for_platforms_before_first_tick(
    service, store, create_task, monkeypatch
):
    task = create_task()
    sent = []
    sleeps = []

    async def send(reminder):
        sent.append(reminder.task.id)

    async def sleep(interval):
        sleeps.append(interval)
        if len(sleeps) == 2:
            raise asyncio.CancelledError
        # Nothing may be marked or sent before the startup delay is over.
        assert store.get_task(task.space_id, task.seq).reminded is False

    monkeypatch.setattr(asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await run_loop(service, send)
    assert sent == [task.id]
    assert sleeps == [reminders.STARTUP_DELAY_SECONDS, reminders.TICK_SECONDS]
