"""Commands: the only way to change a document.

Every command validates its inputs in ``do()`` before it changes anything, so a
rejected command (:class:`CommandError`) leaves the document untouched. Commands keep
whatever ``undo()`` needs, and ``do()`` after ``undo()`` replays the same change with
the same ids.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy.typing as npt

from curvis.data import (
    CategoryGroup,
    CellValue,
    Column,
    ColumnRole,
    ReplicateGroup,
    Table,
    layout_error,
    name_error,
    normalized_order,
)
from curvis.data.column import coerce_cell, convert_values, writable
from curvis.data.names import name_key

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
    @abstractmethod
    def touched_nodes(self) -> tuple[str, ...]:
        """Ids of the tables and columns whose content the command changes.

        The same for do, undo and redo; valid after the first ``do()``. Used to find
        stale nodes in the dependency graph. The table id is always included, so a node
        that reads a whole table goes stale on any change to it.
        """

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
    """Names must not be blank and must be unique, ignoring case."""
    if not name.strip():
        raise CommandError(f"A {kind} name cannot be blank.")
    key = name_key(name)
    existing = next((t for t in taken if name_key(t) == key), None)
    if existing is not None:
        raise CommandError(f"There is already a {kind} called '{existing}'.")


def _table_nodes(table: Table) -> tuple[str, ...]:
    """The table id followed by the ids of all its columns."""
    return (table.id, *(c.id for c in table.columns))


def _check_insert_index(index: int | None, length: int) -> int:
    if index is None:
        return length
    if not 0 <= index <= length:
        raise CommandError(f"Position {index} is out of range (0 to {length}).")
    return index


@dataclass
class _Layout:
    """Column order, names and group membership of a table, to restore on undo.

    Groups are restored as the same objects, so other references to them stay valid.
    """

    columns: list[Column]
    column_state: list[tuple[str, str | None]]
    groups: list[ReplicateGroup]
    group_state: list[tuple[str, str | None]]
    categories: list[CategoryGroup]
    category_names: list[str]
    category_factor: str

    @classmethod
    def capture(cls, table: Table) -> _Layout:
        return cls(
            list(table.columns),
            [(c.name, c.group_id) for c in table.columns],
            list(table.replicate_groups),
            [(g.name, g.category_id) for g in table.replicate_groups],
            list(table.category_groups),
            [c.name for c in table.category_groups],
            table.category_factor,
        )

    def restore(self, table: Table) -> None:
        table.columns = list(self.columns)
        for column, (name, group_id) in zip(self.columns, self.column_state, strict=True):
            column.name, column.group_id = name, group_id
        table.replicate_groups = list(self.groups)
        for group, (name, category_id) in zip(self.groups, self.group_state, strict=True):
            group.name, group.category_id = name, category_id
        table.category_groups = list(self.categories)
        for category, name in zip(self.categories, self.category_names, strict=True):
            category.name = name
        table.category_factor = self.category_factor


def _drop_empty_groups(table: Table) -> None:
    """Remove replicate groups without columns, then category groups without groups."""
    used = {c.group_id for c in table.columns}
    table.replicate_groups = [g for g in table.replicate_groups if g.id in used]
    used_categories = {g.category_id for g in table.replicate_groups}
    table.category_groups = [c for c in table.category_groups if c.id in used_categories]


def _check_layout(table: Table, before: _Layout) -> None:
    """Raise :class:`CommandError` (after restoring ``before``) if the layout is not valid."""
    error = layout_error(table)
    if error is not None:
        before.restore(table)
        raise CommandError(error[0].upper() + error[1:] + ("" if error.endswith(".") else "."))


def _group_label(table: Table, group_id: str | None) -> str:
    if group_id is None:
        return ""
    return table.replicate_group(group_id).name


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

    @property
    def touched_nodes(self) -> tuple[str, ...]:
        return _table_nodes(self.table)


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

    @property
    def touched_nodes(self) -> tuple[str, ...]:
        return _table_nodes(self._table) if self._table else (self.table_id,)


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

    @property
    def touched_nodes(self) -> tuple[str, ...]:
        return (self.table_id,)


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
        self._touched: tuple[str, ...] = (table_id, column.id)

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
        before = _Layout.capture(table)
        table.columns.insert(self._at, self.column)
        _check_layout(table, before)
        table.resize(max(self._old_rows, len(self.column)))
        # A longer column pads the others, which changes them too.
        padded = len(self.column) > self._old_rows and len(table.columns) > 1
        self._touched = _table_nodes(table) if padded else (self.table_id, self.column.id)

    def undo(self, doc: Document) -> None:
        table = doc.table(self.table_id)
        del table.columns[self._at]
        table.resize(self._old_rows)

    @property
    def summary(self) -> str:
        return f"Add column '{self.column.name}'"

    @property
    def touched_nodes(self) -> tuple[str, ...]:
        return self._touched


class RemoveColumn(Command):
    """Remove a column. A replicate group (or category) left empty is removed too."""

    def __init__(self, table_id: str, column_id: str) -> None:
        self.table_id = table_id
        self.column_id = column_id
        self._column: Column | None = None
        self._before: _Layout | None = None

    def do(self, doc: Document) -> None:
        table = _get_table(doc, self.table_id)
        self._column = _get_column(table, self.column_id)
        self._before = _Layout.capture(table)
        del table.columns[table.column_index(self.column_id)]
        _drop_empty_groups(table)

    def undo(self, doc: Document) -> None:
        assert self._before is not None
        self._before.restore(doc.table(self.table_id))

    @property
    def summary(self) -> str:
        return f"Remove column '{self._column.name}'" if self._column else "Remove column"

    @property
    def touched_nodes(self) -> tuple[str, ...]:
        return (self.table_id, self.column_id)


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
        before = _Layout.capture(table)
        table.columns.insert(self.new_index, table.columns.pop(self._old_index))
        _check_layout(table, before)

    def undo(self, doc: Document) -> None:
        columns = doc.table(self.table_id).columns
        columns.insert(self._old_index, columns.pop(self.new_index))

    @property
    def summary(self) -> str:
        return f"Move column '{self._name}'"

    @property
    def touched_nodes(self) -> tuple[str, ...]:
        return (self.table_id, self.column_id)


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

    @property
    def touched_nodes(self) -> tuple[str, ...]:
        return (self.table_id, self.column_id)


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
        self._old_values: npt.NDArray[Any] | None = None
        self._name = ""

    def do(self, doc: Document) -> None:
        table = _get_table(doc, self.table_id)
        column = _get_column(table, self.column_id)
        if column.group_id is not None and self.role is not ColumnRole.Y:
            raise CommandError(
                f"Column '{column.name}' is in replicate group "
                f"'{_group_label(table, column.group_id)}', which holds only Y columns. "
                "Ungroup it first."
            )
        self._name = column.name
        self._old_role = column.role
        # Keep the old array only when it is replaced: an array still in the table can
        # be edited in place by later commands, so a reference to it is not a snapshot.
        self._old_values = None
        if self.role.is_numeric != column.role.is_numeric:
            self._old_values = column.values
            column.values = convert_values(column.values, self.role)
        column.role = self.role

    def undo(self, doc: Document) -> None:
        column = doc.table(self.table_id).column(self.column_id)
        column.role = self._old_role
        if self._old_values is not None:
            column.values = self._old_values

    @property
    def summary(self) -> str:
        return f"Set role of column '{self._name}' to {self.role.value}"

    @property
    def touched_nodes(self) -> tuple[str, ...]:
        return (self.table_id, self.column_id)


# --- groups and order --------------------------------------------------------------


class _LayoutCommand(Command):
    """Base for commands that change column order, names or groups of one table.

    ``do()`` captures the layout, calls :meth:`apply` and checks the result; if the
    result breaks the layout rules, the table is restored and the command refused.
    """

    def __init__(self, table_id: str) -> None:
        self.table_id = table_id
        self._before: _Layout | None = None
        self._touched: tuple[str, ...] = (table_id,)

    def do(self, doc: Document) -> None:
        table = _get_table(doc, self.table_id)
        before = _Layout.capture(table)
        try:
            changed = self.apply(table)
        except CommandError:
            before.restore(table)
            raise
        _check_layout(table, before)
        self._before = before
        self._touched = (self.table_id, *changed)

    def undo(self, doc: Document) -> None:
        assert self._before is not None
        self._before.restore(doc.table(self.table_id))

    @abstractmethod
    def apply(self, table: Table) -> Sequence[str]:
        """Make the change; return the ids of the columns it affects."""

    @property
    def touched_nodes(self) -> tuple[str, ...]:
        return self._touched


def _get_columns(table: Table, column_ids: Sequence[str]) -> list[Column]:
    if not column_ids:
        raise CommandError("No columns were given.")
    return [_get_column(table, c) for c in dict.fromkeys(column_ids)]


def _normalize(table: Table) -> None:
    _drop_empty_groups(table)
    table.columns = normalized_order(table)


class GroupColumns(_LayoutCommand):
    """Combine Y columns into a new replicate group (one series).

    Columns already in a group leave it (a group left empty is removed). The columns
    are moved next to each other, where the first of them is. If all the columns were
    in groups of the same category, the new group gets that category too.
    """

    def __init__(
        self, table_id: str, column_ids: Sequence[str], name: str, group_id: str | None = None
    ) -> None:
        super().__init__(table_id)
        self.column_ids = list(column_ids)
        self.group = ReplicateGroup(name) if group_id is None else ReplicateGroup(name, id=group_id)

    def apply(self, table: Table) -> Sequence[str]:
        columns = _get_columns(table, self.column_ids)
        for column in columns:
            if column.role is not ColumnRole.Y:
                raise CommandError(
                    f"Column '{column.name}' has the role {column.role.value}. "
                    "Only Y columns can be grouped as replicates."
                )
        if any(g.id == self.group.id for g in table.replicate_groups):
            raise CommandError("This replicate group is already in the table.")
        categories = {
            None if c.group_id is None else table.replicate_group(c.group_id).category_id
            for c in columns
        }
        self.group.category_id = categories.pop() if len(categories) == 1 else None
        table.replicate_groups.append(self.group)
        for column in columns:
            column.group_id = self.group.id
        _normalize(table)
        return [c.id for c in columns]

    @property
    def summary(self) -> str:
        return f"Group {_columns_label(len(self.column_ids))} as '{self.group.name}'"


class UngroupColumns(_LayoutCommand):
    """Take columns out of their replicate groups.

    Columns taken from the middle of a group (or category) move to just after it, so
    groups stay contiguous. Groups and categories left empty are removed.
    """

    def __init__(self, table_id: str, column_ids: Sequence[str]) -> None:
        super().__init__(table_id)
        self.column_ids = list(column_ids)

    def apply(self, table: Table) -> Sequence[str]:
        columns = [c for c in _get_columns(table, self.column_ids) if c.group_id is not None]
        if not columns:
            raise CommandError("None of these columns is in a replicate group.")
        for column in columns:
            column.group_id = None
        # Each group gathers at its first column, so a column taken from the middle of a
        # group (or category) ends up right after it.
        _normalize(table)
        return [c.id for c in columns]

    @property
    def summary(self) -> str:
        return f"Ungroup {_columns_label(len(self.column_ids))}"


class SetCategory(_LayoutCommand):
    """Put replicate groups in a category group (a level of the second factor).

    ``category`` is the name of the level: an existing category with that name (ignoring
    case) is used, otherwise a new one is made. ``None`` takes the groups out of their
    categories. The groups of a category are moved next to each other.
    """

    def __init__(
        self,
        table_id: str,
        group_ids: Sequence[str],
        category: str | None,
        category_id: str | None = None,
    ) -> None:
        super().__init__(table_id)
        self.group_ids = list(group_ids)
        self.category = category
        self._new = (
            None
            if category is None
            else CategoryGroup(category)
            if category_id is None
            else CategoryGroup(category, id=category_id)
        )

    def apply(self, table: Table) -> Sequence[str]:
        if not self.group_ids:
            raise CommandError("No replicate groups were given.")
        try:
            groups = [table.replicate_group(g) for g in dict.fromkeys(self.group_ids)]
        except KeyError as exc:
            raise CommandError(f"Table '{table.name}' has no replicate group {exc}.") from None
        category_id: str | None = None
        if self._new is not None:
            key = name_key(self._new.name)
            existing = next((c for c in table.category_groups if name_key(c.name) == key), None)
            if existing is None:
                self._new.name = self._new.name.strip()
                table.category_groups.append(self._new)
                existing = self._new
            category_id = existing.id
        for group in groups:
            group.category_id = category_id
        _normalize(table)
        return [c.id for g in groups for c in table.group_columns(g.id)]

    @property
    def summary(self) -> str:
        n = len(self.group_ids)
        groups = "1 group" if n == 1 else f"{n} groups"
        if self.category is None:
            return f"Remove {groups} from their category"
        return f"Put {groups} in category '{self.category}'"


class ReorderColumns(_LayoutCommand):
    """Put the table's columns in a new order (``column_ids``, every column once)."""

    def __init__(self, table_id: str, column_ids: Sequence[str], summary: str = "") -> None:
        super().__init__(table_id)
        self.column_ids = list(column_ids)
        self._summary = summary or "Reorder columns"

    def apply(self, table: Table) -> Sequence[str]:
        if sorted(self.column_ids) != sorted(c.id for c in table.columns):
            raise CommandError("The new order must list every column of the table once.")
        old = [c.id for c in table.columns]
        table.columns = [table.column(c) for c in self.column_ids]
        return [new for new, was in zip(self.column_ids, old, strict=True) if new != was]

    @property
    def summary(self) -> str:
        return self._summary


