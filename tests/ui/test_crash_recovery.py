"""Crash recovery end to end: Curvis is killed mid-session, the next start offers the work."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from pytestqt.qtbot import QtBot

from curvis.io.recovery import RecoveryStore
from curvis.settings import Settings
from curvis.ui.main_window import MainWindow, SaveChoice

# Runs Curvis with one changed, never-saved project and an autosave interval of 50 ms,
# says "ready" once the timer has written a recovery copy, and keeps running.
SESSION = """
import sys, time
from pathlib import Path
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from curvis.core import RenameTable
from curvis.io.recovery import RecoveryStore
from curvis.settings import Settings
from curvis.ui.main_window import MainWindow

app = QApplication(["curvis"])
folder = Path(sys.argv[1])
settings = Settings(QSettings(sys.argv[2], QSettings.Format.IniFormat))
window = MainWindow(settings, recovery=RecoveryStore(folder))
window.show()
tab = window.new_document()
tab.document.execute(RenameTable(tab.document.tables[0].id, "Survived the crash"))
window.autosave.set_interval(50)
deadline = time.monotonic() + 30
while not list(folder.rglob("*.cvp")) or window.scheduler.busy:
    if time.monotonic() > deadline:
        sys.exit("no recovery copy was written")
    app.processEvents()
    time.sleep(0.01)
print("ready", flush=True)
while True:
    app.processEvents()
    time.sleep(0.05)
"""


def test_killed_session_is_recovered_on_the_next_start(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_ask_save", lambda self, tab: SaveChoice.DISCARD)
    folder = tmp_path / "recovery"
    ini = str(tmp_path / "settings.ini")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    child = subprocess.Popen(
        [sys.executable, "-c", SESSION, str(folder), ini],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert child.stdout is not None
        line = child.stdout.readline()
        assert line.strip() == "ready", child.stderr.read() if child.stderr else ""
    finally:
        child.kill()  # SIGKILL / TerminateProcess: no clean-up runs
        child.communicate(timeout=30)
    assert len(list(folder.rglob("*.cvp"))) == 1

    settings = Settings(QSettings(ini, QSettings.Format.IniFormat))
    window = MainWindow(settings, recovery=RecoveryStore(folder))
    qtbot.addWidget(window)
    window.show()
    try:
        [copy] = window.launcher.recovered
        assert copy.recoverable
        assert copy.name == "Untitled"
        window.open_recovered(copy)
        qtbot.waitUntil(lambda: window.current_tab is not None, timeout=10_000)
        tab = window.current_tab
        assert tab is not None
        assert tab.document.tables[0].name == "Survived the crash"
        assert tab.is_modified and tab.path is None
        assert window.launcher.recovered == []
        # The dead session's lock is gone; only this session's files are left.
        assert window.recovery is not None
        names = sorted(p.name for p in folder.iterdir())
        assert names == sorted([window.recovery.session, f"{window.recovery.session}.lock"])
    finally:
        window.close()  # discards the project: a clean exit
        if window._scheduler is not None:
            window._scheduler.shutdown()
    assert list(folder.iterdir()) == []
