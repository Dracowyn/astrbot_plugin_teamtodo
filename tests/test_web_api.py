import json
import math
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from astrbot_plugin_teamtodo.core.models import (
    MAX_ASSIGNEES,
    MAX_TAGS,
    MAX_TITLE_CHARS,
    Actor,
    GroupMember,
    Mention,
    Priority,
    SpaceEventAction,
    TaskStatus,
    TodoError,
    UserRef,
)
from astrbot_plugin_teamtodo.core.parsing import TaskFields, TaskRef
from astrbot_plugin_teamtodo.core.store import Store
from astrbot_plugin_teamtodo.services import web_api
from astrbot_plugin_teamtodo.services.admin_service import AdminService, NotFound
from astrbot_plugin_teamtodo.services.share import ShareConflict, ShareService
from astrbot_plugin_teamtodo.services.todo_service import TodoService

from astrbot.api.web import PluginRequest, bind_request_context

TZ = ZoneInfo("Asia/Shanghai")
NOW = 1_800_000_000.0
GROUP = "qq:GroupMessage:1000"
AUTO = "群1000"
PREFIX = "/astrbot_plugin_teamtodo/"
NO_JSON = object()
"""Body sentinel: the request body is not valid JSON."""

ALICE = UserRef(platform_id="qq", user_id="u1")
BOB = UserRef(platform_id="qq", user_id="bob")
USER_KEYS = {"platform_id", "user_id", "nickname"}
TASK_KEYS = {
    "space_id",
    "seq",
    "title",
    "status",
    "assignees",
    "due_at",
    "tags",
    "priority",
    "description",
    "creator",
    "created_at",
    "updated_at",
    "done_at",
    "overdue",
}
SUMMARY_KEYS = {
    "id",
    "name",
    "owner",
    "created_at",
    "binding_count",
    "member_count",
    "task_counts",
    "overdue_count",
}
DETAIL_KEYS = SUMMARY_KEYS | {"bindings", "members", "invite", "share"}
EVENT_KEYS = {"kind", "task_seq", "action", "summary", "actor_name", "created_at"}
ROUTES = {
    ("meta", "GET"),
    ("spaces", "GET"),
    ("spaces/<space_id>", "GET"),
    ("spaces/<space_id>/rename", "POST"),
    ("spaces/<space_id>/delete", "POST"),
    ("spaces/<space_id>/unbind", "POST"),
    ("spaces/<space_id>/members/role", "POST"),
    ("spaces/<space_id>/members/sync", "POST"),
    ("spaces/<space_id>/invite/reset", "POST"),
    ("spaces/<space_id>/share", "POST"),
    ("spaces/<space_id>/tasks", "GET"),
    ("spaces/<space_id>/tasks", "POST"),
    ("spaces/<space_id>/tasks/<seq>", "GET"),
    ("spaces/<space_id>/tasks/<seq>", "POST"),
    ("spaces/<space_id>/tasks/<seq>/delete", "POST"),
    ("spaces/<space_id>/events", "GET"),
}


class FakeRaw:
    """The parts of a Starlette request that ``PluginRequest`` reads."""

    def __init__(self, method, query, body):
        self.method = method
        self.url = SimpleNamespace(path="/api/plug/x")
        self.headers = {}
        self.cookies = {}
        self.client = None
        self.query_params = SimpleNamespace(multi_items=lambda: list(query))
        self._body = body

    async def json(self):
        if self._body is NO_JSON:
            raise ValueError("not json")
        return self._body


@pytest.fixture
def store():
    store = Store(":memory:", clock=lambda: NOW)
    yield store
    store.close()


@pytest.fixture
def todo(store):
    return TodoService(store, TZ, clock=lambda: NOW)


@pytest.fixture
def holder(store):
    return {"service": AdminService(store, TZ, clock=lambda: NOW)}


@pytest.fixture
def fetch_members():
    async def fetch(session):
        return []

    return fetch


@pytest.fixture
def routes(holder, fetch_members):
    api = web_api.WebApi(
        lambda: holder["service"],
        TZ,
        fetch_group_members=fetch_members,
        remind_before_minutes=60,
        auto_archive_days=7,
    )
    return {
        (route.removeprefix(PREFIX), methods[0]): handler
        for route, handler, methods, _ in api.routes()
    }


@pytest.fixture
def seeded(todo):
    """Space 1 (owner Alice, member Bob, bound to GROUP) with overdue task #1."""
    alice = Actor(
        user=ALICE,
        nickname="Alice",
        session=GROUP,
        is_private=False,
        is_bot_admin=False,
        is_session_admin=True,
    )
    fields = TaskFields(
        title="写周报",
        mentions=(Mention(user=BOB, name="Bob"),),
        due_at=NOW - 60,
        clear_due=False,
        priority=Priority.HIGH,
        clear_priority=False,
        add_tags=("doc",),
        remove_tags=(),
    )
    todo.add_task(alice, AUTO, fields)
    todo.note_session_title(GROUP, "Dev Team")
    _, code, _ = todo.create_invite(alice, AUTO)
    return {"actor": alice, "code": code}


