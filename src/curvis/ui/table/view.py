"""Spreadsheet-style view of a data table.

- Typing in a cell starts editing it. Enter, Tab and the arrow keys then save the cell
  and move on. F2 (on every platform) or a double-click edits the text in place: the
  arrow keys then move the text cursor.
- Enter moves down, Shift+Enter up, Tab right and Shift+Tab left.
- Ctrl+C and Ctrl+V copy and paste tab-separated text, which Excel reads and writes.
- Delete or Backspace clears the selected cells.
- Right-clicking a column header adds, deletes and renames columns, sets their role,
  groups them as replicates or into categories, and opens bulk rename.
- Groups are shown above their columns; columns and groups move by drag and drop
  (see :mod:`curvis.ui.table.header`).

The view only sends commands through its :class:`TableModel`; it computes nothing.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractItemModel,
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QLine,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPoint,
    QRect,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QGuiApplication,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QHeaderView,
    QInputDialog,
    QLineEdit,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QWidget,
)

from curvis.data import ColumnRole, NodeKind
from curvis.ui.table.bulk_rename import BulkRenameDialog
from curvis.ui.table.clipboard import from_tsv, to_tsv
from curvis.ui.table.format import cell_text
from curvis.ui.table.header import GroupedHeader
from curvis.ui.table.model import TableModel

DEFAULT_COLUMN_WIDTH = 96
CELL_PADDING = 4

_NUMBER_FLAGS = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
_TEXT_FLAGS = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

_EnterKeys = (Qt.Key.Key_Return, Qt.Key.Key_Enter)
_ARROWS = {
    Qt.Key.Key_Up: (-1, 0),
    Qt.Key.Key_Down: (1, 0),
    Qt.Key.Key_Left: (0, -1),
    Qt.Key.Key_Right: (0, 1),
}

type Range = tuple[int, int, int, int]
"""A block of cells: (top, left, bottom, right), inclusive."""


def _key_step(event: QKeyEvent) -> tuple[int, int] | None:
    """The cursor move for Enter or Tab (with or without Shift), else None."""
    key, shift = event.key(), bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
    if key in _EnterKeys:
        return (-1, 0) if shift else (1, 0)
    if key == Qt.Key.Key_Tab:
        return (0, -1) if shift else (0, 1)
    if key == Qt.Key.Key_Backtab:
        return (0, -1)
    return None


class CellDelegate(QStyledItemDelegate):
    """Editor for one cell. Sends ``move_requested`` after saving with Enter, Tab or arrows."""

    move_requested = Signal(int, int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.enter_mode = False
        """True when editing was started by typing: the arrow keys then save and move."""

    def createEditor(  # noqa: N802 (Qt API)
        self,
        parent: QWidget,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> QWidget:
        editor = QLineEdit(parent)
        editor.setFrame(False)
        return editor

    def eventFilter(self, editor: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt API)
        if event.type() == QEvent.Type.KeyPress and isinstance(editor, QLineEdit):
            assert isinstance(event, QKeyEvent)
            step = _key_step(event)
            if (
                step is None
                and self.enter_mode
                and event.modifiers()
                in (
                    Qt.KeyboardModifier.NoModifier,
                    Qt.KeyboardModifier.KeypadModifier,
                )
            ):
                step = _ARROWS.get(Qt.Key(event.key()))
            if step is not None:
                self.commitData.emit(editor)
                self.closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.NoHint)
                self.move_requested.emit(*step)
                return True
        return super().eventFilter(editor, event)


class TableView(QTableView):
    """The table of one document. ``error`` sends why an edit was refused."""

    error = Signal(str)

    def __init__(self, model: TableModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("tableView")
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.AnyKeyPressed
            | QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.setWordWrap(False)
        self.setTabKeyNavigation(True)
        self.setCornerButtonEnabled(True)

        self.cell_delegate = CellDelegate(self)
        self.cell_delegate.move_requested.connect(self.move_cursor)
        self.setItemDelegate(self.cell_delegate)

        self.grouped_header = GroupedHeader(self)
        self.setHorizontalHeader(self.grouped_header)
        self.grouped_header.columns_clicked.connect(
            lambda start, end: self.select_columns(start, end - 1)
        )
        self.grouped_header.move_requested.connect(self._on_move_requested)
        self.grouped_header.rename_requested.connect(
            lambda kind, node_id: self.rename_band(NodeKind(kind), node_id)
        )

        rows = self.verticalHeader()
        rows.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)  # needed for fast scrolling
        rows.setDefaultSectionSize(self.fontMetrics().height() + 8)
        columns = self.horizontalHeader()
        columns.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        columns.setDefaultSectionSize(DEFAULT_COLUMN_WIDTH)
        columns.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        columns.setHighlightSections(True)
        columns.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        columns.customContextMenuRequested.connect(self._show_header_menu)

        self.setModel(model)
        model.error.connect(self.error)
        model.bands_changed.connect(self._on_bands_changed)
        self.setCurrentIndex(model.index(0, 0))

    def model(self) -> TableModel:
        model = super().model()
        assert isinstance(model, TableModel)
        return model

    def setModel(self, model: QAbstractItemModel | None) -> None:  # noqa: N802 (Qt API)
        if not isinstance(model, TableModel):
            raise TypeError("TableView needs a TableModel")
        super().setModel(model)

    # --- painting ------------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt API)
        """Paint the visible cells in one pass.

        Qt's default paints every cell through the item delegate and the style, which
        costs about 0.1 ms per cell from Python: too slow for 60 fps. Here each visible
        column is read as one slice of its array, and colours come from the palette.
        """
        model = self.model()
        area = event.rect()
        painter = QPainter(self.viewport())
        palette = self.palette()
        group = (
            QPalette.ColorGroup.Active if self.isActiveWindow() else QPalette.ColorGroup.Inactive
        )
        painter.fillRect(area, palette.color(group, QPalette.ColorRole.Base))

        first_row, last_row = self.rowAt(area.top()), self.rowAt(area.bottom())
        first_col, last_col = self.columnAt(area.left()), self.columnAt(area.right())
        if first_row < 0 or first_col < 0:
            painter.end()
            return
        last_row = model.rowCount() - 1 if last_row < 0 else last_row
        last_col = model.columnCount() - 1 if last_col < 0 else last_col
        y0 = self.rowViewportPosition(first_row)
        row_h = self.rowHeight(first_row)  # rows have a fixed height
        n_rows = last_row - first_row + 1
        cols = [
            (c, self.columnViewportPosition(c), self.columnWidth(c))
            for c in range(first_col, last_col + 1)
        ]
        bottom = y0 + n_rows * row_h

        # Selected cells.
        visible = [
            (max(t, first_row), max(lf, first_col), min(b, last_row), min(rt, last_col))
            for t, lf, b, rt in self._selection_ranges()
        ]
        visible = [r for r in visible if r[0] <= r[2] and r[1] <= r[3]]
        highlight = palette.color(group, QPalette.ColorRole.Highlight)
        for top, left, bot, right in visible:
            x = self.columnViewportPosition(left)
            x_end = self.columnViewportPosition(right) + self.columnWidth(right)
            y = y0 + (top - first_row) * row_h
            painter.fillRect(x, y, x_end - x, (bot - top + 1) * row_h, highlight)

        # Text.
        text_pen = palette.color(group, QPalette.ColorRole.Text)
        selected_pen = palette.color(group, QPalette.ColorRole.HighlightedText)
        painter.setPen(text_pen)
        painter.setFont(self.font())
        pad = CELL_PADDING
        text_h = row_h
        digit_w = max(self.fontMetrics().horizontalAdvance("0"), 1)
        for c, x, w in cols:
            texts = model.display_texts(c, first_row, last_row + 1, (w - 2 * pad) // digit_w)
            if not texts:
                continue
            align = _TEXT_FLAGS if model.is_text_column(c) else _NUMBER_FLAGS
            selected_rows = [(t, b) for t, lf, b, rt in visible if lf <= c <= rt]
            y = y0
            for i, text in enumerate(texts):
                if text:
                    rect = QRect(x + pad, y, w - 2 * pad, text_h)
                    if selected_rows and any(t <= first_row + i <= b for t, b in selected_rows):
                        painter.setPen(selected_pen)
                        painter.drawText(rect, align, text)
                        painter.setPen(text_pen)
                    else:
                        painter.drawText(rect, align, text)
                y += row_h

        # Grid.
        if self.showGrid():
            option = QStyleOptionViewItem()
            self.initViewItemOption(option)
            hint = self.style().styleHint(QStyle.StyleHint.SH_Table_GridLineColor, option, self)
            grid = QColor.fromRgba(hint & 0xFFFFFFFF)
            painter.setPen(grid)
            right_edge = cols[-1][1] + cols[-1][2]
            lines = [QLine(x + w - 1, area.top(), x + w - 1, bottom - 1) for _, x, w in cols]
            lines += [
                QLine(
                    area.left(), y0 + (i + 1) * row_h - 1, right_edge - 1, y0 + (i + 1) * row_h - 1
                )
                for i in range(n_rows)
            ]
            painter.drawLines(lines)

        # The current cell.
        current = self.currentIndex()
        if current.isValid() and self.state() != QAbstractItemView.State.EditingState:
            r, c = current.row(), current.column()
            if first_row <= r <= last_row and first_col <= c <= last_col:
                pen = QPen(highlight.darker(130) if visible else highlight, 2)
                pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                x, w = self.columnViewportPosition(c), self.columnWidth(c)
                y = y0 + (r - first_row) * row_h
                painter.drawRect(x + 1, y + 1, w - 3, row_h - 3)
        painter.end()

    def _selection_ranges(self) -> list[Range]:
        selection = self.selectionModel().selection()
        return [
            (r.top(), r.left(), r.bottom(), r.right())
            for r in (selection.at(i) for i in range(selection.size()))
        ]

    # --- navigation ----------------------------------------------------------------------

    def edit(  # type: ignore[override]
        self,
        index: QModelIndex | QPersistentModelIndex,
        trigger: QAbstractItemView.EditTrigger = QAbstractItemView.EditTrigger.AllEditTriggers,
        event: QEvent | None = None,
    ) -> bool:
        self.cell_delegate.enter_mode = trigger == QAbstractItemView.EditTrigger.AnyKeyPressed
        return super().edit(index, trigger, event)  # type: ignore[arg-type]  # Qt allows null

    def move_cursor(self, d_row: int, d_col: int) -> None:
        """Move the current cell by (d_row, d_col) and select only it."""
        model = self.model()
        current = self.currentIndex()
        row = max(0, min(current.row() + d_row, model.rowCount() - 1))
        col = max(0, min(current.column() + d_col, model.columnCount() - 1))
        self.selectionModel().setCurrentIndex(
            model.index(row, col), QItemSelectionModel.SelectionFlag.ClearAndSelect
        )

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt API)
        if self.state() != QAbstractItemView.State.EditingState:
            if event.matches(QKeySequence.StandardKey.Copy):
                self.copy_selection()
                return
            if event.matches(QKeySequence.StandardKey.Paste):
                self.paste()
                return
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and not (
                event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
            ):
                self.clear_selected_cells()
                return
            step = _key_step(event)
            if step is not None:
                self.move_cursor(*step)
                return
            if event.key() == Qt.Key.Key_F2 and self.currentIndex().isValid():
                # Qt's own edit key is Return on macOS, which moves down here.
                self.edit(self.currentIndex(), QAbstractItemView.EditTrigger.EditKeyPressed, event)
                return
        super().keyPressEvent(event)

    # --- selection -----------------------------------------------------------------------

    def selected_ranges(self) -> list[Range]:
        """Selected blocks, or the current cell if nothing is selected."""
        ranges = self._selection_ranges()
        if not ranges:
            current = self.currentIndex()
            if current.isValid():
                ranges = [(current.row(), current.column(), current.row(), current.column())]
        return ranges

    def selected_columns(self) -> list[int]:
        """Positions of data columns with a selected cell, or of the current column."""
        cols = {c for _, left, _, right in self.selected_ranges() for c in range(left, right + 1)}
        return sorted(c for c in cols if c < self.model().n_data_columns)

    def select_block(self, top: int, left: int, bottom: int, right: int) -> None:
        model = self.model()
        selection = QItemSelection(model.index(top, left), model.index(bottom, right))
        flags = QItemSelectionModel.SelectionFlag.ClearAndSelect
        self.selectionModel().select(selection, flags)
        self.selectionModel().setCurrentIndex(
            model.index(top, left), QItemSelectionModel.SelectionFlag.NoUpdate
        )

    def select_columns(self, left: int, right: int) -> None:
        """Select whole columns ``left`` to ``right``, spare rows included."""
        self.select_block(0, left, self.model().rowCount() - 1, right)

    # --- clipboard -----------------------------------------------------------------------

    def selection_tsv(self) -> str:
        """The selection as tab-separated text, at full precision.

        Several blocks are copied as their bounding rectangle, with cells outside the
        blocks left empty. Spare rows and columns past the data are left out.
        """
        ranges = self.selected_ranges()
        if not ranges:
            return ""
        model = self.model()
        top = min(r[0] for r in ranges)
        left = min(r[1] for r in ranges)
        bottom = min(max(r[2] for r in ranges), model.n_data_rows - 1)
        right = min(max(r[3] for r in ranges), model.n_data_columns - 1)
        if bottom < top or right < left:
            return ""

        def selected(row: int, col: int) -> bool:
            return any(t <= row <= b and lf <= col <= rt for t, lf, b, rt in ranges)

        single = len(ranges) == 1
        rows = [
            [
                cell_text(model.value(row, col)) if single or selected(row, col) else ""
                for col in range(left, right + 1)
            ]
            for row in range(top, bottom + 1)
        ]
        return to_tsv(rows)

    def copy_selection(self) -> None:
        text = self.selection_tsv()
        if text:
            QGuiApplication.clipboard().setText(text)

    def paste(self, text: str | None = None) -> bool:
        """Paste tab-separated text (default: the clipboard) at the selection, as one step."""
        if text is None:
            text = QGuiApplication.clipboard().text()
        rows = from_tsv(text)
        ranges = self.selected_ranges()
        if not rows or not rows[0] or not ranges:
            return False
        top = min(r[0] for r in ranges)
        left = min(r[1] for r in ranges)
        model = self.model()
        left = min(left, model.n_data_columns)
        if not model.write_block(top, left, rows):
            return False
        self.select_block(top, left, top + len(rows) - 1, left + len(rows[0]) - 1)
        return True

    def clear_selected_cells(self) -> bool:
        return self.model().clear_ranges(self.selected_ranges())

    # --- columns -------------------------------------------------------------------------

    def insert_column_left(self) -> bool:
        cols = self.selected_columns()
        position = cols[0] if cols else self.model().n_data_columns
        return self._insert_column(position, cols[0] if cols else None)

    def insert_column_right(self) -> bool:
        cols = self.selected_columns()
        position = cols[-1] + 1 if cols else self.model().n_data_columns
        return self._insert_column(position, cols[-1] if cols else None)

    def _insert_column(self, position: int, beside: int | None) -> bool:
        if not self.model().insert_column(position, beside=beside):
            return False
        index = self.model().index(max(self.currentIndex().row(), 0), position)
        self.selectionModel().setCurrentIndex(
            index, QItemSelectionModel.SelectionFlag.ClearAndSelect
        )
        return True

    def delete_selected_columns(self) -> bool:
        return self.model().remove_columns(self.selected_columns())

    def rename_current_column(self) -> bool:
        cols = self.selected_columns()
        if not cols:
            return False
        column = self.model().column_at(cols[0])
        assert column is not None
        name = self._ask_column_name(column.name)
        if name is None:
            return False
        return self.model().rename_column(cols[0], name)

    def set_role_of_selected(self, role: ColumnRole) -> bool:
        return self.model().set_role(self.selected_columns(), role)

    def _ask_column_name(self, current: str) -> str | None:
        """Ask for a new column name (tests replace this)."""
        return self._ask_text("Rename column", "Column name:", current)

    def _ask_text(self, title: str, label: str, current: str) -> str | None:
        """Ask for a line of text (tests replace this)."""
        text, ok = QInputDialog.getText(self, title, label, QLineEdit.EchoMode.Normal, current)
        return text if ok else None

    def _ask_category(self, current: str, existing: list[str]) -> str | None:
        """Ask for a category name, offering the existing ones (tests replace this)."""
        text, ok = QInputDialog.getItem(
            self,
            "Set category",
            f"{self.model().table.category_factor}:",
            existing,
            existing.index(current) if current in existing else 0,
            True,
        )
        return text if ok else None

    # --- groups --------------------------------------------------------------------------

    def group_selected(self) -> bool:
        """Combine the selected columns into a replicate group (asks for its name)."""
        cols = self.selected_columns()
        if not cols:
            return False
        name = self._ask_text("Group as replicates", "Group name:", self.model().next_group_name())
        if name is None or not self.model().group_columns(cols, name):
            return False
        self._select_ids([c.id for c in self.model().columns_at(cols)])
        return True

    def ungroup_selected(self) -> bool:
        return self.model().ungroup_columns(self.selected_columns())

    def set_category_of_selected(self) -> bool:
        """Put the selected series in a category (asks which)."""
        model = self.model()
        cols = self.selected_columns()
        if not cols:
            return False
        table = model.table
        existing = [c.name for c in table.category_groups]
        current = model.category_at(cols) or ""
        name = self._ask_category(current, existing)
        if name is None or not name.strip():
            return False
        return model.set_category(cols, name)

    def remove_category_of_selected(self) -> bool:
        return self.model().set_category(self.selected_columns(), None)

    def rename_category_factor(self) -> bool:
        """Rename the second factor (the one whose levels are the categories)."""
        model = self.model()
        name = self._ask_text(
            "Rename category factor", "Name of the second factor:", model.table.category_factor
        )
        return name is not None and model.rename_category_factor(name)

    def rename_band(self, kind: NodeKind, node_id: str) -> bool:
        """Rename a replicate group or category (asks for the name)."""
        table = self.model().table
        if kind is NodeKind.GROUP:
            title, current = "Rename replicate group", table.replicate_group(node_id).name
        else:
            title, current = "Rename category", table.category_group(node_id).name
        name = self._ask_text(title, "Name:", current)
        if name is None or name == current:
            return False
        return self.model().rename_band(kind, node_id, name)

    def bulk_rename(self) -> bool:
        """Open the bulk rename dialog; the result is one undoable command."""
        model = self.model()
        cols = self.selected_columns()
        checked = [c.id for c in model.columns_at(cols)] if len(cols) > 1 else []
        dialog = BulkRenameDialog(model.table, checked, self)
        if not self._exec_dialog(dialog):
            return False
        renames = dialog.renames()
        if not renames:
            return False
        return model.bulk_rename(renames.columns, renames.groups, renames.categories)

    def _exec_dialog(self, dialog: BulkRenameDialog) -> bool:
        """Run a dialog; True if accepted (tests replace this)."""
        return dialog.exec() == BulkRenameDialog.DialogCode.Accepted

    def move_selected_left(self) -> bool:
        return self._move_selected(-1)

    def move_selected_right(self) -> bool:
        return self._move_selected(1)

    def _move_selected(self, step: int) -> bool:
        """Move the selected column, or the group or category it covers, by one place."""
        model = self.model()
        cols = self.selected_columns()
        ids = [c.id for c in model.columns_at(cols)]
        if model.move_by(cols, step) is None:
            return False
        self._select_ids(ids)
        return True

    def _on_move_requested(self, kind: str, node_id: str, boundary: int) -> None:
        model = self.model()
        node = NodeKind(kind)
        if node is NodeKind.COLUMN:
            moved = [node_id]
        else:
            span = model.node_span(node, node_id)
            moved = [] if span is None else [c.id for c in model.table.columns[span[0] : span[1]]]
        if model.move_node(node, node_id, boundary):
            self._select_ids(moved)

    def _select_ids(self, column_ids: list[str]) -> None:
        """Select the whole columns with ``column_ids`` (they are next to each other)."""
        ids = set(column_ids)
        positions = [i for i, c in enumerate(self.model().table.columns) if c.id in ids]
        if positions:
            self.select_columns(positions[0], positions[-1])

    def _on_bands_changed(self) -> None:
        self.updateGeometries()
        self.grouped_header.viewport().update()

    # --- header menu ---------------------------------------------------------------------

    def header_menu(self, section: int) -> QMenu:
        """Context menu for the column header at ``section``; selects that column first."""
        model = self.model()
        if section not in self.selected_columns() or section >= model.n_data_columns:
            self.select_columns(section, section)
        menu = QMenu(self)
        cols = self.selected_columns()
        has_columns = bool(cols)
        if has_columns:
            menu.addAction("Insert column &left", self.insert_column_left)
            menu.addAction("Insert column &right", self.insert_column_right)
        else:
            menu.addAction("&Add column", self.insert_column_right)
        rename = menu.addAction("Re&name column…", self.rename_current_column)
        rename.setEnabled(len(cols) == 1)
        label = "&Delete column" if len(cols) <= 1 else f"&Delete {len(cols)} columns"
        delete = menu.addAction(label, self.delete_selected_columns)
        delete.setEnabled(has_columns)
        menu.addSeparator()
        roles_menu = menu.addMenu("Role")
        roles_menu.setEnabled(has_columns)
        group = QActionGroup(roles_menu)
        roles = {c.role for c in model.columns_at(cols)}
        for role in ColumnRole:
            action = QAction(role.value, roles_menu)
            action.setCheckable(True)
            action.setChecked(roles == {role})
            action.triggered.connect(lambda _=False, r=role: self.set_role_of_selected(r))
            group.addAction(action)
            roles_menu.addAction(action)
        menu.addSeparator()
        columns = model.columns_at(cols)
        grouped = any(c.group_id is not None for c in columns)
        group_action = menu.addAction("&Group as replicates…", self.group_selected)
        group_action.setEnabled(has_columns and all(c.role is ColumnRole.Y for c in columns))
        menu.addAction("&Ungroup", self.ungroup_selected).setEnabled(grouped)
        menu.addAction("Set &category…", self.set_category_of_selected).setEnabled(
            any(c.role is ColumnRole.Y for c in columns)
        )
        menu.addAction("Remove from categor&y", self.remove_category_of_selected).setEnabled(
            model.category_at(cols) is not None
        )
        menu.addSeparator()
        menu.addAction("Move &left", self.move_selected_left).setEnabled(has_columns)
        menu.addAction("Move righ&t", self.move_selected_right).setEnabled(has_columns)
        menu.addAction("&Bulk rename…", self.bulk_rename)
        return menu

    def _show_header_menu(self, pos: QPoint) -> None:
        header = self.horizontalHeader()
        section = header.logicalIndexAt(pos)
        if section < 0:
            return
        self.header_menu(section).exec(header.mapToGlobal(pos))
