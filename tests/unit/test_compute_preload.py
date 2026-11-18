"""The preloader: heavy modules are imported on the main thread a step at a time."""

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from pytestqt.qtbot import QtBot

from curvis.compute import preload
from curvis.compute.import_order import ORDER
from curvis.compute.preload import Preloader, steps

ROOT = Path(__file__).resolve().parents[2]
PROBE_PREFIX = "curvis_preload_probe_"


@pytest.fixture
def probes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Five probe modules that record, in import order, that they were imported."""
    for i in range(5):
        (tmp_path / f"{PROBE_PREFIX}{i}.py").write_text(
            f"import builtins\nbuiltins.curvis_probe_log.append({i})\n"
        )
    monkeypatch.syspath_prepend(str(tmp_path))
    log: list[int] = []
    monkeypatch.setattr("builtins.curvis_probe_log", log, raising=False)
    names = [f"{PROBE_PREFIX}{i}" for i in range(5)]
    yield names
    for name in names:
        sys.modules.pop(name, None)


def log() -> list[int]:
    import builtins

    return list(builtins.curvis_probe_log)  # type: ignore[attr-defined]


def test_steps_list_each_module_once_with_its_order_first() -> None:
    order = {"b": ("x", "y", "b"), "c": ("y", "z")}
    assert steps(["a", "b", "c"], order) == ("a", "x", "y", "b", "z", "c")


def test_ensure_imports_in_order_and_calls_ready_from_the_event_loop(
    qtbot: QtBot, probes: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preload, "STEP_MS", 0)  # one import per step
    preloader = Preloader(order={probes[4]: tuple(probes[:4])})
    ready: list[str] = []
    events_between: list[int] = []
    preloader._timer.timeout.connect(lambda: events_between.append(len(log())))
    preloader.ensure([probes[4]], lambda: ready.append("done"))
    assert ready == [] and log() == []  # nothing happens inside ensure()
    assert not preloader.is_ready([probes[4]])
    qtbot.waitUntil(lambda: bool(ready))
    assert log() == [0, 1, 2, 3, 4]
    assert events_between == [1, 2, 3, 4, 5]  # one import per trip through the event loop
    assert preloader.is_ready(probes) and not preloader.busy


def test_a_step_imports_several_cheap_modules(qtbot: QtBot, probes: list[str]) -> None:
    preloader = Preloader(order={probes[4]: tuple(probes[:4])})
    ready: list[str] = []
    preloader.ensure([probes[4]], lambda: ready.append("done"))
    qtbot.waitUntil(lambda: bool(ready))
    assert log() == [0, 1, 2, 3, 4]


def test_a_discarded_request_is_not_called_back(qtbot: QtBot, probes: list[str]) -> None:
    preloader = Preloader(order={})
    ready: list[str] = []
    waiter = preloader.ensure([probes[0]], lambda: ready.append("first"))
    preloader.discard(waiter)
    preloader.ensure([probes[1]], lambda: ready.append("second"))
    qtbot.waitUntil(lambda: bool(ready))
    assert ready == ["second"]
    assert log() == [1]


def test_a_module_that_fails_to_import_still_releases_the_request(qtbot: QtBot) -> None:
    preloader = Preloader(order={})
    ready: list[str] = []
    preloader.ensure(["curvis_no_such_module"], lambda: ready.append("done"))
    qtbot.waitUntil(lambda: bool(ready))
    assert preloader.is_ready(["curvis_no_such_module"])  # no point in waiting again


def test_idle_preloading_waits_for_a_pause_in_user_input(
    qtbot: QtBot, probes: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preload, "IDLE_MS", 300)
    monkeypatch.setattr(preload, "STEP_MS", 0)
    preloader = Preloader(order={})
    preloader.preload(probes[:2])
    press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, "a")
    QCoreApplication.sendEvent(QObject(), press)  # the user is typing
    assert preloader.busy
    qtbot.wait(150)
    assert log() == []  # still within IDLE_MS of the key press
    qtbot.waitUntil(lambda: not preloader.busy, timeout=3000)
    assert log() == [0, 1]
    assert not preloader._watching_input  # the event filter is removed when done


def test_a_waiting_job_does_not_wait_for_idle(
    qtbot: QtBot, probes: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preload, "IDLE_MS", 60_000)
    preloader = Preloader(order={})
    preloader.preload(probes[:3])
    QCoreApplication.sendEvent(
        QObject(),
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, "a"),
    )
    ready: list[str] = []
    preloader.ensure([probes[2]], lambda: ready.append("done"))
    qtbot.waitUntil(lambda: bool(ready), timeout=1000)
    assert log() == [2]  # only what the job needs; the rest waits for idle
    preloader.stop()


def test_every_target_ends_with_itself() -> None:
    from curvis.analysis.base import light_imports

    assert set(ORDER) == set(light_imports())
    for target, names in ORDER.items():
        assert names[-1] == target
        assert len(set(names)) == len(names)


@pytest.mark.skipif(sys.platform != "linux", reason="the import order is generated on Linux")
def test_the_import_order_is_up_to_date() -> None:
    """Fails after a dependency upgrade changes the import graph: regenerate the file."""
    result = subprocess.run(
        [sys.executable, "scripts/import_order.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --- the first analysis run ------------------------------------------------------------

FIRST_RUN_PROBE = """
import json, sys, time
import numpy as np
from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import QApplication

