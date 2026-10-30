import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from astrbot_plugin_teamtodo import main
from astrbot_plugin_teamtodo.core.models import (
    GroupMember,
    Mention,
    SpaceEventAction,
    SpaceRole,
    Task,
    TaskStatus,
    TodoError,
    UserRef,
)
from astrbot_plugin_teamtodo.core.permissions import DENIAL_MESSAGES, Op
from astrbot_plugin_teamtodo.services.access import SessionInfo
from astrbot_plugin_teamtodo.services.event_context import ASSIGNED_SUFFIX
from astrbot_plugin_teamtodo.services.reminders import Reminder

import astrbot.api.message_components as Comp

GROUP = "qq:GroupMessage:1000"


class FakeEvent:
    """Duck-typed stand-in for AstrMessageEvent."""

    def __init__(
        self,
        *components,
        sender="u1",
        name="Alice",
        private=False,
        bot_admin=False,
        group=None,
        platform_name="aiocqhttp",
    ):
        self.components = list(components)
        self.platform_name = platform_name
        self.sender = sender
        self.name = name
        self.private = private
        self.bot_admin = bot_admin
        self.group = group
        self.unified_msg_origin = "qq:FriendMessage:u1" if private else GROUP

    def get_messages(self):
        return self.components

    def get_platform_id(self):
        return "qq"

    def get_platform_name(self):
        return self.platform_name

    def get_self_id(self):
        return "bot"

    def get_sender_id(self):
        return self.sender

    def get_sender_name(self):
        return self.name

    def get_group_id(self):
        return "" if self.private else "1000"

    def is_private_chat(self):
        return self.private

    def is_admin(self):
        return self.bot_admin

    async def get_group(self):
        return self.group

    def plain_result(self, text):
        return text

    def chain_result(self, chain):
        return chain


class FakeContext:
    """Records registered tools and proactive messages."""

    def __init__(self):
        self.tools = []
        self.web_apis = []
        self.sent = []
        self.send_result = True
        self.send_error = None

    def add_llm_tools(self, *tools):
        self.tools.extend(tools)

    def register_web_api(self, route, handler, methods, desc):
        self.web_apis.append((route, handler, methods, desc))

    async def send_message(self, session, chain):
        if self.send_error is not None:
            raise self.send_error
        self.sent.append((session, chain.chain))
        return self.send_result


class FakeReminderService:
    def __init__(self, store, **kwargs):
        self.store = store
        self.kwargs = kwargs


class FakeLoop:
    """Stands in for ``run_loop``: records its arguments and waits forever."""

    def __init__(self, error=None):
        self.calls = []
        self.cancelled = False
        self.error = error

    async def __call__(self, service, send):
        self.calls.append((service, send))
        if self.error is not None:
            raise self.error
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def owner_group():
    return SimpleNamespace(
        group_owner="u1", group_admins=["mod"], group_name="Dev Team"
    )


@pytest.fixture
def enabled(monkeypatch):
    """Per-session plugin switch; flip ``enabled["value"]`` to disable."""
    state = {"value": True, "calls": []}

    async def is_enabled(session, name):
        state["calls"].append((session, name))
        return state["value"]

    monkeypatch.setattr(
        main.SessionPluginManager,
        "is_plugin_enabled_for_session",
        staticmethod(is_enabled),
    )
    return state


@pytest.fixture
def loop(monkeypatch):
    fake = FakeLoop()
    monkeypatch.setattr(main, "run_loop", fake)
    monkeypatch.setattr(main, "ReminderService", FakeReminderService)
    return fake


@pytest.fixture
def titles(monkeypatch):
    """Records ``note_session_title`` calls; set ``titles.error`` to fail."""

    class Recorder(list):
        error = None

    calls = Recorder()

    def note(self, session, title):
        if calls.error is not None:
            raise calls.error
        calls.append((session, title))

    monkeypatch.setattr(main.TodoService, "note_session_title", note)
    return calls


@pytest_asyncio.fixture
async def plugin(tmp_path, monkeypatch, loop, enabled):
    monkeypatch.setattr(main.StarTools, "get_data_dir", lambda name: tmp_path)
    instance = main.TeamTodoPlugin(FakeContext(), {"timezone": "Asia/Shanghai"})
    yield instance
    await instance.terminate()


async def run(plugin, *components, **kwargs):
    event = FakeEvent(*components, **kwargs)
    return [reply async for reply in plugin.todo(event)]


@pytest.fixture
def admin_group_members(monkeypatch):
    """Fake group membership without replacing the command's real store."""
    fetch = AsyncMock(return_value=[])
    monkeypatch.setattr(main.group_members, "from_event", fetch)
    return fetch


def test_message_parts_converts_plain_and_mentions():
    # Arrange
    event = FakeEvent(
        Comp.At(qq="bot"),
        Comp.Plain(text="/todo assign #1 +"),
        Comp.At(qq=42, name="Bob"),
        Comp.AtAll(),
        Comp.Image(file="x.png"),
    )

    # Act
    parts = main.message_parts(event)

    # Assert
    assert parts == [
        "/todo assign #1 +",
        Mention(user=UserRef(platform_id="qq", user_id="42"), name="Bob"),
    ]


