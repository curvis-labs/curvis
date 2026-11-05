"""The on-screen renderer: draws a :class:`~curvis.charts.spec.ChartSpec` with its
:class:`~curvis.charts.data.ChartData` using pyqtgraph.

It draws XY graphs (scatter or line, SD / SEM / Error-column error bars, fitted curves
and their confidence bands), bar graphs (mean with error bars) and box plots. It
computes nothing: every number comes from :mod:`curvis.charts.data`, and the ranges and
ticks come from :mod:`curvis.charts.axes`, as in the export renderer.

- **The page.** The widget shows the graph's page (its size in cm) zoomed to fit,
  letterboxed: text, symbols, lines and ticks are drawn at their size in points times
  the zoom, so the graph keeps the proportions of its export.
- **Font.** DejaVu Sans, the font Matplotlib exports with, loaded from Matplotlib's
  files (:func:`curvis.charts.axes.chart_font_path`).
- Colours come from the spec's colour scheme, not the app theme (docs/DESIGN.md §3).
- **Legend** to the right of the plot when the spec shows one, listing the series that
  are in the legend. Clicking an XY entry sends :attr:`ChartView.series_toggled`; the
  owner turns it into an undoable command, and the next render shows the result.
- Hovering a point, bar or box shows its values as typed in the table
  (:mod:`curvis.charts.tooltips`).

pyqtgraph is slow to import, so this module is only imported when a graph is shown.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPen, QResizeEvent
from PySide6.QtWidgets import QApplication, QSizePolicy, QToolTip, QWidget

from curvis.charts.axes import (
    AXIS_WIDTH_PT,
    BAND_ALPHA,
    BAR_WIDTH,
    BOX_FILL_ALPHA,
    BOX_LINE_PT,
    BOX_WIDTH,
    CAP_SHARE,
    ERROR_BAR_WIDTH_PT,
    FONT_FAMILY,
    LEGEND_GAP_EM,
    LEGEND_ROW_EM,
    LEGEND_SAMPLE_EM,
    PAGE_PAD_PT,
    SCREEN_DPI,
    TICK_LENGTH_PT,
    TITLE_SCALE,
    AxesLayout,
    AxisLayout,
    axes_layout,
    chart_font_path,
    page_px,
    points_to_px,
)
from curvis.charts.colors import ColorScheme, scheme
from curvis.charts.data import CategoryData, ChartData, XYSeriesData
from curvis.charts.spec import (
    ChartSpec,
    ChartVariant,
    LineStyle,
    SeriesSpec,
    Symbol,
)
from curvis.charts.tooltips import describe_category, describe_point

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
def chart_font_family() -> str:
    """The chart font, loaded once from Matplotlib's copy of DejaVu Sans."""
    path = chart_font_path()
    if path is not None:
        font_id = QFontDatabase.addApplicationFont(str(path))
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        if families:
            return str(families[0])
    return FONT_FAMILY


class CrispErrorBars(pg.ErrorBarItem):  # type: ignore[misc]
    """Error bars drawn without antialiasing: they are only horizontal and vertical
    lines, and antialiasing thousands of them costs most of a redraw."""

    def paint(self, painter: QPainter, *args: Any) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        super().paint(painter, *args)


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
            scatter = self.item.scatter.opts
            painter.translate(self._width / 2, middle)
            painter.scale(scatter["size"], scatter["size"])
            painter.setPen(pg.mkPen(scatter["pen"]))
            painter.setBrush(pg.mkBrush(scatter["brush"]))
            from pyqtgraph.graphicsItems.ScatterPlotItem import Symbols

            path = Symbols[symbol]
            painter.drawPath(path)


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


class BoxItem(pg.GraphicsObject):  # type: ignore[misc]
    """Box plots: a box from Q1 to Q3 with the median, whiskers from min to max."""

    def __init__(self, boxes: Sequence[tuple[float, Any, QColor]], width_px: float) -> None:
        super().__init__()
        self._boxes = boxes  # (position, plot BoxStats, colour)
        self._width_px = width_px
        finite = [b for _, b, _ in boxes if b.n > 0]
        low = min((b.min for b in finite), default=0.0)
        high = max((b.max for b in finite), default=1.0)
        if low == high:
            low, high = low - 0.5, high + 0.5
        left = min((p for p, _, _ in boxes), default=0) - BOX_WIDTH
        right = max((p for p, _, _ in boxes), default=0) + BOX_WIDTH
        self._bounds = QRectF(left, low, right - left, high - low)

    def boundingRect(self) -> QRectF:  # noqa: N802 (Qt API)
        return self._bounds

    def paint(self, painter: QPainter, *_args: Any) -> None:
        half = BOX_WIDTH / 2
        for position, b, color in self._boxes:
            if b.n == 0:
                continue
            pen = QPen(color, self._width_px)
            pen.setCosmetic(True)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            fill = QColor(color)
            fill.setAlphaF(BOX_FILL_ALPHA)
            painter.setBrush(fill)
            painter.drawRect(QRectF(position - half, b.q1, BOX_WIDTH, b.q3 - b.q1))
            painter.drawLine(QPointF(position - half, b.median), QPointF(position + half, b.median))
            painter.drawLine(QPointF(position, b.q3), QPointF(position, b.max))
            painter.drawLine(QPointF(position, b.q1), QPointF(position, b.min))
            cap = half / 2
            painter.drawLine(QPointF(position - cap, b.max), QPointF(position + cap, b.max))
            painter.drawLine(QPointF(position - cap, b.min), QPointF(position + cap, b.min))


