"""Default chart specs: a sensible graph of a table in one click (the Plot menu).

- **XY**: every series against the table's X column. A replicate group is one series
  with mean ± SD error bars; a Y column without a group uses the Error column directly
  to its right, if there is one, and has no error bars otherwise. Curve fits of the
  table are laid over it, and the X axis is logarithmic when a dose-response fit uses
  concentrations.
- **Bar**: one bar per series, the mean of all its values with SD error bars.

:func:`family_problem` says why a table cannot be drawn in a family; the Plot menu
shows it next to the disabled family.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from curvis.analysis.base import Analysis, Family, rows_match_x, x_column_id
from curvis.charts.colors import DEFAULT_SCHEME, scheme
from curvis.charts.spec import (
    DEFAULT_ASPECT,
    DEFAULT_TEXT_SIZE,
    SYMBOL_CYCLE,
    AxisSpec,
    ChartFamily,
    ChartSpec,
    ChartVariant,
    ErrorBars,
    FitOverlay,
    LineStyle,
    Scale,
    SeriesSpec,
)
from curvis.data import Column, ColumnRole, Series, Table, series

DEFAULT_ERROR_BARS = ErrorBars.SD
"""Error bars of replicate groups in a new graph (confirmed for step 2.5)."""

NEEDS_X = "Needs an X column with values"
NEEDS_Y = "Needs a Y column"
NEEDS_SURVIVAL = "Needs a Time + Event pair"
SURVIVAL_LATER = "Survival graphs are not available yet"
NEEDS_TWO_WAY = "Needs a two-way ANOVA"

DOSE_RESPONSE_TYPES = ("hill_4pl", "hill_3pl")


def family_problem(table: Table, family: ChartFamily) -> str | None:
    """Why ``table`` cannot be drawn as a ``family`` graph (no full stop), or None."""
    if family is ChartFamily.XY:
        if not rows_match_x(table):
            return NEEDS_X
        return None if series(table) else NEEDS_Y
    if family is ChartFamily.BAR:
        return None if series(table) else NEEDS_Y
    if family is ChartFamily.SURVIVAL:
        roles = {c.role for c in table.columns}
        if not {ColumnRole.TIME, ColumnRole.EVENT} <= roles:
            return NEEDS_SURVIVAL
        return SURVIVAL_LATER
    return NEEDS_TWO_WAY


def error_column_of(table: Table, column_id: str) -> Column | None:
    """The Error column of the ungrouped Y column ``column_id``: the column directly to
    its right, if that is an Error column. None otherwise."""
    index = table.column_index(column_id)
    column = table.columns[index]
    if column.role is not ColumnRole.Y or column.group_id is not None:
        return None
    if index + 1 < len(table.columns):
        right = table.columns[index + 1]
        if right.role is ColumnRole.ERROR:
            return right
    return None


def series_source(s: Series) -> str:
    """The id a spec uses for a series: its group id, or its single column's id."""
    return s.group_id or s.column_ids[0]


def default_series(
    table: Table,
    *,
    color_scheme: str = DEFAULT_SCHEME,
    error_bars: ErrorBars = DEFAULT_ERROR_BARS,
    line: bool = False,
) -> tuple[SeriesSpec, ...]:
    """A styled :class:`SeriesSpec` for each series of ``table``, left to right."""
    colors = scheme(color_scheme)
    specs: list[SeriesSpec] = []
    for i, s in enumerate(series(table)):
        if s.group_id is not None:
            bars, error_column = error_bars, None
        else:
            error = error_column_of(table, s.column_ids[0])
            bars = ErrorBars.ERROR_COLUMN if error is not None else ErrorBars.NONE
            error_column = error.id if error is not None else None
        specs.append(
            SeriesSpec(
                source=series_source(s),
                color=colors.series_color(i),
                symbol=SYMBOL_CYCLE[i % len(SYMBOL_CYCLE)],
                line_style=LineStyle.SOLID if line else LineStyle.NONE,
                error_bars=bars,
                error_column=error_column,
            )
        )
    return tuple(specs)


def is_fit(analysis: Analysis) -> bool:
    try:
        return analysis.analysis_type.family is Family.FIT
    except KeyError:
        return False


def fits_on_log_x(analysis: Analysis) -> bool:
    """True for a dose-response fit whose X values are concentrations: its curve is
    spaced for, and only reads well on, a log X axis."""
    return analysis.type in DOSE_RESPONSE_TYPES and not getattr(analysis.params, "x_is_log", True)


def default_spec(
    table: Table,
    family: ChartFamily,
    analyses: Sequence[Analysis] = (),
    *,
    error_bars: ErrorBars = DEFAULT_ERROR_BARS,
    aspect_ratio: str = DEFAULT_ASPECT,
    text_size: float = DEFAULT_TEXT_SIZE,
) -> ChartSpec:
    """A new graph of ``table`` in ``family``. ``analyses`` are the document's analyses;
    the curve fits of this table among them are overlaid on an XY graph.

    Raises ``ValueError`` with :func:`family_problem`'s reason if the table cannot be
    drawn in that family.
    """
    problem = family_problem(table, family)
    if problem is not None:
        raise ValueError(f"{problem}.")
    found = series(table)
    if family is ChartFamily.XY:
        x_id = x_column_id(table)
        assert x_id is not None
        fits = [a for a in analyses if a.table_id == table.id and is_fit(a)]
        log_x = any(fits_on_log_x(a) for a in fits)
        y_title = found[0].name if len(found) == 1 else ""
        return ChartSpec(
            table_id=table.id,
            family=family,
            variant=ChartVariant.SCATTER,
            series=default_series(table, error_bars=error_bars),
            x_axis=AxisSpec(
                title=column_title(table.column(x_id)),
                scale=Scale.LOG if log_x else Scale.LINEAR,
            ),
            y_axis=AxisSpec(title=y_title),
            title=table.name,
            aspect_ratio=aspect_ratio,
            text_size=text_size,
            overlays=tuple(FitOverlay(a.id) for a in fits),
        )
    return ChartSpec(
        table_id=table.id,
        family=family,
        variant=ChartVariant.BAR,
        series=tuple(
            replace(s, error_bars=error_bars, error_column=None)
            for s in default_series(table, error_bars=error_bars)
        ),
        y_axis=AxisSpec(title=_shared_unit(table, found)),
        title=table.name,
        aspect_ratio=aspect_ratio,
        text_size=text_size,
    )


def with_fit(spec: ChartSpec, analysis: Analysis) -> ChartSpec:
    """``spec`` with the fitted curves of ``analysis`` laid over it. A linear X axis that
    the user has not limited becomes logarithmic for a dose-response fit on
    concentrations."""
    new = spec.with_overlay(FitOverlay(analysis.id))
    axis = new.x_axis
    if (
        fits_on_log_x(analysis)
        and axis.scale is Scale.LINEAR
        and axis.minimum is None
        and axis.maximum is None
    ):
        new = replace(new, x_axis=replace(axis, scale=Scale.LOG))
    return new


def column_title(column: Column) -> str:
    """An axis title for a column: its name, with the unit in brackets if it has one."""
    return f"{column.name} ({column.unit})" if column.unit else column.name


def _shared_unit(table: Table, found: Sequence[Series]) -> str:
    units = {table.column(c).unit for s in found for c in s.column_ids}
    return units.pop() if len(units) == 1 else ""
