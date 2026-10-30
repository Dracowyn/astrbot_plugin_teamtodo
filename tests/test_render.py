"""Behavioral coverage for chat rendering and resilient WebUI event summaries."""

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

import pytest
from astrbot_plugin_teamtodo.core.models import (
    EventAction,
    Priority,
    Space,
    SpaceEvent,
    SpaceEventAction,
    SpaceRole,
    Task,
    TaskEvent,
    TaskStatus,
    UserRef,
)
from astrbot_plugin_teamtodo.services.render import (
    event_actor_name,
    event_summary,
    format_due,
    help_text,
    mine_list,
    reminder_text,
    spaces_list,
    task_detail,
    task_line,
    task_list,
)

UTC = timezone.utc
SHANGHAI = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 30, 12, tzinfo=UTC).timestamp()
CREATOR = UserRef(platform_id="qq-main", user_id="creator-id")
ALICE = UserRef(platform_id="qq-main", user_id="alice-id")
BOB = UserRef(platform_id="qq-main", user_id="bob-id")


def make_task(**changes) -> Task:
    task = Task(
        id=900,
        space_id=1,
        seq=3,
        title="发布页面",
        status=TaskStatus.TODO,
        assignees=(),
        due_at=None,
        tags=(),
        priority=None,
        description="",
        creator=CREATOR,
        created_session="qq-main:GroupMessage:123",
        created_at=NOW - 3600,
        updated_at=NOW,
        done_at=None,
        reminded=False,
    )
    return replace(task, **changes)


def test_format_due_absent_same_year_and_other_year():
    due = datetime(2026, 11, 5, 15, 59, tzinfo=UTC).timestamp()
    assert format_due(None, NOW, SHANGHAI) == ""
    assert format_due(due, NOW, SHANGHAI) == "11-05 23:59"
    assert format_due(due, NOW + 366 * 86400, SHANGHAI) == "2026-11-05 23:59"
    assert format_due(NOW - 60, NOW, SHANGHAI) == "10-30 19:59"
    assert "已逾期" not in format_due(NOW - 60, NOW, SHANGHAI)


@pytest.mark.parametrize(
    "tz, now, due, expected",
    [
        (
            SHANGHAI,
            datetime(2026, 12, 31, 20, tzinfo=UTC),
            datetime(2027, 1, 2, tzinfo=UTC),
            "01-02 08:00",
        ),
        (
            SHANGHAI,
            datetime(2026, 12, 31, 20, tzinfo=UTC),
            datetime(2026, 12, 31, 15, tzinfo=UTC),
            "2026-12-31 23:00",
        ),
        (
            timezone(timedelta(hours=-5)),
            datetime(2027, 1, 1, 1, tzinfo=UTC),
            datetime(2026, 12, 31, 23, tzinfo=UTC),
            "12-31 18:00",
        ),
    ],
)
def test_format_due_compares_both_years_in_configured_timezone(tz, now, due, expected):
    assert format_due(due.timestamp(), now.timestamp(), tz) == expected


def test_task_line_layout_fields_nickname_fallback_and_description_omission():
    due = datetime(2026, 11, 5, 15, 59, tzinfo=UTC).timestamp()
    task = make_task(
        status=TaskStatus.DOING,
        assignees=(ALICE, BOB),
        due_at=due,
        tags=("前端", "发布"),
        priority=Priority.HIGH,
        description="不要在列表中展示\n<b>原样的描述</b>",
    )
    nicknames = {ALICE.key: "张三", "another-adapter:bob-id": "错误昵称"}
    expected = "#3 [进行中] 发布页面 @张三 @bob-id 截止 11-05 23:59 !高 [前端] [发布]"
    assert task_line(task, nicknames, NOW, SHANGHAI) == expected
    assert (
        task_line(task, nicknames, NOW, SHANGHAI, space_name="研发")
        == "研发" + expected
    )
    assert "\n" not in task_line(task, nicknames, NOW, SHANGHAI)


