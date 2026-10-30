"""Commands for calculations and project constants.

:func:`add_calculation` and :func:`edit_calculation` build one undoable command that
adds (or removes, or renames) the calculated columns and stores the definition with
:class:`SetCalculation`. The values of the calculated columns are not part of these
commands: the :class:`~curvis.compute.calc_engine.CalcEngine` fills them in afterwards.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

import numpy as np

from curvis.core.calculations import CalculatedColumn, Calculation, constant_node
from curvis.core.commands import (
    AddColumn,
    Command,
    CommandError,
    CommandGroup,
    GroupColumns,
    RemoveColumn,
    RenameColumn,
    ReorderColumns,
)
from curvis.core.constants import ProjectConstant
from curvis.data import Column, ColumnRole, Table
from curvis.data.layout import layout_tree
from curvis.data.names import unique_name

if TYPE_CHECKING:
    from curvis.core.document import Document


class SetCalculation(Command):
    """Store ``calculation`` (adding it, or replacing the one with the same id).

    The calculated columns must already be in the table. With ``remove=True`` the
    calculation with that id is removed instead (its columns stay).
    """

    def __init__(self, calculation: Calculation, summary: str = "", remove: bool = False) -> None:
        self.calculation = calculation
        self.remove = remove
        self._summary = summary or ("Remove calculation" if remove else "Change calculation")
        self._old: Calculation | None = None
        self._at = 0

    def do(self, doc: Document) -> None:
        new = self.calculation
        index = next((i for i, c in enumerate(doc.calculations) if c.id == new.id), None)
        if self.remove:
            if index is None:
                raise CommandError("There is no such calculation.")
        else:
            _check_calculation(doc, new)
        self._old = doc.calculations[index] if index is not None else None
        calculations = list(doc.calculations)
        if self.remove:
            assert index is not None
            del calculations[index]
        elif index is None:
            calculations.append(new)
        else:
            calculations[index] = new
        self._at = index if index is not None else len(calculations) - 1
        doc.calculations = calculations

    def undo(self, doc: Document) -> None:
        calculations = list(doc.calculations)
        if self._old is None:
            del calculations[self._at]
        elif self.remove:
            calculations.insert(self._at, self._old)
        else:
            calculations[self._at] = self._old
        doc.calculations = calculations

    @property
    def summary(self) -> str:
        return self._summary

    @property
    def touched_nodes(self) -> tuple[str, ...]:
        columns = [c.column_id for c in self.calculation.columns]
        if self._old is not None:
            columns += [c.column_id for c in self._old.columns]
        return (self.calculation.table_id, *dict.fromkeys(columns))


def _check_calculation(doc: Document, calculation: Calculation) -> None:
    try:
        table = doc.table(calculation.table_id)
    except KeyError:
        raise CommandError("The table of this calculation is gone.") from None
    problem = calculation.pairing.problem()
    if problem is not None:
        raise CommandError(problem)
    if not calculation.columns:
        raise CommandError("A calculation needs at least one calculated column.")
    others = {
        c.column_id
        for other in doc.calculations
        if other.id != calculation.id
        for c in other.columns
    }
    ids = {c.id for c in table.columns}
    for output in calculation.columns:
        if output.column_id not in ids:
            raise CommandError("A calculated column of this calculation is not in the table.")
        if output.column_id in others:
            raise CommandError("A column can belong to only one calculation.")
        if output.column_id in calculation.pairing.columns():
            raise CommandError("A calculated column cannot be one of its own inputs.")


# --- building calculations ---------------------------------------------------------


def add_calculation(doc: Document, calculation: Calculation) -> Command:
    """One command that adds a calculated column for each target of ``calculation``.

    The new columns go right after the column, group or category of their source (or
    after the last column with ``append_at_end``). When every column of a replicate group
    is calculated, the new columns form a replicate group too. ``calculation.columns``
    is ignored; the command fills it in.
    """
    table = _table(doc, calculation)
    problem = calculation.pairing.problem()
    if problem is not None:
        raise CommandError(problem)
    targets = _in_table_order(table, calculation.pairing.targets)
    taken = [c.name for c in table.columns]
    outputs = _new_outputs(table, targets, calculation.suffix, taken)
    commands: list[Command] = [AddColumn(table.id, column) for _, column in outputs]
    pairs = [(source, column.id) for source, column in outputs]
    order = _placed_order(table, pairs, calculation.append_at_end)
    if order != [c.id for c in table.columns] + [column.id for _, column in outputs]:
        commands.append(ReorderColumns(table.id, order, "Place calculated columns"))
    group_names = [g.name for g in table.replicate_groups]
    by_source = dict(pairs)
    for group in table.replicate_groups:
        members = [c.id for c in table.group_columns(group.id)]
        if members and all(m in by_source for m in members):
            name = unique_name(group.name + calculation.suffix, group_names)
            group_names.append(name)
            commands.append(GroupColumns(table.id, [by_source[m] for m in members], name))
    final = replace(
        calculation,
        columns=tuple(CalculatedColumn(column.id, source) for source, column in outputs),
    )
    commands.append(SetCalculation(final, "Store calculation"))
    n = len(outputs)
    label = f"{n} column" if n == 1 else f"{n} columns"
    return CommandGroup(f"Calculate {calculation.kind.label.lower()} for {label}", commands)


def edit_calculation(doc: Document, calculation: Calculation) -> Command:
    """One command that changes the calculation with ``calculation.id`` to ``calculation``.

    Calculated columns whose source is still selected are kept (renamed if they still
    carry the old suffix and the suffix changed); the others are removed, and columns are
    added for newly selected sources. ``calculation.columns`` is ignored.
    """
    try:
        old = doc.calculation(calculation.id)
    except KeyError:
        raise CommandError("There is no such calculation.") from None
    table = _table(doc, calculation)
    problem = calculation.pairing.problem()
    if problem is not None:
        raise CommandError(problem)
    targets = _in_table_order(table, calculation.pairing.targets)
    ids = {c.id for c in table.columns}
    kept = {o.source_id: o for o in old.columns if o.source_id in targets and o.column_id in ids}
    dropped = [o.column_id for o in old.columns if o.source_id not in kept and o.column_id in ids]
    commands: list[Command] = [RemoveColumn(table.id, c) for c in dropped]

    remaining = [c for c in table.columns if c.id not in dropped]
    taken = [c.name for c in remaining]
    if calculation.suffix != old.suffix:
        for source_id, output in kept.items():
            column = table.column(output.column_id)
            if column.name == table.column(source_id).name + old.suffix:
                others = [n for n in taken if n != column.name]
                name = unique_name(table.column(source_id).name + calculation.suffix, others)
                if name != column.name:
                    commands.append(RenameColumn(table.id, column.id, name))
                    taken = [*others, name]
    new_sources = [t for t in targets if t not in kept]
    outputs = _new_outputs(table, new_sources, calculation.suffix, taken)
    commands += [AddColumn(table.id, column) for _, column in outputs]
    if outputs:
        pairs = [(source, column.id) for source, column in outputs]
        order = _placed_order(table, pairs, calculation.append_at_end, exclude=set(dropped))
        commands.append(ReorderColumns(table.id, order, "Place calculated columns"))
    added = {source: column.id for source, column in outputs}
    columns = tuple(kept[t] if t in kept else CalculatedColumn(added[t], t) for t in targets)
    final = replace(calculation, columns=columns)
    commands.append(SetCalculation(final, "Store calculation"))
    return CommandGroup(f"Edit calculation ({calculation.kind.label.lower()})", commands)


def _table(doc: Document, calculation: Calculation) -> Table:
    try:
        return doc.table(calculation.table_id)
    except KeyError:
        raise CommandError("The table of this calculation is gone.") from None


def _in_table_order(table: Table, column_ids: Sequence[str]) -> list[str]:
    positions = {c.id: i for i, c in enumerate(table.columns)}
    missing = [c for c in column_ids if c not in positions]
    if missing:
        raise CommandError("A selected column is no longer in the table.")
    for column_id in column_ids:
        if not table.column(column_id).role.is_numeric:
            name = table.column(column_id).name
            raise CommandError(f"Column '{name}' holds text and cannot be calculated from.")
    return sorted(dict.fromkeys(column_ids), key=positions.__getitem__)


def _new_outputs(
    table: Table, sources: Sequence[str], suffix: str, taken: list[str]
) -> list[tuple[str, Column]]:
    """A new, empty Y column for each source, named "<source><suffix>"."""
    outputs: list[tuple[str, Column]] = []
    for source in sources:
        name = unique_name(table.column(source).name + suffix, taken)
        taken.append(name)
        values = np.full(table.n_rows, math.nan)
        outputs.append((source, Column.new(name, ColumnRole.Y, values)))
    return outputs


def _placed_order(
    table: Table,
    pairs: Sequence[tuple[str, str]],
    at_end: bool,
    exclude: set[str] | None = None,
) -> list[str]:
    """Column order with each new column (source, new id) after its source's top-level
    block (column, group or category), or all at the end."""
    exclude = exclude or set()
    current = [c.id for c in table.columns if c.id not in exclude]
    new_ids = [new for _, new in pairs]
    if at_end:
        return current + new_ids
    after: dict[str, list[str]] = {}
    for node in layout_tree(table):
        block = [c.id for c in table.columns[node.start : node.end]]
        last = next((c for c in reversed(block) if c not in exclude), None)
        for source, new in pairs:
            if source in block:
                key = last if last is not None else ""
                after.setdefault(key, []).append(new)
    order = list(after.pop("", []))
    for column_id in current:
        order.append(column_id)
        order += after.pop(column_id, [])
    for rest in after.values():  # pragma: no cover - every block has a column left
        order += rest
    return order


# --- project constants -------------------------------------------------------------


def _check_constant(doc: Document, name: str, value: float, ignore_id: str | None) -> None:
    from curvis.formula.constants import constant_name_problem

    problem = constant_name_problem(name)
    if problem is not None:
        raise CommandError(problem)
    if any(c.name == name and c.id != ignore_id for c in doc.constants):
        raise CommandError(f"There is already a project constant called '{name}'.")
    if not math.isfinite(value):
        raise CommandError("The value must be a finite number.")


class AddConstant(Command):
    """Add a project constant."""

    def __init__(self, constant: ProjectConstant) -> None:
        self.constant = constant

    def do(self, doc: Document) -> None:
        _check_constant(doc, self.constant.name, self.constant.value, None)
        if any(c.id == self.constant.id for c in doc.constants):
            raise CommandError("This constant is already in the project.")
        doc.constants.append(self.constant)

    def undo(self, doc: Document) -> None:
        doc.constants.remove(self.constant)

    @property
    def summary(self) -> str:
        return f"Add constant '{self.constant.name}'"

    @property
    def touched_nodes(self) -> tuple[str, ...]:
        return (constant_node(self.constant.name),)


class EditConstant(Command):
    """Change the name, value and unit of a project constant."""

    def __init__(self, constant_id: str, name: str, value: float, unit: str = "") -> None:
        self.constant_id = constant_id
        self.name = name
        self.value = float(value)
        self.unit = unit
        self._old: ProjectConstant | None = None
        self._at = 0

    def do(self, doc: Document) -> None:
        try:
            old = doc.constant(self.constant_id)
        except KeyError:
            raise CommandError("There is no such constant.") from None
        _check_constant(doc, self.name, self.value, self.constant_id)
        self._old = old
        self._at = doc.constants.index(old)
        doc.constants[self._at] = ProjectConstant(self.name, self.value, self.unit, old.id)

    def undo(self, doc: Document) -> None:
        assert self._old is not None
        doc.constants[self._at] = self._old

    @property
    def summary(self) -> str:
        return f"Change constant '{self._old.name if self._old else self.name}'"

    @property
    def touched_nodes(self) -> tuple[str, ...]:
        old = self._old.name if self._old else self.name
        return tuple(dict.fromkeys((constant_node(old), constant_node(self.name))))


class RemoveConstant(Command):
    """Remove a project constant."""

    def __init__(self, constant_id: str) -> None:
        self.constant_id = constant_id
        self._old: ProjectConstant | None = None
        self._at = 0

    def do(self, doc: Document) -> None:
        try:
            self._old = doc.constant(self.constant_id)
        except KeyError:
            raise CommandError("There is no such constant.") from None
        self._at = doc.constants.index(self._old)
        del doc.constants[self._at]

    def undo(self, doc: Document) -> None:
        assert self._old is not None
        doc.constants.insert(self._at, self._old)

    @property
    def summary(self) -> str:
        return f"Remove constant '{self._old.name}'" if self._old else "Remove constant"

    @property
    def touched_nodes(self) -> tuple[str, ...]:
        return (constant_node(self._old.name),) if self._old else ()
