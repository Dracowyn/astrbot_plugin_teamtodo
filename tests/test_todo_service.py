import dataclasses as dcs
from zoneinfo import ZoneInfo

import pytest
from astrbot_plugin_teamtodo.core.models import (
    MAX_ASSIGNEES,
    MAX_SPACE_NAME_CHARS,
    Actor,
    EventAction,
    Mention,
    Priority,
    SpaceEventAction,
    SpaceRole,
    TaskStatus,
    TodoError,
    UserRef,
)
from astrbot_plugin_teamtodo.core.parsing import (
    AssignOps,
    TaskRef,
    parse_assign,
    parse_task_fields,
)
from astrbot_plugin_teamtodo.core.permissions import DENIAL_MESSAGES, Op
from astrbot_plugin_teamtodo.core.store import Store
from astrbot_plugin_teamtodo.services.access import (
    CONFIRM_TTL_SECONDS,
    INVITE_TTL_SECONDS,
    ConfirmationCodes,
    JoinRateLimiter,
)
from astrbot_plugin_teamtodo.services.todo_service import (
    CONFIRM_MISMATCH,
    TaskUpdate,
    TodoService,
    auto_space_name,
)

GROUP_A = "qq:GroupMessage:1000"
GROUP_B = "qq:GroupMessage:2000"
PRIVATE = "qq:FriendMessage:u1"
AUTO = "TeamA"


class FakeClock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def store(clock):
    store = Store(":memory:", clock=clock)
    yield store
    store.close()


@pytest.fixture
def service(store, clock):
    return TodoService(
        store,
        ZoneInfo("Asia/Shanghai"),
        clock=clock,
        join_limiter=JoinRateLimiter(clock=clock),
        confirmations=ConfirmationCodes(clock=clock),
    )


def user(uid):
    return UserRef(platform_id="qq", user_id=uid)


def actor(uid, session=GROUP_A, *, admin=False, bot_admin=False):
    return Actor(
        user=user(uid),
        nickname=f"nick-{uid}",
        session=session,
        is_private=session.startswith("qq:FriendMessage"),
        is_bot_admin=bot_admin,
        is_session_admin=admin or bot_admin or session.startswith("qq:FriendMessage"),
    )


def mention(uid):
    return Mention(user=user(uid), name=f"name-{uid}")


def ref(seq, space=None):
    return TaskRef(space_name=space, seq=seq)


def add(service, who, *parts, session_auto=AUTO):
    fields = parse_task_fields(list(parts), service.local_now())
    return service.add_task(who, session_auto, fields).task


def edit_fields(service, *parts):
    return parse_task_fields(list(parts), service.local_now())


OWNER = actor("owner", admin=True)
MEMBER = actor("member")
OTHER = actor("other")


@pytest.fixture
def space(service):
    """Auto-created group space owned by OWNER, with MEMBER known."""
    service.list_tasks(OWNER, AUTO)
    service.list_tasks(MEMBER, AUTO)
    return service.list_tasks(OWNER, AUTO).space


# ----- auto-create and space resolution -------------------------------------


def test_auto_space_name_rules():
    # Act / Assert
    assert auto_space_name(True, "ignored", "1") == "我的任务"
    assert auto_space_name(False, "Team Chat #1", "123456") == "TeamChat1"
    assert auto_space_name(False, None, "123456") == "群3456"
    assert auto_space_name(False, " # ", "99") == "群99"


def test_first_use_by_group_admin_creates_owned_default_space(service, store):
    # Act
    result = service.list_tasks(OWNER, AUTO)

    # Assert
    assert result.space.name == AUTO
    assert result.space.owner == OWNER.user
    assert store.get_session_default(GROUP_A) == result.space.id
    assert store.get_member(result.space.id, OWNER.user).nickname == "nick-owner"


def test_first_use_by_plain_member_creates_ownerless_space(service):
    # Act
    result = service.list_tasks(MEMBER, AUTO)

    # Assert
    assert result.space.owner is None


def test_first_use_in_private_chat_owns_personal_space(service):
    # Act
    result = service.list_tasks(actor("u1", PRIVATE), "我的任务")

    # Assert
    assert result.space.name == "我的任务"
    assert result.space.owner == user("u1")


def test_long_auto_name_is_truncated(service):
    # Act
    result = service.list_tasks(OWNER, "x" * 50)

    # Assert
    assert result.space.name == "x" * MAX_SPACE_NAME_CHARS


def test_denied_operation_rolls_back_auto_created_space(service, store):
    # Act
    with pytest.raises(TodoError):
        service.create_invite(MEMBER, AUTO)

    # Assert
    assert store.list_bindings(GROUP_A) == []


def test_current_space_falls_back_after_leave(service, store, space):
    # Arrange
    second = service.new_space(OWNER, "Second")
    assert service.list_tasks(OWNER, AUTO).space.id == second.id

    # Act / Assert: current unbound -> session default
    service.leave_space(OWNER, "Second")
    assert service.list_tasks(OWNER, AUTO).space.id == space.id

    # Act / Assert: default unbound -> earliest remaining binding
    third = service.new_space(OWNER, "Third")
    service.leave_space(OWNER, AUTO)
    assert service.list_tasks(MEMBER, AUTO).space.id == third.id
    assert store.get_session_default(GROUP_A) == third.id

    # Act / Assert: nothing bound -> auto-create again
    service.leave_space(OWNER, "Third")
    fresh = service.list_tasks(MEMBER, AUTO).space
    assert fresh.id not in (space.id, second.id, third.id)
    assert fresh.owner is None


