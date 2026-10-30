"""Dashboard service behavior, with real transactional storage."""

import dataclasses as dcs
from datetime import timezone

import pytest
from astrbot_plugin_teamtodo.core.models import (
    EventAction,
    GroupMember,
    Priority,
    SpaceEventAction,
    SpaceEventDraft,
    SpaceRole,
    TaskEvent,
    TaskEventDraft,
    TaskStatus,
    TodoError,
    UserRef,
)
from astrbot_plugin_teamtodo.core.store import Store
from astrbot_plugin_teamtodo.services.access import INVITE_TTL_SECONDS, hash_invite_code
from astrbot_plugin_teamtodo.services.admin_service import (
    AdminService,
    NotFound,
    TaskPatch,
)
from astrbot_plugin_teamtodo.services.share import ShareConflict, ShareService


@pytest.fixture
def env(tmp_path):
    now = [1000.0]
    store = Store(tmp_path / "admin.db", clock=lambda: now[0])
    owner = UserRef("chat", "owner")
    first = UserRef("chat", "a")
    second = UserRef("chat", "b")
    with store.transaction():
        space = store.create_space("team", owner)
        store.upsert_member(space.id, first, "Alice")
        store.upsert_member(space.id, second, "")
        store.bind("z", space.id)
        store.bind("a", space.id)
        store.set_session_title("a", "Alpha")
    service = AdminService(store, timezone.utc, clock=lambda: now[0])
    yield service, store, space, owner, first, second, now
    store.close()


def test_detail_and_summary(env):
    service, store, space, owner, first, second, now = env
    with store.transaction():
        store.set_member_role(space.id, second, SpaceRole.ADMIN)
        other = store.create_space("unbound", None)
    assert [row.space.id for row in service.list_spaces()] == [space.id, other.id]
    for status, due in [
        (TaskStatus.TODO, 999),
        (TaskStatus.DOING, 999),
        (TaskStatus.DONE, 999),
        (TaskStatus.ARCHIVED, 999),
        (TaskStatus.TODO, 1000),
        (TaskStatus.TODO, None),
    ]:
        task = service.create_task("admin", space.id, title="task", due_at=due).task
        service.update_task("admin", space.id, task.seq, TaskPatch(status=status))
    detail = service.get_space(space.id)
    assert detail.summary.binding_count == 2
    assert detail.summary.member_count == 3
    assert detail.summary.task_counts == {
        TaskStatus.TODO: 3,
        TaskStatus.DOING: 1,
        TaskStatus.DONE: 1,
        TaskStatus.ARCHIVED: 1,
    }
    assert detail.summary.overdue_count == 2
    assert [m.user for m in detail.members] == [owner, second, first]
    assert [(b.session, title) for b, title in detail.bindings] == [
        ("a", "Alpha"),
        ("z", None),
    ]
    assert detail.invite is None
    assert len(service.list_tasks(space.id, False).tasks) == 5
    assert len(service.list_tasks(space.id, True).tasks) == 6
    assert service.list_spaces()[1].task_counts == dict.fromkeys(TaskStatus, 0)


@pytest.mark.parametrize(
    "operation",
    [
        lambda s: s.get_space(999),
        lambda s: s.rename_space("u", 999, "new"),
        lambda s: s.delete_space("u", 999),
        lambda s: s.unbind("u", 999, "a"),
        lambda s: s.set_role("u", 999, UserRef("x", "x"), SpaceRole.MEMBER),
        lambda s: s.sync_members("u", 999, "chat:GroupMessage:g", []),
        lambda s: s.reset_invite("u", 999),
        lambda s: s.set_share("u", 999, "off"),
        lambda s: s.list_tasks(999, False),
        lambda s: s.get_task(999, 1),
        lambda s: s.create_task("u", 999, title="x"),
        lambda s: s.update_task("u", 999, 1, TaskPatch()),
        lambda s: s.delete_task("u", 999, 1),
        lambda s: s.list_events(999),
    ],
)
def test_missing_spaces(env, operation):
    with pytest.raises(NotFound):
        operation(env[0])


def test_missing_children(env):
    service, _, space, *_ = env
    for operation in [
        lambda: service.get_task(space.id, 99),
        lambda: service.update_task("u", space.id, 99, TaskPatch()),
        lambda: service.delete_task("u", space.id, 99),
        lambda: service.unbind("u", space.id, "missing"),
        lambda: service.set_role("u", space.id, UserRef("x", "x"), SpaceRole.ADMIN),
    ]:
        with pytest.raises(NotFound):
            operation()


