"""Tooltip text for a point, bar, box or survival curve of a graph.

Values typed in the table are shown exactly as in the table; values Curvis computed
(means, SD, SEM, quartiles) with the 4 significant digits of the Results panel.
"""

from __future__ import annotations

import math

import numpy as np

from curvis.analysis.result import format_number
from curvis.charts.data import CategoryData, HistogramData, SurvivalCurveData, XYSeriesData
from curvis.charts.spec import ChartVariant, ErrorBars

_INTEGER_LIMIT = 1e15


def typed(value: float) -> str:
    """A value as the table shows it: the shortest exact text."""
    if math.isnan(value):
        return ""
    if value.is_integer() and abs(value) < _INTEGER_LIMIT:
        return str(int(value))
    return repr(value)


def computed(value: float) -> str:
    return format_number(value)


def describe_point(data: XYSeriesData, index: int, x_name: str = "X") -> str:
    """The tooltip of point ``index`` of an XY series."""
    lines = [f"{data.name} · row {int(data.rows[index]) + 1}"]
    lines.append(f"{x_name}: {typed(float(data.x[index]))}")
    reps = data.replicates[index]
    error = float(data.error[index])
    if reps.shape[0] <= 1:
        lines.append(f"Y: {typed(float(data.y[index]))}")
        if data.error_bars is ErrorBars.ERROR_COLUMN and not math.isnan(error):
            lines.append(f"Error: {typed(error)}")
        return "\n".join(lines)
    lines.append(f"Mean: {computed(float(data.y[index]))}")
    if data.error_bars in (ErrorBars.SD, ErrorBars.SEM) and not math.isnan(error):
        lines.append(f"{data.error_bars.label}: {computed(error)}")
    lines.append(f"n: {int(data.n[index])}")
    lines.append("Values: " + ", ".join(typed(float(v)) for v in reps if not math.isnan(v)))
    return "\n".join(lines)


def describe_cell(data: XYSeriesData, index: int, level: str, factor: str = "") -> str:
    """The tooltip of point ``index`` of an interaction line: one cell of a two-way
    ANOVA (the line is a level of the second factor, ``level`` one of the first)."""
    lines = [f"{data.name} · {factor + ': ' if factor else ''}{level}"]
    lines.append(f"Mean: {computed(float(data.y[index]))}")
    error = float(data.error[index])
    if data.error_bars in (ErrorBars.SD, ErrorBars.SEM) and not math.isnan(error):
        lines.append(f"{data.error_bars.label}: {computed(error)}")
    lines.append(f"n: {int(data.n[index])}")
    return "\n".join(lines)


def describe_category(category: CategoryData, variant: ChartVariant) -> str:
    """The tooltip of a bar, box, violin or column of values."""
    name = category.name
    if category.category is not None:
        name = f"{name} · {category.category}"
    lines = [name]
    if variant in (ChartVariant.BOX, ChartVariant.VIOLIN) and category.box.n:
        b = category.box
        lines += [
            f"Maximum: {typed(b.max)}",
            f"Q3: {computed(b.q3)}",
            f"Median: {computed(b.median)}",
            f"Q1: {computed(b.q1)}",
            f"Minimum: {typed(b.min)}",
        ]
    else:
        lines.append(f"Mean: {computed(category.mean)}")
        if category.error_bars in (ErrorBars.SD, ErrorBars.SEM) and not math.isnan(category.error):
            lines.append(f"{category.error_bars.label}: {computed(category.error)}")
    lines.append(f"n: {category.n}")
    return "\n".join(lines)


def _span(curve: SurvivalCurveData, index: int, time_name: str) -> str:
    """The times a step of a survival curve covers: from its time to the next one."""
    times = curve.km.time
    start = 0.0 if index < 0 else float(times[index])
    if index + 1 < len(times):
        return f"{time_name}: {typed(start)} to {typed(float(times[index + 1]))}"
    return f"{time_name}: {typed(start)}"


def describe_step(curve: SurvivalCurveData, index: int, time_name: str = "Time") -> str:
    """The tooltip of a step of a survival curve: ``index`` is the time it starts at
    (an index into ``curve.km.time``), or -1 for the start of the curve (survival 1)."""
    km = curve.km
    lines = [curve.name, _span(curve, index, time_name)]
    if index < 0:
        lines += ["Survival: 1", f"At risk: {km.n}"]
        return "\n".join(lines)
    lines.append(f"Survival: {computed(float(km.survival[index]))}")
    low, high = float(km.lower[index]), float(km.upper[index])
    if not (math.isnan(low) or math.isnan(high)):
        lines.append(f"95% CI: {computed(low)} to {computed(high)}")
    lines.append(
        f"At risk: {int(km.n_risk[index])}, events: {int(km.n_event[index])}, "
        f"censored: {int(km.n_censor[index])}"
    )
    return "\n".join(lines)


def describe_censored(curve: SurvivalCurveData, index: int, time_name: str = "Time") -> str:
    """The tooltip of the censor tick at ``curve.km.time[index]``."""
    km = curve.km
    count = int(km.n_censor[index])
    subjects = "subject" if count == 1 else "subjects"
    return "\n".join(
        [
            f"{curve.name} · {count} censored {subjects}",
            f"{time_name}: {typed(float(km.time[index]))}",
            f"Survival: {computed(float(km.survival[index]))}",
            f"At risk: {int(km.n_risk[index])}",
        ]
    )


def step_at(curve: SurvivalCurveData, px: float) -> int:
    """The step of ``curve`` in effect at plot X ``px``: the index of the last time at
    or before it, or -1 before the first time."""
    return int(np.searchsorted(curve.ptime, px, side="right")) - 1


def describe_bin(histogram: HistogramData, source: str, index: int) -> str:
    """The tooltip of bin ``index`` of a histogram's series ``source``: its range (the
    right end included, and the left one for the first bin) and count."""
    names = dict(zip((s for s, _c in histogram.counts), histogram.names, strict=False))
    counts = dict(histogram.counts)[source]
    low, high = float(histogram.breaks[index]), float(histogram.breaks[index + 1])
    opening = "[" if index == 0 else "("
    lines = [names.get(source, "")] if names.get(source) else []
    lines.append(f"Bin: {opening}{computed(low)}, {computed(high)}]")
    lines.append(f"Count: {int(counts[index])}")
    return "\n".join(lines)
