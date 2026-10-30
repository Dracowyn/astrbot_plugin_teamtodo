"""Pure parsing of ``/todo`` command arguments.

CONTRACT (signatures and behaviour fixed by the main loop):

``main.py`` converts the incoming message chain into ``parts``: a list where
plain text becomes ``str`` and each ``At`` component becomes a ``Mention``
(``At(qq="all")`` and the bot itself are dropped). Order is preserved, so
``+`` / ``-`` (also ``＋`` / ``－``) right before a mention can be detected.

Syntax is documented in ``docs/design.md`` section 4. Tokens are separated
by whitespace. Field tokens may appear anywhere after the task reference:

- date: see ``core/dates.py`` (``无截止`` clears the due date in ``edit``)
- priority: ``!高`` ``!中`` ``!低`` (``!无`` clears it in ``edit``); also
  ASCII ``!high`` ``!medium`` ``!low`` ``!none``, case-insensitive. The
  full-width prefix ``！`` is equivalent to ``!`` for all priority forms.
- tag: ``[标签]`` adds, ``[-标签]`` removes (removal only meaningful in edit).
  Brackets ``【】`` and ``［］`` are equivalent to ``[]``; opening and closing
  forms may be mixed, e.g. ``[标签】`` or ``【-标签］``.
- assignment: ``+@a -@b`` accepts equivalent signs ``＋@a －@b``.
- admin: every target needs a sign; raw IDs are 5–20 ASCII digits and use
  the current platform instance. Raw additions without a matching explicit
  ``+@`` mention are returned separately for membership verification.
- everything else, joined by single spaces, is the title
- a title wrapped in straight or curly double quotes is taken literally
  (its words are not interpreted as fields)

Length and count limits are NOT enforced here (services enforce
``core.models`` limits); syntax errors raise ``TodoError`` with a short
Chinese message.
"""

from __future__ import annotations

import dataclasses as dcs
import re
from datetime import datetime

from .dates import parse_due_tokens
from .models import Mention, Priority, TodoError, UserRef

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
    if not path:
        return parts
    text = _leading_text(parts)
    offset = 0
    while offset < len(text) and not text[offset].isalnum():
        offset += 1
    for word in path:
        match = re.match(r"\s*(\S+)", text[offset:])
        if match is None or match[1].casefold() != word.casefold():
            return parts
        offset += match.end()
    return _cut_prefix(parts, offset)


def text_of(parts: list[Part]) -> str:
    """Concatenate the text parts (mentions dropped), stripped."""
    return "".join(part for part in parts if isinstance(part, str)).strip()


def parse_task_ref(token: str) -> TaskRef | None:
    """Parse ``#3``, ``3``, ``＃3`` or ``空间名#3``; ``None`` if not a ref.

    ``seq`` must be a positive integer.
    """
    match = re.fullmatch(r"(?:(?P<space>[^#＃\s]+)?[#＃])?(?P<seq>[0-9]+)", token)
    if match is None:
        return None
    digits = match["seq"].lstrip("0")
    if not digits:
        return None
    try:
        seq = int(digits)
    except ValueError:
        return None
    return TaskRef(space_name=match["space"], seq=seq)


def split_ref(parts: list[Part]) -> tuple[TaskRef, list[Part]]:
    """Take the task reference from the first text token.

    Raises:
        TodoError: Missing or malformed reference.
    """
    match = re.match(r"\s*(\S+)", _leading_text(parts))
    ref = parse_task_ref(match[1]) if match else None
    if ref is None:
        raise TodoError("请提供有效的任务编号，如 #3")
    return ref, _cut_prefix(parts, match.end())


def parse_task_fields(parts: list[Part], now: datetime) -> TaskFields:
    """Parse field tokens and the title from ``parts``.

    Args:
        parts: Arguments after the command (and after the ref for ``edit``).
        now: Timezone-aware current time for date resolution.

    Raises:
        TodoError: Empty tag ``[]``, conflicting tokens (two dates, two
            priorities), or an unterminated quoted title.
    """
    tokens = _field_tokens(parts)
    title: list[str] = []
    mentions: list[Mention] = []
    due_at = None
    clear_due = False
    priority = None
    clear_priority = False
    add_tags: list[str] = []
    remove_tags: list[str] = []
    has_due = has_priority = False
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if isinstance(token, Mention):
            mentions.append(token)
            continue
        word, quoted = token
        if quoted:
            title.append(" ".join(word.split()))
            continue
        if word == "无截止":
            if has_due:
                raise TodoError("截止日期不能重复")
            has_due = clear_due = True
            continue
        date_tokens = [word]
        if index < len(tokens):
            following = tokens[index]
            if not isinstance(following, Mention) and not following[1]:
                date_tokens.append(following[0])
        parsed_due, consumed = parse_due_tokens(date_tokens, now)
        if consumed:
            if has_due:
                raise TodoError("截止日期不能重复")
            due_at = parsed_due
            has_due = True
            index += consumed - 1
            continue
        priority_token = ("!" + word[1:] if word.startswith("！") else word).casefold()
        if priority_token in _PRIORITIES:
            if has_priority:
                raise TodoError("优先级不能重复")
            priority = _PRIORITIES[priority_token]
            clear_priority = priority is None
            has_priority = True
            continue
        tag = re.fullmatch(r"[\[【［](-?)([^\[\]【】［］]*)[\]】］]", word)
        if tag:
            if not tag[2]:
                raise TodoError("标签不能为空")
            (remove_tags if tag[1] else add_tags).append(tag[2])
            continue
        title.append(word)
    return TaskFields(
        title=" ".join(part for part in title if part),
        mentions=_unique_mentions(mentions),
        due_at=due_at,
        clear_due=clear_due,
        priority=priority,
        clear_priority=clear_priority,
        add_tags=tuple(dict.fromkeys(add_tags)),
        remove_tags=tuple(dict.fromkeys(remove_tags)),
    )


