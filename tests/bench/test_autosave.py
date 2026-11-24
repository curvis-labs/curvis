"""Benchmark: a recovery copy of a 10 MB project never blocks the UI thread for > 16 ms.

The UI thread does two things for a copy: it takes a snapshot of the document (plain
data that shares the column arrays, so it copies no cells) and hands the write to the
scheduler. The write itself runs on a worker thread; while it runs, a 1 ms timer on the
UI thread records the longest gap between its ticks, which is how long the window could
not respond (the worker holding the GIL, say).

The warm-up round writes the session's first copy, which loads what pyarrow loads on
first use; in the app the preloader has loaded those modules long before the first
autosave, which comes minutes after a project is opened.
"""

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QSettings, Qt, QTimer
from pytestqt.qtbot import QtBot

from curvis.core import RenameTable
from curvis.io.recovery import RecoveryStore
from curvis.settings import Settings
from curvis.ui.main_window import MainWindow, SaveChoice
from tests.bench.test_project_io import ten_mb_document

FRAME_BUDGET_S = 0.016
"""One frame at 60 fps."""


@pytest.fixture
def window(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[MainWindow]:
    monkeypatch.setattr(MainWindow, "_ask_save", lambda self, tab: SaveChoice.DISCARD)
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    win = MainWindow(settings, recovery=RecoveryStore(tmp_path / "recovery"))
    qtbot.addWidget(win)
    yield win
    # Shut the scheduler down (and wait) before closing: the window's own shutdown does
    # not wait, which leaves garbage collection on the main thread for later benchmarks.
    if win._scheduler is not None:
        win._scheduler.shutdown()
    win.close()


def test_recovery_copy_of_10mb_project_does_not_block_ui(
    benchmark: Any, window: MainWindow
) -> None:
    import curvis.io.project  # noqa: F401 - loaded once per session, not per copy

    tab = window._add_tab(ten_mb_document(), None)
    autosave = window.autosave
    assert autosave is not None
    scheduler = window.scheduler
    longest_gap = 0.0
    snapshots: list[float] = []  # how long the UI thread took to hand over each copy
    rounds = 0

    def one_copy() -> None:
        """Take and write one copy (the benchmark times the whole copy)."""
        nonlocal longest_gap, rounds
        tab.document.execute(RenameTable(tab.document.tables[0].id, f"T{time.perf_counter()}"))
        assert scheduler.wait_idle(30_000)  # the edit's own recalculation is not the copy's
        start = time.perf_counter()
        assert autosave.write_if_needed(tab)
        blocked = time.perf_counter() - start
        last = [time.perf_counter()]
        gaps = [0.0]

        def tick() -> None:
            now = time.perf_counter()
            gaps.append(now - last[0])
            last[0] = now

        timer = QTimer()
        timer.setTimerType(Qt.TimerType.PreciseTimer)
        timer.timeout.connect(tick)
        timer.start(1)
        assert scheduler.wait_idle(30_000)
        timer.stop()
        tick()
        rounds += 1
        if rounds > 1:  # not the warm-up round
            longest_gap = max(longest_gap, *gaps)
            snapshots.append(blocked)

    benchmark.pedantic(one_copy, rounds=10, warmup_rounds=1)
    assert rounds == 11
    assert autosave.store.copy_path(autosave.copy_id(tab) or "").is_file()
    assert max(snapshots) < FRAME_BUDGET_S, f"snapshot took {max(snapshots) * 1000:.1f} ms"
    assert longest_gap < FRAME_BUDGET_S, f"UI thread blocked {longest_gap * 1000:.1f} ms"
