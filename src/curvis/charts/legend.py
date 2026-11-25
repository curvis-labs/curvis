"""Where a graph's legend goes and how big it is, the same for the screen and for export.

Both renderers take the legend's place, text size, columns and names from
:func:`graph_layout`, so the Graph panel and an exported graph show the same legend.

- **Outside** (the default). Right of the plot, one column, at the chart text size,
  when it takes at most :data:`~curvis.charts.axes.LEGEND_RIGHT_SHARE` of the page width
  and fits the page height. Otherwise under the plot, in as many columns as fit the page
  width, at most :data:`LEGEND_BELOW_SHARE` of the page height tall, so the plot always
  keeps most of the page. A legend that would be taller first gets smaller text (down
  to :func:`smallest_text`), then names shortened in the middle with "…" so that more columns fit
  (down to :data:`LEGEND_MIN_CHARS` characters), and last only as many entries as fit,
  the rest counted in a last entry ("+12 more").
- **Inside.** In a corner of the plot, at most :data:`LEGEND_INSIDE_SHARE` of the plot
  wide and tall, in the first corner (upper right, upper left, lower right, lower left)
  where it covers no data: no point, line, error bar, band, bar, box or bracket comes
  within :data:`LEGEND_CLEAR_PT` of it. One column at the chart text size is tried in
  every corner first, then two and three columns, then the same with smaller text. When no corner
  is free the legend goes outside, as above.

The sizes are estimated from the number of characters (as the tick thinning of
:mod:`curvis.charts.axes` is), and the plot area from the page minus room for the
title, tick labels and axis titles; the estimates err towards a smaller plot, so an
inside legend keeps clear of the data.

Pure functions, no Qt and no Matplotlib.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from curvis.charts.axes import (
    AXIS_SHARE,
    CM_PER_INCH,
    LEGEND_BORDER_EM,
    LEGEND_COLUMN_GAP_EM,
    LEGEND_GAP_EM,
    LEGEND_RIGHT_SHARE,
    LEGEND_ROW_EM,
    LEGEND_SAMPLE_EM,
    LEGEND_SHARE,
    PAGE_PAD_PT,
    TICK_LENGTH_PT,
    TITLE_PAD_PT,
    TITLE_SCALE,
    AxesLayout,
    axes_layout,
    legend_entry_width,
)
from curvis.charts.data import ChartData
from curvis.charts.fonts import chart_font_info
from curvis.charts.marks import Marks, Polygon, Rect, bar_marks
from curvis.charts.spec import (
    LINE_VARIANTS,
    XY_VARIANTS,
    ChartFamily,
    ChartSpec,
    ChartVariant,
    LegendPosition,
    LineStyle,
)

FloatArray = np.ndarray

LEGEND_BELOW_SHARE = 0.3
"""A legend under the plot takes at most this share of the page height."""
LEGEND_MIN_TEXT_PT = 6.0
LEGEND_MIN_TEXT_SCALE = 0.75
"""A legend's text gets no smaller than this share of the chart text size, nor than
:data:`LEGEND_MIN_TEXT_PT` (unless the chart text is smaller still)."""
LEGEND_TEXT_STEP_PT = 0.5
"""Smaller legend text is tried in steps of this many points."""
LEGEND_MIN_CHARS = 8
"""Names are shortened to no fewer characters than this (the ellipsis included)."""
LEGEND_INSIDE_SHARE = (0.6, 0.9)
"""A legend inside the plot takes at most these shares of the plot's width and height
(and only room that has no data in it)."""
LEGEND_INSET_EM = 0.5
"""Space between an inside legend's frame and the plot's edges, in ems."""
LEGEND_CLEAR_PT = 4.0
"""An inside legend keeps at least this far from the data, in points."""
PLOT_ESTIMATE_SHARE = 0.95
"""The estimated plot area is shrunk by this factor, to err towards a smaller plot."""


class LegendPlace(StrEnum):
    RIGHT = "right"
    BELOW = "below"
    INSIDE = "inside"


class Corner(StrEnum):
    """A corner of the plot; the values are Matplotlib's ``loc`` names."""

    UPPER_RIGHT = "upper right"
    UPPER_LEFT = "upper left"
    LOWER_RIGHT = "lower right"
    LOWER_LEFT = "lower left"

    @property
    def is_right(self) -> bool:
        return self in (Corner.UPPER_RIGHT, Corner.LOWER_RIGHT)

    @property
    def is_upper(self) -> bool:
        return self in (Corner.UPPER_RIGHT, Corner.UPPER_LEFT)