def test_explicit_space_name_must_be_bound_to_session(service, space):
    # Arrange
    service.list_tasks(actor("b", GROUP_B, admin=True), "TeamB")

    # Act / Assert
    with pytest.raises(TodoError, match="没有绑定"):
        service.list_tasks(OWNER, AUTO, "TeamB")
    with pytest.raises(TodoError, match="没有绑定"):
        service.show_task(OWNER, AUTO, ref(1, "TeamB"))


def test_list_spaces_marks_default_and_current(service, space):
    # Arrange
    second = service.new_space(OWNER, "Second")

    # Act
    owner_rows = service.list_spaces(OWNER, AUTO)
    member_rows = service.list_spaces(MEMBER, AUTO)

    # Assert
    assert [(s.id, d, c) for s, d, c in owner_rows] == [
        (space.id, True, False),
        (second.id, False, True),
    ]
    assert [(s.id, d, c) for s, d, c in member_rows] == [
        (space.id, True, True),
        (second.id, False, False),
    ]


def test_use_switches_only_own_current_space(service, space):
    # Arrange
    service.new_space(OWNER, "Second")

    # Act
    used = service.use_space(MEMBER, "Second")

    # Assert
    assert service.list_tasks(MEMBER, AUTO).space.id == used.id
    assert service.list_tasks(OTHER, AUTO).space.id == space.id


def test_default_needs_session_admin(service, space):
    # Arrange
    second = service.new_space(OWNER, "Second")

    # Act / Assert
    with pytest.raises(TodoError, match=DENIAL_MESSAGES[Op.SESSION]):
        service.set_default(MEMBER, "Second")
    assert service.set_default(OWNER, "Second").id == second.id
    assert service.list_tasks(OTHER, AUTO).space.id == second.id


# ----- tasks -------------------------------------------------------------------


def test_add_assigns_mentions_and_makes_them_members(service, store, space):
    # Act
    task = add(service, MEMBER, "写周报 ", mention("x"), " 明天 !高 [doc]")

    # Assert
    assert task.title == "写周报"
    assert task.assignees == (user("x"),)
    assert task.priority == "high"
    assert task.tags == ("doc",)
    assert task.due_at is not None
    assert store.get_member(space.id, user("x")).nickname == "name-x"
    event = store.list_events(space.id, task.seq)[0]
    assert event.action == EventAction.CREATE
    assert event.actor == MEMBER.user
    assert event.session == GROUP_A


@pytest.mark.parametrize(
    ("parts", "message"),
    [
        (["!高"], "标题不能为空"),
        (["x" * 201], "标题不能超过"),
        (["t " + " ".join(f"[t{i}]" for i in range(11))], "标签不能超过"),
        (["t [" + "x" * 21 + "]"], "单个标签"),
    ],
)
def test_add_enforces_limits(service, space, parts, message):
    # Act / Assert
    with pytest.raises(TodoError, match=message):
        add(service, MEMBER, *parts)


def test_add_rejects_too_many_assignees(service, space):
    # Arrange
    parts = ["t"] + [mention(f"m{i}") for i in range(MAX_ASSIGNEES + 1)]

    # Act / Assert
    with pytest.raises(TodoError, match="负责人不能超过"):
        add(service, MEMBER, *parts)


def test_list_hides_archived_unless_requested(service, space):
    # Arrange
    add(service, OWNER, "a")
    add(service, OWNER, "b")
    service.set_status(OWNER, AUTO, ref(2), TaskStatus.ARCHIVED)

    # Act
    visible = service.list_tasks(OWNER, AUTO).tasks
    everything = service.list_tasks(OWNER, AUTO, include_archived=True).tasks

    # Assert
    assert [t.seq for t in visible] == [1]
    assert [t.seq for t in everything] == [1, 2]


def test_show_unknown_task_fails(service, space):
    # Act / Assert
    with pytest.raises(TodoError, match="#9"):
        service.show_task(MEMBER, AUTO, ref(9))


def test_show_by_space_qualified_ref(service, space):
    # Arrange
    add(service, OWNER, "a")

    # Act
    result = service.show_task(MEMBER, AUTO, ref(1, AUTO))

    # Assert
    assert result.task.title == "a"
    assert result.nicknames[MEMBER.user.key] == "nick-member"


def test_status_rules_for_done_at(service, store, clock, space):
    # Arrange
    add(service, MEMBER, "t")

    # Act / Assert
    done = service.set_status(MEMBER, AUTO, ref(1), TaskStatus.DONE).task
    assert done.done_at == clock.now
    clock.now += 10
    archived = service.set_status(MEMBER, AUTO, ref(1), TaskStatus.ARCHIVED).task
    assert archived.done_at == done.done_at
    reopened = service.set_status(MEMBER, AUTO, ref(1), TaskStatus.TODO).task
    assert reopened.done_at is None
    service.set_status(MEMBER, AUTO, ref(1), TaskStatus.DONE)
    doing = service.set_status(MEMBER, AUTO, ref(1), TaskStatus.DOING).task
    assert doing.done_at is None

    actions = [e.action for e in reversed(store.list_events(space.id, 1))]
    assert actions == [
        EventAction.CREATE,
        EventAction.STATUS,
        EventAction.ARCHIVE,
        EventAction.STATUS,
        EventAction.STATUS,
        EventAction.STATUS,
    ]
    assert store.list_events(space.id, 1)[0].detail == {"status": ["done", "doing"]}


