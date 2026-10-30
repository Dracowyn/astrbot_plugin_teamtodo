"""Operations behind the WebUI management page (design 7, M3).

Every caller is a logged-in dashboard user, i.e. an AstrBot administrator, so
there are no per-space permission checks here: authentication is AstrBot's
(``/api/plug/*``) and input validation of the HTTP layer is ``web_api.py``'s.
This module still enforces the ``core.models`` limits and the business rules
shared with ``TodoService`` (status/``done_at`` rules, ``reminded`` reset,
operation log), through the same shared task helpers.

WebUI writes are logged with ``actor=None``, ``actor_label=<username>`` and
``session=""``. Tasks created here have ``creator=UserRef(WEBUI_PLATFORM,
username)`` and ``created_session=""``.

Like ``TodoService``, each public method runs in one ``store.transaction()``
(reads may skip it) and raises ``TodoError`` with a Chinese user-facing
message; unknown ids raise ``NotFound`` (a ``TodoError``).
"""

from __future__ import annotations

import dataclasses as dcs
import time
from collections.abc import Callable
from datetime import tzinfo

from ..core.models import (
    Binding,
    Invite,
    Member,
    Priority,
    Space,
    SpaceEvent,
    SpaceRole,
    TaskEvent,
    TaskStatus,
    TodoError,
    UserRef,
)
from ..core.store import Store
from .todo_service import TaskListResult, TaskResult

MAX_EVENTS = 200
"""Upper bound for ``list_events(limit)``."""


class NotFound(TodoError):
    """The space or task does not exist (HTTP 404)."""


@dcs.dataclass(frozen=True)
class SpaceSummary:
    """One row of the space list."""

    space: Space
    binding_count: int
    member_count: int
    task_counts: dict[TaskStatus, int]
    """Every status present as a key (0 when none)."""
    overdue_count: int
    """TODO/DOING tasks with ``due_at < now``."""


@dcs.dataclass(frozen=True)
class SpaceDetail:
    """A space with its bindings, members and invite state."""

    summary: SpaceSummary
    bindings: list[tuple[Binding, str | None]]
    """``(binding, session title or None)`` in ``list_space_bindings`` order."""
    members: list[Member]
    """Owner first, then admins, then members; each group by ``joined_at``."""
    invite: Invite | None
    """The active invite if any and not expired."""


@dcs.dataclass(frozen=True)
class TaskPatch:
    """A WebUI edit. ``None`` leaves a field unchanged; lists replace whole.

    ``assignees`` must all be members of the space (``TodoError`` otherwise).
    """

    title: str | None = None
    description: str | None = None
    status: TaskStatus | None = None
    assignees: tuple[UserRef, ...] | None = None
    due_at: float | None = None
    clear_due: bool = False
    tags: tuple[str, ...] | None = None
    priority: Priority | None = None
    clear_priority: bool = False


LogEntry = TaskEvent | SpaceEvent


class AdminService:
    """Space and task management for dashboard users."""

    def __init__(
        self, store: Store, tz: tzinfo, *, clock: Callable[[], float] = time.time
    ) -> None:
        """Create the service.

        Args:
            store: Open store.
            tz: Configured time zone.
            clock: Wall-clock Unix seconds.
        """
        raise NotImplementedError

    def now(self) -> float:
        """Current Unix time in seconds."""
        raise NotImplementedError

    # ----- spaces -----

    def list_spaces(self) -> list[SpaceSummary]:
        """All spaces, bound or not, ordered by ``Space.id``."""
        raise NotImplementedError

    def get_space(self, space_id: int) -> SpaceDetail:
        """One space in detail. Raises ``NotFound``."""
        raise NotImplementedError

    def rename_space(self, username: str, space_id: int, name: str) -> Space:
        """Rename a space (same validation as ``/todo space rename``; the new
        name must not collide with another space bound to any session that
        this space is bound to). Logs RENAME. Raises ``NotFound``/``TodoError``.
        """
        raise NotImplementedError

    def delete_space(self, username: str, space_id: int) -> None:
        """Delete a space and everything under it (no confirmation code: the
        page asks before calling). Raises ``NotFound``."""
        raise NotImplementedError

    def unbind(self, username: str, space_id: int, session: str) -> None:
        """Unbind ``session`` from the space, with the same cleanup of the
        session default and current-space rows as ``/todo leave``. Logs
        UNBIND. Raises ``NotFound`` (space, or session not bound to it)."""
        raise NotImplementedError

    def set_role(
        self, username: str, space_id: int, user: UserRef, role: SpaceRole
    ) -> Member:
        """Make a member ADMIN or MEMBER. ``role`` OWNER and changing the
        owner's role raise ``TodoError``; same role is a no-op (no log).
        Logs ROLE. Raises ``NotFound`` (space or member)."""
        raise NotImplementedError

    def reset_invite(self, username: str, space_id: int) -> tuple[str, Invite]:
        """Create a new invite (old one invalid at once), same lifetime as
        ``/todo invite``. Returns the plain code, shown once. Logs INVITE."""
        raise NotImplementedError

    # ----- tasks -----

    def list_tasks(self, space_id: int, include_archived: bool) -> TaskListResult:
        """Tasks ordered by ``seq``; ARCHIVED only when asked. ``NotFound``."""
        raise NotImplementedError

    def get_task(self, space_id: int, seq: int) -> TaskResult:
        """One task. Raises ``NotFound``."""
        raise NotImplementedError

    def create_task(
        self,
        username: str,
        space_id: int,
        *,
        title: str,
        assignees: tuple[UserRef, ...] = (),
        due_at: float | None = None,
        tags: tuple[str, ...] = (),
        priority: Priority | None = None,
        description: str = "",
    ) -> TaskResult:
        """Create a task; assignees must be members. Logs CREATE.
        ``added_assignees`` in the result lists all assignees (no chat
        notification is sent from the WebUI)."""
        raise NotImplementedError

    def update_task(
        self, username: str, space_id: int, seq: int, patch: TaskPatch
    ) -> TaskResult:
        """Apply a patch atomically with the rules of ``TodoService.update_task``
        (status/``done_at``, ``reminded`` reset, one log entry per kind of
        change). An empty or no-op patch returns the task unchanged."""
        raise NotImplementedError

    def delete_task(self, username: str, space_id: int, seq: int) -> TaskResult:
        """Hard-delete with a snapshot DELETE event. Raises ``NotFound``."""
        raise NotImplementedError

    # ----- log -----

    def list_events(
        self, space_id: int, limit: int = 50, task_seq: int | None = None
    ) -> tuple[list[LogEntry], dict[str, str]]:
        """Task and space log entries merged newest first (``created_at DESC``;
        ties: higher id first, task entries before space entries), at most
        ``min(limit, MAX_EVENTS)``. With ``task_seq`` only that task's
        entries. Also returns member nicknames (``UserRef.key`` -> name) for
        rendering. Raises ``NotFound``."""
        raise NotImplementedError
