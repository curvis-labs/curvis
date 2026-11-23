"""The decimal mark of imported text: detect it, and read numbers with it.

Auto-detect only counts cells that settle the question:

- "1,5", "0,123", "12,3456" or ",5" can only have a decimal comma, and "1.5" (and so
  on) only a decimal point;
- "1.234,5" and "1.234.567" use a point to group thousands, so the decimal mark is a
  comma; "1,234.5" and "1,234,567" are the other way around.

A cell such as "1,234" or "1.234" (one to three digits, the mark, then exactly three
digits) could be either a decimal mark or a thousands separator, so it does not count.
When no cell settles it, or cells point both ways, the default (from Settings) is used
and the detection is *unsure*, which the import preview says, but only when some cell
was ambiguous or conflicting: for whole numbers the mark does not matter.

Reading numbers with a mark also accepts thousands grouped with the other character
("1.234,5" with a comma, "1,234.5" with a point). Nothing here uses Qt.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import polars as pl

_NUMBER = re.compile(r"[+-]?(?P<int>\d*)(?P<mark>[.,])(?P<frac>\d+)(?:[eE][+-]?\d+)?")
_GROUPED_POINT = re.compile(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d*)?")
"""Thousands grouped with commas, optionally a decimal point: "1,234.5"."""
_GROUPED_COMMA = re.compile(r"[+-]?\d{1,3}(?:\.\d{3})+(?:,\d*)?")
"""Thousands grouped with points, optionally a decimal comma: "1.234,5"."""

GROUPED_POINT_PATTERN = r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d*)?$"
GROUPED_COMMA_PATTERN = r"^[+-]?\d{1,3}(?:\.\d{3})+(?:,\d*)?$"


@dataclass(frozen=True)
class DecimalGuess:
    """What auto-detect found: the mark to use, and whether the data settled it."""

    comma: bool
    unsure: bool


def classify(cell: str) -> str | None:
    """ "comma" or "point" when ``cell`` can only be read one way, "ambiguous" when it
    could be either ("1,234"), and None when it says nothing (text, whole numbers)."""
    text = cell.strip()
    if not text or len(text) > 64:
        return None
    if "," in text and "." in text:
        if _GROUPED_POINT.fullmatch(text) and text.rfind(".") > text.rfind(","):
            return "point"
        if _GROUPED_COMMA.fullmatch(text) and text.rfind(",") > text.rfind("."):
            return "comma"
        return None
    if text.count(",") > 1:
        return "point" if _GROUPED_POINT.fullmatch(text) else None
    if text.count(".") > 1:
        return "comma" if _GROUPED_COMMA.fullmatch(text) else None
    match = _NUMBER.fullmatch(text)
    if match is None:
        return None
    mark = "comma" if match["mark"] == "," else "point"
    whole, fraction = match["int"], match["frac"]
    exponent = match.end("frac") != len(text)
    could_group = 1 <= len(whole) <= 3 and not whole.startswith("0") and len(fraction) == 3
    if could_group and not exponent:
        return "ambiguous"
    return mark


def detect(cells: Iterable[str | None], default_comma: bool) -> DecimalGuess:
    """The decimal mark of ``cells`` (see the module docstring)."""
    comma = point = ambiguous = 0
    for cell in cells:
        if cell is None:
            continue
        kind = classify(cell)
        if kind == "comma":
            comma += 1
        elif kind == "point":
            point += 1
        elif kind == "ambiguous":
            ambiguous += 1
    if comma and not point:
        return DecimalGuess(True, False)
    if point and not comma:
        return DecimalGuess(False, False)
    return DecimalGuess(default_comma, bool(ambiguous or (comma and point)))


def parse_numbers(cells: pl.Series, decimal_comma: bool) -> pl.Series:
    """Numbers (Float64, null where a cell is not one) from trimmed text ``cells``.

    Thousands grouped with the other character are only looked for when a plain read
    leaves cells over, so the common case costs one pass.
    """
    import polars as pl

    thousands, pattern = (
        (".", GROUPED_COMMA_PATTERN) if decimal_comma else (",", GROUPED_POINT_PATTERN)
    )
    text = cells.str.replace_all(",", ".", literal=True) if decimal_comma else cells
    numbers = text.cast(pl.Float64, strict=False)
    if numbers.null_count() == cells.null_count():
        return numbers
    if not bool(cells.str.contains(thousands, literal=True).any()):
        return numbers
    grouped = cells.str.contains(pattern)
    ungrouped = pl.select(
        pl.when(grouped).then(cells.str.replace_all(thousands, "", literal=True)).otherwise(cells)
    ).to_series()
    if decimal_comma:
        ungrouped = ungrouped.str.replace_all(",", ".", literal=True)
    return ungrouped.cast(pl.Float64, strict=False)
