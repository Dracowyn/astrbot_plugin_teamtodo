"""M4 link transitions and public board contract against the real store."""

import json
import re
from dataclasses import replace
from zoneinfo import ZoneInfo

import pytest
from astrbot_plugin_teamtodo.core.models import (
    Priority,
    SpaceEventAction,
    TaskDraft,
    TaskStatus,
    TodoError,
    UserRef,
)
from astrbot_plugin_teamtodo.core.store import Store
from astrbot_plugin_teamtodo.services.share import (
    ShareConflict,
    ShareService,
    new_token,
    normalize_base_url,
)

OWNER = UserRef(platform_id="private-adapter", user_id="private-owner-id")
UNKNOWN = UserRef(platform_id="other-adapter", user_id="private-unknown-id")
TZ = ZoneInfo("Asia/Shanghai")
UNAVAILABLE = "公开看板服务未开启或未配置对外访问地址，请联系 AstrBot 管理员"
NO_RESET = "当前空间还没有公开链接，用 /todo share on 开启"


@pytest.fixture
def setup():
    now = [100.0]
    store = Store(":memory:", clock=lambda: now[0])
    with store.transaction():
        space = store.create_space("团队", OWNER)
        store.upsert_member(space.id, OWNER, "小明")
    shares = ShareService(
        store,
        TZ,
        enabled=True,
        base_url=" https://example.test/prefix/// ",
        clock=lambda: now[0],
    )
    yield store, space, shares, now
    store.close()


def test_tokens_have_192_bits_urlsafe_shape_and_are_unique():
    tokens = {new_token() for _ in range(1000)}
    assert len(tokens) == 1000
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{32}", token) for token in tokens)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("", ""),
        ("  ", ""),
        ("example.test", ""),
        ("ftp://example.test", ""),
        ("//example.test", ""),
        ("HTTPS://example.test", ""),
        (" https://example.test/prefix/// \n", "https://example.test/prefix"),
        ("http://127.0.0.1:6200/", "http://127.0.0.1:6200"),
        ("https://example.test", "https://example.test"),
        ("https://[::1]:6200/p/", "https://[::1]:6200/p"),
        ("https://example.test/a b", ""),
        ("https://example.test\n伪造的第二行", ""),
        ("https://example.test/\tx", ""),
        ("https://example.test/\x00", ""),
        ("https://example.test/?x=1", ""),
        ("https://example.test?", ""),
        ("https://example.test/#frag", ""),
        ("https://user:secret@example.test", ""),
        ("https://@example.test", ""),
        ("https://example.test:abc", ""),
        ("https://example.test:99999", ""),
        ("https://[::1", ""),
        ("https://", ""),
        ("http:///path", ""),
    ],
)
def test_normalize_base_url_follows_config_contract(raw, expected):
    assert normalize_base_url(raw) == expected


def test_on_reset_off_preserve_noops_and_log_only_changes(setup):
    store, space, shares, now = setup
    assert shares.available is True
    with store.transaction():
        assert shares.change(space.id, "off") is None
        first = shares.change(space.id, "on", actor=OWNER, session="private-session")
    assert shares.url(first) == f"https://example.test/prefix/b/{first.token}/"
    now[0] = 200
    with store.transaction():
        assert shares.change(space.id, "on") == first
    assert store.list_space_events(space.id)[0].detail == {"op": "on"}
    assert len(store.list_space_events(space.id)) == 1
    with store.transaction():
        second = shares.change(space.id, "reset", actor_label="dashboard-admin")
    assert second.token != first.token
    assert second.created_at == 200
    assert shares.board_for_token(first.token) is None
    assert store.find_share(first.token) is None
    with store.transaction():
        assert shares.change(space.id, "off", actor=OWNER) is None
        assert shares.change(space.id, "off") is None
    assert shares.board_for_token(second.token) is None
    entries = store.list_space_events(space.id)
    assert [entry.detail for entry in entries] == [
        {"op": "off"},
        {"op": "reset"},
        {"op": "on"},
    ]
    assert all(entry.action == SpaceEventAction.SHARE for entry in entries)
    assert entries[-1].actor == OWNER
    assert entries[-1].session == "private-session"
    assert entries[1].actor is None
    assert entries[1].actor_label == "dashboard-admin"
    assert entries[1].session == ""
    assert first.token not in json.dumps([entry.detail for entry in entries])
    assert second.token not in json.dumps([entry.detail for entry in entries])


@pytest.mark.parametrize(
    "enabled,base", [(False, "https://example.test"), (True, "bad")]
)
@pytest.mark.parametrize("action", ["on", "reset"])
def test_unavailable_is_conflict_even_when_link_exists(setup, enabled, base, action):
    store, space, shares, _ = setup
    with store.transaction():
        original = shares.change(space.id, "on")
    unavailable = ShareService(store, TZ, enabled=enabled, base_url=base)
    assert unavailable.available is False
    with pytest.raises(ShareConflict, match=UNAVAILABLE):
        with store.transaction():
            unavailable.change(space.id, action)
    assert store.get_share(space.id) == original
    assert len(store.list_space_events(space.id)) == 1
    assert unavailable.url(original) == (
        f"https://example.test/b/{original.token}/" if enabled is False else None
    )
    with store.transaction():
        unavailable.change(space.id, "off")
    assert store.get_share(space.id) is None


def test_reset_missing_and_invalid_action_do_not_write(setup):
    store, space, shares, _ = setup
    with pytest.raises(ShareConflict, match=NO_RESET):
        with store.transaction():
            shares.change(space.id, "reset")
    for action in ("", "ON", "unknown"):
        with pytest.raises(TodoError):
            with store.transaction():
                shares.change(space.id, action)
    assert store.get_share(space.id) is None
    assert store.list_space_events(space.id) == []


