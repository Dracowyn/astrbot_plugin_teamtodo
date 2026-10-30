import hashlib
from types import SimpleNamespace

import pytest
from astrbot_plugin_teamtodo.core.models import Invite
from astrbot_plugin_teamtodo.services import access
from astrbot_plugin_teamtodo.services.access import (
    CONFIRM_TTL_SECONDS,
    GROUP_CACHE_TTL_SECONDS,
    INVITE_ALPHABET,
    INVITE_CODE_LENGTH,
    INVITE_TTL_SECONDS,
    JOIN_FAILURE_LIMIT,
    JOIN_FAILURE_WINDOW_SECONDS,
    ConfirmationCodes,
    JoinRateLimiter,
    SessionAdminResolver,
    SessionInfo,
    generate_invite_code,
    hash_invite_code,
    is_invite_expired,
    new_invite,
)


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeEvent:
    """Duck-typed stand-in for AstrMessageEvent."""

    def __init__(
        self,
        sender="u1",
        *,
        private=False,
        bot_admin=False,
        group=None,
        error=None,
        session="qq:GroupMessage:100",
    ):
        self.sender = sender
        self.private = private
        self.bot_admin = bot_admin
        self.group = group
        self.error = error
        self.unified_msg_origin = session
        self.get_group_calls = 0

    def is_admin(self):
        return self.bot_admin

    def is_private_chat(self):
        return self.private

    def get_sender_id(self):
        return self.sender

    async def get_group(self):
        self.get_group_calls += 1
        if self.error:
            raise self.error
        return self.group


def group(owner="boss", admins=("mod",), name="Team Chat"):
    return SimpleNamespace(
        group_id="100",
        group_owner=owner,
        group_admins=list(admins) if admins is not None else None,
        group_name=name,
    )


# --- session admin resolution -------------------------------------------------


@pytest.mark.asyncio
async def test_private_chat_sender_is_session_admin():
    # Arrange
    event = FakeEvent(private=True)

    # Act
    info = await SessionAdminResolver().resolve(event)

    # Assert
    assert info == SessionInfo(is_session_admin=True, group_name=None)
    assert event.get_group_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sender", "expected"), [("boss", True), ("mod", True), ("pleb", False)]
)
async def test_group_owner_and_admins_are_session_admins(sender, expected):
    # Arrange
    event = FakeEvent(sender, group=group())

    # Act
    info = await SessionAdminResolver().resolve(event)

    # Assert
    assert info.is_session_admin is expected
    assert info.group_name == "Team Chat"


@pytest.mark.asyncio
async def test_bot_admin_is_session_admin_in_any_group():
    # Arrange
    event = FakeEvent("pleb", bot_admin=True, group=group())

    # Act
    info = await SessionAdminResolver().resolve(event)

    # Assert
    assert info.is_session_admin is True
    assert info.group_name == "Team Chat"


@pytest.mark.asyncio
async def test_platform_without_role_fields_yields_non_admin():
    # Arrange
    event = FakeEvent("boss", group=group(owner=None, admins=None, name=None))

    # Act
    info = await SessionAdminResolver().resolve(event)

    # Assert
    assert info == SessionInfo(is_session_admin=False, group_name=None)


@pytest.mark.asyncio
async def test_empty_sender_id_never_matches_missing_owner():
    # Arrange
    event = FakeEvent("", group=group(owner="", admins=("",)))

    # Act
    info = await SessionAdminResolver().resolve(event)

    # Assert
    assert info.is_session_admin is False


@pytest.mark.asyncio
async def test_numeric_ids_from_platform_are_compared_as_strings():
    # Arrange
    event = FakeEvent("42", group=group(owner=42, admins=(7,)))

    # Act
    info = await SessionAdminResolver().resolve(event)

    # Assert
    assert info.is_session_admin is True


@pytest.mark.asyncio
async def test_get_group_raising_fails_closed_and_logs(monkeypatch):
    # Arrange
    warnings = []
    monkeypatch.setattr(
        access.logger, "warning", lambda *args, **kwargs: warnings.append(args)
    )
    event = FakeEvent("boss", error=RuntimeError("api down"))

    # Act
    info = await SessionAdminResolver().resolve(event)

    # Assert
    assert info == SessionInfo(is_session_admin=False, group_name=None)
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_get_group_raising_still_honours_bot_admin():
    # Arrange
    event = FakeEvent("boss", bot_admin=True, error=RuntimeError("api down"))

    # Act
    info = await SessionAdminResolver().resolve(event)

    # Assert
    assert info.is_session_admin is True