def test_build_actor_uses_event_identity():
    # Arrange
    event = FakeEvent(sender="u9", name="", bot_admin=True)

    # Act
    actor = main.build_actor(event, SessionInfo(is_session_admin=True, group_name=None))

    # Assert
    assert actor.user == UserRef(platform_id="qq", user_id="u9")
    assert actor.nickname == "u9"
    assert actor.session == GROUP
    assert actor.is_bot_admin is True
    assert actor.is_session_admin is True
    assert actor.is_private is False


def test_build_actor_rejects_missing_sender():
    # Act / Assert
    with pytest.raises(TodoError):
        main.build_actor(
            FakeEvent(sender=""), SessionInfo(is_session_admin=False, group_name=None)
        )


def test_split_word():
    # Act / Assert
    assert main.split_word(["  add  x y"]) == ("add", ["x y"])
    assert main.split_word(["  ", "new"]) == ("new", [])
    assert main.split_word([]) == ("", [])


def test_load_timezone_falls_back(monkeypatch):
    # Arrange
    warnings = []
    monkeypatch.setattr(main.logger, "warning", lambda *a, **k: warnings.append(a))

    # Act
    tz = main.load_timezone("Not/AZone")

    # Assert
    assert str(tz) == "Asia/Shanghai"
    assert warnings
    assert str(main.load_timezone("UTC")) == "UTC"


def test_load_timezone_without_zone_database(monkeypatch):
    # Arrange
    def missing(name):
        raise main.ZoneInfoNotFoundError(name)

    monkeypatch.setattr(main, "ZoneInfo", missing)

    # Act
    tz = main.load_timezone("Asia/Shanghai")

    # Assert
    assert tz.utcoffset(None) == timedelta(hours=8)


@pytest.mark.asyncio
async def test_reply_while_not_initialized(plugin):
    # Act
    replies = await run(plugin, Comp.Plain(text="/todo"))

    # Assert
    assert replies == ["协作 TODO 插件正在启动，请稍后再试"]


@pytest.mark.asyncio
async def test_bare_todo_and_help_show_help_text(plugin):
    # Arrange
    await plugin.initialize()

    # Act
    bare = await run(plugin, Comp.Plain(text="/todo"))
    explicit = await run(plugin, Comp.Plain(text="/todo help"))
    unknown = await run(plugin, Comp.Plain(text="/todo frobnicate"))

    # Assert
    assert bare == explicit == [main.help_text()]
    assert unknown[0].startswith("未知命令")


@pytest.mark.asyncio
async def test_end_to_end_task_commands(plugin):
    # Arrange
    await plugin.initialize()
    owner = {"group": owner_group()}

    # Act
    added = await run(
        plugin,
        Comp.Plain(text="/todo add 写周报 "),
        Comp.At(qq="bob", name="Bob"),
        Comp.Plain(text=" 明天 !高 [doc]"),
        **owner,
    )
    listed = await run(plugin, Comp.Plain(text="/todo list"), **owner)
    shown = await run(plugin, Comp.Plain(text="/todo show #1"), **owner)
    started = await run(plugin, Comp.Plain(text="/todo start 1"), **owner)
    edited = await run(plugin, Comp.Plain(text="/todo edit #1 新标题"), **owner)
    desc = await run(
        plugin,
        Comp.Plain(text="/todo desc #1 第一行\n联系 "),
        Comp.At(qq="bob", name="Bob"),
        **owner,
    )
    assigned = await run(
        plugin, Comp.Plain(text="/todo assign #1 -"), Comp.At(qq="bob"), **owner
    )
    mine = await run(plugin, Comp.Plain(text="/todo mine"), **owner)
    deleted = await run(plugin, Comp.Plain(text="/todo delete #1"), **owner)

    # Assert
    assert added[0][0].text.startswith("已创建：#1 [待办] 写周报 @Bob")
    assert listed[0].startswith("DevTeam\n待办\n#1")
    assert "创建人：Alice" in shown[0]
    assert started[0].startswith("已更新：#1 [进行中]")
    assert "新标题" in edited[0]
    assert desc == ["已更新任务 #1 的描述"]
    assert assigned[0].startswith("已更新负责人：#1 [进行中] 新标题")
    assert mine == ["没有指派给你的任务"]
    assert deleted == ["已删除任务 #1：新标题"]


@pytest.mark.asyncio
async def test_nameless_mention_renders_as_user_id(plugin):
    # Arrange
    await plugin.initialize()

    # Act
    added = await run(plugin, Comp.Plain(text="/todo add 交接 "), Comp.At(qq="carol"))
    listed = await run(plugin, Comp.Plain(text="/todo list"))

    # Assert
    assert added[0][0].text == "已创建：#1 [待办] 交接 @carol\n"
    assert "交接 @carol" in listed[0]


