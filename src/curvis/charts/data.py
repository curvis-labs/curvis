"""Plot data: the numbers a graph draws, computed from a spec and its table.

:func:`gather` copies what a graph reads out of the document (main thread);
:func:`compute` turns it into :class:`ChartData` (a pure function, run by the
scheduler). The renderers only draw :class:`ChartData`; they compute nothing.

Statistics shown on graphs:

- XY: per row, the mean of a series' replicates with their SD or SEM
  (:func:`curvis.data.row_stats`, validated against R in ``descriptive_stats``), or a
  single column's value with its Error column.
- Bar: the mean of all of a series' values with their SD or SEM (also ``row_stats``).
- Box: :func:`box_stats` — the quartiles by R's ``quantile(type = 6)`` (as GraphPad
  Prism) and whiskers from the minimum to the maximum (validated in ``box_stats``).
- Survival: Kaplan-Meier step curves of each Time + Event pair, their 95% confidence
  bands (log-log) and ticks at censored times, from
  :func:`curvis.analysis.survival.km.kaplan_meier` (validated in ``kaplan_meier``).
- Interaction: the cell means of the graph's two-way ANOVA with their SD or SEM, as the
  analysis reports them (validated in ``two_way_anova``). The levels of the first factor
  are at X = 0, 1, 2 …; each line is one level of the second factor (a category group).

On a log axis the coordinates are log10 values; points at or below 0 cannot be drawn
there and are left out, with a note. The values for tooltips stay as typed.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from curvis.analysis.base import x_column_id
from curvis.analysis.result import CellMeans, FittedCurve, Result
from curvis.charts.spec import ChartFamily, ChartSpec, ChartVariant, ErrorBars, Scale
from curvis.data import Table, name_key, row_stats, series, survival_pairs

type FloatArray = npt.NDArray[np.float64]
type IntArray = npt.NDArray[np.int64]

_QUANTILE_FUZZ = 4 * np.finfo(np.float64).eps


# --- inputs ----------------------------------------------------------------------------


@dataclass(frozen=True)
class SeriesInput:
    """One series' values, copied out of the table."""

    source: str
    name: str
    columns: tuple[FloatArray, ...]
    """One array per replicate column."""
    error: FloatArray | None = None
    """The Error column's values (for :attr:`ErrorBars.ERROR_COLUMN`)."""


@dataclass(frozen=True)
class CurveInput:
    curve: FittedCurve
    show_band: bool


@dataclass(frozen=True)
class ChartInputs:
    """Everything :func:`compute` reads: plain copies, safe off the main thread."""

    spec: ChartSpec
    x: FloatArray | None
    series: tuple[SeriesInput, ...]
    curves: tuple[CurveInput, ...] = ()
    missing: int = 0
    """Series in the spec that are no longer in the table."""
    notes: tuple[str, ...] = ()
    cells: CellMeans | None = None
    """Interaction graphs: the cells of the two-way ANOVA (None: it has no result)."""
    line_names: tuple[tuple[str, str], ...] = ()
    """Interaction graphs: (source, name) of each category group the spec draws."""


def gather(table: Table, spec: ChartSpec, results: Mapping[str, Result]) -> ChartInputs:
    """Copy what ``spec`` draws out of ``table``; ``results`` are the analysis results
    by id (for fit overlays and interaction graphs). Series whose source is gone are
    counted, not drawn."""
    if spec.family is ChartFamily.INTERACTION:
        return _gather_interaction(table, spec, results)
    if spec.family is ChartFamily.SURVIVAL:
        return _gather_survival(table, spec)
    by_source = {s.group_id or s.column_ids[0]: s for s in series(table)}
    inputs: list[SeriesInput] = []
    missing = 0
    for style in spec.series:
        found = by_source.get(style.source)
        if found is None:
            missing += 1
            continue
        error = None
        if style.error_bars is ErrorBars.ERROR_COLUMN and style.error_column is not None:
            try:
                error = np.array(table.column(style.error_column).values, dtype=np.float64)
            except KeyError:
                error = None
        columns = tuple(
            np.array(table.column(c).values, dtype=np.float64) for c in found.column_ids
        )
        inputs.append(SeriesInput(style.source, found.name, columns, error))
    x = None
    if spec.family is ChartFamily.XY:
        x_id = x_column_id(table)
        if x_id is not None:
            x = np.array(table.column(x_id).values, dtype=np.float64)
    curves: list[CurveInput] = []
    notes: list[str] = []
    if spec.family is ChartFamily.XY:
        for overlay in spec.overlays:
            result = results.get(overlay.analysis_id)
            if result is None:
                continue
            curves.extend(CurveInput(c, overlay.show_band) for c in result.curves)
    return ChartInputs(spec, x, tuple(inputs), tuple(curves), missing, tuple(notes))


