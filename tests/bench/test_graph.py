"""Benchmarks for graphs. Target: graph redraw after a data edit < 50 ms (CLAUDE.md).

Each round edits one cell (a command), then waits until the graph has been recomputed
through the dependency graph and the scheduler, redrawn by the Graph panel and painted.
The scheduler's debounce is 0 here, as in the other benchmarks: the ~150 ms debounce
is a deliberate wait for more typing, not work. The fit curve is a fixed result, so
the time is the graph's alone (refitting is benchmarked in test_fit.py).
"""

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from itertools import count
from typing import Any

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QEventLoop, QObject
from PySide6.QtWidgets import QApplication

from curvis.analysis.result import FittedCurve, Result
from curvis.charts.builder import default_spec
from curvis.charts.spec import ChartFamily, ChartSpec, ChartVariant, FitOverlay
from curvis.compute import Scheduler
from curvis.compute.calc_engine import CalcEngine
from curvis.compute.graph_engine import GraphEngine
from curvis.core import AddTable, Document, SetCellValue, add_graph
from curvis.data import Column, ColumnRole, ReplicateGroup, Table
from curvis.ui.graph_panel import GraphPanel

REDRAW_TARGET_S = 0.050
ROWS = 1_000
GROUPS = 4
REPLICATES = 3


@pytest.fixture(scope="module")
def scheduler(qapp: QApplication) -> Iterator[Scheduler]:
    s = Scheduler(debounce_ms=0, max_threads=1, max_processes=1)
    yield s
    s.shutdown()


def big_table() -> Table:
    """X plus 4 replicate groups of 3 columns, 1,000 rows (12,000 Y values)."""
    rng = np.random.default_rng(1)
    x = np.logspace(-3, 2, ROWS)
    columns = [Column.new("Dose", ColumnRole.X, x)]
    groups = []
    for g in range(GROUPS):
        group = ReplicateGroup(f"Group {g + 1}")
        groups.append(group)
        for r in range(REPLICATES):
            y = 10 * g + 80 / (1 + 0.5 / x) + rng.normal(0, 3, ROWS)
            columns.append(Column.new(f"G{g + 1} {r + 1}", ColumnRole.Y, y, group_id=group.id))
    return Table("Big", columns, groups)


@dataclass
class _Results:
    """Stands in for the analysis engine: one fixed fit result."""

    results: dict[str, Result] = field(default_factory=dict)


class PaintCounter(QObject):
    """Counts the paint events of a widget."""

    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt API)
        if event.type() == QEvent.Type.Paint:
            self.count += 1
        return False


class GraphBench:
    def __init__(self, scheduler: Scheduler, family: ChartFamily, variant: ChartVariant) -> None:
        self.doc = Document()
        self.table = big_table()
        self.doc.execute(AddTable(self.table))
        spec: ChartSpec = default_spec(self.table, family)
        spec = replace(spec, variant=variant)
        analyses = _Results()
        if family is ChartFamily.XY:
            grid = np.logspace(-3, 2, 200)
            fit = 80 / (1 + 0.5 / grid)
            curve = FittedCurve(
                self.table.replicate_groups[0].id,
                "Group 1",
                tuple(grid),
                tuple(fit),
                tuple(fit - 2),
                tuple(fit + 2),
                log_x=True,
            )
            analyses.results["fit"] = Result("m", "s", curves=(curve,))
            spec = replace(spec, overlays=(FitOverlay("fit", show_band=True),))
        self.calc = CalcEngine(self.doc, scheduler)
        self.engine = GraphEngine(self.doc, self.calc, analyses)  # type: ignore[arg-type]
        self.panel = GraphPanel(self.doc, self.engine)
        self.panel.resize(640, 520)
        self.panel.set_table(self.table.id)
        self.panel.show()
        self.graph_id = spec.id
        self._loop = QEventLoop()
        self.engine.state_changed.connect(self._on_state)
        self.doc.execute(add_graph(self.doc, spec))
        self.wait()
        self._values = count()
        self.column = self.table.columns[1].id
        chart = self.panel.chart
        assert chart is not None
        self.viewport = chart.plot_widget.viewport()
        self.paints = PaintCounter()
        self.viewport.installEventFilter(self.paints)
        QApplication.processEvents()

    def _on_state(self, graph_id: str) -> None:
        if graph_id == self.graph_id and not self.engine.is_stale(graph_id):
            self._loop.quit()

    def wait(self) -> None:
        if self.engine.is_stale(self.graph_id) or self.graph_id not in self.engine.data:
            self._loop.exec()

    def edit_and_redraw(self) -> None:
        value = 50.0 + next(self._values) % 7
        self.doc.execute(SetCellValue(self.table.id, self.column, ROWS // 2, value))
        painted = self.paints.count
        self.wait()
        # Until the graph is on screen: the app's own repaint, as after a real edit.
        while self.paints.count == painted:
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 5)

    def close(self) -> None:
        self.viewport.removeEventFilter(self.paints)
        self.panel.close()
        self.engine.close()
        self.calc.close()


def run_redraw(
    benchmark: Any, scheduler: Scheduler, family: ChartFamily, variant: ChartVariant
) -> None:
    bench = GraphBench(scheduler, family, variant)
    try:
        benchmark.pedantic(bench.edit_and_redraw, rounds=30, warmup_rounds=3)
        chart = bench.panel.chart
        assert chart is not None and chart.data is not None
        assert benchmark.stats.stats.min < REDRAW_TARGET_S
    finally:
        bench.close()


def test_xy_redraw_after_data_edit(benchmark: Any, scheduler: Scheduler) -> None:
    """XY: mean ± SD of 4 replicate groups × 3 columns × 1,000 rows, and a fit curve with
    its confidence band."""
    run_redraw(benchmark, scheduler, ChartFamily.XY, ChartVariant.SCATTER)


def test_bar_redraw_after_data_edit(benchmark: Any, scheduler: Scheduler) -> None:
    """Bar: mean ± SD of the same 4 groups (3,000 values each)."""
    run_redraw(benchmark, scheduler, ChartFamily.BAR, ChartVariant.BAR)


def test_box_redraw_after_data_edit(benchmark: Any, scheduler: Scheduler) -> None:
    """Box plot of the same 4 groups."""
    run_redraw(benchmark, scheduler, ChartFamily.BAR, ChartVariant.BOX)