@pytest.mark.asyncio
async def test_end_to_end_space_commands(plugin):
    # Arrange
    await plugin.initialize()
    owner = {"group": owner_group()}

    # Act
    spaces = await run(plugin, Comp.Plain(text="/todo spaces"), **owner)
    created = await run(plugin, Comp.Plain(text="/todo space new Ops"), **owner)
    used = await run(plugin, Comp.Plain(text="/todo use DevTeam"), **owner)
    default = await run(plugin, Comp.Plain(text="/todo default Ops"), **owner)
    renamed = await run(plugin, Comp.Plain(text="/todo space rename Core"), **owner)
    invite = await run(plugin, Comp.Plain(text="/todo invite"), **owner)
    code = invite[0].split("：")[1].split("\n")[0]
    joined_twice = await run(plugin, Comp.Plain(text=f"/todo join {code}"), **owner)
    await run(
        plugin,
        Comp.Plain(text="/todo admin +"),
        Comp.At(qq="mod", name="Moderator"),
        **owner,
    )
    member = plugin._store.get_member(1, UserRef(platform_id="qq", user_id="mod"))
    assert member is not None
    assert member.role is SpaceRole.ADMIN
    assert member.nickname == "Moderator"
    claim = await run(plugin, Comp.Plain(text="/todo claim"), **owner)
    left = await run(plugin, Comp.Plain(text="/todo leave Ops"), **owner)
    confirm = await run(plugin, Comp.Plain(text="/todo space delete Core"), **owner)
    delete_code = confirm[0].rsplit(" ", 1)[1]
    deleted = await run(
        plugin, Comp.Plain(text=f"/todo space delete Core {delete_code}"), **owner
    )

    # Assert
    assert spaces == ["DevTeam（默认）（当前）"]
    assert created == ["已创建空间「Ops」，并切换为你的当前空间"]
    assert used == ["已切换到空间「DevTeam」"]
    assert default == ["本会话的默认空间已改为「Ops」"]
    assert renamed == ["当前空间已改名为「Core」"]
    assert len(code) == 12
    assert joined_twice == ["本会话已经绑定了空间「Core」"]
    assert claim == [DENIAL_MESSAGES[Op.CLAIM]]
    assert left == ["本会话已解绑空间「Ops」，空间和任务仍然保留"]
    assert "60 秒内" in confirm[0]
    assert deleted == ["已删除空间「Core」及其全部任务"]


@pytest.mark.asyncio
@pytest.mark.parametrize("private", [False, True], ids=["group", "private"])
@pytest.mark.parametrize("nickname", ["Saved Name", ""], ids=["named", "nameless"])
async def test_admin_existing_raw_id_preserves_name_without_fetch(
    plugin, admin_group_members, private, nickname
):
    """Use persisted names, falling back to the ID, in either chat type."""
    await plugin.initialize()
    context = {"private": private, "group": owner_group()}
    await run(plugin, Comp.Plain(text="/todo list"), **context)
    target = UserRef(platform_id="qq", user_id="123456")
    with plugin._store.transaction():
        plugin._store.upsert_member(1, target, nickname)

    promoted = await run(plugin, Comp.Plain(text="/todo admin +123456"), **context)
    member = plugin._store.get_member(1, target)
    assert member.role is SpaceRole.ADMIN
    assert member.nickname == nickname
    assert f"{nickname or '123456'}：管理员" in promoted[0]

    revoked = await run(plugin, Comp.Plain(text="/todo admin -123456"), **context)

    member = plugin._store.get_member(1, target)
    assert member.role is SpaceRole.MEMBER
    assert member.nickname == nickname
    assert f"{nickname or '123456'}：成员" in revoked[0]
    events = plugin._store.list_space_events(1)[:2]
    assert [(event.action, event.detail) for event in events] == [
        (SpaceEventAction.ROLE, {"user": target.key, "role": ["admin", "member"]}),
        (SpaceEventAction.ROLE, {"user": target.key, "role": ["member", "admin"]}),
    ]
    admin_group_members.assert_not_called()


@pytest.mark.asyncio
async def test_admin_unknown_raw_id_registers_group_name_and_actor_platform(
    plugin, monkeypatch, admin_group_members
):
    """Resolve IDs in the actor's platform instance and persist the role log."""
    monkeypatch.setattr(FakeEvent, "get_platform_id", lambda self: "qq-instance")
    target = UserRef(platform_id="qq-instance", user_id="123456")
    wrong_platform = UserRef(platform_id="qq", user_id="123456")
    admin_group_members.return_value = [
        GroupMember(wrong_platform, "Other Instance", ("Other Instance",)),
        GroupMember(target, "Group Card", ("Group Card", "Account Name")),
    ]
    await plugin.initialize()

    replies = await run(
        plugin, Comp.Plain(text="/todo admin +123456"), group=owner_group()
    )

    member = plugin._store.get_member(1, target)
    assert member is not None
    assert member.role is SpaceRole.ADMIN
    assert member.nickname == "Group Card"
    assert plugin._store.get_member(1, wrong_platform) is None
    assert "Group Card：管理员" in replies[0]
    event = plugin._store.list_space_events(1)[0]
    assert event.action is SpaceEventAction.ROLE
    assert event.detail == {"user": target.key, "role": ["member", "admin"]}
    assert event.actor == UserRef(platform_id="qq-instance", user_id="u1")
    assert event.session == GROUP
    admin_group_members.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("members", [[], None], ids=["missing", "unavailable"])
