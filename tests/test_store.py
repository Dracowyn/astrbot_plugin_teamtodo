"""Behavioral persistence tests against real in-memory and file databases."""

import sqlite3
from dataclasses import replace

import pytest
from astrbot_plugin_teamtodo.core.models import (
    EventAction,
    Priority,
    SpaceRole,
    TaskDraft,
    TaskEventDraft,
    TaskStatus,
    UserRef,
)
from astrbot_plugin_teamtodo.core.store import Store

OWNER = UserRef(platform_id="adapter:one", user_id="owner:1")
OTHER = UserRef(platform_id="adapter", user_id="one:owner:1")
THIRD = UserRef(platform_id="z", user_id="3")
ALL = tuple(TaskStatus)


@pytest.fixture(params=["memory", "file"])
def store(request, tmp_path):
    now = [100.0]
    path = ":memory:" if request.param == "memory" else tmp_path / "tasks.sqlite3"
    database = Store(path, clock=lambda: now[0])
    yield database, now
    database.close()
    database.close()


def draft(space_id, **changes):
    return TaskDraft(
        **{
            "space_id": space_id,
            "title": "任务",
            "creator": OWNER,
            "created_session": "session",
            **changes,
        }
    )


def event_draft(space_id, seq=1, **changes):
    return TaskEventDraft(
        **{
            "space_id": space_id,
            "task_seq": seq,
            "action": EventAction.CREATE,
            "detail": {"标题": "原文", "values": [None, True, 2]},
            **changes,
        }
    )


WRITE_OPERATIONS = [
    lambda db, s, t: db.create_space("new", None),
    lambda db, s, t: db.rename_space(s, "new"),
    lambda db, s, t: db.set_space_owner(s, OTHER),
    lambda db, s, t: db.delete_space(s),
    lambda db, s, t: db.bind("new", s),
    lambda db, s, t: db.unbind("session", s),
    lambda db, s, t: db.set_session_default("session", s),
    lambda db, s, t: db.set_session_default("session", None),
    lambda db, s, t: db.set_current("session", OWNER, s),
    lambda db, s, t: db.set_current("session", OWNER, None),
    lambda db, s, t: db.upsert_member(s, OTHER, "other"),
    lambda db, s, t: db.set_member_role(s, OTHER, SpaceRole.ADMIN),
    lambda db, s, t: db.create_task(draft(s)),
    lambda db, s, t: db.update_task(t),
    lambda db, s, t: db.delete_task(s, t.seq),
    lambda db, s, t: db.add_event(event_draft(s)),
    lambda db, s, t: db.set_invite(s, "hash", 200),
    lambda db, s, t: db.clear_invite(s),
]


@pytest.mark.parametrize("operation", WRITE_OPERATIONS)
def test_every_write_requires_explicit_transaction(store, operation):
    db, _ = store
    with db.transaction():
        space = db.create_space("space", OWNER)
        db.upsert_member(space.id, OTHER, "Other")
        db.bind("session", space.id)
        task = db.create_task(draft(space.id))
    with pytest.raises(RuntimeError):
        operation(db, space.id, task)
    assert db.get_space(space.id) == space
    assert db.get_task(space.id, 1) == task


def test_rollback_is_atomic_and_nested_transactions_rejected(store):
    db, _ = store
    with pytest.raises(ZeroDivisionError):
        with db.transaction():
            space = db.create_space("rolled back", OWNER)
            db.bind("session", space.id)
            db.set_session_default("session", space.id)
            db.set_current("session", OWNER, space.id)
            db.create_task(draft(space.id, assignees=(OWNER,), tags=("tag",)))
            db.add_event(event_draft(space.id))
            db.set_invite(space.id, "rollback", 0)
            raise ZeroDivisionError
    assert db.list_spaces() == []
    assert db.list_bindings("session") == []
    assert db.get_session_default("session") is None
    assert db.get_current("session", OWNER) is None
    assert db.list_members(space.id) == []
    assert db.list_tasks(space.id, ALL) == []
    assert db.list_events(space.id) == []
    assert db.find_invite("rollback") is None
    with db.transaction():
        with pytest.raises(RuntimeError):
            with db.transaction():
                pytest.fail("nested transactions must not enter")
        kept = db.create_space("committed", None)
    assert db.get_space(kept.id) == kept
    with pytest.raises(KeyboardInterrupt):
        with db.transaction():
            db.rename_space(kept.id, "interrupted")
            raise KeyboardInterrupt
    assert db.get_space(kept.id) == kept