@dataclass
class _Drawn:
    """What the last render put on the plot, for legend clicks and tooltips."""

    xy: list[tuple[XYSeriesData, Any]] = field(default_factory=list)
    """(series data, its main PlotDataItem)."""
    categories: list[CategoryData] = field(default_factory=list)
    positions: dict[str, int] = field(default_factory=dict)
    """Position of each bar or box drawn, by source."""
    legend_sources: dict[int, str] = field(default_factory=dict)
    """id(item) -> series source, for legend clicks."""


class ChartView(QWidget):
    """A graph drawn from a spec and its plot data."""

    series_toggled = Signal(str, bool)
    """(series source, visible): a legend entry was clicked."""
    double_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Graph")
        self.family = chart_font_family()
        # The plot and, to its right, the legend (outside the data, like Prism).
        self.plot_widget = pg.GraphicsLayoutWidget(self)
        self.plot_widget.setAccessibleName("Graph")
        self.plot_item: pg.PlotItem = pg.PlotItem()
        self.plot_item.setMenuEnabled(False)
        self.plot_item.hideButtons()
        self.plot_item.vb.setMouseEnabled(False, False)
        self.plot_item.vb.disableAutoRange()
        self.plot_item.titleLabel.item.setFont(QFont(self.family))
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
        xy = spec.variant in (ChartVariant.SCATTER, ChartVariant.LINE)
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
        colors = scheme(spec.color_scheme)
        self.layout_ = axes_layout(spec, data)
        self._style(spec, colors)
        if data is not None:
            if spec.variant in (ChartVariant.SCATTER, ChartVariant.LINE):
                self._draw_xy(spec, data, colors)
            elif spec.variant in (ChartVariant.BAR, ChartVariant.BOX):
                self._draw_categories(spec, data, colors)
            else:
                self.message = UNSUPPORTED
        self._apply_layout(spec, self.layout_)

    def _style(self, spec: ChartSpec, colors: ColorScheme) -> None:
        widget, plot = self.plot_widget, self.plot_item
        ink = QColor(colors.ink)
        widget.setBackground(QColor(colors.page))
        pad = self._px(PAGE_PAD_PT)
        widget.ci.setContentsMargins(pad, pad, pad, pad)
        widget.ci.setSpacing(0)
        font = QFont(self.family)
        font.setPointSizeF(self._font_pt(spec.text_size))
        size = f"{self._font_pt(spec.text_size):.3f}pt"
        title_size = f"{self._font_pt(spec.text_size * TITLE_SCALE):.3f}pt"
        plot.setTitle(spec.title or None, color=colors.ink, size=title_size)
        tick = max(1, round(self._px(TICK_LENGTH_PT)))
        for name, axis_spec in (("bottom", spec.x_axis), ("left", spec.y_axis)):
            axis = plot.getAxis(name)
            pen = pg.mkPen(ink, width=self._px(AXIS_WIDTH_PT))
            axis.setPen(pen)
            axis.setTextPen(pg.mkPen(ink))
            axis.setTickFont(font)
            axis.setLabel(
                axis_spec.title or None,
                color=colors.ink,
                **{"font-size": size, "font-family": self.family},
            )
            axis.setLogMode(False)  # plot coordinates are log10 already; ticks come from us
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

    def _legend_wanted(self, spec: ChartSpec) -> bool:
        return spec.show_legend and any(s.in_legend for s in spec.series)

    def _add_legend_entry(self, item: Any, name: str, patch: bool = False) -> None:
        assert self.spec is not None
        em = self._px(self.spec.text_size)
        sample = LegendSample(item, LEGEND_SAMPLE_EM * em, LEGEND_ROW_EM * em, patch)
        self.legend.addItem(sample, name)
        _sample, label = self.legend.items[-1]
        label.item.setFont(QFont(self.family))
        label.item.document().setDocumentMargin(0)
        label.setText(name, size=self.legend.opts["labelTextSize"])
        self.legend.layout.setHorizontalSpacing(LEGEND_GAP_EM * em)
        self.legend.layout.setVerticalSpacing(0)
        self.legend.layout.setContentsMargins(LEGEND_GAP_EM * em, 0, 0, 0)
        self.legend.updateSize()

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
            bars = CrispErrorBars(
                pen=pg.mkPen(QColor(style.color), width=max(1.0, self._px(ERROR_BAR_WIDTH_PT)))
            )
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
        if spec.variant is ChartVariant.LINE and line_style is LineStyle.NONE:
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
        plot = self.plot_item
        assert self.layout_ is not None
        styles = {s.source: s for s in spec.series}
        positions = self.layout_.positions
        shown = [c for c in data.categories if c.source in positions]
        self._drawn.categories = shown
        self._drawn.positions = dict(positions)
        ink = QColor(colors.ink)
        legend = self._legend_wanted(spec)
        self._show_legend(legend)
        if legend:
            for c in shown:
                if styles[c.source].in_legend:
                    sample = pg.BarGraphItem(
                        x=[0],
                        height=[1],
                        width=1,
                        brush=QColor(styles[c.source].color),
                        pen=pg.mkPen(ink, width=self._px(AXIS_WIDTH_PT)),
                    )
                    self._add_legend_entry(sample, c.name, patch=True)
                    self.legend.items[-1][0].setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        if not shown:
            return
        if spec.variant is ChartVariant.BOX:
            boxes = [
                (float(positions[c.source]), c.plot_box, QColor(styles[c.source].color))
                for c in shown
            ]
            plot.addItem(BoxItem(boxes, self._px(BOX_LINE_PT)))
            return
        x = np.array([positions[c.source] for c in shown], dtype=np.float64)
        heights = np.array([c.mean for c in shown], dtype=np.float64)
        brushes = [QColor(styles[c.source].color) for c in shown]
        bar = pg.BarGraphItem(
            x=x,
            height=np.nan_to_num(heights),
            width=BAR_WIDTH,
            brushes=brushes,
            pen=pg.mkPen(ink, width=self._px(AXIS_WIDTH_PT)),
        )
        plot.addItem(bar)
        top = np.array([c.err_high for c in shown], dtype=np.float64)
        bottom = np.array([c.err_low for c in shown], dtype=np.float64)
        keep = ((top > 0) | (bottom > 0)) & np.isfinite(heights)
        if np.any(keep):
            plot.addItem(
                pg.ErrorBarItem(
                    x=x[keep],
                    y=heights[keep],
                    top=top[keep],
                    bottom=bottom[keep],
                    beam=BAR_WIDTH / 3,
                    pen=pg.mkPen(ink, width=self._px(BOX_LINE_PT)),
                )
            )

    def _apply_layout(self, spec: ChartSpec, layout: AxesLayout) -> None:
        plot = self.plot_item
        for name, axis_layout in (("bottom", layout.x), ("left", layout.y)):
            plot.getAxis(name).setTicks(_ticks(axis_layout))
        plot.vb.setRange(
            xRange=(layout.x.low, layout.x.high), yRange=(layout.y.low, layout.y.high), padding=0
        )

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
        """The tooltip for the point, bar or box under ``scene_pos`` (scene coordinates)."""
        if self.spec is None:
            return None
        vb = self.plot_item.vb
        if not vb.sceneBoundingRect().contains(scene_pos):
            return None
        if self._drawn.xy:
            return self._xy_tooltip(scene_pos)
        if self._drawn.categories:
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
        x_name = self.spec.x_axis.title or "X"
        return describe_point(best[1], best[2], x_name)

    def _category_tooltip(self, x: float, y: float) -> str | None:
        assert self.spec is not None
        box = self.spec.variant is ChartVariant.BOX
        half = (BOX_WIDTH if box else BAR_WIDTH) / 2
        for c in self._drawn.categories:
            if abs(x - self._drawn.positions[c.source]) > half:
                continue
            if box:
                b = c.plot_box
                if b.n and b.min <= y <= b.max:
                    return describe_category(c, self.spec.variant)
            else:
                low, high = c.mean - c.err_low, c.mean + c.err_high
                if min(0.0, low) <= y <= max(0.0, high):
                    return describe_category(c, self.spec.variant)
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
    the plot area in it. For the image-diff tests and screenshots."""
    width, height = page_px(spec, SCREEN_DPI * scale)
    view = ChartView()
    view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    view.resize(width, height)
    view.show()
    view.render_chart(spec, data)
    app = QApplication.instance()
    for _ in range(3):
        if app is not None:
            app.processEvents()
    image = view.plot_widget.grab().toImage()
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


def _nan_to_edge(limit: Any, fit: Any) -> Any:
    """Band limits with gaps filled by the curve itself (no band there)."""
    return np.where(np.isfinite(limit), limit, fit)


__all__ = ("BoxItem", "ChartView", "LegendSample", "chart_font_family", "render_page")
