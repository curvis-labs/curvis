"""The result of an analysis: what the Results panel shows and Ctrl+C copies.

A :class:`Result` holds the exact method name, a one-line plain-language reading,
assumption warnings, named values and tables (e.g. post-hoc comparisons). Values are
stored as numbers; they are turned into text only for display and the clipboard
(:func:`shown`), and at full precision for tooltips (:func:`full_text`).

A P value gets significance stars next to it: * P < 0.05, ** P < 0.01, *** P < 0.001.
Results are frozen dataclasses of plain values, so they pickle across processes.

How P values are shown follows the Settings (:class:`PDisplay`): "P < 0.001" (the
default), "P < 0.0001", or exact (2.3 × 10⁻⁷). It changes only the text: results keep
full precision and the stars do not change. A reading names its P values with a marker
(:func:`p_marker`) that :func:`render_p` turns into text when it is shown, so a change of
the setting needs no new run.
"""

from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass
from enum import StrEnum

type Cell = float | int | str | None
"""One value of a result: a number, a whole number, text, or missing (None or NaN)."""

STAR_THRESHOLDS: tuple[tuple[float, str], ...] = ((0.001, "***"), (0.01, "**"), (0.05, "*"))
"""(threshold, stars): a P value below the threshold gets the stars; the first match wins."""

STARS_FOOTNOTE = "* P < 0.05, ** P < 0.01, *** P < 0.001."

SIGNIFICANT_DIGITS = 4
"""Significant digits of numbers shown in results."""

P_SIGNIFICANT_DIGITS = 3
P_FLOOR = 0.001
"""P values below this are shown as "< 0.001" (the default display)."""
P_EXACT_DIGITS = 2
"""Significant digits of an exact P value below :data:`P_FLOOR` (2.3 × 10⁻⁷)."""


class PDisplay(StrEnum):
    """How P values are shown (Settings, Results)."""

    BELOW_0001 = "0.001"
    """ "P < 0.001" for smaller values (the default)."""
    BELOW_00001 = "0.0001"
    """ "P < 0.0001" for smaller values."""
    EXACT = "exact"
    """Every value, e.g. 2.3 × 10⁻⁷."""

    @property
    def label(self) -> str:
        return {
            "0.001": "P < 0.001",
            "0.0001": "P < 0.0001",
            "exact": "Exact (e.g. 2.3 × 10⁻⁷)",
        }[self.value]


DEFAULT_P_DISPLAY = PDisplay.BELOW_0001
_SUPERSCRIPT = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")


class Kind(StrEnum):
    """How a value is shown."""

    TEXT = "text"
    NUMBER = "number"
    INTEGER = "integer"
    P_VALUE = "p_value"
    LABEL = "label"
    """A number that names a row, such as an X value: shown as typed (up to 6 digits)."""


def stars(p: float | None) -> str:
    """Significance stars for a P value ("" when not significant or missing)."""
    if p is None or math.isnan(p):
        return ""
    for threshold, mark in STAR_THRESHOLDS:
        if p < threshold:
            return mark
    return ""


def _is_missing(value: Cell) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _p_of(value: Cell) -> float | None:
    """``value`` as a P value, or None if it is missing or text."""
    if _is_missing(value) or isinstance(value, str) or value is None:
        return None
    return float(value)


def _fixed(value: float, digits: int) -> str:
    """``value`` rounded to ``digits`` significant digits, without an exponent."""
    exponent = math.floor(math.log10(abs(value)))
    decimals = max(0, digits - 1 - exponent)
    text = f"{value:.{decimals}f}"
    # Rounding can add a digit (9.9996 → 10.000): drop the extra decimal.
    if decimals > 0 and len(text.lstrip("-").replace(".", "").lstrip("0")) > digits:
        text = f"{value:.{decimals - 1}f}"
    return text


def format_number(value: float) -> str:
    """A number for display: 4 significant digits; an exponent only when very large or small."""
    if math.isnan(value):
        return ""
    if math.isinf(value):
        return "∞" if value > 0 else "−∞"
    if value == 0:
        return "0"
    if 1e-4 <= abs(value) < 1e6:
        return _fixed(value, SIGNIFICANT_DIGITS)
    return f"{value:.{SIGNIFICANT_DIGITS - 1}e}"


