"""Behavioral coverage of strict chat dates and ISO due dates."""

from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

import pytest
from astrbot_plugin_teamtodo.core.dates import parse_due_tokens, parse_iso_due

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 30, 12, 34, 56, tzinfo=SHANGHAI)


@pytest.mark.parametrize(
    ("token", "expected_date"),
    [
        ("2026-11-05", (2026, 11, 5)),
        ("2020-01-02", (2020, 1, 2)),
        ("11-05", (2026, 11, 5)),
        ("10-30", (2026, 10, 30)),
        ("10-29", (2027, 10, 29)),
        ("01-01", (2027, 1, 1)),
        ("今天", (2026, 10, 30)),
        ("明天", (2026, 10, 31)),
        ("后天", (2026, 11, 1)),
        ("周一", (2026, 11, 2)),
        ("周二", (2026, 11, 3)),
        ("周三", (2026, 11, 4)),
        ("周四", (2026, 11, 5)),
        ("周五", (2026, 11, 6)),
        ("周六", (2026, 10, 31)),
        ("周日", (2026, 11, 1)),
        ("周天", (2026, 11, 1)),
        ("星期一", (2026, 11, 2)),
        ("星期二", (2026, 11, 3)),
        ("星期三", (2026, 11, 4)),
        ("星期四", (2026, 11, 5)),
        ("星期五", (2026, 11, 6)),
        ("星期六", (2026, 10, 31)),
        ("星期日", (2026, 11, 1)),
        ("星期天", (2026, 11, 1)),
    ],
)
@pytest.mark.parametrize(
    ("time_token", "hour", "minute", "consumed"),
    [(None, 23, 59, 1), ("18:00", 18, 0, 2), ("0:00", 0, 0, 2)],
)
def test_all_chat_date_forms(token, expected_date, time_token, hour, minute, consumed):
    tokens = [token] if time_token is None else [token, time_token]
    expected = datetime(*expected_date, hour, minute, tzinfo=SHANGHAI)

    result = parse_due_tokens(tokens, NOW)

    assert result == (expected.timestamp(), consumed)
    assert isinstance(result[0], float)


@pytest.mark.parametrize("token", ["今天", "10-30"])
def test_today_is_not_rolled_forward_when_explicit_time_has_passed(token):
    late_now = NOW.replace(hour=23, minute=59, second=59)
    expected = datetime(2026, 10, 30, 8, 0, tzinfo=SHANGHAI).timestamp()

    assert parse_due_tokens([token, "08:00"], late_now) == (expected, 2)


@pytest.mark.parametrize(
    ("token", "expected_date"),
    [
        ("今天", (2026, 12, 31)),
        ("明天", (2027, 1, 1)),
        ("后天", (2027, 1, 2)),
        ("周四", (2027, 1, 7)),
        ("01-01", (2027, 1, 1)),
    ],
)
def test_relative_dates_cross_year_boundary(token, expected_date):
    now = datetime(2026, 12, 31, 15, tzinfo=SHANGHAI)
    expected = datetime(*expected_date, 23, 59, tzinfo=SHANGHAI).timestamp()

    assert parse_due_tokens([token], now) == (expected, 1)


@pytest.mark.parametrize(
    "tokens",
    [
        [],
        [""],
        ["普通标题", "明天"],
        ["18:00"],
        ["0:00", "今天"],
        ["2026-02-29"],
        ["2026-02-30", "18:00"],
        ["2026-13-01"],
        ["2026-00-10"],
        ["2026-11-00"],
        ["0000-01-01"],
        ["02-30"],
        ["04-31"],
        ["13-01"],
        ["00-01"],
        ["11-00"],
        ["2026-1-05"],
        ["2026-11-5"],
        ["1-05"],
        ["11-5"],
        ["20261105"],
        ["2026/11/05"],
        ["２０２６-１１-０５"],
        ["2026-11-05 18:00"],
        ["2026-11-05T18:00"],
        ["今天18:00"],
        ["明天。"],
        [" 今天"],
        ["今天\n"],
        ["下周一"],
        ["周末"],
        ["周七"],
        ["星期八"],
    ],
)
def test_non_dates_and_invalid_dates_are_not_consumed(tokens):
    assert parse_due_tokens(tokens, NOW) == (None, 0)


@pytest.mark.parametrize(
    "time_token",
    [
        "24:00",
        "23:60",
        "-1:00",
        "12:-1",
        "1:2",
        "001:00",
        "12:00:00",
        "12:00Z",
        "１２:００",
        "18:00\n",
        "18:00 ",
        "明天",
        "文字",
        "",
    ],
)
def test_invalid_following_time_is_left_unconsumed(time_token):
    expected = datetime(2026, 11, 5, 23, 59, tzinfo=SHANGHAI).timestamp()

    assert parse_due_tokens(["11-05", time_token], NOW) == (expected, 1)