def test_rename_and_unbind(env):
    service, store, space, owner, *_ = env
    with store.transaction():
        other = store.create_space("taken", None)
        store.bind("z", other.id)
        store.set_session_default("a", space.id)
        store.set_current("a", owner, space.id)
        store.set_current("z", owner, space.id)
    with pytest.raises(TodoError):
        service.rename_space("admin", space.id, "taken")
    for name in ["", "bad name", "bad#name", "x" * 1000]:
        with pytest.raises(TodoError):
            service.rename_space("admin", space.id, name)
    assert service.rename_space("admin", space.id, " renamed ").name == "renamed"
    service.unbind("admin", space.id, "a")
    assert store.get_session_default("a") is None
    assert store.get_current("a", owner) is None
    assert store.get_current("z", owner) == space.id
    assert store.get_session_titles(["a"]) == {"a": "Alpha"}
    events, _ = service.list_events(space.id)
    assert events[0].action == SpaceEventAction.UNBIND
    assert events[0].detail == {"session": "a"}
    assert events[1].detail == {"name": ["team", "renamed"]}
    assert all(
        e.actor is None and e.actor_label == "admin" and e.session == "" for e in events
    )


def test_roles_and_invites(env):
    service, store, space, owner, first, _, now = env
    for user, role in [
        (owner, SpaceRole.MEMBER),
        (owner, SpaceRole.OWNER),
        (first, SpaceRole.OWNER),
    ]:
        with pytest.raises(TodoError):
            service.set_role("admin", space.id, user, role)
    assert (
        service.set_role("admin", space.id, first, SpaceRole.MEMBER).role
        == SpaceRole.MEMBER
    )
    assert service.list_events(space.id)[0] == []
    assert (
        service.set_role("admin", space.id, first, SpaceRole.ADMIN).role
        == SpaceRole.ADMIN
    )
    service.set_role("admin", space.id, first, SpaceRole.ADMIN)
    assert len(service.list_events(space.id)[0]) == 1
    event = service.list_events(space.id)[0][0]
    assert event.detail == {"user": first.key, "role": ["member", "admin"]}
    code, invite = service.reset_invite("admin", space.id)
    assert invite.expires_at == now[0] + INVITE_TTL_SECONDS
    assert service.get_space(space.id).invite == invite
    code2, invite2 = service.reset_invite("admin", space.id)
    assert code2 != code
    assert store.find_invite(hash_invite_code(code)) is None
    assert store.find_invite(hash_invite_code(code2)) == invite2
    now[0] = invite2.expires_at
    assert service.get_space(space.id).invite is None


def test_sync_members_refreshes_without_removing_or_changing_roles(env):
    service, store, space, owner, first, second, now = env
    session = "chat:GroupMessage:g"
    newcomer = UserRef("chat", "new")
    with store.transaction():
        store.bind(session, space.id)
        store.set_member_role(space.id, first, SpaceRole.ADMIN)
    before = {m.user: m for m in store.list_members(space.id)}
    now[0] += 20
    added = service.sync_members(
        "dashboard",
        space.id,
        session,
        [
            GroupMember(owner, "Owner refreshed", ()),
            GroupMember(first, "Admin refreshed", ()),
            GroupMember(newcomer, "New", ()),
            GroupMember(newcomer, "New refreshed", ()),
        ],
    )
    assert added == 1
    for user, nickname in [(owner, "Owner refreshed"), (first, "Admin refreshed")]:
        member = store.get_member(space.id, user)
        assert member == dcs.replace(before[user], nickname=nickname)
    assert store.get_member(space.id, second) == before[second]
    member = store.get_member(space.id, newcomer)
    assert member.role == SpaceRole.MEMBER
    assert member.nickname == "New refreshed"
    assert member.joined_at == now[0]
    assert len(store.list_members(space.id)) == 4
    events = store.list_space_events(space.id)
    assert len(events) == 1
    event = events[0]
    assert event.action == SpaceEventAction.SYNC
    assert event.detail == {"session": session, "added": 1}
    assert event.actor is None and event.actor_label == "dashboard"
    assert event.session == ""


