"""Column header with group bands, and drag and drop of columns and groups.

Above the column names the header draws one band row for category groups and one for
replicate groups (each only when the table has any). A band is drawn as one header
box spanning its columns. A column without a band above it has a taller box instead.

- Clicking a band selects its columns.
- Double-clicking a column name or a band edits the name in place
  (:class:`~curvis.ui.inline_edit.InlineEditor`). Tab and Shift+Tab keep the name and
  edit the next or previous name in the same row; they stop at the last one.
- Dragging a band moves the whole group or category.
- Dragging the header of a column that is already selected moves that column. A drag
  that starts on an unselected column selects a range of columns, as before.

A dragged item moves only among its siblings (see :mod:`curvis.data.layout`): a
replicate stays in its group, and nothing lands inside another group. A line shows
where the item will land.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QHelpEvent, QMouseEvent, QPainter, QPaintEvent, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QStyle,
    QStyleOptionHeader,
    QToolTip,
    QWidget,
)

from curvis.data import NodeKind
from curvis.ui.inline_edit import InlineEditor
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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._drag: _Drag | None = None
        self.editor: InlineEditor | None = None
        """The field of the name being edited, if any."""
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
        model = self.table_model()
        if model is None or event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        pos = event.position().toPoint()
        self._drag = None
        band = self.band_at(pos)
        if band is not None:
            self.edit_band(band.kind, band.id)
            event.accept()
            return
        section = self.logicalIndexAt(pos.x())
        if 0 <= section < model.n_data_columns:
            self.edit_column(section)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    # --- renaming in place --------------------------------------------------------------

    def name_rect(self, section: int) -> QRect:
        """Where the name of the column at ``section`` is drawn (above its role line)."""
        top = self.band_count * self.band_height
        line = self.fontMetrics().lineSpacing()
        name_top = top + max((self.height() - top - 2 * line) // 2, 0)
        left = self.sectionViewportPosition(section)
        return QRect(left + 2, max(name_top - 1, top), self.sectionSize(section) - 4, line + 2)

    def role_line_top(self) -> int:
        """Where the role line (X, Y, ƒ Y …) under the column names starts."""
        top = self.band_count * self.band_height
        line = self.fontMetrics().lineSpacing()
        return top + max((self.height() - top - 2 * line) // 2, 0) + line

    def edit_column(self, section: int) -> InlineEditor | None:
        """Edit the name of the column at ``section`` in place."""
        model = self.table_model()
        column = model.column_at(section) if model is not None else None
        if model is None or column is None:
            return None
        self._scroll_to(section)
        column_id = column.id

        def commit(text: str, step: int) -> bool:
            problem = model.column_name_problem(column_id, text)
            if problem is not None:
                model.error.emit(problem)
                return False
            position = model.table.column_index(column_id)
            model.rename_column(position, text.strip())
            target = position + step
            if step and 0 <= target < model.n_data_columns:
                self.edit_column(target)
            return True

        return self._open_editor(self.name_rect(section), column.name, commit)

    def edit_band(self, kind: NodeKind, node_id: str) -> InlineEditor | None:
        """Edit the name of a replicate group or category in place."""
        model = self.table_model()
        found = self._find_band(kind, node_id)
        if model is None or found is None:
            return None
        row, band = found
        self._scroll_to(band.start)

        def commit(text: str, step: int) -> bool:
            problem = model.band_name_problem(kind, node_id, text)
            if problem is not None:
                model.error.emit(problem)
                return False
            if text.strip() != band.name:
                model.rename_band(kind, node_id, text)
            following = self._neighbour_band(kind, node_id, step) if step else None
            if following is not None:
                self.edit_band(following.kind, following.id)
            return True

        rect = self.band_rect(band, row).adjusted(2, 2, -2, -2)
        return self._open_editor(rect, band.name, commit)

    def _open_editor(
        self, rect: QRect, text: str, commit: Callable[[str, int], bool]
    ) -> InlineEditor:
        self.editor = InlineEditor(
            self.viewport(), rect, text, commit, restore_focus=self.parentWidget()
        )
        return self.editor

    def _find_band(self, kind: NodeKind, node_id: str) -> tuple[int, Band] | None:
        model = self.table_model()
        if model is None:
            return None
        for row, bands in enumerate(model.bands()):
            for band in bands:
                if band is not None and band.kind is kind and band.id == node_id:
                    return row, band
        return None

    def _neighbour_band(self, kind: NodeKind, node_id: str, step: int) -> Band | None:
        """The next (``step`` 1) or previous (-1) band in the same row, or None."""
        model = self.table_model()
        found = self._find_band(kind, node_id)
        if model is None or found is None:
            return None
        row, band = found
        bands = model.bands()[row]
        positions = range(band.end, len(bands)) if step > 0 else range(band.start - 1, -1, -1)
        return next((bands[i] for i in positions if bands[i] is not None), None)

    def _scroll_to(self, section: int) -> None:
        view = self.parentWidget()
        model = self.table_model()
        if isinstance(view, QAbstractItemView) and model is not None:
            row = max(view.currentIndex().row(), 0)
            view.scrollTo(model.index(row, section))

    def viewportEvent(self, event: QEvent) -> bool:  # noqa: N802 (Qt API)
        if event.type() == QEvent.Type.ToolTip:
            assert isinstance(event, QHelpEvent)
            band = self.band_at(event.pos())
            model = self.table_model()
            if band is not None and model is not None:
                QToolTip.showText(event.globalPos(), model.band_tooltip(band), self)
                return True
        return super().viewportEvent(event)
