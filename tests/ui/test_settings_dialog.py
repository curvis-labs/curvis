"""Tests for the Settings dialog and the theme."""

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from curvis.settings import Settings, Theme
from curvis.ui.settings_dialog import SettingsDialog
from curvis.ui.theme import ThemeManager, is_dark, system_is_dark


def test_dialog_shows_current_values(qtbot: QtBot, settings: Settings) -> None:
    settings.theme = Theme.DARK
    settings.decimal_places = 2
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    assert dialog.theme.currentData() == Theme.DARK
    assert dialog.decimal_places.value() == 2


def test_ok_with_defaults_changes_nothing(qtbot: QtBot, settings: Settings) -> None:
    changed: list[str] = []
    settings.changed.connect(changed.append)
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    dialog.buttons.button(dialog.buttons.StandardButton.Ok).click()
    assert changed == []
    assert settings.theme is Theme.SYSTEM


def test_ok_applies_changes(qtbot: QtBot, settings: Settings) -> None:
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    dialog.theme.setCurrentIndex(dialog.theme.findData(Theme.LIGHT))
    dialog.decimal_places.setValue(6)
    dialog.accept()
    assert settings.theme is Theme.LIGHT
    assert settings.decimal_places == 6


def test_cancel_discards_changes(qtbot: QtBot, settings: Settings) -> None:
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    dialog.theme.setCurrentIndex(dialog.theme.findData(Theme.DARK))
    dialog.decimal_places.setValue(9)
    dialog.reject()
    assert settings.theme is Theme.SYSTEM
    assert settings.decimal_places == 4


def test_decimal_places_limited_to_valid_range(qtbot: QtBot, settings: Settings) -> None:
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    assert (dialog.decimal_places.minimum(), dialog.decimal_places.maximum()) == (0, 15)


def test_theme_manager_applies_light_dark_and_system(qapp: QApplication) -> None:
    original = qapp.palette()
    manager = ThemeManager(qapp)
    try:
        manager.apply(Theme.DARK)
        assert is_dark(qapp.palette())
        manager.apply(Theme.LIGHT)
        assert not is_dark(qapp.palette())
        manager.apply(Theme.SYSTEM)
        assert is_dark(qapp.palette()) == system_is_dark()
    finally:
        manager.deleteLater()
        qapp.setPalette(original)


def test_dark_palette_dims_disabled_text(qapp: QApplication) -> None:
    from curvis.ui.theme import dark_palette

    palette = dark_palette()
    role = QPalette.ColorRole.WindowText
    active = palette.color(QPalette.ColorGroup.Active, role).lightness()
    disabled = palette.color(QPalette.ColorGroup.Disabled, role).lightness()
    assert disabled < active
