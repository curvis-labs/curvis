"""The crash dialog and the crash log check box in Settings."""

from pathlib import Path

from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from curvis.settings import Settings
from curvis.ui.crash_dialog import COPIED, TITLE, CrashDialog
from curvis.ui.settings_dialog import SettingsDialog


def test_dialog_shows_the_report_and_copies_it(qtbot: QtBot) -> None:
    dialog = CrashDialog("Curvis 0.1.0b1\nValueError: boom\n", Path("/logs/crash.log"))
    qtbot.addWidget(dialog)
    assert dialog.windowTitle() == TITLE
    assert dialog.text.isReadOnly()
    assert dialog.text.toPlainText() == "Curvis 0.1.0b1\nValueError: boom\n"
    assert "crash.log" in dialog.where.text()
    clipboard = QApplication.clipboard()
    clipboard.clear()
    dialog.copy_button.click()
    assert clipboard.text() == "Curvis 0.1.0b1\nValueError: boom\n"
    assert dialog.skeleton.feedback.text() == COPIED


def test_crash_log_is_off_until_turned_on_in_settings(qtbot: QtBot, settings: Settings) -> None:
    assert settings.crash_log is False
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    assert not dialog.crash_log.isChecked()
    changed: list[str] = []
    settings.changed.connect(changed.append)
    dialog.crash_log.setChecked(True)
    dialog.accept()
    assert settings.crash_log is True
    assert changed == [Settings.CRASH_LOG]
