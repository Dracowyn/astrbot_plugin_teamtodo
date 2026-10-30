"""Business operations behind the ``/todo`` commands.

Every public method runs read -> permission check -> write (+ operation log)
inside one ``store.transaction()`` and never awaits, so concurrent commands
cannot interleave between a check and its write. Failures raise ``TodoError``
with a user-facing Chinese message; the transaction is rolled back.

Space resolution (design 2.2): an explicit space name must be bound to the
actor's session. Otherwise the actor's current space is used, falling back to
the session default, then to the earliest remaining binding, and finally to a
newly auto-created space.
"""

from __future__ import annotations

import dataclasses as dcs
import itertools
import re
import time
from collections.abc import Callable, Sequence
from datetime import datetime, tzinfo

from ..core import permissions as perms
from ..core.models import (
    MAX_SPACE_NAME_CHARS,
    Actor,
    EventAction,
    GroupMember,
    Invite,
    Member,
    Mention,
    Priority,
    Space,
    SpaceEventAction,
    SpaceEventDraft,
    Task,
    TaskDraft,
    TaskStatus,
    TodoError,
    UserRef,
)
from ..core.parsing import AssignOps, TaskFields, TaskRef
from ..core.store import Store
from . import task_ops
from .access import (
    ConfirmationCodes,
    JoinRateLimiter,
    hash_invite_code,
    is_invite_expired,
    new_invite,
)

PRIVATE_SPACE_NAME = "我的任务"
MINE_STATUSES = (TaskStatus.TODO, TaskStatus.DOING)
CONFIRM_MISMATCH = "确认码错误或已过期，请重新执行命令获取新的确认码"
_NAME_BREAKERS = re.compile(r"[\s#＃]+")
"""Characters that would break ``空间名#3`` refs and single-token name args."""


def auto_space_name(is_private: bool, group_name: str | None, group_id: str) -> str:
    """Return the sanitized auto-space base name (design 2.1).

    Private chats use 我的任务. Groups use group_name or 群<last 4 digits>.
    Whitespace and # are removed; creation caps the final name's length.
    """
    if is_private:
        return PRIVATE_SPACE_NAME
    name = _NAME_BREAKERS.sub("", group_name or "")
    return name or f"群{group_id[-4:]}"


@dcs.dataclass(frozen=True)
class TaskResult:
    """A task with its space and the display names needed to render it."""

    space: Space
    task: Task
    nicknames: dict[str, str]
    added_assignees: tuple[UserRef, ...] = ()
    """Users that became assignees in this operation (for notifications)."""


@dcs.dataclass(frozen=True)
class TaskUpdate:
    """A combined task change; every field left at its default is kept.

    ``title``, ``due_at``, ``priority``, ``description`` and ``status`` replace
    the current value when not ``None``; ``clear_due`` / ``clear_priority``
    win over the corresponding value. An empty ``description`` clears it.
    """

    status: TaskStatus | None = None
    title: str | None = None
    due_at: float | None = None
    clear_due: bool = False
    priority: Priority | None = None
    clear_priority: bool = False
    add_tags: tuple[str, ...] = ()
    remove_tags: tuple[str, ...] = ()
    description: str | None = None
    add_assignees: tuple[Mention, ...] = ()
    """They become members of the space (as in ``assign``), then assignees."""
    remove_assignees: tuple[UserRef, ...] = ()


@dcs.dataclass(frozen=True)
class TaskListResult:
    """Tasks of one space with display names."""

    space: Space
    tasks: list[Task]
    nicknames: dict[str, str]


