import dataclasses as dcs

import pytest
from astrbot_plugin_teamtodo.core.models import (
    Actor,
    Member,
    Space,
    SpaceRole,
    Task,
    TaskStatus,
    TodoError,
    UserRef,
)
from astrbot_plugin_teamtodo.core.permissions import (
    DENIAL_MESSAGES,
    Op,
    can_claim,
    can_delete_space,
    can_delete_task,
    can_edit_task,
    can_manage_admins,
    can_manage_space,
    can_run_session_op,
    can_use_space,
    is_space_admin,
    is_space_owner,
    require,
)

SPACE_ID = 1
OWNER = UserRef(platform_id="qq", user_id="owner")
ADMIN = UserRef(platform_id="qq", user_id="admin")
CREATOR = UserRef(platform_id="qq", user_id="creator")
ASSIGNEE = UserRef(platform_id="qq", user_id="assignee")
STRANGER = UserRef(platform_id="qq", user_id="stranger")

SPACE = Space(id=SPACE_ID, name="team", owner=OWNER, created_at=0.0)
ORPHAN_SPACE = Space(id=SPACE_ID, name="team", owner=None, created_at=0.0)
TASK = Task(
    id=10,
    space_id=SPACE_ID,
    seq=1,
    title="t",
    status=TaskStatus.TODO,
    assignees=(ASSIGNEE,),
    due_at=None,
    tags=(),
    priority=None,
    description="",
    creator=CREATOR,
    created_session="qq:GroupMessage:1",
    created_at=0.0,
    updated_at=0.0,
    done_at=None,
    reminded=False,
)


def actor(user, *, bot_admin=False, session_admin=False):
    return Actor(
        user=user,
        nickname=user.user_id,
        session="qq:GroupMessage:1",
        is_private=False,
        is_bot_admin=bot_admin,
        is_session_admin=session_admin or bot_admin,
    )


def member(user, role, space_id=SPACE_ID):
    return Member(
        space_id=space_id, user=user, nickname=user.user_id, role=role, joined_at=0.0
    )


# Each case: (user, role or None for no member row)
ROLES = {
    "owner": (OWNER, SpaceRole.OWNER),
    "admin": (ADMIN, SpaceRole.ADMIN),
    "creator": (CREATOR, SpaceRole.MEMBER),
    "assignee": (ASSIGNEE, SpaceRole.MEMBER),
    "member": (STRANGER, SpaceRole.MEMBER),
    "non_member": (STRANGER, None),
}

# Expected results per operation, in ROLES order.
MATRIX = {
    "edit_task": {
        "owner": True,
        "admin": True,
        "creator": True,
        "assignee": True,
        "member": False,
        "non_member": False,
    },
    "delete_task": {
        "owner": True,
        "admin": True,
        "creator": True,
        "assignee": False,
        "member": False,
        "non_member": False,
    },
    "manage_space": {
        "owner": True,
        "admin": True,
        "creator": False,
        "assignee": False,
        "member": False,
        "non_member": False,
    },
    "manage_admins": {
        "owner": True,
        "admin": False,
        "creator": False,
        "assignee": False,
        "member": False,
        "non_member": False,
    },
    "delete_space": {
        "owner": True,
        "admin": False,
        "creator": False,
        "assignee": False,
        "member": False,
        "non_member": False,
    },
    "use_space": dict.fromkeys(ROLES, True),
}

CHECKS = {
    "edit_task": lambda a, m: can_edit_task(a, m, TASK),
    "delete_task": lambda a, m: can_delete_task(a, m, TASK),
    "manage_space": lambda a, m: can_manage_space(a, m, SPACE),
    "manage_admins": lambda a, m: can_manage_admins(a, m, SPACE),
    "delete_space": lambda a, m: can_delete_space(a, m, SPACE),
    "use_space": lambda a, m: can_use_space(a, m),
}


@pytest.mark.parametrize(("op", "who"), [(op, who) for op in MATRIX for who in ROLES])
def test_permission_matrix_matches_design(op, who):
    # Arrange
    user, role = ROLES[who]
    the_member = member(user, role) if role else None

    # Act
    allowed = CHECKS[op](actor(user), the_member)

    # Assert
    assert allowed is MATRIX[op][who]


