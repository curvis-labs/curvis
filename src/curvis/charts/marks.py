"""The marks of bar-family graphs: what both renderers draw, as plain shapes.

Bars, horizontal bars, boxes, violins, columns of values and histograms are turned into
rectangles, polygons, line segments and symbols in plot coordinates (log10 on a log
axis), with their colours and line widths in points. The Graph panel
(:mod:`curvis.charts.render_qt`) and the export (:mod:`curvis.charts.render_mpl`) only
draw these, so the two cannot place a mark differently.

- **Bars**: the mean, outlined in ink, with an ink error bar away from zero. Grouped,
  the bars of a cluster touch.
- **Boxes**: Q1 to Q3 lightly filled, the median, whiskers to the minimum and maximum.
- **Violins**: the density outline, as wide as the place allows where it is highest,
  lightly filled; the median as a solid line and the quartiles as dashed ones across.
  A group of one value has only its median line.
- **Column scatter**: each value as a symbol, with the mean as a line and its error bar
  (both ways) in ink.
- **Individual values** (bar, box and violin): the values as symbols over the mark,
  spread sideways so they do not hide each other.
- **Histograms**: a bar per bin, outlined in ink; several series are see-through.

A horizontal graph is laid out upright and its coordinates swapped at the end.

Symbols of one column are spread like a dot plot: values closer than a symbol's height
share a row, and the symbols of a row sit side by side, centred, squeezed together when
the place is too narrow. The spread is worked out from the page size, so it is the same
on screen and on export.

Pure: no Qt and no Matplotlib.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from curvis.charts.axes import (
    AXIS_SHARE,
    AXIS_WIDTH_PT,
    BAR_WIDTH,
    BOX_FILL_ALPHA,
    BOX_LINE_PT,
    BOX_WIDTH,
    CM_PER_INCH,
    SCATTER_WIDTH,
    VIOLIN_WIDTH,
    AxesLayout,
    grouped_entries,
    is_grouped,
)
from curvis.charts.data import CategoryData, ChartData
from curvis.charts.grouping import grouped_layout
from curvis.charts.spec import (
    HORIZONTAL_VARIANTS,
    ChartSpec,
    ChartVariant,
    LineStyle,
    SeriesSpec,
    Symbol,
)

type FloatArray = npt.NDArray[np.float64]
type Point = tuple[float, float]

GROUPED_BOX_SHARE = 0.8
"""Grouped boxes and columns of values take this share of their place."""
GROUPED_VIOLIN_SHARE = 0.9
MEAN_LINE_WIDTH = 0.4
"""Width of a column scatter's mean line, in category units."""
POINTS_SHARE = 0.8
"""Individual values spread over this share of the bar, box or violin."""
HISTOGRAM_ALPHA = 0.55
"""Fill of a histogram's bars when it shows several series."""


@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float
    fill: str
    fill_alpha: float
    edge: str
    edge_pt: float


@dataclass(frozen=True)
class Polygon:
    xs: FloatArray
    ys: FloatArray
    fill: str
    fill_alpha: float
    edge: str
    edge_pt: float


@dataclass(frozen=True)
class Lines:
    segments: tuple[tuple[Point, Point], ...]
    color: str
    width_pt: float
    style: LineStyle = LineStyle.SOLID


@dataclass(frozen=True)
class Points:
    xs: FloatArray
    ys: FloatArray
    symbol: Symbol
    size_pt: float
    color: str


@dataclass(frozen=True)
class Label:
    """A line of text centred on ``x`` with its bottom at ``y`` (plot coordinates): the
    text of a comparison bracket."""

    x: float
    y: float
    text: str
    color: str
    size_pt: float


@dataclass(frozen=True)
class LegendEntry:
    name: str
    color: str
    fill_alpha: float = 1.0
    edge: str | None = None
    """Outline of a patch (None: the fill colour)."""
    symbol: Symbol | None = None
    """A symbol instead of a patch."""
    size_pt: float = 7.0


@dataclass(frozen=True)
class Hit:
    """Where a mark answers a hover: a box in plot coordinates."""

    x0: float
    y0: float
    x1: float
    y1: float
    source: str
    bin: int = -1
    """Histograms: the bin under the pointer."""

    def contains(self, x: float, y: float) -> bool:
        return min(self.x0, self.x1) <= x <= max(self.x0, self.x1) and min(
            self.y0, self.y1
        ) <= y <= max(self.y0, self.y1)


