"""Shared data model for the team todo plugin.

Everything here is pure (no astrbot imports) and frozen. State changes go
through ``dataclasses.replace``; always construct these classes with keyword
arguments so that adding a field cannot silently shift values.

Times are Unix seconds (UTC, float). Conversion to the configured time zone
happens only in parsing and rendering.
"""

from __future__ import annotations

import dataclasses as dcs
from enum import StrEnum

MAX_TITLE_CHARS = 200
MAX_DESCRIPTION_CHARS = 4000
MAX_TAG_CHARS = 20
MAX_TAGS = 10
MAX_ASSIGNEES = 20
MAX_SPACE_NAME_CHARS = 30
"""Limits enforced by services for every entry point (commands, LLM, WebUI)."""
WEBUI_PLATFORM = "webui"
"""``UserRef.platform_id`` of the creator of tasks made in the WebUI; the
``user_id`` is the dashboard username. Never bound to a chat session (M3)."""


class TaskStatus(StrEnum):
    """Lifecycle of a task. ``ARCHIVED`` tasks are hidden from the board."""

    TODO = "todo"
    DOING = "doing"
    DONE = "done"
    ARCHIVED = "archived"


ACTIVE_STATUSES = (TaskStatus.TODO, TaskStatus.DOING, TaskStatus.DONE)
"""Statuses shown on the board and in ``/todo list``."""


class Priority(StrEnum):
    """Optional task priority."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SpaceRole(StrEnum):
    """Role of a member inside one space."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


@dcs.dataclass(frozen=True)
class UserRef:
    """Identity of a person: platform instance id plus platform user id.

    ``platform_id`` is ``event.get_platform_id()`` (the adapter instance id
    that also prefixes ``unified_msg_origin``), not the adapter type name.
    The same human on two platforms is two different users.
    """

    platform_id: str
    user_id: str

    @property
    def key(self) -> str:
        """Stable string form used as a map key and stored in the database."""
        return f"{self.platform_id}:{self.user_id}"


@dcs.dataclass(frozen=True)
class Mention:
    """A user referenced by an ``At`` component in the incoming message."""

    user: UserRef
    name: str
    """Display name carried by the component; may be empty."""


@dcs.dataclass(frozen=True)
class Actor:
    """The person performing an operation, resolved from the event only.

    Never build an ``Actor`` from LLM-supplied arguments.
    """

    user: UserRef
    nickname: str
    session: str
    """``unified_msg_origin`` of the conversation the request came from."""
    is_private: bool
    is_bot_admin: bool
    """AstrBot administrator (``event.is_admin()``)."""
    is_session_admin: bool
    """May run session-level operations (join/leave/default/claim).

    True for bot admins, for the user in a private chat, and for the group
    owner or group admins in a group chat (see ``services/access.py``).
    """


@dcs.dataclass(frozen=True)
class GroupMember:
    """A chat group member as listed by the platform (M3.1)."""

    user: UserRef
    """``platform_id`` is the platform instance id of the group's session."""
    nickname: str
    """Display name: the group card when set, else the account nickname."""
    names: tuple[str, ...]
    """Every non-empty name (card, account nickname) for exact matching."""


@dcs.dataclass(frozen=True)
class Space:
    """A container of tasks, bound to zero or more sessions."""

    id: int
    name: str
    owner: UserRef | None
    """``None`` while an auto-created group space waits to be claimed."""
    created_at: float


@dcs.dataclass(frozen=True)
class Binding:
    """A session bound to a space."""

    session: str
    space_id: int
    bound_at: float


@dcs.dataclass(frozen=True)
class Member:
    """A user known to a space, with their role and latest nickname.

    The space owner is stored as a member with role ``OWNER``; ``Space.owner``
    and that row are kept in sync by the store.
    """

    space_id: int
    user: UserRef
    nickname: str
    role: SpaceRole
    joined_at: float


@dcs.dataclass(frozen=True)
class TaskDraft:
    """Input for creating a task; the store allocates ``id`` and ``seq``."""

    space_id: int
    title: str
    creator: UserRef
    created_session: str
    assignees: tuple[UserRef, ...] = ()
    due_at: float | None = None
    tags: tuple[str, ...] = ()
    priority: Priority | None = None
    description: str = ""