def test_setting_same_status_is_a_no_op(service, store, space):
    # Arrange
    add(service, MEMBER, "t")

    # Act
    service.set_status(MEMBER, AUTO, ref(1), TaskStatus.TODO)

    # Assert
    assert len(store.list_events(space.id, 1)) == 1


def test_plain_member_cannot_change_others_task(service, space):
    # Arrange
    add(service, OWNER, "t")

    # Act / Assert
    with pytest.raises(TodoError, match=DENIAL_MESSAGES[Op.EDIT_TASK]):
        service.set_status(MEMBER, AUTO, ref(1), TaskStatus.DONE)


def test_assignee_can_edit_but_cannot_delete(service, space):
    # Arrange
    add(service, OWNER, "t ", mention("member"))

    # Act
    service.set_status(MEMBER, AUTO, ref(1), TaskStatus.DOING)

    # Assert
    with pytest.raises(TodoError, match=DENIAL_MESSAGES[Op.DELETE_TASK]):
        service.delete_task(MEMBER, AUTO, ref(1))


def test_creator_can_delete_own_task_with_snapshot(service, store, space):
    # Arrange
    add(service, MEMBER, "t ", mention("x"), " [a]")

    # Act
    deleted = service.delete_task(MEMBER, AUTO, ref(1)).task

    # Assert
    assert store.get_task(space.id, 1) is None
    event = store.list_events(space.id, 1)[0]
    assert event.action == EventAction.DELETE
    snapshot = event.detail["snapshot"]
    assert snapshot["title"] == "t"
    assert snapshot["tags"] == ["a"]
    assert snapshot["assignees"] == [{"platform_id": "qq", "user_id": "x"}]
    assert snapshot["id"] == deleted.id


def test_space_admin_and_bot_admin_can_delete_others_task(service, store, space):
    # Arrange
    add(service, MEMBER, "a")
    add(service, MEMBER, "b")

    # Act
    service.delete_task(OWNER, AUTO, ref(1))
    service.delete_task(actor("root", bot_admin=True), AUTO, ref(2))

    # Assert
    assert store.list_tasks(space.id, tuple(TaskStatus)) == []


def test_edit_changes_fields_and_resets_reminded(service, store, space):
    # Arrange
    task = add(service, MEMBER, "old 明天 !低 [a] [b]")
    store_task = dcs.replace(task, reminded=True)
    with store.transaction():
        store.update_task(store_task)

    # Act
    edited = service.edit_task(
        MEMBER, AUTO, ref(1), edit_fields(service, "new 后天 18:00 !无 [-a] [c]")
    ).task

    # Assert
    assert edited.title == "new"
    assert edited.due_at != task.due_at
    assert edited.priority is None
    assert edited.tags == ("b", "c")
    assert edited.reminded is False
    detail = store.list_events(space.id, 1)[0].detail
    assert set(detail) == {"title", "due_at", "priority", "tags"}
    assert detail["title"] == ["old", "new"]
    assert detail["tags"] == [["a", "b"], ["b", "c"]]


def test_edit_without_due_change_keeps_reminded(service, store, space):
    # Arrange
    task = add(service, MEMBER, "old 明天")
    with store.transaction():
        store.update_task(dcs.replace(task, reminded=True))

    # Act
    edited = service.edit_task(MEMBER, AUTO, ref(1), edit_fields(service, "new")).task

    # Assert
    assert edited.reminded is True


def test_edit_clear_due(service, space):
    # Arrange
    add(service, MEMBER, "t 明天")

    # Act
    edited = service.edit_task(
        MEMBER, AUTO, ref(1), edit_fields(service, "无截止")
    ).task

    # Assert
    assert edited.due_at is None


def test_edit_rejects_no_change_and_mentions(service, space):
    # Arrange
    add(service, MEMBER, "t")

    # Act / Assert
    with pytest.raises(TodoError, match="没有需要修改"):
        service.edit_task(MEMBER, AUTO, ref(1), edit_fields(service, "t"))
    with pytest.raises(TodoError, match="assign"):
        service.edit_task(MEMBER, AUTO, ref(1), edit_fields(service, mention("x")))


def test_edit_enforces_tag_limit(service, space):
    # Arrange
    add(service, MEMBER, "t " + " ".join(f"[t{i}]" for i in range(10)))

    # Act / Assert
    with pytest.raises(TodoError, match="标签不能超过"):
        service.edit_task(MEMBER, AUTO, ref(1), edit_fields(service, "[extra]"))


def test_assign_replace_and_delta(service, store, space):
    # Arrange
    add(service, MEMBER, "t ", mention("a"))

    # Act / Assert: replace
    replaced = service.assign(
        MEMBER, AUTO, ref(1), parse_assign([mention("b"), " ", mention("c")])
    ).task
    assert replaced.assignees == (user("b"), user("c"))
    assert store.get_member(space.id, user("c")) is not None

    # Act / Assert: delta
    delta = service.assign(
        MEMBER, AUTO, ref(1), parse_assign(["+", mention("d"), " -", mention("b")])
    ).task
    assert delta.assignees == (user("c"), user("d"))
    event = store.list_events(space.id, 1)[0]
    assert event.action == EventAction.ASSIGN
    assert event.detail == {"assignees": [["qq:b", "qq:c"], ["qq:c", "qq:d"]]}

    # Act / Assert: clear, then unchanged clear is a no-op
    cleared = service.assign(MEMBER, AUTO, ref(1), parse_assign([])).task
    assert cleared.assignees == ()
    service.assign(MEMBER, AUTO, ref(1), parse_assign([]))
    assert len(store.list_events(space.id, 1)) == 4