@pytest.mark.asyncio
async def test_get_group_returning_none_fails_closed_and_is_not_cached():
    # Arrange
    resolver = SessionAdminResolver()
    event = FakeEvent("boss", group=None)

    # Act
    first = await resolver.resolve(event)
    event.group = group()
    second = await resolver.resolve(event)

    # Assert
    assert first.is_session_admin is False
    assert second.is_session_admin is True
    assert event.get_group_calls == 2


@pytest.mark.asyncio
async def test_group_lookup_is_cached_per_session_until_ttl():
    # Arrange
    clock = FakeClock()
    resolver = SessionAdminResolver(clock=clock)
    event = FakeEvent("mod", group=group())
    await resolver.resolve(event)
    event.group = group(admins=())  # mod demoted on the platform

    # Act
    clock.now += GROUP_CACHE_TTL_SECONDS - 1
    cached = await resolver.resolve(event)
    clock.now += 1
    refreshed = await resolver.resolve(event)

    # Assert
    assert cached.is_session_admin is True
    assert refreshed.is_session_admin is False
    assert event.get_group_calls == 2


@pytest.mark.asyncio
async def test_cache_is_keyed_by_session():
    # Arrange
    resolver = SessionAdminResolver(clock=FakeClock())
    first = FakeEvent("mod", group=group(), session="qq:GroupMessage:1")
    second = FakeEvent("mod", group=group(admins=()), session="qq:GroupMessage:2")

    # Act
    a = await resolver.resolve(first)
    b = await resolver.resolve(second)

    # Assert
    assert a.is_session_admin is True
    assert b.is_session_admin is False


@pytest.mark.asyncio
async def test_stale_cache_entries_are_pruned():
    # Arrange
    clock = FakeClock()
    resolver = SessionAdminResolver(clock=clock)
    await resolver.resolve(FakeEvent(group=group(), session="s1"))

    # Act
    clock.now += GROUP_CACHE_TTL_SECONDS
    await resolver.resolve(FakeEvent(group=group(), session="s2"))

    # Assert
    assert set(resolver._cache) == {"s2"}


# --- invite codes -------------------------------------------------------------


def test_invite_alphabet_has_no_ambiguous_characters():
    # Assert
    assert not set("0O1IL") & set(INVITE_ALPHABET)
    assert len(set(INVITE_ALPHABET)) == len(INVITE_ALPHABET)


def test_generated_invite_code_has_length_and_alphabet():
    # Act
    codes = [generate_invite_code() for _ in range(200)]

    # Assert
    assert all(len(c) == INVITE_CODE_LENGTH for c in codes)
    assert all(set(c) <= set(INVITE_ALPHABET) for c in codes)
    assert len(set(codes)) == len(codes)


def test_hash_is_sha256_of_normalized_code():
    # Arrange
    code = "ABCDEFGHJKMN"

    # Act
    digest = hash_invite_code(code)

    # Assert
    assert digest == hashlib.sha256(code.encode()).hexdigest()
    assert hash_invite_code(code) == digest


def test_hash_normalizes_case_and_surrounding_whitespace():
    # Act / Assert
    assert hash_invite_code("  abcdefghjkmn\n") == hash_invite_code("ABCDEFGHJKMN")


def test_different_codes_hash_differently():
    # Act / Assert
    assert hash_invite_code("ABCDEFGHJKMN") != hash_invite_code("ABCDEFGHJKMP")


def test_new_invite_stores_only_hash_and_expires_after_ttl():
    # Act
    code, invite = new_invite(space_id=3, now=1000.0)

    # Assert
    assert invite == Invite(
        space_id=3,
        code_hash=hash_invite_code(code),
        expires_at=1000.0 + INVITE_TTL_SECONDS,
    )
    assert code not in invite.code_hash
    assert INVITE_TTL_SECONDS == 7 * 24 * 3600


def test_invite_expiry_boundary():
    # Arrange
    invite = Invite(space_id=1, code_hash="x", expires_at=500.0)

    # Act / Assert
    assert is_invite_expired(invite, 499.9) is False
    assert is_invite_expired(invite, 500.0) is True


# --- join rate limiter --------------------------------------------------------


def test_join_is_blocked_after_limit_failures_in_window():
    # Arrange
    clock = FakeClock()
    limiter = JoinRateLimiter(clock=clock)

    # Act
    for _ in range(JOIN_FAILURE_LIMIT - 1):
        limiter.record_failure("s")
        clock.now += 1
    before_limit = limiter.is_blocked("s")
    limiter.record_failure("s")

    # Assert
    assert before_limit is False
    assert limiter.is_blocked("s") is True
    assert limiter.is_blocked("other") is False


