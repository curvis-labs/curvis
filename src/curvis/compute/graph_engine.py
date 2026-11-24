"""Keep the graphs of one document up to date.

Each graph (:class:`~curvis.charts.spec.ChartSpec`) is a node in the
:class:`~curvis.compute.calc_engine.CalcEngine`'s dependency graph. It reads its table
(so any change to the table restales it), the columns of its series, the X and Error
columns, and the curve-fit analyses it overlays. When any of those change, the shared
:class:`~curvis.compute.recalc.Recalculator` marks it stale and reruns it through the
scheduler, after the analyses it reads have finished.

The job is :func:`curvis.charts.data.compute`, which turns the copied inputs into
:class:`~curvis.charts.data.ChartData`; the Graph panel only draws that. A graph whose
fit failed is still drawn, without that fit's curves.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from curvis.analysis.result import DEFAULT_P_DISPLAY, PDisplay
from curvis.charts.data import ChartData, compute, gather
from curvis.charts.spec import ChartFamily, ChartSpec, ErrorBars, PLabel
from curvis.compute.analysis_engine import AnalysisEngine
from curvis.compute.calc_engine import CalcEngine
from curvis.compute.jobs import Job, JobError
from curvis.compute.recalc import UPSTREAM_ERROR, NodeJob
from curvis.core import CycleError, Document, UndoEvent
from curvis.data import ColumnRole, Table, series, survival_pairs

type _Signature = tuple[ChartSpec, tuple[str, ...]]


def graph_reads(table: Table, spec: ChartSpec) -> list[str]:
    """What a graph reads: its table, the columns it draws, the fits it overlays, the
    analysis it draws (interaction graphs) and the one whose comparisons it shows."""
    reads = [table.id]
    if spec.family is ChartFamily.SURVIVAL:
        pairs = {p.source: p for p in survival_pairs(table)}
        for style in spec.series:
            pair = pairs.get(style.source)
            if pair is not None:
                reads += [pair.time_id, pair.event_id]
        return list(dict.fromkeys(reads))
    by_source = {s.group_id or s.column_ids[0]: s for s in series(table)}
    for style in spec.series:
        found = by_source.get(style.source)
        if found is not None:
            reads.extend(found.column_ids)
        if style.error_bars is ErrorBars.ERROR_COLUMN and style.error_column is not None:
            reads.append(style.error_column)
    if spec.family is ChartFamily.XY:
        reads.extend(c.id for c in table.columns if c.role is ColumnRole.X)
        reads.extend(o.analysis_id for o in spec.overlays)
    if spec.analysis_id is not None:
        reads.append(spec.analysis_id)
    if spec.comparisons is not None:
        reads.append(spec.comparisons.analysis_id)
    return list(dict.fromkeys(reads))


class GraphEngine(QObject):
    """Computes the plot data of ``document``'s graphs in ``calc_engine``'s graph."""

    graphs_changed = Signal()
    """Graphs were added, removed or changed."""
    state_changed = Signal(str)
    """A graph (by id) became stale, or got new plot data or an error."""

    def __init__(
        self,
        document: Document,
        calc_engine: CalcEngine,
        analysis_engine: AnalysisEngine | None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.document = document
        self.calc_engine = calc_engine
        self.analysis_engine = analysis_engine
        self.data: dict[str, ChartData] = {}
        """Latest plot data of each graph."""
        self.errors: dict[str, str] = {}
        """Why a graph has no plot data, for each graph whose last run failed."""
        self.p_display = DEFAULT_P_DISPLAY
        """How P values are shown (Settings), on brackets labelled with them."""
        self._signatures: dict[str, _Signature] = {}
        self._definitions_changed = False
        self._closed = False
        recalculator = calc_engine.recalculator
        recalculator.stale.connect(self._on_stale)
        recalculator.node_finished.connect(self._on_finished)
        recalculator.node_failed.connect(self._on_failed)
        recalculator.node_cancelled.connect(self._on_cancelled)
        calc_engine.attach(self)
        self._unsubscribe = document.undo_stack.subscribe(self._after_event)

    # --- public ----------------------------------------------------------------------

    def is_stale(self, graph_id: str) -> bool:
        return self.calc_engine.recalculator.is_stale(graph_id)

    def set_p_display(self, display: PDisplay) -> None:
        """Show P values as ``display``: graphs whose brackets show P values redraw."""
        if display is self.p_display:
            return
        self.p_display = display
        nodes = [
            g.id
            for g in self.document.graphs
            if g.comparisons is not None
            and g.comparisons.label is PLabel.P_VALUE
            and g.id in self._signatures
        ]
        if nodes:
            self.calc_engine.recalculator.recompute(nodes)

    def close(self) -> None:
        self._closed = True
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
        """Declare every graph in the dependency graph; return those whose definition or
        reads changed."""
        graph = self.calc_engine.graph
        old = self._signatures
        new: dict[str, _Signature] = {}
        for spec in self.document.graphs:
            try:
                reads = tuple(graph_reads(self.document.table(spec.table_id), spec))
            except KeyError:
                reads = ()
            new[spec.id] = (spec, reads)
        for node in old:
            if node not in new:
                graph.remove_node(node)
                self.data.pop(node, None)
                self.errors.pop(node, None)
        changed = [n for n, signature in new.items() if old.get(n) != signature]
        for node in changed:
            try:
                graph.set_reads(node, new[node][1])
            except CycleError:  # pragma: no cover - nothing reads a graph
                graph.set_reads(node, [])
        if changed or list(old) != list(new):
            self._definitions_changed = True
        self._signatures = new
        return changed

    def plan(self, node: str) -> NodeJob | None:
        try:
            spec = self.document.graph(node)
        except KeyError:
            return None
        try:
            table = self.document.table(spec.table_id)
        except KeyError:
            raise ValueError("The table of this graph was deleted.") from None
        results = self.analysis_engine.results if self.analysis_engine is not None else {}
        inputs = gather(table, spec, results, self.p_display)
        return NodeJob(Job(compute, (inputs,)))

    # --- following the recalculator ----------------------------------------------------

    def _after_event(self, _event: UndoEvent) -> None:
        if self._definitions_changed:
            self._definitions_changed = False
            self.graphs_changed.emit()

    def _on_stale(self, nodes: list[str]) -> None:
        for node in nodes:
            if node in self._signatures:
                self.state_changed.emit(node)

    def _on_finished(self, node: str, result: object) -> None:
        if node not in self._signatures or not isinstance(result, ChartData):
            return
        self.data[node] = result
        self.errors.pop(node, None)
        self.state_changed.emit(node)

    def _on_failed(self, node: str, error: JobError) -> None:
        if node not in self._signatures:
            return
        if error.error_type == UPSTREAM_ERROR:
            # A fit (or a calculated column) it reads failed: draw the data anyway.
            QTimer.singleShot(0, lambda: self._redraw_without_upstream(node))
            return
        self.errors[node] = error.message
        self.state_changed.emit(node)

    def _redraw_without_upstream(self, node: str) -> None:
        if not self._closed and node in self._signatures and not self.is_stale(node):
            self.calc_engine.recalculator.recompute([node])

    def _on_cancelled(self, node: str) -> None:
        if node in self._signatures:
            self.state_changed.emit(node)
