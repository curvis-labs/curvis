"""Default chart specs: a sensible graph of a table in one click (the Plot menu).

- **XY**: every series against the table's X column. A replicate group is one series
  with mean ± SD error bars; a Y column without a group uses the Error column directly
  to its right, if there is one, and has no error bars otherwise. Curve fits of the
  table are laid over it, and the X axis is logarithmic when a dose-response fit uses
  concentrations.
- **Bar**: one bar per series, the mean of all its values with SD error bars.
- **Interaction**: the cell means of the table's latest two-way ANOVA, with the levels
  of the first factor on the X axis and one line per level of the second factor (the
  category groups), with SD error bars. An interaction graph keeps its type.

New XY graphs show a legend; bar graphs name their bars on the X axis and show one only
when the table has category groups, which a new bar graph is grouped by (one cluster per
series name, coloured by category). :func:`with_variant` changes the type of a graph,
keeping its series and their style; :func:`with_grouping` and :func:`with_palette`
change its clusters and colours.

A new graph copies its page size, palette, font and text size from
:class:`GraphDefaults` (the Settings' "New graphs"), so later changes to the Settings
never change it.

:func:`family_problem` says why a table cannot be drawn in a family; the Plot menu
shows it next to the disabled family.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from curvis.analysis.base import Analysis, Family, rows_match_x, x_column_id
from curvis.charts.colors import CURVIS, ColorScheme, recolored
from curvis.charts.grouping import Entry, grouped_layout
from curvis.charts.spec import (
    GROUPABLE_VARIANTS,
    HORIZONTAL_VARIANTS,
    SYMBOL_CYCLE,
    AxisSpec,
    ChartFamily,
    ChartSpec,
    ChartVariant,
    ErrorBars,
    FitOverlay,
    GraphDefaults,
    Grouping,
    LabelAngle,
    LineStyle,
    Scale,
    SeriesSpec,
    Symbol,
)
from curvis.data import Column, ColumnRole, Series, Table, series, survival_pairs

DEFAULT_ERROR_BARS = ErrorBars.SD
"""Error bars of replicate groups in a new graph (confirmed for step 2.5)."""

NEEDS_X = "Needs an X column with values"
NEEDS_Y = "Needs a Y column"
NEEDS_SURVIVAL = "Needs a Time + Event pair"
NEEDS_TWO_WAY = "Needs a two-way ANOVA"

DOSE_RESPONSE_TYPES = ("hill_4pl", "hill_3pl", "biphasic", "bell_shaped")


TWO_WAY = "two_way_anova"
INTERACTION_ONLY = "An interaction graph keeps its type"
SURVIVAL_ONLY = "A survival graph keeps its type"
SURVIVAL_Y_TITLE = "Survival probability"
HISTOGRAM_Y_TITLE = "Number of values"


def two_way_analyses(table: Table, analyses: Sequence[Analysis]) -> list[Analysis]:
    """The two-way ANOVAs of ``table`` among ``analyses``, oldest first."""
    return [a for a in analyses if a.table_id == table.id and a.type == TWO_WAY]


def first_factor_name(analysis: Analysis) -> str:
    """The name of the first factor of a two-way ANOVA, as its results show it."""
    from curvis.analysis.compare.two_way_analysis import DEFAULT_FACTOR

    return str(getattr(analysis.params, "factor_name", "")).strip() or DEFAULT_FACTOR


def family_problem(
    table: Table, family: ChartFamily, analyses: Sequence[Analysis] = ()
) -> str | None:
    """Why ``table`` cannot be drawn as a ``family`` graph (no full stop), or None.
    ``analyses`` are the document's analyses (an interaction graph draws a two-way
    ANOVA)."""
    if family is ChartFamily.XY:
        if not rows_match_x(table):
            return NEEDS_X
        return None if series(table) else NEEDS_Y
    if family is ChartFamily.BAR:
        return None if series(table) else NEEDS_Y
    if family is ChartFamily.SURVIVAL:
        return None if survival_pairs(table) else NEEDS_SURVIVAL
    return None if two_way_analyses(table, analyses) else NEEDS_TWO_WAY


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
    palette: ColorScheme = CURVIS,
    error_bars: ErrorBars = DEFAULT_ERROR_BARS,
    line: bool = False,
) -> tuple[SeriesSpec, ...]:
    """A styled :class:`SeriesSpec` for each series of ``table``, left to right."""
    colors = palette
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


def interaction_series(
    table: Table,
    *,
    palette: ColorScheme = CURVIS,
    error_bars: ErrorBars = DEFAULT_ERROR_BARS,
) -> tuple[SeriesSpec, ...]:
    """A styled line for each category group of ``table`` (a level of the second
    factor of a two-way ANOVA), left to right."""
    colors = palette
    return tuple(
        SeriesSpec(
            source=c.id,
            color=colors.series_color(i),
            symbol=SYMBOL_CYCLE[i % len(SYMBOL_CYCLE)],
            line_style=LineStyle.SOLID,
            error_bars=error_bars,
        )
        for i, c in enumerate(table.category_groups)
    )


def survival_series(table: Table, *, palette: ColorScheme = CURVIS) -> tuple[SeriesSpec, ...]:
    """A styled step curve for each Time + Event pair of ``table``, left to right. The
    symbol is unused: censored subjects are marked with ticks."""
    colors = palette
    return tuple(
        SeriesSpec(
            source=pair.source,
            color=colors.series_color(i),
            symbol=Symbol.NONE,
            line_style=LineStyle.SOLID,
            error_bars=ErrorBars.NONE,
        )
        for i, pair in enumerate(survival_pairs(table))
    )


def graph_series_names(table: Table, spec: ChartSpec) -> dict[str, str]:
    """What ``spec`` can draw from ``table``, by source: its category groups for an
    interaction graph, its Time + Event pairs for a survival graph, its series
    otherwise."""
    if spec.family is ChartFamily.INTERACTION:
        return {c.id: c.name for c in table.category_groups}
    if spec.family is ChartFamily.SURVIVAL:
        return {p.source: p.name for p in survival_pairs(table)}
    return {series_source(s): s.name for s in series(table)}


def default_series_of(table: Table, spec: ChartSpec) -> tuple[SeriesSpec, ...]:
    """The default style of everything ``spec`` can draw from ``table``, in the graph's
    palette."""
    if spec.family is ChartFamily.INTERACTION:
        return interaction_series(table, palette=spec.palette)
    if spec.family is ChartFamily.SURVIVAL:
        return survival_series(table, palette=spec.palette)
    return default_series(table, palette=spec.palette)


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
    defaults: GraphDefaults | None = None,
) -> ChartSpec:
    """A new graph of ``table`` in ``family``. ``analyses`` are the document's analyses;
    the curve fits of this table among them are overlaid on an XY graph. ``defaults``
    (the Settings' "New graphs") give its size, palette, font and text size.

    Raises ``ValueError`` with :func:`family_problem`'s reason if the table cannot be
    drawn in that family.
    """
    problem = family_problem(table, family, analyses)
    if problem is not None:
        raise ValueError(f"{problem}.")
    d = defaults if defaults is not None else GraphDefaults()
    palette = d.palette
    found = series(table)
    if family is ChartFamily.INTERACTION:
        analysis = two_way_analyses(table, analyses)[-1]
        factor = first_factor_name(analysis)
        return ChartSpec(
            table_id=table.id,
            family=family,
            variant=ChartVariant.INTERACTION,
            series=interaction_series(table, palette=palette, error_bars=error_bars),
            x_axis=AxisSpec(title=factor),
            y_axis=AxisSpec(title=_shared_unit(table, found)),
            title=table.name,
            aspect_ratio=d.aspect_ratio,
            custom_size=d.custom_size,
            palette=d.palette,
            font=d.font,
            text_size=d.text_size,
            show_legend=True,
            analysis_id=analysis.id,
        )
    if family is ChartFamily.SURVIVAL:
        pairs = survival_pairs(table)
        return ChartSpec(
            table_id=table.id,
            family=family,
            variant=ChartVariant.KAPLAN_MEIER,
            series=survival_series(table, palette=palette),
            x_axis=AxisSpec(title=column_title(table.column(pairs[0].time_id))),
            y_axis=AxisSpec(title=SURVIVAL_Y_TITLE),
            title=table.name,
            aspect_ratio=d.aspect_ratio,
            custom_size=d.custom_size,
            palette=d.palette,
            font=d.font,
            text_size=d.text_size,
            show_legend=True,
        )
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
            series=default_series(table, palette=palette, error_bars=error_bars),
            x_axis=AxisSpec(
                title=column_title(table.column(x_id)),
                scale=Scale.LOG if log_x else Scale.LINEAR,
            ),
            y_axis=AxisSpec(title=y_title),
            title=table.name,
            aspect_ratio=d.aspect_ratio,
            custom_size=d.custom_size,
            palette=d.palette,
            font=d.font,
            text_size=d.text_size,
            overlays=tuple(FitOverlay(a.id) for a in fits),
            show_legend=True,
        )
    bar = ChartSpec(
        table_id=table.id,
        family=family,
        variant=ChartVariant.BAR,
        series=tuple(
            replace(s, error_bars=error_bars, error_column=None)
            for s in default_series(table, palette=palette, error_bars=error_bars)
        ),
        y_axis=AxisSpec(title=_shared_unit(table, found)),
        title=table.name,
        aspect_ratio=d.aspect_ratio,
        custom_size=d.custom_size,
        palette=d.palette,
        font=d.font,
        text_size=d.text_size,
        show_legend=bool(table.category_groups),
    )
    if any(s.category_id is not None for s in found):
        bar = with_grouping(bar, Grouping.BY_SERIES, table)
    return bar


GRAPH_TYPES = (
    ChartVariant.SCATTER,
    ChartVariant.LINE,
    ChartVariant.BAR,
    ChartVariant.HORIZONTAL_BAR,
    ChartVariant.BOX,
    ChartVariant.VIOLIN,
    ChartVariant.COLUMN_SCATTER,
    ChartVariant.HISTOGRAM,
)
"""The types a graph can be changed to (Change graph type)."""
TOOLBAR_TYPES = (ChartVariant.SCATTER, ChartVariant.LINE, ChartVariant.BAR, ChartVariant.BOX)
"""The types on the Graph panel's toolbar (SPEC.md); the others are in the menu."""


def variant_problem(
    table: Table, variant: ChartVariant, current: ChartSpec | None = None
) -> str | None:
    """Why a graph of ``table`` (``current``, if given) cannot be changed to
    ``variant`` (no full stop), or None."""
    if current is not None and current.family is ChartFamily.INTERACTION:
        return INTERACTION_ONLY
    if current is not None and current.family is ChartFamily.SURVIVAL:
        return SURVIVAL_ONLY
    if variant not in GRAPH_TYPES:
        return "This chart type cannot be drawn yet"
    return family_problem(table, variant.family)


def with_variant(spec: ChartSpec, variant: ChartVariant, table: Table) -> ChartSpec:
    """``spec`` drawn as ``variant``, keeping its series, their style, the title, size,
    text and legend. Moving to the bar family turns Error-column bars into SD and makes
    the X axis the categories; moving to XY puts the table's X column on the X axis and
    uses the Error column of a Y column that has one. Fit overlays are kept, so they come
    back when the graph is XY again.

    Raises ``ValueError`` with :func:`variant_problem`'s reason.
    """
    problem = variant_problem(table, variant, spec)
    if problem is not None:
        raise ValueError(f"{problem}.")
    family = variant.family
    if spec.family is ChartFamily.BAR and family is ChartFamily.BAR:
        category, value = _bar_axes(spec)
        changed = replace(spec, variant=variant)
        if variant is ChartVariant.HISTOGRAM and spec.variant is not ChartVariant.HISTOGRAM:
            # Overlaid series need their names: a legend when there are several.
            changed = replace(changed, show_legend=len(spec.series) > 1)
        changed = _recolor_for(changed, spec, table)
        return _with_bar_axes(changed, category, value, table)
    if spec.family is ChartFamily.BAR:
        # Back to the upright layout first, so the X axis is the categories' again.
        category, value = _bar_axes(spec)
        spec = replace(spec, variant=ChartVariant.BAR, x_axis=category, y_axis=value)
    if family is spec.family:
        return replace(spec, variant=variant)
    if family is ChartFamily.BAR:
        series = tuple(
            replace(s, error_bars=ErrorBars.SD, error_column=None)
            if s.error_bars is ErrorBars.ERROR_COLUMN
            else s
            for s in spec.series
        )
        bar = replace(spec, family=family, variant=ChartVariant.BAR, series=series)
        return _with_bar_axes(replace(bar, variant=variant), AxisSpec(), spec.y_axis, table)
    x_id = x_column_id(table)
    assert x_id is not None
    styled: list[SeriesSpec] = []
    for s in spec.series:
        error = None
        if any(c.id == s.source for c in table.columns):
            error = error_column_of(table, s.source)
        if error is not None:
            s = replace(s, error_bars=ErrorBars.ERROR_COLUMN, error_column=error.id)
        styled.append(s)
    x_axis = AxisSpec(title=column_title(table.column(x_id)))
    return replace(spec, family=family, variant=variant, series=tuple(styled), x_axis=x_axis)


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


def _recolor_for(changed: ChartSpec, before: ChartSpec, table: Table) -> ChartSpec:
    """Colours that suit ``changed``'s variant when it is grouped and ``before``'s was
    not, or the other way round: a grouped graph colours by category (or series name),
    one that cannot be grouped (histogram, column scatter) gives each series its own.
    The grouping is kept, so it comes back with a type that can be grouped."""
    if changed.grouping is Grouping.NONE:
        return changed
    was = before.variant in GROUPABLE_VARIANTS
    now = changed.variant in GROUPABLE_VARIANTS
    if was == now:
        return changed
    if now:
        return with_grouping(changed, changed.grouping, table)
    return replace(with_grouping(changed, Grouping.NONE, table), grouping=changed.grouping)


def _bar_axes(spec: ChartSpec) -> tuple[AxisSpec, AxisSpec]:
    """(category axis, value axis) of a bar-family graph, whichever way it is drawn.
    A histogram's value axis is its X axis; it has no category axis."""
    if spec.variant in HORIZONTAL_VARIANTS:
        return spec.y_axis, spec.x_axis
    if spec.variant is ChartVariant.HISTOGRAM:
        return AxisSpec(), spec.x_axis
    return spec.x_axis, spec.y_axis


def _with_bar_axes(spec: ChartSpec, category: AxisSpec, value: AxisSpec, table: Table) -> ChartSpec:
    """``spec`` with the category and value axes put where its variant draws them."""
    if spec.variant in HORIZONTAL_VARIANTS:
        return replace(spec, x_axis=value, y_axis=replace(category, label_angle=LabelAngle.AUTO))
    if spec.variant is ChartVariant.HISTOGRAM:
        if not value.title:
            value = replace(value, title=_shared_unit(table, series(table)))
        return replace(spec, x_axis=value, y_axis=AxisSpec(title=HISTOGRAM_Y_TITLE))
    return replace(spec, x_axis=category, y_axis=value)


def with_grouping(spec: ChartSpec, grouping: Grouping, table: Table) -> ChartSpec:
    """``spec`` laid out with ``grouping``, its series recoloured to match: grouped, the
    series of one colour (a category, or a series name) share the palette's colour of
    that place; not grouped, each series takes the palette's colours in turn."""
    if grouping is Grouping.NONE:
        colors = [spec.palette.series_color(i) for i in range(len(spec.series))]
    else:
        colors = [
            spec.palette.series_color(i)
            for i in grouped_color_indexes(group_entries(spec, table), grouping)
        ]
    styled = tuple(replace(s, color=c) for s, c in zip(spec.series, colors, strict=True))
    return replace(spec, grouping=grouping, series=styled)


def group_entries(spec: ChartSpec, table: Table) -> list[Entry]:
    """The series of ``spec`` as :mod:`curvis.charts.grouping` sees them."""
    categories = {c.id: c.name for c in table.category_groups}
    found = {series_source(s): s for s in series(table)}
    entries = []
    for s in spec.series:
        known = found.get(s.source)
        name = known.name if known is not None else ""
        category = categories.get(known.category_id or "") if known is not None else None
        entries.append(Entry(s.source, name, category))
    return entries


def grouped_color_indexes(entries: Sequence[Entry], grouping: Grouping) -> list[int]:
    """The palette index of each entry's colour in a graph grouped by ``grouping``."""
    found = grouped_layout(entries, grouping)
    keys = [key for key, _name in found.legend()]
    return [keys.index(p.color_key) for p in found.places]


def with_palette(spec: ChartSpec, palette: ColorScheme) -> ChartSpec:
    """``spec`` in ``palette``: the page, ink and series colours change; series that
    shared a colour share the new one."""
    colors = recolored([s.color for s in spec.series], palette)
    styled = tuple(replace(s, color=c) for s, c in zip(spec.series, colors, strict=True))
    return replace(spec, palette=palette, series=styled)


def column_title(column: Column) -> str:
    """An axis title for a column: its name, with the unit in brackets if it has one."""
    return f"{column.name} ({column.unit})" if column.unit else column.name


def _shared_unit(table: Table, found: Sequence[Series]) -> str:
    units = {table.column(c).unit for s in found for c in s.column_ids}
    return units.pop() if len(units) == 1 else ""