@pytest.mark.parametrize(
    "status,label",
    [
        (TaskStatus.TODO, "待办"),
        (TaskStatus.DOING, "进行中"),
        (TaskStatus.DONE, "已完成"),
        (TaskStatus.ARCHIVED, "已归档"),
    ],
)
@pytest.mark.parametrize("due_offset", [-1, 0, 1, None])
def test_task_line_overdue_only_for_unfinished_tasks_before_cutoff(
    status, label, due_offset
):
    task = make_task(
        status=status, due_at=None if due_offset is None else NOW + due_offset
    )
    rendered = task_line(task, {}, NOW, UTC)
    assert rendered.startswith(f"#3 [{label}] 发布页面")
    overdue = due_offset == -1 and status in (TaskStatus.TODO, TaskStatus.DOING)
    assert ("（已逾期）" in rendered) is overdue
    assert ("截止 " in rendered) is (due_offset is not None)


@pytest.mark.parametrize(
    "priority,label",
    [(Priority.HIGH, "高"), (Priority.MEDIUM, "中"), (Priority.LOW, "低")],
)
def test_task_line_priority_labels(priority, label):
    assert task_line(make_task(priority=priority), {}, NOW, UTC).endswith(f" !{label}")


def test_task_line_optional_fields_do_not_leave_extra_spaces():
    assert task_line(make_task(), {}, NOW, UTC) == "#3 [待办] 发布页面"
    assert task_line(make_task(), {}, NOW, UTC, space_name="") == "#3 [待办] 发布页面"
    assert "截止 1970-01-01 00:00" in task_line(make_task(due_at=0), {}, NOW, UTC)


def test_task_detail_includes_creator_local_creation_time_and_verbatim_description():
    description = "第一行 **原始 Markdown**\n<b>不渲染 HTML</b>\nhttps://example.com\n  保留缩进\n"
    task = make_task(assignees=(ALICE,), description=description, due_at=NOW - 1)
    rendered = task_detail(
        task, "研发", {CREATOR.key: "创建者", ALICE.key: "张三"}, NOW, SHANGHAI
    )
    assert "研发#3" in rendered
    assert "待办" in rendered
    assert "发布页面" in rendered
    assert "@张三" in rendered
    assert "（已逾期）" in rendered
    assert "创建者" in rendered
    assert "2026-10-30 19:00" in rendered
    assert description in rendered
    assert task.description not in task_line(task, {}, NOW, SHANGHAI)
    assert "creator-id" not in rendered
    assert "900" not in rendered
    assert "qq-main:GroupMessage:123" not in rendered


def test_task_detail_creator_falls_back_to_user_id_and_empty_description_is_plain_text():
    rendered = task_detail(make_task(), "研发", {}, NOW, UTC)
    assert "creator-id" in rendered
    assert "2026-10-30 11:00" in rendered
    assert "#3 [待办] 发布页面" in rendered
    assert "None" not in rendered


def test_empty_nickname_falls_back_to_user_id():
    task = make_task(assignees=(ALICE,))
    nicknames = {ALICE.key: "", CREATOR.key: ""}
    assert task_line(task, nicknames, NOW, UTC) == "#3 [待办] 发布页面 @alice-id"
    assert "创建人：creator-id" in task_detail(task, "研发", nicknames, NOW, UTC)


def test_help_mine_line_mentions_group_scope():
    assert "/todo mine：查看指派给自己的任务（群聊只含本群空间）" in help_text()


def test_task_list_groups_by_status_and_sorts_each_group_without_mutating_input():
    tasks = [
        make_task(seq=8, status=TaskStatus.ARCHIVED),
        make_task(seq=9, status=TaskStatus.TODO),
        make_task(seq=6, status=TaskStatus.DONE),
        make_task(seq=2, status=TaskStatus.DOING),
        make_task(seq=1, status=TaskStatus.TODO),
    ]
    original = list(tasks)
    rendered = task_list("研发", tasks, {}, NOW, UTC)
    assert "研发" in rendered.splitlines()[0]
    lines = rendered.splitlines()
    task_lines = [line for line in lines if line.startswith("#")]
    assert task_lines == [task_line(tasks[i], {}, NOW, UTC) for i in (4, 1, 3, 2, 0)]
    for label in ("待办", "进行中", "已完成", "已归档"):
        assert any(label in line and not line.startswith("#") for line in lines)
    assert tasks == original
    assert "…还有" not in rendered


def test_task_list_omits_empty_status_groups_and_handles_empty_tasks():
    rendered = task_list("研发", [make_task(status=TaskStatus.DONE)], {}, NOW, UTC)
    assert "待办" not in rendered
    assert "进行中" not in rendered
    assert "已归档" not in rendered
    assert "已完成" in rendered
    empty = task_list("研发", [], {}, NOW, UTC)
    assert "暂无任务" in empty
    assert "…还有" not in empty


