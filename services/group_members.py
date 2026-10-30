"""List the members of a chat group from the platform (M3.1, design 7).

Only QQ via OneBot (``aiocqhttp``) can list a whole group; it is queried
with ``get_group_member_list`` so both the group card and the account
nickname are available. Other platforms fall back to ``event.get_group()``,
which is often empty; WebUI sync supports ``aiocqhttp`` only.
"""

from __future__ import annotations

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType

from ..core.models import GroupMember, TodoError, UserRef


def _onebot_members(
    members: list[dict], platform_id: str, self_id: str
) -> list[GroupMember]:
    """Build member identities and distinct names from a OneBot response."""
    result = []
    for member in members:
        if not isinstance(member, dict) or member.get("user_id") in (None, ""):
            continue
        user_id = str(member["user_id"])
        if user_id == self_id:
            continue
        card = (member.get("card") or "").strip()
        nickname = (member.get("nickname") or "").strip()
        names = tuple(dict.fromkeys(name for name in (card, nickname) if name))
        result.append(
            GroupMember(
                user=UserRef(platform_id=platform_id, user_id=user_id),
                nickname=card or nickname,
                names=names,
            )
        )
    return result


async def from_event(event: AstrMessageEvent) -> list[GroupMember] | None:
    """Members of the group the event came from, excluding the bot itself.

    Args:
        event: A chat event.

    Returns:
        The members (``UserRef.platform_id`` = ``event.get_platform_id()``),
        or ``None`` for private chats, events without a bot account id (the
        bot could not be excluded), platforms that cannot list members, and
        failed calls (logged as a warning, never raised).
    """
    try:
        group_id = event.get_group_id()
        if not group_id:
            return None
        platform_id = event.get_platform_id()
        self_id = str(event.get_self_id() or "").strip()
        if self_id in ("", "None"):
            logger.warning("[teamtodo] No bot account id on the event; not listing")
            return None
        if event.get_platform_name() == "aiocqhttp":
            group_id = str(group_id)
            members = await event.bot.call_action(
                "get_group_member_list",
                group_id=int(group_id) if group_id.isdigit() else group_id,
                self_id=self_id,
            )
            return _onebot_members(members, platform_id, self_id)
        group = await event.get_group()
        if group is None or not group.members:
            return None
        result = []
        for member in group.members:
            if member.user_id in (None, "") or str(member.user_id) == self_id:
                continue
            nickname = (member.nickname or "").strip()
            result.append(
                GroupMember(
                    user=UserRef(platform_id=platform_id, user_id=str(member.user_id)),
                    nickname=nickname,
                    names=(nickname,) if nickname else (),
                )
            )
        return result
    except Exception as exc:
        logger.warning("[teamtodo] Failed to fetch group members from event: %s", exc)
        return None


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
    try:
        parsed = MessageSession.from_str(session)
    except (ValueError, TypeError) as exc:
        raise TodoError("该会话不是群聊") from exc
    if parsed.message_type != MessageType.GROUP_MESSAGE:
        raise TodoError("该会话不是群聊")
    platform = context.get_platform_inst(parsed.platform_id)
    if platform is None:
        raise TodoError("该会话所在的平台未加载")
    if platform.meta().name != "aiocqhttp":
        raise TodoError("该平台不支持获取群成员列表")
    try:
        client = platform.get_client()
        login = await client.call_action("get_login_info")
        group_id = parsed.session_id
        members = await client.call_action(
            "get_group_member_list",
            group_id=int(group_id) if group_id.isdigit() else group_id,
        )
        return _onebot_members(members, parsed.platform_id, str(login["user_id"]))
    except Exception as exc:
        logger.warning(
            "[teamtodo] Failed to fetch group members for session %s: %s", session, exc
        )
        raise TodoError("获取群成员失败") from exc
