"""Tests for the application entry point: files opened by the OS."""

from pathlib import Path

import pytest
from PySide6.QtGui import QFileOpenEvent
from PySide6.QtWidgets import QApplication

from curvis.app import FileOpenRouter, project_paths
from curvis.ui.main_window import MainWindow


def test_project_paths_from_command_line() -> None:
    args = ["a.cvp", "-flag", "notes.txt", "B.CVP"]
    assert project_paths(args) == [Path("a.cvp"), Path("B.CVP")]


def test_file_open_events_reach_the_window(
    qapp: QApplication, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """macOS sends a QFileOpenEvent when a .cvp file is double-clicked in the Finder."""
    opened: list[Path] = []
    monkeypatch.setattr(window, "open_path", opened.append)
    router = FileOpenRouter()
    qapp.installEventFilter(router)
    try:
        QApplication.sendEvent(qapp, QFileOpenEvent(str(tmp_path / "early.cvp")))
        assert opened == []  # no window yet: queued
        router.set_window(window)
        assert opened == [tmp_path / "early.cvp"]
        QApplication.sendEvent(qapp, QFileOpenEvent(str(tmp_path / "late.cvp")))
        assert opened == [tmp_path / "early.cvp", tmp_path / "late.cvp"]
    finally:
        qapp.removeEventFilter(router)
