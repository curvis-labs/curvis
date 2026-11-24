"""The on-screen renderer: draws a :class:`~curvis.charts.spec.ChartSpec` with its
:class:`~curvis.charts.data.ChartData` using pyqtgraph.

It draws XY graphs (scatter or line, SD / SEM / Error-column error bars, fitted curves
and their confidence bands), bar-family graphs (bars, horizontal bars, boxes, violins,
columns of values and histograms, from the shapes of :mod:`curvis.charts.marks`) and
survival curves. It computes nothing: every number comes from :mod:`curvis.charts.data`,
and the ranges and ticks come from :mod:`curvis.charts.axes`, as in the export renderer.

- **The page.** The widget shows the graph's page (its size in cm) zoomed to fit,
  letterboxed: text, symbols, lines and ticks are drawn at their size in points times
  the zoom, so the graph keeps the proportions of its export.
- **Font.** The graph's chart font, loaded from the same file Matplotlib exports with
  (:mod:`curvis.charts.fonts`), with the same line metrics.
- Colours come from the spec's colour scheme, not the app theme (docs/DESIGN.md §3).
- **Legend** to the right of the plot when the spec shows one, listing the series that
  are in the legend. Clicking an XY entry sends :attr:`ChartView.series_toggled`; the
  owner turns it into an undoable command, and the next render shows the result.
- Hovering a point, bar or box shows its values as typed in the table; hovering a
  survival curve shows the step under the pointer, and a censor tick its subjects
  (:mod:`curvis.charts.tooltips`).

pyqtgraph is slow to import, so this module is only imported when a graph is shown.
"""

from __future__ import annotations

import functools
import html
import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QResizeEvent,
    QTransform,
)
from PySide6.QtWidgets import QApplication, QGraphicsWidget, QSizePolicy, QToolTip, QWidget

from curvis.charts.axes import (
    AXIS_WIDTH_PT,
    BAND_ALPHA,
    CAP_SHARE,
    ERROR_BAR_WIDTH_PT,
    FONT_ASCENT_EM,
    FONT_DESCENT_EM,
    LEGEND_BORDER_EM,
    LEGEND_COLUMN_GAP_EM,
    LEGEND_GAP_EM,
    LEGEND_ROW_EM,
    LEGEND_SAMPLE_EM,
    PAGE_PAD_PT,
    SCREEN_DPI,
    TICK_LENGTH_PT,
    TITLE_PAD_PT,
    TITLE_SCALE,
    AxesLayout,
    AxisLayout,
    axes_layout,
    legend_columns,
    page_px,
    points_to_px,
    wrap_to_fit,
)
from curvis.charts.colors import ColorScheme
from curvis.charts.data import CategoryData, ChartData, SurvivalCurveData, XYSeriesData
from curvis.charts.fonts import ChartFont, chart_font_info
from curvis.charts.marks import Hit, Label, LegendEntry, Marks, Polygon, Rect, bar_marks
from curvis.charts.spec import (
    LINE_VARIANTS,
    XY_VARIANTS,
    ChartFamily,
    ChartSpec,
    ChartVariant,
    LineStyle,
    SeriesSpec,
    Symbol,
)
from curvis.charts.tooltips import (
    describe_bin,
    describe_category,
    describe_cell,
    describe_censored,
    describe_point,
    describe_step,
    step_at,
)

pg.setConfigOptions(antialias=True)

HOVER_RADIUS_PX = 8.0
"""A point shows its tooltip when the pointer is this close (in pixels)."""
UNSUPPORTED = "This chart type cannot be drawn yet."

_SYMBOLS = {
    Symbol.CIRCLE: "o",
    Symbol.SQUARE: "s",
    Symbol.TRIANGLE: "t1",
    Symbol.DIAMOND: "d",
    Symbol.NONE: None,
}
_PEN_STYLES = {
    LineStyle.SOLID: Qt.PenStyle.SolidLine,
    LineStyle.DASH: Qt.PenStyle.DashLine,
    LineStyle.DOT: Qt.PenStyle.DotLine,
}


@functools.cache
def chart_font_family(font_id: str = "dejavu_sans") -> str:
    """The family of the chart font ``font_id``, loaded once from its file (the one
    Matplotlib exports with)."""
    info = chart_font_info(font_id)
    path = info.path()
    if path is not None:
        loaded = QFontDatabase.addApplicationFont(str(path))
        families = QFontDatabase.applicationFontFamilies(loaded) if loaded >= 0 else []
        if families:
            return str(families[0])
    return info.family


def chart_font(family: str) -> QFont:
    """The chart font, drawn as Matplotlib draws it: antialiased in grey and unhinted.
    Subpixel (LCD) antialiasing, which some systems use for screen text, gives text
    coloured fringes that no export has."""
    font = QFont(family)
    font.setStyleStrategy(
        QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.NoSubpixelAntialias
    )
    # Unhinted outlines and advances, as Matplotlib lays text out: Windows hints them by
    # default, which moves the letters of a label by several pixels.
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return font


class CrispErrorBars(pg.ErrorBarItem):  # type: ignore[misc]
    """Error bars drawn without antialiasing, with a 1 px cosmetic pen: they are only
    horizontal and vertical lines, and Qt draws thin lines on its fast path. A bar
    ``width`` pixels wide is drawn in passes moved by a device pixel sideways and up and
    down; a wide pen would take Qt's slow path (about 25 times slower for thousands of
    bars)."""

    width = 1

    def paint(self, painter: QPainter, *args: Any) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        if self.width <= 1:
            super().paint(painter, *args)
            return
        base = painter.worldTransform()
        offsets = [i - (self.width - 1) / 2 for i in range(self.width)]
        # Moving sideways thickens the vertical lines, moving up and down the caps.
        moves = {(dx, 0.0) for dx in offsets} | {(0.0, dy) for dy in offsets}
        for dx, dy in sorted(moves):
            painter.setWorldTransform(base * QTransform.fromTranslate(dx, dy))
            super().paint(painter, *args)
        painter.setWorldTransform(base)