app = QApplication([])
from curvis.analysis import base
from curvis.analysis.base import AnalysisInputs, SeriesData
from curvis.analysis.result import Result
from curvis.compute import Job, Scheduler

kind = base.get("kruskal_wallis")
assert "scipy.stats" not in sys.modules
scheduler = Scheduler(max_processes=1)
if sys.argv[1] == "after_idle_preload":
    scheduler.preloader.preload(base.light_imports())
    while scheduler.preloader.busy:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 5)

rng = np.random.default_rng(1)
series = tuple(SeriesData(f"g{i}", f"G{i}", (rng.normal(i, 1, 12),)) for i in range(3))
job = Job(kind.run, (AnalysisInputs("T", 12, series), kind.default_params()), imports=kind.imports)

gaps = []
last = [time.perf_counter()]
def tick():
    now = time.perf_counter()
    gaps.append(now - last[0])
    last[0] = now
timer = QTimer()
timer.setTimerType(Qt.TimerType.PreciseTimer)
timer.timeout.connect(tick)
results = []
loop = QEventLoop()
scheduler.finished.connect(lambda node, result: (results.append(result), loop.quit()))
scheduler.failed.connect(lambda node, error: (results.append(error), loop.quit()))
def submit():
    global start
    start = time.perf_counter()
    scheduler.submit("kw", job, delay_ms=0)
timer.start(5)
QTimer.singleShot(50, submit)
loop.exec()
total = time.perf_counter() - start
print(json.dumps({"max_gap": max(gaps), "total": total, "ok": isinstance(results[0], Result)}))
scheduler.shutdown()
"""


def first_run(mode: str) -> dict[str, float]:
    env = {**os.environ, "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "offscreen")}
    result = subprocess.run(
        [sys.executable, "-c", FIRST_RUN_PROBE, mode],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    data: dict[str, float] = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["ok"]
    return data


def test_the_first_kruskal_wallis_run_after_idle_preloading_does_not_block() -> None:
    """The usual case: the app was idle for a moment after start-up."""
    data = first_run("after_idle_preload")
    assert data["max_gap"] < 0.1, data


def test_a_first_run_before_preloading_blocks_only_one_step_at_a_time() -> None:
    """The worst case (accepted by the project owner): a run straight after start-up
    waits for SciPy, whose largest step, ``import scipy.stats``, cannot be split. The
    event loop still runs between steps."""
    data = first_run("cold")
    assert data["max_gap"] < 0.5 * data["total"], data
