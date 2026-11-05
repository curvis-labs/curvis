"""Garbage collection stays on the main thread while the scheduler's workers run Python.

A collection triggered by allocations on a worker thread would free Python-owned Qt
objects in reference cycles there, deleting them off the GUI thread (a segfault in CI,
PR #30). These tests fail without :mod:`curvis.compute.gc_main_thread`.
"""

from __future__ import annotations

import gc
import threading
from collections.abc import Iterator

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from curvis.compute import Scheduler, gc_main_thread


class CyclicProbe(QObject):
    """A Python-owned QObject that only the cyclic collector can free. Records the
    thread it was finalized on."""

    def __init__(self, finalized: list[threading.Thread]) -> None:
        super().__init__()
        self.me = self  # the cycle
        self._finalized = finalized

    def __del__(self) -> None:
        self._finalized.append(threading.current_thread())


@pytest.fixture
def scheduler(qapp: QApplication) -> Iterator[Scheduler]:
    s = Scheduler(debounce_ms=0, max_threads=1, max_processes=1)
    yield s
    s.shutdown()


def test_cycles_dropped_on_a_worker_are_collected_on_the_main_thread(
    qtbot: QtBot, scheduler: Scheduler
) -> None:
    finalized: list[threading.Thread] = []
    holder = [CyclicProbe(finalized)]

    def job() -> list[tuple[int, str]]:
        holder.clear()  # the probe is now unreachable garbage
        # Allocate like a CSV preview does (tuples per record): enough container
        # objects for automatic collection of every generation to trigger here.
        # Return them, so they are still alive when the main thread's timer next
        # looks: it sees only the net count (allocations minus frees) at that moment.
        # Freed in the job, the net count could be anywhere near zero, depending on
        # how full the tuple free list was (it is full after many earlier tests).
        return [(i, str(i)) for i in range(300_000)]

    done: list[object] = []
    scheduler.finished.connect(lambda node, result: done.append(result))
    scheduler.submit("allocate", job)
    qtbot.waitUntil(lambda: bool(done), timeout=10_000)
    qtbot.waitUntil(lambda: bool(finalized), timeout=5_000)
    assert finalized == [threading.main_thread()]


def test_the_scheduler_holds_main_thread_collection_until_shut_down(qapp: QApplication) -> None:
    # A window closed earlier in the session may still hold it (shutdown(wait=False)).
    before = (gc_main_thread.is_held(), gc.isenabled())
    first = Scheduler()
    second = Scheduler()
    assert gc_main_thread.is_held() and not gc.isenabled()
    first.shutdown()
    assert gc_main_thread.is_held()
    second.shutdown()
    assert (gc_main_thread.is_held(), gc.isenabled()) == before


def test_a_shutdown_that_does_not_wait_keeps_collection_on_the_main_thread(
    qapp: QApplication,
) -> None:
    s = Scheduler()
    s.shutdown(wait=False)  # a job could still be running on a worker
    assert gc_main_thread.is_held()


def test_collect_if_due_follows_the_thresholds(qapp: QApplication) -> None:
    old = gc.get_threshold()
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        gc.collect()
        gc.set_threshold(1_000_000)
        assert gc_main_thread.collect_if_due() == 0  # nothing due
        gc.set_threshold(10)
        garbage: list[object] = []
        for _ in range(50):
            cycle: list[object] = []
            cycle.append(cycle)
            garbage.append(cycle)
        del cycle
        garbage.clear()
        assert gc_main_thread.collect_if_due() >= 50
        assert gc.get_count()[0] < 10
    finally:
        gc.set_threshold(*old)
        if was_enabled:
            gc.enable()


def test_hold_and_release_only_on_the_main_thread(qapp: QApplication) -> None:
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            gc_main_thread.hold()
        except RuntimeError as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert len(errors) == 1


def test_freeze_startup_objects_runs_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gc_main_thread, "_frozen", False)
    try:
        gc_main_thread.freeze_startup_objects()
        frozen = gc.get_freeze_count()
        assert frozen > 0
        gc_main_thread.freeze_startup_objects()  # later calls do nothing
        assert gc.get_freeze_count() == frozen
    finally:
        gc.unfreeze()  # keep the rest of the test session collectable


def test_frozen_objects_are_still_freed_by_reference_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finalized: list[threading.Thread] = []

    class Plain:
        def __del__(self) -> None:
            finalized.append(threading.current_thread())

    holder = [Plain()]
    monkeypatch.setattr(gc_main_thread, "_frozen", False)
    try:
        gc_main_thread.freeze_startup_objects()
        holder.clear()
        assert finalized == [threading.main_thread()]
    finally:
        gc.unfreeze()
