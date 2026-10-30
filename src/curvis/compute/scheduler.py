"""Job scheduler: debounced, cancellable jobs on threads or processes, results via Qt signals.

Use one :class:`Scheduler` per application, from the Qt main thread. Each job belongs to
a node (a calculated column, analysis or graph id). Per node there is at most one
pending job (waiting out the debounce delay) and one live run; a newer submit for the
node replaces the pending job and cancels the older run.

Results and errors are delivered on the main thread through the ``finished`` and
``failed`` signals. A job that raises, cannot be pickled, or kills its worker process
reports a :class:`~curvis.compute.jobs.JobError`; it never takes the app down.
"""

from __future__ import annotations

import contextlib
import functools
import importlib
import multiprocessing
import os
import time
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from multiprocessing import shared_memory
from typing import Any

from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, Qt, QTimer, Signal

from curvis.compute import shared
from curvis.compute.jobs import (
    WEIGHTS,
    CancelFlag,
    Cancelled,
    Job,
    JobError,
    JobLike,
    Weight,
    set_current_flag,
)

DEBOUNCE_MS = 150
"""Default delay between the last submit for a node and the start of its job."""


@dataclass(eq=False)
class _Run:
    """One started job. Identity matters: a node's current run is compared by ``is``."""

    node_id: str
    weight: Weight
    flag: CancelFlag | shared.SharedCancelFlag
    future: Future[Any] = field(default_factory=Future)
    blocks: list[shared_memory.SharedMemory] = field(default_factory=list)
    pool: ProcessPoolExecutor | None = None


def _run_light(job: JobLike, flag: CancelFlag) -> Any:
    set_current_flag(flag)
    try:
        return job()
    finally:
        set_current_flag(None)


def _import_on_main_thread(job: JobLike) -> None:
    """Import the modules a light job needs before a worker thread runs it."""
    if not isinstance(job, Job):
        return
    for name in job.imports:
        # A module that fails to import is left to the job, which then reports the error.
        with contextlib.suppress(Exception):
            importlib.import_module(name)


def _default_processes() -> int:
    return max(1, (os.cpu_count() or 2) - 1)