def test_spaces_members_ownership_and_missing_errors(store):
    db, now = store
    assert db.get_space(999) is None
    assert db.get_member(999, OWNER) is None
    with db.transaction():
        first = db.create_space("same", OWNER)
        second = db.create_space("same", None)
        owner = db.get_member(first.id, OWNER)
        assert owner.nickname == OWNER.user_id
        assert owner.role is SpaceRole.OWNER
        assert owner.joined_at == first.created_at == 100
        assert db.list_spaces() == [first, second]
        assert db.list_members(second.id) == []
        now[0] = 200
        renamed = db.rename_space(first.id, "renamed")
        assert renamed == replace(first, name="renamed")
        assert db.upsert_member(first.id, OWNER, "昵称").role is SpaceRole.OWNER
        assert db.upsert_member(first.id, OWNER, "").nickname == "昵称"
        other = db.upsert_member(first.id, OTHER, "Other")
        assert other.role is SpaceRole.MEMBER
        assert db.upsert_member(first.id, OTHER, "").joined_at == 200
        admin = db.set_member_role(first.id, OTHER, SpaceRole.ADMIN)
        assert db.upsert_member(first.id, OTHER, "New") == replace(
            admin, nickname="New"
        )
        assert (
            db.set_member_role(first.id, OTHER, SpaceRole.MEMBER).role
            is SpaceRole.MEMBER
        )
        with pytest.raises(ValueError):
            db.set_member_role(first.id, OTHER, SpaceRole.OWNER)
        with pytest.raises(ValueError):
            db.set_member_role(first.id, OWNER, SpaceRole.MEMBER)
        transferred = db.set_space_owner(first.id, OTHER)
        assert transferred.owner == OTHER
        assert db.get_member(first.id, OWNER).role is SpaceRole.ADMIN
        assert db.get_member(first.id, OTHER).role is SpaceRole.OWNER
        assert db.get_member(first.id, OTHER).nickname == "New"
        assert db.set_space_owner(first.id, OTHER) == transferred
        assert db.get_member(first.id, OTHER).role is SpaceRole.OWNER
        assert db.set_space_owner(second.id, THIRD).owner == THIRD
        assert db.get_member(second.id, THIRD).nickname == THIRD.user_id
        blank = db.upsert_member(second.id, OTHER, "")
        assert blank.nickname == ""
        assert db.upsert_member(second.id, OTHER, "later").nickname == "later"
        assert db.upsert_member(second.id, OTHER, "").nickname == "later"
        for action in (
            lambda: db.rename_space(999, "missing"),
            lambda: db.set_space_owner(999, OWNER),
            lambda: db.set_member_role(first.id, THIRD, SpaceRole.ADMIN),
        ):
            with pytest.raises(KeyError):
                action()
        db.delete_space(999)


def test_member_order_has_timestamp_key_and_lossless_identity_ties(store):
    db, now = store
    # OWNER and OTHER have identical display keys but different identities.
    assert OWNER.key == OTHER.key
    with db.transaction():
        space = db.create_space("space", None)
        now[0] = 200
        db.upsert_member(space.id, OWNER, "same")
        db.upsert_member(space.id, OTHER, "same")
        db.upsert_member(space.id, THIRD, "same")
        key_first = UserRef(platform_id="a:b", user_id="c")
        column_first = UserRef(platform_id="a", user_id="z")
        db.upsert_member(space.id, column_first, "column")
        db.upsert_member(space.id, key_first, "key")
        now[0] = 100
        early = UserRef(platform_id="zz", user_id="earliest")
        db.upsert_member(space.id, early, "early")
    assert [m.user for m in db.list_members(space.id)] == [
        early,
        key_first,
        column_first,
        OTHER,
        OWNER,
        THIRD,
    ]
    assert db.get_member(space.id, OWNER).user == OWNER
    assert db.get_member(space.id, OTHER).user == OTHER


