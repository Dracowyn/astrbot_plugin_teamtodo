"""Pure parsing of ``/todo`` command arguments.

CONTRACT (signatures and behaviour fixed by the main loop):

``main.py`` converts the incoming message chain into ``parts``: a list where
plain text becomes ``str`` and each ``At`` component becomes a ``Mention``
(``At(qq="all")`` and the bot itself are dropped). Order is preserved, so
``+`` / ``-`` written right before a mention can be detected.

Syntax is documented in ``docs/design.md`` section 4. Tokens are separated
by whitespace. Field tokens may appear anywhere after the task reference:

- date: see ``core/dates.py`` (``无截止`` clears the due date in ``edit``)
- priority: ``!高`` ``!中`` ``!低`` (``!无`` clears it in ``edit``); also
  ASCII ``!high`` ``!medium`` ``!low`` ``!none``, case-insensitive
- tag: ``[标签]`` adds, ``[-标签]`` removes (removal only meaningful in edit)
- everything else, joined by single spaces, is the title
- a title wrapped in straight or curly double quotes is taken literally
  (its words are not interpreted as fields)

Length and count limits are NOT enforced here (services enforce
``core.models`` limits); syntax errors raise ``TodoError`` with a short
Chinese message.
"""

from __future__ import annotations

import dataclasses as dcs
from datetime import datetime

from .models import Mention, Priority

Part = str | Mention


@dcs.dataclass(frozen=True)
class TaskRef:
    """``#3`` / ``3`` -> (None, 3); ``空间名#3`` -> ("空间名", 3)."""

    space_name: str | None
    seq: int


@dcs.dataclass(frozen=True)
class TaskFields:
    """Fields parsed from ``add`` or ``edit`` arguments."""

    title: str
    mentions: tuple[Mention, ...]
    """Mentions in order, de-duplicated by user key (``add`` assigns them)."""
    due_at: float | None
    clear_due: bool
    priority: Priority | None
    clear_priority: bool
    add_tags: tuple[str, ...]
    remove_tags: tuple[str, ...]


@dcs.dataclass(frozen=True)
class AssignOps:
    """Result of ``assign`` arguments.

    If no mention is prefixed with ``+`` or ``-``, ``replace`` holds all
    mentions (possibly empty = clear all assignees) and ``add``/``remove``
    are empty. Otherwise ``replace`` is ``None``; an unprefixed mention mixed
    with prefixed ones counts as ``add``.
    """

    replace: tuple[Mention, ...] | None
    add: tuple[Mention, ...]
    remove: tuple[Mention, ...]


def strip_command(parts: list[Part], path: tuple[str, ...]) -> list[Part]:
    """Drop the leading command words from ``parts``.

    The first text part may start with a wake prefix (any run of
    non-alphanumeric, non-CJK characters such as ``/``). ``path`` is e.g.
    ``("todo", "add")`` or ``("todo", "space", "new")``; words compare
    case-insensitively. Leading whitespace-only text parts are skipped.

    Returns:
        The remaining parts (text parts trimmed at the cut point). If the
        command words are not found at the start, ``parts`` is returned
        unchanged.
    """
    raise NotImplementedError


def text_of(parts: list[Part]) -> str:
    """Concatenate the text parts (mentions dropped), stripped."""
    raise NotImplementedError


def parse_task_ref(token: str) -> TaskRef | None:
    """Parse ``#3``, ``3``, ``＃3`` or ``空间名#3``; ``None`` if not a ref.

    ``seq`` must be a positive integer.
    """
    raise NotImplementedError


def split_ref(parts: list[Part]) -> tuple[TaskRef, list[Part]]:
    """Take the task reference from the first text token.

    Raises:
        TodoError: Missing or malformed reference.
    """
    raise NotImplementedError


def parse_task_fields(parts: list[Part], now: datetime) -> TaskFields:
    """Parse field tokens and the title from ``parts``.

    Args:
        parts: Arguments after the command (and after the ref for ``edit``).
        now: Timezone-aware current time for date resolution.

    Raises:
        TodoError: Empty tag ``[]``, conflicting tokens (two dates, two
            priorities), or an unterminated quoted title.
    """
    raise NotImplementedError


def parse_assign(parts: list[Part]) -> AssignOps:
    """Parse ``@a @b`` (replace) or ``+@a -@b`` (delta) after the task ref.

    A ``+``/``-`` counts when it is the last non-space character of the text
    part directly before the mention.

    Raises:
        TodoError: Any non-whitespace text other than ``+``/``-`` signs.
    """
    raise NotImplementedError
