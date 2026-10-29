"""Commands: the only way to change a document.

Every command validates its inputs in ``do()`` before it changes anything, so a
rejected command (:class:`CommandError`) leaves the document untouched. Commands keep
whatever ``undo()`` needs, and ``do()`` after ``undo()`` replays the same change with
the same ids.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

from curvis.data import CellValue, Column, ColumnRole, Table
from curvis.data.column import coerce_cell, convert_values, writable

if TYPE_CHECKING:
    from curvis.core.document import Document


class CommandError(ValueError):
    """A command cannot run with the given inputs. The document was not changed."""


class Command(ABC):
    """Base class for all document changes."""

    @abstractmethod
    def do(self, doc: Document) -> None:
        """Apply the change. Raise :class:`CommandError` before changing anything if invalid."""

    @abstractmethod
    def undo(self, doc: Document) -> None:
        """Revert the change made by the last ``do()``."""

    @property
    @abstractmethod
    def summary(self) -> str:
        """Short, plain-English description, e.g. "Rename column 'A' to 'Dose'"."""

    @property
    def command_type(self) -> str:
        """Name of the command type, used in the change log."""
        return type(self).__name__


# --- helpers -----------------------------------------------------------------------


def _get_table(doc: Document, table_id: str) -> Table:
    try:
        return doc.table(table_id)
    except KeyError:
        raise CommandError(f"There is no table with id {table_id!r}.") from None


def _get_column(table: Table, column_id: str) -> Column:
    try:
        return table.column(column_id)
    except KeyError:
        raise CommandError(f"Table '{table.name}' has no column with id {column_id!r}.") from None


def _check_name(name: str, taken: Sequence[str], kind: str) -> None:
    if not name.strip():
        raise CommandError(f"A {kind} name cannot be blank.")
    if name in taken:
        raise CommandError(f"There is already a {kind} called '{name}'.")


def _check_insert_index(index: int | None, length: int) -> int:
    if index is None:
        return length
    if not 0 <= index <= length:
        raise CommandError(f"Position {index} is out of range (0 to {length}).")
    return index


# --- tables ------------------------------------------------------------------------


class AddTable(Command):
    """Add a table at ``index`` (default: at the end)."""

    def __init__(self, table: Table, index: int | None = None) -> None:
        self.table = table
        self.index = index
        self._at = 0

    def do(self, doc: Document) -> None:
        _check_name(self.table.name, [t.name for t in doc.tables], "table")
        if any(t.id == self.table.id for t in doc.tables):
            raise CommandError("This table is already in the document.")
        try:
            self.table.check()
        except ValueError as exc:
            raise CommandError(str(exc)) from None
        self._at = _check_insert_index(self.index, len(doc.tables))
        doc.tables.insert(self._at, self.table)

    def undo(self, doc: Document) -> None:
        del doc.tables[self._at]

    @property
    def summary(self) -> str:
        return f"Add table '{self.table.name}'"


class RemoveTable(Command):
    """Remove a table."""

    def __init__(self, table_id: str) -> None:
        self.table_id = table_id
        self._table: Table | None = None
        self._at = 0

    def do(self, doc: Document) -> None:
        self._table = _get_table(doc, self.table_id)
        self._at = doc.table_index(self.table_id)
        del doc.tables[self._at]

    def undo(self, doc: Document) -> None:
        assert self._table is not None
        doc.tables.insert(self._at, self._table)

    @property
    def summary(self) -> str:
        return f"Remove table '{self._table.name}'" if self._table else "Remove table"


class RenameTable(Command):
    """Rename a table."""

    def __init__(self, table_id: str, name: str) -> None:
        self.table_id = table_id
        self.name = name
        self._old_name = ""

    def do(self, doc: Document) -> None:
        table = _get_table(doc, self.table_id)
        taken = [t.name for t in doc.tables if t.id != self.table_id]
        _check_name(self.name, taken, "table")
        self._old_name = table.name
        table.name = self.name

    def undo(self, doc: Document) -> None:
        doc.table(self.table_id).name = self._old_name

    @property
    def summary(self) -> str:
        return f"Rename table '{self._old_name}' to '{self.name}'"


# --- columns -----------------------------------------------------------------------


class AddColumn(Command):
    """Add a column at ``index`` (default: at the end).

    A column shorter than the table is padded with missing values; a longer column
    pads the other columns instead. Undo removes the padding again.
    """

    def __init__(self, table_id: str, column: Column, index: int | None = None) -> None:
        self.table_id = table_id
        self.column = column
        self.index = index
        self._at = 0
        self._old_rows = 0

    def do(self, doc: Document) -> None:
        table = _get_table(doc, self.table_id)
        _check_name(self.column.name, [c.name for c in table.columns], "column")
        if any(c.id == self.column.id for c in table.columns):
            raise CommandError("This column is already in the table.")
        try:
            self.column.check()
        except ValueError as exc:
            raise CommandError(str(exc)) from None
        self._at = _check_insert_index(self.index, len(table.columns))
        self._old_rows = table.n_rows
        table.columns.insert(self._at, self.column)
        table.resize(max(self._old_rows, len(self.column)))

    def undo(self, doc: Document) -> None:
        table = doc.table(self.table_id)
        del table.columns[self._at]
        table.resize(self._old_rows)

    @property
    def summary(self) -> str:
        return f"Add column '{self.column.name}'"


class RemoveColumn(Command):
    """Remove a column."""

    def __init__(self, table_id: str, column_id: str) -> None:
        self.table_id = table_id
        self.column_id = column_id
        self._column: Column | None = None
        self._at = 0

    def do(self, doc: Document) -> None:
        table = _get_table(doc, self.table_id)
        self._column = _get_column(table, self.column_id)
        self._at = table.column_index(self.column_id)
        del table.columns[self._at]

    def undo(self, doc: Document) -> None:
        assert self._column is not None
        doc.table(self.table_id).columns.insert(self._at, self._column)

    @property
    def summary(self) -> str:
        return f"Remove column '{self._column.name}'" if self._column else "Remove column"


class MoveColumn(Command):
    """Move a column to ``new_index`` (its position after the move)."""

    def __init__(self, table_id: str, column_id: str, new_index: int) -> None:
        self.table_id = table_id
        self.column_id = column_id
        self.new_index = new_index
        self._old_index = 0
        self._name = ""

    def do(self, doc: Document) -> None:
        table = _get_table(doc, self.table_id)
        column = _get_column(table, self.column_id)
        if not 0 <= self.new_index < len(table.columns):
            raise CommandError(
                f"Position {self.new_index} is out of range (0 to {len(table.columns) - 1})."
            )
        self._name = column.name
        self._old_index = table.column_index(self.column_id)
        table.columns.insert(self.new_index, table.columns.pop(self._old_index))

    def undo(self, doc: Document) -> None:
        columns = doc.table(self.table_id).columns
        columns.insert(self._old_index, columns.pop(self.new_index))

    @property
    def summary(self) -> str:
        return f"Move column '{self._name}'"


class RenameColumn(Command):
    """Rename a column."""

    def __init__(self, table_id: str, column_id: str, name: str) -> None:
        self.table_id = table_id
        self.column_id = column_id
        self.name = name
        self._old_name = ""

    def do(self, doc: Document) -> None:
        table = _get_table(doc, self.table_id)
        column = _get_column(table, self.column_id)
        taken = [c.name for c in table.columns if c.id != self.column_id]
        _check_name(self.name, taken, "column")
        self._old_name = column.name
        column.name = self.name

    def undo(self, doc: Document) -> None:
        doc.table(self.table_id).column(self.column_id).name = self._old_name

    @property
    def summary(self) -> str:
        return f"Rename column '{self._old_name}' to '{self.name}'"


class SetColumnRole(Command):
    """Change a column's role.

    Switching between a numeric role and TEXT converts the values: numbers become
    text, and text that is not a number becomes missing (NaN). Undo restores the
    original values exactly.
    """

    def __init__(self, table_id: str, column_id: str, role: ColumnRole) -> None:
        self.table_id = table_id
        self.column_id = column_id
        self.role = ColumnRole(role)
        self._old_role = ColumnRole.Y
        self._old_values: npt.NDArray[Any] = np.empty(0)
        self._name = ""

    def do(self, doc: Document) -> None:
        column = _get_column(_get_table(doc, self.table_id), self.column_id)
        self._name = column.name
        self._old_role = column.role
        self._old_values = column.values
        column.values = convert_values(column.values, self.role)
        column.role = self.role

    def undo(self, doc: Document) -> None:
        column = doc.table(self.table_id).column(self.column_id)
        column.role = self._old_role
        column.values = self._old_values

    @property
    def summary(self) -> str:
        return f"Set role of column '{self._name}' to {self.role.value}"


# --- cells -------------------------------------------------------------------------


class SetCellBlock(Command):
    """Write a rectangular block of values (e.g. a paste) starting at a cell.

    ``values`` is a list of rows. The block covers the columns at positions
    ``left_col`` onwards; it must not run past the last column. Writing past the
    last row adds rows to the table (filled with missing values elsewhere).
    """

    def __init__(
        self,
        table_id: str,
        top_row: int,
        left_col: int,
        values: Sequence[Sequence[CellValue]],
    ) -> None:
        self.table_id = table_id
        self.top_row = top_row
        self.left_col = left_col
        self.values = [list(row) for row in values]
        self._old_rows = 0
        self._column_ids: list[str] = []
        self._old_values: list[npt.NDArray[Any]] = []

    @property
    def _height(self) -> int:
        return len(self.values)

    @property
    def _width(self) -> int:
        return len(self.values[0]) if self.values else 0

    def do(self, doc: Document) -> None:
        table = _get_table(doc, self.table_id)
        height, width = self._height, self._width
        if height == 0 or width == 0:
            raise CommandError("There are no values to write.")
        if any(len(row) != width for row in self.values):
            raise CommandError("All rows of the block must have the same length.")
        if self.top_row < 0 or self.left_col < 0:
            raise CommandError("Row and column positions cannot be negative.")
        if self.left_col + width > len(table.columns):
            raise CommandError("The block runs past the last column.")
        columns = table.columns[self.left_col : self.left_col + width]
        try:
            cells = [
                [coerce_cell(row[j], col.role) for row in self.values]
                for j, col in enumerate(columns)
            ]
        except ValueError as exc:
            raise CommandError(f"{exc}.") from None

        self._old_rows = table.n_rows
        end = self.top_row + height
        table.resize(max(self._old_rows, end))
        self._column_ids = [c.id for c in columns]
        self._old_values = []
        for column, new in zip(columns, cells, strict=True):
            self._old_values.append(column.values[self.top_row : end].copy())
            with writable(column.values) as arr:
                arr[self.top_row : end] = new

    def undo(self, doc: Document) -> None:
        table = doc.table(self.table_id)
        end = self.top_row + self._height
        for column_id, old in zip(self._column_ids, self._old_values, strict=True):
            with writable(table.column(column_id).values) as arr:
                arr[self.top_row : end] = old
        table.resize(self._old_rows)

    @property
    def summary(self) -> str:
        cells = self._height * self._width
        return f"Set {cells} cells from row {self.top_row + 1}"


class SetCellValue(Command):
    """Write one cell. Writing past the last row adds rows to the table."""

    def __init__(self, table_id: str, column_id: str, row: int, value: CellValue) -> None:
        self.table_id = table_id
        self.column_id = column_id
        self.row = row
        self.value = value
        self._block: SetCellBlock | None = None
        self._name = ""

    def do(self, doc: Document) -> None:
        table = _get_table(doc, self.table_id)
        column = _get_column(table, self.column_id)
        self._name = column.name
        block = SetCellBlock(
            self.table_id, self.row, table.column_index(self.column_id), [[self.value]]
        )
        block.do(doc)
        self._block = block

    def undo(self, doc: Document) -> None:
        assert self._block is not None
        self._block.undo(doc)

    @property
    def summary(self) -> str:
        return f"Set cell in column '{self._name}', row {self.row + 1}"
