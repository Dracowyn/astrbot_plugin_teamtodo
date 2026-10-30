"""Platform member lookups without network access."""

from types import SimpleNamespace

import pytest
from astrbot_plugin_teamtodo.core.models import GroupMember, TodoError, UserRef
from astrbot_plugin_teamtodo.services import group_members

from astrbot.core.platform.astrbot_message import Group, MessageMember


class FakeClient:
    def __init__(self, members=(), *, error_action=None):
        self.members = list(members)
        self.error_action = error_action
        self.calls = []

    async def call_action(self, action, **params):
        self.calls.append((action, params))
        if action == self.error_action:
            raise RuntimeError("platform failure detail")
        if action == "get_login_info":
            return {"user_id": 99}
        if action == "get_group_member_list":
            return self.members
        raise AssertionError(f"Unexpected action: {action}")


class FakeEvent:
    def __init__(self, *, platform="aiocqhttp", group_id="123", members=(), group=None):
        self.platform = platform
        self.group_id = group_id
        self.bot = FakeClient(members)
        self.group = group
        self.message_obj = SimpleNamespace(self_id="99")

    def get_group_id(self):
        return self.group_id

    def get_platform_name(self):
        return self.platform

    def get_platform_id(self):
        return "qq-main"

    def get_self_id(self):
        return self.message_obj.self_id

    async def get_group(self):
        if isinstance(self.group, Exception):
            raise self.group
        return self.group


class FakePlatform:
    def __init__(self, client, name="aiocqhttp"):
        self.client = client
        self.name = name

    def meta(self):
        return SimpleNamespace(name=self.name)

    def get_client(self):
        return self.client


class FakeContext:
    def __init__(self, platform):
        self.platform = platform
        self.requested_platform = None

    def get_platform_inst(self, platform_id):
        self.requested_platform = platform_id
        return self.platform


def onebot_members():
    return [
        {"user_id": 1, "card": " 群名片 ", "nickname": " 账号昵称 "},
        {"user_id": "2", "card": " 同名 ", "nickname": "同名"},
        {"user_id": 3, "card": "  ", "nickname": " 昵称 "},
        {"user_id": 4, "card": None, "nickname": None},
        {"user_id": 99, "nickname": "bot"},
        {"card": "missing id"},
        {"user_id": None, "nickname": "null id"},
        {"user_id": "", "nickname": "empty id"},
        None,
    ]


def expected_members():
    return [
        GroupMember(UserRef("qq-main", "1"), "群名片", ("群名片", "账号昵称")),
        GroupMember(UserRef("qq-main", "2"), "同名", ("同名",)),
        GroupMember(UserRef("qq-main", "3"), "昵称", ("昵称",)),
        GroupMember(UserRef("qq-main", "4"), "", ()),
    ]


@pytest.mark.asyncio
async def test_event_onebot_preserves_card_and_account_names_and_routes_bot():
    event = FakeEvent(members=onebot_members())
    assert await group_members.from_event(event) == expected_members()
    assert event.bot.calls == [
        ("get_group_member_list", {"group_id": 123, "self_id": "99"})
    ]


@pytest.mark.asyncio
async def test_event_onebot_string_group_id_is_passed_through():
    event = FakeEvent(group_id="plain-group", members=[{"user_id": 1}])
    assert await group_members.from_event(event) == [
        GroupMember(UserRef("qq-main", "1"), "", ())
    ]
    assert event.bot.calls == [
        ("get_group_member_list", {"group_id": "plain-group", "self_id": "99"})
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["aiocqhttp", "generic"])
@pytest.mark.parametrize("self_id", ["", None, "None", "  "])
async def test_event_without_bot_id_does_not_list(platform, self_id, warnings):
    group = Group("123", members=[MessageMember("1", "Alice")])
    event = FakeEvent(platform=platform, members=[{"user_id": 1}], group=group)
    event.message_obj.self_id = self_id
    assert await group_members.from_event(event) is None
    assert event.bot.calls == []
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_event_generic_group_members_are_normalized_and_bot_is_excluded():
    event = FakeEvent(
        platform="generic",
        group=Group(
            group_id="123",
            members=[
                MessageMember("1", " Alice "),
                MessageMember("2", None),
                MessageMember("99", "bot"),
                MessageMember("", "missing"),
                MessageMember(None, "missing"),
            ],
        ),
    )
    assert await group_members.from_event(event) == [
        GroupMember(UserRef("qq-main", "1"), "Alice", ("Alice",)),
        GroupMember(UserRef("qq-main", "2"), "", ()),
    ]
    assert event.bot.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [None, Group("123"), Group("123", members=[])])