class TodoService:
    """Orchestrates store, permissions and access helpers for one plugin."""

    def __init__(
        self,
        store: Store,
        tz: tzinfo,
        *,
        clock: Callable[[], float] = time.time,
        join_limiter: JoinRateLimiter | None = None,
        confirmations: ConfirmationCodes | None = None,
    ) -> None:
        """Create the service.

        Args:
            store: Open store.
            tz: Configured time zone for date parsing and rendering.
            clock: Wall-clock Unix seconds.
            join_limiter: Join failure limiter (a fresh one by default).
            confirmations: Confirmation code store (a fresh one by default).
        """
        self._store = store
        self.tz = tz
        self._clock = clock
        self._joins = join_limiter or JoinRateLimiter()
        self._confirms = confirmations or ConfirmationCodes()

    def now(self) -> float:
        """Current Unix time in seconds."""
        return self._clock()

    def local_now(self) -> datetime:
        """Current time as an aware datetime in the configured zone."""
        return datetime.fromtimestamp(self._clock(), self.tz)

    # ----- internal helpers (each used by many operations) -----

    def _bound_space(self, session: str, name: str) -> Space:
        space_id = self._store.session_space_names(session).get(name)
        if space_id is None:
            raise TodoError(f"本会话没有绑定名为「{name}」的空间")
        return self._store.get_space(space_id)

    def _current_space(self, actor: Actor, auto_name: str) -> Space:
        session = actor.session
        bound = [b.space_id for b in self._store.list_bindings(session)]
        for space_id in (
            self._store.get_current(session, actor.user),
            self._store.get_session_default(session),
        ):
            if space_id in bound:
                return self._store.get_space(space_id)
        if bound:
            self._store.set_session_default(session, bound[0])
            return self._store.get_space(bound[0])
        owner = actor.user if actor.is_private or actor.is_session_admin else None
        space = self._store.create_space(self._unique_name(session, auto_name), owner)
        self._store.bind(session, space.id)
        self._store.set_session_default(session, space.id)
        self._space_log(actor, space, SpaceEventAction.CREATE, {"name": space.name})
        self._space_log(actor, space, SpaceEventAction.BIND, {"session": session})
        return space

    def _space(
        self, actor: Actor, auto_name: str, name: str | None
    ) -> tuple[Space, Member]:
        """Resolve the target space and upsert the actor as a member."""
        if name:
            space = self._bound_space(actor.session, name)
        else:
            space = self._current_space(actor, auto_name)
        member = self._store.upsert_member(space.id, actor.user, actor.nickname)
        return space, member

    def _task(
        self, actor: Actor, auto_name: str, ref: TaskRef
    ) -> tuple[Space, Member, Task]:
        space, member = self._space(actor, auto_name, ref.space_name)
        task = self._store.get_task(space.id, ref.seq)
        if task is None:
            raise TodoError(f"空间「{space.name}」里没有任务 #{ref.seq}")
        return space, member, task

    def _unique_name(self, session: str, base: str) -> str:
        """First of ``base``, ``base-2``, ... unused in ``session``."""
        taken = self._store.session_space_names(session)
        for index in itertools.count(1):
            suffix = "" if index == 1 else f"-{index}"
            candidate = base[: MAX_SPACE_NAME_CHARS - len(suffix)] + suffix
            if candidate not in taken:
                return candidate
        raise AssertionError("unreachable")  # pragma: no cover

    def _result(
        self, space: Space, task: Task, added: tuple[UserRef, ...] = ()
    ) -> TaskResult:
        return TaskResult(
            space=space,
            task=task,
            nicknames=self._nicknames(space.id),
            added_assignees=added,
        )

    def _nicknames(self, space_id: int) -> dict[str, str]:
        # Empty nicknames (e.g. a mention without a name) fall back to user_id
        # in rendering.
        return {
            m.user.key: m.nickname
            for m in self._store.list_members(space_id)
            if m.nickname
        }

    def _log(self, actor: Actor, task: Task, action: EventAction, detail: dict) -> None:
        task_ops.log_event(
            self._store,
            task,
            action,
            detail,
            actor=actor.user,
            actor_label="",
            session=actor.session,
        )

    def _space_log(
        self, actor: Actor, space: Space, action: SpaceEventAction, detail: dict
    ) -> None:
        self._store.add_space_event(
            SpaceEventDraft(
                space_id=space.id,
                action=action,
                detail=detail,
                actor=actor.user,
                session=actor.session,
            )
        )

    def _apply(
        self, actor: Actor, space: Space, member: Member, task: Task, change: TaskUpdate
    ) -> Task | None:
        """Check edit permission and translate chat changes into shared rules."""
        perms.require(perms.can_edit_task(actor, member, task), perms.Op.EDIT_TASK)
        due_at = task.due_at if change.due_at is None else change.due_at
        tags = dict.fromkeys((*task.tags, *change.add_tags))
        assignees = dict.fromkeys(
            (*task.assignees, *(m.user for m in change.add_assignees))
        )
        updated = dcs.replace(
            task,
            title=task.title if change.title is None else change.title,
            due_at=None if change.clear_due else due_at,
            priority=None
            if change.clear_priority
            else change.priority or task.priority,
            tags=tuple(tag for tag in tags if tag not in change.remove_tags),
            description=task.description
            if change.description is None
            else change.description.strip(),
            assignees=tuple(
                user for user in assignees if user not in change.remove_assignees
            ),
            status=change.status or task.status,
        )
        return task_ops.apply(
            self._store,
            self._clock,
            task,
            updated,
            actor=actor.user,
            actor_label="",
            session=actor.session,
            added_assignees=change.add_assignees,
        )

    @staticmethod
    def _check_space_name(name: str) -> str:
        """Validate a user-chosen space name and return it stripped."""
        name = name.strip()
        if not name:
            raise TodoError("空间名不能为空")
        if _NAME_BREAKERS.search(name):
            raise TodoError("空间名不能包含空格或 #")
        if len(name) > MAX_SPACE_NAME_CHARS:
            raise TodoError(f"空间名不能超过 {MAX_SPACE_NAME_CHARS} 字")
        return name

    def note_session_title(self, session: str, title: str) -> None:
        """Refresh a changed, nonblank session title for the WebUI."""
        if not title.strip():
            return
        with self._store.transaction():
            if self._store.get_session_titles([session]).get(session) != title:
                self._store.set_session_title(session, title)

    # ----- tasks -----

    def add_task(
        self,
        actor: Actor,
        auto_name: str,
        fields: TaskFields,
        space_name: str | None = None,
        description: str = "",
    ) -> TaskResult:
        """Create a task in the named bound space or the actor's current space.

        Mentions become members and assignees; removal/clear flags are ignored.
        Description is stripped. Returns all assignees as added.

        Raises:
            TodoError: Unbound space or a violated task limit.
        """
        with self._store.transaction():
            space, _ = self._space(actor, auto_name, space_name)
            draft = TaskDraft(
                space_id=space.id,
                title=fields.title,
                creator=actor.user,
                created_session=actor.session,
                assignees=tuple(m.user for m in fields.mentions),
                due_at=fields.due_at,
                tags=fields.add_tags,
                priority=fields.priority,
                description=description.strip(),
            )
            task_ops.check_task(draft)
            for mention in fields.mentions:
                self._store.upsert_member(space.id, mention.user, mention.name)
            task = self._store.create_task(draft)
            self._log(actor, task, EventAction.CREATE, {"title": task.title})
            return self._result(space, task, task.assignees)

    def list_tasks(
        self,
        actor: Actor,
        auto_name: str,
        space_name: str | None = None,
        include_archived: bool = False,
    ) -> TaskListResult:
        """List tasks by seq in the named bound space or current space.

        ARCHIVED tasks are excluded unless requested. Resolves actor membership
        and returns the space and nonempty member nicknames.

        Raises:
            TodoError: Unbound space name.
        """
        statuses = (*MINE_STATUSES, TaskStatus.DONE)
        if include_archived:
            statuses = (*statuses, TaskStatus.ARCHIVED)
        with self._store.transaction():
            space, _ = self._space(actor, auto_name, space_name)
            tasks = self._store.list_tasks(space.id, statuses)
            return TaskListResult(
                space=space, tasks=tasks, nicknames=self._nicknames(space.id)
            )

    def show_task(self, actor: Actor, auto_name: str, ref: TaskRef) -> TaskResult:
        """Read a task in a bound space, resolving actor membership.

        Raises:
            TodoError: Unbound space name or unknown task.
        """
        with self._store.transaction():
            space, _, task = self._task(actor, auto_name, ref)
            return self._result(space, task)

    def set_status(
        self, actor: Actor, auto_name: str, ref: TaskRef, status: TaskStatus
    ) -> TaskResult:
        """Change status as creator, assignee or space admin.

        DONE sets done_at to now; ARCHIVED keeps it; TODO/DOING clear it.
        Setting the existing status is a no-op.

        Raises:
            TodoError: Unknown task or permission denied.
        """
        with self._store.transaction():
            space, member, task = self._task(actor, auto_name, ref)
            saved = self._apply(actor, space, member, task, TaskUpdate(status=status))
            return self._result(space, saved or task)

    def assign(
        self, actor: Actor, auto_name: str, ref: TaskRef, ops: AssignOps
    ) -> TaskResult:
        """Replace or add/remove assignees; mentions become members.

        Returns the saved task and users not previously assigned.

        Raises:
            TodoError: Unknown task, permission denied or too many assignees.
        """
        with self._store.transaction():
            space, member, task = self._task(actor, auto_name, ref)
            perms.require(perms.can_edit_task(actor, member, task), perms.Op.EDIT_TASK)
            if ops.replace is not None:
                added = ops.replace
                assignees = tuple(m.user for m in ops.replace)
            else:
                added = ops.add
                removed = {m.user for m in ops.remove}
                merged = dict.fromkeys((*task.assignees, *(m.user for m in ops.add)))
                assignees = tuple(user for user in merged if user not in removed)
            updated = dcs.replace(task, assignees=assignees)
            task_ops.check_task(updated)
            saved = task_ops.apply(
                self._store,
                self._clock,
                task,
                updated,
                actor=actor.user,
                actor_label="",
                session=actor.session,
                added_assignees=added,
            )
            if saved is None:
                return self._result(space, task)
            added = tuple(user for user in assignees if user not in task.assignees)
            return self._result(space, saved, added)

    def edit_task(
        self, actor: Actor, auto_name: str, ref: TaskRef, fields: TaskFields
    ) -> TaskResult:
        """Edit title, due time, priority and tags; reject assignee mentions.

        Due changes reset reminded; clear flags override values.

        Raises:
            TodoError: Unknown task, permission denied, no change, mentions
                given, or a violated limit.
        """
        if fields.mentions:
            raise TodoError("修改负责人请使用 /todo assign")
        change = TaskUpdate(
            title=fields.title or None,
            due_at=fields.due_at,
            clear_due=fields.clear_due,
            priority=fields.priority,
            clear_priority=fields.clear_priority,
            add_tags=fields.add_tags,
            remove_tags=fields.remove_tags,
        )
        with self._store.transaction():
            space, member, task = self._task(actor, auto_name, ref)
            saved = self._apply(actor, space, member, task, change)
            if saved is None:
                raise TodoError("没有需要修改的内容")
            return self._result(space, saved)

    def set_description(
        self, actor: Actor, auto_name: str, ref: TaskRef, text: str
    ) -> TaskResult:
        """Set the stripped description, or clear it with empty text.

        Raises:
            TodoError: Unknown task, permission denied or text too long.
        """
        with self._store.transaction():
            space, member, task = self._task(actor, auto_name, ref)
            change = TaskUpdate(description=text)
            saved = self._apply(actor, space, member, task, change)
            return self._result(space, saved or task)

    def update_task(
        self, actor: Actor, auto_name: str, ref: TaskRef, change: TaskUpdate
    ) -> TaskResult:
        """Apply all combined changes atomically, with shared task rules.

        Returns the unchanged task on a no-op, otherwise the saved task and
        newly assigned users. Any failure rolls back every part of the change.

        Raises:
            TodoError: Unknown task, permission denied or a violated limit.
        """
        with self._store.transaction():
            space, member, task = self._task(actor, auto_name, ref)
            saved = self._apply(actor, space, member, task, change)
            if saved is None:
                return self._result(space, task)
            added = tuple(
                user for user in saved.assignees if user not in task.assignees
            )
            return self._result(space, saved, added)

    def find_members(
        self,
        actor: Actor,
        auto_name: str,
        nickname: str,
        space_name: str | None = None,
    ) -> list[Member]:
        """Find exact casefolded nicknames in a bound or current space.

        Raises:
            TodoError: Unbound space name.
        """
        key = nickname.strip().casefold()
        with self._store.transaction():
            space, _ = self._space(actor, auto_name, space_name)
            if not key:
                return []
            return [
                member
                for member in self._store.list_members(space.id)
                if member.nickname.strip().casefold() == key
            ]

    def delete_task(self, actor: Actor, auto_name: str, ref: TaskRef) -> TaskResult:
        """Hard-delete as creator or admin, retaining a full snapshot event.

        Returns the task as it was before deletion.

        Raises:
            TodoError: Unknown task or permission denied.
        """
        with self._store.transaction():
            space, member, task = self._task(actor, auto_name, ref)
            perms.require(
                perms.can_delete_task(actor, member, task), perms.Op.DELETE_TASK
            )
            self._log(actor, task, EventAction.DELETE, {"snapshot": dcs.asdict(task)})
            self._store.delete_task(space.id, task.seq)
            return self._result(space, task)

    def mine(self, actor: Actor) -> tuple[list[tuple[str, Task]], dict[str, str]]:
        """List actor's TODO/DOING assignments ordered by due time.

        Private chats span all spaces; groups only include spaces bound here.
        Returns (space_name, task) pairs and nicknames from listed spaces only.
        """
        tasks = self._store.list_tasks_assigned_to(actor.user, MINE_STATUSES)
        if not actor.is_private:
            bound = {b.space_id for b in self._store.list_bindings(actor.session)}
            tasks = [task for task in tasks if task.space_id in bound]
        names: dict[int, str] = {}
        nicknames: dict[str, str] = {}
        for space_id in dict.fromkeys(task.space_id for task in tasks):
            names[space_id] = self._store.get_space(space_id).name
            nicknames.update(self._nicknames(space_id))
        return [(names[task.space_id], task) for task in tasks], nicknames

    # ----- spaces and sessions -----

    def list_spaces(
        self, actor: Actor, auto_name: str
    ) -> list[tuple[Space, bool, bool]]:
        """List bound spaces in binding order with default/current flags."""
        with self._store.transaction():
            current, _ = self._space(actor, auto_name, None)
            default = self._store.get_session_default(actor.session)
            return [
                (
                    self._store.get_space(b.space_id),
                    b.space_id == default,
                    b.space_id == current.id,
                )
                for b in self._store.list_bindings(actor.session)
            ]

    def use_space(self, actor: Actor, name: str) -> Space:
        """Switch only the actor's current space in this session.

        Raises:
            TodoError: Unbound space name.
        """
        with self._store.transaction():
            space, _ = self._space(actor, "", name)
            self._store.set_current(actor.session, actor.user, space.id)
            return space

    def set_default(self, actor: Actor, name: str) -> Space:
        """Change this session's default space as a session admin.

        Raises:
            TodoError: Permission denied or unbound space name.
        """
        perms.require(perms.can_run_session_op(actor), perms.Op.SESSION)
        with self._store.transaction():
            space = self._bound_space(actor.session, name)
            self._store.set_session_default(actor.session, space.id)
            return space

    def new_space(self, actor: Actor, name: str) -> Space:
        """Create, own and bind a space, then select it for the actor.

        Raises:
            TodoError: Permission denied, invalid or taken name.
        """
        perms.require(perms.can_run_session_op(actor), perms.Op.SESSION)
        name = self._check_space_name(name)
        with self._store.transaction():
            if name in self._store.session_space_names(actor.session):
                raise TodoError(f"本会话已有名为「{name}」的空间")
            space = self._store.create_space(name, actor.user)
            self._store.upsert_member(space.id, actor.user, actor.nickname)
            self._store.bind(actor.session, space.id)
            self._store.set_current(actor.session, actor.user, space.id)
            self._space_log(actor, space, SpaceEventAction.CREATE, {"name": space.name})
            self._space_log(
                actor, space, SpaceEventAction.BIND, {"session": actor.session}
            )
            return space

    def rename_space(self, actor: Actor, auto_name: str, name: str) -> Space:
        """Rename the current space as a space admin.

        The new name must be free in every session bound to this space.

        Raises:
            TodoError: Permission denied, invalid or taken name.
        """
        name = self._check_space_name(name)
        with self._store.transaction():
            space, member = self._space(actor, auto_name, None)
            perms.require(
                perms.can_manage_space(actor, member, space), perms.Op.MANAGE_SPACE
            )
            for binding in self._store.list_space_bindings(space.id):
                taken = self._store.session_space_names(binding.session).get(name)
                if taken not in (None, space.id):
                    raise TodoError(f"空间名「{name}」在某个绑定会话里已被占用")
            renamed = self._store.rename_space(space.id, name)
            self._space_log(
                actor, space, SpaceEventAction.RENAME, {"name": [space.name, name]}
            )
            return renamed

    def delete_space(self, actor: Actor, name: str, code: str | None) -> str | None:
        """Delete a bound space as owner after a confirmation round.

        Returns a confirmation code when code is None; otherwise deletes and
        returns None. A wrong or expired code voids the pending confirmation.

        Raises:
            TodoError: Unbound name, permission denied or invalid code.
        """
        with self._store.transaction():
            space = self._bound_space(actor.session, name)
            member = self._store.get_member(space.id, actor.user)
            perms.require(
                perms.can_delete_space(actor, member, space), perms.Op.DELETE_SPACE
            )
            key = (actor.session, actor.user.key, "space.delete", space.id)
            if code is None:
                return self._confirms.issue(key)
            if not self._confirms.consume(key, code):
                raise TodoError(CONFIRM_MISMATCH)
            self._store.delete_space(space.id)
            return None

    def create_invite(self, actor: Actor, auto_name: str) -> tuple[Space, str, Invite]:
        """Reset the current space's invite as a space admin.

        Returns the space, plain code (show once, never log) and stored invite.

        Raises:
            TodoError: Permission denied.
        """
        with self._store.transaction():
            space, member = self._space(actor, auto_name, None)
            perms.require(
                perms.can_manage_space(actor, member, space), perms.Op.MANAGE_SPACE
            )
            code, invite = new_invite(space.id, self._clock())
            self._store.set_invite(invite.space_id, invite.code_hash, invite.expires_at)
            self._space_log(
                actor, space, SpaceEventAction.INVITE, {"expires_at": invite.expires_at}
            )
            return space, code, invite

    def join_space(self, actor: Actor, code: str) -> Space:
        """Bind this session using an invite as a session admin.

        Invalid/expired codes count against the session's rate limit; name
        collisions do not. Joining never renames the shared space.

        Raises:
            TodoError: Permission denied, rate limited, invalid/expired code,
                space already bound, or name taken here.
        """
        perms.require(perms.can_run_session_op(actor), perms.Op.SESSION)
        if not code.strip():
            raise TodoError("请提供邀请码")
        if self._joins.is_blocked(actor.session):
            raise TodoError("邀请码错误次数过多，请一小时后再试")
        with self._store.transaction():
            invite = self._store.find_invite(hash_invite_code(code))
            if invite is None or is_invite_expired(invite, self._clock()):
                self._joins.record_failure(actor.session)
                raise TodoError("邀请码无效或已过期")
            space = self._store.get_space(invite.space_id)
            bound = self._store.session_space_names(actor.session)
            if space.id in bound.values():
                raise TodoError(f"本会话已经绑定了空间「{space.name}」")
            if space.name in bound:
                raise TodoError(
                    f"本会话已有名为「{space.name}」的空间，"
                    "请先用 /todo space rename 改名后再加入"
                )
            self._store.bind(actor.session, space.id)
            self._store.upsert_member(space.id, actor.user, actor.nickname)
            self._space_log(
                actor, space, SpaceEventAction.BIND, {"session": actor.session}
            )
            return space

    def leave_space(self, actor: Actor, name: str) -> Space:
        """Unbind as session admin, keeping the space and clearing selections.

        Raises:
            TodoError: Permission denied or unbound space name.
        """
        perms.require(perms.can_run_session_op(actor), perms.Op.SESSION)
        with self._store.transaction():
            space = self._bound_space(actor.session, name)
            self._store.unbind(actor.session, space.id)
            self._space_log(
                actor, space, SpaceEventAction.UNBIND, {"session": actor.session}
            )
            return space

    def claim(self, actor: Actor, auto_name: str) -> Space:
        """Claim the ownerless current space as a group-session admin.

        Raises:
            TodoError: Private chat, not a session admin, or space owned.
        """
        if actor.is_private:
            raise TodoError("只能在群聊里认领空间")
        with self._store.transaction():
            space, _ = self._space(actor, auto_name, None)
            perms.require(perms.can_claim(actor, space), perms.Op.CLAIM)
            claimed = self._store.set_space_owner(space.id, actor.user)
            self._space_log(actor, space, SpaceEventAction.CLAIM, {})
            return claimed

    def admin_precheck(self, actor: Actor, users: frozenset[UserRef]) -> set[UserRef]:
        """Deny non-owners read-only (no transaction, no auto space, no fetch).

        Mirrors ``_current_space``; ``set_admins`` re-checks everything.
        Returns the ``users`` that are not members of the current space.

        Raises:
            TodoError: The actor may not manage admins of the current space.
        """
        return task_ops.admin_precheck(self._store, actor, users)

    def set_admins(
        self,
        actor: Actor,
        auto_name: str,
        ops: AssignOps,
        raw: frozenset[UserRef] = frozenset(),
        verified: dict[UserRef, str] | None = None,
        id_label: str = "用户 ID",
    ) -> tuple[Space, list[Member]]:
        """Appoint or revoke admins atomically; see ``task_ops.set_roles``.

        Raises:
            TodoError: Bad usage, denied permission, or a rejected target.
        """
        if ops.replace is not None:
            raise TodoError("用法：/todo admin +@某人 / -@某人 或 +用户ID / -用户ID")
        with self._store.transaction():
            space, member = self._space(actor, auto_name, None)
            perms.require(
                perms.can_manage_admins(actor, member, space), perms.Op.MANAGE_ADMINS
            )
            return space, task_ops.set_roles(
                self._store, space, ops, actor, raw, verified or {}, id_label
            )

    def sync_precheck(self, actor: Actor, name: str | None = None) -> Space:
        """Check sync permission read-only, without creating a target space."""
        return task_ops.sync_precheck(self._store, actor, name)

    def sync_members(
        self, actor: Actor, members: Sequence[GroupMember], name: str | None = None
    ) -> tuple[Space, int, int]:
        """Re-resolve and recheck the target, then sync and log atomically."""
        with self._store.transaction():
            space = self.sync_precheck(actor, name)
            added, total = task_ops.sync_members(self._store, space, actor, members)
            return space, added, total
