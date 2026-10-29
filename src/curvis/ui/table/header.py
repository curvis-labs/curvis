"""Column header with group bands, and drag and drop of columns and groups.

Above the column names the header draws one band row for category groups and one for
replicate groups (each only when the table has any). A band is drawn as one header
box spanning its columns. A column without a band above it has a taller box instead.

- Clicking a band selects its columns; double-clicking it renames the group.
- Dragging a band moves the whole group or category.
- Dragging the header of a column that is already selected moves that column. A drag
  that starts on an unselected column selects a range of columns, as before.

A dragged item moves only among its siblings (see :mod:`curvis.data.layout`): a
replicate stays in its group, and nothing lands inside another group. A line shows
where the item will land.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QHelpEvent, QMouseEvent, QPainter, QPaintEvent, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QHeaderView,
    QStyle,
    QStyleOptionHeader,
    QToolTip,
    QWidget,
)

from curvis.data import NodeKind
from curvis.ui.table.model import Band, TableModel

DROP_LINE_WIDTH = 3


@dataclass
class _Drag:
    kind: NodeKind
    id: str
    start: QPoint
    section: int
    active: bool = False
    boundary: int | None = None


class GroupedHeader(QHeaderView):
    """Horizontal header of a :class:`~curvis.ui.table.view.TableView` with group bands."""

    columns_clicked = Signal(int, int)
    """A band was clicked: select columns ``start`` to ``end - 1``."""
    move_requested = Signal(str, str, int)
    """Drop: move the (kind, id) to column boundary ``boundary``."""
    rename_requested = Signal(str, str)
    """A band was double-clicked: rename the (kind, id)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._drag: _Drag | None = None
        self.setSectionsClickable(True)

    def table_model(self) -> TableModel | None:
        model = self.model()
        return model if isinstance(model, TableModel) else None

    # --- geometry ----------------------------------------------------------------------

    @property
    def band_height(self) -> int:
        return self.fontMetrics().height() + 8

    @property
    def band_count(self) -> int:
        model = self.table_model()
        return len(model.bands()) if model is not None else 0

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        size = super().sizeHint()
        return QSize(size.width(), size.height() + self.band_count * self.band_height)

    def band_at(self, pos: QPoint) -> Band | None:
        """The band under ``pos`` (header coordinates), or None."""
        model = self.table_model()
        row = pos.y() // self.band_height
        if model is None or pos.y() < 0 or row >= self.band_count:
            return None
        return model.band_at(row, self.logicalIndexAt(pos.x()))

    def band_rect(self, band: Band, row: int) -> QRect:
        """The box of ``band`` in band row ``row``, in header coordinates."""
        left = self.sectionViewportPosition(band.start)
        right = self.sectionViewportPosition(band.end - 1) + self.sectionSize(band.end - 1)
        return QRect(left, row * self.band_height, right - left, self.band_height)

    def boundary_x(self, boundary: int) -> int:
        """The x position of the line left of column ``boundary``."""
        if boundary < self.count():
            return self.sectionViewportPosition(boundary)
        last = self.count() - 1
        return self.sectionViewportPosition(last) + self.sectionSize(last)

    def gap_at(self, x: int) -> int:
        """The column boundary nearest to ``x``."""
        model = self.table_model()
        n = model.n_data_columns if model is not None else 0
        section = self.logicalIndexAt(x)
        if section < 0:
            return n if x > 0 else 0
        middle = self.sectionViewportPosition(section) + self.sectionSize(section) // 2
        return min(section + (1 if x >= middle else 0), n)

    # --- painting ----------------------------------------------------------------------

    def paintSection(  # noqa: N802 (Qt API)
        self,
        painter: QPainter,
        rect: QRect,
        logicalIndex: int,  # noqa: N803 (Qt API)
    ) -> None:
        model = self.table_model()
        n_bands = self.band_count
        if model is None or n_bands == 0:
            super().paintSection(painter, rect, logicalIndex)
            return
        band_h = self.band_height
        top = rect.top()
        for row in range(n_bands):
            band = model.band_at(row, logicalIndex)
            if band is None:
                continue  # the box below grows upward into the empty band
            box = self.band_rect(band, row)
            box.setTop(top)
            slice_ = QRect(rect.left(), top, rect.width(), box.bottom() - top + 1)
            self._paint_band(painter, band, box, slice_, logicalIndex)
            top = (row + 1) * band_h
        column_rect = QRect(rect.left(), top, rect.width(), rect.bottom() - top + 1)
        super().paintSection(painter, column_rect, logicalIndex)

    def _paint_band(
        self, painter: QPainter, band: Band, box: QRect, clip: QRect, section: int
    ) -> None:
        """Draw the part of a band's box inside ``clip`` (one section's slice of it)."""
        option = QStyleOptionHeader()
        self.initStyleOption(option)
        option.rect = box
        option.section = section
        option.text = band.name
        option.textAlignment = Qt.AlignmentFlag.AlignCenter
        option.orientation = Qt.Orientation.Horizontal
        option.position = QStyleOptionHeader.SectionPosition.Middle
        option.state |= QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Raised
        selection = self.selectionModel()
        if selection is not None and all(
            selection.isColumnSelected(c) for c in range(band.start, band.end)
        ):
            option.state |= QStyle.StateFlag.State_On | QStyle.StateFlag.State_Sunken
        painter.save()
        painter.setClipRect(clip)
        if band.kind is NodeKind.CATEGORY:
            bold = painter.font()
            bold.setBold(True)
            painter.setFont(bold)
        self.style().drawControl(QStyle.ControlElement.CE_Header, option, painter, self)
        painter.restore()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt API)
        super().paintEvent(event)
        drag = self._drag
        if drag is None or not drag.active or drag.boundary is None:
            return
        painter = QPainter(self.viewport())
        x = self.boundary_x(drag.boundary)
        color = self.palette().color(QPalette.ColorRole.Highlight)
        painter.fillRect(x - DROP_LINE_WIDTH // 2, 0, DROP_LINE_WIDTH, self.height(), color)
        painter.end()

    # --- mouse -------------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        model = self.table_model()
        if model is None or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        pos = event.position().toPoint()
        band = self.band_at(pos)
        if band is not None:
            self.columns_clicked.emit(band.start, band.end)
            self._drag = _Drag(band.kind, band.id, pos, band.start)
            event.accept()
            return
        section = self.logicalIndexAt(pos.x())
        column = model.column_at(section)
        selection = self.selectionModel()
        if (
            column is not None
            and selection is not None
            and selection.isColumnSelected(section)
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
        ):
            self._drag = _Drag(NodeKind.COLUMN, column.id, pos, section)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        drag, model = self._drag, self.table_model()
        if drag is None or model is None:
            super().mouseMoveEvent(event)
            return
        pos = event.position().toPoint()
        distance = (pos - drag.start).manhattanLength()
        if not drag.active and distance >= QApplication.startDragDistance():
            drag.active = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        if drag.active:
            boundary = model.drop_boundary(drag.kind, drag.id, self.gap_at(pos.x()))
            if boundary != drag.boundary:
                drag.boundary = boundary
                self.viewport().update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        drag = self._drag
        if drag is None:
            super().mouseReleaseEvent(event)
            return
        self._drag = None
        self.unsetCursor()
        self.viewport().update()
        if drag.active and drag.boundary is not None:
            self.move_requested.emit(drag.kind.value, drag.id, drag.boundary)
        elif not drag.active and drag.kind is NodeKind.COLUMN:
            self.columns_clicked.emit(drag.section, drag.section + 1)
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        band = self.band_at(event.position().toPoint())
        if band is not None and event.button() == Qt.MouseButton.LeftButton:
            self._drag = None
            self.rename_requested.emit(band.kind.value, band.id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def viewportEvent(self, event: QEvent) -> bool:  # noqa: N802 (Qt API)
        if event.type() == QEvent.Type.ToolTip:
            assert isinstance(event, QHelpEvent)
            band = self.band_at(event.pos())
            model = self.table_model()
            if band is not None and model is not None:
                QToolTip.showText(event.globalPos(), model.band_tooltip(band), self)
                return True
        return super().viewportEvent(event)