async def test_event_generic_missing_group_or_members_returns_none(group):
    assert (
        await group_members.from_event(FakeEvent(platform="generic", group=group))
        is None
    )


@pytest.mark.asyncio
async def test_private_event_does_not_query_members():
    event = FakeEvent(group_id="")
    assert await group_members.from_event(event) is None
    assert event.bot.calls == []


@pytest.fixture
def warnings(monkeypatch):
    messages = []
    monkeypatch.setattr(
        group_members.logger, "warning", lambda *args, **kwargs: messages.append(args)
    )
    return messages


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["aiocqhttp", "generic"])
async def test_event_call_failure_warns_without_raising(platform, warnings):
    event = FakeEvent(platform=platform, group=RuntimeError("lookup failed"))
    event.bot.error_action = "get_group_member_list"
    assert await group_members.from_event(event) is None
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_event_failure_before_platform_lookup_is_also_caught(warnings):
    event = FakeEvent()

    def fail():
        raise RuntimeError("event failed")

    event.get_group_id = fail
    assert await group_members.from_event(event) is None
    assert len(warnings) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session",
    ["qq-main:FriendMessage:1", "qq-main:OtherMessage:1", "broken", "qq:bad:1"],
)
async def test_session_non_group_and_malformed_session_have_short_reason(session):
    context = FakeContext(None)
    with pytest.raises(TodoError, match="^该会话不是群聊$"):
        await group_members.from_session(context, session)
    assert context.requested_platform is None


@pytest.mark.asyncio
async def test_session_platform_not_loaded():
    context = FakeContext(None)
    with pytest.raises(TodoError, match="^该会话所在的平台未加载$"):
        await group_members.from_session(context, "qq-main:GroupMessage:123")
    assert context.requested_platform == "qq-main"


@pytest.mark.asyncio
async def test_session_unsupported_platform():
    client = FakeClient()
    context = FakeContext(FakePlatform(client, name="generic"))
    with pytest.raises(TodoError, match="^该平台不支持获取群成员列表$"):
        await group_members.from_session(context, "qq-main:GroupMessage:123")
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error_action", ["get_login_info", "get_group_member_list"])
async def test_session_call_failures_hide_exception_text(error_action, warnings):
    context = FakeContext(FakePlatform(FakeClient(error_action=error_action)))
    with pytest.raises(TodoError, match="^获取群成员失败$"):
        await group_members.from_session(context, "qq-main:GroupMessage:123")
    assert len(warnings) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "group_id,expected_id",
    [("00123", 123), ("plain-group", "plain-group"), ("123:thread", "123:thread")],
)
async def test_session_members_use_platform_instance_and_do_not_guess_extra_parts(
    group_id, expected_id
):
    client = FakeClient(onebot_members())
    context = FakeContext(FakePlatform(client))
    result = await group_members.from_session(
        context, f"qq-main:GroupMessage:{group_id}"
    )
    assert result == expected_members()
    assert context.requested_platform == "qq-main"
    assert ("get_group_member_list", {"group_id": expected_id}) in client.calls
    assert ("get_login_info", {}) in client.calls


@pytest.mark.asyncio
async def test_session_success_with_no_human_members_returns_empty_list():
    context = FakeContext(FakePlatform(FakeClient([{"user_id": 99}])))
    assert await group_members.from_session(context, "qq-main:GroupMessage:123") == []
