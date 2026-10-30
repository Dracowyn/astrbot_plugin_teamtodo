"""Shared public link transitions and field-whitelisted board data (M4)."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from datetime import tzinfo
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from ..core import permissions as perms
from ..core.models import (
    ACTIVE_STATUSES,
    Actor,
    ShareLink,
    SpaceEventAction,
    SpaceEventDraft,
    TaskStatus,
    TodoError,
    UserRef,
)
from ..core.store import Store

if TYPE_CHECKING:
    from .todo_service import TodoService

UNAVAILABLE = "公开看板服务未开启或未配置对外访问地址，请联系 AstrBot 管理员"
NO_LINK = "当前空间没有开启公开链接"
NO_RESET = "当前空间还没有公开链接，用 /todo share on 开启"
USAGE = "用法：/todo share [on|reset|off]"
LINK_PREFIX = "当前空间的公开看板（只读，拿到链接的人都能查看）："
UNKNOWN_MEMBER = "未知成员"


class ShareConflict(TodoError):
    """A sharing state conflict, mapped to HTTP 409 by the WebUI."""


def new_token() -> str:
    """Return a 192-bit URL-safe public link token."""
    return secrets.token_urlsafe(24)


def normalize_base_url(value: str) -> str:
    """Normalize the configured external address or reject it.

    Args:
        value: Raw ``public_base_url`` from the plugin config.

    Returns:
        The address without surrounding whitespace and trailing slashes, or
        ``""`` when it is not a plain ``http(s)://host[:port][/path]`` URL:
        inner whitespace or control characters, a query, a fragment, user
        info, a missing host or an invalid port all make it invalid, since
        the result is pasted verbatim into chat replies.
    """
    value = value.strip().rstrip("/")
    if not value.startswith(("http://", "https://")) or any(
        char.isspace() or not char.isprintable() or char in "?#" for char in value
    ):
        return ""
    try:
        parts = urlsplit(value)
        _ = parts.port  # raises ValueError for a non-numeric or out-of-range port
    except ValueError:
        return ""
    if not parts.hostname or parts.username is not None:
        return ""
    return value


class ShareService:
    """Manage public links inside the caller's permission-checked transaction."""

    def __init__(
        self,
        store: Store,
        tz: tzinfo,
        *,
        enabled: bool = False,
        base_url: str = "",
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Configure sharing without binding a server.

        Args:
            store: Open repository shared with the caller.
            tz: Configured IANA time zone for the public response.
            enabled: Whether the public server is configured to run.
            base_url: External address, including any reverse-proxy prefix.
            clock: Wall-clock Unix seconds.
        """
        self._store = store
        self._tz = tz
        self._clock = clock
        self.base_url = normalize_base_url(base_url)
        self.available = enabled and bool(self.base_url)

    def url(self, link: ShareLink) -> str | None:
        """Build a trailing-slash link, or None without an external address."""
        return f"{self.base_url}/b/{link.token}/" if self.base_url else None

    def change(
        self,
        space_id: int,
        action: str,
        *,
        actor: UserRef | None = None,
        actor_label: str = "",
        session: str = "",
    ) -> ShareLink | None:
        """Apply on/reset/off; caller holds the transaction and checks access.

        Args:
            space_id: Existing target space.
            action: Exactly on, reset or off.
            actor: Chat user, absent for WebUI operations.
            actor_label: Dashboard username for WebUI operations.
            session: Chat session, empty for WebUI operations.

        Returns:
            The active link, or None after turning sharing off.

        Raises:
            ShareConflict: Sharing is unavailable or no link can be reset.
            TodoError: The action is not supported.
        """
        if action not in ("on", "reset", "off"):
            raise TodoError(USAGE)
        if action != "off" and not self.available:
            raise ShareConflict(UNAVAILABLE)
        link = self._store.get_share(space_id)
        if action == "on" and link is not None:
            return link
        if action == "reset" and link is None:
            raise ShareConflict(NO_RESET)
        if action == "off":
            if link is None:
                return None
            self._store.clear_share(space_id)
            link = None
        else:
            link = self._store.set_share(space_id, new_token())
        self._store.add_space_event(
            SpaceEventDraft(
                space_id=space_id,
                action=SpaceEventAction.SHARE,
                detail={"op": action},
                actor=actor,
                actor_label=actor_label,
                session=session,
            )
        )
        return link

    def board_for_token(self, token: str) -> dict[str, Any] | None:
        """Return only the public board whitelist, or None for an invalid link."""
        link = self._store.find_share(token)
        if link is None:
            return None
        space = self._store.get_space(link.space_id)
        if space is None:
            return None
        tasks = self._store.list_tasks(space.id, ACTIVE_STATUSES)
        # Several adapters (and the actor fallback in event_context) store the
        # user id, or its first characters, as the nickname when the platform
        # gives no name. Such values would publish the id, so they count as
        # missing.
        nicknames = {}
        for member in self._store.list_members(space.id):
            nickname = (member.nickname or "").strip()
            user_id = member.user.user_id
            if (
                nickname
                and nickname != user_id
                and not (len(nickname) >= 6 and user_id.startswith(nickname))
            ):
                nicknames[member.user] = nickname
        now = self._clock()
        return {
            "space": {"name": space.name},
            "tasks": [
                {
                    "seq": task.seq,
                    "title": task.title,
                    "status": task.status.value,
                    "assignees": [
                        {"nickname": nicknames.get(user, UNKNOWN_MEMBER)}
                        for user in task.assignees
                    ],
                    "due_at": task.due_at,
                    "tags": list(task.tags),
                    "priority": task.priority.value
                    if task.priority is not None
                    else None,
                    "description": task.description,
                    "done_at": task.done_at,
                    "updated_at": task.updated_at,
                    "overdue": task.due_at is not None
                    and task.due_at < now
                    and task.status in (TaskStatus.TODO, TaskStatus.DOING),
                }
                for task in tasks
            ],
            "updated_at": max((space.created_at, *(task.updated_at for task in tasks))),
            "server_time": now,
            "timezone": str(self._tz),
        }


def chat_share(
    service: TodoService, actor: Actor, auto_name: str, action: str = ""
) -> str:
    """Resolve, authorize and manage the current space's link atomically."""
    with service._store.transaction():
        space, member = service._space(actor, auto_name, None)
        perms.require(
            perms.can_manage_space(actor, member, space), perms.Op.MANAGE_SPACE
        )
        if action == "":
            link = service._store.get_share(space.id)
        else:
            previous = service._store.get_share(space.id)
            link = service._shares.change(
                space.id, action, actor=actor.user, session=actor.session
            )
            if action == "off" and previous is not None:
                return "已关闭当前空间的公开链接"
        if link is None:
            return NO_LINK
        url = service._shares.url(link)
        if url is None:
            raise ShareConflict(UNAVAILABLE)
        return f"{LINK_PREFIX}\n{url}"
