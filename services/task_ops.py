"""Shared task limits, lifecycle transitions and transactional operation logs.

Also holds the chat-side space role update (``set_roles``). Callers resolve
identities, permissions and patch syntax before invoking these helpers inside
their store transaction. Chat and WebUI writes share all rules; log identity
is always explicit and independent of the task creator.
"""

from __future__ import annotations

import dataclasses as dcs
from collections.abc import Callable, Mapping

from ..core.models import (
    MAX_ASSIGNEES,
    MAX_DESCRIPTION_CHARS,
    MAX_TAG_CHARS,
    MAX_TAGS,
    MAX_TITLE_CHARS,
    Actor,
    EventAction,
    Member,
    Mention,
    Space,
    SpaceEventAction,
    SpaceEventDraft,
    SpaceRole,
    Task,
    TaskDraft,
    TaskEventDraft,
    TaskStatus,
    TodoError,
    UserRef,
)
from ..core.parsing import AssignOps
from ..core.store import Store


def check_task(task: Task | TaskDraft) -> None:
    """Enforce shared limits on a task about to be written."""
    if not task.title.strip():
        raise TodoError("任务标题不能为空")
    if len(task.title) > MAX_TITLE_CHARS:
        raise TodoError(f"任务标题不能超过 {MAX_TITLE_CHARS} 字")
    if len(task.description) > MAX_DESCRIPTION_CHARS:
        raise TodoError(f"描述不能超过 {MAX_DESCRIPTION_CHARS} 字")
    if len(task.tags) > MAX_TAGS:
        raise TodoError(f"标签不能超过 {MAX_TAGS} 个")
    if any(len(tag) > MAX_TAG_CHARS for tag in task.tags):
        raise TodoError(f"单个标签不能超过 {MAX_TAG_CHARS} 字")
    if len(task.assignees) > MAX_ASSIGNEES:
        raise TodoError(f"负责人不能超过 {MAX_ASSIGNEES} 人")


def log_event(
    store: Store,
    task: Task,
    action: EventAction,
    detail: dict,
    *,
    actor: UserRef | None,
    actor_label: str,
    session: str,
) -> None:
    """Append a task event with the caller's explicit log identity."""
    store.add_event(
        TaskEventDraft(
            space_id=task.space_id,
            task_seq=task.seq,
            action=action,
            detail=detail,
            actor=actor,
            actor_label=actor_label,
            session=session,
        )
    )


def apply(
    store: Store,
    clock: Callable[[], float],
    task: Task,
    updated: Task,
    *,
    actor: UserRef | None,
    actor_label: str,
    session: str,
    added_assignees: tuple[Mention, ...] = (),
) -> Task | None:
    """Save proposed fields with shared lifecycle rules and one log per kind.

    DONE transitions set completion time, ARCHIVED preserves it, and TODO or
    DOING clears it. Due changes reset reminders. Added mentions become members
    even on a no-op; no-op task changes return None without saving or logging.
    """
    fields = {
        name: [getattr(task, name), getattr(updated, name)]
        for name in ("title", "due_at", "priority", "tags", "description")
        if getattr(task, name) != getattr(updated, name)
    }
    assignees_changed = updated.assignees != task.assignees
    if fields or assignees_changed:
        check_task(updated)
    for mention in added_assignees:
        store.upsert_member(task.space_id, mention.user, mention.name)
    status_changed = updated.status != task.status
    if status_changed:
        if updated.status == TaskStatus.DONE:
            done_at = clock()
        elif updated.status == TaskStatus.ARCHIVED:
            done_at = task.done_at
        else:
            done_at = None
        updated = dcs.replace(updated, done_at=done_at)
    elif not fields and not assignees_changed:
        return None
    if "due_at" in fields:
        updated = dcs.replace(updated, reminded=False)
    saved = store.update_task(updated)
    identity = {"actor": actor, "actor_label": actor_label, "session": session}
    if fields:
        log_event(store, saved, EventAction.UPDATE, fields, **identity)
    if assignees_changed:
        log_event(
            store,
            saved,
            EventAction.ASSIGN,
            {
                "assignees": [
                    [u.key for u in task.assignees],
                    [u.key for u in saved.assignees],
                ]
            },
            **identity,
        )
    if status_changed:
        action = (
            EventAction.ARCHIVE
            if saved.status == TaskStatus.ARCHIVED
            else EventAction.STATUS
        )
        log_event(
            store, saved, action, {"status": [task.status, saved.status]}, **identity
        )
    return saved


def set_roles(
    store: Store,
    space: Space,
    ops: AssignOps,
    actor: Actor,
    raw: frozenset[UserRef],
    verified: Mapping[UserRef, str],
    id_label: str,
) -> list[Member]:
    """Promote ``ops.add`` to admin and demote ``ops.remove``, logging changes.

    Membership is decided here, inside the caller's transaction, so it always
    refers to the space being modified. An added non-member joins first: a
    mention proves presence, while a raw ID (in ``raw``) must be in
    ``verified`` (the chat group's member list) and takes its group name.

    Args:
        store: Store with an open transaction.
        space: The space being modified.
        ops: Delta operations (``replace`` is ignored).
        actor: The requesting user, recorded in the role logs.
        raw: Additions given as raw IDs rather than mentions.
        verified: Raw IDs found in the group's member list -> display name.
        id_label: What a raw ID is called on this platform, for errors.

    Returns:
        The updated member rows, additions first, in argument order.

    Raises:
        TodoError: Unverified raw addition, non-member removal, or an owner.
    """
    changed = []
    for mention, role in [(m, SpaceRole.ADMIN) for m in ops.add] + [
        (m, SpaceRole.MEMBER) for m in ops.remove
    ]:
        user = mention.user
        target = store.get_member(space.id, user)
        if target is None and role == SpaceRole.ADMIN:
            if user in raw and user not in verified:
                raise TodoError(
                    f"找不到 {id_label} {user.user_id}："
                    "不是空间成员，也不在本群成员列表里"
                )
            name = verified[user] if user in raw else mention.name
            target = store.upsert_member(space.id, user, name)
        if target is None:
            raise TodoError(f"{mention.name or user.user_id} 还不是这个空间的成员")
        if target.role == SpaceRole.OWNER or target.user == space.owner:
            raise TodoError("不能修改所有者的角色")
        changed.append(store.set_member_role(space.id, target.user, role))
        if target.role != role:
            store.add_space_event(
                SpaceEventDraft(
                    space_id=space.id,
                    action=SpaceEventAction.ROLE,
                    detail={"user": target.user.key, "role": [target.role, role]},
                    actor=actor.user,
                    session=actor.session,
                )
            )
    return changed
