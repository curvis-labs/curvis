"""Benchmarks. Run with ``uv run pytest tests/bench --benchmark-only``."""

from typing import Any

from pytestqt.qtbot import QtBot

from curvis.ui.main_window import MainWindow


def test_create_main_window(benchmark: Any, qtbot: QtBot) -> None:
    def create() -> MainWindow:
        window = MainWindow()
        qtbot.addWidget(window)
        return window

    window = benchmark(create)
    assert window.windowTitle() == "Curvis"