async def send(
    routes, method, template, body=None, *, query=(), username="root", **segments
):
    if method == "POST" and body is None:
        body = {}
    raw = FakeRaw(method, query, body)
    with bind_request_context(PluginRequest(raw, username=username)):
        response = await routes[(template, method)](**segments)
    return response.status_code, json.loads(response.body)


# ----- routes and readiness -----------------------------------------------


def test_route_table_matches_the_design(routes, holder, fetch_members):
    # Arrange
    api = web_api.WebApi(
        lambda: None,
        TZ,
        fetch_group_members=fetch_members,
        remind_before_minutes=1,
        auto_archive_days=1,
    )

    # Act
    registered = api.routes()

    # Assert
    assert set(routes) == ROUTES
    assert len(registered) == len(ROUTES)
    assert all(route.startswith(PREFIX) for route, _, _, _ in registered)
    assert all(len(methods) == 1 for _, _, methods, _ in registered)
    assert all(desc for _, _, _, desc in registered)


@pytest.mark.asyncio
@pytest.mark.parametrize(("template", "method"), sorted(ROUTES))
async def test_every_route_answers_503_when_not_ready(routes, holder, template, method):
    # Arrange
    holder["service"] = None
    segments = {name: "1" for name in ("space_id", "seq") if f"<{name}>" in template}

    # Act
    status, body = await send(routes, method, template, **segments)

    # Assert
    assert status == 503
    assert body == {"status": "error", "message": web_api.NOT_READY, "data": None}


@pytest.mark.asyncio
async def test_meta(routes):
    # Act
    status, body = await send(routes, "GET", "meta")

    # Assert
    assert status == 200
    assert body == {
        "timezone": "Asia/Shanghai",
        "server_time": NOW,
        "auto_archive_days": 7,
        "remind_before_minutes": 60,
        "share_available": False,
        "limits": {
            "title": 200,
            "description": 4000,
            "tag": 20,
            "tags": 10,
            "assignees": 20,
            "space_name": 30,
        },
    }


# ----- input validation ------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    ["0", "-1", "+1", "abc", "1.5", " 1", "1 ", "１", "", "2000000001", "1" * 11],
)
async def test_bad_path_ids_are_rejected(routes, seeded, raw):
    # Act
    space = await send(routes, "GET", "spaces/<space_id>", space_id=raw)
    task = await send(
        routes, "GET", "spaces/<space_id>/tasks/<seq>", space_id="1", seq=raw
    )

    # Assert
    assert space == (
        400,
        {"status": "error", "message": "空间编号必须是正整数", "data": None},
    )
    assert task[0] == 400
    assert task[1]["message"] == "任务编号必须是正整数"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [NO_JSON, None, [], "x", 3])
async def test_post_body_must_be_a_json_object(routes, seeded, monkeypatch, body):
    # Arrange
    raw = FakeRaw("POST", (), body)

    # Act
    with bind_request_context(PluginRequest(raw, username="root")):
        response = await routes[("spaces/<space_id>/delete", "POST")](space_id="1")

    # Assert
    assert response.status_code == 400
    assert json.loads(response.body)["message"] == "请求体必须是 JSON 对象"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({}, "任务标题不能为空"),
        ({"title": "  "}, "任务标题不能为空"),
        ({"title": 5}, "任务标题不能为空"),
        ({"title": "t", "description": None}, "描述必须是字符串"),
        ({"title": "t", "assignees": {}}, "负责人必须是列表"),
        ({"title": "t", "assignees": [ALICE.key]}, "成员必须是"),
        ({"title": "t", "assignees": [{"platform_id": "qq"}]}, "成员必须是"),
        ({"title": "t", "assignees": [{"platform_id": "qq", "user_id": 1}]}, "成员"),
        (
            {"title": "t", "assignees": [{"platform_id": "qq", "user_id": " "}]},
            "成员必须是",
        ),
        (
            {"title": "t", "assignees": [{"platform_id": "q" * 129, "user_id": "u"}]},
            "成员必须是",
        ),
        (
            {
                "title": "t",
                "assignees": [
                    {"platform_id": "qq", "user_id": str(i)}
                    for i in range(MAX_ASSIGNEES + 1)
                ],
            },
            "负责人必须是列表",
        ),
        ({"title": "t", "tags": "doc"}, "标签必须是字符串列表"),
        ({"title": "t", "tags": ["a", 1]}, "标签必须是字符串列表"),
        ({"title": "t", "tags": [str(i) for i in range(MAX_TAGS + 1)]}, "标签必须"),
        ({"title": "t", "tags": ["a", " "]}, "标签不能为空"),
        ({"title": "t", "due_at": True}, "截止时间"),
        ({"title": "t", "due_at": "1800000000"}, "截止时间"),
        ({"title": "t", "due_at": math.nan}, "截止时间"),
        ({"title": "t", "due_at": math.inf}, "截止时间"),
        ({"title": "t", "due_at": -1}, "截止时间"),
        ({"title": "t", "due_at": 0}, "截止时间"),
        ({"title": "t", "due_at": web_api.MAX_DUE_AT + 1}, "截止时间"),
        ({"title": "t", "due_at": 10**400}, "截止时间"),
        ({"title": "t", "priority": "urgent"}, "优先级"),
        ({"title": "t", "priority": 1}, "优先级"),
        ({"title": "t", "priority": ["high"]}, "优先级"),
    ],
)
async def test_create_task_rejects_bad_fields(routes, seeded, store, body, message):
    # Act
    status, reply = await send(
        routes, "POST", "spaces/<space_id>/tasks", body, space_id="1"
    )

    # Assert
    assert status == 400
    assert reply["message"].startswith(message)
    assert store.get_task(1, 2) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"title": None},
        {"title": ""},
        {"description": 1},
        {"status": "bogus"},
        {"status": 1},
        {"status": None},
        {"assignees": None},
        {"tags": None},
        {"due_at": False},
        {"priority": "none"},
    ],
)
async def test_update_task_rejects_bad_fields(routes, seeded, store, body):
    # Arrange
    before = store.get_task(1, 1)

    # Act
    status, _ = await send(
        routes, "POST", "spaces/<space_id>/tasks/<seq>", body, space_id="1", seq="1"
    )

    # Assert
    assert status == 400
    assert store.get_task(1, 1) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["yes", "TRUE", " 1", "2", ""])
