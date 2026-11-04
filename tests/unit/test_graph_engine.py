"""Graphs as dependency-graph nodes: they get plot data, redraw when their data or fits
change, and still draw when a fit they show fails."""

import math
import time
from collections.abc import Iterator

import numpy as np
import pytest
from pytestqt.qtbot import QtBot

from curvis.analysis.base import Analysis
from curvis.analysis.fit.analyses import LinearParams
from curvis.charts.builder import default_spec
from curvis.charts.data import ChartData
from curvis.charts.spec import ChartFamily
from curvis.compute import Scheduler
from curvis.compute.analysis_engine import AnalysisEngine
from curvis.compute.calc_engine import CalcEngine
from curvis.compute.graph_engine import GraphEngine, graph_reads
from curvis.core import (
    AddTable,
    Document,
    GroupColumns,
    RemoveColumn,
    SetCellValue,
    add_analysis,
    add_graph,
    remove_graph,
)
from curvis.data import Column, Table

NAN = math.nan


class Lab:
    def __init__(self, scheduler: Scheduler) -> None:
        self.doc = Document()
        self.doc.execute(
            AddTable(
                Table(
                    "T",
                    [
                        Column.new("x", "X", [1, 2, 3, 4], id="x"),
                        Column.new("a1", "Y", [1, 2, 3, 4], id="a1"),
                        Column.new("a2", "Y", [3, 4, NAN, 6], id="a2"),
                        Column.new("b", "Y", [10, 20, 30, 41], id="b"),
                    ],
                    id="T",
                )
            )
        )
        self.doc.execute(GroupColumns("T", ["a1", "a2"], "A", group_id="gA"))
        self.scheduler = scheduler
        self.calc = CalcEngine(self.doc, scheduler)
        self.analyses = AnalysisEngine(self.doc, self.calc)
        self.engine = GraphEngine(self.doc, self.calc, self.analyses)
        self.states: list[str] = []
        self.engine.state_changed.connect(self.states.append)
        self.changes = 0
        self.engine.graphs_changed.connect(self._count)

    def _count(self) -> None:
        self.changes += 1

    @property
    def table(self) -> Table:
        return self.doc.tables[0]

    def plot(self, family: ChartFamily = ChartFamily.XY) -> str:
        spec = default_spec(self.table, family, self.doc.analyses)
        self.doc.execute(add_graph(self.doc, spec))
        self.settle()
        return spec.id

    def data(self, graph_id: str) -> ChartData:
        assert graph_id not in self.engine.errors, self.engine.errors[graph_id]
        return self.engine.data[graph_id]

    def settle(self, timeout: float = 10) -> None:
        deadline = time.monotonic() + timeout
        while True:
            self.scheduler.wait_idle(50)
            if not (self.calc.busy or self.scheduler.busy):
                # A graph whose fit failed is redrawn from a zero-delay timer.
                from PySide6.QtCore import QCoreApplication

                QCoreApplication.processEvents()
                if not (self.calc.busy or self.scheduler.busy):
                    break
            assert time.monotonic() < deadline, "recompute did not settle"
        self.scheduler.wait_idle()


@pytest.fixture
def scheduler(qtbot: QtBot) -> Iterator[Scheduler]:
    s = Scheduler(debounce_ms=20, max_processes=1)
    yield s
    s.shutdown()


@pytest.fixture
def lab(scheduler: Scheduler) -> Iterator[Lab]:
    harness = Lab(scheduler)
    yield harness
    harness.engine.close()
    harness.analyses.close()
    harness.calc.close()


def test_a_new_graph_gets_plot_data(lab: Lab) -> None:
    graph = lab.plot()
    data = lab.data(graph)
    assert [s.name for s in data.xy] == ["A", "b"]
    np.testing.assert_array_equal(data.xy[0].y, [2, 3, 3, 5])
    assert lab.changes == 1


def test_it_redraws_when_its_data_changes(lab: Lab) -> None:
    graph = lab.plot()
    lab.states.clear()
    lab.doc.execute(SetCellValue("T", "a2", 2, 5))
    assert lab.engine.is_stale(graph) and graph in lab.states
    lab.settle()
    np.testing.assert_array_equal(lab.data(graph).xy[0].y, [2, 3, 4, 5])


def test_reads(lab: Lab) -> None:
    spec = default_spec(lab.table, ChartFamily.XY)
    assert graph_reads(lab.table, spec) == ["T", "a1", "a2", "b", "x"]
    bar = default_spec(lab.table, ChartFamily.BAR)
    assert "x" not in graph_reads(lab.table, bar)


def test_it_redraws_after_its_fit(lab: Lab) -> None:
    fit = Analysis("linear_regression", "T", ("b",), LinearParams())
    lab.doc.execute(add_analysis(lab.doc, fit))
    lab.settle()
    graph = lab.plot()
    (curve,) = lab.data(graph).curves
    assert curve.py[-1] == pytest.approx(lab.analyses.results[fit.id].curves[0].y[-1])
    lab.doc.execute(SetCellValue("T", "b", 3, 100))
    lab.settle()
    (moved,) = lab.data(graph).curves
    assert moved.py[-1] > curve.py[-1] + 10


def test_it_still_draws_when_its_fit_fails(lab: Lab) -> None:
    fit = Analysis("linear_regression", "T", ("b",), LinearParams())
    lab.doc.execute(add_analysis(lab.doc, fit))
    lab.settle()
    graph = lab.plot()
    lab.doc.execute(RemoveColumn("T", "b"))
    lab.settle()
    assert fit.id in lab.analyses.errors
    data = lab.data(graph)
    assert [s.name for s in data.xy] == ["A"] and data.curves == ()
    assert "1 series is no longer in the table." in data.notes


def test_removed_graphs_are_forgotten(lab: Lab) -> None:
    graph = lab.plot(ChartFamily.BAR)
    assert lab.data(graph).categories
    lab.doc.execute(remove_graph(lab.doc, graph))
    lab.settle()
    assert graph not in lab.engine.data and not lab.engine.owns(graph)
    lab.doc.undo()
    lab.settle()
    assert lab.data(graph).categories
