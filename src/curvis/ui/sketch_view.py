"""Draws a model's sketch (:mod:`curvis.analysis.fit.sketch`) in *About this analysis*.

Colours are palette roles (docs/DESIGN.md, section 11): the curve in ``Link``, axes,
markers and labels in ``PlaceholderText``, a fixed parameter in ``Highlight``. The sketch
is an illustration of the model, never a fit of the data, so it has no scale.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPaintEvent, QPalette, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from curvis.analysis.fit.sketch import PlacedMarker, SketchData

LEFT, RIGHT, TOP, BOTTOM = 18, 8, 8, 18
"""Margins around the plot area, in pixels (room for the axis letters and labels)."""
DOT = 3.5


class SketchView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.data: SketchData | None = None
        self._frame: tuple[QRectF, float, float, float, float] = (QRectF(), 0.0, 1.0, 0.0, 1.0)
        self.setAccessibleName("Sketch of the model")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(80)
        self.setMaximumHeight(220)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        return QSize(360, 130)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        return QSize(120, 80)

    def set_sketch(self, data: SketchData | None) -> None:
        self.data = data
        self.setVisible(data is not None)
        self.update()

    # --- drawing -----------------------------------------------------------------------

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802 (Qt API)
        data = self.data
        if data is None or len(data.x) < 2:
            return
        finite = np.isfinite(data.y)
        if not finite.any():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setPointSizeF(max(font.pointSizeF() - 1, 7))
        painter.setFont(font)
        palette = self.palette()
        quiet = palette.color(QPalette.ColorRole.PlaceholderText)
        accent = palette.color(QPalette.ColorRole.Highlight)

        area = QRectF(self.rect()).adjusted(LEFT, TOP, -RIGHT, -BOTTOM)
        x0, x1 = float(data.x[0]), float(data.x[-1])
        ys = [float(v) for v in data.y[finite]]
        ys += [m.position for m in data.markers if m.kind == "h"]
        y0, y1 = min(ys), max(ys)
        if y1 - y0 < 1e-12:
            y0, y1 = y0 - 1, y1 + 1
        pad = 0.08 * (y1 - y0)
        y0, y1 = y0 - pad, y1 + pad

        self._frame = (area, x0, x1, y0, y1)
        to_px = self._px

        # axes
        painter.setPen(QPen(quiet, 1))
        painter.drawLine(area.bottomLeft(), area.bottomRight())
        painter.drawLine(area.bottomLeft(), area.topLeft())
        metrics = painter.fontMetrics()
        painter.drawText(
            QPointF(area.right() - metrics.horizontalAdvance("X"), self.height() - 4), "X"
        )
        painter.drawText(QPointF(4, area.top() + metrics.ascent()), "Y")

        # markers under the curve
        for marker in data.markers:
            colour = accent if marker.fixed else quiet
            pen = QPen(colour, 1.5 if marker.fixed else 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            if marker.kind == "h":
                left, right = to_px(x0, marker.position), to_px(x1, marker.position)
                painter.drawLine(left, right)
            elif marker.kind == "v" and math.isfinite(marker.y):
                painter.drawLine(to_px(marker.position, y0), to_px(marker.position, marker.y))

        # the curve
        path = QPainterPath()
        started = False
        for x, y in zip(data.x, data.y, strict=True):
            if not math.isfinite(float(y)):
                started = False
                continue
            point = to_px(float(x), float(y))
            if started:
                path.lineTo(point)
            else:
                path.moveTo(point)
                started = True
        link = palette.color(QPalette.ColorRole.Link)
        painter.setPen(QPen(link, 2.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        # dots and labels
        for marker in data.markers:
            self._label(painter, marker, data, accent if marker.fixed else quiet, link)
        painter.end()

    def _px(self, x: float, y: float) -> QPointF:
        area, x0, x1, y0, y1 = self._frame
        px = area.left() + (x - x0) / (x1 - x0) * area.width()
        py = area.bottom() - (y - y0) / (y1 - y0) * area.height()
        return QPointF(px, py)

    def _label(
        self,
        painter: QPainter,
        marker: PlacedMarker,
        data: SketchData,
        colour: QColor,
        link: QColor,
    ) -> None:
        area = self._frame[0]
        place = self._px
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(marker.label)
        font = QFont(painter.font())
        font.setBold(marker.fixed)
        painter.setFont(font)
        painter.setPen(colour)
        x0, x1 = float(data.x[0]), float(data.x[-1])
        if marker.kind == "h":
            # At the end of the line where the curve is further from it.
            y_left, y_right = _end_values(data)
            at_left = abs(y_left - marker.position) >= abs(y_right - marker.position)
            line = place(x0 if at_left else x1, marker.position)
            text_x = area.left() + 4 if at_left else area.right() - width - 2
            above = line.y() - 3 - metrics.ascent() >= 0
            text_y = line.y() - 3 if above else line.y() + metrics.ascent() + 2
            painter.drawText(QPointF(text_x, text_y), marker.label)
            return
        if not math.isfinite(marker.y):
            return
        point = place(marker.position, marker.y)
        if marker.kind in ("v", "point"):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(link)
            painter.drawEllipse(point, DOT, DOT)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(colour)
        if marker.kind == "v":
            text_x = min(max(point.x() - width / 2, area.left()), area.right() - width)
            painter.drawText(QPointF(text_x, area.bottom() + metrics.ascent() + 2), marker.label)
            return
        # point and label: beside the curve, kept inside the plot area
        text_x = point.x() + 10
        if text_x + width > area.right():
            text_x = max(area.left() + 2, point.x() - 10 - width)
        text_y = point.y() + metrics.ascent() + 6
        if text_y > area.bottom() - 2:
            text_y = point.y() - 8
        painter.drawText(QPointF(text_x, text_y), marker.label)


def _end_values(data: SketchData) -> tuple[float, float]:
    finite = [float(y) for y in data.y if math.isfinite(float(y))]
    return (finite[0], finite[-1]) if finite else (0.0, 0.0)
