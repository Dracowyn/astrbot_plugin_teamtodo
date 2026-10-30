"""Behavioral coverage for command parsing without AstrBot dependencies."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from astrbot_plugin_teamtodo.core import parsing
from astrbot_plugin_teamtodo.core.models import Mention, Priority, TodoError, UserRef
from astrbot_plugin_teamtodo.core.parsing import (
    parse_assign,
    parse_task_fields,
    parse_task_ref,
    split_ref,
    strip_command,
    text_of,
)

NOW = datetime(2026, 10, 30, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
A = Mention(user=UserRef(platform_id="qq", user_id="1"), name="甲")
B = Mention(user=UserRef(platform_id="qq", user_id="2"), name="乙")


@pytest.mark.parametrize("prefix", ["", "/", " /", "!?_", "\t／"])
def test_strip_command_accepts_wake_prefix_and_case_insensitive_words(prefix):
    parts = ["  ", f"{prefix}ToDo ", "SPACE new 名字 ", A]

    remaining = strip_command(parts, ("todo", "space", "new"))

    assert remaining == ["名字 ", A]
    assert parts[-2] == "SPACE new 名字 "


@pytest.mark.parametrize(
    "parts",
    [["todo added 标题"], [A, "todo add"], ["其它 todo add"], ["todo", A, " add"]],
)
def test_strip_command_returns_input_when_command_is_not_at_start(parts):
    assert strip_command(parts, ("todo", "add")) is parts


def test_strip_command_exhaustion_and_empty_path():
    assert strip_command(["/todo", " add  "], ("todo", "add")) == []
    assert strip_command(["/todo add", A], ("todo", "add")) == [A]
    parts = ["/todo add x"]
    assert strip_command(parts, ()) is parts
    assert strip_command([], ("todo",)) == []


def test_text_of_concatenates_without_inventing_whitespace():
    assert text_of(["  原", A, "文  "]) == "原文"


@pytest.mark.parametrize(
    "token,space,seq",
    [
        ("#3", None, 3),
        ("3", None, 3),
        ("＃3", None, 3),
        ("空间名#3", "空间名", 3),
        ("项目＃12", "项目", 12),
    ],
)
def test_task_ref_parses_local_and_cross_space_forms(token, space, seq):
    ref = parse_task_ref(token)
    assert ref.space_name == space
    assert ref.seq == seq


@pytest.mark.parametrize(
    "token", ["", "#0", "0", "#x", "#-1", "#3x", "a#2#3", "a b#3", " #3", "3 "]
)
def test_task_ref_rejects_nonpositive_or_malformed_tokens(token):
    assert parse_task_ref(token) is None


def test_split_ref_preserves_mentions_and_remaining_text():
    ref, rest = split_ref([" ", "项目#3  新标题 ", A])
    assert (ref.space_name, ref.seq) == ("项目", 3)
    assert rest == ["新标题 ", A]
    assert split_ref(["#1", B])[1] == [B]
    assert split_ref(["#1"])[1] == []


@pytest.mark.parametrize("parts", [[], [" "], [A], ["#x x"], ["#0"]])
def test_split_ref_reports_missing_or_invalid_reference(parts):
    with pytest.raises(TodoError):
        split_ref(parts)


@pytest.mark.parametrize(
    "text",
    [
        "标题 明天 18:30 !high [前端] [-旧]",
        "!HIGH [前端] 标题 [-旧] 明天 18:30",
        "明天 18:30 [-旧] !高 标题 [前端]",
    ],
)
def test_fields_are_order_independent_with_date_time_and_mentions(text):
    fields = parse_task_fields([text, A, B, A], NOW)
    assert fields.title == "标题"
    assert fields.mentions == (A, B)
    assert (
        fields.due_at == datetime(2026, 10, 31, 18, 30, tzinfo=NOW.tzinfo).timestamp()
    )
    assert fields.priority == Priority.HIGH
    assert fields.add_tags == ("前端",)
    assert fields.remove_tags == ("旧",)
    assert not fields.clear_due
    assert not fields.clear_priority


@pytest.mark.parametrize("quotes", [('"', '"'), ("“", "”")])
def test_quoted_title_protects_dates_priorities_tags_and_normalizes_whitespace(quotes):
    opening, closing = quotes
    fields = parse_task_fields([f"{opening}明天  !高 [标签]\n原文{closing} 后天"], NOW)
    assert fields.title == "明天 !高 [标签] 原文"
    assert fields.due_at == datetime(2026, 11, 1, 23, 59, tzinfo=NOW.tzinfo).timestamp()
    assert fields.priority is None
    assert fields.add_tags == ()


def test_quotes_and_unquoted_title_can_span_text_components():
    fields = parse_task_fields(['"明', '天" 其它', "文字"], NOW)
    assert fields.title == "明天 其它文字"
    assert fields.due_at is None


@pytest.mark.parametrize(
    "priority,value",
    [
        ("!中", Priority.MEDIUM),
        ("!低", Priority.LOW),
        ("!MeDiuM", Priority.MEDIUM),
        ("!low", Priority.LOW),
        ("!none", None),
        ("!无", None),
    ],
)
def test_priority_aliases_and_explicit_clear(priority, value):
    fields = parse_task_fields([f"无截止 {priority}"], NOW)
    assert fields.title == ""
    assert fields.clear_due and fields.due_at is None
    assert fields.priority == value
    assert fields.clear_priority is (value is None)


@pytest.mark.parametrize(
    "text",
    [
        "今天 明天",
        "今天 无截止",
        "无截止 今天",
        "无截止 无截止",
        "!高 !低",
        "!高 !无",
        "!无 !高",
        "[]",
        "[-]",
        '"未关闭',
        "“未关闭",
    ],
)
def test_conflicting_or_malformed_fields_raise_user_facing_error(text):
    with pytest.raises(TodoError) as error:
        parse_task_fields([text], NOW)
    assert any("\u4e00" <= char <= "\u9fff" for char in str(error.value))


def test_unknown_tokens_are_literal_title_and_duplicate_tags_are_removed_in_order():
    fields = parse_task_fields(
        ["说明 !urgent 02-30 18:00 [甲] [乙] [甲] [-旧] [-旧]"], NOW
    )
    assert fields.title == "说明 !urgent 02-30 18:00"
    assert fields.add_tags == ("甲", "乙")
    assert fields.remove_tags == ("旧",)


def test_quoted_time_does_not_override_a_date_and_mention_keeps_tokens_separate():
    fields = parse_task_fields(['今天 "18:00"', A, "明天的计划"], NOW)
    assert (
        fields.due_at == datetime(2026, 10, 30, 23, 59, tzinfo=NOW.tzinfo).timestamp()
    )
    assert fields.title == "18:00 明天的计划"
    assert parse_task_fields([], NOW).mentions == ()


def test_assign_replaces_deduplicated_mentions_or_clears_all():
    ops = parse_assign([" ", A, B, A])
    assert ops.replace == (A, B)
    assert ops.add == ops.remove == ()
    assert parse_assign([]).replace == ()


def test_assign_delta_counts_unprefixed_as_add_and_deduplicates_each_direction():
    ops = parse_assign([A, " + ", B, "-", A, "+", B])
    assert ops.replace is None
    assert ops.add == (A, B)
    assert ops.remove == (A,)


@pytest.mark.parametrize("parts", [["文字", A], ["+text", A], [A, "意外文本"]])
def test_assign_rejects_stray_text(parts):
    with pytest.raises(TodoError):
        parse_assign(parts)


def test_assign_sign_applies_only_from_directly_preceding_text_part():
    ops = parse_assign(["-", A, B])
    assert ops.remove == (A,)
    assert ops.add == (B,)


@pytest.mark.parametrize("prefix", ["!", "！"])
@pytest.mark.parametrize(
    "suffix,expected",
    [
        ("高", Priority.HIGH),
        ("中", Priority.MEDIUM),
        ("低", Priority.LOW),
        ("无", None),
        ("high", Priority.HIGH),
        ("MeDiuM", Priority.MEDIUM),
        ("LOW", Priority.LOW),
        ("NoNe", None),
    ],
)
def test_priority_prefix_accepts_ascii_and_full_width(prefix, suffix, expected):
    fields = parse_task_fields([f"标题 {prefix}{suffix}"], NOW)

    assert fields.title == "标题"
    assert fields.priority == expected
    assert fields.clear_priority is (expected is None)


@pytest.mark.parametrize("opening", ["[", "【", "［"])
@pytest.mark.parametrize("closing", ["]", "】", "］"])
def test_tag_bracket_forms_can_mix_for_addition_and_removal(opening, closing):
    fields = parse_task_fields(
        [f"标题 {opening}标签{closing} {opening}-旧标签{closing}"], NOW
    )

    assert fields.title == "标题"
    assert fields.add_tags == ("标签",)
    assert fields.remove_tags == ("旧标签",)


@pytest.mark.parametrize("opening", ["[", "【", "［"])
@pytest.mark.parametrize("closing", ["]", "】", "］"])
@pytest.mark.parametrize("content", ["", "-"])
def test_empty_tags_raise_for_every_bracket_pair(opening, closing, content):
    with pytest.raises(TodoError):
        parse_task_fields([f"{opening}{content}{closing}"], NOW)


@pytest.mark.parametrize("text", ["!高 ！低", "！无 !高", "！high ！none"])
def test_priority_conflicts_include_full_width_prefix(text):
    with pytest.raises(TodoError):
        parse_task_fields([text], NOW)


def test_quoted_and_unknown_full_width_tokens_remain_literal():
    fields = parse_task_fields(["“！高 【标签】 ［-旧］” ！urgent"], NOW)

    assert fields.title == "！高 【标签】 ［-旧］ ！urgent"
    assert fields.priority is None
    assert not fields.clear_priority
    assert fields.add_tags == fields.remove_tags == ()


@pytest.mark.parametrize("plus", ["+", "＋"])
@pytest.mark.parametrize("minus", ["-", "－"])
def test_assign_accepts_mixed_ascii_and_full_width_delta_signs(plus, minus):
    ops = parse_assign([f" {plus} ", A, f"{minus}\t", B, A])

    assert ops.replace is None
    assert ops.add == (A,)
    assert ops.remove == (B,)


def test_full_width_assign_sign_applies_only_to_the_next_mention():
    ops = parse_assign(["－", A, B])

    assert ops.replace is None
    assert ops.remove == (A,)
    assert ops.add == (B,)


@pytest.mark.parametrize("text", ["＋text", "－文字"])
def test_full_width_assign_sign_does_not_allow_stray_text(text):
    with pytest.raises(TodoError):
        parse_assign([text, A])


@pytest.mark.parametrize("sign", ["+", "＋", "-", "－"])
@pytest.mark.parametrize("digits", ["12345", "12345678901234567890"])
@pytest.mark.parametrize("spacing", ["", " ", "\t\n"])
def test_admin_accepts_signed_ascii_ids_with_boundary_lengths(sign, digits, spacing):
    ops, raw_additions = parsing.parse_admin([f"{sign}{spacing}{digits}"], "qq-main")
    mention = Mention(user=UserRef("qq-main", digits), name="")

    assert ops.replace is None
    assert ops.add == ((mention,) if sign in ("+", "＋") else ())
    assert ops.remove == ((mention,) if sign in ("-", "－") else ())
    assert raw_additions == (frozenset({mention.user}) if ops.add else frozenset())


@pytest.mark.parametrize(
    "text",
    [
        "+1234",
        "-1234",
        "+123456789012345678901",
        "－123456789012345678901",
        "+１２３４５",
        "+١٢٣٤٥",
        "+1234５",
        "12345",
        "12345678901234567890",
        "+12345 67890",
        "+12345 text",
        "+12345 -",
        "+",
        "－",
        "++12345",
        "+-12345",
        "+12 345",
        "@12345",
        '"+12345"',
        '"unfinished',
    ],
)
def test_admin_rejects_invalid_ids_unsigned_targets_and_stray_text(text):
    with pytest.raises(TodoError, match="用法"):
        parsing.parse_admin([text], "qq-main")


@pytest.mark.parametrize("parts", [[], [" \t"], [A], ["+", A, B], ["+", A, "+"]])
def test_admin_requires_nonempty_targets_and_a_sign_for_every_mention(parts):
    with pytest.raises(TodoError, match="用法"):
        parsing.parse_admin(parts, "qq")


def test_admin_mixes_raw_ids_and_mentions_with_split_text_components():
    ops, raw_additions = parsing.parse_admin(
        ["+12", "345 -", " 67890 ＋", "\t", A, " － ", B], "qq-main"
    )
    added = Mention(user=UserRef("qq-main", "12345"), name="")
    removed = Mention(user=UserRef("qq-main", "67890"), name="")

    assert ops.replace is None
    assert ops.add == (added, A)
    assert ops.remove == (removed, B)
    assert raw_additions == frozenset({added.user})


@pytest.mark.parametrize("mention_first", [False, True])
def test_admin_plus_mention_proves_duplicate_raw_addition_and_preserves_name(
    mention_first,
):
    mention = Mention(user=UserRef("qq", "12345"), name="甲")
    parts = ["+", mention, " +12345"] if mention_first else ["+12345 +", mention]

    ops, raw_additions = parsing.parse_admin(parts, "qq")

    assert ops.add == (mention,)
    assert ops.remove == ()
    assert raw_additions == frozenset()


def test_admin_removal_mention_does_not_prove_raw_addition():
    mention = Mention(user=UserRef("qq", "12345"), name="甲")

    ops, raw_additions = parsing.parse_admin(["+12345 -", mention], "qq")

    assert tuple(item.user for item in ops.add) == (mention.user,)
    assert ops.remove == (mention,)
    assert raw_additions == frozenset({mention.user})


def test_admin_deduplicates_both_directions_and_prefers_explicit_removal_name():
    mention = Mention(user=UserRef("qq", "12345"), name="甲")

    ops, raw_additions = parsing.parse_admin(
        ["+67890 +67890 -12345 -", mention, " -12345"], "qq"
    )

    assert ops.add == (Mention(user=UserRef("qq", "67890"), name=""),)
    assert ops.remove == (mention,)
    assert raw_additions == frozenset({UserRef("qq", "67890")})


def test_admin_duplicate_proof_is_scoped_to_the_platform_instance():
    other_platform = Mention(user=UserRef("qq-other", "12345"), name="甲")

    ops, raw_additions = parsing.parse_admin(["+12345 +", other_platform], "qq-main")

    assert ops.add == (
        Mention(user=UserRef("qq-main", "12345"), name=""),
        other_platform,
    )
    assert raw_additions == frozenset({UserRef("qq-main", "12345")})


@pytest.mark.parametrize("text", ["12345", "+12345", "－12345678901234567890"])
def test_task_assign_still_rejects_raw_ids(text):
    with pytest.raises(TodoError):
        parse_assign([text])
