"""Tests for the Recalculator: stale nodes from the dependency graph, run in order."""

import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import numpy as np
import pytest
from pytestqt.qtbot import QtBot

from curvis.compute import UPSTREAM_ERROR, Job, JobError, NodeJob, Recalculator, Scheduler
from curvis.core import (
    AddColumn,
    AddTable,
    DependencyGraph,
    Document,
    SetCellBlock,
    SetCellValue,
)
from curvis.data import Column, Table
from tests.unit import compute_jobs as jobs


class Harness:
    """A document with one table (columns x, y), a graph and a recording planner.

    Derived nodes compute ``f(inputs)`` where an input is either a column (its sum) or
    another derived node (its stored result). ``fns`` overrides f per node.
    """

    def __init__(self, scheduler: Scheduler) -> None:
        self.doc = Document()
        self.table = Table(
            "Data", [Column.new("x", "X", [1, 2, 3]), Column.new("y", "Y", [10, 0, 0])]
        )
        self.doc.execute(AddTable(self.table))
        self.x, self.y = (c.id for c in self.table.columns)
        self.graph = DependencyGraph()
        self.values: dict[str, Any] = {}
        self.planned: list[str] = []
        self.finished: list[str] = []
        self.failed: dict[str, JobError] = {}
        self.stale: list[list[str]] = []
        self.fns: dict[str, Callable[..., Any]] = {}
        self.weights: dict[str, str] = {}
        self.recalc = Recalculator(self.doc, self.graph, scheduler, self.plan)
        self.recalc.node_finished.connect(self._store)
        self.recalc.node_failed.connect(lambda n, e: self.failed.__setitem__(n, e))
        self.recalc.stale.connect(self.stale.append)
        self.scheduler = scheduler

    def _input(self, node: str) -> Any:
        if node in (self.x, self.y):
            return float(np.nansum(self.table.column(node).values))
        return self.values[node]

    def plan(self, node: str) -> NodeJob | None:
        self.planned.append(node)
        inputs = [self._input(r) for r in self.graph.reads(node)]  # snapshot now
        fn = self.fns.get(node, lambda *a: sum(a))
        if self.weights.get(node) == "heavy":
            return NodeJob(Job(fn, tuple(inputs)), "heavy")
        return NodeJob(lambda: fn(*inputs))

    def _store(self, node: str, result: Any) -> None:
        self.values[node] = result
        self.finished.append(node)

    def set_x(self, row: int, value: float) -> None:
        self.doc.execute(SetCellValue(self.table.id, self.x, row, value))

    def settle(self, timeout: float = 10) -> None:
        deadline = time.monotonic() + timeout
        while self.recalc.busy or self.scheduler.busy:
            assert time.monotonic() < deadline, "recompute did not settle"
            self.scheduler.wait_idle(50)
        self.scheduler.wait_idle()


@pytest.fixture
def scheduler(qtbot: QtBot) -> Iterator[Scheduler]:
    s = Scheduler(debounce_ms=20, max_processes=1)
    yield s
    s.shutdown()


@pytest.fixture
def h(scheduler: Scheduler) -> Iterator[Harness]:
    harness = Harness(scheduler)
    yield harness
    harness.recalc.close()


def test_chain_runs_in_dependency_order(h: Harness) -> None:
    h.graph.set_reads("C", ["B"])
    h.graph.set_reads("B", ["A"])
    h.graph.set_reads("A", [h.x])
    h.set_x(0, 100)
    assert h.stale == [["A", "B", "C"]]
    assert h.recalc.is_stale("C")
    h.settle()
    assert h.finished == ["A", "B", "C"]
    assert h.planned == ["A", "B", "C"]
    assert h.values == {"A": 105, "B": 105, "C": 105}
    assert not h.recalc.is_stale("C")


def test_diamond_runs_independent_nodes_in_parallel(h: Harness) -> None:
    barrier = threading.Barrier(2, timeout=5)

    def meet(v: float) -> float:
        barrier.wait()  # breaks (and fails the job) unless A and B run at the same time
        return v

    h.graph.set_reads("A", [h.x])
    h.graph.set_reads("B", [h.y])
    h.graph.set_reads("C", ["A", "B"])
    h.fns.update(A=meet, B=meet)
    h.doc.execute(SetCellBlock(h.table.id, 0, 0, [[1, 1]]))  # touches x and y
    h.settle()
    assert h.failed == {}
    assert set(h.finished[:2]) == {"A", "B"} and h.finished[2] == "C"
    assert h.values["C"] == (1 + 2 + 3) + 1


def test_nodes_that_did_not_change_are_not_recomputed(h: Harness) -> None:
    h.graph.set_reads("A", [h.x])
    h.graph.set_reads("B", [h.y])
    h.set_x(0, 5)
    h.settle()
    assert h.finished == ["A"]


def test_edits_are_debounced(h: Harness) -> None:
    h.graph.set_reads("A", [h.x])
    for value in range(5):
        h.set_x(0, value)
    h.settle()
    assert h.finished == ["A"]
    assert h.values["A"] == 4 + 2 + 3