async def test_admin_unknown_raw_id_group_miss_is_clear_and_does_not_write(
    plugin, admin_group_members, members
):
    """Missing or unavailable group membership cannot authorize registration."""
    await plugin.initialize()
    await run(plugin, Comp.Plain(text="/todo list"), group=owner_group())
    baseline_members = plugin._store.list_members(1)
    baseline_events = plugin._store.list_space_events(1)
    admin_group_members.return_value = members

    replies = await run(
        plugin, Comp.Plain(text="/todo admin +123456"), group=owner_group()
    )

    assert replies == ["找不到 QQ 号 123456：不是空间成员，也不在本群成员列表里"]
    assert plugin._store.list_members(1) == baseline_members
    assert plugin._store.list_space_events(1) == baseline_events
    admin_group_members.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_unknown_raw_id_in_private_never_fetches(
    plugin, admin_group_members
):
    """Private chats cannot establish a raw target's group membership."""
    await plugin.initialize()
    await run(plugin, Comp.Plain(text="/todo list"), private=True)
    baseline_members = plugin._store.list_members(1)
    baseline_events = plugin._store.list_space_events(1)

    replies = await run(plugin, Comp.Plain(text="/todo admin +123456"), private=True)

    assert replies == ["找不到 QQ 号 123456：不是空间成员，也不在本群成员列表里"]
    assert plugin._store.list_members(1) == baseline_members
    assert plugin._store.list_space_events(1) == baseline_events
    admin_group_members.assert_not_called()


@pytest.mark.asyncio
async def test_admin_unknown_raw_id_removal_never_fetches_or_registers(
    plugin, admin_group_members
):
    """Revoking a nonmember retains the existing error even for a group hit."""
    target = UserRef(platform_id="qq", user_id="123456")
    admin_group_members.return_value = [
        GroupMember(target, "Group Card", ("Group Card",))
    ]
    await plugin.initialize()
    await run(plugin, Comp.Plain(text="/todo list"), group=owner_group())
    baseline_members = plugin._store.list_members(1)
    baseline_events = plugin._store.list_space_events(1)

    replies = await run(
        plugin, Comp.Plain(text="/todo admin -123456"), group=owner_group()
    )

    assert replies == ["123456 还不是这个空间的成员"]
    assert plugin._store.list_members(1) == baseline_members
    assert plugin._store.list_space_events(1) == baseline_events
    admin_group_members.assert_not_called()


@pytest.mark.asyncio
async def test_admin_multiple_unknown_raw_ids_fetch_group_once(
    plugin, admin_group_members
):
    """One group listing supplies every unknown added ID in a command."""
    targets = [
        GroupMember(UserRef("qq", "123456"), "First Card", ("First Card",)),
        GroupMember(UserRef("qq", "234567"), "Second Card", ("Second Card",)),
    ]
    admin_group_members.return_value = targets
    await plugin.initialize()

    replies = await run(
        plugin, Comp.Plain(text="/todo admin +123456 +234567"), group=owner_group()
    )

    for target in targets:
        member = plugin._store.get_member(1, target.user)
        assert member is not None
        assert member.role is SpaceRole.ADMIN
        assert member.nickname == target.nickname
        assert f"{target.nickname}：管理员" in replies[0]
    events = plugin._store.list_space_events(1)[:2]
    assert {event.detail["user"] for event in events} == {
        target.user.key for target in targets
    }
    assert all(event.action is SpaceEventAction.ROLE for event in events)
    admin_group_members.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_mention_only_registers_without_group_fetch(
    plugin, admin_group_members
):
    """Explicit mentions retain their existing automatic registration behavior."""
    await plugin.initialize()

    replies = await run(
        plugin,
        Comp.Plain(text="/todo admin +"),
        Comp.At(qq="123456", name="Mention Name"),
        group=owner_group(),
    )

    target = UserRef(platform_id="qq", user_id="123456")
    member = plugin._store.get_member(1, target)
    assert member is not None
    assert member.nickname == "Mention Name"
    assert member.role is SpaceRole.ADMIN
    assert "Mention Name：管理员" in replies[0]
    admin_group_members.assert_not_called()


@pytest.mark.asyncio
async def test_admin_mixed_mentions_and_raw_ids_update_together(
    plugin, admin_group_members
):
    """Promote mentioned and group-listed users while revoking another raw ID."""
    await plugin.initialize()
    await run(plugin, Comp.Plain(text="/todo list"), group=owner_group())
    revoked = UserRef(platform_id="qq", user_id="345678")
    with plugin._store.transaction():
        plugin._store.upsert_member(1, revoked, "Former Admin")
        plugin._store.set_member_role(1, revoked, SpaceRole.ADMIN)
    raw = UserRef(platform_id="qq", user_id="234567")
    mentioned = UserRef(platform_id="qq", user_id="123456")
    admin_group_members.return_value = [GroupMember(raw, "Group Card", ("Group Card",))]

    replies = await run(
        plugin,
        Comp.Plain(text="/todo admin +"),
        Comp.At(qq=mentioned.user_id, name="Mention Name"),
        Comp.Plain(text=" +234567 -345678"),
        group=owner_group(),
    )

    for user, name, role in [
        (mentioned, "Mention Name", SpaceRole.ADMIN),
        (raw, "Group Card", SpaceRole.ADMIN),
        (revoked, "Former Admin", SpaceRole.MEMBER),
    ]:
        member = plugin._store.get_member(1, user)
        assert member.nickname == name
        assert member.role is role
        assert (
            f"{name}：{'管理员' if role is SpaceRole.ADMIN else '成员'}" in replies[0]
        )
    events = plugin._store.list_space_events(1)[:3]
    assert {event.detail["user"] for event in events} == {
        mentioned.key,
        raw.key,
        revoked.key,
    }
    assert all(event.action is SpaceEventAction.ROLE for event in events)
    admin_group_members.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_first", [True, False], ids=["raw-first", "mention-first"])
