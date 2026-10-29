"""Text shown in table cells."""

import math

from curvis.data.column import CellValue

_INTEGER_LIMIT = 1e15
"""Whole numbers below this are shown without a decimal point or exponent."""


def number_text(value: float) -> str:
    """Shortest text that reads back as exactly ``value``; "" for missing (NaN)."""
    if math.isnan(value):
        return ""
    if value.is_integer() and abs(value) < _INTEGER_LIMIT:
        return str(int(value))
    return repr(value)


def cell_text(value: CellValue, decimals: int | None = None) -> str:
    """Text for one cell. Numbers are rounded to ``decimals`` places when it is given."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    number = float(value)
    if decimals is not None and not math.isnan(number):
        return f"{number:.{decimals}f}"
    return number_text(number)


def fitted_number_text(value: float, max_chars: int, decimals: int | None = None) -> str:
    """Text for a number in a cell ``max_chars`` digits wide, like a spreadsheet.

    The normal text (see :func:`cell_text`) if it fits; else the number rounded to as
    many significant digits as fit; "###…" if not even one does. The stored value is
    not changed: editing and copying still give it in full.
    """
    text = cell_text(value, decimals)
    if len(text) <= max_chars or not math.isfinite(value):
        return text
    digits = max_chars - 1 - (value < 0)  # room for the decimal point and the sign
    while digits >= 1:
        text = f"{value:.{digits}g}"
        if len(text) <= max_chars:
            return text
        digits -= 1
    return "#" * max(1, max_chars)