def test_bindings_defaults_currents_order_and_session_specific_unbind(store):
    db, now = store
    with db.transaction():
        first = db.create_space("first", None)
        second = db.create_space("second", None)
        third = db.create_space("third", None)
        second_binding = db.bind("z", second.id)
        original = db.bind("z", first.id)
        db.bind("a", first.id)
        now[0] = 50
        early = db.bind("z", third.id)
        now[0] = 500
        assert db.bind("z", first.id) == original
        db.bind("b", first.id)
        assert db.list_bindings("z") == [early, original, second_binding]
        assert [b.session for b in db.list_space_bindings(first.id)] == ["a", "z", "b"]
        assert db.session_space_names("z") == {
            "third": third.id,
            "first": first.id,
            "second": second.id,
        }
        db.set_session_default("z", first.id)
        db.set_session_default("a", first.id)
        db.set_current("z", OWNER, first.id)
        db.set_current("z", OTHER, second.id)
        db.set_current("a", OWNER, first.id)
        db.unbind("z", first.id)
        assert db.get_session_default("z") is None
        assert db.get_current("z", OWNER) is None
        assert db.get_current("z", OTHER) == second.id
        assert db.get_session_default("a") == first.id
        assert db.get_current("a", OWNER) == first.id
        assert db.get_space(first.id) == first
        db.set_session_default("z", second.id)
        db.set_session_default("z", third.id)
        assert db.get_session_default("z") == third.id
        db.set_session_default("z", None)
        db.set_current("z", OTHER, third.id)
        assert db.get_current("z", OTHER) == third.id
        db.set_current("z", OTHER, None)
        db.unbind("missing", 999)
    assert db.get_session_default("z") is None
    assert db.get_current("z", OTHER) is None
    assert db.get_current("z", OWNER) is None
    assert db.list_bindings("missing") == []
    assert db.list_space_bindings(999) == []
    assert db.session_space_names("missing") == {}


def test_task_fields_ordered_dedup_updates_and_immutable_fields(store):
    db, now = store
    with db.transaction():
        space = db.create_space("space", None)
        task = db.create_task(
            draft(
                space.id,
                assignees=(OWNER, OTHER, OWNER),
                tags=("b", "a", "b"),
                due_at=150,
                priority=Priority.HIGH,
                description="<b>原文</b>\nnext",
            )
        )
        assert task.seq == 1
        assert task.status is TaskStatus.TODO
        assert task.reminded is False
        assert task.done_at is None
        assert task.created_at == task.updated_at == 100
        assert task.assignees == (OWNER, OTHER)
        assert task.tags == ("b", "a")
        assert task.creator == OWNER
        assert task.created_session == "session"
        assert task.priority is Priority.HIGH
        assert task.description == "<b>原文</b>\nnext"
        now[0] = 300
        changed = replace(
            task,
            title="changed",
            status=TaskStatus.ARCHIVED,
            assignees=(THIRD, OWNER, THIRD),
            due_at=280,
            tags=("x", "y", "x"),
            priority=Priority.LOW,
            description="new",
            done_at=250,
            reminded=True,
            space_id=999,
            seq=99,
            creator=THIRD,
            created_session="forged",
            created_at=1,
        )
        stored = db.update_task(changed)
        assert stored == replace(
            task,
            title="changed",
            status=TaskStatus.ARCHIVED,
            assignees=(THIRD, OWNER),
            due_at=280,
            tags=("x", "y"),
            priority=Priority.LOW,
            description="new",
            done_at=250,
            reminded=True,
            updated_at=300,
        )
        assert db.get_task(space.id, 1) == stored
        cleared = db.update_task(
            replace(
                stored,
                title="clear",
                status=TaskStatus.DOING,
                assignees=(),
                due_at=None,
                tags=(),
                priority=None,
                description="",
                done_at=None,
                reminded=False,
            )
        )
        assert cleared.assignees == cleared.tags == ()
        assert cleared.due_at is cleared.priority is cleared.done_at is None
        assert cleared.reminded is False
        assert cleared.status is TaskStatus.DOING
        assert db.list_tasks(space.id, ()) == []
        assert db.list_tasks(space.id, (TaskStatus.TODO,)) == []
        assert db.list_tasks(space.id, (TaskStatus.DOING,)) == [cleared]
        assert db.list_tasks_assigned_to(OWNER, ()) == []
        assert db.list_tasks_assigned_to(OWNER, ALL) == []
        with pytest.raises(KeyError):
            db.update_task(replace(task, id=999))
        db.delete_task(space.id, 1)
        db.delete_task(space.id, 1)
        assert db.get_task(space.id, 1) is None
        assert db.get_task(999, 1) is None
        assert db.create_task(draft(space.id)).seq == 2
        other_space = db.create_space("another", None)
        assert db.create_task(draft(other_space.id)).seq == 1


