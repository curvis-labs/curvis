"""Tests for the job scheduler: threads, processes, debounce, cancellation and errors."""

import os
import pickle
import threading
import time
from collections.abc import Iterator
from multiprocessing import shared_memory
from typing import Any

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from curvis.compute import Job, JobError, Scheduler, check_cancelled, is_cancelled
from curvis.compute import shared as shared_mod
from tests.unit import compute_jobs as jobs


class Recorder:
    """Collects everything a scheduler emits, with the thread each signal arrived on."""

    def __init__(self, scheduler: Scheduler) -> None:
        self.started: list[str] = []
        self.finished: list[tuple[str, Any]] = []
        self.failed: list[tuple[str, JobError]] = []
        self.cancelled: list[str] = []
        self.threads: set[int] = set()
        scheduler.started.connect(self.started.append)
        scheduler.finished.connect(self._on_finished)
        scheduler.failed.connect(lambda n, e: self.failed.append((n, e)))
        scheduler.cancelled.connect(self.cancelled.append)

    def _on_finished(self, node: str, result: Any) -> None:
        self.threads.add(threading.get_ident())
        self.finished.append((node, result))

    @property
    def results(self) -> list[Any]:
        return [r for _, r in self.finished]


@pytest.fixture
def scheduler(qtbot: QtBot) -> Iterator[Scheduler]:
    s = Scheduler(max_processes=2)
    yield s
    s.shutdown()


@pytest.fixture(scope="module")
def heavy(qapp: QApplication) -> Iterator[Scheduler]:
    """One scheduler with a warm process pool for all heavy-job tests (spawn is slow)."""
    s = Scheduler(max_processes=2)
    s.start_process_pool()
    yield s
    s.shutdown()


@pytest.fixture
def rec(scheduler: Scheduler) -> Recorder:
    return Recorder(scheduler)


@pytest.fixture
def heavy_rec(heavy: Scheduler) -> Iterator[Recorder]:
    recorder = Recorder(heavy)
    yield recorder
    assert heavy.wait_idle()
    for signal in (heavy.started, heavy.finished, heavy.failed, heavy.cancelled):
        signal.disconnect()


# --- light jobs ---------------------------------------------------------------------


def test_light_job_runs_off_the_ui_thread_and_reports_on_it(
    qtbot: QtBot, scheduler: Scheduler, rec: Recorder
) -> None:
    main = threading.get_ident()
    scheduler.submit("n", jobs.where, delay_ms=0)
    qtbot.waitUntil(lambda: bool(rec.finished))
    (_, (pid, worker_thread)) = rec.finished[0]
    assert pid == os.getpid()
    assert worker_thread != main
    assert rec.threads == {main}
    assert rec.started == ["n"]
    assert not scheduler.busy


def test_job_object_and_plain_callable(qtbot: QtBot, scheduler: Scheduler, rec: Recorder) -> None:
    scheduler.submit("a", Job(jobs.add, (1, 2)), delay_ms=0)
    scheduler.submit("b", lambda: "closure", delay_ms=0)
    qtbot.waitUntil(lambda: len(rec.finished) == 2)
    assert dict(rec.finished) == {"a": 3, "b": "closure"}


def test_debounce_runs_only_the_last_of_rapid_submits(
    qtbot: QtBot, scheduler: Scheduler, rec: Recorder
) -> None:
    t0 = time.monotonic()
    for i in range(5):
        scheduler.submit("n", Job(jobs.add, (i, 0)))
    assert scheduler.is_busy("n")
    assert rec.started == []  # nothing starts before the debounce delay
    qtbot.waitUntil(lambda: bool(rec.finished))
    assert time.monotonic() - t0 >= 0.15
    assert rec.results == [4]
    assert rec.started == ["n"]


def test_debounce_restarts_on_each_submit(
    qtbot: QtBot, scheduler: Scheduler, rec: Recorder
) -> None:
    scheduler.submit("n", Job(jobs.add, (1, 0)), delay_ms=300)
    qtbot.wait(200)
    scheduler.submit("n", Job(jobs.add, (2, 0)), delay_ms=300)
    qtbot.wait(200)
    assert rec.started == []  # 400 ms after the first submit, but only 200 after the last
    qtbot.waitUntil(lambda: bool(rec.finished))
    assert rec.results == [2]


