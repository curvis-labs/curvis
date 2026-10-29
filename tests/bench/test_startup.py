"""Start-up benchmarks. Target: cold start to launcher < 1.5 s (CLAUDE.md)."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings
from pytestqt.qtbot import QtBot

from curvis.settings import Settings
from curvis.ui.main_window import MainWindow

COLD_START_TARGET_S = 1.5


def test_cold_start_to_launcher(benchmark: Any, tmp_path: Path) -> None:
    """A new Python process runs ``python -m curvis`` until the launcher has been painted."""
    env = dict(os.environ)
    env.update(
        CURVIS_EXIT_AFTER_LAUNCHER="1",
        QT_QPA_PLATFORM=env.get("QT_QPA_PLATFORM", "offscreen"),
        HOME=str(tmp_path),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        APPDATA=str(tmp_path / "appdata"),
    )

    def start() -> int:
        return subprocess.run(
            [sys.executable, "-m", "curvis"], env=env, capture_output=True, timeout=60
        ).returncode

    code = benchmark.pedantic(start, rounds=5, warmup_rounds=1)
    assert code == 0
    assert benchmark.stats.stats.min < COLD_START_TARGET_S


def test_create_main_window_shell(benchmark: Any, qtbot: QtBot, tmp_path: Path) -> None:
    """Building the main window: menus, actions, launcher and tab widget."""
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))

    def create() -> MainWindow:
        window = MainWindow(settings)
        qtbot.addWidget(window)
        return window

    window = benchmark(create)
    assert window.windowTitle() == "Curvis"