def test_upstream_failure_reaches_downstream_nodes(h: Harness) -> None:
    h.graph.set_reads("A", [h.x])
    h.graph.set_reads("B", ["A"])
    h.graph.set_reads("C", ["B"])
    h.graph.set_reads("D", [h.x])  # independent of A
    h.fns["A"] = lambda v: jobs.boom("A broke")
    h.set_x(0, 1)
    h.settle()
    assert h.failed["A"].message == "A broke"
    assert h.failed["B"].error_type == UPSTREAM_ERROR
    assert h.failed["C"].error_type == UPSTREAM_ERROR
    assert "A broke" in h.failed["C"].message
    assert set(h.recalc.errors) == {"A", "B", "C"}
    assert h.finished == ["D"]
    assert not h.recalc.busy

    del h.fns["A"]  # fixed: the next edit clears the errors
    h.set_x(0, 2)
    h.settle()
    assert h.recalc.errors == {}
    assert sorted(h.finished[1:]) == ["A", "B", "C", "D"]


def test_undo_and_redo_reschedule(h: Harness) -> None:
    h.graph.set_reads("A", [h.x])
    h.set_x(0, 10)
    h.settle()
    assert h.values["A"] == 15
    h.doc.undo()
    h.settle()
    assert h.values["A"] == 6
    h.doc.redo()
    h.settle()
    assert h.values["A"] == 15
    assert h.finished == ["A", "A", "A"]


def test_edit_during_a_batch_restarts_it_with_new_inputs(h: Harness) -> None:
    h.graph.set_reads("A", [h.x])
    h.graph.set_reads("B", ["A"])
    h.fns["A"] = lambda v: jobs.sleep_then(0.3, v)
    h.set_x(0, 100)
    h.scheduler.wait_idle(100)  # A is running now
    assert h.scheduler.is_busy("A")
    h.set_x(0, 200)
    h.settle()
    assert h.finished == ["A", "B"]  # the first run of A was thrown away; B ran once
    assert h.values == {"A": 205, "B": 205}


def test_node_without_a_job_lets_its_readers_run(h: Harness) -> None:
    h.graph.set_reads("A", [h.x])
    h.graph.set_reads("B", ["A"])
    h.values["A"] = "kept"
    original = h.plan
    h.recalc.plan = lambda n: None if n == "A" else original(n)
    h.fns["B"] = lambda a: f"from {a}"
    h.set_x(0, 1)
    h.settle()
    assert h.finished == ["B"]
    assert h.values["B"] == "from kept"


def test_a_failing_planner_fails_only_its_node(h: Harness) -> None:
    h.graph.set_reads("A", [h.x])
    h.graph.set_reads("B", [h.x])
    original = h.plan

    def plan(node: str) -> NodeJob | None:
        if node == "A":
            raise KeyError("no such column")
        return original(node)

    h.recalc.plan = plan
    h.set_x(0, 1)
    h.settle()
    assert h.failed["A"].error_type == "KeyError"
    assert h.finished == ["B"]


def test_two_documents_share_a_scheduler(scheduler: Scheduler) -> None:
    one, two = Harness(scheduler), Harness(scheduler)
    try:
        one.graph.set_reads("A1", [one.x])
        two.graph.set_reads("A2", [two.x])
        one.set_x(0, 10)
        two.set_x(0, 20)
        one.settle()
        two.settle()
        assert one.finished == ["A1"] and two.finished == ["A2"]
        assert (one.values["A1"], two.values["A2"]) == (15, 25)
    finally:
        one.recalc.close()
        two.recalc.close()


def test_recompute_includes_the_node_itself(h: Harness) -> None:
    h.graph.set_reads("A", [h.x])
    h.graph.set_reads("B", ["A"])
    h.recalc.recompute(["A"])
    h.settle()
    assert h.finished == ["A", "B"]


def test_heavy_node_runs_in_a_process(h: Harness) -> None:
    h.graph.set_reads("A", [h.x, h.y])
    h.graph.set_reads("B", ["A"])
    h.fns["A"] = jobs.add
    h.weights["A"] = "heavy"
    h.set_x(0, 1)
    h.settle(timeout=30)
    assert h.failed == {}
    assert h.values == {"A": 6 + 10, "B": 16}


def test_settled_signal(qtbot: QtBot, h: Harness) -> None:
    h.graph.set_reads("A", [h.x])
    with qtbot.waitSignal(h.recalc.settled, timeout=5000):
        h.set_x(0, 1)


def test_close_stops_watching(h: Harness) -> None:
    h.graph.set_reads("A", [h.x])
    h.recalc.close()
    h.set_x(0, 1)
    h.settle()
    assert h.finished == []
    h.recalc = Recalculator(h.doc, h.graph, h.scheduler, h.plan)  # for the fixture's close


def test_new_columns_and_tables_mark_readers_stale(h: Harness) -> None:
    h.graph.set_reads("T", [h.table.id])
    h.doc.execute(AddColumn(h.table.id, Column.new("z", "Y", [1, 2, 3, 4, 5])))
    assert h.stale[-1] == ["T"]
    other = Table("Other", [Column.new("a", "Y", [1])])
    h.graph.set_reads("O", [other.columns[0].id])
    h.doc.execute(AddTable(other))
    assert h.stale[-1] == ["O"]