@pytest.mark.parametrize("existing_only", [False, True])
def test_sync_members_logs_zero_additions(env, existing_only):
    service, store, space, owner, *_ = env
    session = "chat:GroupMessage:g"
    with store.transaction():
        store.bind(session, space.id)
    before = store.list_members(space.id)
    members = [GroupMember(owner, "", ())] if existing_only else []
    assert service.sync_members("dashboard", space.id, session, members) == 0
    assert store.list_members(space.id) == before
    events = store.list_space_events(space.id)
    assert len(events) == 1
    event = events[0]
    assert event.action == SpaceEventAction.SYNC
    assert event.detail == {"session": session, "added": 0}
    assert event.actor is None and event.actor_label == "dashboard"
    assert event.session == ""


def test_sync_members_requires_session_bound_to_target_space(env):
    service, store, space, *_ = env
    session = "chat:GroupMessage:other"
    with store.transaction():
        other = store.create_space("other", None)
        store.bind(session, other.id)
    before = store.list_members(space.id)
    with pytest.raises(NotFound, match="会话未绑定到此空间"):
        service.sync_members("dashboard", space.id, session, [])
    assert store.list_members(space.id) == before
    assert store.list_space_events(space.id) == []


def test_sync_members_platform_mismatch_rolls_back_prior_upsert(env):
    service, store, space, *_ = env
    session = "chat:GroupMessage:g"
    valid = UserRef("chat", "new")
    invalid = UserRef("different", "new")
    with store.transaction():
        store.bind(session, space.id)
    before = store.list_members(space.id)
    with pytest.raises(TodoError, match="平台"):
        service.sync_members(
            "dashboard",
            space.id,
            session,
            [GroupMember(valid, "Valid", ()), GroupMember(invalid, "Invalid", ())],
        )
    assert store.list_members(space.id) == before
    assert store.get_member(space.id, valid) is None
    assert store.get_member(space.id, invalid) is None
    assert store.list_space_events(space.id) == []


def test_sync_members_log_failure_rolls_back_members_and_log(env, monkeypatch):
    service, store, space, *_ = env
    session = "chat:GroupMessage:g"
    newcomer = UserRef("chat", "new")
    with store.transaction():
        store.bind(session, space.id)
    before = store.list_members(space.id)
    add_event = store.add_space_event

    def fail_after_log(draft):
        add_event(draft)
        raise RuntimeError("log failure")

    monkeypatch.setattr(store, "add_space_event", fail_after_log)
    with pytest.raises(RuntimeError, match="log failure"):
        service.sync_members(
            "dashboard", space.id, session, [GroupMember(newcomer, "New", ())]
        )
    assert store.list_members(space.id) == before
    assert store.get_member(space.id, newcomer) is None
    assert store.list_space_events(space.id) == []


def test_task_replacement_status_and_identity(env):
    service, store, space, _, first, second, now = env
    created = service.create_task(
        "dashboard",
        space.id,
        title="Initial",
        assignees=(first, second),
        due_at=1200,
        tags=("a", "b"),
        priority=Priority.HIGH,
        description=" text ",
    )
    task = created.task
    assert task.creator == UserRef("webui", "dashboard")
    assert task.created_session == ""
    assert task.description == "text"
    assert created.added_assignees == (first, second)
    assert store.get_member(space.id, task.creator) is None
    assert created.nicknames == {"chat:owner": "owner", first.key: "Alice"}
    assert service.get_task(space.id, task.seq).task == task
    assert (
        service.update_task("dashboard", space.id, task.seq, TaskPatch()).task == task
    )
    with store.transaction():
        store.update_task(dcs.replace(task, reminded=True))
    updated = service.update_task(
        "dashboard",
        space.id,
        task.seq,
        TaskPatch(
            title="Changed",
            description=" new ",
            status=TaskStatus.DONE,
            assignees=(second, first),
            tags=("b", "a"),
            clear_due=True,
            due_at=1300,
            clear_priority=True,
            priority=Priority.LOW,
        ),
    ).task
    assert updated.assignees == (second, first)
    assert updated.tags == ("b", "a")
    assert updated.due_at is None and updated.priority is None and not updated.reminded
    assert updated.description == "new" and updated.done_at == now[0]
    assert (
        service.update_task(
            "dashboard", space.id, task.seq, TaskPatch(status=TaskStatus.ARCHIVED)
        ).task.done_at
        == now[0]
    )
    now[0] += 10
    reopened = service.update_task(
        "dashboard",
        space.id,
        task.seq,
        TaskPatch(status=TaskStatus.DOING, assignees=(), tags=(), description=""),
    ).task
    assert reopened.done_at is None and reopened.assignees == () and reopened.tags == ()
    events, _ = service.list_events(space.id)
    assert {e.action for e in events} >= {
        EventAction.CREATE,
        EventAction.UPDATE,
        EventAction.ASSIGN,
        EventAction.STATUS,
        EventAction.ARCHIVE,
    }
    assert all(
        e.actor is None and e.actor_label == "dashboard" and e.session == ""
        for e in events
    )
    deleted = service.delete_task("dashboard", space.id, task.seq)
    assert deleted.task == reopened
    assert store.get_task(space.id, task.seq) is None
    snapshot = service.list_events(space.id)[0][0].detail["snapshot"]
    assert snapshot["title"] == "Changed" and snapshot["seq"] == task.seq