@dataclass
class Marks:
    """Drawn in this order: polygons and rectangles, lines, points, then the lines
    that go over the points (a column scatter's mean and error bars, comparison
    brackets) and the labels (the brackets' text)."""

    shapes: list[Rect | Polygon] = field(default_factory=list)
    lines: list[Lines] = field(default_factory=list)
    points: list[Points] = field(default_factory=list)
    over: list[Lines] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)
    legend: list[LegendEntry] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)


def bar_marks(spec: ChartSpec, data: ChartData, layout: AxesLayout) -> Marks:
    """The marks of a bar-family graph (in plot coordinates of ``layout``)."""
    if spec.variant is ChartVariant.HISTOGRAM:
        return _histogram(spec, data)
    marks = Marks()
    styles = {s.source: s for s in spec.series}
    shown = [c for c in data.categories if c.source in layout.positions]
    grouped = is_grouped(spec)
    ink = spec.palette.ink
    spread = _Spread(spec, layout)
    for c in shown:
        style, at = styles[c.source], layout.positions[c.source]
        if spec.variant in (ChartVariant.BAR, ChartVariant.HORIZONTAL_BAR):
            width = layout.slot if grouped else BAR_WIDTH
            _bar(marks, c, style, at, width, ink)
            dots = ink
        elif spec.variant is ChartVariant.BOX:
            width = layout.slot * GROUPED_BOX_SHARE if grouped else BOX_WIDTH
            _box(marks, c, style, at, width)
            dots = style.color
        elif spec.variant is ChartVariant.VIOLIN:
            width = layout.slot * GROUPED_VIOLIN_SHARE if grouped else VIOLIN_WIDTH
            _violin(marks, c, style, at, width)
            dots = style.color
        else:  # column scatter
            width = SCATTER_WIDTH
            _mean_and_error(marks, c, at, ink)
            dots = style.color
        if len(c.values):
            share = 1.0 if spec.variant is ChartVariant.COLUMN_SCATTER else POINTS_SHARE
            offsets = spread.offsets(c.values, style.size, width * share)
            symbol = style.symbol if style.symbol is not Symbol.NONE else Symbol.CIRCLE
            marks.points.append(Points(at + offsets, c.values, symbol, style.size, dots))
    _category_legend(marks, spec, data, layout)
    _brackets(marks, spec, layout)
    if spec.variant in HORIZONTAL_VARIANTS:
        return _swapped(marks)
    return marks


# --- marks -----------------------------------------------------------------------------


def _brackets(marks: Marks, spec: ChartSpec, layout: AxesLayout) -> None:
    """The comparison brackets the layout placed, in ink, with their text."""
    if not layout.brackets:
        return
    ink = spec.palette.ink
    segments: list[tuple[Point, Point]] = []
    for b in layout.brackets:
        segments += [
            ((b.x0, b.y_leg), (b.x0, b.y)),
            ((b.x0, b.y), (b.x1, b.y)),
            ((b.x1, b.y), (b.x1, b.y_leg)),
        ]
        marks.labels.append(Label(b.x_text, b.y_text, b.text, ink, spec.text_size))
    marks.over.append(Lines(tuple(segments), ink, AXIS_WIDTH_PT))


def _bar(
    marks: Marks, c: CategoryData, style: SeriesSpec, at: float, width: float, ink: str
) -> None:
    half = width / 2
    height = c.mean if math.isfinite(c.mean) else 0.0
    marks.shapes.append(
        Rect(at - half, 0.0, at + half, height, style.color, 1.0, ink, AXIS_WIDTH_PT)
    )
    low, high = c.mean - c.err_low, c.mean + c.err_high
    marks.hits.append(Hit(at - half, min(0.0, low), at + half, max(0.0, high), c.source))
    if not math.isfinite(c.mean) or (c.err_high <= 0 and c.err_low <= 0):
        return
    cap = width / 6
    marks.lines.append(
        Lines(
            (
                ((at, low), (at, high)),
                ((at - cap, high), (at + cap, high)),
                ((at - cap, low), (at + cap, low)),
            ),
            ink,
            BOX_LINE_PT,
        )
    )


