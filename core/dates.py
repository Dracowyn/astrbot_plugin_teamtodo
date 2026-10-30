"""Strict due-date token parsing for chat commands.

CONTRACT (signatures and behaviour fixed by the main loop):

Accepted tokens (the whole token must match; anything else -> ``None``):

=================  ==========================================================
``2026-11-05``     that date, 23:59
``2026-11-05 18:00`` not a single token; see ``parse_due_tokens``
``11-05``          this year's Nov 5, or next year's if that date is before
                   today
``今天/明天/后天``   today / +1 / +2 days, 23:59
``周一`` .. ``周日``  the NEXT such weekday, strictly after today (today being
                   Wednesday, ``周三`` means next week's Wednesday); also
                   accept ``星期一`` .. ``星期日`` and ``周天``/``星期天``
=================  ==========================================================

A following ``HH:MM`` token (24h, ``0:00``..``23:59``) sets the time instead
of 23:59. A bare ``HH:MM`` without a date token is not a due date.
Invalid calendar dates (``02-30``) -> ``None``.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

_FULL_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_MONTH_DAY = re.compile(r"[0-9]{2}-[0-9]{2}")
_TIME = re.compile(r"(?:[01]?[0-9]|2[0-3]):[0-5][0-9]")
_RELATIVE_DAYS = {"今天": 0, "明天": 1, "后天": 2}
_WEEKDAYS = {
    prefix + suffix: index
    for prefix in ("周", "星期")
    for index, suffix in enumerate("一二三四五六日")
}
_WEEKDAYS.update({"周天": 6, "星期天": 6})


def _require_aware(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必须包含时区")


def _parse_date(token: str, today: date) -> date | None:
    if _FULL_DATE.fullmatch(token):
        return date.fromisoformat(token)
    if _MONTH_DAY.fullmatch(token):
        month, day = map(int, token.split("-"))
        result = date(today.year, month, day)
        if result < today:
            result = date(today.year + 1, month, day)
        return result
    if token in _RELATIVE_DAYS:
        return today + timedelta(days=_RELATIVE_DAYS[token])
    if token in _WEEKDAYS:
        days = (_WEEKDAYS[token] - today.weekday()) % 7 or 7
        return today + timedelta(days=days)
    return None


def parse_due_tokens(tokens: list[str], now: datetime) -> tuple[float | None, int]:
    """Parse a due date at the start of ``tokens``.

    Args:
        tokens: Whitespace-split words; only the first one or two are examined.
        now: Current time, timezone-aware, in the configured zone. The result
            is interpreted in ``now.tzinfo``.

    Returns:
        ``(unix_seconds, consumed)`` where ``consumed`` is 0 (no date), 1
        (date only) or 2 (date + time). ``unix_seconds`` is ``None`` iff
        ``consumed == 0``.

    Raises:
        ValueError: If ``now`` is naive.
    """
    _require_aware(now)
    if not tokens:
        return None, 0
    try:
        due_date = _parse_date(tokens[0], now.date())
    except (ValueError, OverflowError):
        return None, 0
    if due_date is None:
        return None, 0

    due_time = time(23, 59)
    consumed = 1
    if len(tokens) > 1 and _TIME.fullmatch(tokens[1]):
        hour, minute = map(int, tokens[1].split(":"))
        due_time = time(hour, minute)
        consumed = 2
    return datetime.combine(due_date, due_time, tzinfo=now.tzinfo).timestamp(), consumed


def parse_iso_due(value: str, now: datetime) -> float:
    """Parse an ISO 8601 date or datetime supplied by the LLM tool (M2).

    A date without time means 23:59; a datetime without offset is taken in
    ``now.tzinfo``.

    Raises:
        ValueError: Unparseable value or naive ``now``.
    """
    _require_aware(now)
    try:
        due_date = date.fromisoformat(value)
    except ValueError:
        due = datetime.fromisoformat(value)
        if due.tzinfo is None:
            due = due.replace(tzinfo=now.tzinfo)
    else:
        due = datetime.combine(due_date, time(23, 59), tzinfo=now.tzinfo)
    return due.timestamp()
