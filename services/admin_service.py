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

Space detail hides expired invites without deleting them. Task patches replace
ordered assignee/tag collections atomically; dashboard assignees must already
be members. Logs preserve deleted task snapshots and merge both event kinds.

Like ``TodoService``, each public method runs in one ``store.transaction()``
(reads may skip it) and raises ``TodoError`` with a Chinese user-facing
message; unknown ids raise ``NotFound`` (a ``TodoError``).
"""

from __future__ import annotations

import dataclasses as dcs
import time
from collections.abc import Callable, Sequence
from datetime import tzinfo

from ..core.models import (
    WEBUI_PLATFORM,
    Binding,
    EventAction,
    GroupMember,
    Invite,
    Member,
    Priority,
    Space,
    SpaceEvent,
    SpaceEventAction,
    SpaceEventDraft,
    SpaceRole,
    Task,
    TaskDraft,
    TaskEvent,
    TaskStatus,
    TodoError,
    UserRef,
)
from ..core.store import Store
from . import task_ops
from .access import is_invite_expired, new_invite
from .todo_service import TaskListResult, TaskResult, TodoService

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
        self._store = store
        self.tz = tz
        self._clock = clock

    def now(self) -> float:
        """Current Unix time in seconds."""
        return self._clock()

    # ----- spaces -----

    def list_spaces(self) -> list[SpaceSummary]:
        """All spaces, bound or not, ordered by ``Space.id``."""
        now = self.now()
        return [self._summary(space, now) for space in self._store.list_spaces()]

    def get_space(self, space_id: int) -> SpaceDetail:
        """One space in detail. Raises ``NotFound``."""
        space = self._space(space_id)
        now = self.now()
        bindings = self._store.list_space_bindings(space_id)
        titles = self._store.get_session_titles([b.session for b in bindings])
        ranks = {SpaceRole.OWNER: 0, SpaceRole.ADMIN: 1, SpaceRole.MEMBER: 2}
        members = sorted(
            self._store.list_members(space_id),
            key=lambda member: (ranks[member.role], member.joined_at),
        )
        invite = self._store.get_invite(space_id)
        if invite is not None and is_invite_expired(invite, now):
            invite = None
        return SpaceDetail(
            self._summary(space, now),
            [(binding, titles.get(binding.session)) for binding in bindings],
            members,
            invite,
        )

    def rename_space(self, username: str, space_id: int, name: str) -> Space:
        """Rename a space (same validation as ``/todo space rename``; the new
        name must not collide with another space bound to any session that
        this space is bound to). Logs RENAME. Raises ``NotFound``/``TodoError``.
        """
        with self._store.transaction():
            space = self._space(space_id)
            name = TodoService._check_space_name(name)
            for binding in self._store.list_space_bindings(space_id):
                existing = self._store.session_space_names(binding.session).get(name)
                if existing is not None and existing != space_id:
                    raise TodoError("绑定会话中已有同名空间")
            if name == space.name:
                return space
            updated = self._store.rename_space(space_id, name)
            self._log_space(
                username,
                space_id,
                SpaceEventAction.RENAME,
                {"name": [space.name, name]},
            )
            return updated

    def delete_space(self, username: str, space_id: int) -> None:
        """Delete a space and everything under it (no confirmation code: the
        page asks before calling). Raises ``NotFound``."""
        with self._store.transaction():
            self._space(space_id)
            self._store.delete_space(space_id)

    def unbind(self, username: str, space_id: int, session: str) -> None:
        """Unbind ``session`` from the space, with the same cleanup of the
        session default and current-space rows as ``/todo leave``. Logs
        UNBIND. Raises ``NotFound`` (space, or session not bound to it)."""
        with self._store.transaction():
            self._space(space_id)
            if not any(
                b.session == session for b in self._store.list_space_bindings(space_id)
            ):
                raise NotFound("会话未绑定到此空间")
            self._store.unbind(session, space_id)
            self._log_space(
                username, space_id, SpaceEventAction.UNBIND, {"session": session}
            )

    def set_role(
        self, username: str, space_id: int, user: UserRef, role: SpaceRole
    ) -> Member:
        """Make a member ADMIN or MEMBER. ``role`` OWNER and changing the
        owner's role raise ``TodoError``; same role is a no-op (no log).
        Logs ROLE. Raises ``NotFound`` (space or member)."""
        with self._store.transaction():
            self._space(space_id)
            member = self._store.get_member(space_id, user)
            if member is None:
                raise NotFound("空间成员不存在")
            if role == SpaceRole.OWNER or member.role == SpaceRole.OWNER:
                raise TodoError("不能在管理页修改所有者角色")
            if role == member.role:
                return member
            updated = self._store.set_member_role(space_id, user, role)
            self._log_space(
                username,
                space_id,
                SpaceEventAction.ROLE,
                {"user": user.key, "role": [member.role, role]},
            )
            return updated

    def sync_members(
        self,
        username: str,
        space_id: int,
        session: str,
        members: Sequence[GroupMember],
    ) -> int:
        """Register the members of one bound group session (M3.1).

        Each member is upserted (new ones join as MEMBER; existing ones only
        get their nickname refreshed, roles never change); members missing
        from the list are kept. Logs one SYNC ``{"session", "added"}`` even
        when nothing was added.

        Returns:
            How many members were new to the space.

        Raises:
            NotFound: Unknown space, or ``session`` is not bound to it.
            TodoError: A member's ``platform_id`` differs from the session's.
        """
        with self._store.transaction():
            self._space(space_id)
            if not any(
                b.session == session for b in self._store.list_space_bindings(space_id)
            ):
                raise NotFound("会话未绑定到此空间")
            platform_id = session.split(":", 1)[0]
            added = 0
            for member in members:
                if member.user.platform_id != platform_id:
                    raise TodoError("成员平台与会话平台不一致")
                if self._store.get_member(space_id, member.user) is None:
                    added += 1
                self._store.upsert_member(space_id, member.user, member.nickname)
            self._log_space(
                username,
                space_id,
                SpaceEventAction.SYNC,
                {"session": session, "added": added},
            )
            return added

    def reset_invite(self, username: str, space_id: int) -> tuple[str, Invite]:
        """Create a new invite (old one invalid at once), same lifetime as
        ``/todo invite``. Returns the plain code, shown once. Logs INVITE."""
        with self._store.transaction():
            self._space(space_id)
            code, invite = new_invite(space_id, self.now())
            invite = self._store.set_invite(
                space_id, invite.code_hash, invite.expires_at
            )
            self._log_space(
                username,
                space_id,
                SpaceEventAction.INVITE,
                {"expires_at": invite.expires_at},
            )
            return code, invite

    # ----- tasks -----

    def list_tasks(self, space_id: int, include_archived: bool) -> TaskListResult:
        """Tasks ordered by ``seq``; ARCHIVED only when asked. ``NotFound``."""
        space = self._space(space_id)
        statuses = tuple(
            s for s in TaskStatus if include_archived or s != TaskStatus.ARCHIVED
        )
        return TaskListResult(
            space, self._store.list_tasks(space_id, statuses), self._nicknames(space_id)
        )

    def get_task(self, space_id: int, seq: int) -> TaskResult:
        """One task. Raises ``NotFound``."""
        space = self._space(space_id)
        task = self._task(space_id, seq)
        return TaskResult(space, task, self._nicknames(space_id))

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
        with self._store.transaction():
            space = self._space(space_id)
            assignees = tuple(dict.fromkeys(assignees))
            for user in assignees:
                if self._store.get_member(space_id, user) is None:
                    raise TodoError("负责人必须是空间成员")
            draft = TaskDraft(
                space_id=space_id,
                title=title,
                creator=UserRef(WEBUI_PLATFORM, username),
                created_session="",
                assignees=assignees,
                due_at=due_at,
                tags=tuple(dict.fromkeys(tags)),
                priority=priority,
                description=description.strip(),
            )
            task_ops.check_task(draft)
            task = self._store.create_task(draft)
            task_ops.log_event(
                self._store,
                task,
                EventAction.CREATE,
                {"title": task.title},
                actor=None,
                actor_label=username,
                session="",
            )
            return TaskResult(space, task, self._nicknames(space_id), task.assignees)

    def update_task(
        self, username: str, space_id: int, seq: int, patch: TaskPatch
    ) -> TaskResult:
        """Apply a patch atomically with the rules of ``TodoService.update_task``
        (status/``done_at``, ``reminded`` reset, one log entry per kind of
        change). An empty or no-op patch returns the task unchanged."""
        with self._store.transaction():
            space = self._space(space_id)
            task = self._task(space_id, seq)
            assignees = task.assignees
            if patch.assignees is not None:
                assignees = tuple(dict.fromkeys(patch.assignees))
                for user in assignees:
                    if self._store.get_member(space_id, user) is None:
                        raise TodoError("负责人必须是空间成员")
            proposed = dcs.replace(
                task,
                title=task.title if patch.title is None else patch.title,
                description=task.description
                if patch.description is None
                else patch.description.strip(),
                status=task.status if patch.status is None else patch.status,
                assignees=assignees,
                due_at=None
                if patch.clear_due
                else task.due_at
                if patch.due_at is None
                else patch.due_at,
                tags=task.tags
                if patch.tags is None
                else tuple(dict.fromkeys(patch.tags)),
                priority=None
                if patch.clear_priority
                else task.priority
                if patch.priority is None
                else patch.priority,
            )
            saved = task_ops.apply(
                self._store,
                self._clock,
                task,
                proposed,
                actor=None,
                actor_label=username,
                session="",
            )
            added = tuple(user for user in assignees if user not in task.assignees)
            return TaskResult(space, saved or task, self._nicknames(space_id), added)

    def delete_task(self, username: str, space_id: int, seq: int) -> TaskResult:
        """Hard-delete with a snapshot DELETE event. Raises ``NotFound``."""
        with self._store.transaction():
            space = self._space(space_id)
            task = self._task(space_id, seq)
            task_ops.log_event(
                self._store,
                task,
                EventAction.DELETE,
                {"snapshot": dcs.asdict(task)},
                actor=None,
                actor_label=username,
                session="",
            )
            self._store.delete_task(space_id, seq)
            return TaskResult(space, task, self._nicknames(space_id))

    # ----- log -----

    def list_events(
        self, space_id: int, limit: int = 50, task_seq: int | None = None
    ) -> tuple[list[LogEntry], dict[str, str]]:
        """Task and space log entries merged newest first (``created_at DESC``;
        ties: higher id first, task entries before space entries), at most
        ``min(limit, MAX_EVENTS)``. With ``task_seq`` only that task's
        entries. Also returns member nicknames (``UserRef.key`` -> name) for
        rendering. Raises ``NotFound``."""
        self._space(space_id)
        limit = max(0, min(limit, MAX_EVENTS))
        entries: list[LogEntry] = list(
            self._store.list_events(space_id, task_seq, limit)
        )
        if task_seq is None:
            entries.extend(self._store.list_space_events(space_id, limit))
        entries.sort(
            key=lambda event: (
                event.created_at,
                event.id,
                isinstance(event, TaskEvent),
            ),
            reverse=True,
        )
        return entries[:limit], self._nicknames(space_id)

    def _space(self, space_id: int) -> Space:
        space = self._store.get_space(space_id)
        if space is None:
            raise NotFound("空间不存在")
        return space

    def _task(self, space_id: int, seq: int) -> Task:
        task = self._store.get_task(space_id, seq)
        if task is None:
            raise NotFound("任务不存在")
        return task

    def _nicknames(self, space_id: int) -> dict[str, str]:
        return {
            m.user.key: m.nickname
            for m in self._store.list_members(space_id)
            if m.nickname
        }

    def _summary(self, space: Space, now: float) -> SpaceSummary:
        tasks = self._store.list_tasks(space.id, tuple(TaskStatus))
        counts = dict.fromkeys(TaskStatus, 0)
        overdue = 0
        for task in tasks:
            counts[task.status] += 1
            if (
                task.status in (TaskStatus.TODO, TaskStatus.DOING)
                and task.due_at is not None
                and task.due_at < now
            ):
                overdue += 1
        return SpaceSummary(
            space,
            len(self._store.list_space_bindings(space.id)),
            len(self._store.list_members(space.id)),
            counts,
            overdue,
        )

    def _log_space(
        self, username: str, space_id: int, action: SpaceEventAction, detail: dict
    ) -> None:
        self._store.add_space_event(
            SpaceEventDraft(
                space_id,
                action,
                detail,
                actor=None,
                actor_label=username,
                session="",
            )
        )
