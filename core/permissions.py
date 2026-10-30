"""Permission decisions (design sections 2.4 and 4), as pure functions.

Every function takes the ``Actor`` and the actor's own ``Member`` row in the
space concerned (``None`` when the actor has no row yet). A ``Member`` that
belongs to another user or another space is ignored, so a wiring mistake in
the caller can only deny, never grant.

Rules:

- AstrBot admins (``actor.is_bot_admin``) may do everything everywhere.
- Anyone using a bound session may create, list and show tasks; the service
  upserts them as a plain member first. A missing ``Member`` row never grants
  a space role.
- "Space admin" includes the owner. Owner-only: appoint or revoke admins,
  delete the space.
- Session-level operations (join, leave, default, space new, claim) need
  ``actor.is_session_admin``; ``claim`` also needs an ownerless space.

Callers turn a ``False`` into a user-facing error with ``require(allowed, op)``
so that every entry point shows the same text from ``DENIAL_MESSAGES``.
"""

from __future__ import annotations

from enum import StrEnum

from .models import Actor, Member, Space, SpaceRole, Task, TodoError


class Op(StrEnum):
    """Permission-checked operations, used to look up denial messages."""

    EDIT_TASK = "edit_task"
    """Edit fields, change status, assign, set description."""
    DELETE_TASK = "delete_task"
    MANAGE_SPACE = "manage_space"
    """Invite, rename the space, share links (M4)."""
    MANAGE_ADMINS = "manage_admins"
    DELETE_SPACE = "delete_space"
    SESSION = "session"
    """join, leave, default, space new."""
    CLAIM = "claim"


DENIAL_MESSAGES: dict[Op, str] = {
    Op.EDIT_TASK: "只有任务的创建人、负责人或空间管理员可以修改这个任务",
    Op.DELETE_TASK: "只有任务的创建人或空间管理员可以删除这个任务",
    Op.MANAGE_SPACE: "只有空间管理员可以执行这个操作",
    Op.MANAGE_ADMINS: "只有空间所有者可以任命或撤销管理员",
    Op.DELETE_SPACE: "只有空间所有者可以删除空间",
    Op.SESSION: "只有会话管理员（群主、群管理员或 AstrBot 管理员）可以执行这个操作",
    Op.CLAIM: "只有会话管理员可以认领空间，并且只能认领没有所有者的空间",
}
"""The single source of Chinese denial texts, keyed by operation."""


def _role(actor: Actor, member: Member | None, space_id: int) -> SpaceRole | None:
    """Return the actor's role in ``space_id``, ignoring a mismatched row."""
    if member is None or member.user != actor.user or member.space_id != space_id:
        return None
    return member.role


def require(allowed: bool, op: Op) -> None:
    """Raise the standard denial for ``op`` unless ``allowed``.

    Args:
        allowed: Result of one of the ``can_*`` checks.
        op: The operation that was checked.

    Raises:
        TodoError: When ``allowed`` is false; the message comes from
            ``DENIAL_MESSAGES``.
    """
    if not allowed:
        raise TodoError(DENIAL_MESSAGES[op])


def can_use_space(actor: Actor, member: Member | None) -> bool:
    """Whether the actor may create, list and show tasks in a bound space.

    Always true: anyone in a bound session is (or is about to become) a
    member. Checking that the space is bound to the session is the service's
    job.

    Args:
        actor: The requesting user.
        member: The actor's member row, or ``None`` if not yet a member.

    Returns:
        True.
    """
    return True


def is_space_admin(actor: Actor, member: Member | None, space: Space) -> bool:
    """Whether the actor is an admin (or the owner) of ``space``.

    Args:
        actor: The requesting user.
        member: The actor's member row in ``space``, or ``None``.
        space: The space concerned.

    Returns:
        True for bot admins and for members with role OWNER or ADMIN.
    """
    if actor.is_bot_admin:
        return True
    return _role(actor, member, space.id) in (SpaceRole.OWNER, SpaceRole.ADMIN)


def is_space_owner(actor: Actor, member: Member | None, space: Space) -> bool:
    """Whether the actor owns ``space``.

    Both ``space.owner`` and the member row must agree, so an out-of-sync
    store fails closed.

    Args:
        actor: The requesting user.
        member: The actor's member row in ``space``, or ``None``.
        space: The space concerned.

    Returns:
        True for bot admins and for the recorded owner.
    """
    if actor.is_bot_admin:
        return True
    return (
        space.owner == actor.user and _role(actor, member, space.id) == SpaceRole.OWNER
    )


def can_manage_space(actor: Actor, member: Member | None, space: Space) -> bool:
    """Whether the actor may invite, rename the space or manage share links.

    Args:
        actor: The requesting user.
        member: The actor's member row in ``space``, or ``None``.
        space: The space concerned.

    Returns:
        True for space admins (including the owner) and bot admins.
    """
    return is_space_admin(actor, member, space)


def can_manage_admins(actor: Actor, member: Member | None, space: Space) -> bool:
    """Whether the actor may appoint or revoke space admins.

    Args:
        actor: The requesting user.
        member: The actor's member row in ``space``, or ``None``.
        space: The space concerned.

    Returns:
        True for the owner and bot admins.
    """
    return is_space_owner(actor, member, space)


def can_delete_space(actor: Actor, member: Member | None, space: Space) -> bool:
    """Whether the actor may delete ``space``.

    Args:
        actor: The requesting user.
        member: The actor's member row in ``space``, or ``None``.
        space: The space concerned.

    Returns:
        True for the owner and bot admins.
    """
    return is_space_owner(actor, member, space)


def can_edit_task(actor: Actor, member: Member | None, task: Task) -> bool:
    """Whether the actor may edit fields, status, assignees or description.

    Args:
        actor: The requesting user.
        member: The actor's member row in the task's space, or ``None``.
        task: The task concerned.

    Returns:
        True for bot admins, space admins, the creator and any assignee.
    """
    if actor.is_bot_admin or actor.user == task.creator or actor.user in task.assignees:
        return True
    return _role(actor, member, task.space_id) in (SpaceRole.OWNER, SpaceRole.ADMIN)


def can_delete_task(actor: Actor, member: Member | None, task: Task) -> bool:
    """Whether the actor may delete ``task``. Assignees alone may not.

    Args:
        actor: The requesting user.
        member: The actor's member row in the task's space, or ``None``.
        task: The task concerned.

    Returns:
        True for bot admins, space admins and the creator.
    """
    if actor.is_bot_admin or actor.user == task.creator:
        return True
    return _role(actor, member, task.space_id) in (SpaceRole.OWNER, SpaceRole.ADMIN)


def can_run_session_op(actor: Actor) -> bool:
    """Whether the actor may join, leave, set the default or create spaces.

    Args:
        actor: The requesting user.

    Returns:
        True for session admins and bot admins.
    """
    return actor.is_bot_admin or actor.is_session_admin


def can_claim(actor: Actor, space: Space) -> bool:
    """Whether the actor may claim ``space`` as its owner.

    Checking that the space is bound to the actor's session is the service's
    job.

    Args:
        actor: The requesting user.
        space: The space to claim.

    Returns:
        True when the actor is a session admin and the space has no owner.
    """
    return can_run_session_op(actor) and space.owner is None
