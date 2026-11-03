"""Analyses as dependency-graph nodes: they run, rerun when their data changes, wait for
the calculated columns they read, and report why they cannot run."""

import math
import time
from collections.abc import Iterator

import pytest
from pytestqt.qtbot import QtBot

from curvis.analysis.base import Analysis
from curvis.analysis.descriptive import DescriptiveParams
from curvis.analysis.result import Result
from curvis.compute import Scheduler
from curvis.compute.analysis_engine import AnalysisEngine
from curvis.compute.calc_engine import CalcEngine
from curvis.core import (
    AddTable,
    Calculation,
    Document,
    GroupColumns,
    RemoveColumn,
    RenameColumn,
    SetCellValue,
    add_analysis,
    add_calculation,
    edit_analysis,
    remove_analysis,
)
from curvis.data import Column, Pairing, Table

NAN = math.nan


class Lab:
    def __init__(self, scheduler: Scheduler) -> None:
        self.doc = Document()
        self.doc.execute(
            AddTable(
                Table(
                    "T",
                    [
                        Column.new("x", "X", [1, 2, 3], id="x"),
                        Column.new("a1", "Y", [1, 2, 3], id="a1"),
                        Column.new("a2", "Y", [3, 4, NAN], id="a2"),
                        Column.new("b", "Y", [10, 20, 30], id="b"),
                    ],
                    id="T",
                )
            )
        )
        self.doc.execute(GroupColumns("T", ["a1", "a2"], "A", group_id="gA"))
        self.scheduler = scheduler
        self.calc = CalcEngine(self.doc, scheduler)
        self.engine = AnalysisEngine(self.doc, self.calc)
        self.states: list[str] = []
        self.engine.state_changed.connect(self.states.append)
        self.changes = 0
        self.engine.analyses_changed.connect(self._count)

    def _count(self) -> None:
        self.changes += 1

    def add(self, inputs: tuple[str, ...] = ("gA",), **params: bool) -> Analysis:
        analysis = Analysis("descriptive_statistics", "T", inputs, DescriptiveParams(**params))
        self.doc.execute(add_analysis(self.doc, analysis))
        self.settle()
        return analysis

    def result(self, analysis: Analysis) -> Result:
        assert analysis.id not in self.engine.errors, self.engine.errors[analysis.id]
        return self.engine.results[analysis.id]

    def means(self, analysis: Analysis, table: int = 0) -> list[float]:
        return [row[2] for row in self.result(analysis).tables[table].rows]  # type: ignore[misc]

    def settle(self, timeout: float = 10) -> None:
        deadline = time.monotonic() + timeout
        while self.calc.busy or self.scheduler.busy:
            assert time.monotonic() < deadline, "recompute did not settle"
            self.scheduler.wait_idle(50)
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
    harness.calc.close()


def test_a_new_analysis_runs(lab: Lab) -> None:
    analysis = lab.add()
    assert lab.means(analysis) == [2, 3, 3]
    assert lab.result(analysis).method == "Descriptive statistics"
    assert lab.changes == 1


def test_it_reruns_when_its_data_changes_and_shows_stale_meanwhile(lab: Lab) -> None:
    analysis = lab.add()
    lab.states.clear()
    lab.doc.execute(SetCellValue("T", "a1", 0, 5))
    assert lab.engine.is_stale(analysis.id)
    assert analysis.id in lab.engine.results  # the old result stays visible
    lab.settle()
    assert not lab.engine.is_stale(analysis.id)
    assert lab.means(analysis) == [4, 3, 3]
    assert lab.states.count(analysis.id) >= 2  # stale, then the new result
    lab.doc.undo()
    lab.settle()
    assert lab.means(analysis) == [2, 3, 3]


def test_it_does_not_rerun_for_columns_it_does_not_read(lab: Lab) -> None:
    analysis = lab.add()
    lab.states.clear()
    lab.doc.execute(SetCellValue("T", "b", 0, 99))
    assert not lab.engine.is_stale(analysis.id)
    lab.settle()
    assert analysis.id not in lab.states


def test_a_renamed_series_column_reruns_it(lab: Lab) -> None:
    analysis = lab.add(("b",))
    assert lab.result(analysis).tables[0].title == "b"
    lab.doc.execute(RenameColumn("T", "b", "Drug"))
    lab.settle()
    assert lab.result(analysis).tables[0].title == "Drug"


def test_a_series_that_was_regrouped_says_what_to_do(lab: Lab) -> None:
    analysis = lab.add()
    lab.doc.execute(GroupColumns("T", ["a1", "a2", "b"], "A", group_id="gA2"))
    lab.settle()
    # The group gA is gone (its columns moved to gA2): the analysis says what to do.
    assert "Click Edit…" in lab.engine.errors[analysis.id]
    assert analysis.id not in lab.engine.results


def test_it_waits_for_a_calculated_column_it_reads(lab: Lab) -> None:
    lab.doc.execute(add_calculation(lab.doc, Calculation("T", "{A} * 10", Pairing(("b",)))))
    lab.settle()
    calc_column = lab.doc.calculations[0].columns[0].column_id
    analysis = lab.add((calc_column,))
    assert lab.means(analysis) == [100, 200, 300]
    lab.doc.execute(SetCellValue("T", "b", 0, 1))
    lab.settle()
    assert lab.means(analysis) == [10, 200, 300]


def test_editing_parameters_reruns_it(lab: Lab) -> None:
    analysis = lab.add()
    assert len(lab.result(analysis).tables[0].columns) == 7
    edited = Analysis(
        analysis.type,
        "T",
        ("gA", "b"),
        DescriptiveParams(confidence_interval=False),
        id=analysis.id,
    )
    lab.doc.execute(edit_analysis(lab.doc, edited))
    lab.settle()
    result = lab.result(analysis)
    assert len(result.tables) == 2
    assert len(result.tables[0].columns) == 5
    lab.doc.undo()
    lab.settle()
    assert len(lab.result(analysis).tables) == 1


def test_a_deleted_column_gives_an_error_that_says_what_to_do(lab: Lab) -> None:
    analysis = lab.add(("b",))
    lab.doc.execute(RemoveColumn("T", "b"))
    lab.settle()
    assert "Click Edit…" in lab.engine.errors[analysis.id]
    lab.doc.undo()
    lab.settle()
    assert lab.means(analysis) == [10, 20, 30]


def test_removing_an_analysis_forgets_it(lab: Lab) -> None:
    analysis = lab.add()
    lab.doc.execute(remove_analysis(lab.doc, analysis.id))
    lab.settle()
    assert analysis.id not in lab.engine.results
    assert analysis.id not in lab.calc.graph
    lab.doc.undo()
    lab.settle()
    assert lab.means(analysis) == [2, 3, 3]


def test_analyses_of_an_opened_document_run_at_once(scheduler: Scheduler) -> None:
    first = Lab(scheduler)
    analysis = first.add()
    first.engine.close()
    first.calc.close()
    calc = CalcEngine(first.doc, scheduler)
    engine = AnalysisEngine(first.doc, calc)
    first.calc, first.engine = calc, engine
    first.settle()
    assert first.means(analysis) == [2, 3, 3]
    engine.close()
    calc.close()