async def test_admin_raw_id_with_same_explicit_mention_keeps_name_without_fetch(
    plugin, admin_group_members, raw_first
):
    """Mention proof wins over duplicate raw IDs regardless of target order."""
    await plugin.initialize()
    mention = Comp.At(qq="123456", name="Mention Name")
    components = (
        [Comp.Plain(text="/todo admin +123456 +"), mention]
        if raw_first
        else [Comp.Plain(text="/todo admin +"), mention, Comp.Plain(text=" +123456")]
    )

    replies = await run(plugin, *components, group=owner_group())

    target = UserRef(platform_id="qq", user_id="123456")
    member = plugin._store.get_member(1, target)
    assert member is not None
    assert member.nickname == "Mention Name"
    assert member.role is SpaceRole.ADMIN
    assert replies[0].count("Mention Name：管理员") == 1
    events = [
        event
        for event in plugin._store.list_space_events(1)
        if event.action is SpaceEventAction.ROLE
    ]
    assert len(events) == 1
    assert events[0].detail == {"user": target.key, "role": ["member", "admin"]}
    admin_group_members.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_exists", [False, True], ids=["group-hit", "space-member"]
)
async def test_admin_second_unknown_raw_id_failure_rolls_back_every_target(
    plugin, admin_group_members, first_exists
):
    """A later unresolved ID prevents both earlier registration and promotion."""
    await plugin.initialize()
    await run(plugin, Comp.Plain(text="/todo list"), group=owner_group())
    first = UserRef(platform_id="qq", user_id="111111")
    if first_exists:
        with plugin._store.transaction():
            plugin._store.upsert_member(1, first, "Saved Name")
    admin_group_members.return_value = [
        GroupMember(first, "Group Card", ("Group Card",))
    ]
    baseline_members = plugin._store.list_members(1)
    baseline_events = plugin._store.list_space_events(1)

    replies = await run(
        plugin,
        Comp.Plain(text="/todo admin +111111 +123456"),
        group=owner_group(),
    )

    assert replies == ["找不到 QQ 号 123456：不是空间成员，也不在本群成员列表里"]
    assert plugin._store.list_members(1) == baseline_members
    assert plugin._store.list_space_events(1) == baseline_events
    admin_group_members.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "components",
    [
        [Comp.Plain(text="/todo admin +123456 +234567 -345678")],
        [Comp.Plain(text="/todo admin +99999999")],
        [Comp.Plain(text="/todo admin +"), Comp.At(qq="mod", name="Moderator")],
        [Comp.Plain(text="/todo admin")],
        [Comp.Plain(text="/todo admin nonsense")],
        [
            Comp.Plain(
                text="/todo admin " + " ".join(f"+{10000 + i}" for i in range(30))
            )
        ],
    ],
    ids=["raw-ids", "other-id", "mention", "empty", "malformed", "over-cap"],
)
async def test_admin_non_owner_gets_one_denial_without_lookups_or_fetch(
    plugin, admin_group_members, monkeypatch, components
):
    """Denial comes first and reveals nothing about the given targets."""
    await plugin.initialize()
    await run(plugin, Comp.Plain(text="/todo list"), group=owner_group())
    target = UserRef("qq", "123456")
    with plugin._store.transaction():
        plugin._store.upsert_member(1, target, "Known")
    baseline = (plugin._store.list_members(1), plugin._store.list_space_events(1))
    lookups = []
    get_member = plugin._store.get_member

    def recording_get_member(space_id, user):
        lookups.append(user)
        return get_member(space_id, user)

    monkeypatch.setattr(plugin._store, "get_member", recording_get_member)
    admin_group_members.return_value = [GroupMember(target, "Card", ("Card",))]

    replies = await run(plugin, *components, sender="pleb", group=owner_group())

    assert replies == [DENIAL_MESSAGES[Op.MANAGE_ADMINS]]
    assert set(lookups) <= {UserRef("qq", "pleb")}
    admin_group_members.assert_not_called()
    assert (plugin._store.list_members(1), plugin._store.list_space_events(1)) == (
        baseline
    )


@pytest.mark.asyncio
async def test_admin_denial_in_unbound_group_creates_no_space(
    plugin, admin_group_members
):
    await plugin.initialize()

    replies = await run(
        plugin, Comp.Plain(text="/todo admin +123456"), sender="pleb", group=None
    )

    assert replies == [DENIAL_MESSAGES[Op.MANAGE_ADMINS]]
    assert plugin._store.list_bindings(GROUP) == []
    admin_group_members.assert_not_called()


@pytest.mark.asyncio
async def test_admin_owner_over_target_cap_gets_usage_without_fetch(
    plugin, admin_group_members
):
    await plugin.initialize()
    ids = " ".join(f"+{10000 + i}" for i in range(21))

    replies = await run(
        plugin, Comp.Plain(text=f"/todo admin {ids}"), group=owner_group()
    )

    assert replies[0].startswith("一次最多操作 20 人")
    admin_group_members.assert_not_called()