def test_assign_enforces_limit(service, space):
    # Arrange
    add(service, MEMBER, "t")
    ops = AssignOps(
        replace=tuple(mention(f"m{i}") for i in range(MAX_ASSIGNEES + 1)),
        add=(),
        remove=(),
    )

    # Act / Assert
    with pytest.raises(TodoError, match="负责人不能超过"):
        service.assign(MEMBER, AUTO, ref(1), ops)


def test_description_set_clear_and_limit(service, store, space):
    # Arrange
    add(service, MEMBER, "t")

    # Act / Assert
    assert (
        service.set_description(MEMBER, AUTO, ref(1), " line1\nline2 ").task.description
        == "line1\nline2"
    )
    service.set_description(MEMBER, AUTO, ref(1), "line1\nline2")  # unchanged
    assert service.set_description(MEMBER, AUTO, ref(1), "").task.description == ""
    with pytest.raises(TodoError, match="描述不能超过"):
        service.set_description(MEMBER, AUTO, ref(1), "x" * 4001)
    assert len(store.list_events(space.id, 1)) == 3
    with pytest.raises(TodoError, match=DENIAL_MESSAGES[Op.EDIT_TASK]):
        service.set_description(OTHER, AUTO, ref(1), "hack")


@pytest.fixture
def mine_setup(service, space):
    """MEMBER has open tasks in TeamA (group A) and TeamB (group B only)."""
    b_admin = actor("b", GROUP_B, admin=True)
    service.list_tasks(b_admin, "TeamB")
    add(service, OWNER, "a1 ", mention("member"), " 后天")
    add(service, OWNER, "a2 ", mention("member"), " 明天")
    add(service, OWNER, "a3 ", mention("member"))
    service.set_status(OWNER, AUTO, ref(3), TaskStatus.DONE)
    add(service, b_admin, "b1 ", mention("member"), session_auto="TeamB")


def test_mine_in_private_chat_lists_open_tasks_across_all_spaces(service, mine_setup):
    # Act
    items, nicknames = service.mine(actor("member", "qq:FriendMessage:member"))

    # Assert
    assert [(name, t.title) for name, t in items] == [
        (AUTO, "a2"),
        (AUTO, "a1"),
        ("TeamB", "b1"),
    ]
    assert nicknames["qq:b"] == "nick-b"


def test_mine_in_group_chat_lists_only_spaces_bound_to_session(service, mine_setup):
    # Act
    items, nicknames = service.mine(MEMBER)

    # Assert
    assert [(name, t.title) for name, t in items] == [(AUTO, "a2"), (AUTO, "a1")]
    assert "qq:b" not in nicknames
    assert nicknames["qq:owner"] == "nick-owner"


def test_mine_in_unrelated_group_lists_nothing(service, mine_setup):
    # Act
    items, nicknames = service.mine(actor("member", "qq:GroupMessage:3000"))

    # Assert
    assert items == []
    assert nicknames == {}


def test_nameless_mention_is_left_out_of_nicknames(service, space):
    # Arrange
    nameless = Mention(user=user("carol"), name="")

    # Act
    result = service.add_task(MEMBER, AUTO, edit_fields(service, "t ", nameless))

    # Assert
    assert result.task.assignees == (user("carol"),)
    assert "qq:carol" not in result.nicknames


# ----- space and session commands ----------------------------------------------


def test_new_space_needs_session_admin_and_unique_valid_name(service, store, space):
    # Act / Assert
    with pytest.raises(TodoError, match=DENIAL_MESSAGES[Op.SESSION]):
        service.new_space(MEMBER, "X")
    with pytest.raises(TodoError, match="已有名为"):
        service.new_space(OWNER, AUTO)
    with pytest.raises(TodoError, match="空格或 #"):
        service.new_space(OWNER, "a b")
    with pytest.raises(TodoError, match="不能为空"):
        service.new_space(OWNER, " ")
    with pytest.raises(TodoError, match="不能超过"):
        service.new_space(OWNER, "x" * 31)
    created = service.new_space(OWNER, "X")
    assert created.owner == OWNER.user
    assert store.get_current(GROUP_A, OWNER.user) == created.id


def test_rename_needs_space_admin(service, space):
    # Act / Assert
    with pytest.raises(TodoError, match=DENIAL_MESSAGES[Op.MANAGE_SPACE]):
        service.rename_space(MEMBER, AUTO, "New")
    assert service.rename_space(OWNER, AUTO, "New").name == "New"


def test_rename_rejects_name_taken_in_any_bound_session(service, space):
    # Arrange: share the space with group B, where "Taken" exists
    b_admin = actor("b", GROUP_B, admin=True)
    service.new_space(b_admin, "Taken")
    _, code, _ = service.create_invite(OWNER, AUTO)
    service.join_space(b_admin, code)

    # Act / Assert
    with pytest.raises(TodoError, match="已被占用"):
        service.rename_space(OWNER, AUTO, "Taken")