async def test_include_archived_accepts_only_1_0_true_false(routes, seeded, raw):
    # Act
    status, body = await send(
        routes,
        "GET",
        "spaces/<space_id>/tasks",
        query=[("include_archived", raw)],
        space_id="1",
    )

    # Assert
    assert status == 400
    assert body["message"].startswith("include_archived")


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["abc", "-1", "1.5", "１", "1234567"])
async def test_bad_event_limit_is_rejected(routes, seeded, raw):
    # Act
    status, _ = await send(
        routes, "GET", "spaces/<space_id>/events", query=[("limit", raw)], space_id="1"
    )

    # Assert
    assert status == 400


# ----- spaces -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_space_list_and_detail_are_whitelisted(routes, seeded):
    # Act
    _, listed = await send(routes, "GET", "spaces")
    _, detail = await send(routes, "GET", "spaces/<space_id>", space_id="1")

    # Assert
    summary = listed["spaces"][0]
    assert set(listed) == {"spaces"}
    assert set(summary) == SUMMARY_KEYS
    assert summary["id"] == 1
    assert summary["name"] == AUTO
    assert summary["owner"] == {"platform_id": "qq", "user_id": "u1", "nickname": "u1"}
    assert summary["task_counts"] == {"todo": 1, "doing": 0, "done": 0, "archived": 0}
    assert summary["overdue_count"] == 1
    assert set(detail) == DETAIL_KEYS
    assert detail["owner"]["nickname"] == "Alice"
    assert detail["bindings"] == [
        {"session": GROUP, "title": "Dev Team", "bound_at": NOW}
    ]
    assert [(m["user_id"], m["role"], m["nickname"]) for m in detail["members"]] == [
        ("u1", "owner", "Alice"),
        ("bob", "member", "Bob"),
    ]
    assert all(set(m) == USER_KEYS | {"role", "joined_at"} for m in detail["members"])
    assert detail["invite"] == {"expires_at": pytest.approx(NOW + 7 * 86400)}
    assert detail["share"] is None
    text = json.dumps(detail, ensure_ascii=False)
    assert seeded["code"] not in text
    assert "hash" not in text


@pytest.mark.asyncio
async def test_unknown_space_is_404(routes):
    # Act
    status, body = await send(routes, "GET", "spaces/<space_id>", space_id="9")

    # Assert
    assert status == 404
    assert body["message"] == "空间不存在"


@pytest.mark.asyncio
async def test_rename_space(routes, seeded):
    # Act
    ok = await send(
        routes, "POST", "spaces/<space_id>/rename", {"name": "新名"}, space_id="1"
    )
    wrong_type = await send(
        routes, "POST", "spaces/<space_id>/rename", {"name": 1}, space_id="1"
    )
    bad_name = await send(
        routes, "POST", "spaces/<space_id>/rename", {"name": "a#b"}, space_id="1"
    )
    missing = await send(
        routes, "POST", "spaces/<space_id>/rename", {"name": "x"}, space_id="9"
    )

    # Assert
    assert ok[0] == 200
    assert set(ok[1]) == DETAIL_KEYS
    assert ok[1]["name"] == "新名"
    assert wrong_type == (
        400,
        {"status": "error", "message": "空间名必须是字符串", "data": None},
    )
    assert bad_name[0] == 400
    assert missing[0] == 404