CORNERS = (Corner.UPPER_RIGHT, Corner.UPPER_LEFT, Corner.LOWER_RIGHT, Corner.LOWER_LEFT)
"""The corners an inside legend tries, in this order."""


@dataclass(frozen=True)
class LegendLayout:
    place: LegendPlace
    text_size: float
    """Points."""
    names: tuple[str, ...]
    """The names of the entries listed, in order, shortened with "…" where needed."""
    columns: int = 1
    more: int = 0
    """Entries left out at the end, counted in a last entry (:attr:`more_text`)."""
    corner: Corner | None = None
    """Inside: the corner of the plot the legend is in."""

    @property
    def shown(self) -> int:
        """How many of the entries are listed (the first ones)."""
        return len(self.names)

    @property
    def more_text(self) -> str:
        return f"+{self.more} more" if self.more else ""

    @property
    def texts(self) -> tuple[str, ...]:
        """Every line of the legend: the names, then the "+N more" entry, if any."""
        return (*self.names, self.more_text) if self.more else self.names

    @property
    def rows(self) -> int:
        return math.ceil(len(self.texts) / self.columns) if self.texts else 0

    def room(self, spec: ChartSpec) -> tuple[float, float]:
        """Shares of the page width and height the legend takes from the plot."""
        if self.place is LegendPlace.RIGHT:
            return (LEGEND_SHARE, 0.0)
        if self.place is LegendPlace.BELOW:
            height = _page_pt(spec)[1]
            return (
                0.0,
                min(LEGEND_BELOW_SHARE, self.rows * LEGEND_ROW_EM * self.text_size / height),
            )
        return (0.0, 0.0)


# --- names ---------------------------------------------------------------------------


def legend_names(spec: ChartSpec, data: ChartData | None, *, hidden: bool = True) -> list[str]:
    """The names a renderer lists in the legend of ``spec`` drawing ``data``, in order.
    ``hidden``: also series toggled off (the Graph panel lists them dimmed; an export
    leaves them out). Bar-family graphs list what :func:`~curvis.charts.marks.bar_marks`
    gives (toggled-off series are never listed there)."""
    if not spec.show_legend or data is None:
        return []
    styles = {s.source: s for s in spec.series}

    def listed(source: str) -> bool:
        style = styles.get(source)
        return style is not None and style.in_legend and (hidden or style.visible)

    if spec.variant in XY_VARIANTS:
        return [s.name for s in data.xy if listed(s.source)]
    if spec.variant is ChartVariant.KAPLAN_MEIER:
        return [c.name for c in data.survival if listed(c.source)]
    if spec.family is ChartFamily.BAR:
        return [e.name for e in bar_marks(spec, data, axes_layout(spec, data, (0.0, 0.0))).legend]
    return []


# --- layout --------------------------------------------------------------------------


def graph_layout(
    spec: ChartSpec, data: ChartData | None, names: Sequence[str]
) -> tuple[AxesLayout, LegendLayout | None]:
    """The axes and the legend of ``spec`` drawing ``data`` with a legend listing
    ``names`` (none: no legend)."""
    if not spec.show_legend or not names:
        return axes_layout(spec, data, (0.0, 0.0)), None
    if spec.legend_position is LegendPosition.INSIDE and data is not None:
        layout = axes_layout(spec, data, (0.0, 0.0))
        inside = inside_legend(spec, names, layout, occupancy(spec, data, layout))
        if inside is not None:
            return layout, inside
    legend = outside_legend(spec, names)
    return axes_layout(spec, data, legend.room(spec)), legend


def smallest_text(text_size: float) -> float:
    """The smallest text a legend may get for chart text of ``text_size`` points."""
    return min(text_size, max(LEGEND_MIN_TEXT_PT, LEGEND_MIN_TEXT_SCALE * text_size))


def _sizes(text_size: float) -> list[float]:
    """Text sizes to try, from the chart's down to :func:`smallest_text`."""
    low = smallest_text(text_size)
    sizes = [text_size]
    size = text_size - LEGEND_TEXT_STEP_PT
    while size > low + 1e-9:
        sizes.append(size)
        size -= LEGEND_TEXT_STEP_PT
    if low < text_size:
        sizes.append(low)
    return sizes