def test_link_and_event_rollback_together(setup):
    store, space, shares, _ = setup
    with pytest.raises(RuntimeError, match="rollback"):
        with store.transaction():
            shares.change(space.id, "on")
            raise RuntimeError("rollback")
    assert store.get_share(space.id) is None
    assert store.list_space_events(space.id) == []


def test_board_whitelist_order_nicknames_and_hidden_rows(setup):
    store, space, shares, now = setup
    tasks = []
    with store.transaction():
        link = shares.change(space.id, "on")
        for status in (
            TaskStatus.DOING,
            TaskStatus.TODO,
            TaskStatus.DONE,
            TaskStatus.ARCHIVED,
        ):
            now[0] += 10
            task = store.create_task(
                TaskDraft(
                    space_id=space.id,
                    title=f"任务 {status}",
                    creator=OWNER,
                    created_session="private-session",
                    assignees=(OWNER, UNKNOWN),
                    due_at=50,
                    tags=("标签",),
                    priority=Priority.HIGH,
                    description="描述",
                )
            )
            tasks.append(
                store.update_task(
                    replace(
                        task,
                        status=status,
                        done_at=now[0] if status == TaskStatus.DONE else None,
                    )
                )
            )
    now[0] = 200
    board = shares.board_for_token(link.token)
    assert set(board) == {"space", "tasks", "updated_at", "server_time", "timezone"}
    assert board["space"] == {"name": "团队"}
    assert board["updated_at"] == 130
    assert board["server_time"] == 200
    assert board["timezone"] == "Asia/Shanghai"
    assert [task["seq"] for task in board["tasks"]] == [1, 2, 3]
    for public, task in zip(board["tasks"], tasks, strict=False):
        assert set(public) == {
            "seq",
            "title",
            "status",
            "assignees",
            "due_at",
            "tags",
            "priority",
            "description",
            "done_at",
            "updated_at",
            "overdue",
        }
        assert public == {
            "seq": task.seq,
            "title": task.title,
            "status": task.status.value,
            "assignees": [{"nickname": "小明"}, {"nickname": "未知成员"}],
            "due_at": 50,
            "tags": ["标签"],
            "priority": "high",
            "description": "描述",
            "done_at": task.done_at,
            "updated_at": task.updated_at,
            "overdue": task.status in (TaskStatus.TODO, TaskStatus.DOING),
        }
        assert all(set(member) == {"nickname"} for member in public["assignees"])
    dump = json.dumps(board)
    for hidden in (
        OWNER.user_id,
        UNKNOWN.user_id,
        OWNER.platform_id,
        UNKNOWN.platform_id,
        "private-session",
    ):
        assert hidden not in dump
    assert shares.board_for_token("unknown-token") is None
    with store.transaction():
        store.delete_space(space.id)
    assert shares.board_for_token(link.token) is None


@pytest.mark.parametrize(
    "due_at,status,overdue",
    [
        (None, TaskStatus.TODO, False),
        (200, TaskStatus.TODO, False),
        (201, TaskStatus.DOING, False),
        (199, TaskStatus.DOING, True),
        (199, TaskStatus.DONE, False),
    ],
)
def test_overdue_boundaries(setup, due_at, status, overdue):
    store, space, shares, now = setup
    now[0] = 200
    with store.transaction():
        link = shares.change(space.id, "on")
        task = store.create_task(
            TaskDraft(
                space_id=space.id,
                title="边界",
                creator=OWNER,
                created_session="",
                due_at=due_at,
            )
        )
        store.update_task(replace(task, status=status))
    assert shares.board_for_token(link.token)["tasks"][0]["overdue"] is overdue


def test_empty_board_and_space_creation_updated_at_floor(setup):
    store, space, shares, now = setup
    with store.transaction():
        link = shares.change(space.id, "on")
    assert shares.board_for_token(link.token)["updated_at"] == space.created_at
    assert shares.board_for_token(link.token)["tasks"] == []
    now[0] = 50
    with store.transaction():
        store.create_task(
            TaskDraft(
                space_id=space.id, title="旧时间", creator=OWNER, created_session=""
            )
        )
    assert shares.board_for_token(link.token)["updated_at"] == space.created_at


def test_board_never_shows_id_like_nicknames(setup):
    store, space, shares, _ = setup
    members = {
        UserRef("weixin", "wxid_abcdef123456"): "wxid_abcdef123456",
        UserRef("line", "U1234567890abcdef"): "U1234567",
        UserRef("qq", "10001"): "  ",
        UserRef("qq", "10002"): " 小红 ",
        UserRef("qq", "abc12345"): "abc",
        UserRef("qq", "10003"): "",
    }
    with store.transaction():
        link = shares.change(space.id, "on")
        for user, nickname in members.items():
            store.upsert_member(space.id, user, nickname)
        store.create_task(
            TaskDraft(
                space_id=space.id,
                title="昵称",
                creator=OWNER,
                created_session="",
                assignees=tuple(members),
            )
        )
    board = shares.board_for_token(link.token)
    assert board["tasks"][0]["assignees"] == [
        {"nickname": "未知成员"},
        {"nickname": "未知成员"},
        {"nickname": "未知成员"},
        {"nickname": "小红"},
        # A short nickname that happens to be an id prefix is a real name.
        {"nickname": "abc"},
        {"nickname": "未知成员"},
    ]
    dump = json.dumps(board, ensure_ascii=False)
    for user in members:
        assert user.user_id not in dump
    assert "U1234567" not in dump
