from datetime import timedelta
from types import SimpleNamespace

import pytest
from astrbot_plugin_teamtodo import main
from astrbot_plugin_teamtodo.core.models import Mention, TodoError, UserRef
from astrbot_plugin_teamtodo.core.permissions import DENIAL_MESSAGES, Op
from astrbot_plugin_teamtodo.services.access import SessionInfo

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
    ):
        self.components = list(components)
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


def owner_group():
    return SimpleNamespace(
        group_owner="u1", group_admins=["mod"], group_name="Dev Team"
    )


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setattr(main.StarTools, "get_data_dir", lambda name: tmp_path)
    instance = main.TeamTodoPlugin(None, {"timezone": "Asia/Shanghai"})
    yield instance
    if instance._store is not None:
        instance._store.close()


async def run(plugin, *components, **kwargs):
    event = FakeEvent(*components, **kwargs)
    return [reply async for reply in plugin.todo(event)]


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
    assert added[0].startswith("已创建：#1 [待办] 写周报 @Bob")
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
    assert added == ["已创建：#1 [待办] 交接 @carol"]
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
    admin = await run(
        plugin, Comp.Plain(text="/todo admin +"), Comp.At(qq="mod"), **owner
    )
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
    assert admin == ["mod 还不是这个空间的成员"]
    assert claim == [DENIAL_MESSAGES[Op.CLAIM]]
    assert left == ["本会话已解绑空间「Ops」，空间和任务仍然保留"]
    assert "60 秒内" in confirm[0]
    assert deleted == ["已删除空间「Core」及其全部任务"]


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
