"""New names for bulk rename: find and replace, and numbered patterns."""

import re
from collections.abc import Sequence

NUMBER_MARK = "#"
"""Placeholder for the number in a numbered pattern; ``##`` pads to two digits, and so on."""

_MARKS = re.compile(f"{re.escape(NUMBER_MARK)}+")


def find_replace(name: str, find: str, replace: str, *, match_case: bool = False) -> str:
    """``name`` with every occurrence of ``find`` replaced (plain text, not a pattern)."""
    if not find:
        return name
    flags = 0 if match_case else re.IGNORECASE
    return re.sub(re.escape(find), lambda _m: replace, name, flags=flags)


def numbered_name(pattern: str, number: int) -> str:
    """``pattern`` with each run of ``#`` replaced by ``number``, zero-padded to its length.

    A pattern without ``#`` gets the number after a space: "Rep" gives "Rep 1".
    """
    if NUMBER_MARK not in pattern:
        return f"{pattern} {number}" if pattern else str(number)
    return _MARKS.sub(lambda m: f"{number:0{len(m.group())}d}", pattern)


def numbered_names(pattern: str, count: int, start: int = 1, step: int = 1) -> list[str]:
    """``count`` names from ``pattern``, numbered ``start``, ``start + step``, …"""
    return [numbered_name(pattern, start + i * step) for i in range(count)]


def renamed(
    names: Sequence[str],
    *,
    find: str | None = None,
    replace: str = "",
    match_case: bool = False,
    pattern: str | None = None,
    start: int = 1,
    step: int = 1,
) -> list[str]:
    """New names for ``names``: find and replace if ``find`` is given, else numbered."""
    if find is not None:
        return [find_replace(n, find, replace, match_case=match_case) for n in names]
    if pattern is not None:
        return numbered_names(pattern, len(names), start, step)
    return list(names)