def test_invite_and_join_flow(service, store, space):
    # Arrange
    b_admin = actor("b", GROUP_B, admin=True)
    _, old_code, _ = service.create_invite(OWNER, AUTO)
    invite_space, code, invite = service.create_invite(OWNER, AUTO)

    # Act
    with pytest.raises(TodoError, match="无效或已过期"):
        service.join_space(b_admin, old_code)
    joined = service.join_space(b_admin, code.lower())

    # Assert
    assert joined.id == invite_space.id == space.id
    assert invite.expires_at == service.now() + INVITE_TTL_SECONDS
    assert store.session_space_names(GROUP_B) == {AUTO: space.id}
    assert store.get_member(space.id, b_admin.user) is not None
    with pytest.raises(TodoError, match="已经绑定"):
        service.join_space(b_admin, code)


def test_invite_needs_space_admin(service, space):
    # Act / Assert
    with pytest.raises(TodoError, match=DENIAL_MESSAGES[Op.MANAGE_SPACE]):
        service.create_invite(MEMBER, AUTO)


def test_join_rejects_name_collision_without_renaming_or_counting(
    service, store, space
):
    # Arrange
    b_admin = actor("b", GROUP_B, admin=True)
    local = service.list_tasks(b_admin, AUTO).space  # group B's own "TeamA"
    _, code, _ = service.create_invite(OWNER, AUTO)

    # Act / Assert: rejected every time, never rate limited
    for _ in range(6):
        with pytest.raises(
            TodoError,
            match=f"本会话已有名为「{AUTO}」的空间，请先用 /todo space rename 改名后再加入",
        ):
            service.join_space(b_admin, code)
    assert store.get_space(space.id).name == AUTO
    assert store.session_space_names(GROUP_B) == {AUTO: local.id}

    # Act / Assert: after renaming the local space the join succeeds
    service.rename_space(b_admin, AUTO, "Local")
    joined = service.join_space(b_admin, code)
    assert joined.name == AUTO
    assert store.session_space_names(GROUP_B) == {"Local": local.id, AUTO: space.id}


def test_join_needs_session_admin(service, space):
    # Act / Assert
    with pytest.raises(TodoError, match=DENIAL_MESSAGES[Op.SESSION]):
        service.join_space(actor("pleb", GROUP_B), "ANYTHING")
    with pytest.raises(TodoError, match="请提供邀请码"):
        service.join_space(actor("b", GROUP_B, admin=True), " ")


def test_join_rate_limit_blocks_after_five_failures(service, clock, space):
    # Arrange
    b_admin = actor("b", GROUP_B, admin=True)
    _, code, _ = service.create_invite(OWNER, AUTO)
    for _ in range(5):
        with pytest.raises(TodoError, match="无效"):
            service.join_space(b_admin, "WRONGWRONG22")

    # Act / Assert
    with pytest.raises(TodoError, match="次数过多"):
        service.join_space(b_admin, code)
    clock.now += 3600
    assert service.join_space(b_admin, code).id == space.id


def test_expired_invite_counts_as_failure(service, clock, space):
    # Arrange
    b_admin = actor("b", GROUP_B, admin=True)
    _, code, _ = service.create_invite(OWNER, AUTO)
    clock.now += INVITE_TTL_SECONDS

    # Act / Assert
    with pytest.raises(TodoError, match="无效或已过期"):
        service.join_space(b_admin, code)


def test_leave_needs_session_admin_and_keeps_space(service, store, space):
    # Act / Assert
    with pytest.raises(TodoError, match=DENIAL_MESSAGES[Op.SESSION]):
        service.leave_space(MEMBER, AUTO)
    service.leave_space(OWNER, AUTO)
    assert store.list_bindings(GROUP_A) == []
    assert store.get_space(space.id) is not None


def test_claim_rules(service, store):
    # Arrange: ownerless group space
    ownerless = service.list_tasks(MEMBER, AUTO).space
    group_admin = actor("ga", admin=True)

    # Act / Assert
    with pytest.raises(TodoError, match="群聊"):
        service.claim(actor("u1", PRIVATE), "我的任务")
    with pytest.raises(TodoError, match=DENIAL_MESSAGES[Op.CLAIM]):
        service.claim(MEMBER, AUTO)
    claimed = service.claim(group_admin, AUTO)
    assert claimed.id == ownerless.id
    assert claimed.owner == group_admin.user
    assert store.get_member(ownerless.id, group_admin.user).role == SpaceRole.OWNER
    with pytest.raises(TodoError, match=DENIAL_MESSAGES[Op.CLAIM]):
        service.claim(actor("ga2", admin=True), AUTO)


def test_owner_appoints_and_revokes_admins(service, store, space):
    # Act
    _, changed = service.set_admins(OWNER, AUTO, parse_assign(["+", mention("member")]))

    # Assert
    assert [m.role for m in changed] == [SpaceRole.ADMIN]
    admin = actor("member")
    assert service.rename_space(admin, AUTO, "Renamed").name == "Renamed"
    with pytest.raises(TodoError, match=DENIAL_MESSAGES[Op.MANAGE_ADMINS]):
        service.set_admins(admin, AUTO, parse_assign(["+", mention("other")]))
    _, changed = service.set_admins(OWNER, AUTO, parse_assign(["-", mention("member")]))
    assert changed[0].role == SpaceRole.MEMBER