@pytest.mark.asyncio
async def test_delete_space(routes, seeded):
    # Act
    deleted = await send(routes, "POST", "spaces/<space_id>/delete", space_id="1")
    after = await send(routes, "GET", "spaces/<space_id>", space_id="1")

    # Assert
    assert deleted == (200, {"ok": True})
    assert after[0] == 404


@pytest.mark.asyncio
async def test_unbind(routes, seeded):
    # Act
    bad = [
        await send(routes, "POST", "spaces/<space_id>/unbind", body, space_id="1")
        for body in ({}, {"session": 1}, {"session": " "}, {"session": "x" * 513})
    ]
    not_bound = await send(
        routes, "POST", "spaces/<space_id>/unbind", {"session": "qq:x:1"}, space_id="1"
    )
    ok = await send(
        routes, "POST", "spaces/<space_id>/unbind", {"session": GROUP}, space_id="1"
    )

    # Assert
    assert [status for status, _ in bad] == [400] * 4
    assert not_bound[0] == 404
    assert ok[0] == 200
    assert ok[1]["bindings"] == []
    assert ok[1]["binding_count"] == 0


@pytest.mark.asyncio
async def test_set_member_role(routes, seeded):
    # Arrange
    def body(**extra):
        return {"platform_id": "qq", "user_id": "bob", "role": "admin", **extra}

    # Act
    promoted = await send(
        routes, "POST", "spaces/<space_id>/members/role", body(), space_id="1"
    )
    invalid = [
        await send(
            routes, "POST", "spaces/<space_id>/members/role", value, space_id="1"
        )
        for value in (
            body(role="owner"),
            body(role=True),
            body(role=None),
            {"platform_id": "qq", "role": "admin"},
            body(user_id=7),
        )
    ]
    unknown = await send(
        routes,
        "POST",
        "spaces/<space_id>/members/role",
        body(user_id="nobody"),
        space_id="1",
    )
    owner = await send(
        routes,
        "POST",
        "spaces/<space_id>/members/role",
        body(user_id="u1", role="member"),
        space_id="1",
    )

    # Assert
    assert promoted[0] == 200
    assert [(m["user_id"], m["role"]) for m in promoted[1]["members"]] == [
        ("u1", "owner"),
        ("bob", "admin"),
    ]
    assert [status for status, _ in invalid] == [400] * 5
    assert unknown[0] == 404
    assert owner[0] == 400


@pytest.mark.asyncio
async def test_reset_invite_returns_the_code_once(routes, seeded, store):
    # Act
    status, body = await send(
        routes, "POST", "spaces/<space_id>/invite/reset", space_id="1"
    )
    _, detail = await send(routes, "GET", "spaces/<space_id>", space_id="1")

    # Assert
    assert status == 200
    assert set(body) == {"code", "expires_at"}
    assert body["code"] != seeded["code"]
    assert detail["invite"] == {"expires_at": body["expires_at"]}
    assert body["code"] not in json.dumps(detail)


# ----- tasks ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasks_is_whitelisted_with_overdue(routes, seeded, todo):
    # Arrange
    todo.set_status(seeded["actor"], AUTO, TaskRef(None, 1), TaskStatus.ARCHIVED)

    # Act
    hidden = await send(routes, "GET", "spaces/<space_id>/tasks", space_id="1")
    shown = [
        await send(
            routes,
            "GET",
            "spaces/<space_id>/tasks",
            query=[("include_archived", raw)],
            space_id="1",
        )
        for raw in ("1", "true", "0", "false")
    ]

    # Assert
    assert hidden == (200, {"tasks": [], "server_time": NOW})
    assert [len(body["tasks"]) for _, body in shown] == [1, 1, 0, 0]
    task = shown[0][1]["tasks"][0]
    assert set(task) == TASK_KEYS
    assert task["overdue"] is False
    assert task["status"] == "archived"


@pytest.mark.asyncio
async def test_get_task_with_events(routes, seeded):
    # Arrange
    await send(
        routes,
        "POST",
        "spaces/<space_id>/tasks/<seq>",
        {"status": "doing"},
        space_id="1",
        seq="1",
        username="root",
    )

    # Act
    status, body = await send(
        routes, "GET", "spaces/<space_id>/tasks/<seq>", space_id="1", seq="1"
    )
    missing = await send(
        routes, "GET", "spaces/<space_id>/tasks/<seq>", space_id="1", seq="9"
    )

    # Assert
    assert status == 200
    assert set(body) == {"task", "events"}
    task = body["task"]
    assert set(task) == TASK_KEYS
    assert task["assignees"] == [
        {"platform_id": "qq", "user_id": "bob", "nickname": "Bob"}
    ]
    assert task["creator"] == {
        "platform_id": "qq",
        "user_id": "u1",
        "nickname": "Alice",
    }
    assert task["overdue"] is True
    assert task["priority"] == "high"
    assert task["tags"] == ["doc"]
    assert [e["action"] for e in body["events"]] == ["status", "create"]
    assert all(set(e) == EVENT_KEYS for e in body["events"])
    assert body["events"][0]["actor_name"] == "root"
    assert body["events"][0]["kind"] == "task"
    assert body["events"][0]["task_seq"] == 1
    assert "进行中" in body["events"][0]["summary"]
    assert missing[0] == 404


