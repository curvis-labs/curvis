"""The page and axes of a graph, the same for the screen and for export.

Both renderers (:mod:`curvis.charts.render_qt` and :mod:`curvis.charts.render_mpl`) take
the view ranges, the tick positions and the tick labels from :func:`axes_layout`, the
page size from :func:`page_px` and the font from :mod:`curvis.charts.fonts`. So an exported
graph shows the same ranges and ticks as the Graph panel, and the image-diff tests can
compare the two renderers.

- **Ranges.** XY: the points of the shown series with their error bars and the fitted
  curves (and bands), padded by 5 % on each side. Bar: the categories from −0.6 to
  n − 0.4, and Y from 0 (or the lowest bar) to 5 % above the highest bar or error bar.
  Box: the whiskers, padded by 5 %. A range the user typed (From / To) replaces the
  automatic end it gives; on a log axis it is converted to log10, like the plot data.
- **Ticks.** Linear axes: steps of 1, 2 or 5 × 10^k, about five of them. Log axes: one
  major tick per decade, minor ticks at 2–9; a range shorter than a decade gets linear
  steps of the values instead. On a small page there are fewer ticks (every second or
  third decade), so the labels do not run into each other: both renderers then show
  every label, where pyqtgraph would hide some and Matplotlib would overlap them. Labels
  read like the table (0.01, 1, 100); very large or small values use exponents (1e+08).
- **Survival.** Like XY: the steps of the shown curves (from time 0 and survival 1)
  and, when drawn, their bands, padded by 5 %.
- **Categories.** The shown bars, boxes, violins or columns of values are numbered
  0, 1, 2 … from the left, so a hidden series leaves no gap. Grouped graphs put the
  clusters at 0, 1, 2 … and share each one's width between its places
  (:mod:`curvis.charts.grouping`). A horizontal graph puts the categories on the Y
  axis, the first at the top, and its values along the X axis. The value axis covers
  everything drawn: bars from 0, boxes and violins whole, and individual values.
  Interaction graphs put the levels of the first factor at 0, 1, 2 … (from −0.5 to
  n − 0.5); their Y range is that of XY graphs.
- **Histograms.** X spans the bins exactly; Y the counts from 0.

Pure functions, no Qt and no Matplotlib: the renderers and the tests import it freely.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from curvis.charts.data import CategoryData, ChartData, value_axis_scale
from curvis.charts.fonts import FONTS, chart_font_info
from curvis.charts.grouping import Entry, grouped_layout
from curvis.charts.spec import (
    GROUPABLE_VARIANTS,
    HORIZONTAL_VARIANTS,
    AxisSpec,
    ChartFamily,
    ChartSpec,
    ChartVariant,
    Grouping,
    LabelAngle,
    Scale,
)

SCREEN_DPI = 96.0
"""Pixels per inch of a page drawn at scale 1 (Qt's logical dpi, and the image diffs)."""
CM_PER_INCH = 2.54
PADDING = 0.05
"""Share of the data span added at each end of an automatic range."""
BAR_WIDTH = 0.6
BOX_WIDTH = 0.5
VIOLIN_WIDTH = 0.8
SCATTER_WIDTH = 0.6
"""Width a column of values may spread over (column scatter, individual values)."""
CLUSTER_WIDTH = 0.8
"""Width of a cluster of a grouped graph; its places share it."""
CAP_SHARE = 0.012
"""Width of an XY error bar's cap, as a share of the X range."""
TARGET_TICKS = 5
MAX_DECADES = 8
CHAR_WIDTH_EM = FONTS["dejavu_sans"].char_width_em
"""Average width of a character of DejaVu Sans, in ems: to tell how many tick labels
fit. Each font has its own (:attr:`ChartFont.char_width_em`)."""
AXIS_SHARE = 0.72
"""Share of the page that an axis spans, roughly (the rest holds labels and titles)."""
LEGEND_SHARE = 0.25
"""Share of the page width a legend takes, roughly."""

FONT_FAMILY = FONTS["dejavu_sans"].family
"""The default chart font; a graph names its own (``ChartSpec.font``)."""

# Line widths and lengths in points, shared by both renderers.
AXIS_WIDTH_PT = 0.75
TICK_LENGTH_PT = 3.75
MINOR_TICK_LENGTH_PT = 2.5
ERROR_BAR_WIDTH_PT = 0.75
BOX_LINE_PT = 1.125
TITLE_SCALE = 1.2
"""The title is this many times the chart text size."""
TITLE_PAD_PT = 6.0
"""Space between the title's baseline and the top of the plot (Matplotlib's
``axes.titlepad``)."""
FONT_ASCENT_EM = FONTS["dejavu_sans"].ascent_em
"""DejaVu Sans's typographic ascender (OS/2 ``sTypoAscender`` over ``unitsPerEm``).
Matplotlib makes a line of text at least this tall above its baseline: the title's
baseline sits this far (or the height of its tallest letter, if more) below the top of
the page's padding, and an axis title's or tick label's below the top of its line.
Each chart font has its own (:attr:`ChartFont.ascent_em`)."""
FONT_DESCENT_EM = FONTS["dejavu_sans"].descent_em
"""DejaVu Sans's typographic descender: a line of text reaches at least this far below
its baseline. Matplotlib's line of text is ascent + descent (1 em for DejaVu Sans)
high, where Qt's is the font's line height (1.16 em); the Graph panel lays out axis
titles and turned tick labels with Matplotlib's."""
FONT_LINE_GAP_EM = FONTS["dejavu_sans"].line_gap_em
"""DejaVu Sans's typographic line gap. Matplotlib adds half of it above and below each
line of a text of several lines: the lines of a wrapped title are 1.2 em apart."""
ELLIPSIS = "…"
LEGEND_SAMPLE_EM = 1.6
"""Width of a legend entry's sample (symbol, line or patch), in ems of the text size."""
LEGEND_ROW_EM = 1.5
"""Height of a legend row, in ems."""
LEGEND_GAP_EM = 0.4
"""Space between a legend sample and its name, in ems."""
LEGEND_BORDER_EM = (LEGEND_ROW_EM - 1.0) / 2
"""Space inside the legend's edge, all round, in ems: half a row's extra height."""
PAGE_PAD_PT = 2.0
"""Space around everything drawn on the page: between the page edge and the plot or
legend, and on each side of the gap between the plot and the legend."""
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
    label_angle: float = 0.0
    """Degrees the tick labels are turned (counter-clockwise; X axis only)."""


@dataclass(frozen=True)
class Room:
    """How much room an axis has for tick labels, roughly, in points."""

    length: float
    text_size: float
    horizontal: bool
    char_em: float = CHAR_WIDTH_EM
    """Average width of a character of the chart font, in ems."""


@dataclass(frozen=True)
class AxesLayout:
    x: AxisLayout
    y: AxisLayout
    positions: dict[str, float] = field(default_factory=dict)
    """Bar-family graphs: where each shown series is drawn on the category axis, by
    source."""
    slot: float = 1.0
    """Bar-family graphs: the width each series' place has (1 unless grouped)."""
    horizontal: bool = False
    """The categories are on the Y axis (horizontal bars)."""


# --- page --------------------------------------------------------------------------------


def points_to_px(points: float, dpi: float = SCREEN_DPI) -> float:
    return points * dpi / 72.0


def page_px(spec: ChartSpec, dpi: float = SCREEN_DPI) -> tuple[int, int]:
    """The graph's page in whole pixels at ``dpi``, cut off as Matplotlib does."""
    width, height = spec.page_size_cm
    return (
        max(1, int(width / CM_PER_INCH * dpi)),
        max(1, int(height / CM_PER_INCH * dpi)),
    )


def chart_font_path(font_id: str = "dejavu_sans") -> Path | None:
    """The file of the chart font ``font_id`` (bundled, or Matplotlib's DejaVu Sans)."""
    return chart_font_info(font_id).path()


# --- layout ------------------------------------------------------------------------------


def shown_categories(spec: ChartSpec, data: ChartData | None) -> list[str]:
    """Sources of the bars or boxes drawn, left to right."""
    if data is None:
        return []
    visible = {s.source for s in spec.series if s.visible}
    return [c.source for c in data.categories if c.source in visible]


def is_grouped(spec: ChartSpec) -> bool:
    return spec.grouping is not Grouping.NONE and spec.variant in GROUPABLE_VARIANTS


def grouped_entries(spec: ChartSpec, data: ChartData | None) -> list[Entry]:
    """The shown series of a bar-family graph as :mod:`curvis.charts.grouping` sees them."""
    shown = set(shown_categories(spec, data))
    cats = data.categories if data is not None else ()
    return [Entry(c.source, c.name, c.category) for c in cats if c.source in shown]


def axes_layout(spec: ChartSpec, data: ChartData | None) -> AxesLayout:
    """The ranges and ticks of ``spec`` drawing ``data`` (None: the frame only)."""
    if spec.family is ChartFamily.BAR:
        if spec.variant is ChartVariant.HISTOGRAM:
            return _histogram_layout(spec, data)
        if spec.variant in HORIZONTAL_VARIANTS:
            return _horizontal_layout(spec, data)
        x, positions, slot = _category_axis(spec, data)
    elif spec.family is ChartFamily.INTERACTION:
        bounds = _xy_bounds(spec, data)
        x = _level_axis(data)
    else:
        bounds = _xy_bounds(spec, data)
        x = _axis(spec.x_axis, _pad(bounds[0], bounds[1]), room=_room(spec, horizontal=True))
    x_room = _room(spec, horizontal=True)
    angle = label_angle(spec.x_axis, x, x_room)
    x = _shorten(replace(x, label_angle=angle), x_room, spec.page_size_cm[1] / CM_PER_INCH * 72)
    y_room = _room(spec, horizontal=False)
    y_room = replace(y_room, length=max(0.0, y_room.length - _label_depth(x, x_room)))
    if spec.family is ChartFamily.BAR:
        y = _value_axis(spec, spec.y_axis, data, positions, y_room)
        return AxesLayout(x, y, positions, slot)
    y = _axis(spec.y_axis, _pad(bounds[2], bounds[3]), room=y_room)
    return AxesLayout(x, y, {})


def _horizontal_layout(spec: ChartSpec, data: ChartData | None) -> AxesLayout:
    """Categories on the Y axis (the first at the top), values along X."""
    category, positions, slot = _category_axis(spec, data)
    top = category.high + category.low  # flip: the first category at the top
    positions = {s: top - p for s, p in positions.items()}
    ticks = tuple(Tick(top - t.value, t.label) for t in category.major)
    width = spec.page_size_cm[0] / CM_PER_INCH * 72.0
    y = _shorten_side(replace(category, major=ticks), spec, LABEL_WIDTH_SHARE * width)
    x_room = _room(spec, horizontal=True)
    x = _value_axis(spec, spec.x_axis, data, positions, x_room)
    angle = label_angle(spec.x_axis, x, x_room)
    x = replace(x, label_angle=angle)
    return AxesLayout(x, y, positions, slot, horizontal=True)


def _histogram_layout(spec: ChartSpec, data: ChartData | None) -> AxesLayout:
    """X spans the bins exactly, Y the counts from 0 (both linear)."""
    hist = data.histogram if data is not None else None
    visible = {s.source for s in spec.series if s.visible}
    if hist is not None and len(hist.breaks):
        low, high = float(hist.breaks[0]), float(hist.breaks[-1])
        tallest = max(
            (int(c.max()) for source, c in hist.counts if source in visible and len(c)),
            default=0,
        )
    else:
        low, high, tallest = 0.0, 1.0, 0
    if not high > low:
        low, high = _pad(low, high)
    x_room = _room(spec, horizontal=True)
    x = _axis(spec.x_axis, (low, high), log=False, room=x_room)
    x = replace(x, label_angle=label_angle(spec.x_axis, x, x_room))
    y_room = _room(spec, horizontal=False)
    y_room = replace(y_room, length=max(0.0, y_room.length - _label_depth(x, x_room)))
    top = max(tallest, 1) * (1 + PADDING)
    y = _axis(spec.y_axis, (0.0, top), log=False, room=y_room)
    return AxesLayout(x, y, {})


LABEL_WIDTH_SHARE = 0.4
"""Category names on the Y axis may take at most this share of the page width; longer
ones are shortened with an ellipsis."""


def _shorten_side(axis: AxisLayout, spec: ChartSpec, width: float) -> AxisLayout:
    """Labels of a vertical axis wider than ``width`` points, shortened with "…"."""
    char = chart_font_info(spec.font).char_width_em * spec.text_size
    longest = max(3, int(width / char))
    if all(len(t.label) <= longest for t in axis.major):
        return axis
    ticks = tuple(
        Tick(t.value, t.label if len(t.label) <= longest else t.label[: longest - 1] + ELLIPSIS)
        for t in axis.major
    )
    return replace(axis, major=ticks)


def _label_depth(axis: AxisLayout, room: Room) -> float:
    """How much further down turned tick labels reach than level ones, in points."""
    if not axis.label_angle:
        return 0.0
    widest = max((len(t.label) for t in axis.major), default=0) * room.char_em * room.text_size
    return max(0.0, widest * math.sin(math.radians(axis.label_angle)) - room.text_size)


LEGEND_RIGHT_SHARE = 0.45
"""A legend wider than this share of the page goes under the plot instead."""
LEGEND_COLUMN_GAP_EM = 1.0
"""Space between the columns of a legend under the plot, in ems."""
LABEL_DEPTH_SHARE = 0.45
"""Turned tick labels may take at most this share of the page height; longer ones are
shortened with an ellipsis."""


def legend_entry_width(name: str, text_size: float, char_em: float = CHAR_WIDTH_EM) -> float:
    """Rough width of one legend entry (sample, gap and name), in points."""
    return (LEGEND_SAMPLE_EM + LEGEND_GAP_EM + len(name) * char_em) * text_size


def legend_columns(spec: ChartSpec, names: Sequence[str]) -> int:
    """Where the legend goes: 0 to the right of the plot (one column), otherwise under
    the plot in this many columns. It goes under the plot when, at the right, it would
    take more than :data:`LEGEND_RIGHT_SHARE` of the page width."""
    if not names:
        return 0
    width = spec.page_size_cm[0] / CM_PER_INCH * 72.0
    text = spec.text_size
    char = chart_font_info(spec.font).char_width_em
    entry = max(legend_entry_width(n, text, char) for n in names)
    if entry + 2 * LEGEND_BORDER_EM * text <= LEGEND_RIGHT_SHARE * width:
        return 0
    gap = LEGEND_COLUMN_GAP_EM * text
    fit = max(1, int((width - 2 * LEGEND_BORDER_EM * text + gap) // (entry + gap)))
    columns = min(len(names), fit)
    rows = math.ceil(len(names) / columns)
    return math.ceil(len(names) / rows)  # balanced: no column shorter by more than one


def column_major(count: int, columns: int) -> list[int]:
    """The entries ``0 … count − 1`` laid out row by row in ``columns`` columns, in the
    order of a column-by-column layout (Matplotlib's legend) that puts them in the same
    places."""
    rows = math.ceil(count / columns) if columns else 0
    order = []
    for column in range(columns):
        for row in range(rows):
            index = row * columns + column
            if index < count:
                order.append(index)
    return order


def _shorten(axis: AxisLayout, room: Room, height: float) -> AxisLayout:
    """Turned labels that would reach deeper than :data:`LABEL_DEPTH_SHARE` of the page
    ``height`` (points), shortened with an ellipsis."""
    if not axis.label_angle:
        return axis
    reach = math.sin(math.radians(axis.label_angle)) * room.char_em * room.text_size
    longest = max(3, int(LABEL_DEPTH_SHARE * height / reach))
    if all(len(t.label) <= longest for t in axis.major):
        return axis
    ticks = tuple(
        Tick(t.value, t.label if len(t.label) <= longest else t.label[: longest - 1] + "…")
        for t in axis.major
    )
    return replace(axis, major=ticks)


def shorten_to_fit(text: str, width: float, measure: Callable[[str], float]) -> str:
    """``text`` whole if it fits in ``width``, otherwise its longest beginning that fits
    with an ellipsis after it (just the ellipsis if nothing does). ``measure`` gives the
    width of a text in the units of ``width``; each renderer measures with its own font
    engine, both on DejaVu Sans, so they shorten a title alike (DESIGN.md: the page
    leaves room for everything it shows)."""
    if not text or measure(text) <= width:
        return text
    low, high = 0, len(text) - 1  # the longest beginning that fits is text[:low]
    while low < high:
        middle = (low + high + 1) // 2
        if measure(text[:middle].rstrip() + ELLIPSIS) <= width:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + ELLIPSIS


def wrap_to_fit(text: str, width: float, measure: Callable[[str], float]) -> tuple[str, ...]:
    """``text`` broken into lines no wider than ``width``, between words, filling each
    line before starting the next. A single word wider than ``width`` gets a line of its
    own, shortened with an ellipsis (:func:`shorten_to_fit`). ``measure`` gives the width
    of a text in the units of ``width``; each renderer measures with its own font engine,
    both on DejaVu Sans, so they break a title alike (DESIGN.md: the page leaves room for
    everything it shows)."""
    words = text.split()
    if not words:
        return ()
    if measure(" ".join(words)) <= width:
        return (" ".join(words),)
    lines: list[str] = []
    line = ""
    for word in words:
        joined = f"{line} {word}" if line else word
        if not line or measure(joined) <= width:
            line = joined
            continue
        lines.append(line)
        line = word
    lines.append(line)
    return tuple(shorten_to_fit(line, width, measure) for line in lines)


ANGLED_PITCH_EM = 1.6
"""Space along the axis that a label turned by 45° needs, in ems of the text size."""


def label_angle(axis: AxisSpec, layout: AxisLayout, room: Room) -> float:
    """Degrees to turn the X tick labels: the axis' choice, or for Auto 0° when the
    labels fit side by side, 45° when they do not and 90° when even that is too tight."""
    if axis.label_angle is not LabelAngle.AUTO:
        return float(axis.label_angle.value)
    labels = [t.label for t in layout.major if t.label]
    if not axis.show_labels or len(labels) < 2:
        return 0.0
    span = layout.high - layout.low
    values = sorted(t.value for t in layout.major)
    gap = min(b - a for a, b in itertools.pairwise(values))
    pitch = room.length * gap / span if span > 0 else room.length
    text = room.text_size
    widest = max(len(label) for label in labels) * room.char_em * text
    if widest + 0.5 * text <= pitch:
        return 0.0
    return 45.0 if ANGLED_PITCH_EM * text <= pitch else 90.0


def _room(spec: ChartSpec, *, horizontal: bool) -> Room:
    """Room for the tick labels of the X (``horizontal``) or Y axis of ``spec``."""
    width, height = (size / CM_PER_INCH * 72.0 for size in spec.page_size_cm)
    if horizontal:
        length = width * (AXIS_SHARE - (LEGEND_SHARE if spec.show_legend else 0.0))
    else:
        length = height * AXIS_SHARE
    return Room(length, spec.text_size, horizontal, chart_font_info(spec.font).char_width_em)


def _fits(ticks: Sequence[Tick], room: Room | None) -> bool:
    """True when ``ticks``' labels fit along the axis without running into each other."""
    if room is None or len(ticks) < 2:
        return True
    text = room.text_size
    if room.horizontal:
        need = max(len(t.label) for t in ticks) * room.char_em * text + text
    else:
        need = 1.6 * text
    return (len(ticks) - 1) * need <= room.length


def _category_axis(
    spec: ChartSpec, data: ChartData | None
) -> tuple[AxisLayout, dict[str, float], float]:
    """The category axis (upright: the first category at 0), where each shown series is
    drawn on it and the width of its place."""
    if is_grouped(spec):
        found = grouped_layout(grouped_entries(spec, data), spec.grouping)
        slot = CLUSTER_WIDTH / max(found.slots, 1)
        positions = {
            p.source: p.cluster - CLUSTER_WIDTH / 2 + (p.slot + 0.5) * slot for p in found.places
        }
        n = max(len(found.clusters), 1)
        ticks = tuple(Tick(float(i), name) for i, name in enumerate(found.clusters))
        return AxisLayout(-0.6, n - 0.4, ticks), positions, slot
    sources = shown_categories(spec, data)
    names = {c.source: c.name for c in data.categories} if data is not None else {}
    n = max(len(sources), 1)
    ticks = tuple(Tick(float(i), names.get(s, "")) for i, s in enumerate(sources))
    return AxisLayout(-0.6, n - 0.4, ticks), {s: float(i) for i, s in enumerate(sources)}, 1.0


def _level_axis(data: ChartData | None) -> AxisLayout:
    """Interaction graphs: the levels of the first factor at 0, 1, 2 …, with half a step
    of room at each end."""
    levels = data.levels if data is not None else ()
    n = max(len(levels), 1)
    return AxisLayout(-0.5, n - 0.5, tuple(Tick(float(i), name) for i, name in enumerate(levels)))


def _value_axis(
    spec: ChartSpec,
    axis: AxisSpec,
    data: ChartData | None,
    positions: dict[str, float],
    room: Room,
) -> AxisLayout:
    """The value axis of a bar-family graph: it covers everything drawn."""
    shown: list[CategoryData] = (
        [c for c in data.categories if c.source in positions] if data is not None else []
    )
    log = value_axis_scale(spec) is Scale.LOG
    points = [c.values for c in shown if len(c.values)]
    variant = spec.variant
    if variant in (ChartVariant.BAR, ChartVariant.HORIZONTAL_BAR):
        values: list[float] = []
        for c in shown:
            if math.isfinite(c.mean):
                values += [c.mean, c.mean - c.err_low, c.mean + c.err_high]
        low, high = _bounds(points)
        values += [v for v in (low, high) if math.isfinite(v)]
        top = max([0.0, *values])
        bottom = min([0.0, *values])
        auto = (bottom, top + PADDING * ((top - bottom) or 1.0))
        # A log axis cannot show 0: the Format dialog keeps bars linear.
        return _axis(axis, auto, log=False, room=room)
    found: list[np.ndarray] | list[float] = list(points)
    extra: list[float] = []
    for c in shown:
        if c.plot_box.n:
            extra += [c.plot_box.min, c.plot_box.max]
        if c.violin is not None and len(c.violin.grid):
            extra += [float(c.violin.grid[0]), float(c.violin.grid[-1])]
        if variant is ChartVariant.COLUMN_SCATTER:
            extra += [v for v in (c.plot_mean, c.plot_low, c.plot_high) if math.isfinite(v)]
    low, high = _bounds([*found, np.asarray(extra, dtype=np.float64)])
    return _axis(axis, _pad(low, high), log=log, room=room)


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
        for km in data.survival:
            if km.source not in visible or len(km.px) == 0:
                continue
            xs.append(km.px)
            ys.append(km.py)
            if spec.show_bands:
                ys += [km.low, km.high]
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


def _axis(
    axis: AxisSpec,
    auto: tuple[float, float],
    *,
    log: bool | None = None,
    room: Room | None = None,
) -> AxisLayout:
    """The layout of ``axis`` with the automatic range ``auto`` (plot coordinates);
    ``room`` thins the ticks to what fits."""
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
        for every in range(1, 13):
            major, minor = log_ticks(low, high, every)
            if len(major) <= 2 or _fits(major, room):
                break
        return AxisLayout(low, high, major, minor)
    for target in range(TARGET_TICKS, 1, -1):
        ticks = linear_ticks(low, high, target)
        if len(ticks) <= 2 or _fits(ticks, room):
            break
    return AxisLayout(low, high, ticks)


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


def linear_ticks(low: float, high: float, target: int = TARGET_TICKS) -> tuple[Tick, ...]:
    step = nice_step(high - low, target)
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


def log_ticks(
    low: float, high: float, every: int = 1
) -> tuple[tuple[Tick, ...], tuple[float, ...]]:
    """Major and minor ticks of a log axis whose range is ``low``–``high`` in log10, with
    a major tick every ``every`` decades (at least every ``MAX_DECADES``-th)."""
    first, last = math.ceil(low - 1e-9), math.floor(high + 1e-9)
    if last - first < 1:
        # Less than a decade: linear steps of the values, placed at their log10.
        values = linear_ticks(10.0**low, 10.0**high, max(2, TARGET_TICKS + 1 - every))
        major = tuple(Tick(math.log10(t.value), log_label(t.value)) for t in values if t.value > 0)
        return major, ()
    every = max(every, math.ceil((last - first) / MAX_DECADES))
    major = tuple(Tick(float(d), log_label(10.0**d)) for d in range(first, last + 1, every))
    minor: list[float] = []
    for d in range(first - 1, last + 1):
        for k in range(2, 10) if every == 1 else range(1, 2):
            v = d + math.log10(k)
            if low <= v <= high and (every == 1 or (d - first) % every):
                minor.append(v)
    return major, tuple(minor)


__all__ = (
    "AxesLayout",
    "AxisLayout",
    "Tick",
    "axes_layout",
    "chart_font_path",
    "is_grouped",
    "linear_ticks",
    "log_ticks",
    "page_px",
    "points_to_px",
    "shorten_to_fit",
    "wrap_to_fit",
)
