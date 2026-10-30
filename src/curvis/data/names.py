"""Name comparison for tables and columns."""

from collections.abc import Iterable


def name_key(name: str) -> str:
    """Key used to compare names: case-insensitive, so 'Dose' and 'dose' are the same name."""
    return name.casefold()


def column_letters(index: int) -> str:
    """Spreadsheet-style name for a 0-based position: A, B, …, Z, AA, AB, …"""
    if index < 0:
        raise ValueError("index cannot be negative")
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters


def unique_name(name: str, taken: Iterable[str]) -> str:
    """``name``, or ``name (2)``, ``name (3)``, … if it is in ``taken`` (ignoring case)."""
    used = {name_key(t) for t in taken}
    candidate, n = name, 1
    while name_key(candidate) in used:
        n += 1
        candidate = f"{name} ({n})"
    return candidate


def next_column_names(taken: Iterable[str], count: int = 1) -> list[str]:
    """The first ``count`` names A, B, C, … that are not in ``taken`` (ignoring case)."""
    used = {name_key(name) for name in taken}
    names: list[str] = []
    index = 0
    while len(names) < count:
        candidate = column_letters(index)
        if name_key(candidate) not in used:
            names.append(candidate)
            used.add(name_key(candidate))
        index += 1
    return names
