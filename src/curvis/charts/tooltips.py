"""Tooltip text for a point, bar or box of a graph.

Values typed in the table are shown exactly as in the table; values Curvis computed
(means, SD, SEM, quartiles) with the 4 significant digits of the Results panel.
"""

from __future__ import annotations

import math

from curvis.analysis.result import format_number
from curvis.charts.data import CategoryData, XYSeriesData
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


def describe_category(category: CategoryData, variant: ChartVariant) -> str:
    """The tooltip of a bar or a box."""
    lines = [category.name]
    if variant is ChartVariant.BOX:
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
