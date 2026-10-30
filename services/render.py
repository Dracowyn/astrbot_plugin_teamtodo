"""Plain-text rendering of tasks and spaces for chat replies.

CONTRACT (signatures and behaviour fixed by the main loop):

- Status labels: 待办 / 进行中 / 已完成 / 已归档. Priority: 高 / 中 / 低.
- ``nicknames`` maps ``UserRef.key`` -> display name; fall back to
  ``user_id`` when missing or empty. Assignees render as ``@name``.
- A task is overdue when ``due_at < now`` and its status is TODO or DOING.
- Due dates render in ``tz`` as ``MM-DD HH:MM`` (``YYYY-MM-DD HH:MM`` when
  not in the current year); overdue appends ``（已逾期）``.
- Output is plain text only (no Markdown, no HTML). Multi-line descriptions
  are rendered as-is in ``task_detail`` and omitted in one-line views.
- Long lists are truncated to ``limit`` task lines with a trailing
  ``…还有 N 条`` line.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, tzinfo

from ..core.models import Priority, Space, SpaceEvent, Task, TaskEvent, TaskStatus

_STATUS_LABELS = {
    TaskStatus.TODO: "待办",
    TaskStatus.DOING: "进行中",
    TaskStatus.DONE: "已完成",
    TaskStatus.ARCHIVED: "已归档",
}
_PRIORITY_LABELS = {
    Priority.HIGH: "高",
    Priority.MEDIUM: "中",
    Priority.LOW: "低",
}


def format_due(due_at: float | None, now: float, tz: tzinfo) -> str:
    """Render a due time, or ``""`` when ``due_at`` is ``None``."""
    if due_at is None:
        return ""
    due = datetime.fromtimestamp(due_at, tz)
    current = datetime.fromtimestamp(now, tz)
    pattern = "%m-%d %H:%M" if due.year == current.year else "%Y-%m-%d %H:%M"
    return due.strftime(pattern)


def task_line(
    task: Task,
    nicknames: Mapping[str, str],
    now: float,
    tz: tzinfo,
    *,
    space_name: str | None = None,
) -> str:
    """One line: ``#3 [进行中] 标题 @张三 @李四 截止 11-05 23:59 !高 [前端]``.

    When ``space_name`` is given the ref is written ``空间名#3``.
    """
    reference = f"{space_name if space_name is not None else ''}#{task.seq}"
    parts = [reference, f"[{_STATUS_LABELS[task.status]}]", task.title]
    parts.extend(
        f"@{nicknames.get(user.key) or user.user_id}" for user in task.assignees
    )
    if task.due_at is not None:
        due = format_due(task.due_at, now, tz)
        if task.due_at < now and task.status in (TaskStatus.TODO, TaskStatus.DOING):
            due += "（已逾期）"
        parts.append(f"截止 {due}")
    if task.priority is not None:
        parts.append(f"!{_PRIORITY_LABELS[task.priority]}")
    parts.extend(f"[{tag}]" for tag in task.tags)
    return " ".join(parts)


def reminder_text(task: Task, space_name: str, now: float, tz: tzinfo) -> str:
    """Body of a due reminder (M2); the caller prepends the @ mentions.

    ``⏰ 空间名#3 标题 将于 11-05 23:59 到期`` when ``due_at >= now``, otherwise
    ``⏰ 空间名#3 标题 已于 11-05 23:59 逾期``. Due time uses ``format_due``.
    """
    due = format_due(task.due_at, now, tz)
    if task.due_at >= now:
        timing = f"将于 {due} 到期"
    else:
        timing = f"已于 {due} 逾期"
    return f"⏰ {space_name}#{task.seq} {task.title} {timing}"


def event_summary(
    event: TaskEvent | SpaceEvent, nicknames: Mapping[str, str], tz: tzinfo
) -> str:
    """One Chinese line describing a log entry for the WebUI (M3).

    Users are shown as ``@nickname`` (``user_id`` fallback, like
    ``task_line``); times with ``format_due``; statuses and priorities with
    the labels above; field names as 标题 / 截止时间 / 优先级 / 标签 / 描述.
    Must handle every ``EventAction`` and ``SpaceEventAction`` with the
    ``detail`` shapes documented in ``core/models.py`` and written by the
    services (incl. auto-archive ``{"auto": True}`` and DELETE snapshots),
    and must not raise on an unexpected ``detail`` (fall back to the action
    name). Examples: ``创建任务 #3「写周报」``, ``#3 状态：待办 → 进行中``,
    ``#3 负责人：@张三 → @张三 @李四``, ``修改 #3：标题、截止时间``,
    ``#3 自动归档``, ``删除任务 #3「写周报」``, ``空间改名：旧 → 新``,
    ``@李四：成员 → 管理员``, ``重置邀请码，有效期至 11-06 18:00``.
    Description texts are never included (they may be long).
    """
    raise NotImplementedError


def event_actor_name(
    event: TaskEvent | SpaceEvent, nicknames: Mapping[str, str]
) -> str:
    """Who did it: member nickname (``user_id`` fallback) for chat users,
    ``actor_label`` for WebUI users, ``系统`` for automatic actions (M3)."""
    raise NotImplementedError


def task_detail(
    task: Task,
    space_name: str,
    nicknames: Mapping[str, str],
    now: float,
    tz: tzinfo,
) -> str:
    """Multi-line detail including creator, created time and description."""
    created = datetime.fromtimestamp(task.created_at, tz).strftime("%Y-%m-%d %H:%M")
    creator = nicknames.get(task.creator.key) or task.creator.user_id
    lines = [
        task_line(task, nicknames, now, tz, space_name=space_name),
        f"创建人：{creator}",
        f"创建时间：{created}",
    ]
    if task.description:
        lines.extend(("描述：", task.description))
    return "\n".join(lines)


def task_list(
    space_name: str,
    tasks: list[Task],
    nicknames: Mapping[str, str],
    now: float,
    tz: tzinfo,
    *,
    limit: int = 50,
) -> str:
    """Header with the space name, then tasks grouped by status in the order
    待办, 进行中, 已完成, 已归档 (empty groups omitted), each group ordered by
    ``seq``. ``暂无任务`` when ``tasks`` is empty."""
    if not tasks:
        return f"{space_name}\n暂无任务"
    lines = [space_name]
    remaining = max(0, limit)
    shown = 0
    for status, label in _STATUS_LABELS.items():
        group = sorted(
            (task for task in tasks if task.status == status), key=lambda task: task.seq
        )
        visible = group[:remaining]
        if visible:
            lines.append(label)
            lines.extend(task_line(task, nicknames, now, tz) for task in visible)
            shown += len(visible)
            remaining -= len(visible)
    if shown < len(tasks):
        lines.append(f"…还有 {len(tasks) - shown} 条")
    return "\n".join(lines)


def mine_list(
    items: list[tuple[str, Task]],
    nicknames: Mapping[str, str],
    now: float,
    tz: tzinfo,
    *,
    limit: int = 50,
) -> str:
    """``(space_name, task)`` pairs in the given order, each rendered with
    ``task_line(..., space_name=...)``. ``没有指派给你的任务`` when empty."""
    if not items:
        return "没有指派给你的任务"
    visible = items[: max(0, limit)]
    lines = [
        task_line(task, nicknames, now, tz, space_name=space_name)
        for space_name, task in visible
    ]
    if len(visible) < len(items):
        lines.append(f"…还有 {len(items) - len(visible)} 条")
    return "\n".join(lines)


def spaces_list(rows: list[tuple[Space, bool, bool]]) -> str:
    """``(space, is_session_default, is_current_for_user)`` rows, one per
    line, marking ``（默认）`` and ``（当前）``."""
    return "\n".join(
        space.name
        + ("（默认）" if is_default else "")
        + ("（当前）" if is_current else "")
        for space, is_default, is_current in rows
    )


def help_text() -> str:
    """Command summary shown by ``/todo`` and ``/todo help``."""
    return "\n".join(
        (
            "任务命令（以 /todo 开头）：",
            "/todo 或 /todo help：显示帮助",
            "/todo add <字段…>：在当前空间创建任务",
            "/todo list [空间名] [all]：列出任务，all 包含已归档",
            "/todo mine：查看指派给自己的任务（群聊只含本群空间）",
            "/todo show <ref>：查看详情",
            "/todo start <ref>：改为进行中",
            "/todo done <ref>：改为已完成",
            "/todo reopen <ref>：改为待办",
            "/todo archive <ref>：改为已归档",
            "/todo assign <ref> [@a @b]：整体替换负责人，留空清除",
            "/todo assign <ref> +@a -@b：增减负责人",
            "/todo edit <ref> <字段…>：修改标题、截止时间、优先级、标签",
            "/todo desc <ref> [全文]：设置描述，留空清除",
            "/todo delete <ref>：删除任务",
            "ref 写作 #3、3 或 空间名#3；只能引用本会话绑定的空间。",
            "字段：标题、@负责人、日期 [HH:MM]、!高 / !中 / !低、[标签]。",
            "edit 可用 无截止、!无、[-标签] 清除或移除字段。双引号内按字面理解。",
            "日期：YYYY-MM-DD、MM-DD、今天、明天、后天、周一到周日（下一个）；默认 23:59。",
            "空间与会话命令：",
            "/todo spaces：列出本会话绑定的空间，标记默认和当前空间",
            "/todo use <空间名>：切换自己的当前空间",
            "/todo default <空间名>：修改会话默认空间",
            "/todo space new <名字>：新建、绑定空间并切换为自己的当前空间",
            "/todo space rename <新名字>：重命名当前空间",
            "/todo space delete <空间名> [确认码]：删除空间，需在 60 秒内二次确认",
            "/todo invite：重置当前空间邀请码，旧码失效",
            "/todo join <邀请码>：将本会话绑定到对应空间",
            "/todo leave <空间名>：解绑本会话与空间",
            "/todo claim：认领当前无主空间",
            "/todo admin +@x / -@x：任命或撤销当前空间管理员",
        )
    )
