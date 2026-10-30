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

import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from .models import (
    Binding,
    EventAction,
    Invite,
    Member,
    Priority,
    Space,
    SpaceRole,
    Task,
    TaskDraft,
    TaskEvent,
    TaskEventDraft,
    TaskStatus,
    UserRef,
)


def _migrate_v1(conn: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE spaces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            owner_platform TEXT,
            owner_user TEXT,
            created_at REAL NOT NULL,
            next_seq INTEGER NOT NULL DEFAULT 1
        )""",
        """CREATE TABLE bindings (
            session TEXT NOT NULL,
            space_id INTEGER NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
            bound_at REAL NOT NULL,
            PRIMARY KEY (session, space_id)
        )""",
        """CREATE TABLE session_defaults (
            session TEXT PRIMARY KEY,
            space_id INTEGER NOT NULL REFERENCES spaces(id) ON DELETE CASCADE
        )""",
        """CREATE TABLE current_spaces (
            session TEXT NOT NULL,
            platform TEXT NOT NULL,
            user_id TEXT NOT NULL,
            space_id INTEGER NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
            PRIMARY KEY (session, platform, user_id)
        )""",
        """CREATE TABLE members (
            space_id INTEGER NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
            platform TEXT NOT NULL,
            user_id TEXT NOT NULL,
            nickname TEXT NOT NULL,
            role TEXT NOT NULL,
            joined_at REAL NOT NULL,
            PRIMARY KEY (space_id, platform, user_id)
        )""",
        """CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            space_id INTEGER NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
            seq INTEGER NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            due_at REAL,
            priority TEXT,
            description TEXT NOT NULL,
            creator_platform TEXT NOT NULL,
            creator_user TEXT NOT NULL,
            created_session TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            done_at REAL,
            reminded INTEGER NOT NULL,
            UNIQUE (space_id, seq)
        )""",
        """CREATE TABLE task_assignees (
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            platform TEXT NOT NULL,
            user_id TEXT NOT NULL,
            PRIMARY KEY (task_id, platform, user_id),
            UNIQUE (task_id, position)
        )""",
        """CREATE TABLE task_tags (
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            tag TEXT NOT NULL,
            PRIMARY KEY (task_id, tag),
            UNIQUE (task_id, position)
        )""",
        """CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            space_id INTEGER NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
            task_seq INTEGER NOT NULL,
            action TEXT NOT NULL,
            detail TEXT NOT NULL,
            actor_platform TEXT,
            actor_user TEXT,
            actor_label TEXT NOT NULL,
            session TEXT NOT NULL,
            created_at REAL NOT NULL
        )""",
        """CREATE TABLE invites (
            space_id INTEGER PRIMARY KEY REFERENCES spaces(id) ON DELETE CASCADE,
            code_hash TEXT NOT NULL UNIQUE,
            expires_at REAL NOT NULL
        )""",
        "CREATE INDEX assigned_user ON task_assignees(platform, user_id)",
        "CREATE INDEX event_space ON events(space_id, created_at DESC, id DESC)",
    )
    for statement in statements:
        conn.execute(statement)


_MIGRATIONS = (_migrate_v1,)


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
        self._clock = clock
        self._in_transaction = False
        self._conn = sqlite3.connect(db_path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version < len(_MIGRATIONS):
                with self.transaction():
                    for number, migration in enumerate(_MIGRATIONS, start=1):
                        if number > version:
                            migration(self._conn)
                            self._conn.execute(f"PRAGMA user_version={number}")
        except BaseException:
            self._conn.close()
            raise

    def close(self) -> None:
        """Close the connection. Safe to call twice."""
        self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Run the enclosed block atomically (``BEGIN IMMEDIATE``).

        Raises:
            RuntimeError: If a transaction is already open.
        """
        if self._in_transaction:
            raise RuntimeError("Transactions cannot nest")
        self._conn.execute("BEGIN IMMEDIATE")
        self._in_transaction = True
        try:
            yield
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        finally:
            self._in_transaction = False

    # ----- spaces -----

    def create_space(self, name: str, owner: UserRef | None) -> Space:
        """Insert a space. When ``owner`` is given, also insert that user as
        an OWNER member (nickname = ``owner.user_id`` until upserted).

        Name uniqueness is NOT checked here (it is per bound session; see
        ``session_space_names``).
        """
        self._require_transaction()
        now = self._clock()
        cursor = self._conn.execute(
            """INSERT INTO spaces(name, owner_platform, owner_user, created_at)
               VALUES (?, ?, ?, ?)""",
            (
                name,
                owner.platform_id if owner else None,
                owner.user_id if owner else None,
                now,
            ),
        )
        space_id = cursor.lastrowid
        if owner is not None:
            self._conn.execute(
                "INSERT INTO members VALUES (?, ?, ?, ?, ?, ?)",
                (
                    space_id,
                    owner.platform_id,
                    owner.user_id,
                    owner.user_id,
                    SpaceRole.OWNER,
                    now,
                ),
            )
        return self.get_space(space_id)

    def get_space(self, space_id: int) -> Space | None:
        """Return the space or ``None``."""
        row = self._conn.execute(
            "SELECT * FROM spaces WHERE id=?", (space_id,)
        ).fetchone()
        return self._space(row) if row is not None else None

    def list_spaces(self) -> list[Space]:
        """All spaces ordered by id."""
        return [
            self._space(row)
            for row in self._conn.execute("SELECT * FROM spaces ORDER BY id")
        ]

    def rename_space(self, space_id: int, name: str) -> Space:
        """Rename and return the updated space. Raises ``KeyError`` if missing."""
        self._require_transaction()
        cursor = self._conn.execute(
            "UPDATE spaces SET name=? WHERE id=?", (name, space_id)
        )
        if not cursor.rowcount:
            raise KeyError(space_id)
        return self.get_space(space_id)

    def set_space_owner(self, space_id: int, owner: UserRef) -> Space:
        """Make ``owner`` the owner: upsert them as OWNER member, demote the
        previous owner (if any) to ADMIN. Raises ``KeyError`` if missing."""
        self._require_transaction()
        space = self.get_space(space_id)
        if space is None:
            raise KeyError(space_id)
        if space.owner == owner:
            return space
        if space.owner is not None:
            self._conn.execute(
                """UPDATE members SET role=?
                   WHERE space_id=? AND platform=? AND user_id=?""",
                (
                    SpaceRole.ADMIN,
                    space_id,
                    space.owner.platform_id,
                    space.owner.user_id,
                ),
            )
        existing = self.get_member(space_id, owner)
        self.upsert_member(space_id, owner, owner.user_id if existing is None else "")
        self._conn.execute(
            "UPDATE members SET role=? WHERE space_id=? AND platform=? AND user_id=?",
            (SpaceRole.OWNER, space_id, owner.platform_id, owner.user_id),
        )
        self._conn.execute(
            "UPDATE spaces SET owner_platform=?, owner_user=? WHERE id=?",
            (owner.platform_id, owner.user_id, space_id),
        )
        return self.get_space(space_id)

    def delete_space(self, space_id: int) -> None:
        """Delete the space and everything under it (bindings, defaults,
        current-space rows, members, tasks, assignees, tags, events, invite)."""
        self._require_transaction()
        self._conn.execute("DELETE FROM spaces WHERE id=?", (space_id,))

    # ----- bindings, defaults, current space -----

    def bind(self, session: str, space_id: int) -> Binding:
        """Bind; idempotent (returns the existing binding unchanged)."""
        self._require_transaction()
        self._conn.execute(
            """INSERT INTO bindings VALUES (?, ?, ?)
               ON CONFLICT(session, space_id) DO NOTHING""",
            (session, space_id, self._clock()),
        )
        row = self._conn.execute(
            "SELECT * FROM bindings WHERE session=? AND space_id=?", (session, space_id)
        ).fetchone()
        return self._binding(row)

    def unbind(self, session: str, space_id: int) -> None:
        """Remove the binding. Also clears the session default and any user's
        current-space row of that session when they point at ``space_id``."""
        self._require_transaction()
        for table in ("session_defaults", "current_spaces", "bindings"):
            self._conn.execute(
                f"DELETE FROM {table} WHERE session=? AND space_id=?",
                (session, space_id),
            )

    def list_bindings(self, session: str) -> list[Binding]:
        """Bindings of a session ordered by ``bound_at`` then ``space_id``."""
        return [
            self._binding(row)
            for row in self._conn.execute(
                "SELECT * FROM bindings WHERE session=? ORDER BY bound_at, space_id",
                (session,),
            )
        ]

    def list_space_bindings(self, space_id: int) -> list[Binding]:
        """Bindings of a space ordered by ``bound_at`` then ``session``."""
        return [
            self._binding(row)
            for row in self._conn.execute(
                "SELECT * FROM bindings WHERE space_id=? ORDER BY bound_at, session",
                (space_id,),
            )
        ]

    def session_space_names(self, session: str) -> dict[str, int]:
        """Map of space name -> space id for all spaces bound to ``session``."""
        rows = self._conn.execute(
            """SELECT s.name, s.id FROM bindings b JOIN spaces s ON s.id=b.space_id
               WHERE b.session=? ORDER BY b.bound_at, b.space_id""",
            (session,),
        )
        return {row["name"]: row["id"] for row in rows}

    def get_session_default(self, session: str) -> int | None:
        """The session's default space id, or ``None`` if unset."""
        row = self._conn.execute(
            "SELECT space_id FROM session_defaults WHERE session=?", (session,)
        ).fetchone()
        return row["space_id"] if row is not None else None

    def set_session_default(self, session: str, space_id: int | None) -> None:
        """Set or clear the session default."""
        self._require_transaction()
        if space_id is None:
            self._conn.execute(
                "DELETE FROM session_defaults WHERE session=?", (session,)
            )
        else:
            self._conn.execute(
                """INSERT INTO session_defaults VALUES (?, ?)
                   ON CONFLICT(session) DO UPDATE SET space_id=excluded.space_id""",
                (session, space_id),
            )

    def get_current(self, session: str, user: UserRef) -> int | None:
        """The user's current space id in that session, or ``None``."""
        row = self._conn.execute(
            "SELECT space_id FROM current_spaces WHERE session=? AND platform=? AND user_id=?",
            (session, user.platform_id, user.user_id),
        ).fetchone()
        return row["space_id"] if row is not None else None

    def set_current(self, session: str, user: UserRef, space_id: int | None) -> None:
        """Set or clear the user's current space in that session."""
        self._require_transaction()
        identity = (session, user.platform_id, user.user_id)
        if space_id is None:
            self._conn.execute(
                "DELETE FROM current_spaces WHERE session=? AND platform=? AND user_id=?",
                identity,
            )
        else:
            self._conn.execute(
                """INSERT INTO current_spaces VALUES (?, ?, ?, ?)
                   ON CONFLICT(session, platform, user_id)
                   DO UPDATE SET space_id=excluded.space_id""",
                (*identity, space_id),
            )

    # ----- members -----

    def upsert_member(self, space_id: int, user: UserRef, nickname: str) -> Member:
        """Insert as MEMBER if unknown; otherwise update the nickname when the
        given one is non-empty. Never changes the role."""
        self._require_transaction()
        self._conn.execute(
            """INSERT INTO members VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(space_id, platform, user_id) DO UPDATE
               SET nickname=CASE WHEN ? != ''
                   THEN excluded.nickname ELSE members.nickname END""",
            (
                space_id,
                user.platform_id,
                user.user_id,
                nickname,
                SpaceRole.MEMBER,
                self._clock(),
                nickname,
            ),
        )
        return self.get_member(space_id, user)

    def get_member(self, space_id: int, user: UserRef) -> Member | None:
        """Return the member or ``None``."""
        row = self._conn.execute(
            "SELECT * FROM members WHERE space_id=? AND platform=? AND user_id=?",
            (space_id, user.platform_id, user.user_id),
        ).fetchone()
        return self._member(row) if row is not None else None

    def list_members(self, space_id: int) -> list[Member]:
        """Members ordered by ``joined_at`` then user key."""
        return [
            self._member(row)
            for row in self._conn.execute(
                """SELECT * FROM members WHERE space_id=?
               ORDER BY joined_at, platform || ':' || user_id, platform, user_id""",
                (space_id,),
            )
        ]

    def set_member_role(self, space_id: int, user: UserRef, role: SpaceRole) -> Member:
        """Change the role of an existing member (ADMIN or MEMBER only;
        ownership changes go through ``set_space_owner``).

        Raises:
            KeyError: Member does not exist.
            ValueError: ``role`` is OWNER, or the member is the owner.
        """
        self._require_transaction()
        if role == SpaceRole.OWNER:
            raise ValueError("Ownership changes require set_space_owner")
        member = self.get_member(space_id, user)
        if member is None:
            raise KeyError((space_id, user))
        if member.role == SpaceRole.OWNER:
            raise ValueError("The owner cannot be demoted directly")
        self._conn.execute(
            "UPDATE members SET role=? WHERE space_id=? AND platform=? AND user_id=?",
            (role, space_id, user.platform_id, user.user_id),
        )
        return self.get_member(space_id, user)

    # ----- tasks -----

    def create_task(self, draft: TaskDraft) -> Task:
        """Insert with the next per-space ``seq`` (max ever used + 1, never
        reused even after deletes), status TODO, ``reminded=False``."""
        self._require_transaction()
        now = self._clock()
        cursor = self._conn.execute(
            """INSERT INTO tasks (
                space_id, seq, title, status, due_at, priority, description,
                creator_platform, creator_user, created_session,
                created_at, updated_at, done_at, reminded
            ) VALUES (?, (SELECT next_seq FROM spaces WHERE id=?), ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, NULL, 0)""",
            (
                draft.space_id,
                draft.space_id,
                draft.title,
                TaskStatus.TODO,
                draft.due_at,
                draft.priority,
                draft.description,
                draft.creator.platform_id,
                draft.creator.user_id,
                draft.created_session,
                now,
                now,
            ),
        )
        self._conn.execute(
            "UPDATE spaces SET next_seq=next_seq+1 WHERE id=?", (draft.space_id,)
        )
        self._write_task_children(cursor.lastrowid, draft.assignees, draft.tags)
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id=?", (cursor.lastrowid,)
        ).fetchone()
        return self._task(row)

    def get_task(self, space_id: int, seq: int) -> Task | None:
        """Return the task or ``None``."""
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE space_id=? AND seq=?", (space_id, seq)
        ).fetchone()
        return self._task(row) if row is not None else None

    def update_task(self, task: Task) -> Task:
        """Persist all mutable fields of ``task`` (title, status, assignees,
        due_at, tags, priority, description, done_at, reminded), set
        ``updated_at`` to now and return the stored task.

        The store does not derive ``done_at``/``reminded``; callers set them.
        Raises ``KeyError`` if the task does not exist.
        """
        self._require_transaction()
        cursor = self._conn.execute(
            """UPDATE tasks SET title=?, status=?, due_at=?, priority=?, description=?,
               done_at=?, reminded=?, updated_at=? WHERE id=?""",
            (
                task.title,
                task.status,
                task.due_at,
                task.priority,
                task.description,
                task.done_at,
                task.reminded,
                self._clock(),
                task.id,
            ),
        )
        if not cursor.rowcount:
            raise KeyError(task.id)
        self._conn.execute("DELETE FROM task_assignees WHERE task_id=?", (task.id,))
        self._conn.execute("DELETE FROM task_tags WHERE task_id=?", (task.id,))
        self._write_task_children(task.id, task.assignees, task.tags)
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id=?", (task.id,)
        ).fetchone()
        return self._task(row)

    def delete_task(self, space_id: int, seq: int) -> None:
        """Delete the task with its assignees and tags (events are kept)."""
        self._require_transaction()
        self._conn.execute(
            "DELETE FROM tasks WHERE space_id=? AND seq=?", (space_id, seq)
        )

    def list_tasks(self, space_id: int, statuses: tuple[TaskStatus, ...]) -> list[Task]:
        """Tasks of a space in the given statuses, ordered by ``seq``."""
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        rows = self._conn.execute(
            f"SELECT * FROM tasks WHERE space_id=? AND status IN ({placeholders}) ORDER BY seq",
            (space_id, *statuses),
        )
        return [self._task(row) for row in rows]

    def list_tasks_assigned_to(
        self, user: UserRef, statuses: tuple[TaskStatus, ...]
    ) -> list[Task]:
        """Tasks in any space that have ``user`` among the assignees,
        ordered by ``due_at`` (``None`` last), then ``space_id``, ``seq``."""
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        rows = self._conn.execute(
            f"""SELECT t.* FROM tasks t JOIN task_assignees a ON a.task_id=t.id
                WHERE a.platform=? AND a.user_id=? AND t.status IN ({placeholders})
                ORDER BY t.due_at IS NULL, t.due_at, t.space_id, t.seq""",
            (user.platform_id, user.user_id, *statuses),
        )
        return [self._task(row) for row in rows]

    def list_unreminded_due(
        self, due_before: float, statuses: tuple[TaskStatus, ...]
    ) -> list[Task]:
        """Tasks in any space with ``reminded`` false, a non-null ``due_at``
        <= ``due_before`` and a status in ``statuses``, ordered by ``due_at``,
        then ``space_id``, ``seq``. (M2)"""
        raise NotImplementedError

    def list_done_before(self, done_before: float) -> list[Task]:
        """DONE tasks in any space with ``done_at`` <= ``done_before``,
        ordered by ``done_at``, then ``space_id``, ``seq``. (M2)"""
        raise NotImplementedError

    # ----- operation log -----

    def add_event(self, draft: TaskEventDraft) -> TaskEvent:
        """Append a log entry (``detail`` stored as JSON)."""
        self._require_transaction()
        cursor = self._conn.execute(
            """INSERT INTO events (space_id, task_seq, action, detail,
                actor_platform, actor_user, actor_label, session, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                draft.space_id,
                draft.task_seq,
                draft.action,
                json.dumps(draft.detail, ensure_ascii=False),
                draft.actor.platform_id if draft.actor else None,
                draft.actor.user_id if draft.actor else None,
                draft.actor_label,
                draft.session,
                self._clock(),
            ),
        )
        row = self._conn.execute(
            "SELECT * FROM events WHERE id=?", (cursor.lastrowid,)
        ).fetchone()
        return self._event(row)

    def list_events(
        self, space_id: int, task_seq: int | None = None, limit: int = 50
    ) -> list[TaskEvent]:
        """Newest first; filtered to one task when ``task_seq`` is given."""
        if task_seq is None:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE space_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
                (space_id, limit),
            )
        else:
            rows = self._conn.execute(
                """SELECT * FROM events WHERE space_id=? AND task_seq=?
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (space_id, task_seq, limit),
            )
        return [self._event(row) for row in rows]

    # ----- invites -----

    def set_invite(self, space_id: int, code_hash: str, expires_at: float) -> Invite:
        """Replace the space's invite (at most one per space)."""
        self._require_transaction()
        self._conn.execute(
            """INSERT INTO invites VALUES (?, ?, ?)
               ON CONFLICT(space_id) DO UPDATE
               SET code_hash=excluded.code_hash, expires_at=excluded.expires_at""",
            (space_id, code_hash, expires_at),
        )
        return Invite(space_id=space_id, code_hash=code_hash, expires_at=expires_at)

    def find_invite(self, code_hash: str) -> Invite | None:
        """Look up by hash; expired invites are returned too (caller checks)."""
        row = self._conn.execute(
            "SELECT * FROM invites WHERE code_hash=?", (code_hash,)
        ).fetchone()
        return Invite(**row) if row is not None else None

    def clear_invite(self, space_id: int) -> None:
        """Remove the space's invite if any."""
        self._require_transaction()
        self._conn.execute("DELETE FROM invites WHERE space_id=?", (space_id,))

    def _require_transaction(self) -> None:
        if not self._in_transaction:
            raise RuntimeError("Writes require store.transaction()")

    @staticmethod
    def _space(row: sqlite3.Row) -> Space:
        owner = (
            None
            if row["owner_platform"] is None
            else UserRef(platform_id=row["owner_platform"], user_id=row["owner_user"])
        )
        return Space(
            id=row["id"], name=row["name"], owner=owner, created_at=row["created_at"]
        )

    @staticmethod
    def _binding(row: sqlite3.Row) -> Binding:
        return Binding(
            session=row["session"], space_id=row["space_id"], bound_at=row["bound_at"]
        )

    @staticmethod
    def _member(row: sqlite3.Row) -> Member:
        return Member(
            space_id=row["space_id"],
            user=UserRef(platform_id=row["platform"], user_id=row["user_id"]),
            nickname=row["nickname"],
            role=SpaceRole(row["role"]),
            joined_at=row["joined_at"],
        )

    def _write_task_children(
        self, task_id: int, assignees: tuple[UserRef, ...], tags: tuple[str, ...]
    ) -> None:
        self._conn.executemany(
            "INSERT INTO task_assignees VALUES (?, ?, ?, ?)",
            (
                (task_id, position, user.platform_id, user.user_id)
                for position, user in enumerate(dict.fromkeys(assignees))
            ),
        )
        self._conn.executemany(
            "INSERT INTO task_tags VALUES (?, ?, ?)",
            (
                (task_id, position, tag)
                for position, tag in enumerate(dict.fromkeys(tags))
            ),
        )

    def _task(self, row: sqlite3.Row) -> Task:
        assignees = tuple(
            UserRef(platform_id=assigned["platform"], user_id=assigned["user_id"])
            for assigned in self._conn.execute(
                "SELECT platform, user_id FROM task_assignees WHERE task_id=? ORDER BY position",
                (row["id"],),
            )
        )
        tags = tuple(
            tag["tag"]
            for tag in self._conn.execute(
                "SELECT tag FROM task_tags WHERE task_id=? ORDER BY position",
                (row["id"],),
            )
        )
        return Task(
            id=row["id"],
            space_id=row["space_id"],
            seq=row["seq"],
            title=row["title"],
            status=TaskStatus(row["status"]),
            assignees=assignees,
            due_at=row["due_at"],
            tags=tags,
            priority=Priority(row["priority"]) if row["priority"] is not None else None,
            description=row["description"],
            creator=UserRef(
                platform_id=row["creator_platform"], user_id=row["creator_user"]
            ),
            created_session=row["created_session"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            done_at=row["done_at"],
            reminded=bool(row["reminded"]),
        )

    @staticmethod
    def _event(row: sqlite3.Row) -> TaskEvent:
        actor = (
            None
            if row["actor_platform"] is None
            else UserRef(platform_id=row["actor_platform"], user_id=row["actor_user"])
        )
        return TaskEvent(
            id=row["id"],
            space_id=row["space_id"],
            task_seq=row["task_seq"],
            action=EventAction(row["action"]),
            detail=json.loads(row["detail"]),
            actor=actor,
            actor_label=row["actor_label"],
            session=row["session"],
            created_at=row["created_at"],
        )