def test_task_list_order_status_filter_and_due_none_last(store):
    db, _ = store
    with db.transaction():
        a = db.create_space("a", None)
        b = db.create_space("b", None)
        late = db.create_task(draft(a.id, due_at=None, assignees=(OWNER, OTHER)))
        same_a = db.create_task(draft(a.id, due_at=200, assignees=(OWNER,)))
        same_seq = db.create_task(draft(a.id, due_at=200, assignees=(OWNER,)))
        same_b = db.create_task(draft(b.id, due_at=200, assignees=(OWNER,)))
        early = db.create_task(draft(b.id, due_at=100, assignees=(OWNER,)))
        archived = db.create_task(draft(b.id, due_at=0, assignees=(OWNER,)))
        db.update_task(replace(archived, status=TaskStatus.ARCHIVED))
        unassigned = db.create_task(draft(a.id, due_at=0, assignees=(OTHER,)))
    assert db.list_tasks(a.id, ALL) == [late, same_a, same_seq, unassigned]
    assert db.list_tasks_assigned_to(OWNER, (TaskStatus.TODO,)) == [
        early,
        same_a,
        same_seq,
        same_b,
        late,
    ]
    assert db.list_tasks_assigned_to(THIRD, ALL) == []
    assert db.list_tasks(999, ALL) == []


def test_events_survive_task_deletion_and_have_newest_tie_order(store):
    db, now = store
    with db.transaction():
        space = db.create_space("space", None)
        task = db.create_task(draft(space.id))
        first = db.add_event(event_draft(space.id, actor=OWNER, session="session"))
        second = db.add_event(event_draft(space.id, actor_label="管理员"))
        now[0] = 50
        older = db.add_event(event_draft(space.id, seq=2, action=EventAction.ARCHIVE))
        db.delete_task(space.id, task.seq)
        deletion = db.add_event(
            event_draft(
                space.id,
                action=EventAction.DELETE,
                detail={"snapshot": {"title": "任务"}},
            )
        )
    assert first.actor == OWNER
    assert first.detail == {"标题": "原文", "values": [None, True, 2]}
    assert first.session == "session"
    assert second.actor is None
    assert second.actor_label == "管理员"
    assert older.created_at == 50
    assert db.list_events(space.id) == [second, first, deletion, older]
    assert db.list_events(space.id, task_seq=1) == [second, first, deletion]
    assert db.list_events(space.id, limit=1) == [second]
    assert db.list_events(space.id, limit=0) == []
    assert db.list_events(space.id, task_seq=2, limit=1) == [older]