@pytest.mark.asyncio
async def test_admin_space_switch_during_fetch_cannot_skip_verification(
    plugin, admin_group_members
):
    """TOCTOU: a member of space A is not registered in B without the group."""
    await plugin.initialize()
    owner = {"group": owner_group()}
    await run(plugin, Comp.Plain(text="/todo list"), **owner)
    member_of_a = UserRef("qq", "123456")
    verified = UserRef("qq", "234567")
    with plugin._store.transaction():
        plugin._store.upsert_member(1, member_of_a, "Known")
    await run(plugin, Comp.Plain(text="/todo space new Ops"), **owner)
    await run(plugin, Comp.Plain(text="/todo use DevTeam"), **owner)
    actor = main.build_actor(
        FakeEvent(), SessionInfo(is_session_admin=True, group_name="Dev Team")
    )

    async def fetch(event):
        plugin._service.use_space(actor, "Ops")
        return [GroupMember(verified, "Card", ("Card",))]

    admin_group_members.side_effect = fetch
    baseline = {
        space_id: (
            plugin._store.list_members(space_id),
            plugin._store.list_space_events(space_id),
        )
        for space_id in (1, 2)
    }

    replies = await run(plugin, Comp.Plain(text="/todo admin +123456 +234567"), **owner)

    assert replies == ["找不到 QQ 号 123456：不是空间成员，也不在本群成员列表里"]
    admin_group_members.assert_awaited_once()
    for space_id, (members, events) in baseline.items():
        assert plugin._store.list_members(space_id) == members
        assert plugin._store.list_space_events(space_id) == events


@pytest.mark.asyncio
async def test_admin_raw_id_error_uses_neutral_label_off_qq(
    plugin, admin_group_members
):
    await plugin.initialize()

    replies = await run(
        plugin,
        Comp.Plain(text="/todo admin +123456"),
        group=owner_group(),
        platform_name="telegram",
    )

    assert replies == ["找不到 用户 ID 123456：不是空间成员，也不在本群成员列表里"]
    admin_group_members.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_terminate_during_fetch_fails_cleanly(plugin, admin_group_members):
    await plugin.initialize()

    async def fetch(event):
        plugin._service = None
        return []

    admin_group_members.side_effect = fetch

    replies = await run(
        plugin, Comp.Plain(text="/todo admin +123456"), group=owner_group()
    )

    assert replies == [main.GENERIC_FAILURE]


@pytest.mark.asyncio
async def test_usage_errors_and_denials_are_replied(plugin):
    # Arrange
    await plugin.initialize()

    # Act
    no_ref = await run(plugin, Comp.Plain(text="/todo show"))
    no_name = await run(plugin, Comp.Plain(text="/todo use"))
    bad_list = await run(plugin, Comp.Plain(text="/todo list a b"))
    denied = await run(plugin, Comp.Plain(text="/todo join ABC"), sender="pleb")

    # Assert
    assert no_ref == ["请提供有效的任务编号，如 #3"]
    assert no_name == ["用法：/todo use <空间名>"]
    assert bad_list == ["用法：/todo list [空间名] [all]"]
    assert "会话管理员" in denied[0]


@pytest.mark.asyncio
async def test_unexpected_error_gives_generic_reply(plugin, monkeypatch):
    # Arrange
    await plugin.initialize()

    def boom(*args, **kwargs):
        raise RuntimeError("secret internals")

    monkeypatch.setattr(plugin._service, "list_tasks", boom)
    monkeypatch.setattr(main.logger, "exception", lambda *a, **k: None)

    # Act
    replies = await run(plugin, Comp.Plain(text="/todo list"))

    # Assert
    assert replies == [main.GENERIC_FAILURE]


@pytest.mark.asyncio
async def test_terminate_closes_store(plugin):
    # Arrange
    await plugin.initialize()

    # Act
    await plugin.terminate()

    # Assert
    assert plugin._store is None
    assert await run(plugin, Comp.Plain(text="/todo")) == [
        "协作 TODO 插件正在启动，请稍后再试"
    ]


# ----- M2: tools, assignment replies, reminders -----------------------------


def test_registers_the_seven_llm_tools(plugin):
    # Act
    names = [tool.name for tool in plugin.context.tools]

    # Assert
    assert names == [
        "teamtodo_add_task",
        "teamtodo_list_tasks",
        "teamtodo_get_task",
        "teamtodo_update_task",
        "teamtodo_list_spaces",
        "teamtodo_use_space",
        "teamtodo_sync_members",
    ]


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, (60, 7)),
        ({"remind_before_minutes": 15, "auto_archive_days": 0}, (15, 0)),
        ({"remind_before_minutes": -5, "auto_archive_days": -1}, (0, 0)),
        ({"remind_before_minutes": "30", "auto_archive_days": "x"}, (30, 7)),
        ({"remind_before_minutes": None, "auto_archive_days": [1]}, (60, 7)),
    ],
)
def test_reminder_config_is_parsed_and_clamped(config, expected, monkeypatch):
    # Arrange
    monkeypatch.setattr(main.logger, "warning", lambda *a, **k: None)

    # Act
    instance = main.TeamTodoPlugin(FakeContext(), config)

    # Assert
    assert (instance._remind_minutes, instance._archive_days) == expected


