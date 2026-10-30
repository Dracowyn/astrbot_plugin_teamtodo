"""WebUI management API (design 7, "M3 定稿"): routes, validation, serialization.

Handlers are registered by ``main.py`` through ``context.register_web_api``;
they are not ``@filter`` handlers, so they may live here. AstrBot matches them
in ``astrbot/dashboard/api/plugins.py`` (``_match_registered_web_api``), which
passes every ``<name>`` path segment to the handler as a keyword argument, and
only after ``require_plugin_scope`` accepted a dashboard JWT or an API key with
the ``plugin`` scope. Every caller is therefore an AstrBot administrator; the
caller's name comes from that authentication (``request.username``) and never
from the request body.

Everything else in a request is untrusted: path ids must be positive decimal
integers, JSON bodies must be objects whose fields have exactly the contract
types (``bool`` is not a number), enums and list lengths are checked, and
unknown keys are ignored. Responses are built from explicit field whitelists:
no row ids, creating sessions, reminder flags or invite codes/hashes (the
plain invite code is returned once, by ``invite/reset``).

Status codes: 400 invalid input or a ``TodoError``, 404 ``NotFound``, 503 the
plugin is not initialized (or was terminated / uninstalled: AstrBot never
unregisters web routes, so stale handlers must fail cleanly), 500 anything
else with a fixed message (details only in the log).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import tzinfo
from typing import Any

from astrbot.api import logger
from astrbot.api.web import error_response, json_response, request

from ..core.models import (
    MAX_ASSIGNEES,
    MAX_DESCRIPTION_CHARS,
    MAX_SPACE_NAME_CHARS,
    MAX_TAG_CHARS,
    MAX_TAGS,
    MAX_TITLE_CHARS,
    Priority,
    SpaceRole,
    Task,
    TaskEvent,
    TaskStatus,
    TodoError,
    UserRef,
)
from .admin_service import (
    MAX_EVENTS,
    AdminService,
    LogEntry,
    NotFound,
    SpaceDetail,
    SpaceSummary,
    TaskPatch,
)
from .event_context import PLUGIN_NAME
from .render import event_actor_name, event_summary

MAX_ID = 2_000_000_000
"""Largest accepted space id or task number in a path."""
MAX_ID_DIGITS = 10
DEFAULT_EVENTS = 50
MAX_DUE_AT = 4_102_444_800
"""2100-01-01 UTC. Later (or negative) times would overflow ``datetime`` on
some platforms when rendered, so they are rejected."""
MAX_REF_CHARS = 128
"""Longest ``platform_id`` / ``user_id`` accepted in a ``Ref``."""
MAX_SESSION_CHARS = 512
"""Longest ``unified_msg_origin`` accepted by ``unbind``."""
MAX_USERNAME_CHARS = 64
"""Dashboard usernames are stored as log labels and creator ids; capped."""
UNKNOWN_USER = "unknown"
EDITABLE_ROLES = {"admin": SpaceRole.ADMIN, "member": SpaceRole.MEMBER}
BOOL_QUERY = {"1": True, "true": True, "0": False, "false": False}

NOT_READY = "协作 TODO 插件尚未就绪，请稍后再试"
INTERNAL_ERROR = "服务器内部错误，详情见 AstrBot 日志"
PATH_LABELS = {"space_id": "空间编号", "seq": "任务编号"}

WebHandler = Callable[..., Awaitable[Any]]
Route = tuple[str, WebHandler, list[str], str]


class InvalidInput(Exception):
    """Malformed request input; ``str(exc)`` is a Chinese message (HTTP 400)."""


def _path_id(raw: object, label: str) -> int:
    """Parse a path id: ASCII decimal, 1..``MAX_ID``, no sign or spaces.

    Raises:
        InvalidInput: Anything else.
    """
    text = raw if isinstance(raw, str) else ""
    if (
        not text.isascii()
        or not text.isdecimal()
        or len(text) > MAX_ID_DIGITS
        or not 0 < int(text) <= MAX_ID
    ):
        raise InvalidInput(f"{label}必须是正整数")
    return int(text)


def _ref(value: object) -> UserRef:
    """Parse ``{platform_id, user_id}`` (non-blank strings; extra keys ignored).

    Raises:
        InvalidInput: Not an object, or a field is missing, blank, too long or
            not a string.
    """
    if isinstance(value, dict):
        platform_id, user_id = value.get("platform_id"), value.get("user_id")
        if all(
            isinstance(item, str) and item.strip() and len(item) <= MAX_REF_CHARS
            for item in (platform_id, user_id)
        ):
            return UserRef(platform_id=platform_id, user_id=user_id)
    raise InvalidInput("成员必须是 {platform_id, user_id} 形式，且两项都是非空字符串")


def _task_input(body: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    """Validate task fields of a create or update body.

    Absent keys are left out. On update, ``null`` for ``due_at`` / ``priority``
    becomes ``clear_due`` / ``clear_priority``; on create it means "none".
    ``status`` is only read on update. Lengths of texts are checked by the
    service (shared with the chat entry points); list sizes are checked here.

    Args:
        body: The JSON object.
        creating: Create (``title`` required) rather than update.

    Returns:
        Keyword arguments named like ``TaskPatch`` fields.

    Raises:
        InvalidInput: A field has the wrong type or value.
    """
    fields: dict[str, Any] = {}
    if creating or "title" in body:
        title = body.get("title")
        if not isinstance(title, str) or not title.strip():
            raise InvalidInput("任务标题不能为空")
        fields["title"] = title.strip()
    if "description" in body:
        if not isinstance(body["description"], str):
            raise InvalidInput("描述必须是字符串")
        fields["description"] = body["description"]
    if not creating and "status" in body:
        status = body["status"]
        if not isinstance(status, str) or status not in TaskStatus._value2member_map_:
            raise InvalidInput("状态只能是 todo、doing、done 或 archived")
        fields["status"] = TaskStatus(status)
    if "assignees" in body:
        raw = body["assignees"]
        if not isinstance(raw, list) or len(raw) > MAX_ASSIGNEES:
            raise InvalidInput(f"负责人必须是列表，且不能超过 {MAX_ASSIGNEES} 人")
        fields["assignees"] = tuple(dict.fromkeys(_ref(item) for item in raw))
    if "tags" in body:
        raw = body["tags"]
        if (
            not isinstance(raw, list)
            or len(raw) > MAX_TAGS
            or not all(isinstance(tag, str) for tag in raw)
        ):
            raise InvalidInput(f"标签必须是字符串列表，且不能超过 {MAX_TAGS} 个")
        tags = tuple(dict.fromkeys(tag.strip() for tag in raw))
        if "" in tags:
            raise InvalidInput("标签不能为空")
        fields["tags"] = tags
    if "due_at" in body:
        due = body["due_at"]
        if due is None:
            if not creating:
                fields["clear_due"] = True
        # The range test also rejects NaN and infinities; ``math.isfinite``
        # would raise OverflowError on a huge JSON integer.
        elif (
            isinstance(due, bool)
            or not isinstance(due, int | float)
            or not 0 < due <= MAX_DUE_AT
        ):
            raise InvalidInput("截止时间必须是 Unix 秒数或 null")
        else:
            fields["due_at"] = float(due)
    if "priority" in body:
        priority = body["priority"]
        if priority is None:
            if not creating:
                fields["clear_priority"] = True
        elif (
            not isinstance(priority, str) or priority not in Priority._value2member_map_
        ):
            raise InvalidInput("优先级只能是 high、medium、low 或 null")
        else:
            fields["priority"] = Priority(priority)
    return fields


def _username() -> str:
    """The authenticated dashboard user as one printable, length-capped line."""
    raw = request.username if isinstance(request.username, str) else ""
    text = "".join(ch if ch.isprintable() else " " for ch in raw)
    return " ".join(text.split())[:MAX_USERNAME_CHARS] or UNKNOWN_USER


def _user(user: UserRef, nicknames: Mapping[str, str]) -> dict[str, Any]:
    """``User``: the ref plus a nickname (``user_id`` when unknown)."""
    return {
        "platform_id": user.platform_id,
        "user_id": user.user_id,
        "nickname": nicknames.get(user.key) or user.user_id,
    }


def _task(task: Task, nicknames: Mapping[str, str], now: float) -> dict[str, Any]:
    """``Task`` without ``id``, ``created_session`` and ``reminded``."""
    return {
        "space_id": task.space_id,
        "seq": task.seq,
        "title": task.title,
        "status": task.status.value,
        "assignees": [_user(user, nicknames) for user in task.assignees],
        "due_at": task.due_at,
        "tags": list(task.tags),
        "priority": task.priority.value if task.priority is not None else None,
        "description": task.description,
        "creator": _user(task.creator, nicknames),
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "done_at": task.done_at,
        "overdue": task.due_at is not None
        and task.due_at < now
        and task.status in (TaskStatus.TODO, TaskStatus.DOING),
    }


def _summary(
    summary: SpaceSummary, nicknames: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """``SpaceSummary``; the owner's nickname comes from ``nicknames`` when
    given (the space list has none, so it falls back to the user id)."""
    space = summary.space
    return {
        "id": space.id,
        "name": space.name,
        "owner": _user(space.owner, nicknames or {}) if space.owner else None,
        "created_at": space.created_at,
        "binding_count": summary.binding_count,
        "member_count": summary.member_count,
        "task_counts": {
            status.value: summary.task_counts.get(status, 0) for status in TaskStatus
        },
        "overdue_count": summary.overdue_count,
    }


def _detail(detail: SpaceDetail) -> dict[str, Any]:
    """``SpaceDetail``: only the invite's expiry, never its code or hash."""
    nicknames = {m.user.key: m.nickname for m in detail.members if m.nickname}
    return {
        **_summary(detail.summary, nicknames),
        "bindings": [
            {"session": binding.session, "title": title, "bound_at": binding.bound_at}
            for binding, title in detail.bindings
        ],
        "members": [
            {**_user(m.user, nicknames), "role": m.role.value, "joined_at": m.joined_at}
            for m in detail.members
        ],
        "invite": (
            {"expires_at": detail.invite.expires_at}
            if detail.invite is not None
            else None
        ),
    }


