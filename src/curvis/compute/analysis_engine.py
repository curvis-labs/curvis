"""Keep the analyses of one document up to date.

Each :class:`~curvis.analysis.base.Analysis` is a node in the
:class:`~curvis.compute.calc_engine.CalcEngine`'s dependency graph, reading the columns
of its selected series. When those change (or a calculated column it reads gets new
values), the shared :class:`~curvis.compute.recalc.Recalculator` marks the analysis
stale and reruns it through the scheduler. A new or edited analysis runs at once.

Results are derived and kept here, not in the document. While an analysis is stale its
last result stays available, so the Results panel can show it with a "Recalculating…"
badge. An analysis that cannot run keeps the reason in :attr:`AnalysisEngine.errors`
instead of a result.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from curvis.analysis.base import Analysis, AnalysisError
from curvis.analysis.result import Result
from curvis.compute.calc_engine import CalcEngine
from curvis.compute.jobs import Job, JobError
from curvis.compute.recalc import UPSTREAM_ERROR, NodeJob
from curvis.core import CycleError, Document, UndoEvent

type _Signature = tuple[Analysis, tuple[str, ...]]


class AnalysisEngine(QObject):
    """Runs the analyses of ``document`` in ``calc_engine``'s graph and scheduler."""

    analyses_changed = Signal()
    """Analyses were added, removed or edited."""
    state_changed = Signal(str)
    """An analysis (by id) became stale, or got a new result or error."""

    def __init__(
        self, document: Document, calc_engine: CalcEngine, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self.document = document
        self.calc_engine = calc_engine
        self.results: dict[str, Result] = {}
        """Latest result of each analysis that ran without an error."""
        self.errors: dict[str, str] = {}
        """Why an analysis has no result, for each analysis whose last run failed."""
        self._signatures: dict[str, _Signature] = {}
        self._definitions_changed = False
        recalculator = calc_engine.recalculator
        recalculator.stale.connect(self._on_stale)
        recalculator.node_finished.connect(self._on_finished)
        recalculator.node_failed.connect(self._on_failed)
        recalculator.node_cancelled.connect(self._on_cancelled)
        calc_engine.attach(self)
        self._unsubscribe = document.undo_stack.subscribe(self._after_event)

    # --- public ----------------------------------------------------------------------

    def is_stale(self, analysis_id: str) -> bool:
        """True while the analysis waits for, or is running, a new computation."""
        return self.calc_engine.recalculator.is_stale(analysis_id)

    @property
    def busy(self) -> bool:
        return any(self.is_stale(a) for a in self._signatures)

    def close(self) -> None:
        self._unsubscribe()
        recalculator = self.calc_engine.recalculator
        for signal, slot in (
            (recalculator.stale, self._on_stale),
            (recalculator.node_finished, self._on_finished),
            (recalculator.node_failed, self._on_failed),
            (recalculator.node_cancelled, self._on_cancelled),
        ):
            signal.disconnect(slot)

    # --- NodeProvider ------------------------------------------------------------------

    def owns(self, node: str) -> bool:
        return node in self._signatures

    def sync(self) -> list[str]:
        """Declare every analysis in the graph; return those whose definition changed."""
        graph = self.calc_engine.graph
        old = self._signatures
        new: dict[str, _Signature] = {}
        for analysis in self.document.analyses:
            try:
                table = self.document.table(analysis.table_id)
                reads = tuple(analysis.analysis_type.reads(table, analysis))
            except KeyError:
                reads = ()
            new[analysis.id] = (analysis, reads)
        for node in old:
            if node not in new:
                graph.remove_node(node)
                self.results.pop(node, None)
                self.errors.pop(node, None)
        changed = [n for n, signature in new.items() if old.get(n) != signature]
        for node in changed:
            try:
                graph.set_reads(node, new[node][1])
            except CycleError:  # pragma: no cover - nothing reads an analysis yet
                graph.set_reads(node, [])
        if changed or list(old) != list(new):
            self._definitions_changed = True
        self._signatures = new
        # New or edited analyses, and those whose reads changed (a column added to a
        # selected group): their inputs are different, so they run again.
        return changed

    def plan(self, node: str) -> NodeJob | None:
        try:
            analysis = self.document.analysis(node)
        except KeyError:
            return None
        try:
            table = self.document.table(analysis.table_id)
        except KeyError:
            raise AnalysisError("The table of this analysis was deleted.") from None
        try:
            kind = analysis.analysis_type
        except KeyError:
            raise AnalysisError(f"Curvis has no analysis of type '{analysis.type}'.") from None
        problem = kind.problem(table) or kind.check(table, analysis.inputs, analysis.params)
        if problem is not None:
            raise AnalysisError(f"{problem} Click Edit… to change the analysis.")
        inputs = kind.gather(table, analysis)
        job = Job(kind.run, (inputs, analysis.params), imports=kind.imports)
        return NodeJob(job, kind.weight)

    # --- following the recalculator ----------------------------------------------------

    def _after_event(self, _event: UndoEvent) -> None:
        if self._definitions_changed:
            self._definitions_changed = False
            self.analyses_changed.emit()

    def _on_stale(self, nodes: list[str]) -> None:
        for node in nodes:
            if node in self._signatures:
                self.state_changed.emit(node)

    def _on_finished(self, node: str, result: object) -> None:
        if node not in self._signatures or not isinstance(result, Result):
            return
        self.results[node] = result
        self.errors.pop(node, None)
        self.state_changed.emit(node)

    def _on_failed(self, node: str, error: JobError) -> None:
        if node not in self._signatures:
            return
        known = error.error_type in (AnalysisError.__name__, UPSTREAM_ERROR)
        message = error.message if known else f"The analysis failed: {error.message}"
        self.errors[node] = message
        self.results.pop(node, None)
        self.state_changed.emit(node)

    def _on_cancelled(self, node: str) -> None:
        if node in self._signatures:
            self.state_changed.emit(node)