def format_p(p: float, display: PDisplay = DEFAULT_P_DISPLAY) -> str:
    """A P value for display: 3 significant digits (0.0312, 0.452, 1.00); below the
    floor of ``display`` "< 0.001" or "< 0.0001", or exactly, with 2 significant digits
    and a power of ten (2.3 × 10⁻⁷)."""
    if math.isnan(p):
        return ""
    if display is PDisplay.EXACT:
        if p >= P_FLOOR:
            return _fixed(p, P_SIGNIFICANT_DIGITS)
        if p < sys.float_info.min:
            # Below the smallest normal double: an underflow, not a measured value.
            return f"< {_power_of_ten(sys.float_info.min)}"
        return _power_of_ten(p)
    floor = 0.0001 if display is PDisplay.BELOW_00001 else P_FLOOR
    if p < floor:
        return f"< {floor:g}"
    return _fixed(p, P_SIGNIFICANT_DIGITS)


def _power_of_ten(p: float) -> str:
    """``p`` as m × 10ⁿ with :data:`P_EXACT_DIGITS` significant digits."""
    mantissa, exponent = f"{p:.{P_EXACT_DIGITS - 1}e}".split("e")
    return f"{mantissa} × 10{str(int(exponent)).translate(_SUPERSCRIPT)}"


_P_MARKER = re.compile(r"\u27e6P:([^\u27e7]+)\u27e7")


def p_marker(p: float) -> str:
    """A P value in a reading, turned into "P = 0.0312" or "P < 0.001" by
    :func:`render_p` when the reading is shown."""
    return f"\u27e6P:{p!r}\u27e7"


def p_phrase(p: float, display: PDisplay = DEFAULT_P_DISPLAY) -> str:
    """ "P = 0.0312", "P < 0.001" or "P = 2.3 × 10⁻⁷"."""
    text = format_p(p, display)
    return f"P {text}" if text.startswith("<") else f"P = {text}"


def render_p(text: str, display: PDisplay = DEFAULT_P_DISPLAY) -> str:
    """``text`` with its P-value markers written out as ``display`` shows them."""
    return _P_MARKER.sub(lambda m: p_phrase(float(m.group(1)), display), text)


def shown(value: Cell, kind: Kind, display: PDisplay = DEFAULT_P_DISPLAY) -> str:
    """The text shown for ``value`` in the Results panel."""
    if _is_missing(value):
        return ""
    if isinstance(value, str):
        return value
    assert value is not None
    if kind is Kind.INTEGER:
        return str(int(value))
    if kind is Kind.P_VALUE:
        return format_p(float(value), display)
    if kind is Kind.LABEL and math.isfinite(float(value)):
        return f"{float(value):.6g}"
    return format_number(float(value))


def full_text(value: Cell, kind: Kind) -> str:
    """``value`` at full precision (the tooltip of a shown value)."""
    if _is_missing(value):
        return ""
    if isinstance(value, str):
        return value
    assert value is not None
    if kind is Kind.INTEGER:
        return str(int(value))
    number = float(value)
    if math.isinf(number):
        return "Infinity" if number > 0 else "-Infinity"
    if number.is_integer() and abs(number) < 1e15:
        return str(int(number))
    return repr(number)


@dataclass(frozen=True)
class Value:
    """A named result value, e.g. ``Value("F", 128.4)`` or a P value."""

    label: str
    value: Cell
    kind: Kind = Kind.NUMBER
    unit: str = ""


@dataclass(frozen=True)
class ResultColumn:
    """A column of a :class:`ResultTable`."""

    label: str
    kind: Kind = Kind.NUMBER
    unit: str = ""

    @property
    def heading(self) -> str:
        return f"{self.label} ({self.unit})" if self.unit else self.label


@dataclass(frozen=True)
class ResultTable:
    """A titled table of results, e.g. the comparisons of a post-hoc test."""

    title: str
    columns: tuple[ResultColumn, ...]
    rows: tuple[tuple[Cell, ...], ...] = ()

    def __post_init__(self) -> None:
        for row in self.rows:
            if len(row) != len(self.columns):
                raise ValueError(
                    f"table '{self.title}': a row has {len(row)} cells for "
                    f"{len(self.columns)} columns"
                )


@dataclass(frozen=True)
class FittedCurve:
    """A fitted curve for graphs: points along the fit and its confidence band, in the
    units of the X column. The limits are NaN where the band is not defined."""

    series_id: str
    name: str
    x: tuple[float, ...]
    y: tuple[float, ...]
    lower: tuple[float, ...]
    upper: tuple[float, ...]
    log_x: bool = False
    """The points are spaced for a log X axis (dose-response on concentrations)."""
    confidence: float = 0.95


@dataclass(frozen=True)
class CellMeans:
    """The cells of a two-way design, for the interaction chart: per level of factor A
    (``a``) and factor B (``b``), the n, mean, SD and SEM of the cell's values."""

    factor_a: str
    factor_b: str
    levels_a: tuple[str, ...]
    levels_b: tuple[str, ...]
    n: tuple[tuple[int, ...], ...]
    """n[i][j]: level i of factor A at level j of factor B; likewise below."""
    mean: tuple[tuple[float, ...], ...]
    sd: tuple[tuple[float, ...], ...]
    sem: tuple[tuple[float, ...], ...]