class LegendSample(pg.ItemSample):  # type: ignore[misc]
    """A legend sample sized in ems of the chart text, like the exported legend's: the
    series' line across it and its symbol in the middle, or a patch for a bar or box.
    A hidden series shows its sample dimmed."""

    def __init__(self, item: Any, width: float, height: float, patch: bool = False) -> None:
        super().__init__(item)
        self._width, self._height, self._patch = width, height, patch
        self.setFixedWidth(width)
        self.setFixedHeight(height)

    def boundingRect(self) -> QRectF:  # noqa: N802 (Qt API)
        return QRectF(0, 0, self._width, self._height)

    def paint(self, painter: QPainter, *_args: Any) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.item.isVisible():
            painter.setOpacity(0.3)
        middle = self._height / 2
        opts = self.item.opts
        if self._patch:
            side = 0.7 * self._height / LEGEND_ROW_EM  # 0.7 em, as on export
            painter.setPen(pg.mkPen(opts["pen"]))
            painter.setBrush(pg.mkBrush(opts["brush"]))
            painter.drawRect(QRectF(0, middle - side / 2, self._width, side))
            return
        pen = opts.get("pen")
        if pen is not None:
            painter.setPen(pg.mkPen(pen))
            painter.drawLine(QPointF(0, middle), QPointF(self._width, middle))
        symbol = opts.get("symbol")
        if symbol is not None:
            # The item's own options: a series without values has no scatter style yet.
            size = opts["symbolSize"]
            painter.translate(self._width / 2, middle)
            painter.scale(size, size)
            painter.setPen(pg.mkPen(opts["symbolPen"]))
            painter.setBrush(pg.mkBrush(opts["symbolBrush"]))
            from pyqtgraph.graphicsItems.ScatterPlotItem import Symbols

            path = Symbols[symbol]
            painter.drawPath(path)


LABEL_PAD_PT = 4.0
"""Space between the tick labels and the axis title, as Matplotlib's ``labelpad``."""


class ChartAxis(pg.AxisItem):  # type: ignore[misc]
    """An axis laid out like the export's.

    - The axis title is centred on the axis (pyqtgraph appends a space to it, which
      moves it off centre) and sits inside the axis' own area, so the page edge does
      not cut it.
    - X tick labels can be turned by :attr:`label_angle` degrees, ending under their
      tick, as Matplotlib draws them with ``rotation_mode="anchor"``.
    - The axis title and turned tick labels take a line of Matplotlib's height
      (``FONT_ASCENT_EM + FONT_DESCENT_EM``), with the baseline where Matplotlib puts
      it, not Qt's taller line, so they sit where the export puts them.
    - :attr:`overflow` says how far the tick labels reach past the ends of the axis
      (pixels before and after); :attr:`on_overflow` is called when it changes, so the
      chart can give the page room for them.
    """

    # Class defaults: pyqtgraph lays the axis out while it is being made.
    label_angle = 0.0
    label_gap = 0.0
    """Pixels between the tick labels and the axis title."""
    em = 0.0
    """Pixels per em of the chart text (tick labels and axis title)."""
    ascent_em = FONT_ASCENT_EM
    descent_em = FONT_DESCENT_EM
    """The chart font's line metrics (Matplotlib's, from :mod:`curvis.charts.fonts`)."""
    overflow = (0.0, 0.0)
    on_overflow: Callable[[], None] | None = None

    def __init__(self, orientation: str) -> None:
        super().__init__(orientation)
        self._turned: list[tuple[QPointF, float, float, str]] = []
        self.label.document().setDocumentMargin(0)

    def labelString(self) -> str:  # noqa: N802 (pyqtgraph API)
        style = ";".join(f"{k}: {v}" for k, v in self.labelStyle.items())
        return f"<span style='{style}'>{html.escape(self.labelText)}</span>"

    def resizeEvent(self, ev: Any = None) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(ev)
        if self.label is None:
            return
        br = self.label.boundingRect()
        size = self.size()
        # The label's line starts at the outer edge of the axis' area.
        shift = self._baseline_shift(self.label.font())
        if self.orientation == "left":
            self.label.setPos(QPointF(-shift, size.height() / 2 + br.width() / 2))
        elif self.orientation == "bottom":
            self.label.setPos(
                QPointF(size.width() / 2 - br.width() / 2, size.height() - self._line() - shift)
            )

    def _line(self) -> float:
        """Height of a line of Matplotlib's in the chart font, in pixels (before the
        chart has set :attr:`em`: the label's own height)."""
        if not self.em:
            return float(self.label.boundingRect().height())
        return (self.ascent_em + self.descent_em) * self.em

    def _baseline_shift(self, font: QFont) -> float:
        """How much lower Qt puts the baseline under the top of a line of ``font`` than
        Matplotlib does, in pixels (0 before the chart has set :attr:`em`)."""
        if not self.em:
            return 0.0
        return QFontMetricsF(font).ascent() - self.ascent_em * self.em

    def _title_room(self) -> float:
        if not self.label.isVisible():
            return 0.0
        return self._line() + self.label_gap

    def _updateWidth(self) -> None:  # noqa: N802 (pyqtgraph API)
        if self.orientation != "left" or self.fixedWidth is not None or not self.isVisible():
            super()._updateWidth()
            return
        shown = self.style["showValues"]
        w = (self.textWidth + self.style["tickTextOffset"][0]) if shown else 0
        w += max(0, self.style["tickLength"]) + self._title_room()
        self.setMaximumWidth(w)
        self.setMinimumWidth(w)

    def _updateHeight(self) -> None:  # noqa: N802 (pyqtgraph API)
        if self.orientation != "bottom" or self.fixedHeight is not None or not self.isVisible():
            super()._updateHeight()
            return
        shown = self.style["showValues"]
        # Level labels take a line of Matplotlib's; turned ones reach as deep as their
        # turned lines do (_turn).
        text = self.textHeight if self.label_angle or not self.em else self._line()
        h = (text + self.style["tickTextOffset"][1]) if shown else 0
        h += max(0, self.style["tickLength"]) + self._title_room()
        self.setMaximumHeight(h)
        self.setMinimumHeight(h)
        self.picture = None

    def generateDrawSpecs(self, p: QPainter) -> Any:  # noqa: N802 (pyqtgraph API)
        specs = super().generateDrawSpecs(p)
        self._turned = []
        if specs is None:
            return specs
        axis_spec, tick_specs, text_specs = specs
        bounds = self.mapRectFromParent(self.geometry())
        if self.orientation == "bottom" and self.label_angle and text_specs:
            text_specs = self._turn(text_specs, bounds)
        elif self.orientation == "bottom" and self.em and text_specs:
            # Level labels hang from the top of their line with Matplotlib's baseline.
            up = self._baseline_shift(self.style["tickFont"] or QFont())
            text_specs = [(r.translated(0, -up), f, t) for r, f, t in text_specs]
        if self.orientation == "left":
            before = max((bounds.top() - r.top() for r, _f, _t in text_specs), default=0.0)
            after = max((r.bottom() - bounds.bottom() for r, _f, _t in text_specs), default=0.0)
        elif self._turned:
            before, after = self._turned_reach(bounds)
        else:
            before = max((bounds.left() - r.left() for r, _f, _t in text_specs), default=0.0)
            after = max((r.right() - bounds.right() for r, _f, _t in text_specs), default=0.0)
        overflow = (max(0.0, before), max(0.0, after))
        if any(abs(a - b) > 0.5 for a, b in zip(overflow, self.overflow, strict=True)):
            self.overflow = overflow
            if self.on_overflow is not None:
                QTimer.singleShot(0, self.on_overflow)
        return axis_spec, tick_specs, text_specs

    def _turn(self, text_specs: list[Any], bounds: QRectF) -> list[Any]:
        """Turn the labels: each ends under its tick, centred on the line through it."""
        font = self.style["tickFont"] or QFont()
        metrics = QFontMetricsF(font)
        angle = math.radians(self.label_angle)
        top = bounds.top() + max(0, self.style["tickLength"]) + self.style["tickTextOffset"][1]
        deepest = 0.0
        h = self._line()  # Matplotlib's line, centred on the anchor (va="center")
        for rect, _flags, text in text_specs:
            w = metrics.horizontalAdvance(text)
            self._turned.append((QPointF(rect.center().x(), top), w, h, text))
            deepest = max(deepest, w * math.sin(angle) + h / 2 * math.cos(angle))
        self._updateMaxTextSize(math.ceil(deepest))
        return []

    def _turned_reach(self, bounds: QRectF) -> tuple[float, float]:
        angle = math.radians(self.label_angle)
        before = after = 0.0
        for anchor, w, h, _text in self._turned:
            left = anchor.x() - w * math.cos(angle) - h / 2 * math.sin(angle)
            right = anchor.x() + h / 2 * math.sin(angle)
            before = max(before, bounds.left() - left)
            after = max(after, right - bounds.right())
        return before, after

    def drawPicture(  # noqa: N802 (pyqtgraph API)
        self,
        p: QPainter,
        axis_spec: Any,
        tick_specs: Any,
        text_specs: Any,
    ) -> None:
        super().drawPicture(p, axis_spec, tick_specs, text_specs)
        if not self._turned:
            return
        if self.style["tickFont"] is not None:
            p.setFont(self.style["tickFont"])
        p.setPen(self.textPen())
        # The baseline as Matplotlib puts it in a line of height h centred on the anchor.
        baseline = self.ascent_em * self.em - self._line() / 2
        for anchor, w, _h, text in self._turned:
            p.save()
            p.translate(anchor)
            p.rotate(-self.label_angle)
            p.drawText(QPointF(-w, baseline), text)
            p.restore()


