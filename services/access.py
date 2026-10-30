"""Access helpers: session admins, invite codes, join rate limit, confirmations.

All state here is in memory and per process. Clocks are injectable for tests;
the in-memory helpers default to ``time.monotonic`` while invite expiry uses
the wall-clock ``now`` that is also stored in the database.
"""

from __future__ import annotations

import dataclasses as dcs
import hashlib
import secrets
import time
from collections import deque
from collections.abc import Callable, Hashable

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..core.models import Invite

GROUP_CACHE_TTL_SECONDS = 300.0
INVITE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
"""Upper-case letters and digits without the look-alikes 0/O/1/I/L."""
INVITE_CODE_LENGTH = 12
INVITE_TTL_SECONDS = 7 * 24 * 3600.0
JOIN_FAILURE_LIMIT = 5
JOIN_FAILURE_WINDOW_SECONDS = 3600.0
CONFIRM_TTL_SECONDS = 60.0


@dcs.dataclass(frozen=True)
class SessionInfo:
    """What the plugin needs to know about the sender's session."""

    is_session_admin: bool
    group_name: str | None
    """Group name when the platform provides it; ``None`` in private chats."""


@dcs.dataclass(frozen=True)
class _GroupRoles:
    owner: str | None
    admins: frozenset[str]
    name: str | None
    fetched_at: float


class SessionAdminResolver:
    """Decides whether the sender of an event is a session admin.

    Bot admins and private-chat users always are. In a group, the group owner
    and group admins are, as reported by ``event.get_group()``; the result is
    cached per session for ``ttl`` seconds. Platforms that do not fill
    ``group_owner`` / ``group_admins`` yield "not admin", so only bot admins
    can run session operations there. Lookup failures fail closed and are not
    cached.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        ttl: float = GROUP_CACHE_TTL_SECONDS,
    ) -> None:
        """Create a resolver with an empty cache.

        Args:
            clock: Monotonic time source in seconds.
            ttl: How long one group lookup is reused.
        """
        self._clock = clock
        self._ttl = ttl
        self._cache: dict[str, _GroupRoles] = {}

    async def resolve(self, event: AstrMessageEvent) -> SessionInfo:
        """Resolve the sender's session-admin status and the group name.

        Args:
            event: The incoming message event.

        Returns:
            The session info. Never raises for platform lookup failures.
        """
        is_bot_admin = event.is_admin()
        if event.is_private_chat():
            return SessionInfo(is_session_admin=True, group_name=None)

        session = event.unified_msg_origin
        now = self._clock()
        roles = self._cache.get(session)
        if roles is None or now - roles.fetched_at >= self._ttl:
            roles = None
            try:
                group = await event.get_group()
                if group is None:
                    raise LookupError("get_group returned None")
            except Exception as exc:
                # Fail closed and do not cache, so the next message retries.
                logger.warning(
                    "[teamtodo] Group lookup failed for session %s; treating "
                    "sender as a non-admin: %s",
                    session,
                    exc,
                )
            else:
                roles = _GroupRoles(
                    owner=str(group.group_owner) if group.group_owner else None,
                    admins=frozenset(str(a) for a in group.group_admins or ()),
                    name=group.group_name or None,
                    fetched_at=now,
                )
                # Drop stale entries so the cache only holds recently active groups.
                self._cache = {
                    key: value
                    for key, value in self._cache.items()
                    if now - value.fetched_at < self._ttl
                }
                self._cache[session] = roles

        if roles is None:
            return SessionInfo(is_session_admin=is_bot_admin, group_name=None)
        sender = str(event.get_sender_id() or "")
        is_group_admin = bool(sender) and (
            sender == roles.owner or sender in roles.admins
        )
        return SessionInfo(
            is_session_admin=is_bot_admin or is_group_admin, group_name=roles.name
        )


def generate_invite_code() -> str:
    """Generate a fresh invite code.

    Returns:
        ``INVITE_CODE_LENGTH`` characters drawn from ``INVITE_ALPHABET`` with
        ``secrets``. Show it to the user once; never log or store it.
    """
    return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(INVITE_CODE_LENGTH))


def hash_invite_code(code: str) -> str:
    """Normalize a user-typed invite code and hash it for storage or lookup.

    Normalization strips surrounding whitespace and upper-cases, so users may
    type the code in lower case.

    Args:
        code: The code as generated or as typed by a user.

    Returns:
        The SHA-256 hex digest of the normalized code.
    """
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def new_invite(space_id: int, now: float) -> tuple[str, Invite]:
    """Create a new invite for a space.

    Args:
        space_id: The space the invite joins.
        now: Current wall-clock Unix time in seconds.

    Returns:
        The plain code to show once, and the ``Invite`` to store (hash only,
        expiring ``INVITE_TTL_SECONDS`` after ``now``).
    """
    code = generate_invite_code()
    invite = Invite(
        space_id=space_id,
        code_hash=hash_invite_code(code),
        expires_at=now + INVITE_TTL_SECONDS,
    )
    return code, invite


def is_invite_expired(invite: Invite, now: float) -> bool:
    """Whether an invite can no longer be used.

    Args:
        invite: The stored invite.
        now: Current wall-clock Unix time in seconds.

    Returns:
        True once ``now`` reaches ``invite.expires_at``.
    """
    return now >= invite.expires_at


class JoinRateLimiter:
    """Limits failed ``join`` attempts per session over a rolling window.

    Call ``is_blocked`` before trying a code and ``record_failure`` after a
    bad or expired code. Memory is bounded: each session keeps at most
    ``limit`` timestamps and sessions with no recent failure are dropped.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        limit: int = JOIN_FAILURE_LIMIT,
        window: float = JOIN_FAILURE_WINDOW_SECONDS,
    ) -> None:
        """Create an empty limiter.

        Args:
            clock: Monotonic time source in seconds.
            limit: Failures allowed per session within ``window``.
            window: Rolling window length in seconds.
        """
        self._clock = clock
        self._limit = limit
        self._window = window
        self._failures: dict[str, deque[float]] = {}

    def is_blocked(self, session: str) -> bool:
        """Whether the session has used up its failed attempts.

        Args:
            session: ``unified_msg_origin`` of the session.

        Returns:
            True when ``limit`` failures happened within the last ``window``.
        """
        now = self._clock()
        recent = [t for t in self._failures.get(session, ()) if now - t < self._window]
        return len(recent) >= self._limit

    def record_failure(self, session: str) -> None:
        """Record one failed join attempt for the session.

        Args:
            session: ``unified_msg_origin`` of the session.
        """
        now = self._clock()
        self._failures = {
            key: times
            for key, times in self._failures.items()
            if now - times[-1] < self._window
        }
        self._failures.setdefault(session, deque(maxlen=self._limit)).append(now)


