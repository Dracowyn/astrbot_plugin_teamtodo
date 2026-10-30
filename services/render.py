"""Plain-text rendering of tasks and spaces for chat replies.

CONTRACT (signatures and behaviour fixed by the main loop):

- Status labels: 待办 / 进行中 / 已完成 / 已归档. Priority: 高 / 中 / 低.
- ``nicknames`` maps ``UserRef.key`` -> display name; fall back to
  ``user_id`` when missing. Assignees render as ``@name``.
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
from datetime import tzinfo

from ..core.models import Space, Task


def format_due(due_at: float | None, now: float, tz: tzinfo) -> str:
    """Render a due time, or ``""`` when ``due_at`` is ``None``."""
    raise NotImplementedError


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
    raise NotImplementedError


def task_detail(
    task: Task,
    space_name: str,
    nicknames: Mapping[str, str],
    now: float,
    tz: tzinfo,
) -> str:
    """Multi-line detail including creator, created time and description."""
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


def spaces_list(rows: list[tuple[Space, bool, bool]]) -> str:
    """``(space, is_session_default, is_current_for_user)`` rows, one per
    line, marking ``（默认）`` and ``（当前）``."""
    raise NotImplementedError


def help_text() -> str:
    """Command summary shown by ``/todo`` and ``/todo help``."""
    raise NotImplementedError