def _gather_interaction(
    table: Table, spec: ChartSpec, results: Mapping[str, Result]
) -> ChartInputs:
    categories = {c.id: c.name for c in table.category_groups}
    names = tuple((s.source, categories[s.source]) for s in spec.series if s.source in categories)
    result = results.get(spec.analysis_id or "")
    cells = result.cells if result is not None else None
    notes = () if cells is not None else (NO_TWO_WAY_RESULT,)
    missing = len(spec.series) - len(names)
    return ChartInputs(spec, None, (), missing=missing, notes=notes, cells=cells, line_names=names)


def _gather_survival(table: Table, spec: ChartSpec) -> ChartInputs:
    """Survival graphs: each series is a Time + Event pair, as (time, event) columns."""
    pairs = {p.source: p for p in survival_pairs(table)}
    inputs: list[SeriesInput] = []
    missing = 0
    for style in spec.series:
        pair = pairs.get(style.source)
        if pair is None:
            missing += 1
            continue
        columns = tuple(
            np.array(table.column(c).values, dtype=np.float64)
            for c in (pair.time_id, pair.event_id)
        )
        inputs.append(SeriesInput(style.source, pair.name, columns))
    return ChartInputs(spec, None, tuple(inputs), missing=missing)


NO_TWO_WAY_RESULT = (
    "The two-way ANOVA this graph draws has no results. Check it in the Results panel."
)


# --- outputs ---------------------------------------------------------------------------


@dataclass(frozen=True)
class XYSeriesData:
    """The points of one XY series. ``px``/``py`` are plot coordinates (log10 on a log
    axis); ``err_low``/``err_high`` are distances below and above ``py`` in plot
    coordinates (NaN: no bar). The other arrays are the values as typed, per point."""

    source: str
    name: str
    px: FloatArray
    py: FloatArray
    err_low: FloatArray
    err_high: FloatArray
    rows: IntArray
    """Table row (0-based) of each point."""
    x: FloatArray
    y: FloatArray
    """The value of a single column, or the mean of the replicates."""
    error: FloatArray
    """SD, SEM or the Error column's value as typed (NaN when none); bars use its size."""
    n: IntArray
    replicates: FloatArray
    """Shape (points, replicate columns): the replicate values of each row."""
    error_bars: ErrorBars


@dataclass(frozen=True)
class BoxStats:
    """Five-number summary of one group (NaN when n = 0)."""

    n: int
    min: float
    q1: float
    median: float
    q3: float
    max: float


@dataclass(frozen=True)
class CategoryData:
    """One bar or box: a series' values pooled over its replicate columns."""

    source: str
    name: str
    position: int
    mean: float
    error: float
    """SD or SEM of the values (NaN when none or n < 2)."""
    n: int
    box: BoxStats
    plot_box: BoxStats
    """``box`` in plot coordinates (log10 on a log Y axis)."""
    error_bars: ErrorBars
    err_low: float = 0.0
    err_high: float = 0.0
    """How far a bar's error bar reaches below and above its mean. Bars show it only
    away from zero (above a positive mean, below a negative one), as GraphPad Prism
    does by default; 0 where there is none."""


@dataclass(frozen=True)
class CurveData:
    series_source: str
    name: str
    px: FloatArray
    py: FloatArray
    low: FloatArray
    high: FloatArray
    show_band: bool


@dataclass(frozen=True)
class SurvivalCurveData:
    """One Kaplan-Meier curve, in plot coordinates (log10 on a log axis).

    ``px``/``py`` trace the steps: from (0, 1), at each time a vertical drop to the new
    survival, then flat to the next time. ``low``/``high`` are the confidence band at
    the same ``px`` (NaN where there is none: after the curve reaches 0). The censor
    ticks are at ``censor_px``/``censor_py``.
    """

    source: str
    name: str
    px: FloatArray
    py: FloatArray
    low: FloatArray
    high: FloatArray
    censor_px: FloatArray
    censor_py: FloatArray
    n: int
    n_events: int
    median: float
    """Median survival in the units of the Time column (NaN: not reached)."""