def test_adding_unknown_admin_registers_member_and_logs_role(service, store, space):
    _, changed = service.set_admins(OWNER, AUTO, parse_assign(["+", mention("ghost")]))

    target = store.get_member(space.id, user("ghost"))
    assert target == changed[0]
    assert target.nickname == "name-ghost"
    assert target.role == SpaceRole.ADMIN
    event = store.list_space_events(space.id)[0]
    assert event.action == SpaceEventAction.ROLE
    assert event.detail == {"user": user("ghost").key, "role": ["member", "admin"]}
    assert event.actor == OWNER.user
    assert event.session == GROUP_A


def test_admin_removal_requires_member_and_target_cannot_be_owner(
    service, store, space
):
    # Act / Assert
    with pytest.raises(TodoError, match="还不是这个空间的成员"):
        service.set_admins(OWNER, AUTO, parse_assign(["-", mention("ghost")]))
    assert store.get_member(space.id, user("ghost")) is None
    with pytest.raises(TodoError, match="所有者"):
        service.set_admins(
            actor("root", bot_admin=True), AUTO, parse_assign(["-", mention("owner")])
        )
    with pytest.raises(TodoError, match="用法"):
        service.set_admins(OWNER, AUTO, parse_assign([mention("member")]))


def test_delete_space_confirmation_flow(service, store, clock, space):
    # Act / Assert: non-owner denied
    with pytest.raises(TodoError, match=DENIAL_MESSAGES[Op.DELETE_SPACE]):
        service.delete_space(MEMBER, AUTO, None)

    # Wrong code voids the pending one
    code = service.delete_space(OWNER, AUTO, None)
    assert len(code) == 4 and code.isdigit()
    wrong = f"{(int(code) + 1) % 10000:04d}"
    with pytest.raises(TodoError, match=CONFIRM_MISMATCH):
        service.delete_space(OWNER, AUTO, wrong)
    with pytest.raises(TodoError, match=CONFIRM_MISMATCH):
        service.delete_space(OWNER, AUTO, code)

    # Expired code
    code = service.delete_space(OWNER, AUTO, None)
    clock.now += CONFIRM_TTL_SECONDS
    with pytest.raises(TodoError, match=CONFIRM_MISMATCH):
        service.delete_space(OWNER, AUTO, code)

    # Correct code deletes
    code = service.delete_space(OWNER, AUTO, None)
    assert service.delete_space(OWNER, AUTO, code) is None
    assert store.get_space(space.id) is None
    assert store.list_bindings(GROUP_A) == []


def test_delete_space_code_is_bound_to_user(service, space):
    # Arrange
    root = actor("root", bot_admin=True)
    code = service.delete_space(OWNER, AUTO, None)

    # Act / Assert
    with pytest.raises(TodoError, match=CONFIRM_MISMATCH):
        service.delete_space(root, AUTO, code)


# ----- M2: added assignees, combined update, member lookup -----------------


def test_add_reports_all_assignees_as_added_and_stores_description(service, space):
    # Arrange
    fields = parse_task_fields(["写周报 ", mention("bob")], service.local_now())

    # Act
    result = service.add_task(MEMBER, AUTO, fields, description="  第一行\n第二行  ")

    # Assert
    assert result.added_assignees == (user("bob"),)
    assert result.task.description == "第一行\n第二行"


def test_add_rejects_too_long_description(service, space):
    # Arrange
    fields = parse_task_fields(["x"], service.local_now())

    # Act / Assert
    with pytest.raises(TodoError, match="描述不能超过"):
        service.add_task(MEMBER, AUTO, fields, description="d" * 4001)


def test_assign_reports_only_new_assignees(service, space):
    # Arrange
    add(service, MEMBER, "x ", mention("bob"))

    # Act
    replaced = service.assign(
        MEMBER,
        AUTO,
        ref(1),
        AssignOps(replace=(mention("bob"), mention("eve")), add=(), remove=()),
    )
    removed = service.assign(
        MEMBER, AUTO, ref(1), AssignOps(replace=None, add=(), remove=(mention("eve"),))
    )
    unchanged = service.assign(
        MEMBER, AUTO, ref(1), AssignOps(replace=None, add=(mention("bob"),), remove=())
    )

    # Assert
    assert replaced.added_assignees == (user("eve"),)
    assert removed.added_assignees == ()
    assert unchanged.added_assignees == ()


def test_update_applies_all_fields_and_logs_like_single_operations(
    service, store, clock, space
):
    # Arrange
    add(service, MEMBER, "旧标题 明天 !高 [a] [b] ", mention("bob"))
    store_task = store.get_task(space.id, 1)
    with store.transaction():
        store.update_task(dcs.replace(store_task, reminded=True))
    clock.now += 60
    change = TaskUpdate(
        status=TaskStatus.DONE,
        title="新标题",
        due_at=clock.now + 3600,
        priority=Priority.LOW,
        add_tags=("c",),
        remove_tags=("a",),
        description="说明",
        add_assignees=(mention("eve"),),
        remove_assignees=(user("bob"),),
    )

    # Act
    result = service.update_task(MEMBER, AUTO, ref(1), change)

    # Assert
    task = result.task
    assert (task.title, task.priority, task.tags) == (
        "新标题",
        Priority.LOW,
        ("b", "c"),
    )
    assert task.description == "说明"
    assert task.due_at == clock.now + 3600
    assert task.reminded is False
    assert task.assignees == (user("eve"),)
    assert result.added_assignees == (user("eve"),)
    assert task.status == TaskStatus.DONE and task.done_at == clock.now
    assert store.get_member(space.id, user("eve")).nickname == "name-eve"
    events = list(reversed(store.list_events(space.id, 1)))
    assert [e.action for e in events] == [
        EventAction.CREATE,
        EventAction.UPDATE,
        EventAction.ASSIGN,
        EventAction.STATUS,
    ]
    assert set(events[1].detail) == {
        "title",
        "due_at",
        "priority",
        "tags",
        "description",
    }
    assert events[2].detail == {"assignees": [["qq:bob"], ["qq:eve"]]}
    assert events[3].detail == {"status": ["todo", "done"]}


