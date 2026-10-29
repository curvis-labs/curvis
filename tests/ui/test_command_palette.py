"""Tests for the command palette and the action registry."""

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QWidget
from pytestqt.qtbot import QtBot

from curvis.ui.actions import ActionRegistry
from curvis.ui.command_palette import CommandPalette
from curvis.ui.main_window import MainWindow


def menu_actions(menu: QMenu) -> list[QAction]:
    result = []
    for action in menu.actions():
        if action.isSeparator():
            continue
        sub = action.menu()
        if isinstance(sub, QMenu):
            result += menu_actions(sub)
        else:
            result.append(action)
    return result


def test_every_menu_action_is_registered(window: MainWindow) -> None:
    """New actions must go through the registry so the palette lists them."""
    actions = []
    for top in window.menuBar().actions():
        sub = top.menu()
        assert isinstance(sub, QMenu)
        actions += menu_actions(sub)
    assert actions
    unregistered = [a.text() for a in actions if a not in window.registry]
    assert unregistered == []


def test_palette_lists_every_registered_action(window: MainWindow, qtbot: QtBot) -> None:
    palette = CommandPalette(window.registry, window)
    qtbot.addWidget(palette)
    assert [e.id for e in palette.visible_entries()] == [e.id for e in window.registry.entries()]


def test_filter_matches_all_words_ignoring_case(window: MainWindow, qtbot: QtBot) -> None:
    palette = CommandPalette(window.registry, window)
    qtbot.addWidget(palette)
    qtbot.keyClicks(palette.search, "file SAVE")
    assert [e.id for e in palette.visible_entries()] == ["file.save", "file.save_as"]
    palette.search.setText("nothing like this")
    assert palette.visible_entries() == []


def test_enter_runs_selected_action(window: MainWindow, qtbot: QtBot) -> None:
    palette = CommandPalette(window.registry, window)
    qtbot.addWidget(palette)
    qtbot.keyClicks(palette.search, "new project")
    qtbot.keyClick(palette.search, Qt.Key.Key_Return)
    assert window.tabs.count() == 1
    assert not palette.isVisible()


def test_arrow_keys_move_selection(window: MainWindow, qtbot: QtBot) -> None:
    window.new_document()  # enables the Save actions
    palette = CommandPalette(window.registry, window)
    qtbot.addWidget(palette)
    qtbot.keyClicks(palette.search, "save")
    assert palette.list.currentRow() == 0
    qtbot.keyClick(palette.search, Qt.Key.Key_Down)
    assert palette.list.currentRow() == 1
    qtbot.keyClick(palette.search, Qt.Key.Key_Up)
    assert palette.list.currentRow() == 0


def test_disabled_actions_are_shown_but_do_not_run(window: MainWindow, qtbot: QtBot) -> None:
    palette = CommandPalette(window.registry, window)
    qtbot.addWidget(palette)
    palette.search.setText("undo")
    entries = palette.visible_entries()
    assert [e.id for e in entries] == ["edit.undo"]
    item = palette.list.item(0)
    assert not item.flags() & Qt.ItemFlag.ItemIsEnabled
    triggered: list[bool] = []
    entries[0].action.triggered.connect(lambda: triggered.append(True))
    palette.list.setCurrentRow(0)
    palette.run_selected()
    assert triggered == []


def test_ctrl_k_opens_the_palette(window: MainWindow, qtbot: QtBot) -> None:
    seen: list[QWidget] = []

    def close_palette() -> None:
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, CommandPalette):
            seen.append(dialog)
            dialog.reject()

    QTimer.singleShot(0, close_palette)
    action = window.registry.action("view.command_palette")
    assert action.shortcut().toString() == "Ctrl+K"
    action.trigger()
    assert len(seen) == 1


def test_registry_rejects_duplicate_ids(qtbot: QtBot) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    registry = ActionRegistry(parent)
    registry.register("x", "X", lambda: None, category="Test")
    with pytest.raises(ValueError, match="already registered"):
        registry.register("x", "Other", lambda: None, category="Test")


def test_registered_action_text_strips_mnemonics(qtbot: QtBot) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    registry = ActionRegistry(parent)
    registry.register("a", "Save &as…", lambda: None, category="File", shortcut="Ctrl+Shift+S")
    entry = registry.entries()[0]
    assert entry.text == "Save as…"
    assert entry.shortcut
    assert entry.action in parent.actions()
