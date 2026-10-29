"""Benchmarks for the job scheduler and dependency-ordered recompute."""

from collections.abc import Iterator
from typing import Any

import numpy as np
import pytest
from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QApplication

from curvis.compute import Job, NodeJob, Recalculator, Scheduler
from curvis.core import AddTable, DependencyGraph, Document, SetCellValue
from curvis.data import Column, Table
from tests.unit import compute_jobs as jobs


@pytest.fixture(scope="module")
def scheduler(qapp: QApplication) -> Iterator[Scheduler]:
    """One worker thread and one process: the timings then measure the scheduler's own
    overhead. With a pool of threads, which idle thread the OS wakes changes a round
    trip between ~30 and ~130 µs from run to run, too noisy for a 15% gate.
    (Parallel execution is covered by the unit tests.)
    """
    s = Scheduler(debounce_ms=0, max_threads=1, max_processes=1)
    s.start_process_pool()
    yield s
    s.shutdown()


class Results:
    """Collects ``finished`` results; :meth:`wait` blocks in a Qt event loop like the app.

    (A busy ``processEvents`` loop would compete with the workers for the GIL and
    make the timings depend on luck.)
    """

    def __init__(self, signal: Any) -> None:
        self.values: list[Any] = []
        self._target = 0
        self._loop = QEventLoop()
        self._signal = signal
        signal.connect(self._on_result)

    def _on_result(self, *args: Any) -> None:
        self.values.append(args[-1] if args else None)
        if len(self.values) >= self._target:
            self._loop.quit()

    def wait(self, count: int) -> None:
        self._target = count
        if len(self.values) < count:
            self._loop.exec()

    def close(self) -> None:
        self._signal.disconnect(self._on_result)


def test_100_light_job_round_trips(benchmark: Any, scheduler: Scheduler) -> None:
    """Submit → result signal on the main thread, 100 times one after the other.

    One round trip is ~50 µs and depends on thread wake-up luck; 100 average that out.
    """
    results = Results(scheduler.finished)

    def round_trips() -> None:
        results.values.clear()
        for i in range(100):
            scheduler.submit("light", Job(jobs.add, (i, 0)), delay_ms=0)
            results.wait(i + 1)

    benchmark(round_trips)
    results.close()
    assert results.values == list(range(100))


def test_heavy_job_round_trip_8mb(benchmark: Any, scheduler: Scheduler) -> None:
    """An 8 MB array to a worker process through shared memory, and a float back."""
    arr = np.random.default_rng(0).normal(size=1_000_000)
    results = Results(scheduler.finished)

    def round_trip() -> None:
        results.values.clear()
        scheduler.submit("heavy", Job(jobs.column_mean, (arr,)), "heavy", delay_ms=0)
        results.wait(1)

    for _ in range(4):  # let every worker import the job module first
        round_trip()
    benchmark(round_trip)
    results.close()
    assert results.values == [pytest.approx(float(arr.mean()))]


def test_1000_debounced_submits(benchmark: Any, scheduler: Scheduler) -> None:
    """Main-thread cost of edits arriving faster than the debounce delay.

    (Immediate supersedes are not timed: whether ``future.cancel()`` wins the race
    against the worker thread changes the time by a third from run to run.)
    """
    started: list[str] = []
    scheduler.started.connect(started.append)

    def burst() -> None:
        for i in range(1000):
            scheduler.submit("burst", Job(jobs.add, (i, 0)), delay_ms=60_000)
        scheduler.cancel("burst")

    benchmark(burst)
    scheduler.started.disconnect(started.append)
    assert started == []


def test_recalc_50_nodes(benchmark: Any, scheduler: Scheduler) -> None:
    """One cell edit that makes 50 nodes in three levels stale (10 → 20 → 20)."""
    table = Table("Data", [Column.new(f"c{i}", "Y", np.arange(100.0)) for i in range(10)])
    doc = Document()
    doc.execute(AddTable(table))
    col_ids = [c.id for c in table.columns]
    graph = DependencyGraph()
    for i in range(10):
        graph.set_reads(f"a{i}", [col_ids[0], col_ids[i]])
    for i in range(20):
        graph.set_reads(f"b{i}", [f"a{i % 10}", f"a{(i + 1) % 10}"])
    for i in range(20):
        graph.set_reads(f"c{i}", [f"b{i}", f"b{(i + 7) % 20}"])
    values: dict[str, float] = {}

    def plan(node: str) -> NodeJob:
        inputs = [values.get(r, 1.0) for r in graph.reads(node)]
        return NodeJob(lambda: sum(inputs))

    recalc = Recalculator(doc, graph, scheduler, plan)
    recalc.node_finished.connect(values.__setitem__)
    settled = Results(recalc.settled)
    row = [0]

    def edit() -> None:
        settled.values.clear()
        row[0] = (row[0] + 1) % 100
        doc.execute(SetCellValue(table.id, col_ids[0], row[0], 1.0))
        settled.wait(1)

    benchmark(edit)
    settled.close()
    recalc.close()
    assert len(values) == 50