@pytest.mark.parametrize(
    "limit,seqs,remaining", [(2, [1, 3], 2), (0, [], 4), (4, [1, 3, 2, 4], 0)]
)
def test_task_list_truncates_task_lines_globally_across_groups(limit, seqs, remaining):
    tasks = [
        make_task(seq=4, status=TaskStatus.DONE),
        make_task(seq=3),
        make_task(seq=2, status=TaskStatus.DOING),
        make_task(seq=1),
    ]
    rendered = task_list("研发", tasks, {}, NOW, UTC, limit=limit)
    lines = rendered.splitlines()
    assert [int(line.split()[0][1:]) for line in lines if line.startswith("#")] == seqs
    if remaining:
        assert lines[-1] == f"…还有 {remaining} 条"
    else:
        assert "…还有" not in rendered


def test_mine_list_preserves_input_order_and_space_reference_prefixes():
    items = [
        ("乙空间", make_task(seq=9)),
        ("甲空间", make_task(seq=2, status=TaskStatus.DONE)),
    ]
    rendered = mine_list(items, {}, NOW, UTC)
    assert rendered.splitlines() == [
        task_line(task, {}, NOW, UTC, space_name=space) for space, task in items
    ]
    assert "没有指派给你的任务" in mine_list([], {}, NOW, UTC)


@pytest.mark.parametrize("limit,remaining", [(1, 2), (0, 3)])
def test_mine_list_truncates_in_given_order(limit, remaining):
    items = [("研发", make_task(seq=seq)) for seq in (9, 2, 7)]
    lines = mine_list(items, {}, NOW, UTC, limit=limit).splitlines()
    assert lines[:-1] == [
        task_line(task, {}, NOW, UTC, space_name=space) for space, task in items[:limit]
    ]
    assert lines[-1] == f"…还有 {remaining} 条"


def test_spaces_list_preserves_rows_and_marks_default_and_current_independently():
    rows = [
        (Space(id=1, name="研发", owner=CREATOR, created_at=NOW), True, False),
        (Space(id=2, name="运营", owner=None, created_at=NOW), False, True),
        (Space(id=3, name="设计", owner=None, created_at=NOW), True, True),
        (Space(id=4, name="其他", owner=None, created_at=NOW), False, False),
    ]
    assert spaces_list(rows).splitlines() == [
        "研发（默认）",
        "运营（当前）",
        "设计（默认）（当前）",
        "其他",
    ]
    assert spaces_list([]) == ""


def test_help_lists_task_space_and_share_commands():
    text = help_text()
    assert "/todo" in text
    for command in (
        "help",
        "add",
        "list",
        "all",
        "mine",
        "show",
        "start",
        "done",
        "reopen",
        "archive",
        "assign",
        "edit",
        "desc",
        "delete",
        "spaces",
        "use",
        "default",
        "space new",
        "space rename",
        "space delete",
        "invite",
        "join",
        "leave",
        "claim",
        "admin",
    ):
        assert command in text
    assert "/todo share [on|off|reset]" in text
    assert "确认码" in text
    assert "!高" in text and "!中" in text and "!低" in text
    assert "@" in text and "标签" in text


@pytest.mark.parametrize(
    "op,expected",
    [("on", "开启公开链接"), ("reset", "重置公开链接"), ("off", "关闭公开链接")],
)
def test_share_event_summary(op, expected):
    event = make_event(SpaceEventAction.SHARE, {"op": op}, space=True)
    assert event_summary(event, {}, UTC) == expected


@pytest.mark.parametrize(
    "detail", [{}, {"op": "unknown"}, {"op": []}, {"op": None}, [], None]
)
def test_share_event_summary_malformed_detail_falls_back(detail):
    event = make_event(SpaceEventAction.SHARE, detail, space=True)
    assert event_summary(event, {}, UTC) == str(SpaceEventAction.SHARE)


@pytest.mark.parametrize(
    "offset, expected",
    [
        (60, "将于 10-30 20:01 到期"),
        (0, "将于 10-30 20:00 到期"),
        (-60, "已于 10-30 19:59 逾期"),
    ],
)
def test_reminder_text_due_boundary(offset, expected):
    task = make_task(due_at=NOW + offset, assignees=(ALICE,))
    assert (
        reminder_text(task, "研发", NOW, SHANGHAI) == f"⏰ 研发#3 发布页面 {expected}"
    )