@pytest.mark.asyncio
async def test_initialize_starts_reminder_loop_and_terminate_stops_it(plugin, loop):
    # Arrange
    plugin._remind_minutes, plugin._archive_days = 30, 3

    # Act
    await plugin.initialize()
    await asyncio.sleep(0)
    service, send = loop.calls[0]
    await plugin.terminate()

    # Assert
    assert service.kwargs == {"lead_seconds": 1800, "archive_after_days": 3}
    assert service.store is not None
    assert send == plugin._send_reminder
    assert loop.cancelled is True
    assert plugin._reminder_task is None
    assert plugin._store is None and plugin._service is None


@pytest.mark.asyncio
async def test_terminate_logs_a_crashed_reminder_loop(plugin, loop, monkeypatch):
    # Arrange
    loop.error = RuntimeError("boom")
    logged = []
    monkeypatch.setattr(main.logger, "exception", lambda *a, **k: logged.append(a))
    await plugin.initialize()
    await asyncio.sleep(0)

    # Act
    await plugin.terminate()

    # Assert
    assert logged
    assert plugin._store is None


def reminder(session, *assignees):
    task = Task(
        id=1,
        space_id=1,
        seq=3,
        title="写周报",
        status=TaskStatus.TODO,
        assignees=tuple(assignees),
        due_at=1_800_000_000.0,
        tags=(),
        priority=None,
        description="",
        creator=UserRef(platform_id="qq", user_id="u1"),
        created_session=session,
        created_at=0.0,
        updated_at=0.0,
        done_at=None,
        reminded=True,
    )
    return Reminder(
        session=session, space_name="Dev", task=task, nicknames={"qq:bob": "Bob"}
    )


@pytest.fixture
def reminder_text(monkeypatch):
    monkeypatch.setattr(
        main, "reminder_text", lambda task, space, now, tz: f"[due] {space}#{task.seq}"
    )


@pytest.mark.asyncio
async def test_group_reminder_mentions_assignees_on_the_same_platform(
    plugin, reminder_text
):
    # Arrange
    item = reminder(
        GROUP,
        UserRef(platform_id="qq", user_id="bob"),
        UserRef(platform_id="tg", user_id="77"),
        UserRef(platform_id="qq", user_id="eve"),
    )

    # Act
    await plugin._send_reminder(item)

    # Assert
    session, chain = plugin.context.sent[0]
    assert session == GROUP
    assert [(c.qq, c.name) for c in chain[:2]] == [("bob", "Bob"), ("eve", "")]
    assert len(chain) == 3 and chain[2].text == " [due] Dev#3"


@pytest.mark.asyncio
async def test_private_or_unassigned_reminder_is_text_only(plugin, reminder_text):
    # Act
    await plugin._send_reminder(
        reminder("qq:FriendMessage:u1", UserRef(platform_id="qq", user_id="bob"))
    )
    await plugin._send_reminder(reminder(GROUP))

    # Assert
    assert [[c.text for c in chain] for _, chain in plugin.context.sent] == [
        ["[due] Dev#3"],
        ["[due] Dev#3"],
    ]


@pytest.mark.asyncio
async def test_reminder_skipped_when_plugin_disabled_for_session(
    plugin, enabled, reminder_text
):
    # Arrange
    enabled["value"] = False

    # Act
    await plugin._send_reminder(reminder(GROUP))

    # Assert
    assert enabled["calls"] == [(GROUP, main.PLUGIN_NAME)]
    assert plugin.context.sent == []


@pytest.mark.asyncio
async def test_reminder_send_failures_are_only_logged(
    plugin, reminder_text, monkeypatch
):
    # Arrange
    warnings, errors = [], []
    monkeypatch.setattr(main.logger, "warning", lambda *a, **k: warnings.append(a))
    monkeypatch.setattr(main.logger, "exception", lambda *a, **k: errors.append(a))
    plugin.context.send_result = False

    # Act
    await plugin._send_reminder(reminder(GROUP))
    plugin.context.send_error = RuntimeError("network")
    await plugin._send_reminder(reminder(GROUP))
    await plugin._send_reminder(reminder("not-a-session"))

    # Assert
    assert len(warnings) == 1
    assert len(errors) == 2


@pytest.mark.asyncio
async def test_add_and_assign_mention_new_assignees_in_groups(plugin):
    # Arrange
    await plugin.initialize()

    # Act
    added = await run(
        plugin, Comp.Plain(text="/todo add 写周报 "), Comp.At(qq="bob", name="Bob")
    )
    self_added = await run(
        plugin, Comp.Plain(text="/todo add 自己的 "), Comp.At(qq="u1", name="Alice")
    )
    assigned = await run(
        plugin,
        Comp.Plain(text="/todo assign #1 +"),
        Comp.At(qq="eve", name="Eve"),
        Comp.Plain(text=" +"),
        Comp.At(qq="bob", name="Bob"),
    )
    removed = await run(
        plugin, Comp.Plain(text="/todo assign #1 -"), Comp.At(qq="eve", name="Eve")
    )

    # Assert
    chain = added[0]
    assert chain[0].text.startswith("已创建：#1 [待办] 写周报 @Bob")
    assert chain[0].text.endswith("\n")
    assert [(c.qq, c.name) for c in chain[1:-1]] == [("bob", "Bob")]
    assert chain[-1].text == ASSIGNED_SUFFIX
    assert self_added == ["已创建：#2 [待办] 自己的 @Alice"]
    assert assigned[0][0].text.startswith("已更新负责人：#1")
    assert [c.qq for c in assigned[0][1:-1]] == ["eve"]
    assert isinstance(removed[0], str)