class ConfirmationCodes:
    """Single-use 4-digit codes confirming a dangerous command.

    A code is bound to a caller-chosen key such as
    ``(session, user_key, "space.delete", space_id)``. Issuing again for the
    same key replaces the old code. Any ``consume`` call removes the pending
    code, so a wrong guess requires a new confirmation round.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        ttl: float = CONFIRM_TTL_SECONDS,
    ) -> None:
        """Create an empty store.

        Args:
            clock: Monotonic time source in seconds.
            ttl: Seconds a code stays valid.
        """
        self._clock = clock
        self._ttl = ttl
        self._pending: dict[Hashable, tuple[str, float]] = {}

    def issue(self, key: Hashable) -> str:
        """Issue a new code for ``key``, replacing any pending one.

        Args:
            key: Identifies the actor, session, command and target.

        Returns:
            The 4-digit code to show to the user.
        """
        now = self._clock()
        self._pending = {
            k: entry for k, entry in self._pending.items() if entry[1] > now
        }
        code = f"{secrets.randbelow(10000):04d}"
        self._pending[key] = (code, now + self._ttl)
        return code

    def consume(self, key: Hashable, code: str) -> bool:
        """Check and use up the pending code for ``key``.

        Args:
            key: The same key passed to ``issue``.
            code: The code typed by the user.

        Returns:
            True when a code was pending, not expired and equal to ``code``.
            The pending code is removed in every case.
        """
        entry = self._pending.pop(key, None)
        if entry is None or self._clock() >= entry[1]:
            return False
        # Compare bytes: compare_digest rejects non-ASCII str input.
        return secrets.compare_digest(entry[0].encode(), code.strip().encode())