def test_join_unblocks_as_failures_leave_rolling_window():
    # Arrange
    clock = FakeClock()
    limiter = JoinRateLimiter(clock=clock)
    limiter.record_failure("s")
    clock.now += 600
    for _ in range(JOIN_FAILURE_LIMIT - 1):
        limiter.record_failure("s")

    # Act
    clock.now += JOIN_FAILURE_WINDOW_SECONDS - 600 - 1
    still_blocked = limiter.is_blocked("s")
    clock.now += 1
    after_oldest_expired = limiter.is_blocked("s")

    # Assert
    assert still_blocked is True
    assert after_oldest_expired is False


def test_rate_limiter_memory_is_bounded():
    # Arrange
    clock = FakeClock()
    limiter = JoinRateLimiter(clock=clock)
    for i in range(50):
        limiter.record_failure("s")
        limiter.record_failure(f"old-{i}")

    # Act
    clock.now += JOIN_FAILURE_WINDOW_SECONDS
    limiter.record_failure("new")

    # Assert
    assert set(limiter._failures) == {"new"}


def test_rate_limiter_keeps_at_most_limit_timestamps_per_session():
    # Arrange
    limiter = JoinRateLimiter(clock=FakeClock())

    # Act
    for _ in range(100):
        limiter.record_failure("s")

    # Assert
    assert len(limiter._failures["s"]) == JOIN_FAILURE_LIMIT


# --- confirmation codes -------------------------------------------------------

KEY = ("qq:GroupMessage:1", "qq:u1", "space.delete", 7)


def test_confirmation_code_is_four_digits():
    # Act
    codes = [ConfirmationCodes().issue(KEY) for _ in range(100)]

    # Assert
    assert all(len(c) == 4 and c.isdigit() for c in codes)


def test_confirmation_code_is_single_use():
    # Arrange
    confirm = ConfirmationCodes(clock=FakeClock())
    code = confirm.issue(KEY)

    # Act
    first = confirm.consume(KEY, f" {code} ")
    second = confirm.consume(KEY, code)

    # Assert
    assert first is True
    assert second is False


def test_confirmation_code_expires_after_ttl():
    # Arrange
    clock = FakeClock()
    confirm = ConfirmationCodes(clock=clock)
    code = confirm.issue(KEY)
    other_code = confirm.issue("other")

    # Act
    clock.now += CONFIRM_TTL_SECONDS - 0.5
    in_time = confirm.consume("other", other_code)
    clock.now += 0.5
    too_late = confirm.consume(KEY, code)

    # Assert
    assert in_time is True
    assert too_late is False


def test_reissue_replaces_previous_code():
    # Arrange
    confirm = ConfirmationCodes(clock=FakeClock())
    old = confirm.issue(KEY)
    new = confirm.issue(KEY)
    wrong = old if old != new else f"{(int(new) + 1) % 10000:04d}"

    # Act / Assert
    assert confirm.consume(KEY, wrong) is False
    assert confirm.consume(KEY, new) is False  # the failed attempt used it up


def test_reissued_code_is_accepted():
    # Arrange
    confirm = ConfirmationCodes(clock=FakeClock())
    confirm.issue(KEY)
    new = confirm.issue(KEY)

    # Act / Assert
    assert confirm.consume(KEY, new) is True


def test_wrong_code_is_rejected_and_discards_pending_code():
    # Arrange
    confirm = ConfirmationCodes(clock=FakeClock())
    code = confirm.issue(KEY)
    wrong = f"{(int(code) + 1) % 10000:04d}"

    # Act
    attempt = confirm.consume(KEY, wrong)
    retry = confirm.consume(KEY, code)

    # Assert
    assert attempt is False
    assert retry is False


def test_code_is_bound_to_its_key():
    # Arrange
    confirm = ConfirmationCodes(clock=FakeClock())
    code = confirm.issue(KEY)

    # Act / Assert
    assert confirm.consume(("other", "qq:u2", "space.delete", 7), code) is False
    assert confirm.consume(KEY, code) is True


def test_non_ascii_input_is_rejected_without_error():
    # Arrange
    confirm = ConfirmationCodes(clock=FakeClock())
    confirm.issue(KEY)

    # Act / Assert
    assert confirm.consume(KEY, "确认") is False


def test_expired_confirmations_are_pruned_on_issue():
    # Arrange
    clock = FakeClock()
    confirm = ConfirmationCodes(clock=clock)
    for i in range(20):
        confirm.issue(("k", i))

    # Act
    clock.now += CONFIRM_TTL_SECONDS
    confirm.issue(KEY)

    # Assert
    assert set(confirm._pending) == {KEY}