def test_custom_debounce(qtbot: QtBot) -> None:
    s = Scheduler(debounce_ms=400)
    try:
        r = Recorder(s)
        t0 = time.monotonic()
        s.submit("n", Job(jobs.add, (1, 1)))
        qtbot.wait(200)
        assert r.started == []
        qtbot.waitUntil(lambda: bool(r.finished), timeout=2000)
        assert time.monotonic() - t0 >= 0.4
        s.debounce_ms = 0
        s.submit("n", Job(jobs.add, (2, 2)))
        assert r.started == ["n", "n"]  # started at once
    finally:
        s.shutdown()


def test_debounce_is_per_node(qtbot: QtBot, scheduler: Scheduler, rec: Recorder) -> None:
    scheduler.submit("a", Job(jobs.add, (1, 0)))
    scheduler.submit("b", Job(jobs.add, (2, 0)))
    qtbot.waitUntil(lambda: len(rec.finished) == 2)
    assert dict(rec.finished) == {"a": 1, "b": 2}


def test_newer_job_supersedes_a_running_one(
    qtbot: QtBot, scheduler: Scheduler, rec: Recorder
) -> None:
    scheduler.submit("n", Job(jobs.sleep_then, (0.3, "old")), delay_ms=0)
    qtbot.wait(50)
    scheduler.submit("n", Job(jobs.sleep_then, (0.0, "new")), delay_ms=0)
    assert scheduler.wait_idle()
    qtbot.wait(50)
    assert rec.results == ["new"]
    assert rec.failed == [] and rec.cancelled == []


def test_queued_job_is_cancelled_before_it_starts(qtbot: QtBot) -> None:
    s = Scheduler(max_threads=1)
    ran: list[str] = []
    try:
        r = Recorder(s)
        s.submit("blocker", Job(jobs.sleep_then, (0.2, "blocker")), delay_ms=0)
        s.submit("n", lambda: ran.append("old"), delay_ms=0)  # waits for the only thread
        s.submit("n", lambda: ran.append("new") or "new", delay_ms=0)
        assert s.wait_idle()
        assert ran == ["new"]
        assert dict(r.finished) == {"blocker": "blocker", "n": "new"}
    finally:
        s.shutdown()


def test_check_cancelled_stops_a_superseded_thread_job(
    qtbot: QtBot, scheduler: Scheduler, rec: Recorder
) -> None:
    stopped = threading.Event()

    def long_job() -> str:
        try:
            jobs.loop_until_cancelled(5)
        finally:
            stopped.set()
        return "finished"

    scheduler.submit("n", long_job, delay_ms=0)
    qtbot.wait(50)
    scheduler.submit("n", lambda: "new", delay_ms=0)
    assert stopped.wait(1), "the superseded job did not stop"
    qtbot.waitUntil(lambda: bool(rec.finished))
    assert rec.results == ["new"]


def test_cancel_drops_pending_and_running_jobs(
    qtbot: QtBot, scheduler: Scheduler, rec: Recorder
) -> None:
    scheduler.submit("pending", Job(jobs.add, (1, 1)), delay_ms=5000)
    scheduler.submit("running", Job(jobs.loop_until_cancelled, (5,)), delay_ms=0)
    qtbot.wait(20)
    scheduler.cancel("pending")
    scheduler.cancel("running")
    scheduler.cancel("unknown")  # no-op
    assert scheduler.wait_idle(2000)
    qtbot.wait(100)
    assert rec.cancelled == ["pending", "running"]
    assert rec.finished == [] and rec.failed == []


def test_job_that_cancels_itself_reports_cancelled(
    qtbot: QtBot, scheduler: Scheduler, rec: Recorder
) -> None:
    from curvis.compute import Cancelled

    def job() -> None:
        raise Cancelled

    scheduler.submit("n", job, delay_ms=0)
    qtbot.waitUntil(lambda: bool(rec.cancelled))
    assert rec.failed == []


def test_check_cancelled_outside_a_job_does_nothing() -> None:
    check_cancelled()
    assert not is_cancelled()