@pytest.mark.parametrize(
    "fields",
    [
        {"title": " "},
        {"title": "x" * 1000},
        {"description": "x" * 20000},
        {"tags": ("x" * 1000,)},
        {"tags": tuple(str(i) for i in range(100))},
        {"assignees": (UserRef("missing", "user"),)},
    ],
)
def test_task_validation_is_atomic(env, fields):
    service, _, space, *_ = env
    args = {"title": "valid", **fields}
    with pytest.raises(TodoError):
        service.create_task("admin", space.id, **args)
    task = service.create_task("admin", space.id, title="valid").task
    before = service.list_events(space.id)[0]
    with pytest.raises(TodoError):
        service.update_task("admin", space.id, task.seq, TaskPatch(**fields))
    assert service.get_task(space.id, task.seq).task == task
    assert service.list_events(space.id)[0] == before


def test_log_order_filter_and_cap(env):
    service, store, space, *_ = env
    with store.transaction():
        for i in range(220):
            store.add_space_event(
                SpaceEventDraft(space.id, SpaceEventAction.INVITE, {"i": i})
            )
            store.add_event(TaskEventDraft(space.id, 1, EventAction.UPDATE, {"i": i}))
    events, names = service.list_events(space.id, 1000)
    assert len(events) == 200
    assert isinstance(events[0], TaskEvent) and not isinstance(events[1], TaskEvent)
    assert [e.id for e in events[:4]] == [220, 220, 219, 219]
    assert names["chat:a"] == "Alice" and "chat:b" not in names
    assert len(service.list_events(space.id)[0]) == 50
    filtered = service.list_events(space.id, 1000, task_seq=1)[0]
    assert len(filtered) == 200 and all(isinstance(e, TaskEvent) for e in filtered)
    assert service.list_events(space.id, task_seq=999)[0] == []


def test_delete_space_cascades(env):
    service, store, space, owner, *_ = env
    service.create_task("admin", space.id, title="task")
    code, _ = service.reset_invite("admin", space.id)
    with store.transaction():
        store.set_session_default("a", space.id)
        store.set_current("a", owner, space.id)
    service.delete_space("admin", space.id)
    assert store.get_space(space.id) is None
    assert store.list_space_bindings(space.id) == []
    assert store.list_members(space.id) == []
    assert store.list_tasks(space.id, tuple(TaskStatus)) == []
    assert store.list_events(space.id) == [] and store.list_space_events(space.id) == []
    assert store.find_invite(hash_invite_code(code)) is None
    assert (
        store.get_session_default("a") is None and store.get_current("a", owner) is None
    )
    assert store.get_session_titles(["a"]) == {"a": "Alpha"}


def test_noop_rename_and_task_due_changes(env):
    service, store, space, _, first, second, now = env
    assert service.now() == now[0]
    assert service.rename_space("admin", space.id, "team") == space
    assert service.list_events(space.id)[0] == []
    task = service.create_task("admin", space.id, title="task", assignees=(first,)).task
    changed = service.update_task(
        "admin",
        space.id,
        task.seq,
        TaskPatch(due_at=2000, priority=Priority.LOW, assignees=(second,)),
    ).task
    assert changed.due_at == 2000 and changed.priority == Priority.LOW
    with store.transaction():
        changed = store.update_task(dcs.replace(changed, reminded=True))
    unchanged = service.update_task(
        "admin",
        space.id,
        task.seq,
        TaskPatch(due_at=2000, priority=Priority.LOW, assignees=(second,)),
    ).task
    assert unchanged == changed and unchanged.reminded
    now[0] += 1
    service.rename_space("admin", space.id, "new")
    assert service.list_events(space.id)[0][0].action == SpaceEventAction.RENAME
    assert service.list_events(space.id, 0)[0] == []