class RenameCategoryFactor(_LayoutCommand):
    """Rename the second factor of a table, whose levels are its category groups."""

    def __init__(self, table_id: str, name: str) -> None:
        super().__init__(table_id)
        self.name = name
        self._old_name = ""

    def apply(self, table: Table) -> Sequence[str]:
        self._old_name = table.category_factor
        table.category_factor = self.name
        return ()

    @property
    def summary(self) -> str:
        return f"Rename category factor '{self._old_name}' to '{self.name}'"


class BulkRename(_LayoutCommand):
    """Rename columns, replicate groups and category groups in one step.

    Each mapping goes from id to new name. Names are checked on the result, so names
    can be swapped (A to B and B to A) in one command.
    """

    def __init__(
        self,
        table_id: str,
        columns: Mapping[str, str] | None = None,
        groups: Mapping[str, str] | None = None,
        categories: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(table_id)
        self.columns = dict(columns or {})
        self.groups = dict(groups or {})
        self.categories = dict(categories or {})
        self._single_old = ""

    def apply(self, table: Table) -> Sequence[str]:
        if not (self.columns or self.groups or self.categories):
            raise CommandError("There are no names to change.")
        try:
            items: list[Column | ReplicateGroup | CategoryGroup] = [
                *(table.column(i) for i in self.columns),
                *(table.replicate_group(i) for i in self.groups),
                *(table.category_group(i) for i in self.categories),
            ]
        except KeyError as exc:
            raise CommandError(f"Table '{table.name}' has nothing with id {exc}.") from None
        error = name_error(table, self.columns, self.groups, self.categories)
        if error is not None:
            raise CommandError(error)
        names = [*self.columns.values(), *self.groups.values(), *self.categories.values()]
        if len(items) == 1:
            self._single_old = items[0].name
        for item, name in zip(items, names, strict=True):
            item.name = name
        return list(self.columns)

    @property
    def summary(self) -> str:
        parts = [
            (len(self.columns), "column", "columns"),
            (len(self.groups), "group", "groups"),
            (len(self.categories), "category", "categories"),
        ]
        if sum(n for n, _, _ in parts) == 1:
            _, kind, _ = next(p for p in parts if p[0])
            (name,) = [*self.columns.values(), *self.groups.values(), *self.categories.values()]
            return f"Rename {kind} '{self._single_old}' to '{name}'"
        listed = [f"{n} {one if n == 1 else many}" for n, one, many in parts if n]
        return "Rename " + " and ".join(listed)


def _columns_label(n: int) -> str:
    return "1 column" if n == 1 else f"{n} columns"


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
        self._touched: tuple[str, ...] = (table_id,)

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
        # Adding rows pads every column, so then every column changes.
        grown = end > self._old_rows
        self._touched = _table_nodes(table) if grown else (self.table_id, *self._column_ids)
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

    @property
    def touched_nodes(self) -> tuple[str, ...]:
        return self._touched


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

    @property
    def touched_nodes(self) -> tuple[str, ...]:
        if self._block is None:
            return (self.table_id, self.column_id)
        return self._block.touched_nodes


# --- groups ------------------------------------------------------------------------


class CommandGroup(Command):
    """Several commands run as one undo step (e.g. a paste that also adds columns).

    The commands run in order and are undone in reverse order. If one is rejected, the
    ones already done are undone again, so the document is left unchanged.
    """

    def __init__(self, summary: str, commands: Sequence[Command]) -> None:
        if not commands:
            raise ValueError("a command group needs at least one command")
        self.commands = list(commands)
        self._summary = summary

    def do(self, doc: Document) -> None:
        done: list[Command] = []
        try:
            for command in self.commands:
                command.do(doc)
                done.append(command)
        except CommandError:
            for command in reversed(done):
                command.undo(doc)
            raise

    def undo(self, doc: Document) -> None:
        for command in reversed(self.commands):
            command.undo(doc)

    @property
    def summary(self) -> str:
        return self._summary

    @property
    def touched_nodes(self) -> tuple[str, ...]:
        nodes = (node for command in self.commands for node in command.touched_nodes)
        return tuple(dict.fromkeys(nodes))