def _box(marks: Marks, c: CategoryData, style: SeriesSpec, at: float, width: float) -> None:
    b = c.plot_box
    if b.n == 0:
        return
    half, cap = width / 2, width / 4
    color = style.color
    marks.shapes.append(
        Rect(at - half, b.q1, at + half, b.q3, color, BOX_FILL_ALPHA, color, BOX_LINE_PT)
    )
    marks.lines.append(
        Lines(
            (
                ((at - half, b.median), (at + half, b.median)),
                ((at, b.q3), (at, b.max)),
                ((at, b.q1), (at, b.min)),
                ((at - cap, b.max), (at + cap, b.max)),
                ((at - cap, b.min), (at + cap, b.min)),
            ),
            color,
            BOX_LINE_PT,
        )
    )
    marks.hits.append(Hit(at - half, b.min, at + half, b.max, c.source))


def _violin(marks: Marks, c: CategoryData, style: SeriesSpec, at: float, width: float) -> None:
    b, violin, color = c.plot_box, c.violin, style.color
    if b.n == 0:
        return
    half = width / 2
    if violin is None or len(violin.grid) == 0:
        # One value: its median line, half as wide as a violin.
        marks.lines.append(
            Lines((((at - half / 2, b.median), (at + half / 2, b.median)),), color, BOX_LINE_PT)
        )
        marks.hits.append(Hit(at - half, b.median, at + half, b.median, c.source))
        return
    grid, density = violin.grid, violin.density
    peak = float(density.max())
    reach = density / peak * half if peak > 0 else np.zeros_like(density)
    xs = np.concatenate([at + reach, (at - reach)[::-1]])
    ys = np.concatenate([grid, grid[::-1]])
    marks.shapes.append(Polygon(xs, ys, color, BOX_FILL_ALPHA, color, BOX_LINE_PT))

    def across(value: float) -> tuple[Point, Point]:
        r = float(np.interp(value, grid, reach)) if grid[-1] > grid[0] else half
        return ((at - r, value), (at + r, value))

    marks.lines.append(Lines((across(b.median),), color, BOX_LINE_PT))
    marks.lines.append(Lines((across(b.q1), across(b.q3)), color, AXIS_WIDTH_PT, LineStyle.DASH))
    marks.hits.append(Hit(at - half, float(grid[0]), at + half, float(grid[-1]), c.source))


def _mean_and_error(marks: Marks, c: CategoryData, at: float, ink: str) -> None:
    half = MEAN_LINE_WIDTH / 2
    segments: list[tuple[Point, Point]] = []
    if math.isfinite(c.plot_mean):
        segments.append(((at - half, c.plot_mean), (at + half, c.plot_mean)))
        cap = half / 2
        for end in (c.plot_low, c.plot_high):
            if math.isfinite(end) and end != c.plot_mean:
                segments += [((at, c.plot_mean), (at, end)), ((at - cap, end), (at + cap, end))]
    if segments:
        marks.over.append(Lines(tuple(segments), ink, BOX_LINE_PT))
    if len(c.values):
        low, high = float(c.values.min()), float(c.values.max())
        marks.hits.append(Hit(at - SCATTER_WIDTH / 2, low, at + SCATTER_WIDTH / 2, high, c.source))


def _histogram(spec: ChartSpec, data: ChartData) -> Marks:
    marks = Marks()
    hist = data.histogram
    if hist is None or len(hist.breaks) < 2:
        return marks
    styles = {s.source: s for s in spec.series}
    shown = [(source, counts) for source, counts in hist.counts if styles[source].visible]
    alpha = 1.0 if len(shown) <= 1 else HISTOGRAM_ALPHA
    ink = spec.palette.ink
    edges = hist.breaks
    names = dict(zip((source for source, _c in hist.counts), hist.names, strict=False))
    for source, counts in shown:
        color = styles[source].color
        for k, count in enumerate(counts.tolist()):
            if count <= 0:
                continue
            x0, x1 = float(edges[k]), float(edges[k + 1])
            marks.shapes.append(Rect(x0, 0.0, x1, float(count), color, alpha, ink, AXIS_WIDTH_PT))
            marks.hits.append(Hit(x0, 0.0, x1, float(count), source, k))
        if spec.show_legend and styles[source].in_legend:
            marks.legend.append(LegendEntry(names.get(source, ""), color, alpha, ink))
    return marks


# --- legend ------------------------------------------------------------------------------