class Scheduler(QObject):
    """Runs node jobs off the UI thread. Create it on, and call it from, the main thread."""

    started = Signal(str)
    """A node's job has been handed to a worker."""
    finished = Signal(str, object)
    """``(node_id, result)`` of the node's latest job."""
    failed = Signal(str, object)
    """``(node_id, JobError)`` when the node's latest job failed."""
    cancelled = Signal(str)
    """The node's latest job was cancelled with :meth:`cancel` or stopped itself."""

    _done = Signal(object)  # emitted from worker threads; delivered queued to _on_done

    def __init__(
        self,
        debounce_ms: int = DEBOUNCE_MS,
        max_threads: int | None = None,
        max_processes: int | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if debounce_ms < 0:
            raise ValueError("debounce_ms cannot be negative")
        self.debounce_ms = debounce_ms
        self._max_processes = max_processes or _default_processes()
        self._threads = ThreadPoolExecutor(max_threads, thread_name_prefix="curvis-light")
        self._processes: ProcessPoolExecutor | None = None
        self._pending: dict[str, tuple[JobLike, Weight]] = {}
        self._timers: dict[str, QTimer] = {}
        self._runs: dict[str, _Run] = {}  # the current run of each node
        self._live: set[_Run] = set()  # every run whose future is not done yet
        self._closed = False
        self._done.connect(self._on_done, Qt.ConnectionType.QueuedConnection)

    # --- public API ----------------------------------------------------------------

    def submit(
        self,
        node_id: str,
        job: JobLike,
        weight: Weight = "light",
        delay_ms: int | None = None,
    ) -> None:
        """Schedule ``job`` for ``node_id``, replacing any older job for that node.

        ``delay_ms`` is the debounce delay: ``None`` uses :attr:`debounce_ms`, 0 starts
        the job at once. Heavy jobs must be a :class:`Job` with a module-level function.
        """
        if self._closed:
            raise RuntimeError("The scheduler has been shut down.")
        if weight not in WEIGHTS:
            raise ValueError(f"weight must be 'light' or 'heavy', not {weight!r}")
        if weight == "heavy" and not isinstance(job, Job):
            raise TypeError("A heavy job must be a Job(fn, args, kwargs).")
        self._supersede(node_id)
        delay = self.debounce_ms if delay_ms is None else delay_ms
        if delay <= 0:
            self._start(node_id, job, weight)
            return
        self._pending[node_id] = (job, weight)
        timer = self._timers.get(node_id)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setTimerType(Qt.TimerType.PreciseTimer)  # coarse timers may fire 5% early
            timer.timeout.connect(functools.partial(self._fire, node_id))
            self._timers[node_id] = timer
        timer.start(delay)

    def cancel(self, node_id: str) -> None:
        """Drop the node's pending job and cancel its running one (emits ``cancelled``)."""
        if self.is_busy(node_id):
            self._supersede(node_id)
            self.cancelled.emit(node_id)

    def is_busy(self, node_id: str) -> bool:
        """True while the node has a pending or running job."""
        return node_id in self._pending or node_id in self._runs

    @property
    def busy(self) -> bool:
        """True while any node has a pending or running job."""
        return bool(self._pending or self._runs)

    def start_process_pool(self) -> None:
        """Start the worker processes now rather than on the first heavy job."""
        pool = self._process_pool()
        for _ in range(self._max_processes):
            pool.submit(int)  # spawning happens on demand; this forces it

    def wait_idle(self, timeout_ms: int = 10_000) -> bool:
        """Process events until no job is pending or running. For tests and benchmarks."""
        deadline = time.monotonic() + timeout_ms / 1000
        while self.busy or self._live:
            if time.monotonic() > deadline:
                return False
            QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 5)
            time.sleep(0.0005)
        return True

    def shutdown(self, wait: bool = True) -> None:
        """Cancel everything and stop the worker pools. The scheduler cannot be reused."""
        if self._closed:
            return
        self._closed = True
        for timer in self._timers.values():
            timer.stop()
        self._timers.clear()
        self._pending.clear()
        for run in list(self._runs.values()):
            self._stop(run)
        self._runs.clear()
        self._threads.shutdown(wait=wait, cancel_futures=True)
        if self._processes is not None:
            self._processes.shutdown(wait=wait, cancel_futures=True)
            self._processes = None
        for run in self._live:
            shared.release(run.blocks)
        self._live.clear()

    # --- internals -----------------------------------------------------------------

    def _supersede(self, node_id: str) -> None:
        timer = self._timers.get(node_id)
        if timer is not None:
            timer.stop()
        self._pending.pop(node_id, None)
        run = self._runs.pop(node_id, None)
        if run is not None:
            self._stop(run)

    @staticmethod
    def _stop(run: _Run) -> None:
        run.flag.set()
        run.future.cancel()  # only succeeds if it has not started yet

    def _fire(self, node_id: str) -> None:
        timer = self._timers.pop(node_id, None)
        if timer is not None:
            timer.deleteLater()
        pending = self._pending.pop(node_id, None)
        if pending is not None:
            self._start(node_id, *pending)

    def _process_pool(self) -> ProcessPoolExecutor:
        if self._processes is None:
            # spawn everywhere: forking a process that runs Qt threads is unsafe.
            self._processes = ProcessPoolExecutor(
                self._max_processes, mp_context=multiprocessing.get_context("spawn")
            )
        return self._processes

    def _reset_process_pool(self, broken: ProcessPoolExecutor | None) -> None:
        """Replace the pool if ``broken`` is still the current one."""
        if broken is not None and broken is self._processes:
            self._processes.shutdown(wait=False, cancel_futures=True)
            self._processes = None

    def _start(self, node_id: str, job: JobLike, weight: Weight) -> None:
        if weight == "light":
            _import_on_main_thread(job)
            flag = CancelFlag()
            run = _Run(node_id, weight, flag)
            run.future = self._threads.submit(_run_light, job, flag)
        else:
            run = self._start_heavy(node_id, job)
        self._runs[node_id] = run
        self._live.add(run)
        self.started.emit(node_id)
        run.future.add_done_callback(lambda _future: self._notify_done(run))

    def _start_heavy(self, node_id: str, job: JobLike) -> _Run:
        assert isinstance(job, Job)
        blocks: list[shared_memory.SharedMemory] = []
        try:
            cancel_block = shared.new_cancel_block()
            blocks.append(cancel_block)
            run = _Run(node_id, "heavy", shared.SharedCancelFlag(cancel_block), blocks=blocks)
            args = shared.pack(job.args, blocks)
            kwargs = shared.pack(dict(job.kwargs), blocks)
            name = cancel_block.name
            try:
                run.pool = self._process_pool()
                run.future = run.pool.submit(shared.run_packed, job.fn, args, kwargs, name)
            except BrokenProcessPool:
                self._reset_process_pool(run.pool)
                run.pool = self._process_pool()
                run.future = run.pool.submit(shared.run_packed, job.fn, args, kwargs, name)
        except Exception as exc:  # e.g. out of shared memory; report it like any failure
            run = _Run(node_id, "heavy", CancelFlag(), blocks=blocks)
            run.future.set_exception(exc)
        return run

    def _notify_done(self, run: _Run) -> None:
        """Runs on a worker or executor thread: hand over to the main thread."""
        # RuntimeError: the scheduler's Qt object is already gone (app shutting down).
        with contextlib.suppress(RuntimeError):
            self._done.emit(run)

    def _on_done(self, run: _Run) -> None:
        """Runs on the main thread once a run's future is done."""
        self._live.discard(run)
        shared.release(run.blocks)
        node_id = run.node_id
        if self._runs.get(node_id) is not run:
            return  # superseded or cancelled: nobody wants this result
        del self._runs[node_id]

        future = run.future
        if future.cancelled():
            return
        error = future.exception()
        if error is not None:
            if isinstance(error, Cancelled):
                self.cancelled.emit(node_id)
                return
            if isinstance(error, BrokenProcessPool):
                self._reset_process_pool(run.pool)
                message = "The worker process stopped unexpectedly."
                self.failed.emit(node_id, JobError(node_id, message, type(error).__name__))
                return
            self.failed.emit(node_id, JobError.from_exception(node_id, error))
            return
        result = future.result()
        if isinstance(result, shared.WorkerFailure):
            if result.error_type == Cancelled.__name__:
                self.cancelled.emit(node_id)
            else:
                err = JobError(node_id, result.message, result.error_type, result.traceback)
                self.failed.emit(node_id, err)
            return
        if isinstance(result, shared.PickledResult):
            try:
                result = result.load()
            except Exception as exc:  # e.g. a class the main process cannot import
                self.failed.emit(node_id, JobError.from_exception(node_id, exc))
                return
        self.finished.emit(node_id, result)