class ChartTitle(QGraphicsWidget):
    """The graph's title, laid out as the export's (Matplotlib's ``Axes.set_title``).

    pyqtgraph's own title row is 30 px high whatever the zoom and the font, and its label
    is as wide as its text, so a long title made the plot wider than the page allows and
    squeezed the legend. This one is centred over the plot and never wider than it: a
    title that does not fit is broken into lines between words (``wrap_to_fit``), as the
    export does. Lines are spaced as Matplotlib spaces them: each at least
    ``FONT_ASCENT_EM`` above and ``FONT_DESCENT_EM`` below its baseline (or as far as its
    letters reach), plus half ``FONT_LINE_GAP_EM`` on each side when there are several.
    The plot starts ``TITLE_PAD_PT`` below the last line's baseline.

    The lines depend on the plot's width, and the title's height on the lines: when a
    resize changes them, ``on_height`` gives the layout the new height (the plot's width
    does not depend on it, so this settles at once).
    """

    def __init__(self, on_height: Callable[[float], None]) -> None:
        super().__init__()
        self.text = ""
        self.title_font = QFont()
        self.title_color = QColor()
        self.em = 0.0
        self.pad = 0.0
        self.metrics = chart_font_info("dejavu_sans")
        self.lines: tuple[str, ...] = ()
        self.baselines: tuple[float, ...] = ()
        self.on_height = on_height
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.setPreferredWidth(0)
        self.setVisible(False)

    def set_title(
        self,
        text: str,
        font: QFont,
        color: QColor,
        em: float,
        pad: float,
        metrics: ChartFont | None = None,
    ) -> None:
        """Show ``text`` in ``font`` (``em`` pixels high, with the line ``metrics`` of its
        chart font) with ``pad`` pixels between its last baseline and the plot; an empty
        text hides the title and its row."""
        self.text, self.title_font, self.title_color = text, font, color
        self.em, self.pad = em, pad
        if metrics is not None:
            self.metrics = metrics
        self.lines = ()
        self.setVisible(bool(text))
        self._lay_out()

    def shown_lines(self) -> tuple[str, ...]:
        """The title's lines as drawn."""
        return self.lines

    def _lay_out(self) -> None:
        metrics = QFontMetricsF(self.title_font)
        width = self.size().width()
        if width > 0:
            lines = wrap_to_fit(self.text, width, metrics.horizontalAdvance)
        else:  # not laid out yet
            lines = (" ".join(self.text.split()),) if self.text.strip() else ()
        if lines == self.lines and self.baselines:
            return
        self.lines = lines
        m = self.metrics
        gap = m.line_gap_em * self.em / 2 if len(lines) > 1 else 0.0
        baselines: list[float] = []
        y = 0.0
        descent = 0.0
        for line in lines:
            ink = metrics.tightBoundingRect(line)
            ascent = max(m.ascent_em * self.em, -ink.top()) + gap
            y += descent + ascent
            baselines.append(y)
            descent = max(m.descent_em * self.em, ink.bottom()) + gap
        self.baselines = tuple(baselines)
        height = y + self.pad if lines else 0.0
        if height != self.maximumHeight() or not lines:
            self.setMinimumHeight(height)
            self.setMaximumHeight(height)
            self.setPreferredHeight(height)
            self.on_height(height)
        self.update()

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        self._lay_out()

    def paint(self, painter: QPainter, *_args: Any) -> None:
        if not self.lines:
            return
        metrics = QFontMetricsF(self.title_font)
        painter.setFont(self.title_font)
        painter.setPen(self.title_color)
        for line, baseline in zip(self.lines, self.baselines, strict=True):
            width = metrics.horizontalAdvance(line)
            painter.drawText(QPointF((self.size().width() - width) / 2, baseline), line)


@dataclass
class _XYParts:
    """The items of an XY graph, kept between renders so a data change only updates
    their data (rebuilding the items costs more than the whole redraw budget)."""

    key: object
    series: list[tuple[Any, Any]]
    """(points item, error bars) per series."""
    curves: list[tuple[Any, Any, Any, Any]]
    """(line, upper limit, lower limit, fill) per curve; all but the line are None
    without a band."""


_QT_DASHES = {LineStyle.DASH: [4.0, 2.0], LineStyle.DOT: [1.0, 2.0]}
"""Dash patterns in line widths (as the export's)."""