@pytest.mark.parametrize(
    ("time_token", "hour", "minute"),
    [("00:00", 0, 0), ("9:07", 9, 7), ("09:07", 9, 7), ("23:59", 23, 59)],
)
def test_valid_time_boundaries(time_token, hour, minute):
    expected = datetime(2026, 10, 30, hour, minute, tzinfo=SHANGHAI).timestamp()

    assert parse_due_tokens(["今天", time_token], NOW) == (expected, 2)


def test_only_first_two_tokens_are_examined_and_input_is_unchanged():
    tokens = ["今天", "09:00", "23:00", "明天"]
    original = tokens.copy()
    expected = datetime(2026, 10, 30, 9, 0, tzinfo=SHANGHAI).timestamp()

    assert parse_due_tokens(tokens, NOW) == (expected, 2)
    assert tokens == original
    assert parse_due_tokens(["今天", "文字", "09:00"], NOW)[1] == 1


def test_explicit_leap_date_and_current_year_month_day():
    expected = datetime(2024, 2, 29, 23, 59, tzinfo=SHANGHAI).timestamp()
    leap_now = datetime(2024, 2, 28, 12, tzinfo=SHANGHAI)

    assert parse_due_tokens(["2024-02-29"], NOW) == (expected, 1)
    assert parse_due_tokens(["02-29"], leap_now) == (expected, 1)


@pytest.mark.parametrize(
    "now",
    [
        NOW,
        datetime(2023, 12, 31, tzinfo=SHANGHAI),
        datetime(2024, 3, 1, tzinfo=SHANGHAI),
    ],
)
def test_month_day_does_not_search_for_a_valid_leap_year(now):
    assert parse_due_tokens(["02-29"], now) == (None, 0)


@pytest.mark.parametrize("token", ["明天", "后天", "周一", "01-01"])
def test_unrepresentable_date_is_not_consumed(token):
    now = datetime(9999, 12, 31, 12, tzinfo=SHANGHAI)

    assert parse_due_tokens([token], now) == (None, 0)


class _NoOffset(tzinfo):
    def utcoffset(self, dt):
        return None


@pytest.mark.parametrize(
    "now", [datetime(2026, 10, 30), NOW.replace(tzinfo=_NoOffset())]
)
@pytest.mark.parametrize("tokens", [[], ["今天"], ["not-a-date"]])
def test_chat_parser_requires_aware_now_even_for_non_dates(now, tokens):
    with pytest.raises(ValueError):
        parse_due_tokens(tokens, now)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-05", datetime(2026, 11, 5, 23, 59, tzinfo=SHANGHAI)),
        ("2024-02-29", datetime(2024, 2, 29, 23, 59, tzinfo=SHANGHAI)),
        ("2026-11-05T18:00", datetime(2026, 11, 5, 18, tzinfo=SHANGHAI)),
        ("2026-11-05 18:00:30", datetime(2026, 11, 5, 18, 0, 30, tzinfo=SHANGHAI)),
        (
            "2026-11-05T18:00:30.125",
            datetime(2026, 11, 5, 18, 0, 30, 125000, tzinfo=SHANGHAI),
        ),
        (
            "2026-11-05T18:00:00+08:00",
            datetime(2026, 11, 5, 18, tzinfo=SHANGHAI),
        ),
        (
            "2026-11-05T18:00:00Z",
            datetime(2026, 11, 5, 18, tzinfo=timezone.utc),
        ),
        (
            "2026-11-05T18:00:00-04:30",
            datetime(2026, 11, 5, 18, tzinfo=timezone(-timedelta(hours=4, minutes=30))),
        ),
    ],
)
def test_iso_date_and_datetimes_respect_default_zone_and_explicit_offsets(
    value, expected
):
    result = parse_iso_due(value, NOW)

    assert result == expected.timestamp()
    assert isinstance(result, float)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "明天",
        "18:00",
        "2026-02-29",
        "2026-11-31",
        "2026-13-01",
        "2026-11-05T24:00",
        "2026-11-05T18:60",
        "2026-11-05T18:00:60",
        "2026-11-05T18:00+25:00",
        "2026-11-05T18:00 trailing",
        " 2026-11-05",
        "2026-11-05 ",
        "2026/11/05",
    ],
)
def test_invalid_iso_values_raise_value_error(value):
    with pytest.raises(ValueError):
        parse_iso_due(value, NOW)


@pytest.mark.parametrize(
    "now", [datetime(2026, 10, 30), NOW.replace(tzinfo=_NoOffset())]
)
@pytest.mark.parametrize("value", ["2026-11-05", "2026-11-05T18:00Z", "invalid"])
def test_iso_parser_requires_aware_now_even_with_explicit_offset(now, value):
    with pytest.raises(ValueError):
        parse_iso_due(value, now)
