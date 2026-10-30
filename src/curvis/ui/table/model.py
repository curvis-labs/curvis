"""Qt model over one table of a document.

The model reads the columns' NumPy arrays directly; Qt asks only for the cells it is
about to paint. Every change is sent to the document as a command. The model follows
the undo stack, so edits, undo and redo all reach the view the same way.

Below and to the right of the data the model shows spare empty rows and columns.
Writing into them adds rows, and new Y columns, in the same command as the write.

Above the column names the header shows *bands*: one row of category groups and one
of replicate groups, each only when the table has any (see :meth:`TableModel.bands`).

Calculated columns are read-only: their cells cannot be edited, and the header marks
them with ``ƒ`` (and ``⚠`` when their formula fails).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QPersistentModelIndex, Qt
from PySide6.QtCore import Signal as QtSignal

from curvis.core import (
    AddColumn,
    BulkRename,
    Command,
    CommandError,
    CommandGroup,
    Document,
    GroupColumns,
    RemoveColumn,
    RenameCategoryFactor,
    RenameColumn,
    ReorderColumns,
    SetCategory,
    SetCellBlock,
    SetCellValue,
    SetColumnRole,
    UndoEvent,
    UngroupColumns,
)
from curvis.data import CellValue, Column, ColumnRole, NodeKind, Table
from curvis.data.layout import column_category, find_node, layout_tree, moved_order, snap_boundary
from curvis.data.names import name_key, next_column_names
from curvis.ui.table.format import cell_text, fitted_number_text

SPARE_ROWS = 20
"""Empty rows shown below the data, to type or paste into."""

SPARE_COLUMNS = 3
"""Empty columns shown right of the data."""

type Index = QModelIndex | QPersistentModelIndex

_NUMBER_ALIGNMENT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
_TEXT_ALIGNMENT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter


CALCULATED_MARK = "ƒ"
ERROR_MARK = "⚠"


def _no_error(_column_id: str) -> str | None:
    return None


@dataclass(frozen=True)
class Band:
    """A category or replicate group drawn above columns ``start`` to ``end - 1``."""

    kind: NodeKind
    id: str
    name: str
    start: int
    end: int


def _bands(table: Table) -> list[list[Band | None]]:
    """Header bands, top row first; each row has one entry per column (None: no band)."""
    if not table.replicate_groups and not table.category_groups:
        return []
    n = len(table.columns)
    categories: list[Band | None] = [None] * n
    groups: list[Band | None] = [None] * n
    for node in layout_tree(table):
        if node.kind is NodeKind.CATEGORY:
            name = table.category_group(node.id).name
            band = Band(node.kind, node.id, name, node.start, node.end)
            categories[node.start : node.end] = [band] * (node.end - node.start)
        children = node.children if node.kind is NodeKind.CATEGORY else (node,)
        for child in children:
            if child.kind is NodeKind.GROUP:
                name = table.replicate_group(child.id).name
                band = Band(child.kind, child.id, name, child.start, child.end)
                groups[child.start : child.end] = [band] * (child.end - child.start)
    rows = [(categories, table.category_groups), (groups, table.replicate_groups)]
    return [row for row, used in rows if used]


class TableModel(QAbstractTableModel):
    """Table model for one table of ``document``. ``error`` sends why a change was refused."""

    error = QtSignal(str)
    bands_changed = QtSignal()
    """The header bands (groups above columns) changed."""

    def __init__(
        self,
        document: Document,
        table_id: str,
        *,
        decimal_places: int = 4,
        is_calculated: Callable[[Column], bool] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.document = document
        self.table_id = table_id
        self._decimal_places = decimal_places
        self._is_calculated = is_calculated or (lambda c: document.is_calculated(c.id))
        self._calculation_error: Callable[[str], str | None] = _no_error
        # What the view was last told. Kept so insert/remove signals match the view.
        self._columns: list[Column] = list(self.table.columns)
        self._n_rows = self.table.n_rows
        self._bands = _bands(self.table)
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

    def bands(self) -> list[list[Band | None]]:
        """Header bands above the columns, top row first (empty without groups).

        Each row has one entry per data column: the band drawn above it, or None.
        """
        return self._bands

    def band_at(self, row: int, col: int) -> Band | None:
        """The band in band row ``row`` above column ``col``, or None."""
        if 0 <= row < len(self._bands) and 0 <= col < len(self._bands[row]):
            return self._bands[row][col]
        return None

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
        """Set how the model tells calculated columns apart (default: from the document)."""
        self._is_calculated = predicate
        self._all_data_changed()

    # --- calculated columns ------------------------------------------------------------

    def is_calculated_column(self, col: int) -> bool:
        column = self.column_at(col)
        return column is not None and self._is_calculated(column)

    def set_calculation_errors(self, errors: Callable[[str], str | None]) -> None:
        """Set where the model reads why a calculated column (by id) has no values."""
        self._calculation_error = errors

    def refresh_values(self) -> None:
        """Calculated values or their errors changed (they change without a command)."""
        self.sync()

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
            if column is None:
                return ""
            role_text = column.role.value
            if self._is_calculated(column):
                role_text = f"{CALCULATED_MARK} {role_text}"
                if self._calculation_error(column.id):
                    role_text += f" {ERROR_MARK}"
            return f"{column.name}\n{role_text}"
        if role == Qt.ItemDataRole.ToolTipRole and orientation == Qt.Orientation.Horizontal:
            column = self.column_at(section)
            if column is not None:
                tip = f"{column.name} ({column.role.value} column)"
                if column.group_id is not None:
                    tip += (
                        f" in replicate group '{self.table.replicate_group(column.group_id).name}'"
                    )
                calculation = self.document.calculation_of(column.id)
                if calculation is not None:
                    tip += f"\nCalculated: {calculation.formula}"
                    error = self._calculation_error(column.id)
                    if error:
                        tip += f"\nError: {error}"
                return tip
        return None

    def band_tooltip(self, band: Band) -> str:
        table = self.table
        if band.kind is NodeKind.CATEGORY:
            return f"{table.category_factor}: {band.name}"
        n = band.end - band.start
        return f"Replicate group '{band.name}' ({n} column{'' if n == 1 else 's'})"

    def flags(self, index: Index) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if not self.is_calculated_column(index.column()):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

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

        The parts of the ranges outside the data, and calculated columns, are ignored.
        """
        commands: list[Command] = []
        cells = 0
        n_rows, n_cols = self.table.n_rows, len(self.table.columns)
        for top, left, bottom, right in ranges:
            bottom, right = min(bottom, n_rows - 1), min(right, n_cols - 1)
            if top > bottom:
                continue
            for start, end in self._editable_runs(left, right):
                blank = [[None] * (end - start + 1) for _ in range(bottom - top + 1)]
                commands.append(SetCellBlock(self.table_id, top, start, blank))
                cells += len(blank) * len(blank[0])
        if not commands:
            return False
        return self.execute(CommandGroup(f"Clear {_cells(cells)}", commands))

    def _editable_runs(self, left: int, right: int) -> list[tuple[int, int]]:
        """Runs (first, last) of columns from ``left`` to ``right`` that are not calculated."""
        runs: list[tuple[int, int]] = []
        for col in range(left, right + 1):
            if self.is_calculated_column(col):
                continue
            if runs and runs[-1][1] == col - 1:
                runs[-1] = (runs[-1][0], col)
            else:
                runs.append((col, col))
        return runs

    def insert_column(
        self, position: int, role: ColumnRole = ColumnRole.Y, *, beside: int | None = None
    ) -> bool:
        """Add a new, empty column at ``position`` (named A, B, … as free).

        A Y column inserted next to the column at ``beside`` joins its replicate group,
        so adding a column beside a replicate adds another replicate. Beside a column
        that sits in a category directly, it joins that category as a new series.
        """
        table = self.table
        position = max(0, min(position, len(table.columns)))
        (name,) = next_column_names([c.name for c in table.columns])
        neighbour = self.column_at(beside) if beside is not None else None
        group_id = category_id = None
        if neighbour is not None and role is ColumnRole.Y:
            group_id, category_id = neighbour.group_id, neighbour.category_id
        column = Column.new(name, role, group_id=group_id, category_id=category_id)
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

    # --- groups ------------------------------------------------------------------------

    def group_ids_at(self, positions: Sequence[int]) -> list[str]:
        """Replicate groups of the columns at ``positions``, left to right, each once."""
        ids = [c.group_id for c in self.columns_at(positions) if c.group_id is not None]
        return list(dict.fromkeys(ids))

    def next_group_name(self) -> str:
        """A free replicate group name: "Group 1", "Group 2", …"""
        taken = {name_key(g.name) for g in self.table.replicate_groups}
        n = 1
        while name_key(f"Group {n}") in taken:
            n += 1
        return f"Group {n}"

    def group_columns(self, positions: Sequence[int], name: str) -> bool:
        """Combine the columns at ``positions`` into a new replicate group."""
        columns = self.columns_at(positions)
        if not columns:
            return False
        return self.execute(GroupColumns(self.table_id, [c.id for c in columns], name.strip()))

    def ungroup_columns(self, positions: Sequence[int]) -> bool:
        """Take the columns at ``positions`` out of their replicate groups."""
        columns = self.columns_at(positions)
        if not columns:
            return False
        return self.execute(UngroupColumns(self.table_id, [c.id for c in columns]))

    def set_category(self, positions: Sequence[int], category: str | None) -> bool:
        """Put the series at ``positions`` in a category (None: take them out).

        The series are the replicate groups of the selected columns and the selected
        Y columns that are not in a group (each a series of its own).
        """
        columns = self.columns_at(positions)
        group_ids = self.group_ids_at(positions)
        column_ids = [
            c.id
            for c in columns
            if c.group_id is None
            and c.role is ColumnRole.Y
            and (category is not None or c.category_id is not None)
        ]
        if not group_ids and not column_ids:
            self.error.emit("Select Y columns or replicate groups to put in a category.")
            return False
        name = None if category is None else category.strip()
        return self.execute(SetCategory(self.table_id, group_ids, name, column_ids=column_ids))

    def category_at(self, positions: Sequence[int]) -> str | None:
        """Name of the category of the first selected series that has one, or None."""
        table = self.table
        for column in self.columns_at(positions):
            category_id = column_category(table, column)
            if category_id is not None:
                return table.category_group(category_id).name
        return None

    def rename_band(self, kind: NodeKind, node_id: str, name: str) -> bool:
        """Rename a replicate group or category group."""
        name = name.strip()
        if kind is NodeKind.GROUP:
            return self.execute(BulkRename(self.table_id, groups={node_id: name}))
        return self.execute(BulkRename(self.table_id, categories={node_id: name}))

    def rename_category_factor(self, name: str) -> bool:
        if name.strip() == self.table.category_factor:
            return False
        return self.execute(RenameCategoryFactor(self.table_id, name.strip()))

    def bulk_rename(
        self,
        columns: Mapping[str, str],
        groups: Mapping[str, str],
        categories: Mapping[str, str],
    ) -> bool:
        """Rename columns, groups and categories (id to new name) as one undo step."""
        return self.execute(BulkRename(self.table_id, columns, groups, categories))

    # --- moving ------------------------------------------------------------------------

    def drop_boundary(self, kind: NodeKind, node_id: str, gap: int) -> int | None:
        """Where a dragged column, group or category lands if dropped at ``gap``."""
        return snap_boundary(self.table, kind, node_id, gap)

    def node_span(self, kind: NodeKind, node_id: str) -> tuple[int, int] | None:
        """The columns (start, end) a column, group or category covers."""
        found = find_node(self.table, kind, node_id)
        return None if found is None else (found[0].start, found[0].end)

    def node_at(self, positions: Sequence[int]) -> tuple[NodeKind, str] | None:
        """The category, group or column whose columns are exactly ``positions``."""
        columns = self.columns_at(positions)
        if not columns:
            return None
        table = self.table
        span = (table.column_index(columns[0].id), table.column_index(columns[-1].id) + 1)
        group_id = columns[0].group_id
        if group_id is not None:
            category_id = table.replicate_group(group_id).category_id
            if category_id is not None and self.node_span(NodeKind.CATEGORY, category_id) == span:
                return NodeKind.CATEGORY, category_id
            if self.node_span(NodeKind.GROUP, group_id) == span:
                return NodeKind.GROUP, group_id
        if len(columns) == 1:
            return NodeKind.COLUMN, columns[0].id
        return None

    def move_by(self, positions: Sequence[int], step: int) -> tuple[NodeKind, str] | None:
        """Move the column, group or category at ``positions`` one place left (-1) or right.

        Returns what moved, or None (with ``error`` sent if the selection cannot move).
        """
        node = self.node_at(positions)
        if node is None:
            self.error.emit("Select one column, or all the columns of a group, to move it.")
            return None
        found = find_node(self.table, *node)
        assert found is not None
        current, siblings = found
        i = siblings.index(current) + step
        if not 0 <= i < len(siblings):
            return None
        boundary = siblings[i].start if step < 0 else siblings[i].end
        return node if self.move_node(*node, boundary) else None

    def move_node(self, kind: NodeKind, node_id: str, boundary: int) -> bool:
        """Move a column, group or category to column boundary ``boundary``, as one step."""
        table = self.table
        order = moved_order(table, kind, node_id, boundary)
        if order is None:
            return False
        if kind is NodeKind.COLUMN:
            label = f"column '{table.column(node_id).name}'"
        elif kind is NodeKind.GROUP:
            label = f"group '{table.replicate_group(node_id).name}'"
        else:
            label = f"category '{table.category_group(node_id).name}'"
        return self.execute(ReorderColumns(self.table_id, order, f"Move {label}"))

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
        bands = _bands(table)
        if bands != self._bands:
            self._bands = bands
            self.bands_changed.emit()
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
