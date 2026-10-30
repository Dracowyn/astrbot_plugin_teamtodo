"""Behavioral coverage for the framework-independent chat renderer."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from astrbot_plugin_teamtodo.core.models import (
    Priority,
    Space,
    Task,
    TaskStatus,
    UserRef,
)
from astrbot_plugin_teamtodo.services.render import (
    format_due,
    help_text,
    mine_list,
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


def test_help_lists_every_m1_command_and_does_not_advertise_m4_share():
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
    assert "share" not in text.lower()
    assert "确认码" in text
    assert "!高" in text and "!中" in text and "!低" in text
    assert "@" in text and "标签" in text
