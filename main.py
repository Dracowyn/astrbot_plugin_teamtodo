"""AstrBot entry point: the ``/todo`` command and the plugin lifecycle.

All command handling lives here (AstrBot only discovers handlers in the
plugin's ``main`` module). The business logic is in ``services/``.

``/todo`` is registered as ONE command that dispatches on its first word
instead of a ``command_group``: AstrBot answers a bare group command with its
own "missing arguments" tree, while the design requires bare ``/todo`` to show
the plugin's help text.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

from .core.models import (
    Actor,
    Mention,
    SpaceRole,
    Task,
    TaskStatus,
    TodoError,
    UserRef,
)
from .core.parsing import (
    Part,
    parse_assign,
    parse_task_fields,
    split_ref,
    strip_command,
    text_of,
)
from .core.store import Store
from .services.access import (
    ConfirmationCodes,
    JoinRateLimiter,
    SessionAdminResolver,
    SessionInfo,
)
from .services.render import (
    format_due,
    help_text,
    mine_list,
    spaces_list,
    task_detail,
    task_line,
    task_list,
)
from .services.todo_service import TodoService, auto_space_name

PLUGIN_NAME = "astrbot_plugin_teamtodo"
DEFAULT_TIMEZONE = "Asia/Shanghai"
GENERIC_FAILURE = "操作失败，请稍后再试"
STATUS_COMMANDS = {
    "start": TaskStatus.DOING,
    "done": TaskStatus.DONE,
    "reopen": TaskStatus.TODO,
    "archive": TaskStatus.ARCHIVED,
}


def load_timezone(name: str) -> tzinfo:
    """Return the configured time zone, falling back to Asia/Shanghai.

    Args:
        name: IANA zone name from the plugin config.

    Returns:
        The zone; ``Asia/Shanghai`` (or a fixed UTC+8 offset if the zone
        database is missing) when ``name`` is invalid.
    """
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "[teamtodo] Invalid timezone %r; using %s", name, DEFAULT_TIMEZONE
        )
    try:
        return ZoneInfo(DEFAULT_TIMEZONE)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8), DEFAULT_TIMEZONE)


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


def split_word(parts: list[Part]) -> tuple[str, list[Part]]:
    """Split the first whitespace-delimited word off the leading text.

    Args:
        parts: Parser parts.

    Returns:
        The word (``""`` if the parts do not start with text) and the rest.
    """
    if not parts or not isinstance(parts[0], str):
        return "", parts
    match = re.match(r"\s*(\S+)", parts[0])
    if match is None:
        return split_word(parts[1:])
    rest = parts[0][match.end() :].lstrip()
    return match[1], ([rest] if rest else []) + parts[1:]


class TeamTodoPlugin(Star):
    """Collaborative TODO with spaces shared across chats."""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context, config)
        self.config = config
        self._tz = load_timezone(str(config.get("timezone") or DEFAULT_TIMEZONE))
        self._store: Store | None = None
        self._service: TodoService | None = None
        self._resolver = SessionAdminResolver()
        self._joins = JoinRateLimiter()
        self._confirms = ConfirmationCodes()

    async def initialize(self) -> None:
        """Open the database and build the service."""
        db_path = StarTools.get_data_dir(PLUGIN_NAME) / "teamtodo.db"
        self._store = Store(db_path)
        self._service = TodoService(
            self._store,
            self._tz,
            join_limiter=self._joins,
            confirmations=self._confirms,
        )

    async def terminate(self) -> None:
        """Close the database."""
        self._service = None
        if self._store is not None:
            self._store.close()
            self._store = None

    @filter.command("todo")
    async def todo(self, event: AstrMessageEvent):
        """协作 TODO，发送 /todo help 查看用法"""
        if self._service is None:
            yield event.plain_result("协作 TODO 插件正在启动，请稍后再试")
            return
        try:
            info = await self._resolver.resolve(event)
            actor = build_actor(event, info)
            auto_name = auto_space_name(
                actor.is_private, info.group_name, str(event.get_group_id() or "")
            )
            args = strip_command(message_parts(event), ("todo",))
            reply = self._dispatch(actor, auto_name, args)
        except TodoError as exc:
            reply = str(exc)
        except Exception:
            logger.exception("[teamtodo] /todo command failed")
            reply = GENERIC_FAILURE
        yield event.plain_result(reply)

    def _dispatch(self, actor: Actor, auto_name: str, args: list[Part]) -> str:
        """Route ``/todo <sub> ...`` to its handler and return the reply.

        Args:
            actor: The requesting user.
            auto_name: Base name for an auto-created space.
            args: Parts after ``todo``.

        Returns:
            Reply text.

        Raises:
            TodoError: User-facing failure.
        """
        sub, rest = split_word(args)
        sub = sub.casefold()
        if sub in ("", "help"):
            return help_text()
        if sub in STATUS_COMMANDS:
            ref, _ = split_ref(rest)
            result = self._service.set_status(
                actor, auto_name, ref, STATUS_COMMANDS[sub]
            )
            return f"已更新：{self._line(result.task, result.nicknames)}"
        if sub == "space":
            sub, rest = split_word(rest)
            sub = f"space {sub.casefold()}"
        handler: Callable[[Actor, str, list[Part]], str] | None = {
            "add": self._add,
            "list": self._list,
            "mine": self._mine,
            "show": self._show,
            "assign": self._assign,
            "edit": self._edit,
            "desc": self._desc,
            "delete": self._delete,
            "spaces": self._spaces,
            "use": self._use,
            "default": self._default,
            "space new": self._space_new,
            "space rename": self._space_rename,
            "space delete": self._space_delete,
            "invite": self._invite,
            "join": self._join,
            "leave": self._leave,
            "claim": self._claim,
            "admin": self._admin,
        }.get(sub)
        if handler is None:
            return "未知命令。\n" + help_text()
        return handler(actor, auto_name, rest)

    def _line(self, task: Task, nicknames: dict[str, str]) -> str:
        return task_line(task, nicknames, self._service.now(), self._service.tz)

    @staticmethod
    def _name_arg(args: list[Part], usage: str) -> list[str]:
        words = text_of(args).split()
        if not words:
            raise TodoError(f"用法：{usage}")
        return words

    # ----- task commands -----

    def _add(self, actor: Actor, auto_name: str, args: list[Part]) -> str:
        fields = parse_task_fields(args, self._service.local_now())
        result = self._service.add_task(actor, auto_name, fields)
        return f"已创建：{self._line(result.task, result.nicknames)}"

    def _list(self, actor: Actor, auto_name: str, args: list[Part]) -> str:
        words = text_of(args).split()
        names = [word for word in words if word.casefold() != "all"]
        if len(names) > 1:
            raise TodoError("用法：/todo list [空间名] [all]")
        result = self._service.list_tasks(
            actor, auto_name, names[0] if names else None, len(names) < len(words)
        )
        return task_list(
            result.space.name,
            result.tasks,
            result.nicknames,
            self._service.now(),
            self._service.tz,
        )

    def _mine(self, actor: Actor, auto_name: str, args: list[Part]) -> str:
        items, nicknames = self._service.mine(actor)
        return mine_list(items, nicknames, self._service.now(), self._service.tz)

    def _show(self, actor: Actor, auto_name: str, args: list[Part]) -> str:
        ref, _ = split_ref(args)
        result = self._service.show_task(actor, auto_name, ref)
        return task_detail(
            result.task,
            result.space.name,
            result.nicknames,
            self._service.now(),
            self._service.tz,
        )

    def _assign(self, actor: Actor, auto_name: str, args: list[Part]) -> str:
        ref, rest = split_ref(args)
        result = self._service.assign(actor, auto_name, ref, parse_assign(rest))
        return f"已更新负责人：{self._line(result.task, result.nicknames)}"

    def _edit(self, actor: Actor, auto_name: str, args: list[Part]) -> str:
        ref, rest = split_ref(args)
        fields = parse_task_fields(rest, self._service.local_now())
        result = self._service.edit_task(actor, auto_name, ref, fields)
        return f"已更新：{self._line(result.task, result.nicknames)}"

    def _desc(self, actor: Actor, auto_name: str, args: list[Part]) -> str:
        ref, rest = split_ref(args)
        text = "".join(
            part if isinstance(part, str) else f"@{part.name or part.user.user_id}"
            for part in rest
        )
        result = self._service.set_description(actor, auto_name, ref, text)
        state = "已更新" if result.task.description else "已清空"
        return f"{state}任务 #{result.task.seq} 的描述"

    def _delete(self, actor: Actor, auto_name: str, args: list[Part]) -> str:
        ref, _ = split_ref(args)
        result = self._service.delete_task(actor, auto_name, ref)
        return f"已删除任务 #{result.task.seq}：{result.task.title}"

    # ----- space and session commands -----

    def _spaces(self, actor: Actor, auto_name: str, args: list[Part]) -> str:
        return spaces_list(self._service.list_spaces(actor, auto_name))

    def _use(self, actor: Actor, auto_name: str, args: list[Part]) -> str:
        name = self._name_arg(args, "/todo use <空间名>")[0]
        space = self._service.use_space(actor, name)
        return f"已切换到空间「{space.name}」"

    def _default(self, actor: Actor, auto_name: str, args: list[Part]) -> str:
        name = self._name_arg(args, "/todo default <空间名>")[0]
        space = self._service.set_default(actor, name)
        return f"本会话的默认空间已改为「{space.name}」"

    def _space_new(self, actor: Actor, auto_name: str, args: list[Part]) -> str:
        name = self._name_arg(args, "/todo space new <名字>")[0]
        space = self._service.new_space(actor, name)
        return f"已创建空间「{space.name}」，并切换为你的当前空间"

    def _space_rename(self, actor: Actor, auto_name: str, args: list[Part]) -> str:
        name = self._name_arg(args, "/todo space rename <新名字>")[0]
        space = self._service.rename_space(actor, auto_name, name)
        return f"当前空间已改名为「{space.name}」"

    def _space_delete(self, actor: Actor, auto_name: str, args: list[Part]) -> str:
        words = self._name_arg(args, "/todo space delete <空间名> [确认码]")
        code = self._service.delete_space(
            actor, words[0], words[1] if len(words) > 1 else None
        )
        if code is None:
            return f"已删除空间「{words[0]}」及其全部任务"
        return (
            f"即将删除空间「{words[0]}」及其全部任务、日志和绑定，且无法恢复。\n"
            f"确认请在 60 秒内发送：/todo space delete {words[0]} {code}"
        )

    def _invite(self, actor: Actor, auto_name: str, args: list[Part]) -> str:
        space, code, invite = self._service.create_invite(actor, auto_name)
        expires = format_due(invite.expires_at, self._service.now(), self._service.tz)
        return (
            f"空间「{space.name}」的邀请码：{code}\n"
            f"有效期至 {expires}，期间可多次使用；旧邀请码已失效。\n"
            f"在其他会话发送 /todo join {code} 即可绑定这个空间。"
        )

    def _join(self, actor: Actor, auto_name: str, args: list[Part]) -> str:
        code = self._name_arg(args, "/todo join <邀请码>")[0]
        space = self._service.join_space(actor, code)
        return f"本会话已绑定空间「{space.name}」，可用 /todo use {space.name} 切换"

    def _leave(self, actor: Actor, auto_name: str, args: list[Part]) -> str:
        name = self._name_arg(args, "/todo leave <空间名>")[0]
        space = self._service.leave_space(actor, name)
        return f"本会话已解绑空间「{space.name}」，空间和任务仍然保留"

    def _claim(self, actor: Actor, auto_name: str, args: list[Part]) -> str:
        space = self._service.claim(actor, auto_name)
        return f"你已成为空间「{space.name}」的所有者"

    def _admin(self, actor: Actor, auto_name: str, args: list[Part]) -> str:
        space, members = self._service.set_admins(actor, auto_name, parse_assign(args))
        lines = [
            f"{m.nickname}：{'管理员' if m.role == SpaceRole.ADMIN else '成员'}"
            for m in members
        ]
        return f"空间「{space.name}」角色已更新：\n" + "\n".join(lines)