@pytest.mark.parametrize("op", list(CHECKS))
def test_bot_admin_may_do_everything_without_member_row(op):
    # Arrange
    bot_admin = actor(STRANGER, bot_admin=True)

    # Act
    allowed = CHECKS[op](bot_admin, None)

    # Assert
    assert allowed is True


def test_assignee_cannot_delete_but_can_edit():
    # Arrange
    a, m = actor(ASSIGNEE), member(ASSIGNEE, SpaceRole.MEMBER)

    # Act / Assert
    assert can_edit_task(a, m, TASK) is True
    assert can_delete_task(a, m, TASK) is False


def test_creator_can_delete_own_task_without_member_row():
    # Arrange
    a = actor(CREATOR)

    # Act
    allowed = can_delete_task(a, None, TASK)

    # Assert
    assert allowed is True


def test_assignee_who_is_also_admin_can_delete():
    # Arrange
    a, m = actor(ASSIGNEE), member(ASSIGNEE, SpaceRole.ADMIN)

    # Act
    allowed = can_delete_task(a, m, TASK)

    # Assert
    assert allowed is True


def test_member_row_of_another_space_grants_nothing():
    # Arrange
    a, m = actor(ADMIN), member(ADMIN, SpaceRole.ADMIN, space_id=99)

    # Act / Assert
    assert is_space_admin(a, m, SPACE) is False
    assert can_edit_task(a, m, TASK) is False
    assert can_delete_task(a, m, TASK) is False


def test_member_row_of_another_user_grants_nothing():
    # Arrange
    a, m = actor(STRANGER), member(ADMIN, SpaceRole.ADMIN)

    # Act / Assert
    assert is_space_admin(a, m, SPACE) is False
    assert can_delete_task(a, m, TASK) is False


def test_owner_role_without_matching_space_owner_fails_closed():
    # Arrange
    a, m = actor(ADMIN), member(ADMIN, SpaceRole.OWNER)

    # Act / Assert
    assert is_space_owner(a, m, SPACE) is False
    assert is_space_admin(a, m, SPACE) is True


def test_space_owner_field_without_owner_role_fails_closed():
    # Arrange
    a, m = actor(OWNER), member(OWNER, SpaceRole.MEMBER)

    # Act / Assert
    assert is_space_owner(a, m, SPACE) is False


@pytest.mark.parametrize(
    ("bot_admin", "session_admin", "expected"),
    [(False, False, False), (False, True, True), (True, False, True)],
)
def test_session_ops_need_session_or_bot_admin(bot_admin, session_admin, expected):
    # Arrange
    a = dcs.replace(
        actor(STRANGER), is_bot_admin=bot_admin, is_session_admin=session_admin
    )

    # Act
    allowed = can_run_session_op(a)

    # Assert
    assert allowed is expected


def test_session_admin_can_claim_ownerless_space():
    # Arrange
    a = actor(STRANGER, session_admin=True)

    # Act / Assert
    assert can_claim(a, ORPHAN_SPACE) is True


@pytest.mark.parametrize("bot_admin", [False, True])
def test_nobody_can_claim_owned_space(bot_admin):
    # Arrange
    a = actor(STRANGER, session_admin=True, bot_admin=bot_admin)

    # Act / Assert
    assert can_claim(a, SPACE) is False


def test_plain_member_cannot_claim_ownerless_space():
    # Arrange
    a = actor(STRANGER)

    # Act / Assert
    assert can_claim(a, ORPHAN_SPACE) is False


def test_require_raises_standard_message_when_denied():
    # Act
    with pytest.raises(TodoError) as exc_info:
        require(False, Op.DELETE_TASK)

    # Assert
    assert str(exc_info.value) == DENIAL_MESSAGES[Op.DELETE_TASK]


def test_require_passes_when_allowed():
    # Act / Assert
    require(True, Op.DELETE_SPACE)


def test_every_op_has_a_denial_message():
    # Assert
    assert set(DENIAL_MESSAGES) == set(Op)
    assert all(DENIAL_MESSAGES.values())