@dataclass(frozen=True)
class ChartData:
    family: ChartFamily
    variant: ChartVariant
    xy: tuple[XYSeriesData, ...] = ()
    categories: tuple[CategoryData, ...] = ()
    curves: tuple[CurveData, ...] = ()
    notes: tuple[str, ...] = ()
    """Secondary sentences for the Graph panel (left-out points, missing series)."""
    levels: tuple[str, ...] = ()
    """Interaction graphs: the levels of the first factor, at X = 0, 1, 2 …"""
    survival: tuple[SurvivalCurveData, ...] = ()
    """Survival graphs: one Kaplan-Meier curve per Time + Event pair."""


# --- statistics ------------------------------------------------------------------------


def box_stats(values: npt.ArrayLike) -> BoxStats:
    """n, minimum, quartiles (R ``quantile`` type 6) and maximum, ignoring NaN.

    Follows R's ``quantile.default`` exactly: position j + h = (n + 1) p (with R's
    fuzz for h), clamped to the smallest and largest value, and the value
    (1 - h) x[j] + h x[j + 1] where the two neighbours differ.
    """
    x = np.asarray(values, dtype=np.float64).ravel()
    x = np.sort(x[~np.isnan(x)])
    n = len(x)
    if n == 0:
        nan = math.nan
        return BoxStats(0, nan, nan, nan, nan, nan)
    padded = np.concatenate([[x[0], x[0]], x, [x[-1], x[-1]]])
    quartiles: list[float] = []
    for p in (0.25, 0.5, 0.75):
        position = p * (n + 1)
        j = math.floor(position + _QUANTILE_FUZZ)
        h = position - j
        if abs(h) < _QUANTILE_FUZZ:
            h = 0.0
        # R (1-based, x padded with two copies at each end): x[j + 2] and x[j + 3].
        low, high = float(padded[j + 1]), float(padded[j + 2])
        if h == 1.0:
            value = high
        elif 0.0 < h < 1.0 and low != high:
            value = (1.0 - h) * low + h * high
        else:
            value = low
        quartiles.append(value)
    return BoxStats(n, float(x[0]), quartiles[0], quartiles[1], quartiles[2], float(x[-1]))


def pooled_stats(values: FloatArray) -> tuple[float, float, float, int]:
    """Mean, SD, SEM and n of ``values`` (NaN left out), with :func:`row_stats`."""
    present = values[~np.isnan(values)]
    if len(present) == 0:
        return math.nan, math.nan, math.nan, 0
    stats = row_stats(list(present[:, np.newaxis]))
    return float(stats.mean[0]), float(stats.sd[0]), float(stats.sem[0]), int(stats.n[0])


# --- compute ---------------------------------------------------------------------------


def _log_mask(values: FloatArray, scale: Scale) -> npt.NDArray[np.bool_]:
    """Where ``values`` can be drawn on an axis with ``scale``."""
    finite = np.isfinite(values)
    return finite & (values > 0) if scale is Scale.LOG else finite


def _to_plot(values: FloatArray, scale: Scale) -> FloatArray:
    if scale is Scale.LINEAR:
        return values
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.log10(values)


def compute(inputs: ChartInputs) -> ChartData:
    """The plot data of ``inputs.spec``. Pure; runs in the scheduler."""
    spec = inputs.spec
    notes = list(inputs.notes)
    if inputs.missing:
        many = inputs.missing != 1
        notes.append(f"{inputs.missing} series {'are' if many else 'is'} no longer in the table.")
    if spec.family is ChartFamily.INTERACTION:
        xy, dropped, levels = _interaction(inputs)
        if dropped:
            s = "s" if dropped != 1 else ""
            notes.append(f"{dropped} mean{s} at or below 0 cannot be shown on a log axis.")
        return ChartData(spec.family, spec.variant, xy=xy, notes=tuple(notes), levels=levels)
    if spec.family is ChartFamily.SURVIVAL:
        drawn, problems = _survival(inputs)
        return ChartData(spec.family, spec.variant, notes=(*notes, *problems), survival=drawn)
    if spec.family is ChartFamily.XY:
        xy, dropped = _xy(inputs)
        curves = _curves(inputs)
        if dropped:
            s = "s" if dropped != 1 else ""
            notes.append(f"{dropped} point{s} at or below 0 cannot be shown on a log axis.")
        return ChartData(spec.family, spec.variant, xy=xy, curves=curves, notes=tuple(notes))
    categories = _categories(inputs)
    return ChartData(spec.family, spec.variant, categories=categories, notes=tuple(notes))