@pytest.mark.asyncio
async def test_create_task(routes, seeded):
    # Act
    status, task = await send(
        routes,
        "POST",
        "spaces/<space_id>/tasks",
        {
            "title": "  发布  ",
            "assignees": [
                {"platform_id": "qq", "user_id": "bob", "nickname": "ignored"},
                {"platform_id": "qq", "user_id": "bob"},
            ],
            "due_at": NOW + 3600,
            "tags": ["a", " a ", "b"],
            "priority": "low",
            "description": "第一行\n第二行",
            "status": "done",
            "creator": {"platform_id": "qq", "user_id": "u1"},
            "username": "mallory",
        },
        username="root",
        space_id="1",
    )

    # Assert
    assert status == 200
    assert set(task) == TASK_KEYS
    assert task["seq"] == 2
    assert task["title"] == "发布"
    assert task["status"] == "todo"
    assert [a["user_id"] for a in task["assignees"]] == ["bob"]
    assert task["due_at"] == NOW + 3600
    assert task["tags"] == ["a", "b"]
    assert task["priority"] == "low"
    assert task["creator"] == {
        "platform_id": "webui",
        "user_id": "root",
        "nickname": "root",
    }
    assert task["overdue"] is False


@pytest.mark.asyncio
async def test_create_task_null_due_and_priority_mean_none(routes, seeded):
    # Act
    status, task = await send(
        routes,
        "POST",
        "spaces/<space_id>/tasks",
        {"title": "t", "due_at": None, "priority": None},
        space_id="1",
    )

    # Assert
    assert status == 200
    assert (task["due_at"], task["priority"]) == (None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"title": "x" * (MAX_TITLE_CHARS + 1)}, 400),
        ({"title": "t", "assignees": [{"platform_id": "qq", "user_id": "eve"}]}, 400),
        ({"title": "t", "tags": ["x" * 21]}, 400),
    ],
)
async def test_create_task_service_rules_are_400(routes, seeded, body, status):
    # Act
    result = await send(routes, "POST", "spaces/<space_id>/tasks", body, space_id="1")

    # Assert
    assert result[0] == status


@pytest.mark.asyncio
async def test_update_null_clears_and_absent_keeps(routes, seeded, store):
    # Arrange
    path = "spaces/<space_id>/tasks/<seq>"

    async def patch(body):
        return await send(routes, "POST", path, body, space_id="1", seq="1")

    # Act
    empty = await patch({})
    retitled = await patch({"title": "新标题", "unknown": 1})
    no_due = await patch({"due_at": None})
    no_priority = await patch({"priority": None})
    reset = await patch(
        {
            "due_at": NOW + 600,
            "priority": "medium",
            "assignees": [],
            "tags": ["x"],
            "description": "d",
            "status": "done",
        }
    )

    # Assert
    assert empty[0] == 200
    assert (empty[1]["due_at"], empty[1]["priority"]) == (NOW - 60, "high")
    assert retitled[1]["title"] == "新标题"
    assert (retitled[1]["due_at"], retitled[1]["priority"]) == (NOW - 60, "high")
    assert (no_due[1]["due_at"], no_due[1]["priority"]) == (None, "high")
    assert (no_priority[1]["due_at"], no_priority[1]["priority"]) == (None, None)
    task = reset[1]
    assert (task["due_at"], task["priority"]) == (NOW + 600, "medium")
    assert (task["assignees"], task["tags"], task["description"]) == ([], ["x"], "d")
    assert (task["status"], task["done_at"]) == ("done", NOW)
    assert store.get_task(1, 1).reminded is False


@pytest.mark.asyncio
async def test_delete_task(routes, seeded):
    # Act
    deleted = await send(
        routes,
        "POST",
        "spaces/<space_id>/tasks/<seq>/delete",
        space_id="1",
        seq="1",
    )
    again = await send(
        routes,
        "POST",
        "spaces/<space_id>/tasks/<seq>/delete",
        space_id="1",
        seq="1",
    )

    # Assert
    assert deleted == (200, {"ok": True})
    assert again[0] == 404