def test_invites_replace_expired_find_and_clear(store):
    db, _ = store
    with db.transaction():
        space = db.create_space("space", None)
        invite = db.set_invite(space.id, "old", 0)
        assert db.find_invite("old") == invite
        assert invite.expires_at == 0
        newer = db.set_invite(space.id, "new", 500)
        assert db.find_invite("old") is None
        assert db.find_invite("new") == newer
        assert newer.space_id == space.id
        db.clear_invite(space.id)
        db.clear_invite(space.id)
        db.clear_invite(999)
    assert db.find_invite("new") is None
    assert db.find_invite("unknown") is None


def test_space_delete_cascades_all_children_without_touching_other_space(store):
    db, _ = store
    with db.transaction():
        removed = db.create_space("removed", OWNER)
        kept = db.create_space("kept", OTHER)
        for space in (removed, kept):
            db.bind("shared", space.id)
            db.bind(str(space.id), space.id)
            db.set_session_default(str(space.id), space.id)
            db.set_current(str(space.id), OWNER, space.id)
            db.create_task(draft(space.id, assignees=(OWNER,), tags=("tag",)))
            db.add_event(event_draft(space.id))
            db.set_invite(space.id, str(space.id), 500)
        db.set_session_default("shared", removed.id)
        db.set_current("shared", OWNER, removed.id)
        db.set_current("shared", OTHER, kept.id)
        db.delete_space(removed.id)
    assert db.list_spaces() == [kept]
    assert db.list_bindings(str(removed.id)) == []
    assert [b.space_id for b in db.list_bindings("shared")] == [kept.id]
    assert db.list_space_bindings(removed.id) == []
    assert db.get_session_default(str(removed.id)) is None
    assert db.get_session_default("shared") is None
    assert db.get_current(str(removed.id), OWNER) is None
    assert db.get_current("shared", OWNER) is None
    assert db.get_current("shared", OTHER) == kept.id
    assert db.list_members(removed.id) == []
    assert db.list_tasks(removed.id, ALL) == []
    assert db.list_tasks_assigned_to(OWNER, ALL) == db.list_tasks(kept.id, ALL)
    assert len(db.list_tasks_assigned_to(OWNER, ALL)) == 1
    assert db.list_events(removed.id) == []
    assert db.find_invite(str(removed.id)) is None
    assert db.get_session_default(str(kept.id)) == kept.id
    assert db.get_current(str(kept.id), OWNER) == kept.id
    assert db.list_members(kept.id)[0].role is SpaceRole.OWNER
    assert len(db.list_events(kept.id)) == 1
    assert db.find_invite(str(kept.id)) is not None


def test_sqlite_configuration_persistence_and_schema_version(tmp_path):
    path = tmp_path / "persistent.sqlite3"
    db = Store(path, clock=lambda: 123.0)
    assert db._conn.isolation_level is None
    assert db._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == 1
    trace = []
    db._conn.set_trace_callback(trace.append)
    with db.transaction():
        space = db.create_space("persisted", OWNER)
        db.bind("session", space.id)
        db.set_session_default("session", space.id)
        db.set_current("session", OWNER, space.id)
        removed = db.create_task(draft(space.id))
        db.delete_task(space.id, removed.seq)
        task = db.create_task(draft(space.id, assignees=(OTHER,), tags=("中文",)))
        event = db.add_event(event_draft(space.id, seq=task.seq, actor=OTHER))
        invite = db.set_invite(space.id, "persisted hash", 1)
    assert "BEGIN IMMEDIATE" in trace
    assert "COMMIT" in trace
    with pytest.raises(ValueError):
        with db.transaction():
            db.rename_space(space.id, "rollback")
            raise ValueError
    assert "ROLLBACK" in trace
    db.close()
    db = Store(str(path), clock=lambda: 456.0)
    try:
        assert db.get_space(space.id) == space
        assert db.get_task(space.id, task.seq) == task
        assert db.list_events(space.id) == [event]
        assert db.find_invite("persisted hash") == invite
        assert db.get_session_default("session") == space.id
        assert db.get_current("session", OWNER) == space.id
        assert db.get_member(space.id, OWNER).role is SpaceRole.OWNER
        assert db.list_bindings("session")[0].bound_at == 123
        with db.transaction():
            assert db.create_task(draft(space.id)).seq == 3
        assert db._conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db._conn.execute("PRAGMA foreign_key_check").fetchall() == []
        with db.transaction(), pytest.raises(sqlite3.IntegrityError):
            db.create_task(draft(999))
    finally:
        db.close()