def parse_assign(parts: list[Part]) -> AssignOps:
    """Parse ``@a @b`` (replace) or ``+@a -@b`` (delta) after the task ref.

    A ``+``/``-`` counts when it is the last non-space character of the text
    part directly before the mention.

    Raises:
        TodoError: Any non-whitespace text other than ``+``/``-`` signs.
    """
    signed: list[tuple[str, Mention]] = []
    previous = ""
    for part in parts:
        if isinstance(part, str):
            part = part.replace("＋", "+").replace("－", "-")
            if any(not char.isspace() and char not in "+-" for char in part):
                raise TodoError("负责人参数只能包含 @ 和加减号")
            previous = part.rstrip()
        else:
            sign = previous[-1:] if previous else ""
            signed.append((sign, part))
            previous = ""
    if not any(sign for sign, _ in signed):
        return AssignOps(
            replace=_unique_mentions([mention for _, mention in signed]),
            add=(),
            remove=(),
        )
    return AssignOps(
        replace=None,
        add=_unique_mentions([mention for sign, mention in signed if sign != "-"]),
        remove=_unique_mentions([mention for sign, mention in signed if sign == "-"]),
    )


def parse_admin(
    parts: list[Part], platform_id: str
) -> tuple[AssignOps, frozenset[UserRef]]:
    """Parse signed admin mentions and 5–20 digit ASCII platform user IDs.

    Returns:
        Delta operations and raw additions without an explicit ``+`` mention.

    Raises:
        TodoError: Empty arguments, unsigned targets, or malformed syntax.
    """
    usage = "用法：/todo admin +@成员 -@成员 或 +用户ID -用户ID（5–20 位数字）"
    try:
        tokens = iter(_field_tokens(parts))
    except TodoError:
        raise TodoError(usage) from None
    additions: dict[str, Mention] = {}
    removals: dict[str, Mention] = {}
    raw_additions: dict[str, UserRef] = {}
    mentioned_additions: set[str] = set()
    for token in tokens:
        if isinstance(token, Mention) or token[1]:
            raise TodoError(usage)
        match = re.fullmatch(r"([+＋－-])([0-9]{5,20})?", token[0])
        if match is None:
            raise TodoError(usage)
        target = None
        digits = match[2]
        if digits is None:
            target = next(tokens, None)
            if not isinstance(target, Mention):
                if (
                    target is None
                    or target[1]
                    or not re.fullmatch(r"[0-9]{5,20}", target[0])
                ):
                    raise TodoError(usage)
                digits = target[0]
        mention = (
            target
            if isinstance(target, Mention)
            else Mention(user=UserRef(platform_id, digits), name="")
        )
        key = mention.user.key
        adding = match[1] in ("+", "＋")
        destination = additions if adding else removals
        if isinstance(target, Mention):
            if key not in destination or not destination[key].name:
                destination[key] = mention
            if adding:
                mentioned_additions.add(key)
        else:
            destination.setdefault(key, mention)
            if adding:
                raw_additions[key] = mention.user
    if not additions and not removals:
        raise TodoError(usage)
    return (
        AssignOps(
            replace=None, add=tuple(additions.values()), remove=tuple(removals.values())
        ),
        frozenset(
            user
            for key, user in raw_additions.items()
            if key not in mentioned_additions
        ),
    )


_PRIORITIES = {
    "!高": Priority.HIGH,
    "!high": Priority.HIGH,
    "!中": Priority.MEDIUM,
    "!medium": Priority.MEDIUM,
    "!低": Priority.LOW,
    "!low": Priority.LOW,
    "!无": None,
    "!none": None,
}


def _leading_text(parts: list[Part]) -> str:
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, Mention):
            break
        chunks.append(part)
    return "".join(chunks)


def _cut_prefix(parts: list[Part], count: int) -> list[Part]:
    for index, part in enumerate(parts):
        if isinstance(part, Mention):
            return parts[index:]
        if count >= len(part):
            count -= len(part)
            continue
        rest = part[count:].lstrip()
        return ([rest] if rest else []) + parts[index + 1 :]
    return []


def _unique_mentions(mentions: list[Mention]) -> tuple[Mention, ...]:
    users: dict[str, Mention] = {}
    for mention in mentions:
        users.setdefault(mention.user.key, mention)
    return tuple(users.values())


def _field_tokens(parts: list[Part]) -> list[Mention | tuple[str, bool]]:
    tokens: list[Mention | tuple[str, bool]] = []
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, str):
            chunks.append(part)
            continue
        tokens.extend(_text_tokens("".join(chunks)))
        chunks.clear()
        tokens.append(part)
    tokens.extend(_text_tokens("".join(chunks)))
    return tokens


def _text_tokens(text: str) -> list[tuple[str, bool]]:
    tokens: list[tuple[str, bool]] = []
    position = 0
    while position < len(text):
        char = text[position]
        if char.isspace():
            position += 1
            continue
        if char in '"“':
            closing = '"' if char == '"' else "”"
            end = text.find(closing, position + 1)
            if end < 0:
                raise TodoError("标题引号未闭合")
            tokens.append((text[position + 1 : end], True))
            position = end + 1
            continue
        end = position + 1
        while end < len(text) and not text[end].isspace() and text[end] not in '"“':
            end += 1
        tokens.append((text[position:end], False))
        position = end
    return tokens