def _xy(inputs: ChartInputs) -> tuple[tuple[XYSeriesData, ...], int]:
    spec = inputs.spec
    x_scale, y_scale = spec.x_axis.scale, spec.y_axis.scale
    if inputs.x is None:
        return (), 0
    x_all = inputs.x
    out: list[XYSeriesData] = []
    dropped = 0
    for s in inputs.series:
        style = spec.series_for(s.source)
        assert style is not None
        reps = np.column_stack(s.columns) if s.columns else np.empty((len(x_all), 0))
        if len(s.columns) == 1:
            y = s.columns[0]
            n = (~np.isnan(y)).astype(np.int64)
            if style.error_bars is ErrorBars.ERROR_COLUMN and s.error is not None:
                error = s.error
            else:
                error = np.full(len(y), np.nan)
        else:
            stats = row_stats(s.columns)
            y, n = stats.mean, stats.n
            if style.error_bars is ErrorBars.SD:
                error = stats.sd
            elif style.error_bars is ErrorBars.SEM:
                error = stats.sem
            else:
                error = np.full(len(y), np.nan)
        if style.error_bars is ErrorBars.NONE:
            error = np.full(len(y), np.nan)
        shown = np.isfinite(x_all) & np.isfinite(y)
        drawable = shown & _log_mask(x_all, x_scale) & _log_mask(y, y_scale)
        dropped += int(np.count_nonzero(shown & ~drawable))
        rows = np.flatnonzero(drawable).astype(np.int64)
        xs, ys, es = x_all[rows], y[rows], error[rows]
        py = _to_plot(ys, y_scale)
        low, high = _error_extent(ys, es, py, y_scale)
        out.append(
            XYSeriesData(
                source=s.source,
                name=s.name,
                px=_to_plot(xs, x_scale),
                py=py,
                err_low=low,
                err_high=high,
                rows=rows,
                x=xs,
                y=ys,
                error=es,
                n=n[rows].astype(np.int64),
                replicates=reps[rows],
                error_bars=style.error_bars,
            )
        )
    return tuple(out), dropped


def _error_extent(
    ys: FloatArray, es: FloatArray, py: FloatArray, y_scale: Scale
) -> tuple[FloatArray, FloatArray]:
    """How far error bars of size ``es`` reach below and above ``py`` (plot coordinates)."""
    with np.errstate(invalid="ignore", divide="ignore"):
        if y_scale is Scale.LOG:
            size = np.abs(es)
            high = np.log10(ys + size) - py
            below = ys - size
            low = np.where(below > 0, py - np.log10(np.where(below > 0, below, 1.0)), np.nan)
            return low, high
        return np.abs(es), np.abs(es)


def _interaction(inputs: ChartInputs) -> tuple[tuple[XYSeriesData, ...], int, tuple[str, ...]]:
    spec, cells = inputs.spec, inputs.cells
    if cells is None:
        return (), 0, ()
    y_scale = spec.y_axis.scale
    columns = {name_key(name): j for j, name in enumerate(cells.levels_b)}
    a = len(cells.levels_a)
    out: list[XYSeriesData] = []
    dropped = 0
    for source, name in inputs.line_names:
        j = columns.get(name_key(name))
        style = spec.series_for(source)
        if j is None or style is None:
            continue
        y = np.array([cells.mean[i][j] for i in range(a)], dtype=np.float64)
        n = np.array([cells.n[i][j] for i in range(a)], dtype=np.int64)
        if style.error_bars is ErrorBars.SD:
            error = np.array([cells.sd[i][j] for i in range(a)], dtype=np.float64)
        elif style.error_bars is ErrorBars.SEM:
            error = np.array([cells.sem[i][j] for i in range(a)], dtype=np.float64)
        else:
            error = np.full(a, np.nan)
        shown = np.isfinite(y)
        drawable = shown & _log_mask(y, y_scale)
        dropped += int(np.count_nonzero(shown & ~drawable))
        rows = np.flatnonzero(drawable).astype(np.int64)
        ys, es = y[rows], error[rows]
        py = _to_plot(ys, y_scale)
        low, high = _error_extent(ys, es, py, y_scale)
        positions = rows.astype(np.float64)
        out.append(
            XYSeriesData(
                source=source,
                name=name,
                px=positions,
                py=py,
                err_low=low,
                err_high=high,
                rows=rows,
                x=positions,
                y=ys,
                error=es,
                n=n[rows],
                replicates=np.empty((len(rows), 0)),
                error_bars=style.error_bars,
            )
        )
    return tuple(out), dropped, cells.levels_a