def test_light_job_error_is_reported(qtbot: QtBot, scheduler: Scheduler, rec: Recorder) -> None:
    scheduler.submit("n", Job(jobs.boom, ("bad input",)), delay_ms=0)
    qtbot.waitUntil(lambda: bool(rec.failed))
    node, error = rec.failed[0]
    assert node == "n" and error.node_id == "n"
    assert error.error_type == "ValueError"
    assert error.message == "bad input"
    assert "boom" in error.traceback
    scheduler.submit("n", Job(jobs.add, (1, 1)), delay_ms=0)  # still works
    qtbot.waitUntil(lambda: bool(rec.finished))
    assert rec.results == [2]


def test_invalid_submit_arguments(scheduler: Scheduler) -> None:
    with pytest.raises(ValueError, match="weight"):
        scheduler.submit("n", lambda: 1, weight="medium")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="heavy job"):
        scheduler.submit("n", lambda: 1, weight="heavy")
    with pytest.raises(ValueError, match="negative"):
        Scheduler(debounce_ms=-1)


def test_shutdown(qtbot: QtBot) -> None:
    s = Scheduler()
    r = Recorder(s)
    s.submit("n", Job(jobs.add, (1, 1)))
    s.shutdown()
    s.shutdown()  # idempotent
    qtbot.wait(250)
    assert r.started == []
    with pytest.raises(RuntimeError, match="shut down"):
        s.submit("n", lambda: 1)


# --- heavy jobs ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "arr",
    [
        np.arange(100_000, dtype=np.float64),
        np.arange(12, dtype=np.int32).reshape(3, 4),
        np.arange(20, dtype=np.float32)[::3],  # not contiguous
        np.array([True, False, True]),
        np.array(3.5),  # 0-d
        np.array([], dtype=np.float64),
        np.array([1.0, np.nan, 3.0]),
    ],
    ids=["float64", "int32-2d", "strided", "bool", "0d", "empty", "nan"],
)
def test_heavy_job_gets_arrays_intact_in_another_process(
    qtbot: QtBot, heavy: Scheduler, heavy_rec: Recorder, arr: np.ndarray[Any, Any]
) -> None:
    heavy.submit("n", Job(jobs.describe, (arr,)), weight="heavy", delay_ms=0)
    qtbot.waitUntil(lambda: bool(heavy_rec.finished or heavy_rec.failed), timeout=20_000)
    assert heavy_rec.failed == []
    info = heavy_rec.results[0]
    assert info["pid"] != os.getpid()
    assert info["shape"] == arr.shape and info["dtype"] == arr.dtype
    assert info["writeable"] is False  # the worker sees a read-only view
    np.testing.assert_array_equal(info["copy"], arr)


def test_heavy_job_with_nested_arguments_and_kwargs(
    qtbot: QtBot, heavy: Scheduler, heavy_rec: Recorder
) -> None:
    data = {"a": np.ones(10), "more": [np.full(5, 2.0), (np.arange(4.0))]}
    heavy.submit("n", Job(jobs.nested_sum, (data,), {"scale": 2.0}), "heavy", delay_ms=0)
    qtbot.waitUntil(lambda: bool(heavy_rec.finished), timeout=20_000)
    assert heavy_rec.results == [2.0 * (10 + 10 + 6)]


def test_heavy_job_can_return_its_shared_input(
    qtbot: QtBot, heavy: Scheduler, heavy_rec: Recorder
) -> None:
    arr = np.linspace(0, 1, 1000)
    for _ in range(3):  # later jobs close the blocks the earlier ones had to keep open
        heavy.submit("n", Job(jobs.echo, (arr,)), "heavy", delay_ms=0)
        assert heavy.wait_idle(20_000)
    assert heavy_rec.failed == []
    assert len(heavy_rec.results) == 3
    for result in heavy_rec.results:
        np.testing.assert_array_equal(result, arr)


def test_arrays_are_not_pickled() -> None:
    arr = np.random.default_rng(0).normal(size=1_000_000)  # 8 MB
    blocks: list[shared_memory.SharedMemory] = []
    try:
        packed = shared_mod.pack(((arr, "text", np.array(["a", None], dtype=object)), {}), blocks)
        assert len(blocks) == 1  # object arrays stay out of shared memory
        assert len(pickle.dumps(packed)) < 2_000
    finally:
        shared_mod.release(blocks)