class MarksItem(pg.GraphicsObject):  # type: ignore[misc]
    """The shapes and lines of a bar-family graph (curvis.charts.marks), in plot
    coordinates, with cosmetic pens ``px`` pixels per point wide."""

    def __init__(self, marks: Marks, px: Callable[[float], float]) -> None:
        super().__init__()
        self._marks = marks
        self._px = px
        xs: list[float] = []
        ys: list[float] = []
        for shape in marks.shapes:
            if isinstance(shape, Rect):
                xs += [shape.x0, shape.x1]
                ys += [shape.y0, shape.y1]
            else:
                xs += [float(np.min(shape.xs)), float(np.max(shape.xs))]
                ys += [float(np.min(shape.ys)), float(np.max(shape.ys))]
        for lines in marks.lines:
            for a, b in lines.segments:
                xs += [a[0], b[0]]
                ys += [a[1], b[1]]
        finite_x = [v for v in xs if math.isfinite(v)] or [0.0, 1.0]
        finite_y = [v for v in ys if math.isfinite(v)] or [0.0, 1.0]
        left, right = min(finite_x), max(finite_x)
        low, high = min(finite_y), max(finite_y)
        self._bounds = QRectF(left, low, (right - left) or 1.0, (high - low) or 1.0)

    def boundingRect(self) -> QRectF:  # noqa: N802 (Qt API)
        return self._bounds

    def _pen(self, color: str, width_pt: float, style: LineStyle = LineStyle.SOLID) -> QPen:
        pen = QPen(QColor(color), self._px(width_pt))
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        if style in _QT_DASHES:
            pen.setDashPattern(_QT_DASHES[style])
        return pen

    def paint(self, painter: QPainter, *_args: Any) -> None:
        for shape in self._marks.shapes:
            fill = QColor(shape.fill)
            fill.setAlphaF(shape.fill_alpha)
            painter.setBrush(fill)
            painter.setPen(self._pen(shape.edge, shape.edge_pt))
            if isinstance(shape, Rect):
                painter.drawRect(
                    QRectF(shape.x0, shape.y0, shape.x1 - shape.x0, shape.y1 - shape.y0)
                )
            elif isinstance(shape, Polygon):
                path = QPainterPath()
                path.moveTo(float(shape.xs[0]), float(shape.ys[0]))
                for x, y in zip(shape.xs[1:].tolist(), shape.ys[1:].tolist(), strict=True):
                    path.lineTo(x, y)
                path.closeSubpath()
                painter.drawPath(path)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for lines in self._marks.lines:
            painter.setPen(self._pen(lines.color, lines.width_pt, lines.style))
            for a, b in lines.segments:
                painter.drawLine(QPointF(*a), QPointF(*b))


@dataclass
class _Drawn:
    """What the last render put on the plot, for legend clicks and tooltips."""

    xy: list[tuple[XYSeriesData, Any]] = field(default_factory=list)
    """(series data, its main PlotDataItem)."""
    categories: list[CategoryData] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)
    """Where each bar, box, violin, column or bin answers a hover."""
    legend_sources: dict[int, str] = field(default_factory=dict)
    """id(item) -> series source, for legend clicks."""
    survival: list[tuple[SurvivalCurveData, Any]] = field(default_factory=list)
    """(survival curve, its step PlotDataItem)."""