@pytest.mark.asyncio
async def test_private_add_replies_with_plain_text(plugin):
    # Arrange
    await plugin.initialize()

    # Act
    added = await run(
        plugin,
        Comp.Plain(text="/todo add 交接 "),
        Comp.At(qq="bob", name="Bob"),
        private=True,
    )

    # Assert
    assert added == ["已创建：#1 [待办] 交接 @Bob"]


# ----- M3: WebUI API lifecycle and session titles ---------------------------


def test_registers_every_web_route_under_the_plugin_prefix(plugin):
    # Act
    routes = [(route, methods) for route, _, methods, _ in plugin.context.web_apis]

    # Assert
    assert len(routes) == 15
    assert all(route.startswith("/astrbot_plugin_teamtodo/") for route, _ in routes)
    assert ("/astrbot_plugin_teamtodo/spaces/<space_id>/tasks", ["POST"]) in routes
    assert all(callable(handler) for _, handler, _, _ in plugin.context.web_apis)


@pytest.mark.asyncio
async def test_web_api_fetcher_reads_current_context(plugin, monkeypatch):
    from astrbot_plugin_teamtodo.services import group_members

    from astrbot.api.web import PluginRequest, bind_request_context

    await plugin.initialize()
    await run(plugin, Comp.Plain(text="/todo add 写周报"), group=owner_group())
    calls = []
    new_user = UserRef(platform_id="qq", user_id="new-member")

    async def fetch(context, session):
        calls.append((context, session))
        return [
            GroupMember(user=new_user, nickname="New Member", names=("New Member",))
        ]

    def sync(username, space_id, session, members):
        with plugin._store.transaction():
            for member in members:
                plugin._store.upsert_member(space_id, member.user, member.nickname)
        return len(members)

    monkeypatch.setattr(group_members, "from_session", fetch)
    monkeypatch.setattr(plugin._admin_service, "sync_members", sync)
    handler = {r: h for r, h, m, _ in plugin.context.web_apis if m == ["POST"]}[
        "/astrbot_plugin_teamtodo/spaces/<space_id>/members/sync"
    ]
    current_context = FakeContext()
    plugin.context = current_context

    async def json_body():
        return {}

    raw = SimpleNamespace(
        method="POST",
        url=SimpleNamespace(path="/x"),
        headers={},
        cookies={},
        client=None,
        json=json_body,
        query_params=SimpleNamespace(multi_items=lambda: []),
    )
    with bind_request_context(PluginRequest(raw, username="root")):
        response = await handler(space_id="1")

    assert response.status_code == 200
    assert calls == [(current_context, GROUP)]
    reply = json.loads(response.body)
    assert reply["added"] == 1
    assert any(
        member["user_id"] == new_user.user_id and member["nickname"] == "New Member"
        for member in reply["detail"]["members"]
    )


@pytest.mark.asyncio
async def test_admin_service_follows_the_plugin_lifecycle(plugin):
    # Arrange
    from astrbot.api.web import PluginRequest, bind_request_context

    handler = {r: h for r, h, m, _ in plugin.context.web_apis if m == ["GET"]}[
        "/astrbot_plugin_teamtodo/spaces"
    ]
    raw = SimpleNamespace(
        method="GET",
        url=SimpleNamespace(path="/x"),
        headers={},
        cookies={},
        client=None,
        query_params=SimpleNamespace(multi_items=lambda: []),
    )

    # Act
    await plugin.initialize()
    await run(plugin, Comp.Plain(text="/todo add 写周报"), group=owner_group())
    admin = plugin._admin_service
    with bind_request_context(PluginRequest(raw, username="root")):
        live = await handler()
    await plugin.terminate()
    with bind_request_context(PluginRequest(raw, username="root")):
        stale = await handler()

    # Assert
    assert isinstance(admin, main.AdminService)
    assert live.status_code == 200
    assert [x["name"] for x in json.loads(live.body)["spaces"]] == ["DevTeam"]
    assert plugin._admin_service is None
    assert stale.status_code == 503


@pytest.mark.asyncio
async def test_commands_record_the_session_title(plugin, titles):
    # Arrange
    await plugin.initialize()

    # Act
    await run(plugin, Comp.Plain(text="/todo"), group=owner_group())
    await run(plugin, Comp.Plain(text="/todo help"), private=True, name=" Alice ")
    await run(plugin, Comp.Plain(text="/todo"), private=True, name="  ")
    await run(plugin, Comp.Plain(text="/todo"))

    # Assert: the last group call reuses the group name cached by the resolver.
    assert titles == [
        (GROUP, "Dev Team"),
        ("qq:FriendMessage:u1", "Alice"),
        (GROUP, "Dev Team"),
    ]


@pytest.mark.asyncio
async def test_session_title_failure_does_not_break_the_command(
    plugin, titles, monkeypatch
):
    # Arrange
    await plugin.initialize()
    titles.error = RuntimeError("db is gone")
    errors = []
    monkeypatch.setattr(main.logger, "exception", lambda *a, **k: errors.append(a))

    # Act
    replies = await run(plugin, Comp.Plain(text="/todo"), private=True)

    # Assert
    assert replies == [main.help_text()]
    assert len(errors) == 1