def _category_legend(marks: Marks, spec: ChartSpec, data: ChartData, layout: AxesLayout) -> None:
    if not spec.show_legend:
        return
    styles = {s.source: s for s in spec.series}
    ink = spec.palette.ink
    scatter = spec.variant is ChartVariant.COLUMN_SCATTER

    def entry(name: str, style: SeriesSpec) -> LegendEntry:
        if scatter:
            symbol = style.symbol if style.symbol is not Symbol.NONE else Symbol.CIRCLE
            return LegendEntry(name, style.color, symbol=symbol, size_pt=style.size)
        return LegendEntry(name, style.color, edge=ink)

    if is_grouped(spec):
        found = grouped_layout(grouped_entries(spec, data), spec.grouping)
        listed: dict[str, bool] = {}
        first: dict[str, SeriesSpec] = {}
        for p in found.places:
            style = styles[p.source]
            first.setdefault(p.color_key, style)
            listed[p.color_key] = listed.get(p.color_key, False) or style.in_legend
        for key, name in found.legend():
            if listed.get(key):
                marks.legend.append(entry(name, first[key]))
        return
    for c in data.categories:
        if c.source in layout.positions and styles[c.source].in_legend:
            marks.legend.append(entry(c.name, styles[c.source]))


# --- spreading values ----------------------------------------------------------------


class _Spread:
    """Sideways offsets of a column of symbols (category units), from the page size."""

    def __init__(self, spec: ChartSpec, layout: AxesLayout) -> None:
        width, height = (size / CM_PER_INCH * 72.0 for size in spec.page_size_cm)
        horizontal = layout.horizontal
        category = layout.y if horizontal else layout.x
        value = layout.x if horizontal else layout.y
        wide = width * max(AXIS_SHARE - layout.legend_room[0], 0.1)
        high = height * max(AXIS_SHARE - layout.legend_room[1], 0.1)
        across, along = (high, wide) if horizontal else (wide, high)
        self.category_per_pt = (category.high - category.low) / max(across, 1.0)
        self.value_per_pt = (value.high - value.low) / max(along, 1.0)

    def offsets(self, values: FloatArray, size_pt: float, width: float) -> FloatArray:
        out = np.zeros(len(values), dtype=np.float64)
        if len(values) == 0:
            return out
        row = size_pt * self.value_per_pt
        step = size_pt * self.category_per_pt
        order = np.argsort(values, kind="stable")
        if row > 0:
            rows = np.floor((values[order] - values[order[0]]) / row).astype(np.int64)
        else:
            rows = np.zeros(len(values), dtype=np.int64)
        for members in _runs(rows):
            k = len(members)
            pitch = min(step, width / k) if k > 1 else 0.0
            for j, index in enumerate(members):
                out[order[index]] = (j - (k - 1) / 2) * pitch
        return out


def _runs(rows: npt.NDArray[np.int64]) -> Iterable[list[int]]:
    """Positions (in sorted order) of the values in each row."""
    start = 0
    for i in range(1, len(rows) + 1):
        if i == len(rows) or rows[i] != rows[start]:
            yield list(range(start, i))
            start = i


# --- horizontal ------------------------------------------------------------------------


def _swapped(marks: Marks) -> Marks:
    """``marks`` with X and Y swapped (horizontal graphs)."""
    shapes: list[Rect | Polygon] = []
    for s in marks.shapes:
        if isinstance(s, Rect):
            shapes.append(Rect(s.y0, s.x0, s.y1, s.x1, s.fill, s.fill_alpha, s.edge, s.edge_pt))
        else:
            shapes.append(Polygon(s.ys, s.xs, s.fill, s.fill_alpha, s.edge, s.edge_pt))

    def swap(found: list[Lines]) -> list[Lines]:
        return [
            Lines(
                tuple(((a[1], a[0]), (b[1], b[0])) for a, b in ln.segments),
                ln.color,
                ln.width_pt,
                ln.style,
            )
            for ln in found
        ]

    points = [Points(p.ys, p.xs, p.symbol, p.size_pt, p.color) for p in marks.points]
    hits = [Hit(h.y0, h.x0, h.y1, h.x1, h.source, h.bin) for h in marks.hits]
    return Marks(
        shapes, swap(marks.lines), points, swap(marks.over), legend=marks.legend, hits=hits
    )


__all__ = (
    "Hit",
    "Label",
    "LegendEntry",
    "Lines",
    "Marks",
    "Points",
    "Polygon",
    "Rect",
    "bar_marks",
)
