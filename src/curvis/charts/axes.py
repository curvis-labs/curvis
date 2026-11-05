"""The page and axes of a graph, the same for the screen and for export.

Both renderers (:mod:`curvis.charts.render_qt` and :mod:`curvis.charts.render_mpl`) take
the view ranges, the tick positions and the tick labels from :func:`axes_layout`, the
page size from :func:`page_px` and the font from :func:`chart_font_path`. So an exported
graph shows the same ranges and ticks as the Graph panel, and the image-diff tests can
compare the two renderers.

- **Ranges.** XY: the points of the shown series with their error bars and the fitted
  curves (and bands), padded by 5 % on each side. Bar: the categories from −0.6 to
  n − 0.4, and Y from 0 (or the lowest bar) to 5 % above the highest bar or error bar.
  Box: the whiskers, padded by 5 %. A range the user typed (From / To) replaces the
  automatic end it gives; on a log axis it is converted to log10, like the plot data.
- **Ticks.** Linear axes: steps of 1, 2 or 5 × 10^k, about five of them. Log axes: one
  major tick per decade (every second decade beyond eight), minor ticks at 2–9; a range
  shorter than a decade gets linear steps of the values instead. Labels read like the
  table (0.01, 1, 100); very large or small values use exponents (1e+08).
- **Categories.** The shown bars or boxes are numbered 0, 1, 2 … from the left, so a
  hidden series leaves no gap.

Pure functions, no Qt and no Matplotlib: the renderers and the tests import it freely.
"""

from __future__ import annotations

import importlib.util
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from curvis.charts.data import ChartData
from curvis.charts.spec import AxisSpec, ChartFamily, ChartSpec, ChartVariant, Scale

SCREEN_DPI = 96.0
"""Pixels per inch of a page drawn at scale 1 (Qt's logical dpi, and the image diffs)."""
CM_PER_INCH = 2.54
PADDING = 0.05
"""Share of the data span added at each end of an automatic range."""
BAR_WIDTH = 0.6
BOX_WIDTH = 0.5
CAP_SHARE = 0.012
"""Width of an XY error bar's cap, as a share of the X range."""
TARGET_TICKS = 5
MAX_DECADES = 8

FONT_FAMILY = "DejaVu Sans"
"""The chart font in both renderers (Matplotlib ships it, Qt loads the same file)."""

# Line widths and lengths in points, shared by both renderers.
AXIS_WIDTH_PT = 0.75
TICK_LENGTH_PT = 3.75
MINOR_TICK_LENGTH_PT = 2.5
ERROR_BAR_WIDTH_PT = 0.75
BOX_LINE_PT = 1.125
TITLE_SCALE = 1.2
"""The title is this many times the chart text size."""
BAND_ALPHA = 0.18
BOX_FILL_ALPHA = 0.25


@dataclass(frozen=True)
class Tick:
    value: float
    """Position in plot coordinates (log10 on a log axis)."""
    label: str


@dataclass(frozen=True)
class AxisLayout:
    low: float
    high: float
    major: tuple[Tick, ...]
    minor: tuple[float, ...] = ()


@dataclass(frozen=True)
class AxesLayout:
    x: AxisLayout
    y: AxisLayout
    positions: dict[str, int]
    """Bar and box graphs: the position of each shown series, by source."""


# --- page --------------------------------------------------------------------------------


def points_to_px(points: float, dpi: float = SCREEN_DPI) -> float:
    return points * dpi / 72.0


def page_px(spec: ChartSpec, dpi: float = SCREEN_DPI) -> tuple[int, int]:
    """The graph's page in pixels at ``dpi``."""
    width, height = spec.page_size_cm
    return (
        max(1, round(width / CM_PER_INCH * dpi)),
        max(1, round(height / CM_PER_INCH * dpi)),
    )