def test_shared_blocks_are_freed_after_success_and_failure(
    qtbot: QtBot, heavy: Scheduler, heavy_rec: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[str] = []
    original = shared_mod.pack

    def spy(obj: Any, blocks: list[shared_memory.SharedMemory]) -> Any:
        result = original(obj, blocks)
        created.extend(b.name for b in blocks)
        return result

    monkeypatch.setattr(shared_mod, "pack", spy)
    heavy.submit("ok", Job(jobs.column_mean, (np.ones(1000),)), "heavy", delay_ms=0)
    heavy.submit("bad", Job(jobs.boom, ()), "heavy", delay_ms=0)
    assert heavy.wait_idle(20_000)
    assert len(heavy_rec.finished) == 1 and len(heavy_rec.failed) == 1
    assert created
    for name in created:
        with pytest.raises(FileNotFoundError):
            shared_memory.SharedMemory(name=name)


def test_heavy_job_error_is_reported(qtbot: QtBot, heavy: Scheduler, heavy_rec: Recorder) -> None:
    heavy.submit("n", Job(jobs.boom, ("from a process",)), "heavy", delay_ms=0)
    qtbot.waitUntil(lambda: bool(heavy_rec.failed), timeout=20_000)
    _, error = heavy_rec.failed[0]
    assert (error.error_type, error.message) == ("ValueError", "from a process")
    assert "boom" in error.traceback


def test_unpicklable_exception_is_reported(
    qtbot: QtBot, heavy: Scheduler, heavy_rec: Recorder
) -> None:
    heavy.submit("n", Job(jobs.raise_unpicklable), "heavy", delay_ms=0)
    qtbot.waitUntil(lambda: bool(heavy_rec.failed), timeout=20_000)
    _, error = heavy_rec.failed[0]
    assert (error.error_type, error.message) == ("UnpicklableError", "bad error")


def test_unpicklable_job_is_reported(qtbot: QtBot, heavy: Scheduler, heavy_rec: Recorder) -> None:
    lock = threading.Lock()
    heavy.submit("n", Job(jobs.add, (lock, 1)), "heavy", delay_ms=0)
    qtbot.waitUntil(lambda: bool(heavy_rec.failed), timeout=20_000)
    _, error = heavy_rec.failed[0]
    assert "pickle" in error.message.lower()
    heavy.submit("n", Job(jobs.add, (1, 1)), "heavy", delay_ms=0)  # the pool still works
    qtbot.waitUntil(lambda: bool(heavy_rec.finished), timeout=20_000)


def test_check_cancelled_stops_a_superseded_process_job(
    qtbot: QtBot, heavy: Scheduler, heavy_rec: Recorder
) -> None:
    heavy.submit("n", Job(jobs.loop_until_cancelled, (10,)), "heavy", delay_ms=0)
    qtbot.wait(300)
    t0 = time.monotonic()
    heavy.submit("n", Job(jobs.add, (1, 2)), "heavy", delay_ms=0)
    qtbot.waitUntil(lambda: bool(heavy_rec.finished), timeout=20_000)
    assert heavy.wait_idle(5000)
    assert time.monotonic() - t0 < 5  # the old job did not run its full 10 s
    assert heavy_rec.results == [3]


def test_worker_crash_is_reported_and_the_pool_recovers(qtbot: QtBot) -> None:
    s = Scheduler(max_processes=1)
    try:
        r = Recorder(s)
        s.submit("n", Job(jobs.crash), "heavy", delay_ms=0)
        qtbot.waitUntil(lambda: bool(r.failed), timeout=20_000)
        _, error = r.failed[0]
        assert error.error_type == "BrokenProcessPool"
        assert error.message == "The worker process stopped unexpectedly."
        s.submit("n", Job(jobs.add, (2, 3)), "heavy", delay_ms=0)
        qtbot.waitUntil(lambda: bool(r.finished), timeout=20_000)
        assert r.results == [5]
    finally:
        s.shutdown()


def test_light_and_heavy_jobs_for_the_same_node_supersede_each_other(
    qtbot: QtBot, heavy: Scheduler, heavy_rec: Recorder
) -> None:
    heavy.submit("n", Job(jobs.sleep_then, (0.5, "heavy")), "heavy", delay_ms=0)
    heavy.submit("n", Job(jobs.sleep_then, (0.0, "light")), "light", delay_ms=0)
    assert heavy.wait_idle(20_000)
    qtbot.wait(50)
    assert heavy_rec.results == ["light"]
