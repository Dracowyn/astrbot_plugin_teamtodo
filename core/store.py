"""Synchronous SQLite persistence for spaces, bindings, members and tasks.

CONTRACT (owned by the main loop; implementers fill in the bodies, signatures
and documented behaviour are fixed):

- One ``sqlite3`` connection per ``Store``, WAL mode, ``foreign_keys=ON``,
  schema versioned with ``PRAGMA user_version`` (M1 = version 1). Later
  milestones add tables through numbered migrations, never by editing v1.
- Every mutating method must run inside ``with store.transaction():`` and
  raises ``RuntimeError`` otherwise. ``transaction()`` issues
  ``BEGIN IMMEDIATE`` and commits on success, rolls back on exception.
  Transactions do not nest (raise ``RuntimeError``).
- Read methods work both inside and outside a transaction.
- Callers (services) do read -> permission check -> write inside one
  transaction with no ``await`` in between; the store itself never awaits.
- Lists are returned in a deterministic order documented per method.
- No permission logic lives here.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from .models import (
    Binding,
    Invite,
    Member,
    Space,
    SpaceRole,
    Task,
    TaskDraft,
    TaskEvent,
    TaskEventDraft,
    TaskStatus,
    UserRef,
)


class Store:
    """SQLite-backed repository. Not thread-safe; used from the event loop."""

    def __init__(
        self, db_path: Path, *, clock: Callable[[], float] = time.time
    ) -> None:
        """Open (and create or migrate) the database.

        Args:
            db_path: Database file in an existing directory. ``:memory:`` is
                accepted for tests.
            clock: Returns Unix seconds; used for every stored timestamp.
        """
        raise NotImplementedError

    def close(self) -> None:
        """Close the connection. Safe to call twice."""
        raise NotImplementedError

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Run the enclosed block atomically (``BEGIN IMMEDIATE``).

        Raises:
            RuntimeError: If a transaction is already open.
        """
        raise NotImplementedError
        yield

    # ----- spaces -----

    def create_space(self, name: str, owner: UserRef | None) -> Space:
        """Insert a space. When ``owner`` is given, also insert that user as
        an OWNER member (nickname = ``owner.user_id`` until upserted).

        Name uniqueness is NOT checked here (it is per bound session; see
        ``session_space_names``).
        """
        raise NotImplementedError

    def get_space(self, space_id: int) -> Space | None:
        """Return the space or ``None``."""
        raise NotImplementedError

    def list_spaces(self) -> list[Space]:
        """All spaces ordered by id."""
        raise NotImplementedError

    def rename_space(self, space_id: int, name: str) -> Space:
        """Rename and return the updated space. Raises ``KeyError`` if missing."""
        raise NotImplementedError

    def set_space_owner(self, space_id: int, owner: UserRef) -> Space:
        """Make ``owner`` the owner: upsert them as OWNER member, demote the
        previous owner (if any) to ADMIN. Raises ``KeyError`` if missing."""
        raise NotImplementedError

    def delete_space(self, space_id: int) -> None:
        """Delete the space and everything under it (bindings, defaults,
        current-space rows, members, tasks, assignees, tags, events, invite)."""
        raise NotImplementedError

    # ----- bindings, defaults, current space -----

    def bind(self, session: str, space_id: int) -> Binding:
        """Bind; idempotent (returns the existing binding unchanged)."""
        raise NotImplementedError

    def unbind(self, session: str, space_id: int) -> None:
        """Remove the binding. Also clears the session default and any user's
        current-space row of that session when they point at ``space_id``."""
        raise NotImplementedError

    def list_bindings(self, session: str) -> list[Binding]:
        """Bindings of a session ordered by ``bound_at`` then ``space_id``."""
        raise NotImplementedError

    def list_space_bindings(self, space_id: int) -> list[Binding]:
        """Bindings of a space ordered by ``bound_at`` then ``session``."""
        raise NotImplementedError

    def session_space_names(self, session: str) -> dict[str, int]:
        """Map of space name -> space id for all spaces bound to ``session``."""
        raise NotImplementedError

    def get_session_default(self, session: str) -> int | None:
        """The session's default space id, or ``None`` if unset."""
        raise NotImplementedError

    def set_session_default(self, session: str, space_id: int | None) -> None:
        """Set or clear the session default."""
        raise NotImplementedError

    def get_current(self, session: str, user: UserRef) -> int | None:
        """The user's current space id in that session, or ``None``."""
        raise NotImplementedError

    def set_current(self, session: str, user: UserRef, space_id: int | None) -> None:
        """Set or clear the user's current space in that session."""
        raise NotImplementedError

    # ----- members -----

    def upsert_member(self, space_id: int, user: UserRef, nickname: str) -> Member:
        """Insert as MEMBER if unknown; otherwise update the nickname when the
        given one is non-empty. Never changes the role."""
        raise NotImplementedError

    def get_member(self, space_id: int, user: UserRef) -> Member | None:
        """Return the member or ``None``."""
        raise NotImplementedError

    def list_members(self, space_id: int) -> list[Member]:
        """Members ordered by ``joined_at`` then user key."""
        raise NotImplementedError

    def set_member_role(self, space_id: int, user: UserRef, role: SpaceRole) -> Member:
        """Change the role of an existing member (ADMIN or MEMBER only;
        ownership changes go through ``set_space_owner``).

        Raises:
            KeyError: Member does not exist.
            ValueError: ``role`` is OWNER, or the member is the owner.
        """
        raise NotImplementedError

    # ----- tasks -----

    def create_task(self, draft: TaskDraft) -> Task:
        """Insert with the next per-space ``seq`` (max ever used + 1, never
        reused even after deletes), status TODO, ``reminded=False``."""
        raise NotImplementedError

    def get_task(self, space_id: int, seq: int) -> Task | None:
        """Return the task or ``None``."""
        raise NotImplementedError

    def update_task(self, task: Task) -> Task:
        """Persist all mutable fields of ``task`` (title, status, assignees,
        due_at, tags, priority, description, done_at, reminded), set
        ``updated_at`` to now and return the stored task.

        The store does not derive ``done_at``/``reminded``; callers set them.
        Raises ``KeyError`` if the task does not exist.
        """
        raise NotImplementedError

    def delete_task(self, space_id: int, seq: int) -> None:
        """Delete the task with its assignees and tags (events are kept)."""
        raise NotImplementedError

    def list_tasks(self, space_id: int, statuses: tuple[TaskStatus, ...]) -> list[Task]:
        """Tasks of a space in the given statuses, ordered by ``seq``."""
        raise NotImplementedError

    def list_tasks_assigned_to(
        self, user: UserRef, statuses: tuple[TaskStatus, ...]
    ) -> list[Task]:
        """Tasks in any space that have ``user`` among the assignees,
        ordered by ``due_at`` (``None`` last), then ``space_id``, ``seq``."""
        raise NotImplementedError

    # ----- operation log -----

    def add_event(self, draft: TaskEventDraft) -> TaskEvent:
        """Append a log entry (``detail`` stored as JSON)."""
        raise NotImplementedError

    def list_events(
        self, space_id: int, task_seq: int | None = None, limit: int = 50
    ) -> list[TaskEvent]:
        """Newest first; filtered to one task when ``task_seq`` is given."""
        raise NotImplementedError

    # ----- invites -----

    def set_invite(self, space_id: int, code_hash: str, expires_at: float) -> Invite:
        """Replace the space's invite (at most one per space)."""
        raise NotImplementedError

    def find_invite(self, code_hash: str) -> Invite | None:
        """Look up by hash; expired invites are returned too (caller checks)."""
        raise NotImplementedError

    def clear_invite(self, space_id: int) -> None:
        """Remove the space's invite if any."""
        raise NotImplementedError