# ----- log --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_events_merge_and_limit(routes, seeded):
    # Arrange
    await send(routes, "POST", "spaces/<space_id>/delete", space_id="9")
    await send(
        routes,
        "POST",
        "spaces/<space_id>/tasks/<seq>/delete",
        space_id="1",
        seq="1",
    )

    # Act
    default = await send(routes, "GET", "spaces/<space_id>/events", space_id="1")
    limited = [
        await send(
            routes,
            "GET",
            "spaces/<space_id>/events",
            query=[("limit", raw)],
            space_id="1",
        )
        for raw in ("1", "0", "999", "")
    ]

    # Assert
    assert default[0] == 200
    events = default[1]["events"]
    assert all(set(e) == EVENT_KEYS for e in events)
    assert {e["kind"] for e in events} == {"task", "space"}
    assert all(e["task_seq"] is None for e in events if e["kind"] == "space")
    assert "delete" in [e["action"] for e in events if e["kind"] == "task"]
    assert "snapshot" not in json.dumps(default[1], ensure_ascii=False)
    assert [len(body["events"]) for _, body in limited] == [
        1,
        1,
        len(events),
        len(events),
    ]


# ----- identity, leaks and error mapping ---------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("username", "expected"),
    [
        ("  ro\not\x00 ", "ro ot"),
        ("x" * 100, "x" * web_api.MAX_USERNAME_CHARS),
        (None, "unknown"),
        ("\t\n", "unknown"),
    ],
)
async def test_username_is_normalised(routes, seeded, username, expected):
    # Act
    _, task = await send(
        routes,
        "POST",
        "spaces/<space_id>/tasks",
        {"title": "t"},
        username=username,
        space_id="1",
    )

    # Assert
    assert task["creator"]["user_id"] == expected


@pytest.mark.asyncio
async def test_responses_never_leak_internal_fields(routes, seeded):
    # Act
    bodies = [
        (await send(routes, "GET", "spaces"))[1],
        (await send(routes, "GET", "spaces/<space_id>", space_id="1"))[1],
        (await send(routes, "GET", "spaces/<space_id>/tasks", space_id="1"))[1],
        (
            await send(
                routes, "GET", "spaces/<space_id>/tasks/<seq>", space_id="1", seq="1"
            )
        )[1],
        (await send(routes, "GET", "spaces/<space_id>/events", space_id="1"))[1],
    ]

    # Assert
    text = json.dumps(bodies, ensure_ascii=False)
    for word in ("created_session", "reminded", "code_hash", "detail", "actor_label"):
        assert word not in text
    assert seeded["code"] not in text
    assert all("data" not in body and body.get("status") != "error" for body in bodies)


class ExplodingService:
    """Raises a configured exception from every operation."""

    def __init__(self, error):
        self.error = error

    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise self.error

        return fail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status", "message"),
    [
        (NotFound("任务不存在"), 404, "任务不存在"),
        (TodoError("负责人必须是空间成员"), 400, "负责人必须是空间成员"),
        (ShareConflict("sharing unavailable"), 409, "sharing unavailable"),
        (RuntimeError("secret /path/db"), 500, web_api.INTERNAL_ERROR),
    ],
)
async def test_error_mapping(routes, holder, monkeypatch, error, status, message):
    # Arrange
    holder["service"] = ExplodingService(error)
    logged = []
    monkeypatch.setattr(web_api.logger, "exception", lambda *a: logged.append(a))

    # Act
    result = await send(routes, "GET", "spaces")

    # Assert
    assert result == (status, {"status": "error", "message": message, "data": None})
    assert len(logged) == (1 if status == 500 else 0)


@pytest.mark.asyncio
async def test_sync_members_continues_failures_and_refreshes_detail(
    holder, seeded, store, monkeypatch
):
    sessions = [
        GROUP,
        "qq:GroupMessage:2000",
        "qq:GroupMessage:3000",
        "qq:GroupMessage:4000",
    ]
    with store.transaction():
        for session in sessions[1:]:
            store.bind(session, 1)
    members = {
        session: [
            GroupMember(
                user=UserRef(platform_id="qq", user_id=f"new{index}"),
                nickname="New",
                names=("New",),
            )
        ]
        for index, session in enumerate(sessions)
        if index in (0, 3)
    }
    fetched, synced, logged = [], [], []

    async def fetch(session):
        fetched.append(session)
        if session == sessions[1]:
            raise TodoError("群不可用")
        if session == sessions[2]:
            raise RuntimeError("secret adapter failure")
        return members[session]

    def sync(username, space_id, session, received):
        synced.append((username, space_id, session, received))
        with store.transaction():
            for member in received:
                store.upsert_member(space_id, member.user, member.nickname)
        return 1

    monkeypatch.setattr(holder["service"], "sync_members", sync)
    monkeypatch.setattr(web_api.logger, "exception", lambda *args: logged.append(args))
    api = web_api.WebApi(
        lambda: holder["service"],
        TZ,
        fetch_group_members=fetch,
        remind_before_minutes=60,
        auto_archive_days=7,
    )
    routes = {(r.removeprefix(PREFIX), m[0]): h for r, h, m, _ in api.routes()}
    status, reply = await send(
        routes,
        "POST",
        "spaces/<space_id>/members/sync",
        space_id="1",
        username=" root\n admin ",
    )

    assert status == 200
    assert fetched == sessions
    assert reply["added"] == 2
    assert reply["results"] == [
        {
            "session": session,
            "title": "Dev Team" if session == GROUP else None,
            "added": 1 if index in (0, 3) else None,
            "error": {1: "群不可用", 2: "获取群成员失败"}.get(index),
        }
        for index, session in enumerate(sessions)
    ]
    assert synced == [
        ("root admin", 1, sessions[index], members[sessions[index]]) for index in (0, 3)
    ]
    assert logged
    assert any(member["nickname"] == "New" for member in reply["detail"]["members"])