@pytest.mark.parametrize(
    "year, expected",
    [(2025, "已于 2025-11-05 23:59 逾期"), (2027, "将于 2027-11-05 23:59 到期")],
)
def test_reminder_text_other_year(year, expected):
    task = make_task(due_at=datetime(year, 11, 5, 15, 59, tzinfo=UTC).timestamp())
    assert (
        reminder_text(task, "研发", NOW, SHANGHAI) == f"⏰ 研发#3 发布页面 {expected}"
    )


def make_event(action, detail, *, space=False, **changes):
    fields = {
        "id": 1,
        "space_id": 1,
        "action": action,
        "detail": detail,
        "actor": ALICE,
        "actor_label": "",
        "session": "qq-main:GroupMessage:123",
        "created_at": NOW,
    }
    fields.update(changes)
    if space:
        return SpaceEvent(**fields)
    return TaskEvent(task_seq=3, **fields)


@pytest.mark.parametrize(
    "action,detail,expected",
    [
        (EventAction.CREATE, {"title": "写周报"}, "创建任务 #3「写周报」"),
        (EventAction.STATUS, {"status": ["todo", "doing"]}, "#3 状态：待办 → 进行中"),
        (
            EventAction.STATUS,
            {"status": [TaskStatus.DOING, TaskStatus.DONE]},
            "#3 状态：进行中 → 已完成",
        ),
        (
            EventAction.STATUS,
            {"status": ["archived", "todo"]},
            "#3 状态：已归档 → 待办",
        ),
        (EventAction.ARCHIVE, {"status": ["done", "archived"]}, "#3 归档"),
        (EventAction.ARCHIVE, {"auto": True}, "#3 自动归档"),
        (
            EventAction.ARCHIVE,
            {"status": ["done", "archived"], "auto": True},
            "#3 自动归档",
        ),
        (
            EventAction.DELETE,
            {"snapshot": {"title": "写周报", "description": "秘密描述"}},
            "删除任务 #3「写周报」",
        ),
        (
            EventAction.ASSIGN,
            {"assignees": [[ALICE.key], [ALICE.key, BOB.key]]},
            "#3 负责人：@张三 → @张三 @李四",
        ),
    ],
)
def test_task_event_summary_actions(action, detail, expected):
    names = {ALICE.key: "张三", BOB.key: "李四"}
    assert event_summary(make_event(action, detail), names, SHANGHAI) == expected


@pytest.mark.parametrize(
    "old,new,expected",
    [
        ([], ["qq-main:user:with:colons"], "#3 负责人：无 → @user:with:colons"),
        ([ALICE.key, BOB.key], [], "#3 负责人：@alice-id @bob-id → 无"),
    ],
)
def test_event_assignees_empty_lists_and_user_key_fallback(old, new, expected):
    event = make_event(EventAction.ASSIGN, {"assignees": [old, new]})
    assert (
        event_summary(event, {ALICE.key: "", "elsewhere:bob-id": "错误"}, UTC)
        == expected
    )


@pytest.mark.parametrize(
    "detail,expected",
    [
        ({"title": ["旧", "新"]}, "修改 #3：标题"),
        ({"due_at": [None, NOW]}, "修改 #3：截止时间"),
        ({"priority": [None, Priority.HIGH]}, "修改 #3：优先级"),
        ({"priority": ["medium", "low"]}, "修改 #3：优先级"),
        ({"priority": ["high", None]}, "修改 #3：优先级"),
        ({"tags": [[], ["发布"]]}, "修改 #3：标签"),
        ({"description": ["秘密旧描述", "秘密新描述"]}, "修改 #3：描述"),
        (
            {
                "title": ["旧", "新"],
                "due_at": [NOW, None],
                "priority": [Priority.HIGH, Priority.MEDIUM],
                "tags": [("旧",), ("新",)],
                "description": ["秘密旧描述", "秘密新描述"],
            },
            "修改 #3：标题、截止时间、优先级、标签、描述",
        ),
    ],
)
def test_update_event_lists_changed_fields_without_values_or_description(
    detail, expected
):
    rendered = event_summary(make_event(EventAction.UPDATE, detail), {}, SHANGHAI)
    assert rendered == expected
    assert "秘密" not in rendered


