"""Keep the calculated columns of one document up to date.

The :class:`CalcEngine` declares every calculated column in a
:class:`~curvis.core.DependencyGraph` (it reads the columns its formula refers to, the
columns of its roles and its constants) and lets a
:class:`~curvis.compute.recalc.Recalculator` recompute it through the scheduler when
any of those change.

After every do, undo or redo the engine first brings the graph up to date with the
document's calculations (before the recalculator looks at the graph), then recomputes
the columns whose definition changed: a new or edited calculation, a renamed or added
column that a formula refers to.

Values of calculated columns are derived data: the engine writes the results straight
into the column, not through a command, so recomputing is never an undo step. Formula
errors leave the column empty (NaN) and are kept in :attr:`CalcEngine.errors`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np
import numpy.typing as npt
from PySide6.QtCore import QObject, Signal

from curvis.compute.jobs import Job, JobError
from curvis.compute.recalc import NodeJob, Recalculator
from curvis.compute.scheduler import Scheduler
from curvis.core import (
    CalculatedColumn,
    Calculation,
    CycleError,
    DependencyGraph,
    Document,
    ProjectConstant,
    UndoEvent,
    constant_node,
)
from curvis.data import Table, role_values

type _Signature = tuple[object, ...]


def _evaluate(
    formula: str,
    columns: dict[str, npt.NDArray[Any]],
    constants: dict[str, float],
    n_rows: int,
    roles: dict[str, npt.NDArray[np.float64]],
) -> npt.NDArray[np.float64]:
    from curvis.formula import evaluate

    return evaluate(formula, columns, constants, n_rows, roles)


class CalcEngine(QObject):
    """Recomputes the calculated columns of ``document`` with ``scheduler``.

    ``global_constants`` returns the user's global constants (from the settings).
    """

    values_changed = Signal(str)
    """Id of a table whose calculated columns got new values or errors."""

    def __init__(
        self,
        document: Document,
        scheduler: Scheduler,
        global_constants: Callable[[], Sequence[ProjectConstant]] = list,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.document = document
        self.global_constants = global_constants
        self.graph = DependencyGraph()
        self.errors: dict[str, str] = {}
        """Why a calculated column (by id) holds no values, for each column that failed."""
        self._signatures: dict[str, _Signature] = {}
        self._graph_errors: dict[str, str] = {}  # problems found while declaring a node
        self._pending: list[str] = []
        self._global_names = {c.name for c in global_constants()}
        # The graph must be up to date before the recalculator reads it after a command,
        # and definition changes are recomputed after it has marked its stale nodes.
        self._unsubscribe_sync = document.undo_stack.subscribe(self._sync_on_event)
        self.recalculator = Recalculator(document, self.graph, scheduler, self._plan, self)
        self._unsubscribe_run = document.undo_stack.subscribe(self._run_pending)
        self.recalculator.node_finished.connect(self._on_finished)
        self.recalculator.node_failed.connect(self._on_failed)
        self._sync()
        self.recompute_all()

    # --- public ----------------------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self.recalculator.busy

    def is_stale(self, column_id: str) -> bool:
        return self.recalculator.is_stale(column_id)

    def recompute_all(self) -> None:
        """Recompute every calculated column (e.g. after opening a project)."""
        self.recalculator.recompute(list(self._signatures))

    def constants_changed(self, names: Sequence[str]) -> None:
        """The constants ``names`` were added, changed or removed outside a command."""
        self.recalculator.changed([constant_node(n) for n in names], delay_ms=0)

    def global_constants_changed(self) -> None:
        """The global constants changed: recompute what uses the old or the new ones."""
        current = {c.name for c in self.global_constants()}
        self.constants_changed(sorted(self._global_names | current))
        self._global_names = current

    def close(self) -> None:
        self._unsubscribe_sync()
        self._unsubscribe_run()
        self.recalculator.close()

    # --- following the document --------------------------------------------------------

    def _sync_on_event(self, _event: UndoEvent) -> None:
        self._pending = self._sync()

    def _run_pending(self, _event: UndoEvent) -> None:
        pending, self._pending = self._pending, []
        if pending:
            self.recalculator.recompute(pending)

    def _sync(self) -> list[str]:
        """Declare every calculated column in the graph; return those whose definition changed."""
        old = self._signatures
        new: dict[str, _Signature] = {}
        reads: dict[str, list[str]] = {}
        for calculation in self.document.calculations:
            try:
                table = self.document.table(calculation.table_id)
            except KeyError:
                continue
            for output in calculation.columns:
                node_reads = _reads(calculation, output, table)
                reads[output.column_id] = node_reads
                new[output.column_id] = (
                    calculation.formula,
                    calculation.pairing,
                    calculation.target_index(output),
                    tuple(node_reads),
                )
        for node in self.graph.nodes:
            if node not in new:
                self.graph.remove_node(node)
                self.errors.pop(node, None)
                self._graph_errors.pop(node, None)
        changed = [n for n, signature in new.items() if old.get(n) != signature]
        for node in changed:
            self._graph_errors.pop(node, None)
            if node in reads[node]:
                self.graph.set_reads(node, [n for n in reads[node] if n != node])
                self._graph_errors[node] = "The formula reads its own result."
                continue
            try:
                self.graph.set_reads(node, reads[node])
            except CycleError as exc:
                self.graph.set_reads(node, [])
                self._graph_errors[node] = _cycle_message(exc, self.document)
        self._signatures = new
        return changed

    # --- computing -----------------------------------------------------------------------

    def _plan(self, node: str) -> NodeJob | None:
        found = self._find(node)
        if found is None:
            return None
        calculation, output, table = found
        if node in self._graph_errors:
            raise ValueError(self._graph_errors[node])
        index = calculation.target_index(output)
        if index < 0:
            raise ValueError("The column it was calculated from was deleted.")
        from curvis.formula import parse, resolve_constants

        formula = parse(calculation.formula)
        columns: dict[str, npt.NDArray[Any]] = {}
        for name in formula.columns:
            column = table.column_by_name(name)
            if column is not None:
                columns[name] = column.values.copy()
        try:
            roles = role_values(table, calculation.pairing, index, set(formula.roles))
        except KeyError:
            raise ValueError("A column used by this calculation was deleted.") from None
        constants = resolve_constants(self.global_constants(), self.document.constants)
        job = Job(_evaluate, (calculation.formula, columns, constants, table.n_rows, roles))
        return NodeJob(job, "light")

    def _on_finished(self, node: str, result: object) -> None:
        found = self._find(node)
        if found is None or not isinstance(result, np.ndarray):
            return
        _calculation, _output, table = found
        if len(result) != table.n_rows:
            return  # the table changed size; a newer run is on its way
        column = table.column(node)
        result.flags.writeable = False
        column.values = result
        self.errors.pop(node, None)
        self.values_changed.emit(table.id)

    def _on_failed(self, node: str, error: JobError) -> None:
        found = self._find(node)
        if found is None:
            return
        _calculation, _output, table = found
        self.errors[node] = error.message
        empty = np.full(table.n_rows, math.nan)
        empty.flags.writeable = False
        table.column(node).values = empty
        self.values_changed.emit(table.id)

    def _find(self, node: str) -> tuple[Calculation, CalculatedColumn, Table] | None:
        calculation = self.document.calculation_of(node)
        if calculation is None:
            return None
        output = calculation.output_for(node)
        try:
            table = self.document.table(calculation.table_id)
            table.column(node)
        except KeyError:
            return None
        assert output is not None
        return calculation, output, table


def _reads(calculation: Calculation, output: CalculatedColumn, table: Table) -> list[str]:
    """Nodes a calculated column reads: columns by name, role columns and constants."""
    from curvis.formula import FormulaError, parse

    try:
        formula = parse(calculation.formula)
    except FormulaError:
        return []
    reads: list[str] = []
    for name in formula.columns:
        column = table.column_by_name(name)
        if column is not None:
            reads.append(column.id)
    index = calculation.target_index(output)
    if index >= 0:
        sources = calculation.pairing.role_sources(index)
        for key in formula.roles:
            reads += sources.get(key, [])
    reads += [constant_node(c) for c in formula.constants]
    return list(dict.fromkeys(reads))


def _cycle_message(error: CycleError, document: Document) -> str:
    names: list[str] = []
    for node in error.path:
        name = next((c.name for t in document.tables for c in t.columns if c.id == node), node)
        names.append(name)
    return "The formula depends on its own result: " + " → ".join(names)