def test_assignee_limit_for_existing_members(env):
    service, store, space, *_ = env
    users = tuple(UserRef("chat", str(i)) for i in range(21))
    with store.transaction():
        for user in users:
            store.upsert_member(space.id, user, user.user_id)
    with pytest.raises(TodoError):
        service.create_task("admin", space.id, title="task", assignees=users)
    task = service.create_task("admin", space.id, title="task").task
    with pytest.raises(TodoError):
        service.update_task("admin", space.id, task.seq, TaskPatch(assignees=users))


def share_admin(store, now, *, enabled=True, base_url="https://todo.example/root/"):
    """Build an administrator service with configured shared-link rules."""
    shares = ShareService(
        store,
        timezone.utc,
        enabled=enabled,
        base_url=base_url,
        clock=lambda: now[0],
    )
    return AdminService(store, timezone.utc, clock=lambda: now[0], share_service=shares)


def test_share_lifecycle_and_noop_logs(env):
    _, store, space, *_, now = env
    service = share_admin(store, now)
    assert service.share_available is True
    assert service.get_space(space.id).share is None
    assert service.set_share("operator", space.id, "off").share is None
    assert store.list_space_events(space.id) == []

    first = service.set_share("operator", space.id, "on")
    link = store.get_share(space.id)
    assert first.share.url == f"https://todo.example/root/b/{link.token}/"
    assert first.share.created_at == now[0]
    assert service.set_share("operator", space.id, "on") == first
    assert len(store.list_space_events(space.id)) == 1

    now[0] += 1
    reset = service.set_share("operator", space.id, "reset")
    assert reset.share.url != first.share.url
    assert reset.share.created_at == now[0]
    assert store.find_share(link.token) is None
    now[0] += 1
    assert service.set_share("operator", space.id, "off").share is None
    assert service.set_share("operator", space.id, "off").share is None
    events = store.list_space_events(space.id)
    assert [event.detail for event in events] == [
        {"op": "off"},
        {"op": "reset"},
        {"op": "on"},
    ]
    assert all(event.action == SpaceEventAction.SHARE for event in events)
    assert all(
        event.actor is None and event.actor_label == "operator" and event.session == ""
        for event in events
    )


@pytest.mark.parametrize(
    ("enabled", "base_url", "available", "has_url"),
    [
        (True, "https://todo.example/", True, True),
        (False, "https://todo.example/", False, True),
        (True, "", False, False),
        (True, "todo.example", False, False),
    ],
)
def test_share_detail_uses_configuration_without_changing_rows(
    env, enabled, base_url, available, has_url
):
    _, store, space, *_, now = env
    with store.transaction():
        link = store.set_share(space.id, "stored-token")
    service = share_admin(store, now, enabled=enabled, base_url=base_url)
    assert service.share_available is available
    detail = service.get_space(space.id)
    assert (detail.share.url is not None) is has_url
    assert detail.share.created_at == link.created_at
    assert store.get_share(space.id) == link
    assert store.list_space_events(space.id) == []
    if not available:
        for action in ("on", "reset"):
            with pytest.raises(ShareConflict) as exc:
                service.set_share("operator", space.id, action)
            assert str(exc.value) == (
                "公开看板服务未开启或未配置对外访问地址，请联系 AstrBot 管理员"
            )
        assert store.get_share(space.id) == link
    assert service.set_share("operator", space.id, "off").share is None


def test_share_reset_without_link_and_invalid_action(env):
    _, store, space, *_, now = env
    service = share_admin(store, now)
    with pytest.raises(ShareConflict) as exc:
        service.set_share("operator", space.id, "reset")
    assert str(exc.value) == "当前空间还没有公开链接，用 /todo share on 开启"
    with pytest.raises(TodoError):
        service.set_share("operator", space.id, "invalid")
    assert store.get_share(space.id) is None
    assert store.list_space_events(space.id) == []


def test_share_log_failure_rolls_back_link(env, monkeypatch):
    _, store, space, *_, now = env
    service = share_admin(store, now)

    def fail_log(*args, **kwargs):
        raise RuntimeError("log write failed")

    monkeypatch.setattr(store, "add_space_event", fail_log)
    with pytest.raises(RuntimeError):
        service.set_share("operator", space.id, "on")
    assert store.get_share(space.id) is None
    assert store.list_space_events(space.id) == []