@pytest.mark.parametrize(
    "action,detail,expected",
    [
        (SpaceEventAction.CREATE, {"name": "研发"}, "创建空间「研发」"),
        (SpaceEventAction.RENAME, {"name": ["旧", "新"]}, "空间改名：旧 → 新"),
        (
            SpaceEventAction.BIND,
            {"session": "qq-main:GroupMessage:123"},
            "绑定会话：qq-main:GroupMessage:123",
        ),
        (
            SpaceEventAction.UNBIND,
            {"session": "qq-main:GroupMessage:123"},
            "解绑会话：qq-main:GroupMessage:123",
        ),
        (SpaceEventAction.CLAIM, {}, "认领空间"),
        (
            SpaceEventAction.ROLE,
            {"user": BOB.key, "role": ["member", "admin"]},
            "@李四：成员 → 管理员",
        ),
        (
            SpaceEventAction.ROLE,
            {"user": ALICE.key, "role": [SpaceRole.OWNER, SpaceRole.MEMBER]},
            "@张三：所有者 → 成员",
        ),
        (
            SpaceEventAction.INVITE,
            {"expires_at": datetime(2026, 11, 6, 10, tzinfo=UTC).timestamp()},
            "重置邀请码，有效期至 11-06 18:00",
        ),
    ],
)
def test_space_event_summary_actions(action, detail, expected):
    event = make_event(action, detail, space=True)
    assert (
        event_summary(event, {ALICE.key: "张三", BOB.key: "李四"}, SHANGHAI) == expected
    )


@pytest.mark.parametrize("added", [0, 1, 12])
@pytest.mark.parametrize(
    "session", ["qq-main:GroupMessage:123", "qq-main:GroupMessage:room:123"]
)
@pytest.mark.parametrize(
    "actor,actor_label,expected_actor",
    [(ALICE, "", "张三"), (None, "WebUI 管理员", "WebUI 管理员")],
)
def test_space_sync_log_preserves_session_count_and_actor(
    added, session, actor, actor_label, expected_actor
):
    event = make_event(
        SpaceEventAction.SYNC,
        {"session": session, "added": added},
        space=True,
        actor=actor,
        actor_label=actor_label,
        session=session if actor is not None else None,
    )
    summary = event_summary(event, {}, UTC)
    bind_summary = event_summary(
        make_event(SpaceEventAction.BIND, {"session": session}, space=True),
        {},
        UTC,
    )
    assert bind_summary[bind_summary.index("：") :] in summary
    assert str(added) in summary.replace(session, "")
    assert "同步" in summary
    assert event_actor_name(event, {ALICE.key: "张三"}) == expected_actor


@pytest.mark.parametrize(
    "detail",
    [
        {},
        {"session": "qq-main:GroupMessage:123"},
        {"added": 1},
        *({"session": session, "added": 1} for session in (None, [], {}, 1, True)),
        *(
            {"session": "qq-main:GroupMessage:123", "added": added}
            for added in (None, [], {}, "1", 1.5, True, False, -1)
        ),
    ],
)
def test_space_sync_malformed_detail_falls_back_to_action(detail):
    event = make_event(SpaceEventAction.SYNC, detail, space=True)
    assert event_summary(event, {}, UTC) == SpaceEventAction.SYNC.value


def test_space_role_user_key_fallback_splits_at_first_colon():
    event = make_event(
        SpaceEventAction.ROLE,
        {"user": "qq-main:user:with:colons", "role": ["admin", "member"]},
        space=True,
    )
    assert (
        event_summary(event, {"qq-main:user:with:colons": ""}, UTC)
        == "@user:with:colons：管理员 → 成员"
    )


@pytest.mark.parametrize(
    "created_at,expires_at,expected",
    [
        (
            datetime(2020, 1, 1, tzinfo=UTC).timestamp(),
            datetime(2020, 11, 6, 10, tzinfo=UTC).timestamp(),
            "重置邀请码，有效期至 11-06 18:00",
        ),
        (
            datetime(2020, 1, 1, tzinfo=UTC).timestamp(),
            datetime(2021, 11, 6, 10, tzinfo=UTC).timestamp(),
            "重置邀请码，有效期至 2021-11-06 18:00",
        ),
    ],
)
def test_event_due_format_uses_event_creation_time(created_at, expires_at, expected):
    event = make_event(
        SpaceEventAction.INVITE,
        {"expires_at": expires_at},
        space=True,
        created_at=created_at,
    )
    assert event_summary(event, {}, SHANGHAI) == expected