def chart_font_path() -> Path | None:
    """DejaVu Sans as shipped with Matplotlib, found without importing Matplotlib."""
    spec = importlib.util.find_spec("matplotlib")
    if spec is None or not spec.submodule_search_locations:
        return None
    for location in spec.submodule_search_locations:
        path = Path(location) / "mpl-data" / "fonts" / "ttf" / "DejaVuSans.ttf"
        if path.is_file():
            return path
    return None


# --- layout ------------------------------------------------------------------------------


def shown_categories(spec: ChartSpec, data: ChartData | None) -> list[str]:
    """Sources of the bars or boxes drawn, left to right."""
    if data is None:
        return []
    visible = {s.source for s in spec.series if s.visible}
    return [c.source for c in data.categories if c.source in visible]


def axes_layout(spec: ChartSpec, data: ChartData | None) -> AxesLayout:
    """The ranges and ticks of ``spec`` drawing ``data`` (None: the frame only)."""
    if spec.family is ChartFamily.BAR:
        return _category_layout(spec, data)
    x_low, x_high, y_low, y_high = _xy_bounds(spec, data)
    x = _axis(spec.x_axis, _pad(x_low, x_high))
    y = _axis(spec.y_axis, _pad(y_low, y_high))
    return AxesLayout(x, y, {})


def _category_layout(spec: ChartSpec, data: ChartData | None) -> AxesLayout:
    sources = shown_categories(spec, data)
    positions = {source: i for i, source in enumerate(sources)}
    names = {c.source: c.name for c in data.categories} if data is not None else {}
    n = max(len(sources), 1)
    x = AxisLayout(
        -0.6, n - 0.4, tuple(Tick(float(i), names.get(s, "")) for s, i in positions.items())
    )
    shown = [c for c in data.categories if c.source in positions] if data is not None else []
    if spec.variant is ChartVariant.BOX:
        values = [v for c in shown if c.plot_box.n for v in (c.plot_box.min, c.plot_box.max)]
        low, high = _bounds(values)
        y = _axis(spec.y_axis, _pad(low, high), log=spec.y_axis.scale is Scale.LOG)
        return AxesLayout(x, y, positions)
    values = []
    for c in shown:
        if math.isfinite(c.mean):
            values += [c.mean, c.mean - c.err_low, c.mean + c.err_high]
    top = max([0.0, *values])
    bottom = min([0.0, *values])
    auto = (bottom, top + PADDING * ((top - bottom) or 1.0))
    # Bars start at 0, which a log axis cannot show: the Format dialog keeps bars linear.
    y = _axis(spec.y_axis, auto, log=False)
    return AxesLayout(x, y, positions)


def _xy_bounds(spec: ChartSpec, data: ChartData | None) -> tuple[float, float, float, float]:
    xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    if data is not None:
        visible = {s.source for s in spec.series if s.visible}
        for series in data.xy:
            if series.source not in visible or len(series.px) == 0:
                continue
            xs.append(series.px)
            ys.append(series.py)
            with np.errstate(invalid="ignore"):
                ys.append(series.py + np.where(np.isfinite(series.err_high), series.err_high, 0))
                ys.append(series.py - np.where(np.isfinite(series.err_low), series.err_low, 0))
        for curve in data.curves:
            if curve.series_source not in visible or len(curve.px) == 0:
                continue
            xs.append(curve.px)
            ys.append(curve.py)
            if curve.show_band:
                ys += [curve.low, curve.high]
    x_low, x_high = _bounds(xs)
    y_low, y_high = _bounds(ys)
    return x_low, x_high, y_low, y_high


def _bounds(values: list[np.ndarray] | list[float]) -> tuple[float, float]:
    if not values:
        return math.nan, math.nan
    flat = np.concatenate([np.atleast_1d(np.asarray(v, dtype=np.float64)) for v in values])
    flat = flat[np.isfinite(flat)]
    if len(flat) == 0:
        return math.nan, math.nan
    return float(np.min(flat)), float(np.max(flat))