class WebApi:
    """The route handlers of the WebUI management page."""

    def __init__(
        self,
        get_service: Callable[[], AdminService | None],
        tz: tzinfo,
        *,
        remind_before_minutes: int,
        auto_archive_days: int,
    ) -> None:
        """Create the handlers.

        Args:
            get_service: Returns the plugin's ``AdminService``, or ``None``
                before ``initialize()`` and after ``terminate()``. Read on
                every request, never captured.
            tz: Configured time zone.
            remind_before_minutes: Reminder lead time from the config.
            auto_archive_days: Auto-archive delay from the config.
        """
        self._get_service = get_service
        self._tz = tz
        self._remind_minutes = remind_before_minutes
        self._archive_days = auto_archive_days

    def routes(self) -> list[Route]:
        """Every route of design 7, prefixed with ``/<plugin name>/``.

        Returns:
            ``(route, handler, methods, description)`` for
            ``context.register_web_api``. Each route has exactly one method,
            so re-registration on reload replaces the previous handler.
        """
        table = (
            ("meta", "GET", self._meta, "Settings and input limits"),
            ("spaces", "GET", self._list_spaces, "List all spaces"),
            ("spaces/<space_id>", "GET", self._get_space, "Space detail"),
            ("spaces/<space_id>/rename", "POST", self._rename, "Rename a space"),
            ("spaces/<space_id>/delete", "POST", self._delete_space, "Delete space"),
            ("spaces/<space_id>/unbind", "POST", self._unbind, "Unbind a session"),
            ("spaces/<space_id>/members/role", "POST", self._set_role, "Set role"),
            (
                "spaces/<space_id>/invite/reset",
                "POST",
                self._reset_invite,
                "Reset invite",
            ),
            ("spaces/<space_id>/tasks", "GET", self._list_tasks, "List tasks"),
            ("spaces/<space_id>/tasks", "POST", self._create_task, "Create a task"),
            ("spaces/<space_id>/tasks/<seq>", "GET", self._get_task, "Task and log"),
            ("spaces/<space_id>/tasks/<seq>", "POST", self._update_task, "Edit task"),
            (
                "spaces/<space_id>/tasks/<seq>/delete",
                "POST",
                self._delete_task,
                "Delete a task",
            ),
            ("spaces/<space_id>/events", "GET", self._list_events, "Space log"),
        )
        return [
            (f"/{PLUGIN_NAME}/{path}", self._endpoint(path, method, op), [method], desc)
            for path, method, op, desc in table
        ]

    def _endpoint(self, path: str, method: str, op: Callable[..., Any]) -> WebHandler:
        """Wrap one operation with input checks and the error contract.

        Path ids are validated, a POST body must be a JSON object, and the
        service is read after the body await so a concurrent ``terminate()``
        cannot hand the operation a closed store.

        Args:
            path: Route template, for the log.
            method: ``GET`` or ``POST``.
            op: ``op(service, body, **ids)`` returning JSON-ready data.

        Returns:
            The async handler AstrBot calls with the path segments.
        """

        async def handler(**segments: str) -> Any:
            try:
                ids = {
                    name: _path_id(value, PATH_LABELS.get(name, name))
                    for name, value in segments.items()
                }
                body: Any = {}
                if method == "POST":
                    body = await request.json(default=None)
                    if not isinstance(body, dict):
                        raise InvalidInput("请求体必须是 JSON 对象")
                service = self._get_service()
                if service is None:
                    return error_response(NOT_READY, status_code=503)
                return json_response(op(service, body, **ids))
            except InvalidInput as exc:
                return error_response(str(exc), status_code=400)
            except NotFound as exc:
                return error_response(str(exc), status_code=404)
            except TodoError as exc:
                return error_response(str(exc), status_code=400)
            except Exception:
                logger.exception("[teamtodo] Web API %s %s failed", method, path)
                return error_response(INTERNAL_ERROR, status_code=500)

        return handler

    def _events(
        self, entries: list[LogEntry], nicknames: Mapping[str, str]
    ) -> list[dict[str, Any]]:
        """``Event[]``; ``detail`` is rendered server-side, never returned."""
        return [
            {
                "kind": "task" if isinstance(entry, TaskEvent) else "space",
                "task_seq": entry.task_seq if isinstance(entry, TaskEvent) else None,
                "action": entry.action.value,
                "summary": event_summary(entry, nicknames, self._tz),
                "actor_name": event_actor_name(entry, nicknames),
                "created_at": entry.created_at,
            }
            for entry in entries
        ]

    # ----- operations: op(service, body, **ids) -> JSON-ready data -----

    def _meta(self, service: AdminService, body: dict) -> dict[str, Any]:
        return {
            "timezone": str(self._tz),
            "server_time": service.now(),
            "auto_archive_days": self._archive_days,
            "remind_before_minutes": self._remind_minutes,
            "limits": {
                "title": MAX_TITLE_CHARS,
                "description": MAX_DESCRIPTION_CHARS,
                "tag": MAX_TAG_CHARS,
                "tags": MAX_TAGS,
                "assignees": MAX_ASSIGNEES,
                "space_name": MAX_SPACE_NAME_CHARS,
            },
        }

    def _list_spaces(self, service: AdminService, body: dict) -> dict[str, Any]:
        return {"spaces": [_summary(summary) for summary in service.list_spaces()]}

    def _get_space(
        self, service: AdminService, body: dict, space_id: int
    ) -> dict[str, Any]:
        return _detail(service.get_space(space_id))

    def _rename(
        self, service: AdminService, body: dict, space_id: int
    ) -> dict[str, Any]:
        name = body.get("name")
        if not isinstance(name, str):
            raise InvalidInput("空间名必须是字符串")
        service.rename_space(_username(), space_id, name)
        return _detail(service.get_space(space_id))

    def _delete_space(
        self, service: AdminService, body: dict, space_id: int
    ) -> dict[str, Any]:
        service.delete_space(_username(), space_id)
        return {"ok": True}

    def _unbind(
        self, service: AdminService, body: dict, space_id: int
    ) -> dict[str, Any]:
        session = body.get("session")
        if (
            not isinstance(session, str)
            or not session.strip()
            or len(session) > MAX_SESSION_CHARS
        ):
            raise InvalidInput(f"会话必须是不超过 {MAX_SESSION_CHARS} 字的非空字符串")
        service.unbind(_username(), space_id, session)
        return _detail(service.get_space(space_id))

    def _set_role(
        self, service: AdminService, body: dict, space_id: int
    ) -> dict[str, Any]:
        user = _ref(body)
        role = body.get("role")
        if not isinstance(role, str) or role not in EDITABLE_ROLES:
            raise InvalidInput("角色只能是 admin 或 member")
        service.set_role(_username(), space_id, user, EDITABLE_ROLES[role])
        return _detail(service.get_space(space_id))

    def _reset_invite(
        self, service: AdminService, body: dict, space_id: int
    ) -> dict[str, Any]:
        code, invite = service.reset_invite(_username(), space_id)
        return {"code": code, "expires_at": invite.expires_at}

    def _list_tasks(
        self, service: AdminService, body: dict, space_id: int
    ) -> dict[str, Any]:
        raw = request.query.get("include_archived")
        include = BOOL_QUERY.get(raw) if raw is not None else False
        if include is None:
            raise InvalidInput("include_archived 只能是 1、0、true 或 false")
        result = service.list_tasks(space_id, include)
        now = service.now()
        return {
            "tasks": [_task(task, result.nicknames, now) for task in result.tasks],
            "server_time": now,
        }

    def _create_task(
        self, service: AdminService, body: dict, space_id: int
    ) -> dict[str, Any]:
        fields = _task_input(body, creating=True)
        result = service.create_task(_username(), space_id, **fields)
        return _task(result.task, result.nicknames, service.now())

    def _get_task(
        self, service: AdminService, body: dict, space_id: int, seq: int
    ) -> dict[str, Any]:
        result = service.get_task(space_id, seq)
        entries, nicknames = service.list_events(space_id, MAX_EVENTS, task_seq=seq)
        return {
            "task": _task(result.task, result.nicknames, service.now()),
            "events": self._events(entries, nicknames),
        }

    def _update_task(
        self, service: AdminService, body: dict, space_id: int, seq: int
    ) -> dict[str, Any]:
        patch = TaskPatch(**_task_input(body, creating=False))
        result = service.update_task(_username(), space_id, seq, patch)
        return _task(result.task, result.nicknames, service.now())

    def _delete_task(
        self, service: AdminService, body: dict, space_id: int, seq: int
    ) -> dict[str, Any]:
        service.delete_task(_username(), space_id, seq)
        return {"ok": True}

    def _list_events(
        self, service: AdminService, body: dict, space_id: int
    ) -> dict[str, Any]:
        raw = (request.query.get("limit") or "").strip()
        if raw and (not raw.isascii() or not raw.isdecimal() or len(raw) > 6):
            raise InvalidInput(f"limit 必须是 1 到 {MAX_EVENTS} 之间的整数")
        limit = min(MAX_EVENTS, max(1, int(raw))) if raw else DEFAULT_EVENTS
        entries, nicknames = service.list_events(space_id, limit)
        return {"events": self._events(entries, nicknames)}