@pytest.mark.asyncio
async def test_sync_members_without_bindings(routes, seeded, store):
    with store.transaction():
        store.unbind(GROUP, 1)
    status, reply = await send(
        routes, "POST", "spaces/<space_id>/members/sync", space_id="1"
    )
    assert status == 200
    assert reply["added"] == 0
    assert reply["results"] == []
    assert reply["detail"]["bindings"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("space_id", "body", "expected"), [("999", {}, 404), ("0", {}, 400), ("1", [], 400)]
)
async def test_sync_members_uses_endpoint_validation(
    routes, seeded, space_id, body, expected
):
    status, _ = await send(
        routes, "POST", "spaces/<space_id>/members/sync", body, space_id=space_id
    )
    assert status == expected


class RecordingService:
    """Proxies an ``AdminService`` and records which methods were called."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = []

    def __getattr__(self, name):
        attr = getattr(self.inner, name)

        def call(*args, **kwargs):
            self.calls.append(name)
            return attr(*args, **kwargs)

        return call


def sync_routes(holder, fetch):
    api = web_api.WebApi(
        lambda: holder["service"],
        TZ,
        fetch_group_members=fetch,
        remind_before_minutes=60,
        auto_archive_days=7,
    )
    return {(r.removeprefix(PREFIX), m[0]): h for r, h, m, _ in api.routes()}


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True], ids=["fetched", "fetch-failed"])
async def test_sync_members_stops_with_503_when_service_goes_away(
    holder, seeded, store, fails
):
    """A terminate() during the fetch must not reach the captured service."""
    with store.transaction():
        store.bind("qq:GroupMessage:2000", 1)
    old = RecordingService(holder["service"])
    holder["service"] = old
    fetched = []

    async def fetch(session):
        fetched.append(session)
        holder["service"] = None
        if fails:
            raise RuntimeError("adapter failure")
        return [GroupMember(UserRef("qq", "new"), "New", ("New",))]

    status, reply = await send(
        sync_routes(holder, fetch),
        "POST",
        "spaces/<space_id>/members/sync",
        space_id="1",
    )

    assert status == 503
    assert reply["message"] == web_api.NOT_READY
    assert old.calls == ["get_space"]
    assert len(fetched) == 1
    assert store.get_member(1, UserRef("qq", "new")) is None


@pytest.mark.asyncio
async def test_sync_members_uses_the_service_read_after_each_fetch(
    holder, seeded, store
):
    """A service replaced during the fetch is the one that syncs and answers."""
    old = RecordingService(holder["service"])
    new = RecordingService(AdminService(store, TZ, clock=lambda: NOW))
    holder["service"] = old

    async def fetch(session):
        holder["service"] = new
        return [GroupMember(UserRef("qq", "new"), "New", ("New",))]

    status, reply = await send(
        sync_routes(holder, fetch),
        "POST",
        "spaces/<space_id>/members/sync",
        space_id="1",
    )

    assert status == 200
    assert reply["added"] == 1
    assert old.calls == ["get_space"]
    assert new.calls == ["sync_members", "get_space"]


def configure_sharing(holder, store, *, enabled=True, base_url="https://todo.example/"):
    """Install a real shared-link service behind the existing API handlers."""
    shares = ShareService(
        store, TZ, enabled=enabled, base_url=base_url, clock=lambda: NOW
    )
    holder["service"] = AdminService(store, TZ, clock=lambda: NOW, share_service=shares)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "base_url", "available"),
    [
        (True, "https://todo.example/", True),
        (False, "https://todo.example/", False),
        (True, "invalid", False),
    ],
)
async def test_meta_share_available_uses_configuration(
    routes, holder, store, enabled, base_url, available
):
    configure_sharing(holder, store, enabled=enabled, base_url=base_url)
    status, reply = await send(routes, "GET", "meta")
    assert status == 200
    assert reply["share_available"] is available


@pytest.mark.asyncio
async def test_share_route_lifecycle_and_username_label(routes, holder, seeded, store):
    configure_sharing(holder, store)
    path = "spaces/<space_id>/share"
    status, opened = await send(
        routes,
        "POST",
        path,
        {"action": "on", "username": "spoofed"},
        space_id="1",
        username="  Dashboard\nOperator  ",
    )
    link = store.get_share(1)
    assert status == 200
    assert set(opened) == DETAIL_KEYS
    assert opened["share"] == {
        "url": f"https://todo.example/b/{link.token}/",
        "created_at": NOW,
    }
    _, unchanged = await send(routes, "POST", path, {"action": "on"}, space_id="1")
    assert unchanged == opened
    share_events = [
        event
        for event in store.list_space_events(1)
        if event.action == SpaceEventAction.SHARE
    ]
    assert len(share_events) == 1
    event = store.list_space_events(1)[0]
    assert event.actor is None
    assert event.actor_label == "Dashboard Operator"
    assert event.session == ""
    assert event.detail == {"op": "on"}
    _, detail = await send(routes, "GET", "spaces/<space_id>", space_id="1")
    assert detail["share"] == opened["share"]

    status, reset = await send(routes, "POST", path, {"action": "reset"}, space_id="1")
    assert status == 200
    assert reset["share"]["url"] != opened["share"]["url"]
    assert store.find_share(link.token) is None
    for _ in range(2):
        status, closed = await send(
            routes, "POST", path, {"action": "off"}, space_id="1"
        )
        assert status == 200
        assert closed["share"] is None
    assert [
        event.detail
        for event in store.list_space_events(1)
        if event.action == SpaceEventAction.SHARE
    ] == [{"op": "off"}, {"op": "reset"}, {"op": "on"}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"action": None},
        {"action": True},
        {"action": 1},
        {"action": []},
        {"action": {}},
        {"action": "ON"},
        {"action": " on "},
        {"action": ""},
        {"action": "invalid"},
    ],
)
async def test_share_route_rejects_invalid_actions(routes, seeded, store, body):
    before = store.list_space_events(1)
    status, _ = await send(
        routes, "POST", "spaces/<space_id>/share", body, space_id="1"
    )
    assert status == 400
    assert store.get_share(1) is None
    assert store.list_space_events(1) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["on", "reset", "off"])
@pytest.mark.parametrize(("space_id", "status"), [("0", 400), ("999", 404)])
async def test_share_route_rejects_invalid_or_missing_space(
    routes, seeded, action, space_id, status
):
    result = await send(
        routes, "POST", "spaces/<space_id>/share", {"action": action}, space_id=space_id
    )
    assert result[0] == status


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["on", "reset"])
async def test_share_route_unavailable_is_conflict(routes, seeded, store, action):
    status, reply = await send(
        routes, "POST", "spaces/<space_id>/share", {"action": action}, space_id="1"
    )
    assert status == 409
    assert reply["message"] == (
        "公开看板服务未开启或未配置对外访问地址，请联系 AstrBot 管理员"
    )
    assert store.get_share(1) is None


@pytest.mark.asyncio
async def test_share_route_reset_requires_existing_link(routes, holder, seeded, store):
    configure_sharing(holder, store)
    status, reply = await send(
        routes, "POST", "spaces/<space_id>/share", {"action": "reset"}, space_id="1"
    )
    assert status == 409
    assert reply["message"] == "当前空间还没有公开链接，用 /todo share on 开启"


@pytest.mark.asyncio
async def test_share_detail_invalid_base_and_disable_when_unavailable(
    routes, holder, seeded, store
):
    with store.transaction():
        link = store.set_share(1, "stored-token")
    configure_sharing(holder, store, base_url="invalid")
    status, detail = await send(routes, "GET", "spaces/<space_id>", space_id="1")
    assert status == 200
    assert detail["share"] == {"url": None, "created_at": link.created_at}
    assert store.get_share(1) == link
    status, detail = await send(
        routes, "POST", "spaces/<space_id>/share", {"action": "off"}, space_id="1"
    )
    assert status == 200
    assert detail["share"] is None
    assert store.get_share(1) is None


@pytest.mark.asyncio
async def test_share_link_only_appears_in_space_detail(routes, holder, seeded, store):
    configure_sharing(holder, store)
    _, opened = await send(
        routes, "POST", "spaces/<space_id>/share", {"action": "on"}, space_id="1"
    )
    token = store.get_share(1).token
    url = opened["share"]["url"]
    replies = [
        (await send(routes, "GET", "spaces"))[1],
        (await send(routes, "GET", "spaces/<space_id>/tasks", space_id="1"))[1],
        (
            await send(
                routes, "GET", "spaces/<space_id>/tasks/<seq>", space_id="1", seq="1"
            )
        )[1],
        (await send(routes, "GET", "spaces/<space_id>/events", space_id="1"))[1],
    ]
    for reply in replies:
        text = json.dumps(reply)
        assert token not in text
        assert url not in text
        assert '"share":' not in text
