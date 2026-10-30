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

from datetime import datetime


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
    raise NotImplementedError


def parse_iso_due(value: str, now: datetime) -> float:
    """Parse an ISO 8601 date or datetime supplied by the LLM tool (M2).

    A date without time means 23:59; a datetime without offset is taken in
    ``now.tzinfo``.

    Raises:
        ValueError: Unparseable value or naive ``now``.
    """
    raise NotImplementedError