def _pad(low: float, high: float) -> tuple[float, float]:
    if not (math.isfinite(low) and math.isfinite(high)):
        return 0.0, 1.0
    span = high - low
    if span <= 0:
        half = abs(low) * 0.1 or 1.0
        return low - half, high + half
    return low - PADDING * span, high + PADDING * span


def _axis(axis: AxisSpec, auto: tuple[float, float], *, log: bool | None = None) -> AxisLayout:
    """The layout of ``axis`` with the automatic range ``auto`` (plot coordinates)."""
    is_log = axis.scale is Scale.LOG if log is None else log
    low, high = auto
    if axis.minimum is not None:
        low = math.log10(axis.minimum) if is_log else axis.minimum
    if axis.maximum is not None:
        high = math.log10(axis.maximum) if is_log else axis.maximum
    if not high > low:
        # A typed end on the wrong side of the automatic other end.
        if axis.minimum is not None and axis.maximum is None:
            high = low + max(abs(low) * 0.1, 1.0)
        else:
            low = high - max(abs(high) * 0.1, 1.0)
    if is_log:
        major, minor = log_ticks(low, high)
    else:
        major, minor = linear_ticks(low, high), ()
    return AxisLayout(low, high, major, minor)


# --- ticks -------------------------------------------------------------------------------


def nice_step(span: float, target: int = TARGET_TICKS) -> float:
    """The 1, 2 or 5 × 10^k nearest to ``span / target``."""
    raw = span / max(target, 1)
    if raw <= 0 or not math.isfinite(raw):
        return 1.0
    power = 10.0 ** math.floor(math.log10(raw))
    ratio = raw / power
    factor = 1.0 if ratio < 1.5 else 2.0 if ratio < 3.0 else 5.0 if ratio < 7.0 else 10.0
    return factor * power


def linear_ticks(low: float, high: float) -> tuple[Tick, ...]:
    step = nice_step(high - low)
    first = math.ceil(low / step - 1e-9)
    last = math.floor(high / step + 1e-9)
    decimals = max(0, -math.floor(math.log10(step) + 1e-9))
    return tuple(Tick(i * step, format_tick(i * step, decimals)) for i in range(first, last + 1))


def format_tick(value: float, decimals: int) -> str:
    """``value`` with the decimals of its axis' step, so labels line up (0.0, 0.5, 1.0)."""
    if value != 0 and (abs(value) >= 1e7 or abs(value) < 1e-6):
        return f"{value:.0e}"
    text = f"{value:.{decimals}f}"
    return text.lstrip("-") if float(text) == 0 else text


def log_label(value: float) -> str:
    """A log-axis label for ``value`` (not log10): 0.01, 1, 100, 1e+08."""
    return f"{value:.6g}" if 1e-6 <= abs(value) < 1e7 else f"{value:.0e}"


def log_ticks(low: float, high: float) -> tuple[tuple[Tick, ...], tuple[float, ...]]:
    """Major and minor ticks of a log axis whose range is ``low``–``high`` in log10."""
    first, last = math.ceil(low - 1e-9), math.floor(high + 1e-9)
    if last - first < 1:
        # Less than a decade: linear steps of the values, placed at their log10.
        values = linear_ticks(10.0**low, 10.0**high)
        major = tuple(Tick(math.log10(t.value), log_label(t.value)) for t in values if t.value > 0)
        return major, ()
    every = 1 if last - first <= MAX_DECADES else math.ceil((last - first) / MAX_DECADES)
    decades = range(first, last + 1, every)
    major = tuple(Tick(float(d), log_label(10.0**d)) for d in decades)
    minor: list[float] = []
    if every == 1:
        for d in range(first - 1, last + 1):
            for k in range(2, 10):
                v = d + math.log10(k)
                if low <= v <= high:
                    minor.append(v)
    return major, tuple(minor)


__all__ = (
    "AxesLayout",
    "AxisLayout",
    "Tick",
    "axes_layout",
    "chart_font_path",
    "linear_ticks",
    "log_ticks",
    "page_px",
    "points_to_px",
)