def test_update_archive_keeps_done_at_and_reopen_clears_it(
    service, store, clock, space
):
    # Arrange
    add(service, MEMBER, "x")
    service.update_task(MEMBER, AUTO, ref(1), TaskUpdate(status=TaskStatus.DONE))
    done_at = clock.now
    clock.now += 100

    # Act
    archived = service.update_task(
        MEMBER, AUTO, ref(1), TaskUpdate(status=TaskStatus.ARCHIVED)
    ).task
    reopened = service.update_task(
        MEMBER, AUTO, ref(1), TaskUpdate(status=TaskStatus.TODO)
    ).task

    # Assert
    assert archived.done_at == done_at
    assert store.list_events(space.id, 1)[1].action == EventAction.ARCHIVE
    assert reopened.done_at is None


def test_update_clears_due_priority_and_description(service, space):
    # Arrange
    add(service, MEMBER, "x 明天 !高")
    service.set_description(MEMBER, AUTO, ref(1), "说明")

    # Act
    task = service.update_task(
        MEMBER,
        AUTO,
        ref(1),
        TaskUpdate(clear_due=True, clear_priority=True, description=""),
    ).task

    # Assert
    assert (task.due_at, task.priority, task.description) == (None, None, "")


def test_update_is_all_or_nothing(service, store, space):
    # Arrange
    add(service, MEMBER, "x")
    before = store.get_task(space.id, 1)
    events_before = len(store.list_events(space.id, 1))
    change = TaskUpdate(
        status=TaskStatus.DONE,
        add_assignees=(mention("eve"),),
        title="t" * 201,
    )

    # Act
    with pytest.raises(TodoError, match="标题不能超过"):
        service.update_task(MEMBER, AUTO, ref(1), change)

    # Assert
    assert store.get_task(space.id, 1) == before
    assert len(store.list_events(space.id, 1)) == events_before
    assert store.get_member(space.id, user("eve")) is None


def test_update_without_effect_writes_nothing(service, store, space):
    # Arrange
    add(service, MEMBER, "x")
    before = store.get_task(space.id, 1)

    # Act
    result = service.update_task(
        MEMBER, AUTO, ref(1), TaskUpdate(title="x", status=TaskStatus.TODO)
    )

    # Assert
    assert result.task == before
    assert result.added_assignees == ()
    assert len(store.list_events(space.id, 1)) == 1


def test_update_requires_edit_permission(service, store, space):
    # Arrange
    add(service, MEMBER, "x")

    # Act / Assert
    with pytest.raises(TodoError, match=DENIAL_MESSAGES[Op.EDIT_TASK]):
        service.update_task(OTHER, AUTO, ref(1), TaskUpdate(status=TaskStatus.DONE))
    assert store.get_task(space.id, 1).status == TaskStatus.TODO


def test_find_members_matches_exact_nickname_ignoring_case(service, store, space):
    # Arrange
    with store.transaction():
        store.upsert_member(space.id, user("b1"), "Bob")
        store.upsert_member(space.id, user("b2"), "bob")
        store.upsert_member(space.id, user("b3"), "Bobby")

    # Act
    found = service.find_members(MEMBER, AUTO, "  BOB ")
    empty = service.find_members(MEMBER, AUTO, "   ")

    # Assert
    assert {m.user for m in found} == {user("b1"), user("b2")}
    assert empty == []


def test_find_members_requires_bound_space(service, space):
    # Act / Assert
    with pytest.raises(TodoError, match="没有绑定"):
        service.find_members(MEMBER, AUTO, "bob", "Elsewhere")


def test_space_event_lifecycle_and_chat_identity(service, store, clock):
    from astrbot_plugin_teamtodo.core.models import SpaceEventAction as Action

    space = service.list_tasks(OWNER, AUTO).space
    initial = list(reversed(store.list_space_events(space.id)))
    assert [(e.action, e.detail) for e in initial] == [
        (Action.CREATE, {"name": AUTO}),
        (Action.BIND, {"session": GROUP_A}),
    ]
    service.rename_space(OWNER, AUTO, "Renamed")
    _, code, invite = service.create_invite(OWNER, AUTO)
    guest = actor("guest", GROUP_B, admin=True)
    service.join_space(guest, code)
    service.leave_space(guest, "Renamed")
    events = list(reversed(store.list_space_events(space.id)))
    assert [(e.action, e.detail) for e in events[2:]] == [
        (Action.RENAME, {"name": [AUTO, "Renamed"]}),
        (Action.INVITE, {"expires_at": invite.expires_at}),
        (Action.BIND, {"session": GROUP_B}),
        (Action.UNBIND, {"session": GROUP_B}),
    ]
    assert all(e.actor == OWNER.user and e.session == GROUP_A for e in events[:4])
    assert all(e.actor == guest.user and e.session == GROUP_B for e in events[4:])
    assert all(e.actor_label == "" and e.created_at == clock.now for e in events)