@dataclass(frozen=True)
class PairP:
    """One comparison of two groups, for the brackets on bar-family graphs: the series
    ids (group or column ids, as the graph's series sources) and the P value the results
    show for it (adjusted, for a post-hoc test)."""

    first: str
    second: str
    p_value: float


@dataclass(frozen=True)
class Result:
    """What an analysis found."""

    method: str
    """The exact method name, e.g. "Dunnett's multiple comparisons test"."""
    summary: str
    """One plain-language sentence that reads the result."""
    warnings: tuple[str, ...] = ()
    """Assumptions that look violated, and other things to look at."""
    values: tuple[Value, ...] = ()
    tables: tuple[ResultTable, ...] = ()
    notes: tuple[str, ...] = ()
    """Extra secondary sentences shown under the results."""
    curves: tuple[FittedCurve, ...] = ()
    """Fitted curves, for graphs (curve fits only)."""
    cells: CellMeans | None = None
    """The cells of a two-way design, for the interaction chart (two-way ANOVA only)."""
    pairs: tuple[PairP, ...] = ()
    """The comparisons of two groups, for brackets on graphs (t-test, Mann-Whitney U,
    one-way ANOVA's post-hoc test, Dunn's test), in the order the results list them."""

    @property
    def has_p_values(self) -> bool:
        return any(v.kind is Kind.P_VALUE for v in self.values) or any(
            c.kind is Kind.P_VALUE for t in self.tables for c in t.columns
        )


@dataclass(frozen=True)
class Grid:
    """A result section laid out as text: what the panel shows and Ctrl+C copies.

    A P-value column is followed by a stars column (heading "").
    """

    title: str
    header: tuple[str, ...]
    shown: tuple[tuple[str, ...], ...]
    full: tuple[tuple[str, ...], ...]
    """The same cells at full precision, for tooltips."""
    numeric: tuple[bool, ...]
    """Per column: right-align it (numbers)."""


def table_grid(table: ResultTable, display: PDisplay = DEFAULT_P_DISPLAY) -> Grid:
    """The text of ``table``, with a stars column after each P-value column."""
    header: list[str] = []
    numeric: list[bool] = []
    for column in table.columns:
        header.append(column.heading)
        numeric.append(column.kind is not Kind.TEXT)
        if column.kind is Kind.P_VALUE:
            header.append("")
            numeric.append(False)
    shown_rows: list[tuple[str, ...]] = []
    full_rows: list[tuple[str, ...]] = []
    for row in table.rows:
        shown_row: list[str] = []
        full_row: list[str] = []
        for column, cell in zip(table.columns, row, strict=True):
            shown_row.append(shown(cell, column.kind, display))
            full_row.append(full_text(cell, column.kind))
            if column.kind is Kind.P_VALUE:
                mark = stars(_p_of(cell))
                shown_row.append(mark)
                full_row.append(mark)
        shown_rows.append(tuple(shown_row))
        full_rows.append(tuple(full_row))
    return Grid(table.title, tuple(header), tuple(shown_rows), tuple(full_rows), tuple(numeric))


def values_grid(
    values: tuple[Value, ...], title: str = "", display: PDisplay = DEFAULT_P_DISPLAY
) -> Grid:
    """Named values as a two-column table (name, value), plus stars for P values."""
    with_stars = any(v.kind is Kind.P_VALUE for v in values)
    header = ("", "Value", "") if with_stars else ("", "Value")
    shown_rows: list[tuple[str, ...]] = []
    full_rows: list[tuple[str, ...]] = []
    for v in values:
        label = f"{v.label} ({v.unit})" if v.unit else v.label
        shown_row = [label, shown(v.value, v.kind, display)]
        full_row = [label, full_text(v.value, v.kind)]
        if with_stars:
            mark = stars(_p_of(v.value)) if v.kind is Kind.P_VALUE else ""
            shown_row.append(mark)
            full_row.append(mark)
        shown_rows.append(tuple(shown_row))
        full_rows.append(tuple(full_row))
    numeric = (False, True, False) if with_stars else (False, True)
    return Grid(title, header, tuple(shown_rows), tuple(full_rows), numeric)


def grids(result: Result, display: PDisplay = DEFAULT_P_DISPLAY) -> list[Grid]:
    """The sections of ``result`` in display order: its values, then its tables."""
    sections: list[Grid] = []
    if result.values:
        sections.append(values_grid(result.values, result.method, display))
    sections += [table_grid(t, display) for t in result.tables]
    return sections