@pytest.mark.parametrize("action", list(EventAction))
@pytest.mark.parametrize("detail", [None, [], "broken", 7, True])
def test_every_task_action_rejects_non_dictionary_detail(action, detail):
    assert event_summary(make_event(action, detail), {}, UTC) == action.value


@pytest.mark.parametrize("action", list(SpaceEventAction))
@pytest.mark.parametrize("detail", [None, [], "broken", 7, True])
def test_every_space_action_rejects_non_dictionary_detail(action, detail):
    assert (
        event_summary(make_event(action, detail, space=True), {}, UTC) == action.value
    )


@pytest.mark.parametrize(
    "action,detail",
    [
        (EventAction.CREATE, {}),
        (EventAction.CREATE, {"title": []}),
        (EventAction.UPDATE, {}),
        (EventAction.UPDATE, {"unknown": ["old", "new"]}),
        (EventAction.UPDATE, {"title": "ab"}),
        (EventAction.UPDATE, {"title": ["old"]}),
        (EventAction.UPDATE, {"title": ["old", "new", "extra"]}),
        (EventAction.UPDATE, {"title": [[], "new"]}),
        (EventAction.UPDATE, {"description": [None, "text"]}),
        (EventAction.UPDATE, {"tags": ["tag", []]}),
        (EventAction.UPDATE, {"tags": [[{}], []]}),
        (EventAction.UPDATE, {"priority": [[], "high"]}),
        (EventAction.UPDATE, {"priority": ["invalid", "high"]}),
        (EventAction.UPDATE, {"due_at": ["tomorrow", None]}),
        (EventAction.UPDATE, {"due_at": [True, None]}),
        (EventAction.STATUS, {}),
        (EventAction.STATUS, {"status": "todo"}),
        (EventAction.STATUS, {"status": ["todo"]}),
        (EventAction.STATUS, {"status": [["todo"], "doing"]}),
        (EventAction.STATUS, {"status": ["todo", "invalid"]}),
        (EventAction.ASSIGN, {}),
        (EventAction.ASSIGN, {"assignees": ["qq-main:alice", []]}),
        (EventAction.ASSIGN, {"assignees": [[{}], []]}),
        (EventAction.ASSIGN, {"assignees": [["missing-platform"], []]}),
        (EventAction.ASSIGN, {"assignees": [[":missing-platform"], []]}),
        (EventAction.ASSIGN, {"assignees": [["qq-main:"], []]}),
        (EventAction.ARCHIVE, {}),
        (EventAction.ARCHIVE, {"auto": "true"}),
        (EventAction.ARCHIVE, {"auto": 1}),
        (EventAction.ARCHIVE, {"auto": True, "status": ["bad", "archived"]}),
        (EventAction.ARCHIVE, {"status": ["done", "todo"]}),
        (EventAction.DELETE, {}),
        (EventAction.DELETE, {"snapshot": []}),
        (EventAction.DELETE, {"snapshot": "secret description"}),
        (EventAction.DELETE, {"snapshot": {}}),
        (EventAction.DELETE, {"snapshot": {"title": None}}),
    ],
)
def test_malformed_task_detail_falls_back_to_action(action, detail):
    assert event_summary(make_event(action, detail), {}, UTC) == action.value


@pytest.mark.parametrize(
    "action,detail",
    [
        (SpaceEventAction.CREATE, {}),
        (SpaceEventAction.CREATE, {"name": {}}),
        (SpaceEventAction.RENAME, {"name": "ab"}),
        (SpaceEventAction.RENAME, {"name": ["old", None]}),
        (SpaceEventAction.BIND, {"session": []}),
        (SpaceEventAction.UNBIND, {}),
        (SpaceEventAction.ROLE, {"user": BOB.key, "role": ["member", "invalid"]}),
        (SpaceEventAction.ROLE, {"user": BOB.key, "role": [["member"], "admin"]}),
        (SpaceEventAction.ROLE, {"user": BOB.key, "role": ["member"]}),
        (SpaceEventAction.ROLE, {"user": [], "role": ["member", "admin"]}),
        (SpaceEventAction.ROLE, {"user": "unqualified", "role": ["member", "admin"]}),
        (SpaceEventAction.INVITE, {}),
        (SpaceEventAction.INVITE, {"expires_at": None}),
        (SpaceEventAction.INVITE, {"expires_at": "tomorrow"}),
        (SpaceEventAction.INVITE, {"expires_at": True}),
    ],
)
def test_malformed_space_detail_falls_back_to_action(action, detail):
    assert (
        event_summary(make_event(action, detail, space=True), {}, UTC) == action.value
    )