def test_rollback_restores_existing_task_children_and_sequence(store):
    db, _ = store
    with db.transaction():
        space = db.create_space("space", OWNER)
        original = db.create_task(draft(space.id, assignees=(OWNER,), tags=("old",)))
    with pytest.raises(ValueError):
        with db.transaction():
            db.update_task(replace(original, assignees=(OTHER,), tags=("new",)))
            db.create_task(draft(space.id, assignees=(THIRD,), tags=("partial",)))
            db.set_space_owner(space.id, OTHER)
            raise ValueError("abort")
    assert db.get_task(space.id, 1) == original
    assert db.list_tasks(space.id, ALL) == [original]
    assert db.get_space(space.id) == space
    assert db.get_member(space.id, OWNER).role is SpaceRole.OWNER
    assert db.get_member(space.id, OTHER) is None
    with db.transaction():
        assert db.create_task(draft(space.id)).seq == 2


def test_unreminded_due_filters_and_orders_across_spaces(store):
    db, _ = store
    statuses = (TaskStatus.TODO, TaskStatus.DOING)
    with db.transaction():
        a = db.create_space("a", None)
        b = db.create_space("b", None)
        same_b = db.create_task(draft(b.id, due_at=200))
        same_a = db.create_task(
            draft(a.id, due_at=200, assignees=(OWNER,), tags=("tag",))
        )
        same_seq = db.create_task(draft(a.id, due_at=200))
        early = db.create_task(draft(b.id, due_at=100))
        early = db.update_task(replace(early, status=TaskStatus.DOING))
        db.create_task(draft(a.id, due_at=201))
        db.create_task(draft(a.id))
        reminded = db.create_task(draft(a.id, due_at=50))
        db.update_task(replace(reminded, reminded=True))
        for status in (TaskStatus.DONE, TaskStatus.ARCHIVED):
            task = db.create_task(draft(a.id, due_at=50))
            db.update_task(replace(task, status=status))
        assert db.list_unreminded_due(200, statuses) == [
            early,
            same_a,
            same_seq,
            same_b,
        ]
    assert db.list_unreminded_due(200, statuses) == [early, same_a, same_seq, same_b]
    assert db.list_unreminded_due(99, statuses) == []
    assert db.list_unreminded_due(200, (TaskStatus.DOING,)) == [early]
    assert db.list_unreminded_due(200, ()) == []
    assert db.get_task(a.id, same_a.seq) == same_a


def test_done_before_filters_and_orders_across_spaces(store):
    db, _ = store
    with db.transaction():
        a = db.create_space("a", None)
        b = db.create_space("b", None)
        tasks = []
        for space_id, done_at in ((b.id, 200), (a.id, 200), (a.id, 200), (b.id, 100)):
            task = db.create_task(draft(space_id, assignees=(OWNER,), tags=("done",)))
            tasks.append(
                db.update_task(replace(task, status=TaskStatus.DONE, done_at=done_at))
            )
        for status, done_at in (
            (TaskStatus.DONE, 201),
            (TaskStatus.DONE, None),
            (TaskStatus.TODO, 50),
            (TaskStatus.DOING, 50),
            (TaskStatus.ARCHIVED, 50),
        ):
            task = db.create_task(draft(a.id))
            db.update_task(replace(task, status=status, done_at=done_at))
        expected = [tasks[3], tasks[1], tasks[2], tasks[0]]
        assert db.list_done_before(200) == expected
    assert db.list_done_before(200) == expected
    assert db.list_done_before(99) == []