def _page_pt(spec: ChartSpec) -> tuple[float, float]:
    width, height = spec.page_size_cm
    return width / CM_PER_INCH * 72.0, height / CM_PER_INCH * 72.0


def _columns_that_fit(entry: float, size: float, width: float) -> int:
    """How many columns of entries ``entry`` points wide fit ``width``."""
    gap = LEGEND_COLUMN_GAP_EM * size
    return max(1, int((width - 2 * LEGEND_BORDER_EM * size + gap) // (entry + gap)))


def _balanced(count: int, columns: int) -> int:
    """``columns`` (at most ``count``) cut to the fewest that keep the same rows."""
    columns = max(1, min(count, columns))
    rows = math.ceil(count / columns)
    return math.ceil(count / rows)


def _shorten(name: str, chars: int) -> str:
    """``name`` in at most ``chars`` characters: its beginning and end with "…" between,
    since names of samples often differ only at the end ("… 250 ml", "… 500 ml")."""
    if len(name) <= chars:
        return name
    keep = max(chars - 1, 1)
    head = (keep + 1) // 2
    tail = keep - head
    return name[:head].rstrip() + "…" + (name[-tail:].lstrip() if tail else "")


def outside_legend(spec: ChartSpec, names: Sequence[str]) -> LegendLayout:
    """The legend right of the plot or under it; see the module docstring."""
    width, height = _page_pt(spec)
    char = chart_font_info(spec.font).char_width_em
    text = spec.text_size
    count = len(names)
    entry = max(legend_entry_width(n, text, char) for n in names)
    if (
        entry + 2 * LEGEND_BORDER_EM * text <= LEGEND_RIGHT_SHARE * width
        and count * LEGEND_ROW_EM * text <= height - 2 * PAGE_PAD_PT
    ):
        return LegendLayout(LegendPlace.RIGHT, text, tuple(names))
    tallest = LEGEND_BELOW_SHARE * height

    def rows_fit(size: float) -> int:
        return max(1, int(tallest // (LEGEND_ROW_EM * size)))

    for size in _sizes(text):
        widest = max(legend_entry_width(n, size, char) for n in names)
        if widest + 2 * LEGEND_BORDER_EM * size > width:
            break  # a name wider than the page: shorten the names
        columns = _balanced(count, _columns_that_fit(widest, size, width))
        if math.ceil(count / columns) <= rows_fit(size):
            return LegendLayout(LegendPlace.BELOW, size, tuple(names), columns)
    # The smallest text: shorter names, so that more columns fit.
    size = smallest_text(text)
    rows = rows_fit(size)
    gap = LEGEND_COLUMN_GAP_EM * size

    def chars_in(columns: int) -> int:
        column = (width - 2 * LEGEND_BORDER_EM * size - (columns - 1) * gap) / columns
        return int((column / size - LEGEND_SAMPLE_EM - LEGEND_GAP_EM) / char)

    longest = max(len(n) for n in names)
    columns = math.ceil(count / rows)
    if chars_in(columns) >= LEGEND_MIN_CHARS:
        chars = min(chars_in(columns), longest)
        columns = _balanced(count, columns)
        return LegendLayout(
            LegendPlace.BELOW, size, tuple(_shorten(n, chars) for n in names), columns
        )
    # Still too many: as many entries as fit, and how many more there are.
    narrowest = legend_entry_width("x" * LEGEND_MIN_CHARS, size, char)
    columns = _columns_that_fit(narrowest, size, width)
    chars = max(min(chars_in(columns), longest), 2)
    shown = max(columns * rows - 1, 1)
    short = tuple(_shorten(n, chars) for n in names[:shown])
    return LegendLayout(LegendPlace.BELOW, size, short, columns, more=count - shown)


def inside_legend(
    spec: ChartSpec, names: Sequence[str], layout: AxesLayout, occupied: Occupancy
) -> LegendLayout | None:
    """The legend in the first free corner of the plot, or None when it does not fit
    one; see the module docstring."""
    plot_w, plot_h = plot_size(spec, layout)
    if plot_w <= 0 or plot_h <= 0:
        return None
    char = chart_font_info(spec.font).char_width_em
    count = len(names)
    for size in dict.fromkeys((spec.text_size, smallest_text(spec.text_size))):
        entry = max(legend_entry_width(n, size, char) for n in names)
        for columns in (1, 2, 3):
            if columns > count:
                continue
            rows = math.ceil(count / columns)
            gap = LEGEND_COLUMN_GAP_EM * size
            box_w = columns * entry + (columns - 1) * gap + 2 * LEGEND_BORDER_EM * size
            box_h = rows * LEGEND_ROW_EM * size
            if box_w > LEGEND_INSIDE_SHARE[0] * plot_w or box_h > LEGEND_INSIDE_SHARE[1] * plot_h:
                continue
            inset = LEGEND_INSET_EM * size
            for corner in CORNERS:
                box = _corner_box(corner, box_w, box_h, inset, plot_w, plot_h)
                if not occupied.covers(box):
                    return LegendLayout(
                        LegendPlace.INSIDE, size, tuple(names), columns, corner=corner
                    )
    return None


def _corner_box(
    corner: Corner, box_w: float, box_h: float, inset: float, plot_w: float, plot_h: float
) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) of a legend in ``corner``, with :data:`LEGEND_CLEAR_PT` round it,
    as shares of the plot (0, 0 at the lower left)."""
    clear = LEGEND_CLEAR_PT
    if corner.is_right:
        x0, x1 = plot_w - inset - box_w - clear, plot_w
    else:
        x0, x1 = 0.0, inset + box_w + clear
    if corner.is_upper:
        y0, y1 = plot_h - inset - box_h - clear, plot_h
    else:
        y0, y1 = 0.0, inset + box_h + clear
    return (x0 / plot_w, y0 / plot_h, x1 / plot_w, y1 / plot_h)


def plot_size(spec: ChartSpec, layout: AxesLayout) -> tuple[float, float]:
    """The plot area's width and height in points, estimated (on the small side) from
    the page minus the title, the tick labels and the axis titles."""
    width, height = _page_pt(spec)
    text = spec.text_size
    char = chart_font_info(spec.font).char_width_em * text
    pad = 2 * PAGE_PAD_PT
    ticks = TICK_LENGTH_PT * 1.8
    y_labels = (
        max((len(t.label) for t in layout.y.major), default=0) if spec.y_axis.show_labels else 0
    )
    left = pad + ticks + y_labels * char + (1.2 * text + 4.0 if spec.y_axis.title else 0.0)
    x_label = text * 1.2
    if layout.x.label_angle and spec.x_axis.show_labels:
        widest = max((len(t.label) for t in layout.x.major), default=0) * char
        x_label = max(x_label, widest * math.sin(math.radians(layout.x.label_angle)) + text)
    bottom = pad + ticks + (x_label if spec.x_axis.show_labels else 0.0)
    bottom += 1.2 * text + 4.0 if spec.x_axis.title else 0.0
    right = pad + text
    top = pad + text / 2
    if spec.title:
        title = text * TITLE_SCALE
        lines = math.ceil(len(spec.title) * char * TITLE_SCALE / max(width - left - right, 1.0))
        top = pad + TITLE_PAD_PT + 1.2 * title * max(lines, 1)
    plot_w = (width - left - right) * PLOT_ESTIMATE_SHARE
    plot_h = (height - top - bottom) * PLOT_ESTIMATE_SHARE
    return min(plot_w, width * AXIS_SHARE), min(plot_h, height * AXIS_SHARE)


# --- what the data covers ------------------------------------------------------------


@dataclass
class Occupancy:
    """What is drawn on the plot, as shares of the plot (0, 0 at the lower left): points,
    line segments and rectangles. ``reach`` is how far round a point it covers (half
    the largest symbol), as shares of the plot's width and height."""

    points: tuple[FloatArray, FloatArray]
    segments: tuple[FloatArray, FloatArray, FloatArray, FloatArray]
    rects: tuple[FloatArray, FloatArray, FloatArray, FloatArray]
    reach: tuple[float, float] = (0.0, 0.0)

    def covers(self, box: tuple[float, float, float, float]) -> bool:
        """True when anything drawn comes into ``box`` (x0, y0, x1, y1)."""
        x0, y0, x1, y1 = box
        px, py = self.points
        rx, ry = self.reach
        if len(px) and bool(
            np.any((px >= x0 - rx) & (px <= x1 + rx) & (py >= y0 - ry) & (py <= y1 + ry))
        ):
            return True
        a, b, c, d = self.rects
        if len(a) and bool(
            np.any(
                (np.minimum(a, c) <= x1)
                & (np.maximum(a, c) >= x0)
                & (np.minimum(b, d) <= y1)
                & (np.maximum(b, d) >= y0)
            )
        ):
            return True
        return _segments_cross(*self.segments, (x0, y0, x1, y1))


def _segments_cross(
    x0: FloatArray, y0: FloatArray, x1: FloatArray, y1: FloatArray, box: tuple[float, ...]
) -> bool:
    """True when any segment (x0, y0)–(x1, y1) reaches into ``box`` (Liang-Barsky)."""
    if not len(x0):
        return False
    left, bottom, right, top = box
    dx, dy = x1 - x0, y1 - y0
    low = np.zeros(len(x0))
    high = np.ones(len(x0))
    inside = np.ones(len(x0), dtype=bool)
    with np.errstate(divide="ignore", invalid="ignore"):
        for p, q in ((-dx, x0 - left), (dx, right - x0), (-dy, y0 - bottom), (dy, top - y0)):
            parallel = p == 0
            inside &= ~(parallel & (q < 0))
            t = np.where(parallel, 0.0, q / np.where(parallel, 1.0, p))
            entering = (p < 0) & ~parallel
            leaving = (p > 0) & ~parallel
            low = np.where(entering, np.maximum(low, t), low)
            high = np.where(leaving, np.minimum(high, t), high)
    return bool(np.any(inside & (low <= high)))


class _Collector:
    """Gathers the drawn things of a graph in plot coordinates, as shares of the plot."""

    def __init__(self, layout: AxesLayout) -> None:
        self.x0, self.y0 = layout.x.low, layout.y.low
        self.sx = 1.0 / (layout.x.high - layout.x.low) if layout.x.high != layout.x.low else 0.0
        self.sy = 1.0 / (layout.y.high - layout.y.low) if layout.y.high != layout.y.low else 0.0
        self.px: list[FloatArray] = []
        self.py: list[FloatArray] = []
        self.seg: list[tuple[FloatArray, FloatArray, FloatArray, FloatArray]] = []
        self.rect: list[tuple[FloatArray, FloatArray, FloatArray, FloatArray]] = []

    def fx(self, x: FloatArray) -> FloatArray:
        return (np.asarray(x, dtype=np.float64) - self.x0) * self.sx

    def fy(self, y: FloatArray) -> FloatArray:
        return (np.asarray(y, dtype=np.float64) - self.y0) * self.sy

    def points(self, x: FloatArray, y: FloatArray) -> None:
        fx, fy = self.fx(x), self.fy(y)
        keep = np.isfinite(fx) & np.isfinite(fy)
        self.px.append(fx[keep])
        self.py.append(fy[keep])

    def segments(self, x0: FloatArray, y0: FloatArray, x1: FloatArray, y1: FloatArray) -> None:
        a, b, c, d = self.fx(x0), self.fy(y0), self.fx(x1), self.fy(y1)
        keep = np.isfinite(a) & np.isfinite(b) & np.isfinite(c) & np.isfinite(d)
        self.seg.append((a[keep], b[keep], c[keep], d[keep]))

    def polyline(self, x: FloatArray, y: FloatArray) -> None:
        """A line through the points (broken where one is missing)."""
        x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
        if len(x) < 2:
            self.points(x, y)
            return
        self.segments(x[:-1], y[:-1], x[1:], y[1:])

    def band(self, x: FloatArray, low: FloatArray, high: FloatArray, middle: FloatArray) -> None:
        """A filled band from ``low`` to ``high``: its edges, and a cross line at each
        point so that a legend wholly inside the band counts too."""
        low = np.where(np.isfinite(low), low, middle)
        high = np.where(np.isfinite(high), high, middle)
        self.polyline(x, low)
        self.polyline(x, high)
        self.segments(x, low, x, high)

    def rects(self, x0: FloatArray, y0: FloatArray, x1: FloatArray, y1: FloatArray) -> None:
        a, b, c, d = self.fx(x0), self.fy(y0), self.fx(x1), self.fy(y1)
        keep = np.isfinite(a) & np.isfinite(b) & np.isfinite(c) & np.isfinite(d)
        self.rect.append((a[keep], b[keep], c[keep], d[keep]))

    def result(self, reach: tuple[float, float]) -> Occupancy:
        def cat(parts: list[FloatArray]) -> FloatArray:
            return np.concatenate(parts) if parts else np.empty(0)

        def cat4(
            parts: list[tuple[FloatArray, FloatArray, FloatArray, FloatArray]],
        ) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray]:
            return (
                cat([p[0] for p in parts]),
                cat([p[1] for p in parts]),
                cat([p[2] for p in parts]),
                cat([p[3] for p in parts]),
            )

        return Occupancy((cat(self.px), cat(self.py)), cat4(self.seg), cat4(self.rect), reach)


def occupancy(
    spec: ChartSpec, data: ChartData, layout: AxesLayout, marks: Marks | None = None
) -> Occupancy:
    """Everything ``spec`` draws of ``data`` on the plot of ``layout``."""
    found = _Collector(layout)
    styles = {s.source: s for s in spec.series}
    biggest = 0.0
    if spec.variant in XY_VARIANTS:
        for curve in data.curves:
            style = styles.get(curve.series_source)
            if style is None or not style.visible or len(curve.px) == 0:
                continue
            found.polyline(curve.px, curve.py)
            if curve.show_band:
                found.band(curve.px, curve.low, curve.high, curve.py)
        for series in data.xy:
            style = styles[series.source]
            if not style.visible:
                continue
            found.points(series.px, series.py)
            biggest = max(biggest, style.size)
            if spec.variant in LINE_VARIANTS or style.line_style is not LineStyle.NONE:
                found.polyline(series.px, series.py)
            low = np.where(np.isfinite(series.err_low), series.err_low, 0.0)
            high = np.where(np.isfinite(series.err_high), series.err_high, 0.0)
            found.segments(series.px, series.py - low, series.px, series.py + high)
    elif spec.variant is ChartVariant.KAPLAN_MEIER:
        for km in data.survival:
            style = styles.get(km.source)
            if style is None or not style.visible or len(km.px) == 0:
                continue
            found.polyline(km.px, km.py)
            if spec.show_bands:
                found.band(km.px, km.low, km.high, km.py)
            found.points(km.censor_px, km.censor_py)
            biggest = max(biggest, style.size)
    elif spec.family is ChartFamily.BAR:
        marks = marks if marks is not None else bar_marks(spec, data, layout)
        rects = [s for s in marks.shapes if isinstance(s, Rect)]
        if rects:
            found.rects(
                np.array([r.x0 for r in rects]),
                np.array([r.y0 for r in rects]),
                np.array([r.x1 for r in rects]),
                np.array([r.y1 for r in rects]),
            )
        polygons = [s for s in marks.shapes if isinstance(s, Polygon)]
        for p in polygons:  # violins: their bounding box
            if len(p.xs):
                found.rects(
                    np.array([np.nanmin(p.xs)]),
                    np.array([np.nanmin(p.ys)]),
                    np.array([np.nanmax(p.xs)]),
                    np.array([np.nanmax(p.ys)]),
                )
        for lines in (*marks.lines, *marks.over):
            if lines.segments:
                ends = np.array(lines.segments, dtype=np.float64)  # (n, 2, 2)
                found.segments(ends[:, 0, 0], ends[:, 0, 1], ends[:, 1, 0], ends[:, 1, 1])
        for points in marks.points:
            found.points(points.xs, points.ys)
            biggest = max(biggest, points.size_pt)
        for label in marks.labels:
            # The text of a bracket: about its width, one line tall, above (x, y).
            plot_w, plot_h = plot_size(spec, layout)
            half = len(label.text) * label.size_pt * 0.3 / max(plot_w, 1.0)
            tall = 1.2 * label.size_pt / max(plot_h, 1.0)
            fx, fy = found.fx(np.array([label.x])), found.fy(np.array([label.y]))
            found.rect.append((fx - half, fy, fx + half, fy + tall))
    plot_w, plot_h = plot_size(spec, layout)
    reach = (biggest / 2 / max(plot_w, 1.0), biggest / 2 / max(plot_h, 1.0))
    return found.result(reach)


__all__ = (
    "CORNERS",
    "LEGEND_BELOW_SHARE",
    "Corner",
    "LegendLayout",
    "LegendPlace",
    "Occupancy",
    "graph_layout",
    "inside_legend",
    "legend_names",
    "occupancy",
    "outside_legend",
    "plot_size",
    "smallest_text",
)
