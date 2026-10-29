"""Qt model over one table of a document.

The model reads the columns' NumPy arrays directly; Qt asks only for the cells it is
about to paint. Every change is sent to the document as a command. The model follows
the undo stack, so edits, undo and redo all reach the view the same way.

Below and to the right of the data the model shows spare empty rows and columns.
Writing into them adds rows, and new Y columns, in the same command as the write.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QPersistentModelIndex, Qt
from PySide6.QtCore import Signal as QtSignal

from curvis.core import (
    AddColumn,
    Command,
    CommandError,
    CommandGroup,
    Document,
    RemoveColumn,
    RenameColumn,
    SetCellBlock,
    SetCellValue,
    SetColumnRole,
    UndoEvent,
)
from curvis.data import CellValue, Column, ColumnRole, Table
from curvis.data.names import next_column_names
from curvis.ui.table.format import cell_text, fitted_number_text

SPARE_ROWS = 20
"""Empty rows shown below the data, to type or paste into."""

SPARE_COLUMNS = 3
"""Empty columns shown right of the data."""

type Index = QModelIndex | QPersistentModelIndex

_NUMBER_ALIGNMENT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
_TEXT_ALIGNMENT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter


def _never_calculated(_column: Column) -> bool:
    return False


class TableModel(QAbstractTableModel):
    """Table model for one table of ``document``. ``error`` sends why a change was refused."""

    error = QtSignal(str)

    def __init__(
        self,
        document: Document,
        table_id: str,
        *,
        decimal_places: int = 4,
        is_calculated: Callable[[Column], bool] = _never_calculated,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.document = document
        self.table_id = table_id
        self._decimal_places = decimal_places
        self._is_calculated = is_calculated
        # What the view was last told. Kept so insert/remove signals match the view.
        self._columns: list[Column] = list(self.table.columns)
        self._n_rows = self.table.n_rows
        self._unsubscribe = document.undo_stack.subscribe(self._on_undo_event)
        self.destroyed.connect(lambda *_: self._unsubscribe())

    @property
    def table(self) -> Table:
        return self.document.table(self.table_id)

    @property
    def n_data_rows(self) -> int:
        return self._n_rows

    @property
    def n_data_columns(self) -> int:
        return len(self._columns)

    def column_at(self, col: int) -> Column | None:
        """The column shown at position ``col``, or None for a spare column."""
        return self._columns[col] if 0 <= col < len(self._columns) else None

    def columns_at(self, positions: Sequence[int]) -> list[Column]:
        """The data columns at ``positions``, left to right (spare positions are skipped)."""
        found = (self.column_at(p) for p in sorted(set(positions)))
        # Not ``if c``: an empty column has length 0.
        return [c for c in found if c is not None]

    def detach(self) -> None:
        """Stop following the document (the model shows the last state it had)."""
        self._unsubscribe()

    # --- decimal places ----------------------------------------------------------------

    @property
    def decimal_places(self) -> int:
        return self._decimal_places

    def set_decimal_places(self, places: int) -> None:
        """Decimal places shown in calculated columns."""
        if places != self._decimal_places:
            self._decimal_places = places
            self._all_data_changed()

    def set_is_calculated(self, predicate: Callable[[Column], bool]) -> None:
        """Set how the model tells calculated columns apart (wired up in step 1.5)."""
        self._is_calculated = predicate
        self._all_data_changed()

    # --- reading -----------------------------------------------------------------------

    def rowCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008, N802 (Qt API)
        return 0 if parent.isValid() else self._n_rows + SPARE_ROWS

    def columnCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008, N802 (Qt API)
        return 0 if parent.isValid() else len(self._columns) + SPARE_COLUMNS

    def value(self, row: int, col: int) -> CellValue:
        """The stored value of a cell; None for spare cells and missing text."""
        column = self.column_at(col)
        if column is None or row >= len(column.values):
            return None
        raw = column.values[row]
        return float(raw) if column.role.is_numeric else raw

    def display_texts(self, col: int, start: int, stop: int, max_chars: int) -> list[str]:
        """Shown text of rows ``start`` to ``stop`` (exclusive) of a column, for painting.

        Reads one slice of the column's array; the list stops where the data stops.
        Numbers are fitted to ``max_chars`` digits (see :func:`fitted_number_text`).
        """
        column = self.column_at(col)
        if column is None:
            return []
        chunk = column.values[start:stop]
        if not column.role.is_numeric:
            return ["" if v is None else v for v in chunk]
        decimals = self._decimal_places if self._is_calculated(column) else None
        return [fitted_number_text(v, max_chars, decimals) for v in chunk.tolist()]

    def is_text_column(self, col: int) -> bool:
        column = self.column_at(col)
        return column is not None and not column.role.is_numeric

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if role == Qt.ItemDataRole.DisplayRole:
            column = self.column_at(index.column())
            if column is None:
                return None
            row = index.row()
            if row >= len(column.values):
                return None
            raw = column.values[row]
            if not column.role.is_numeric:
                return raw
            decimals = self._decimal_places if self._is_calculated(column) else None
            return cell_text(float(raw), decimals)
        if role in (Qt.ItemDataRole.EditRole, Qt.ItemDataRole.ToolTipRole):
            text = cell_text(self.value(index.row(), index.column()))
            return text if text or role == Qt.ItemDataRole.EditRole else None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            column = self.column_at(index.column())
            if column is not None and not column.role.is_numeric:
                return _TEXT_ALIGNMENT
            return _NUMBER_ALIGNMENT
        return None

    def headerData(  # noqa: N802 (Qt API)
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Vertical:
                return str(section + 1)
            column = self.column_at(section)
            return f"{column.name}\n{column.role.value}" if column is not None else ""
        if role == Qt.ItemDataRole.ToolTipRole and orientation == Qt.Orientation.Horizontal:
            column = self.column_at(section)
            if column is not None:
                return f"{column.name} ({column.role.value} column)"
        return None

    def flags(self, index: Index) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable

    # --- writing -----------------------------------------------------------------------

    def setData(self, index: Index, value: object, role: int = Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        text = "" if value is None else str(value)
        return self.write_block(index.row(), index.column(), [[text]])

    def write_block(self, top: int, left: int, values: Sequence[Sequence[CellValue]]) -> bool:
        """Write a block of values with its top-left cell at (top, left), as one command.

        Columns needed past the last one are added as Y columns. Returns False (and
        sends ``error``) if the block was refused.
        """
        if not values or not values[0]:
            return False
        table = self.table
        width = len(values[0])
        if left > len(table.columns):
            # Leave no gap: the block starts in the first spare column.
            left = len(table.columns)
        missing = left + width - len(table.columns)
        commands: list[Command] = []
        if missing > 0:
            names = next_column_names([c.name for c in table.columns], missing)
            commands += [AddColumn(self.table_id, Column.new(n, ColumnRole.Y)) for n in names]
        if missing <= 0 and len(values) == 1 and width == 1:
            column = table.columns[left]
            return self.execute(SetCellValue(self.table_id, column.id, top, values[0][0]))
        block = SetCellBlock(self.table_id, top, left, values)
        commands.append(block)
        return self.execute(CommandGroup(block_summary(values, top), commands))

    def clear_ranges(self, ranges: Sequence[tuple[int, int, int, int]]) -> bool:
        """Clear cells in (top, left, bottom, right) ranges (inclusive), as one command.

        The parts of the ranges outside the data are ignored.
        """
        commands: list[Command] = []
        cells = 0
        n_rows, n_cols = self.table.n_rows, len(self.table.columns)
        for top, left, bottom, right in ranges:
            bottom, right = min(bottom, n_rows - 1), min(right, n_cols - 1)
            if top > bottom or left > right:
                continue
            blank = [[None] * (right - left + 1) for _ in range(bottom - top + 1)]
            commands.append(SetCellBlock(self.table_id, top, left, blank))
            cells += len(blank) * len(blank[0])
        if not commands:
            return False
        return self.execute(CommandGroup(f"Clear {_cells(cells)}", commands))

    def insert_column(self, position: int, role: ColumnRole = ColumnRole.Y) -> bool:
        """Add a new, empty column at ``position`` (named A, B, … as free)."""
        table = self.table
        position = max(0, min(position, len(table.columns)))
        (name,) = next_column_names([c.name for c in table.columns])
        column = Column.new(name, role)
        return self.execute(AddColumn(self.table_id, column, position))

    def remove_columns(self, positions: Sequence[int]) -> bool:
        """Delete the columns at ``positions``, as one command."""
        columns = self.columns_at(positions)
        if not columns:
            return False
        commands: list[Command] = [RemoveColumn(self.table_id, c.id) for c in columns]
        if len(commands) == 1:
            return self.execute(commands[0])
        return self.execute(CommandGroup(f"Delete {len(columns)} columns", commands))

    def rename_column(self, position: int, name: str) -> bool:
        column = self.column_at(position)
        if column is None or name == column.name:
            return False
        return self.execute(RenameColumn(self.table_id, column.id, name.strip()))

    def set_role(self, positions: Sequence[int], role: ColumnRole) -> bool:
        """Set the role of the columns at ``positions``, as one command."""
        columns = self.columns_at(positions)
        commands: list[Command] = [
            SetColumnRole(self.table_id, c.id, role) for c in columns if c.role != role
        ]
        if not commands:
            return False
        if len(commands) == 1:
            return self.execute(commands[0])
        return self.execute(
            CommandGroup(f"Set role of {len(commands)} columns to {role.value}", commands)
        )

    def execute(self, command: Command) -> bool:
        """Run ``command`` on the document; on refusal send ``error`` and return False."""
        try:
            self.document.execute(command)
        except CommandError as exc:
            self.error.emit(str(exc))
            return False
        return True

    # --- following the document --------------------------------------------------------

    def _on_undo_event(self, event: UndoEvent) -> None:
        if event.command is not None and self.table_id not in event.command.touched_nodes:
            return
        try:
            table = self.table
        except KeyError:
            return  # the table is gone; the document tab replaces this view
        self.sync(table)

    def sync(self, table: Table | None = None) -> None:
        """Tell the view what changed since it was last told."""
        table = table if table is not None else self.table
        self._sync_columns(list(table.columns))
        self._sync_rows(table.n_rows)
        self._all_data_changed()
        last = self.columnCount() - 1
        if last >= 0:
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, last)

    def _sync_columns(self, new: list[Column]) -> None:
        old = self._columns
        old_ids, new_ids = [c.id for c in old], [c.id for c in new]
        if old_ids == new_ids:
            self._columns = new
            return
        # Common prefix and suffix; the middle is what was inserted or removed.
        start = 0
        while start < min(len(old), len(new)) and old_ids[start] == new_ids[start]:
            start += 1
        end_old, end_new = len(old), len(new)
        while end_old > start and end_new > start and old_ids[end_old - 1] == new_ids[end_new - 1]:
            end_old -= 1
            end_new -= 1
        if end_old == start:  # pure insertion
            self.beginInsertColumns(QModelIndex(), start, end_new - 1)
            self._columns = new
            self.endInsertColumns()
        elif end_new == start:  # pure removal
            self.beginRemoveColumns(QModelIndex(), start, end_old - 1)
            self._columns = new
            self.endRemoveColumns()
        elif sorted(old_ids) == sorted(new_ids):  # a move
            self.layoutAboutToBeChanged.emit()
            self._columns = new
            self.layoutChanged.emit()
        else:
            self.beginResetModel()
            self._columns = new
            self.endResetModel()

    def _sync_rows(self, n_rows: int) -> None:
        old_total, new_total = self._n_rows + SPARE_ROWS, n_rows + SPARE_ROWS
        if new_total > old_total:
            self.beginInsertRows(QModelIndex(), old_total, new_total - 1)
            self._n_rows = n_rows
            self.endInsertRows()
        elif new_total < old_total:
            self.beginRemoveRows(QModelIndex(), new_total, old_total - 1)
            self._n_rows = n_rows
            self.endRemoveRows()

    def _all_data_changed(self) -> None:
        rows, cols = self.rowCount(), self.columnCount()
        if rows and cols:
            self.dataChanged.emit(self.index(0, 0), self.index(rows - 1, cols - 1))


def block_summary(values: Sequence[Sequence[CellValue]], top: int) -> str:
    """Change-log text for a block write, e.g. "Paste 6 cells from row 3"."""
    return f"Paste {_cells(len(values) * len(values[0]))} from row {top + 1}"


def _cells(n: int) -> str:
    return "1 cell" if n == 1 else f"{n} cells"
