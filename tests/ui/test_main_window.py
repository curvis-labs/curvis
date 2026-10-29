"""Tests for the main window."""

from pytestqt.qtbot import QtBot

from curvis.ui.main_window import MainWindow


def test_main_window_opens_with_title(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    assert window.windowTitle() == "Curvis"
    assert window.isVisible()
