"""List the members of a chat group from the platform (M3.1, design 7).

Only QQ via OneBot (``aiocqhttp``) can list a whole group; it is queried
with ``get_group_member_list`` so both the group card and the account
nickname are available. Other platforms fall back to ``event.get_group()``,
which is often empty; WebUI sync supports ``aiocqhttp`` only.
"""

from __future__ import annotations

from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context

from ..core.models import GroupMember


async def from_event(event: AstrMessageEvent) -> list[GroupMember] | None:
    """Members of the group the event came from, excluding the bot itself.

    Args:
        event: A chat event.

    Returns:
        The members (``UserRef.platform_id`` = ``event.get_platform_id()``),
        or ``None`` for private chats, platforms that cannot list members,
        and failed calls (logged as a warning, never raised).
    """
    raise NotImplementedError


async def from_session(context: Context, session: str) -> list[GroupMember]:
    """Members of the group behind a ``unified_msg_origin``, excluding the bot.

    Args:
        context: The plugin context (``get_platform_inst``).
        session: A bound session string.

    Returns:
        The members (``UserRef.platform_id`` = the session's platform id).

    Raises:
        TodoError: The session is not a group chat, its platform instance is
            not loaded, the platform cannot list members, or the call failed;
            ``str(exc)`` is a short Chinese reason for the WebUI.
    """
    raise NotImplementedError