class ChartView(QWidget):
    """A graph drawn from a spec and its plot data."""

    series_toggled = Signal(str, bool)
    """(series source, visible): a legend entry was clicked."""
    double_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Graph")
        self.family = chart_font_family()
        self.font_info = chart_font_info("dejavu_sans")
        # The plot and, to its right, the legend (outside the data, like Prism).
        self.plot_widget = pg.GraphicsLayoutWidget(self)
        self.plot_widget.setAccessibleName("Graph")
        self.plot_item: pg.PlotItem = pg.PlotItem(
            axisItems={"bottom": ChartAxis("bottom"), "left": ChartAxis("left")}
        )
        self.plot_item.setMenuEnabled(False)
        self.plot_item.hideButtons()
        self.plot_item.vb.setMouseEnabled(False, False)
        self.plot_item.vb.disableAutoRange()
        # Our own title instead of pyqtgraph's (see ChartTitle), in the same place;
        # pyqtgraph's label is let go (setTitle must not be used).
        self.plot_item.layout.removeItem(self.plot_item.titleLabel)
        self.plot_item.titleLabel.setParentItem(None)
        self.title = ChartTitle(lambda h: self.plot_item.layout.setRowFixedHeight(0, h))
        self.title.setParentItem(self.plot_item)
        self.plot_item.titleLabel = self.title
        self.plot_item.layout.addItem(self.title, 0, 1)
        # No margin of pyqtgraph's own round the axes: the page padding is the export's.
        self.plot_item.layout.setContentsMargins(0, 0, 0, 0)
        self.plot_widget.ci.addItem(self.plot_item, 0, 0)
        self.legend = pg.LegendItem()
        self.legend.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.legend.sigSampleClicked.connect(self._on_legend_clicked)
        self._legend_shown = False
        self._xy_parts: _XYParts | None = None
        self.spec: ChartSpec | None = None
        self.data: ChartData | None = None
        self.layout_: AxesLayout | None = None
        self.scale = 1.0
        """Zoom of the page: widget pixels per page pixel at 96 dpi."""
        self.message = ""
        """Why nothing is drawn (an unsupported variant), or ""."""
        self._drawn = _Drawn()
        self._margins = (0.0, 0.0, 0.0)
        self._legend_below = False
        for name in ("bottom", "left"):
            self._axis(name).on_overflow = self._update_margins
        self.plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.plot_widget.scene().sigMouseClicked.connect(self._on_scene_clicked)

    # --- layout ----------------------------------------------------------------------

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        old = self.scale
        self._fit()
        if self.spec is not None and not math.isclose(old, self.scale, rel_tol=1e-3):
            self._redraw()

    def _fit(self) -> None:
        """Letterbox the page in the widget and work out the zoom."""
        spec = self.spec
        aspect = spec.aspect if spec is not None else 3 / 2
        width, height = self.width(), self.height()
        if width <= 0 or height <= 0:
            return
        if width / height > aspect:
            w, h = round(height * aspect), height
        else:
            w, h = width, round(width / aspect)
        self.plot_widget.setGeometry(QRect((width - w) // 2, (height - h) // 2, w, h))
        if spec is not None:
            self.scale = w / page_px(spec)[0]

    def _px(self, points: float) -> float:
        """``points`` in widget pixels at the current zoom."""
        return points_to_px(points) * self.scale

    def _font_pt(self, points: float) -> float:
        """A Qt point size that draws ``points`` of the page at the current zoom: Qt
        turns points into pixels with the screen's logical dpi, the page with 96."""
        dpi = self.logicalDpiY() or SCREEN_DPI
        return points * self.scale * SCREEN_DPI / dpi

    # --- rendering -------------------------------------------------------------------

    def render_chart(self, spec: ChartSpec, data: ChartData | None) -> None:
        """Draw ``spec`` with ``data`` (None: the frame and titles only)."""
        page_changed = self.spec is None or self.spec.page_size_cm != spec.page_size_cm
        self.spec, self.data = spec, data
        if page_changed:
            self._fit()
        xy = spec.variant in XY_VARIANTS
        parts = self._xy_parts
        if xy and data is not None and parts is not None and parts.key == _xy_key(spec, data):
            self.layout_ = axes_layout(spec, data)
            self._update_xy(spec, data)
            self._apply_layout(spec, self.layout_)
            return
        self._redraw()

    def _redraw(self) -> None:
        spec, data = self.spec, self.data
        assert spec is not None
        self._xy_parts = None
        plot = self.plot_item
        plot.clear()
        self.legend.clear()
        self._show_legend(False)
        self._drawn = _Drawn()
        self.message = ""
        colors = spec.palette
        self.font_info = chart_font_info(spec.font)
        self.family = chart_font_family(spec.font)
        self.layout_ = axes_layout(spec, data)
        self._style(spec, colors)
        if data is not None:
            if spec.variant in XY_VARIANTS:
                self._draw_xy(spec, data, colors)
            elif spec.family is ChartFamily.BAR:
                self._draw_categories(spec, data, colors)
            elif spec.variant is ChartVariant.KAPLAN_MEIER:
                self._draw_survival(spec, data)
            else:
                self.message = UNSUPPORTED
        self._place_legend()
        self._apply_layout(spec, self.layout_)

    def _style(self, spec: ChartSpec, colors: ColorScheme) -> None:
        widget, plot = self.plot_widget, self.plot_item
        ink = QColor(colors.ink)
        widget.setBackground(QColor(colors.page))
        self._set_margins()
        widget.ci.setSpacing(0)
        font = chart_font(self.family)
        font.setPointSizeF(self._font_pt(spec.text_size))
        size = f"{self._font_pt(spec.text_size):.3f}pt"
        title_font = chart_font(self.family)
        title_font.setPointSizeF(self._font_pt(spec.text_size * TITLE_SCALE))
        self.title.set_title(
            spec.title,
            title_font,
            ink,
            self._px(spec.text_size * TITLE_SCALE),
            self._px(TITLE_PAD_PT),
            self.font_info,
        )
        tick = max(1, round(self._px(TICK_LENGTH_PT)))
        for name, axis_spec in (("bottom", spec.x_axis), ("left", spec.y_axis)):
            axis = plot.getAxis(name)
            pen = pg.mkPen(ink, width=self._px(AXIS_WIDTH_PT))
            axis.setPen(pen)
            axis.setTextPen(pg.mkPen(ink))
            axis.setTickFont(font)
            axis.label.setFont(font)
            axis.em = self._px(spec.text_size)
            axis.ascent_em = self.font_info.ascent_em
            axis.descent_em = self.font_info.descent_em
            axis.setLabel(
                axis_spec.title or None,
                color=colors.ink,
                **{"font-size": size, "font-family": self.family},
            )
            axis.label_gap = self._px(LABEL_PAD_PT)
            axis.setLogMode(False)  # plot coordinates are log10 already; ticks come from us
            axis._curvis_ticks = None
            axis.setStyle(
                showValues=axis_spec.show_labels,
                tickLength=tick,
                tickTextOffset=max(1, round(self._px(TICK_LENGTH_PT) * 0.8)),
                # Draw every label, as on export: the ticks are already thinned to what
                # fits (curvis.charts.axes), and a label may reach past the axis end.
                hideOverlappingLabels=10_000,
                textFillLimits=[(0, 1.0)],
            )
        plot.showGrid(False, False)
        self.legend.opts["labelTextSize"] = size
        self.legend.setLabelTextColor(colors.ink)

    def _axis(self, name: str) -> ChartAxis:
        axis = self.plot_item.getAxis(name)
        assert isinstance(axis, ChartAxis)
        return axis

    def _set_margins(self) -> None:
        """The page padding, plus room for tick labels that reach past the plot where
        nothing else (the Y axis, the legend, the title) gives them room."""
        pad = self._px(PAGE_PAD_PT)
        left, top, right = self._margins
        right += pad
        if self._legend_shown and not self._legend_below:
            right = 0.0  # all the room goes between the plot and the legend (_place_legend)
        self.plot_widget.ci.setContentsMargins(pad + left, pad + top, right, pad)

    def _update_margins(self) -> None:
        if self.spec is None:
            return
        bottom, left_axis = self._axis("bottom"), self._axis("left")
        before, after = bottom.overflow
        above = self.title.size().height() if self.title.isVisible() else 0.0
        wanted = (
            max(0.0, before - left_axis.width()),
            max(0.0, left_axis.overflow[0] - above),
            max(0.0, after),
        )
        # Grow at once, shrink only by more than a pixel: the room changes the plot's
        # width, which moves the labels a little.
        pairs = list(zip(wanted, self._margins, strict=True))
        if not any(a > b + 0.01 or a < b - 1.0 for a, b in pairs):
            return
        self._margins = wanted
        self._set_margins()
        self._place_legend()

    def _legend_wanted(self, spec: ChartSpec) -> bool:
        return spec.show_legend and any(s.in_legend for s in spec.series)

    def _add_legend_entry(self, item: Any, name: str, patch: bool = False) -> None:
        assert self.spec is not None
        em = self._px(self.spec.text_size)
        sample = LegendSample(item, LEGEND_SAMPLE_EM * em, LEGEND_ROW_EM * em, patch)
        self.legend.addItem(sample, name)
        _sample, label = self.legend.items[-1]
        label.item.setFont(chart_font(self.family))
        label.item.document().setDocumentMargin(0)
        label.setText(name, size=self.legend.opts["labelTextSize"])
        self.legend.layout.setHorizontalSpacing(LEGEND_GAP_EM * em)
        self.legend.layout.setVerticalSpacing(0)

    def _place_legend(self) -> None:
        """Put the legend right of the plot, or under it in columns when the page is
        too narrow (curvis.charts.axes.legend_columns), as on export."""
        if not self._legend_shown or self.spec is None:
            return
        columns = legend_columns(self.spec, self.legend_names())
        em = self._px(self.spec.text_size)
        border, pad = LEGEND_BORDER_EM * em, self._px(PAGE_PAD_PT)
        layout = self.plot_widget.ci
        layout.removeItem(self.legend)
        self.legend.setColumnCount(max(columns, 1))
        if columns:
            # Matplotlib's column spacing sits between a name and the next sample.
            for column in range(1, 2 * columns, 2):
                if column < 2 * columns - 1:
                    self.legend.layout.setColumnSpacing(column, LEGEND_COLUMN_GAP_EM * em)
            self.legend.layout.setContentsMargins(border, 2 * pad, border, 0)
            layout.addItem(self.legend, 1, 0)
            layout.layout.setAlignment(self.legend, Qt.AlignmentFlag.AlignHCenter)
        else:
            # As Matplotlib's constrained layout: the legend's frame against the page
            # edge, and before it the room for tick labels that reach past the end of
            # the X axis, the plot's padding and the legend's (on both its sides); the
            # legend's own border left and right.
            overflow = self._margins[2]
            self.legend.layout.setContentsMargins(3 * pad + border + overflow, 0, border, 0)
            layout.addItem(self.legend, 0, 1)
            layout.layout.setAlignment(self.legend, Qt.AlignmentFlag.AlignTop)
        self._legend_below = bool(columns)
        self.legend.updateSize()
        self._set_margins()

    def _draw_xy(self, spec: ChartSpec, data: ChartData, colors: ColorScheme) -> None:
        plot = self.plot_item
        legend = self._legend_wanted(spec)
        self._show_legend(legend)
        styles = {s.source: s for s in spec.series}
        parts = _XYParts(_xy_key(spec, data), [], [])
        for curve in data.curves:
            style = styles.get(curve.series_source)
            if style is None:
                continue
            color = QColor(style.color)
            upper = lower = band = None
            if curve.show_band:
                upper, lower = pg.PlotCurveItem(), pg.PlotCurveItem()
                fill = QColor(color)
                fill.setAlphaF(BAND_ALPHA)
                band = pg.FillBetweenItem(upper, lower, brush=fill)
                band.setZValue(-1)
                plot.addItem(band)
            pen = pg.mkPen(color, width=self._px(style.line_width))
            line = pg.PlotCurveItem(pen=pen)
            plot.addItem(line)
            parts.curves.append((line, upper, lower, band))
        for series in data.xy:
            style = styles[series.source]
            item = pg.PlotDataItem(name=series.name, **self._point_style(spec, style))
            bars = CrispErrorBars(pen=pg.mkPen(QColor(style.color), width=1))
            # Whole device pixels, rounded down (1 px until the page is zoomed to 2x).
            bars.width = max(1, math.floor(self._px(ERROR_BAR_WIDTH_PT) + 1e-6))
            plot.addItem(item)
            plot.addItem(bars)
            self._drawn.legend_sources[id(item)] = series.source
            if legend and style.in_legend:
                self._add_legend_entry(item, series.name)
            parts.series.append((item, bars))
        self._xy_parts = parts
        self._update_xy(spec, data)

    def _point_style(self, spec: ChartSpec, style: SeriesSpec) -> dict[str, Any]:
        color = QColor(style.color)
        line_style = style.line_style
        if spec.variant in LINE_VARIANTS and line_style is LineStyle.NONE:
            line_style = LineStyle.SOLID
        pen: Any = None
        if line_style is not LineStyle.NONE:
            pen = pg.mkPen(color, width=self._px(style.line_width), style=_PEN_STYLES[line_style])
        return {
            "pen": pen,
            "symbol": _SYMBOLS[style.symbol],
            "symbolSize": self._px(style.size),
            "symbolBrush": color,
            "symbolPen": pg.mkPen(color),
        }

    def _update_xy(self, spec: ChartSpec, data: ChartData) -> None:
        """Put ``data`` into the items made by :meth:`_draw_xy`, and show or hide them."""
        parts = self._xy_parts
        assert parts is not None and self.layout_ is not None
        styles = {s.source: s for s in spec.series}
        visible_curves = [c for c in data.curves if c.series_source in styles]
        for curve, (line, upper, lower, band) in zip(visible_curves, parts.curves, strict=True):
            shown = styles[curve.series_source].visible and len(curve.px) > 0
            line.setData(curve.px, curve.py)
            line.setVisible(shown)
            if band is not None:
                upper.setData(curve.px, _nan_to_edge(curve.high, curve.py))
                lower.setData(curve.px, _nan_to_edge(curve.low, curve.py))
                band.setVisible(shown)
        x = self.layout_.x
        cap = CAP_SHARE * (x.high - x.low)
        self._drawn.xy = []
        for series, (item, bars) in zip(data.xy, parts.series, strict=True):
            shown = styles[series.source].visible
            item.setData(series.px, series.py)
            item.setVisible(shown)
            has_bars = _set_error_bars(bars, series, cap)
            bars.setVisible(shown and has_bars)
            self._drawn.xy.append((series, item))

    def _draw_survival(self, spec: ChartSpec, data: ChartData) -> None:
        """Kaplan-Meier steps, their confidence bands and a tick at each censored time.
        Clicking a legend entry toggles the curve (with its band and ticks)."""
        plot = self.plot_item
        legend = self._legend_wanted(spec)
        self._show_legend(legend)
        styles = {s.source: s for s in spec.series}
        for curve in data.survival:
            style = styles.get(curve.source)
            if style is None:
                continue
            color = QColor(style.color)
            shown = style.visible and len(curve.px) > 0
            if spec.show_bands:
                upper = pg.PlotCurveItem(curve.px, _nan_to_edge(curve.high, curve.py))
                lower = pg.PlotCurveItem(curve.px, _nan_to_edge(curve.low, curve.py))
                fill = QColor(color)
                fill.setAlphaF(BAND_ALPHA)
                band = pg.FillBetweenItem(upper, lower, brush=fill)
                band.setZValue(-1)
                band.setVisible(shown)
                plot.addItem(band)
            line_style = style.line_style
            if line_style is LineStyle.NONE:
                line_style = LineStyle.SOLID  # a survival curve is always drawn
            pen = pg.mkPen(color, width=self._px(style.line_width), style=_PEN_STYLES[line_style])
            item = pg.PlotDataItem(curve.px, curve.py, name=curve.name, pen=pen)
            item.setVisible(shown)
            plot.addItem(item)
            ticks = pg.ScatterPlotItem(
                curve.censor_px,
                curve.censor_py,
                symbol=_censor_tick(),
                size=self._px(style.size),
                pen=pg.mkPen(color, width=self._px(style.line_width)),
                brush=pg.mkBrush(None),
            )
            ticks.setVisible(shown)
            plot.addItem(ticks)
            self._drawn.legend_sources[id(item)] = curve.source
            self._drawn.survival.append((curve, item))
            if legend and style.in_legend:
                self._add_legend_entry(item, curve.name)

    def _show_legend(self, shown: bool) -> None:
        if shown == self._legend_shown:
            return
        layout = self.plot_widget.ci
        if shown:
            layout.addItem(self.legend, 0, 1)
            layout.layout.setAlignment(self.legend, Qt.AlignmentFlag.AlignTop)
        else:
            layout.removeItem(self.legend)
        self._legend_shown = shown

    def _draw_categories(self, spec: ChartSpec, data: ChartData, colors: ColorScheme) -> None:
        """Bar-family graphs: the shapes, lines and symbols of curvis.charts.marks."""
        plot = self.plot_item
        assert self.layout_ is not None
        marks = bar_marks(spec, data, self.layout_)
        self._drawn.categories = [c for c in data.categories if c.source in self.layout_.positions]
        self._drawn.hits = list(marks.hits)
        legend = self._legend_wanted(spec) and bool(marks.legend)
        self._show_legend(legend)
        if legend:
            for entry in marks.legend:
                self._add_legend_entry(self._legend_item(entry), entry.name, entry.symbol is None)
                self.legend.items[-1][0].setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        if marks.shapes or marks.lines:
            plot.addItem(MarksItem(marks, self._px))
        for points in marks.points:
            color = QColor(points.color)
            plot.addItem(
                pg.ScatterPlotItem(
                    points.xs,
                    points.ys,
                    symbol=_SYMBOLS[points.symbol],
                    size=self._px(points.size_pt),
                    brush=color,
                    pen=pg.mkPen(color),
                )
            )
        if marks.over:
            plot.addItem(MarksItem(Marks(lines=list(marks.over)), self._px))
        for label in marks.labels:
            plot.addItem(self._label_item(label))

    def _label_item(self, label: Label) -> Any:
        """A bracket's text, centred above its line (as the export's ``va="bottom"``)."""
        item = pg.TextItem(label.text, color=QColor(label.color), anchor=(0.5, 1.0))
        font = chart_font(self.family)
        font.setPointSizeF(self._font_pt(label.size_pt))
        item.setFont(font)
        item.textItem.document().setDocumentMargin(0)
        item.setPos(label.x, label.y)
        item.setZValue(10)
        return item

    def _legend_item(self, entry: LegendEntry) -> Any:
        """An item whose look a legend sample copies: a patch or a symbol."""
        if entry.symbol is not None:
            color = QColor(entry.color)
            return pg.PlotDataItem(
                [],
                [],
                pen=None,
                symbol=_SYMBOLS[entry.symbol],
                symbolSize=self._px(entry.size_pt),
                symbolBrush=color,
                symbolPen=pg.mkPen(color),
            )
        fill = QColor(entry.color)
        fill.setAlphaF(entry.fill_alpha)
        return pg.BarGraphItem(
            x=[0],
            height=[1],
            width=1,
            brush=fill,
            pen=pg.mkPen(QColor(entry.edge or entry.color), width=self._px(AXIS_WIDTH_PT)),
        )

    def _apply_layout(self, spec: ChartSpec, layout: AxesLayout) -> None:
        """Set the ticks and ranges, only where they changed: new ticks make pyqtgraph
        lay out the axis text again, which costs more than the rest of a data redraw."""
        plot = self.plot_item
        for name, axis_layout in (("bottom", layout.x), ("left", layout.y)):
            axis = self._axis(name)
            ticks = _ticks(axis_layout)
            if getattr(axis, "_curvis_ticks", None) != ticks:
                axis.setTicks(ticks)
                axis._curvis_ticks = ticks
            if axis.label_angle != axis_layout.label_angle:
                axis.label_angle = axis_layout.label_angle
                axis.picture = None
                axis.update()
        ranges = ((layout.x.low, layout.x.high), (layout.y.low, layout.y.high))
        if plot.vb.viewRange() != [list(r) for r in ranges]:
            plot.vb.setRange(xRange=ranges[0], yRange=ranges[1], padding=0)

    # --- legend, clicks and tooltips ---------------------------------------------------

    def _on_legend_clicked(self, item: Any) -> None:
        source = self._drawn.legend_sources.get(id(item))
        if source is not None:
            # pyqtgraph has toggled the item already; the command makes it stick.
            self.series_toggled.emit(source, bool(item.isVisible()))

    def _on_scene_clicked(self, event: Any) -> None:
        if event.double():
            self.double_clicked.emit()

    def legend_entries(self) -> list[tuple[str, Any]]:
        """(source, sample item) of each XY legend entry, for tests."""
        if not self._legend_shown:
            return []
        return [
            (self._drawn.legend_sources[id(sample.item)], sample)
            for sample, _label in self.legend.items
            if id(sample.item) in self._drawn.legend_sources
        ]

    def legend_names(self) -> list[str]:
        """The names listed in the legend (empty without one)."""
        if not self._legend_shown:
            return []
        return [label.text for _sample, label in self.legend.items]

    def plot_rect(self) -> QRectF:
        """The plot area (inside the axes) in this widget's coordinates."""
        rect = self.plot_item.vb.sceneBoundingRect()
        top_left = self.plot_widget.mapFromScene(rect.topLeft())
        bottom_right = self.plot_widget.mapFromScene(rect.bottomRight())
        offset = self.plot_widget.pos()
        return QRectF(top_left + offset, bottom_right + offset)

    def tooltip_at(self, scene_pos: QPointF) -> str | None:
        """The tooltip for the point, bar, box or survival curve under ``scene_pos``
        (scene coordinates)."""
        if self.spec is None:
            return None
        vb = self.plot_item.vb
        if not vb.sceneBoundingRect().contains(scene_pos):
            return None
        if self._drawn.xy:
            return self._xy_tooltip(scene_pos)
        if self._drawn.survival:
            return self._survival_tooltip(scene_pos)
        if self._drawn.hits:
            point = vb.mapSceneToView(scene_pos)
            return self._category_tooltip(point.x(), point.y())
        return None

    def _xy_tooltip(self, scene_pos: QPointF) -> str | None:
        assert self.spec is not None
        vb = self.plot_item.vb
        best: tuple[float, XYSeriesData, int] | None = None
        for series, item in self._drawn.xy:
            if not item.isVisible() or len(series.px) == 0:
                continue
            for i in range(len(series.px)):
                at = vb.mapViewToScene(QPointF(float(series.px[i]), float(series.py[i])))
                distance = math.hypot(at.x() - scene_pos.x(), at.y() - scene_pos.y())
                if distance <= HOVER_RADIUS_PX and (best is None or distance < best[0]):
                    best = (distance, series, i)
        if best is None:
            return None
        series, index = best[1], best[2]
        if self.spec.family is ChartFamily.INTERACTION and self.data is not None:
            level = int(series.rows[index])
            name = self.data.levels[level] if level < len(self.data.levels) else ""
            return describe_cell(series, index, name, self.spec.x_axis.title)
        x_name = self.spec.x_axis.title or "X"
        return describe_point(series, index, x_name)

    def _survival_tooltip(self, scene_pos: QPointF) -> str | None:
        """A censor tick within reach names its subjects; otherwise the step line
        nearest the pointer (within reach) shows its survival and numbers."""
        assert self.spec is not None
        vb = self.plot_item.vb
        origin = vb.mapViewToScene(QPointF(0.0, 0.0))
        unit = vb.mapViewToScene(QPointF(1.0, 1.0))
        sx, sy = unit.x() - origin.x(), unit.y() - origin.y()
        px, py = scene_pos.x(), scene_pos.y()
        time_name = self.spec.x_axis.title or "Time"
        best_tick: tuple[float, SurvivalCurveData, int] | None = None
        best_step: tuple[float, SurvivalCurveData, int] | None = None
        for curve, item in self._drawn.survival:
            if not item.isVisible() or len(curve.px) == 0:
                continue
            if len(curve.censor_px):
                tx = origin.x() + curve.censor_px * sx
                ty = origin.y() + curve.censor_py * sy
                distance = np.hypot(tx - px, ty - py)
                k = int(np.argmin(distance))
                if distance[k] <= HOVER_RADIUS_PX and (
                    best_tick is None or distance[k] < best_tick[0]
                ):
                    best_tick = (float(distance[k]), curve, k)
            x = origin.x() + curve.px * sx
            y = origin.y() + curve.py * sy
            if len(x) < 2:
                continue
            ax, ay, dx, dy = x[:-1], y[:-1], np.diff(x), np.diff(y)
            length = dx * dx + dy * dy
            with np.errstate(invalid="ignore", divide="ignore"):
                t = np.clip(((px - ax) * dx + (py - ay) * dy) / length, 0.0, 1.0)
            t = np.where(length > 0, t, 0.0)
            distance = np.hypot(ax + t * dx - px, ay + t * dy - py)
            k = int(np.argmin(distance))
            if distance[k] <= HOVER_RADIUS_PX and (best_step is None or distance[k] < best_step[0]):
                best_step = (float(distance[k]), curve, k)
        if best_tick is not None:
            _d, curve, k = best_tick
            return describe_censored(curve, step_at(curve, float(curve.censor_px[k])), time_name)
        if best_step is not None:
            _d, curve, k = best_step
            return describe_step(curve, step_at(curve, float(curve.px[k])), time_name)
        return None

    def _category_tooltip(self, x: float, y: float) -> str | None:
        assert self.spec is not None
        for hit in self._drawn.hits:
            if not hit.contains(x, y):
                continue
            if hit.bin >= 0 and self.data is not None and self.data.histogram is not None:
                return describe_bin(self.data.histogram, hit.source, hit.bin)
            found = next((c for c in self._drawn.categories if c.source == hit.source), None)
            if found is not None:
                return describe_category(found, self.spec.variant)
        return None

    def _on_mouse_moved(self, scene_pos: QPointF) -> None:
        text = self.tooltip_at(scene_pos)
        if text:
            view_pos = self.plot_widget.mapFromScene(scene_pos)
            QToolTip.showText(self.plot_widget.mapToGlobal(view_pos), text, self.plot_widget)
        else:
            QToolTip.hideText()


def render_page(
    spec: ChartSpec, data: ChartData | None, scale: float = 1.0
) -> tuple[QImage, QRectF]:
    """Draw ``spec`` offscreen on its page at 96 dpi × ``scale``; returns the image and
    the plot area in it. For the image-diff tests and screenshots.

    The page is painted into a non-premultiplied ARGB32 image: Qt's raster engine draws
    text there with grey antialiasing on every system, where on Windows with ClearType
    it would use subpixel text (coloured fringes) for an opaque target whatever the font
    asks for. The export never has those fringes, and on screen they are only a display
    effect."""
    width, height = page_px(spec, SCREEN_DPI * scale)
    view = ChartView()
    view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    view.resize(width, height)
    view.show()
    view.render_chart(spec, data)
    app = QApplication.instance()
    # Painting lays the axes out, which can give the tick labels more room; a few
    # rounds settle it.
    for _ in range(4):
        if app is not None:
            app.processEvents()
        scratch = QImage(view.plot_widget.size(), QImage.Format.Format_ARGB32)
        painter = QPainter(scratch)
        view.plot_widget.render(painter)
        painter.end()
    if app is not None:
        app.processEvents()
    image = QImage(view.plot_widget.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor(spec.palette.page))
    painter = QPainter(image)
    view.plot_widget.render(painter)
    painter.end()
    rect = view.plot_rect().translated(-view.plot_widget.pos().toPointF())
    view.close()
    view.deleteLater()
    return image, rect


def _ticks(axis: AxisLayout) -> list[list[tuple[float, str]]]:
    return [[(t.value, t.label) for t in axis.major], [(v, "") for v in axis.minor]]


def _xy_key(spec: ChartSpec, data: ChartData) -> object:
    """What decides which items an XY graph has. While it stays the same, a redraw only
    updates the items' data and visibility."""
    shown = replace(spec, series=tuple(replace(s, visible=True) for s in spec.series))
    return (
        shown,
        tuple((s.source, s.name) for s in data.xy),
        tuple((c.series_source, c.show_band) for c in data.curves),
    )


def _set_error_bars(bars: Any, series: XYSeriesData, cap: float) -> bool:
    """Give ``bars`` the error bars of ``series``; False if it has none."""
    keep = np.isfinite(series.err_high) | np.isfinite(series.err_low)
    if not np.any(keep):
        return False
    bars.setData(
        x=series.px[keep],
        y=series.py[keep],
        top=np.nan_to_num(series.err_high[keep]),
        bottom=np.nan_to_num(series.err_low[keep]),
        beam=cap,
    )
    return True


@functools.cache
def _censor_tick() -> QPainterPath:
    """A vertical stroke one symbol size long, as Matplotlib's "|" marker (pyqtgraph's
    own "|" is a filled bar)."""
    path = QPainterPath()
    path.moveTo(0.0, -0.5)
    path.lineTo(0.0, 0.5)
    return path


def _nan_to_edge(limit: Any, fit: Any) -> Any:
    """Band limits with gaps filled by the curve itself (no band there)."""
    return np.where(np.isfinite(limit), limit, fit)


__all__ = (
    "ChartAxis",
    "ChartView",
    "LegendSample",
    "MarksItem",
    "chart_font",
    "chart_font_family",
    "render_page",
)