@dcs.dataclass(frozen=True)
class Task:
    """A task card."""

    id: int
    """Global row id; never shown to users."""
    space_id: int
    seq: int
    """Per-space short number, shown as ``#seq``. Never reused in a space."""
    title: str
    status: TaskStatus
    assignees: tuple[UserRef, ...]
    """Ordered, without duplicates; may be empty."""
    due_at: float | None
    tags: tuple[str, ...]
    """Ordered, without duplicates."""
    priority: Priority | None
    description: str
    """Markdown source, displayed as plain text in the first version."""
    creator: UserRef
    created_session: str
    created_at: float
    updated_at: float
    done_at: float | None
    """Set when the status becomes DONE, cleared when it leaves DONE/ARCHIVED."""
    reminded: bool
    """Due reminder already sent; reset whenever ``due_at`` changes."""


class EventAction(StrEnum):
    """Kinds of operation log entries."""

    CREATE = "create"
    UPDATE = "update"
    STATUS = "status"
    ASSIGN = "assign"
    DELETE = "delete"
    ARCHIVE = "archive"


@dcs.dataclass(frozen=True)
class TaskEventDraft:
    """Input for one operation log entry.

    ``task_seq`` is kept even after the task is deleted. ``actor`` is ``None``
    for automatic actions (auto-archive); ``actor_label`` is set instead for
    WebUI actions (the dashboard username).
    """

    space_id: int
    task_seq: int
    action: EventAction
    detail: dict
    """JSON-serialisable. For UPDATE: ``{"field": [old, new], ...}``.
    For DELETE: ``{"snapshot": <task as dict>}``."""
    actor: UserRef | None = None
    actor_label: str = ""
    session: str = ""


@dcs.dataclass(frozen=True)
class TaskEvent:
    """A stored operation log entry."""

    id: int
    space_id: int
    task_seq: int
    action: EventAction
    detail: dict
    actor: UserRef | None
    actor_label: str
    session: str
    created_at: float


class SpaceEventAction(StrEnum):
    """Kinds of space-level log entries (M3). ``detail`` per action:

    - CREATE ``{"name": str}`` (``space new`` or auto-create)
    - RENAME ``{"name": [old, new]}``
    - BIND / UNBIND ``{"session": str}`` (``space new``, auto-create, ``join``
      / ``leave``, WebUI unbind)
    - CLAIM ``{}``
    - ROLE ``{"user": UserRef.key, "role": [old, new]}``
    - INVITE ``{"expires_at": float}`` (invite created or reset)
    - SYNC ``{"session": str, "added": int}`` (WebUI group member sync, M3.1)
    - SHARE ``{"op": "on" | "reset" | "off"}`` (public link created, replaced
      or removed, M4; the token itself is never logged)

    Deleting a space deletes its log, so there is no DELETE entry.
    """

    CREATE = "create"
    RENAME = "rename"
    BIND = "bind"
    UNBIND = "unbind"
    CLAIM = "claim"
    ROLE = "role"
    INVITE = "invite"
    SYNC = "sync"
    SHARE = "share"


@dcs.dataclass(frozen=True)
class SpaceEventDraft:
    """Input for one space-level log entry (M3); same actor rules as
    ``TaskEventDraft``."""

    space_id: int
    action: SpaceEventAction
    detail: dict
    actor: UserRef | None = None
    actor_label: str = ""
    session: str = ""


@dcs.dataclass(frozen=True)
class SpaceEvent:
    """A stored space-level log entry (M3)."""

    id: int
    space_id: int
    action: SpaceEventAction
    detail: dict
    actor: UserRef | None
    actor_label: str
    session: str
    created_at: float


@dcs.dataclass(frozen=True)
class Invite:
    """The single active invite of a space. Only the hash is stored."""

    space_id: int
    code_hash: str
    """SHA-256 hex of the normalized code (stripped, uppercased); see
    ``services.access.hash_invite_code``."""
    expires_at: float


@dcs.dataclass(frozen=True)
class ShareLink:
    """The single public board link of a space (M4).

    The token is stored in plain text so admins can copy the link again; the
    database already holds every task the link reveals. A space has a link
    exactly when it is shared: turning sharing off deletes the row.
    """

    space_id: int
    token: str
    created_at: float


class TodoError(Exception):
    """A user-facing failure; ``str(exc)`` is shown to the user verbatim."""
