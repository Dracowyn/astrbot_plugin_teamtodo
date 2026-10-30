import dataclasses as dcs

import pytest
from astrbot_plugin_teamtodo.core.models import (
    MAX_ASSIGNEES,
    MAX_DESCRIPTION_CHARS,
    MAX_TAG_CHARS,
    MAX_TAGS,
    MAX_TITLE_CHARS,
    EventAction,
    Mention,
    Priority,
    TaskDraft,
    TaskStatus,
    TodoError,
    UserRef,
)
from astrbot_plugin_teamtodo.core.store import Store
from astrbot_plugin_teamtodo.services import task_ops

USER = UserRef(platform_id="qq", user_id="owner")
OTHER = UserRef(platform_id="tg", user_id="member")


@pytest.fixture
def context():
    store = Store(":memory:", clock=lambda: 100.0)
    with store.transaction():
        space = store.create_space("Team", USER)
        task = store.create_task(
            TaskDraft(
                space_id=space.id,
                title="Original",
                creator=USER,
                created_session="chat",
                due_at=200.0,
            )
        )
    yield store, task
    store.close()


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"title": " \n "}, "标题不能为空"),
        ({"title": "x" * (MAX_TITLE_CHARS + 1)}, "标题不能超过"),
        ({"description": "x" * (MAX_DESCRIPTION_CHARS + 1)}, "描述不能超过"),
        ({"tags": tuple(str(i) for i in range(MAX_TAGS + 1))}, "标签不能超过"),
        ({"tags": ("x" * (MAX_TAG_CHARS + 1),)}, "单个标签不能超过"),
        (
            {
                "assignees": tuple(
                    UserRef("qq", str(i)) for i in range(MAX_ASSIGNEES + 1)
                )
            },
            "负责人不能超过",
        ),
    ],
)
def test_limits_reject_invalid_task_without_writes(context, fields, message):
    store, task = context
    with pytest.raises(TodoError, match=message), store.transaction():
        task_ops.apply(
            store,
            lambda: 123.0,
            task,
            dcs.replace(task, **fields),
            actor=None,
            actor_label="admin",
            session="",
        )
    assert store.get_task(task.space_id, task.seq) == task
    assert store.list_events(task.space_id) == []


def test_exact_limits_accept_draft(context):
    store, task = context
    draft = TaskDraft(
        space_id=task.space_id,
        title="x" * MAX_TITLE_CHARS,
        creator=USER,
        created_session="chat",
        description="x" * MAX_DESCRIPTION_CHARS,
        tags=tuple(str(i) * MAX_TAG_CHARS for i in range(MAX_TAGS)),
        assignees=tuple(UserRef("qq", str(i)) for i in range(MAX_ASSIGNEES)),
    )
    with store.transaction():
        task_ops.check_task(draft)
        created = store.create_task(draft)
    saved = store.get_task(task.space_id, created.seq)
    assert (saved.title, saved.description, saved.tags, saved.assignees) == (
        draft.title,
        draft.description,
        draft.tags,
        draft.assignees,
    )


def test_combined_changes_persist_and_log_each_kind(context):
    store, task = context
    with store.transaction():
        task = store.update_task(dcs.replace(task, reminded=True))
        saved = task_ops.apply(
            store,
            lambda: 123.0,
            task,
            dcs.replace(
                task,
                title="Updated",
                status=TaskStatus.DONE,
                due_at=300.0,
                tags=("tag",),
                priority=Priority.HIGH,
                description="Details",
                assignees=(OTHER,),
            ),
            actor=None,
            actor_label="dashboard",
            session="",
            added_assignees=(Mention(user=OTHER, name="Nick"),),
        )
    assert saved == store.get_task(task.space_id, task.seq)
    assert saved.done_at == 123.0
    assert not saved.reminded
    assert store.get_member(task.space_id, OTHER).nickname == "Nick"
    events = store.list_events(task.space_id)
    assert [event.action for event in events] == [
        EventAction.STATUS,
        EventAction.ASSIGN,
        EventAction.UPDATE,
    ]
    assert all(
        event.actor is None and event.actor_label == "dashboard" and event.session == ""
        for event in events
    )
    assert events[0].detail == {"status": ["todo", "done"]}
    assert events[1].detail == {"assignees": [[], [OTHER.key]]}
    assert events[2].detail == {
        "title": ["Original", "Updated"],
        "due_at": [200.0, 300.0],
        "priority": [None, "high"],
        "tags": [[], ["tag"]],
        "description": ["", "Details"],
    }


@pytest.mark.parametrize(
    "before, after, expected",
    [
        (TaskStatus.TODO, TaskStatus.DONE, 123.0),
        (TaskStatus.DONE, TaskStatus.ARCHIVED, 42.0),
        (TaskStatus.TODO, TaskStatus.ARCHIVED, None),
        (TaskStatus.DONE, TaskStatus.TODO, None),
        (TaskStatus.ARCHIVED, TaskStatus.DOING, None),
        (TaskStatus.ARCHIVED, TaskStatus.DONE, 123.0),
    ],
)
def test_status_transitions_control_completion_timestamp(
    context, before, after, expected
):
    store, task = context
    with store.transaction():
        task = store.update_task(
            dcs.replace(
                task,
                status=before,
                done_at=42.0
                if before in (TaskStatus.DONE, TaskStatus.ARCHIVED)
                else None,
                reminded=True,
            )
        )
        saved = task_ops.apply(
            store,
            lambda: 123.0,
            task,
            dcs.replace(task, status=after),
            actor=USER,
            actor_label="",
            session="chat",
        )
    assert saved.status == after
    assert saved.done_at == expected
    assert saved.reminded
    event = store.list_events(task.space_id)[0]
    assert event.action == (
        EventAction.ARCHIVE if after == TaskStatus.ARCHIVED else EventAction.STATUS
    )
    assert (event.actor, event.actor_label, event.session) == (USER, "", "chat")


def test_noop_upserts_mentions_without_task_or_log_changes(context):
    store, task = context
    with store.transaction():
        saved = task_ops.apply(
            store,
            lambda: 123.0,
            task,
            task,
            actor=USER,
            actor_label="",
            session="chat",
            added_assignees=(Mention(user=OTHER, name="Fresh"),),
        )
    assert saved is None
    assert store.get_task(task.space_id, task.seq) == task
    assert store.list_events(task.space_id) == []
    assert store.get_member(task.space_id, OTHER).nickname == "Fresh"


def test_same_status_and_due_keep_completion_and_reminder(context):
    store, task = context
    with store.transaction():
        task = store.update_task(
            dcs.replace(task, status=TaskStatus.DONE, done_at=42.0, reminded=True)
        )
        saved = task_ops.apply(
            store,
            lambda: 123.0,
            task,
            dcs.replace(task, title="Renamed"),
            actor=USER,
            actor_label="",
            session="chat",
        )
    assert saved.done_at == 42.0
    assert saved.reminded
    assert [event.action for event in store.list_events(task.space_id)] == [
        EventAction.UPDATE
    ]


def test_logging_rolls_back_with_task_and_member_changes(context):
    store, task = context
    with pytest.raises(RuntimeError, match="abort"), store.transaction():
        task_ops.apply(
            store,
            lambda: 123.0,
            task,
            dcs.replace(task, title="Changed", assignees=(OTHER,)),
            actor=USER,
            actor_label="",
            session="chat",
            added_assignees=(Mention(user=OTHER, name="Nick"),),
        )
        raise RuntimeError("abort")
    assert store.get_task(task.space_id, task.seq) == task
    assert store.get_member(task.space_id, OTHER) is None
    assert store.list_events(task.space_id) == []