@pytest.mark.parametrize(
    "timestamp", [float("nan"), float("inf"), -float("inf"), 1e100, 10**1000]
)
def test_invalid_event_timestamps_never_raise(timestamp):
    update = make_event(EventAction.UPDATE, {"due_at": [None, timestamp]})
    invite = make_event(SpaceEventAction.INVITE, {"expires_at": timestamp}, space=True)
    created = make_event(
        SpaceEventAction.INVITE, {"expires_at": NOW}, space=True, created_at=timestamp
    )
    assert event_summary(update, {}, UTC) == "update"
    assert event_summary(invite, {}, UTC) == "invite"
    assert event_summary(created, {}, UTC) == "invite"


@pytest.mark.parametrize("created_at", [None, "today", True, [], {}])
def test_invalid_creation_timestamp_falls_back_for_event_due_time(created_at):
    event = make_event(
        SpaceEventAction.INVITE, {"expires_at": NOW}, space=True, created_at=created_at
    )
    assert event_summary(event, {}, UTC) == "invite"


def test_unknown_event_actions_fall_back_to_action_name():
    assert (
        event_summary(make_event("future-task-action", {}), {}, UTC)
        == "future-task-action"
    )
    assert (
        event_summary(make_event("future-space-action", {}, space=True), {}, UTC)
        == "future-space-action"
    )


@pytest.mark.parametrize("space", [False, True])
@pytest.mark.parametrize(
    "actor,actor_label,nicknames,expected",
    [
        (ALICE, "dashboard-name", {ALICE.key: "张三"}, "张三"),
        (ALICE, "dashboard-name", {}, "alice-id"),
        (ALICE, "", {ALICE.key: ""}, "alice-id"),
        (ALICE, "", {"different-adapter:alice-id": "错误昵称"}, "alice-id"),
        (
            UserRef(platform_id="qq-main", user_id="user:with:colons"),
            "",
            {},
            "user:with:colons",
        ),
        (None, "dashboard-name", {}, "dashboard-name"),
        (None, "", {}, "系统"),
    ],
)
def test_event_actor_identity_precedence(
    space, actor, actor_label, nicknames, expected
):
    action = SpaceEventAction.CLAIM if space else EventAction.ARCHIVE
    event = make_event(action, {}, space=space, actor=actor, actor_label=actor_label)
    assert event_actor_name(event, nicknames) == expected


def test_delete_summary_accepts_complete_service_snapshot_without_description():
    task = make_task(description="秘密描述\n第二行", assignees=(ALICE,))
    event = make_event(EventAction.DELETE, {"snapshot": asdict(task)})
    assert event_summary(event, {}, UTC) == "删除任务 #3「发布页面」"


def test_tuple_event_pairs_and_assignee_sequences_are_supported():
    event = make_event(EventAction.ASSIGN, {"assignees": ((ALICE.key,), (BOB.key,))})
    assert event_summary(event, {}, UTC) == "#3 负责人：@alice-id → @bob-id"
    status = make_event(
        EventAction.STATUS, {"status": (TaskStatus.DONE, TaskStatus.DOING)}
    )
    assert event_summary(status, {}, UTC) == "#3 状态：已完成 → 进行中"


def test_malformed_additional_field_prevents_partial_update_summary():
    event = make_event(
        EventAction.UPDATE,
        {"title": ["旧", "新"], "description": ["秘密", None]},
    )
    assert event_summary(event, {}, UTC) == "update"


def test_absent_space_role_user_and_unhashable_snapshot_title_fall_back():
    role = make_event(SpaceEventAction.ROLE, {"role": ["member", "admin"]}, space=True)
    snapshot = make_event(EventAction.DELETE, {"snapshot": {"title": []}})
    assert event_summary(role, {}, UTC) == "role"
    assert event_summary(snapshot, {}, UTC) == "delete"
