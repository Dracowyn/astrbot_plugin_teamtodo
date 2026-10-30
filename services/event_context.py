"""Read the acting user and message parts from an AstrBot event.

Shared by the ``/todo`` command handler in ``main.py`` and the LLM tools, so
both entry points derive identity the same way: from the event only, never
from user- or LLM-supplied arguments.
"""

from __future__ import annotations

from collections.abc import Mapping

import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent

from ..core.models import Actor, Mention, TodoError, UserRef
from ..core.parsing import Part
from .access import SessionInfo

PLUGIN_NAME = "astrbot_plugin_teamtodo"
"""``name`` in ``metadata.yaml``; also the key of per-session plugin switches."""
ASSIGNED_SUFFIX = " 你被指派了这个任务"


def message_parts(event: AstrMessageEvent) -> list[Part]:
    """Convert the message chain into parser parts.

    ``Plain`` becomes text and ``At`` becomes a ``Mention``; ``@all`` and
    mentions of the bot itself are dropped, other components are ignored.

    Args:
        event: The incoming message event.

    Returns:
        Parts in message order.
    """
    platform_id = event.get_platform_id()
    self_id = str(event.get_self_id())
    parts: list[Part] = []
    for component in event.get_messages():
        if isinstance(component, Comp.Plain):
            parts.append(component.text)
        elif isinstance(component, Comp.At):
            user_id = str(component.qq)
            if user_id in ("all", self_id):
                continue
            parts.append(
                Mention(
                    user=UserRef(platform_id=platform_id, user_id=user_id),
                    name=component.name or "",
                )
            )
    return parts


def build_actor(event: AstrMessageEvent, info: SessionInfo) -> Actor:
    """Build the acting user from the event only (never from LLM input).

    Args:
        event: The incoming message event.
        info: Session admin status from ``SessionAdminResolver``.

    Returns:
        The actor.

    Raises:
        TodoError: The platform did not provide a sender id.
    """
    sender_id = str(event.get_sender_id() or "")
    if not sender_id:
        raise TodoError("无法识别发送者")
    return Actor(
        user=UserRef(platform_id=event.get_platform_id(), user_id=sender_id),
        nickname=event.get_sender_name() or sender_id,
        session=event.unified_msg_origin,
        is_private=event.is_private_chat(),
        is_bot_admin=event.is_admin(),
        is_session_admin=info.is_session_admin,
    )


def assignment_chain(
    text: str,
    actor: Actor,
    added: tuple[UserRef, ...],
    nicknames: Mapping[str, str],
) -> list[Comp.BaseMessageComponent] | None:
    """Build the assignment notification (design 6, M2).

    The chain is ``text``, a newline, one ``At`` per new assignee and then
    ``ASSIGNED_SUFFIX``. Only group chats get one. The actor is never
    mentioned, and neither are users of another platform: their ids mean
    nothing here and could even point at an unrelated local user.

    Args:
        text: Reply text shown before the mentions.
        actor: The user who made the change.
        added: Users that became assignees in this operation.
        nicknames: ``UserRef.key`` -> display name.

    Returns:
        The components, or ``None`` when nobody should be mentioned.
    """
    if actor.is_private:
        return None
    targets = [
        user
        for user in added
        if user != actor.user and user.platform_id == actor.user.platform_id
    ]
    if not targets:
        return None
    return [
        Comp.Plain(text=f"{text}\n"),
        *(
            Comp.At(qq=user.user_id, name=nicknames.get(user.key) or "")
            for user in targets
        ),
        Comp.Plain(text=ASSIGNED_SUFFIX),
    ]
