from astrbot_plugin_teamtodo.core.models import Actor, UserRef
from astrbot_plugin_teamtodo.services.event_context import (
    ASSIGNED_SUFFIX,
    assignment_chain,
)

import astrbot.api.message_components as Comp

GROUP = "qq:GroupMessage:1000"


def actor(private=False):
    return Actor(
        user=UserRef(platform_id="qq", user_id="me"),
        nickname="Me",
        session="qq:FriendMessage:me" if private else GROUP,
        is_private=private,
        is_bot_admin=False,
        is_session_admin=private,
    )


def user(uid, platform="qq"):
    return UserRef(platform_id=platform, user_id=uid)


def test_chain_mentions_new_assignees_after_the_text():
    # Arrange
    added = (user("bob"), user("eve"))

    # Act
    chain = assignment_chain("已创建：#1", actor(), added, {"qq:bob": "Bob"})

    # Assert
    assert isinstance(chain[0], Comp.Plain) and chain[0].text == "已创建：#1\n"
    assert [(c.qq, c.name) for c in chain[1:3]] == [("bob", "Bob"), ("eve", "")]
    assert isinstance(chain[3], Comp.Plain) and chain[3].text == ASSIGNED_SUFFIX


def test_chain_skips_actor_and_other_platforms():
    # Act
    only_self = assignment_chain("t", actor(), (user("me"),), {})
    foreign = assignment_chain("t", actor(), (user("bob", "tg"), user("me")), {})
    mixed = assignment_chain("t", actor(), (user("me"), user("bob")), {})

    # Assert
    assert only_self is None
    assert foreign is None
    assert [c.qq for c in mixed if isinstance(c, Comp.At)] == ["bob"]


def test_no_chain_in_private_chat_or_without_new_assignees():
    # Act / Assert
    assert assignment_chain("t", actor(private=True), (user("bob"),), {}) is None
    assert assignment_chain("t", actor(), (), {}) is None