def test_explicit_space_logs_create_then_bind(service, store):
    from astrbot_plugin_teamtodo.core.models import SpaceEventAction as Action

    space = service.new_space(OWNER, "Explicit")
    events = list(reversed(store.list_space_events(space.id)))
    assert [(e.action, e.detail, e.actor, e.session) for e in events] == [
        (Action.CREATE, {"name": "Explicit"}, OWNER.user, GROUP_A),
        (Action.BIND, {"session": GROUP_A}, OWNER.user, GROUP_A),
    ]


def test_claim_and_actual_role_changes_only_are_logged(service, store):
    from astrbot_plugin_teamtodo.core.models import SpaceEventAction as Action

    space = service.list_tasks(MEMBER, AUTO).space
    service.claim(OWNER, AUTO)
    service.set_admins(
        OWNER, AUTO, AssignOps(replace=None, add=(mention("member"),), remove=())
    )
    service.set_admins(
        OWNER, AUTO, AssignOps(replace=None, add=(mention("member"),), remove=())
    )
    service.set_admins(
        OWNER, AUTO, AssignOps(replace=None, add=(), remove=(mention("member"),))
    )
    events = list(reversed(store.list_space_events(space.id)))
    assert [(e.action, e.detail) for e in events[2:]] == [
        (Action.CLAIM, {}),
        (Action.ROLE, {"user": MEMBER.user.key, "role": ["member", "admin"]}),
        (Action.ROLE, {"user": MEMBER.user.key, "role": ["admin", "member"]}),
    ]
    assert all(e.actor == OWNER.user and e.session == GROUP_A for e in events[2:])


def test_failed_space_changes_roll_back_log_and_state(service, store, space):
    baseline = store.list_space_events(space.id)
    with pytest.raises(TodoError):
        service.set_admins(
            OWNER,
            AUTO,
            AssignOps(
                replace=None,
                add=(mention("member"), mention("new")),
                remove=(mention("unknown"),),
            ),
        )
    assert store.get_member(space.id, MEMBER.user).role == SpaceRole.MEMBER
    assert store.get_member(space.id, user("new")) is None
    assert store.list_space_events(space.id) == baseline
    with pytest.raises(TodoError):
        service.rename_space(MEMBER, AUTO, "Denied")
    assert store.get_space(space.id).name == AUTO
    assert store.list_space_events(space.id) == baseline


def test_note_session_title_ignores_blank_and_updates_only_changes(
    service, store, clock
):
    service.note_session_title(GROUP_A, " \n ")
    assert store.get_session_titles([GROUP_A]) == {}
    service.note_session_title(GROUP_A, "Team name")
    clock.now += 10
    service.note_session_title(GROUP_A, "Team name")
    row = store._conn.execute(
        "SELECT title, updated_at FROM session_titles WHERE session=?", (GROUP_A,)
    ).fetchone()
    assert tuple(row) == ("Team name", clock.now - 10)
    service.note_session_title(GROUP_A, "New name")
    assert store.get_session_titles([GROUP_A]) == {GROUP_A: "New name"}
    assert (
        store._conn.execute(
            "SELECT updated_at FROM session_titles WHERE session=?", (GROUP_A,)
        ).fetchone()[0]
        == clock.now
    )
    service.note_session_title(GROUP_A, "")
    assert store.get_session_titles([GROUP_A]) == {GROUP_A: "New name"}


def test_admin_id_membership_lookup_uses_current_space_and_platform(
    service, store, space
):
    target = user("123456")
    with store.transaction():
        original = store.upsert_member(space.id, target, "Known")
    assert service.get_member(OWNER, AUTO, target) == original
    assert service.get_member(OWNER, AUTO, UserRef("other", "123456")) is None
    second = service.new_space(OWNER, "Second")
    assert service.get_member(OWNER, AUTO, target) is None
    assert store.get_member(second.id, target) is None


def test_admin_member_id_preserves_nickname_and_role_logs(service, store, space):
    target = user("123456")
    with store.transaction():
        store.upsert_member(space.id, target, "Known")
    raw = Mention(target, "")
    _, promoted = service.set_admins(OWNER, AUTO, AssignOps(None, (raw,), ()))
    assert promoted[0].nickname == "Known"
    assert promoted[0].role == SpaceRole.ADMIN
    _, revoked = service.set_admins(OWNER, AUTO, AssignOps(None, (), (raw,)))
    assert revoked[0].nickname == "Known"
    assert revoked[0].role == SpaceRole.MEMBER
    assert [event.detail for event in store.list_space_events(space.id)[:2]] == [
        {"user": target.key, "role": ["admin", "member"]},
        {"user": target.key, "role": ["member", "admin"]},
    ]


def test_admin_unknown_id_removal_preserves_members_and_logs(service, store, space):
    baseline = store.list_space_events(space.id)
    with pytest.raises(TodoError, match="123456 还不是这个空间的成员"):
        service.set_admins(
            OWNER, AUTO, AssignOps(None, (), (Mention(user("123456"), ""),))
        )
    assert store.get_member(space.id, user("123456")) is None
    assert store.list_space_events(space.id) == baseline