def _steps(times: FloatArray, values: FloatArray) -> tuple[FloatArray, FloatArray]:
    """The corners of a step function that is 1 from 0 to the first time and drops to
    ``values[j]`` at ``times[j]``."""
    before = np.concatenate(([1.0], values[:-1]))
    x = np.concatenate(([0.0], np.repeat(times, 2)))
    y = np.concatenate(([1.0], np.column_stack((before, values)).ravel()))
    return x, y


def _survival(inputs: ChartInputs) -> tuple[tuple[SurvivalCurveData, ...], list[str]]:
    from curvis.analysis.survival.km import kaplan_meier

    spec = inputs.spec
    x_scale, y_scale = spec.x_axis.scale, spec.y_axis.scale
    out: list[SurvivalCurveData] = []
    problems: list[str] = []
    for s in inputs.series:
        time, event = s.columns
        try:
            km = kaplan_meier(time, event)
        except ValueError as exc:
            problems.append(f"{s.name} is not drawn: {exc}")
            continue
        x, y = _steps(km.time, km.survival)
        _, low = _steps(km.time, km.lower)
        _, high = _steps(km.time, km.upper)
        keep = _log_mask(x, x_scale) & _log_mask(y, y_scale)
        if y_scale is Scale.LOG:
            low = np.where(low > 0, low, np.nan)
            high = np.where(high > 0, high, np.nan)
        censored = km.n_censor > 0
        cx, cy = km.time[censored], km.survival[censored]
        ckeep = _log_mask(cx, x_scale) & _log_mask(cy, y_scale)
        out.append(
            SurvivalCurveData(
                source=s.source,
                name=s.name,
                px=_to_plot(x[keep], x_scale),
                py=_to_plot(y[keep], y_scale),
                low=_to_plot(low[keep], y_scale),
                high=_to_plot(high[keep], y_scale),
                censor_px=_to_plot(cx[ckeep], x_scale),
                censor_py=_to_plot(cy[ckeep], y_scale),
                n=km.n,
                n_events=km.n_events,
                median=km.median,
            )
        )
    return tuple(out), problems


def _curves(inputs: ChartInputs) -> tuple[CurveData, ...]:
    spec = inputs.spec
    x_scale, y_scale = spec.x_axis.scale, spec.y_axis.scale
    out: list[CurveData] = []
    for item in inputs.curves:
        c = item.curve
        x, y = np.asarray(c.x, dtype=np.float64), np.asarray(c.y, dtype=np.float64)
        low, high = np.asarray(c.lower, dtype=np.float64), np.asarray(c.upper, dtype=np.float64)
        keep = _log_mask(x, x_scale) & _log_mask(y, y_scale)
        if y_scale is Scale.LOG:
            low = np.where(low > 0, low, np.nan)
            high = np.where(high > 0, high, np.nan)
        out.append(
            CurveData(
                c.series_id,
                c.name,
                _to_plot(x[keep], x_scale),
                _to_plot(y[keep], y_scale),
                _to_plot(low[keep], y_scale),
                _to_plot(high[keep], y_scale),
                item.show_band,
            )
        )
    return tuple(out)


def _categories(inputs: ChartInputs) -> tuple[CategoryData, ...]:
    spec = inputs.spec
    y_scale = spec.y_axis.scale
    out: list[CategoryData] = []
    for position, s in enumerate(inputs.series):
        style = spec.series_for(s.source)
        assert style is not None
        values = np.concatenate(s.columns) if s.columns else np.empty(0)
        mean, sd, sem, n = pooled_stats(values)
        error = {ErrorBars.SD: sd, ErrorBars.SEM: sem}.get(style.error_bars, math.nan)
        box = box_stats(values)
        if y_scale is Scale.LOG and spec.variant is ChartVariant.BOX:
            plot_box = box_stats(np.log10(values[values > 0]))
        else:
            plot_box = box
        size = error if math.isfinite(error) and math.isfinite(mean) else 0.0
        err_low, err_high = (size, 0.0) if mean < 0 else (0.0, size)
        out.append(
            CategoryData(
                s.source,
                s.name,
                position,
                mean,
                error,
                n,
                box,
                plot_box,
                style.error_bars,
                err_low,
                err_high,
            )
        )
    return tuple(out)


__all__ = (
    "BoxStats",
    "CategoryData",
    "ChartData",
    "ChartInputs",
    "CurveData",
    "XYSeriesData",
    "box_stats",
    "compute",
    "gather",
)
