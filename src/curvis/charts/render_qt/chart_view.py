"""The on-screen renderer: draws a :class:`~curvis.charts.spec.ChartSpec` with its
:class:`~curvis.charts.data.ChartData` using pyqtgraph.

It draws XY graphs (scatter or line, SD / SEM / Error-column error bars, fitted curves
and their confidence bands), bar graphs (mean with error bars) and box plots. It
computes nothing: every number comes from :mod:`curvis.charts.data`.

- The graph keeps the spec's aspect ratio inside the widget (letterboxed).
- Colours come from the spec's colour scheme, not the app theme (docs/DESIGN.md §3).
- Clicking a legend entry of an XY graph sends :attr:`ChartView.series_toggled`; the
  owner turns it into an undoable command, and the next render shows the result.
- Hovering a point, bar or box shows its values as typed in the table
  (:mod:`curvis.charts.tooltips`).

pyqtgraph is slow to import, so this module is only imported when a graph is shown.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QResizeEvent
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from curvis.charts.colors import ColorScheme, scheme
from curvis.charts.data import CategoryData, ChartData, XYSeriesData
from curvis.charts.spec import (
    AxisSpec,
    ChartFamily,
    ChartSpec,
    ChartVariant,
    LineStyle,
    Scale,
    SeriesSpec,
    Symbol,
)
from curvis.charts.tooltips import describe_category, describe_point

pg.setConfigOptions(antialias=True)

HOVER_RADIUS_PX = 8.0
"""A point shows its tooltip when the pointer is this close (in pixels)."""
BAR_WIDTH = 0.6
BOX_WIDTH = 0.5
BAND_ALPHA = 0.18
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


def _points_to_px(points: float) -> float:
    return points * 96.0 / 72.0


class ChartAxis(pg.AxisItem):  # type: ignore[misc]
    """An axis whose log ticks read as plain numbers (0.01, 1, 100), like the table."""

    def logTickStrings(self, values: list[float], scale: float, spacing: float) -> list[str]:  # noqa: N802
        out: list[str] = []
        for v in values:
            value = 10.0 ** (v * scale)
            out.append(f"{value:.6g}" if 1e-6 <= abs(value) < 1e7 else f"{value:.0e}")
        return out


class CrispErrorBars(pg.ErrorBarItem):  # type: ignore[misc]
    """Error bars drawn without antialiasing: they are only horizontal and vertical
    lines, and antialiasing thousands of them costs most of a redraw."""

    def paint(self, painter: QPainter, *args: Any) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        super().paint(painter, *args)


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

    def __init__(self, boxes: Sequence[tuple[float, Any, QColor]], ink: QColor) -> None:
        super().__init__()
        self._boxes = boxes  # (position, plot BoxStats, colour)
        self._ink = ink
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
            pen = QPen(color, 1.5)
            pen.setCosmetic(True)
            painter.setPen(pen)
            fill = QColor(color)
            fill.setAlphaF(0.25)
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
    legend_sources: dict[int, str] = field(default_factory=dict)
    """id(item) -> series source, for legend clicks."""


class ChartView(QWidget):
    """A graph drawn from a spec and its plot data."""

    series_toggled = Signal(str, bool)
    """(series source, visible): a legend entry was clicked."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Graph")
        # The plot and, to its right, the legend (outside the data, like Prism).
        self.plot_widget = pg.GraphicsLayoutWidget(self)
        self.plot_widget.setAccessibleName("Graph")
        self.plot_item: pg.PlotItem = pg.PlotItem(
            axisItems={"bottom": ChartAxis("bottom"), "left": ChartAxis("left")}
        )
        self.plot_item.setMenuEnabled(False)
        self.plot_item.hideButtons()
        self.plot_item.vb.setMouseEnabled(False, False)
        self.plot_widget.ci.addItem(self.plot_item, 0, 0)
        self.legend = pg.LegendItem()
        self.legend.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.legend.sigSampleClicked.connect(self._on_legend_clicked)
        self._legend_shown = False
        self._xy_parts: _XYParts | None = None
        self.spec: ChartSpec | None = None
        self.data: ChartData | None = None
        self.message = ""
        """Why nothing is drawn (an unsupported variant), or ""."""
        self._drawn = _Drawn()
        self.plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)

    # --- layout ----------------------------------------------------------------------

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        aspect = self.spec.aspect if self.spec is not None else 4 / 3
        width, height = self.width(), self.height()
        if width <= 0 or height <= 0:
            return
        if width / height > aspect:
            w, h = round(height * aspect), height
        else:
            w, h = width, round(width / aspect)
        self.plot_widget.setGeometry(QRect((width - w) // 2, (height - h) // 2, w, h))

    # --- rendering -------------------------------------------------------------------

    def render_chart(self, spec: ChartSpec, data: ChartData | None) -> None:
        """Draw ``spec`` with ``data`` (None: the frame and titles only)."""
        aspect_changed = self.spec is None or self.spec.aspect != spec.aspect
        self.spec, self.data = spec, data
        colors = scheme(spec.color_scheme)
        xy = spec.variant in (ChartVariant.SCATTER, ChartVariant.LINE)
        parts = self._xy_parts
        if xy and data is not None and parts is not None and parts.key == _xy_key(spec, data):
            self._update_xy(spec, data)
            self._apply_ranges(spec)
            return
        self._xy_parts = None
        plot = self.plot_item
        plot.clear()
        self.legend.clear()
        self._show_legend(False)
        self._drawn = _Drawn()
        self.message = ""
        self._style(spec, colors)
        if data is not None:
            if spec.variant in (ChartVariant.SCATTER, ChartVariant.LINE):
                self._draw_xy(spec, data, colors)
            elif spec.variant in (ChartVariant.BAR, ChartVariant.BOX):
                self._draw_categories(spec, data, colors)
            else:
                self.message = UNSUPPORTED
        self._apply_ranges(spec)
        if aspect_changed:
            self._fit()

    def _style(self, spec: ChartSpec, colors: ColorScheme) -> None:
        widget, plot = self.plot_widget, self.plot_item
        ink = QColor(colors.ink)
        widget.setBackground(QColor(colors.page))
        font = QFont(self.font())
        font.setPointSizeF(spec.text_size)
        size = f"{spec.text_size:g}pt"
        plot.setTitle(spec.title or None, color=colors.ink, size=f"{spec.text_size * 1.2:g}pt")
        categorical = spec.family is ChartFamily.BAR
        for name, axis_spec in (("bottom", spec.x_axis), ("left", spec.y_axis)):
            axis = plot.getAxis(name)
            pen = pg.mkPen(ink, width=1)
            axis.setPen(pen)
            axis.setTextPen(pen)
            axis.setTickFont(font)
            axis.setLabel(axis_spec.title or None, color=colors.ink, **{"font-size": size})
            log = axis_spec.scale is Scale.LOG and not (categorical and name == "bottom")
            axis.setLogMode(log)
            axis.setStyle(showValues=axis_spec.show_labels)
            axis.setTicks(None)
        plot.showGrid(False, False)

    def _draw_xy(self, spec: ChartSpec, data: ChartData, colors: ColorScheme) -> None:
        plot = self.plot_item
        self.legend.setLabelTextColor(colors.ink)
        self._show_legend(True)
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
            pen = pg.mkPen(color, width=_points_to_px(style.line_width))
            line = pg.PlotCurveItem(pen=pen)
            plot.addItem(line)
            parts.curves.append((line, upper, lower, band))
        for series in data.xy:
            style = styles[series.source]
            item = pg.PlotDataItem(name=series.name, **_point_style(spec, style))
            # An exact 1 px cosmetic pen: Qt draws it on its fast path.
            bars = CrispErrorBars(pen=pg.mkPen(QColor(style.color), width=1))
            plot.addItem(item)
            plot.addItem(bars)
            self._drawn.legend_sources[id(item)] = series.source
            self.legend.addItem(item, series.name)
            parts.series.append((item, bars))
        self._xy_parts = parts
        self._update_xy(spec, data)

    def _update_xy(self, spec: ChartSpec, data: ChartData) -> None:
        """Put ``data`` into the items made by :meth:`_draw_xy`, and show or hide them."""
        parts = self._xy_parts
        assert parts is not None
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
        width = _x_span(data)
        self._drawn.xy = []
        for series, (item, bars) in zip(data.xy, parts.series, strict=True):
            shown = styles[series.source].visible
            item.setData(series.px, series.py)
            item.setVisible(shown)
            has_bars = _set_error_bars(bars, series, width)
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
        styles = {s.source: s for s in spec.series}
        shown = [c for c in data.categories if styles[c.source].visible]
        ticks = [(c.position, c.name) for c in shown]
        plot.getAxis("bottom").setTicks([ticks])
        self._drawn.categories = shown
        if not shown:
            return
        ink = QColor(colors.ink)
        if spec.variant is ChartVariant.BOX:
            boxes = [(c.position, c.plot_box, QColor(styles[c.source].color)) for c in shown]
            plot.addItem(BoxItem(boxes, ink))
            return
        x = np.array([c.position for c in shown], dtype=np.float64)
        heights = np.array([c.mean for c in shown], dtype=np.float64)
        brushes = [QColor(styles[c.source].color) for c in shown]
        bar = pg.BarGraphItem(
            x=x, height=np.nan_to_num(heights), width=BAR_WIDTH, brushes=brushes, pen=pg.mkPen(ink)
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
                    pen=pg.mkPen(ink, width=1.5),
                )
            )

    def _apply_ranges(self, spec: ChartSpec) -> None:
        vb = self.plot_item.vb
        vb.enableAutoRange(x=True, y=True)
        if spec.family is ChartFamily.BAR:
            n = max(len(self._drawn.categories), 1)
            vb.setXRange(-0.6, n - 0.4, padding=0)
        for axis_spec, horizontal in ((spec.x_axis, True), (spec.y_axis, False)):
            if horizontal and spec.family is ChartFamily.BAR:
                continue
            if axis_spec.minimum is None and axis_spec.maximum is None:
                continue
            low, high = _limits(axis_spec, vb.childrenBounds()[0 if horizontal else 1])
            if horizontal:
                vb.setXRange(low, high, padding=0)
            else:
                vb.setYRange(low, high, padding=0)
        bars = spec.family is ChartFamily.BAR and spec.variant is ChartVariant.BAR
        if bars and spec.y_axis.minimum is None and self._drawn.categories:
            bounds = vb.childrenBounds()[1]
            if bounds is not None:
                top = max(bounds[1], 0.0)
                bottom = min(bounds[0], 0.0)
                vb.setYRange(bottom, top + 0.05 * (top - bottom or 1.0), padding=0)

    # --- legend and tooltips ----------------------------------------------------------

    def _on_legend_clicked(self, item: Any) -> None:
        source = self._drawn.legend_sources.get(id(item))
        if source is not None:
            # pyqtgraph has toggled the item already; the command makes it stick.
            self.series_toggled.emit(source, bool(item.isVisible()))

    def legend_entries(self) -> list[tuple[str, Any]]:
        """(source, sample item) of each legend entry, for tests."""
        if not self._legend_shown:
            return []
        return [
            (self._drawn.legend_sources[id(sample.item)], sample)
            for sample, _label in self.legend.items
        ]

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
            if abs(x - c.position) > half:
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


def _point_style(spec: ChartSpec, style: SeriesSpec) -> dict[str, Any]:
    color = QColor(style.color)
    symbol = _SYMBOLS[style.symbol]
    line_style = style.line_style
    if spec.variant is ChartVariant.LINE and line_style is LineStyle.NONE:
        line_style = LineStyle.SOLID
    pen: Any = None
    if line_style is not LineStyle.NONE:
        pen = pg.mkPen(color, width=_points_to_px(style.line_width), style=_PEN_STYLES[line_style])
    return {
        "pen": pen,
        "symbol": symbol,
        "symbolSize": _points_to_px(style.size),
        "symbolBrush": color,
        "symbolPen": pg.mkPen(color),
    }


def _xy_key(spec: ChartSpec, data: ChartData) -> object:
    """What decides which items an XY graph has. While it stays the same, a redraw only
    updates the items' data and visibility."""
    shown = replace(spec, series=tuple(replace(s, visible=True) for s in spec.series))
    return (
        shown,
        tuple((s.source, s.name) for s in data.xy),
        tuple((c.series_source, c.show_band) for c in data.curves),
    )


def _set_error_bars(bars: Any, series: XYSeriesData, x_span: float) -> bool:
    """Give ``bars`` the error bars of ``series``; False if it has none."""
    keep = np.isfinite(series.err_high) | np.isfinite(series.err_low)
    if not np.any(keep):
        return False
    bars.setData(
        x=series.px[keep],
        y=series.py[keep],
        top=np.nan_to_num(series.err_high[keep]),
        bottom=np.nan_to_num(series.err_low[keep]),
        beam=0.012 * x_span,
    )
    return True


def _x_span(data: ChartData) -> float:
    xs = [s.px for s in data.xy if len(s.px)]
    if not xs:
        return 1.0
    all_x = np.concatenate(xs)
    span = float(np.max(all_x) - np.min(all_x))
    return span if span > 0 else 1.0


def _nan_to_edge(limit: Any, fit: Any) -> Any:
    """Band limits with gaps filled by the curve itself (no band there)."""
    return np.where(np.isfinite(limit), limit, fit)


def _limits(axis: AxisSpec, bounds: Any) -> tuple[float, float]:
    log = axis.scale is Scale.LOG
    auto_low, auto_high = (bounds[0], bounds[1]) if bounds is not None else (0.0, 1.0)

    def convert(value: float | None, fallback: float) -> float:
        if value is None:
            return float(fallback)
        return math.log10(value) if log else value

    return convert(axis.minimum, auto_low), convert(axis.maximum, auto_high)
