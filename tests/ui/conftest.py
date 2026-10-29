"""Fixtures for UI tests. No test touches the real user settings."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from pytestqt.qtbot import QtBot

from curvis.settings import Settings
from curvis.ui.main_window import MainWindow, SaveChoice


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))


class Prompts:
    """Scripted answers for the main window's dialogs, and a record of what was asked."""

    def __init__(self) -> None:
        self.save_choices: list[SaveChoice] = []
        self.save_paths: list[Path | None] = []
        self.open_paths: list[Path | None] = []
        self.asked_save: list[str] = []
        self.errors: list[tuple[str, str]] = []


@pytest.fixture
def prompts() -> Prompts:
    return Prompts()


@pytest.fixture
def window(
    qtbot: QtBot, settings: Settings, prompts: Prompts, monkeypatch: pytest.MonkeyPatch
) -> Iterator[MainWindow]:
    def ask_save(self: MainWindow, tab: object) -> SaveChoice:
        prompts.asked_save.append(tab.title)  # type: ignore[attr-defined]
        # pytest-qt closes the window before fixtures are torn down: discard then.
        return prompts.save_choices.pop(0) if prompts.save_choices else SaveChoice.DISCARD

    monkeypatch.setattr(MainWindow, "_ask_save", ask_save)
    monkeypatch.setattr(MainWindow, "_ask_save_path", lambda self, tab: prompts.save_paths.pop(0))
    monkeypatch.setattr(MainWindow, "_ask_open_path", lambda self: prompts.open_paths.pop(0))
    monkeypatch.setattr(
        MainWindow, "_show_error", lambda self, title, msg: prompts.errors.append((title, msg))
    )
    win = MainWindow(settings)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    yield win
    if win._scheduler is not None:
        win._scheduler.shutdown()
