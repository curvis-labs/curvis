"""Tests for Undo and Redo in the Edit menu."""

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from curvis.ui.main_window import MainWindow
from tests.ui.helpers import add_table


def edit_menu(window: MainWindow) -> QMenu:
    for action in window.menuBar().actions():
        if action.text().replace("&", "") == "Edit":
            menu = action.menu()
            assert isinstance(menu, QMenu)
            return menu
    raise AssertionError("no Edit menu")


def undo_redo(window: MainWindow) -> tuple[QAction, QAction]:
    return window.registry.action("edit.undo"), window.registry.action("edit.redo")


def test_edit_menu_holds_undo_and_redo(window: MainWindow) -> None:
    undo, redo = undo_redo(window)
    actions = edit_menu(window).actions()
    assert undo in actions and redo in actions
    assert undo.shortcut().toString() == "Ctrl+Z"


def test_undo_and_redo_follow_the_stack(window: MainWindow) -> None:
    undo, redo = undo_redo(window)
    tab = window.new_document()
    assert not undo.isEnabled() and not redo.isEnabled()

    add_table(tab)
    assert undo.isEnabled() and not redo.isEnabled()
    assert undo.text() == "&Undo Add table 'Data 1'"

    undo.trigger()
    assert tab.document.tables == []
    assert not undo.isEnabled() and redo.isEnabled()
    assert redo.text() == "&Redo Add table 'Data 1'"
    assert undo.text() == "&Undo"

    redo.trigger()
    assert [t.name for t in tab.document.tables] == ["Data 1"]
    assert undo.isEnabled() and not redo.isEnabled()


def test_undo_acts_on_the_current_tab_only(window: MainWindow) -> None:
    undo, _ = undo_redo(window)
    first = window.new_document()
    add_table(first)
    second = window.new_document()
    assert not undo.isEnabled()  # the new tab has nothing to undo

    window.tabs.setCurrentWidget(first)
    assert undo.isEnabled()
    undo.trigger()
    assert first.document.tables == []
    assert second.document.undo_stack.index == 0


def test_undo_back_to_saved_state_clears_modified_marker(window: MainWindow) -> None:
    tab = window.new_document()
    add_table(tab)
    tab.document.undo_stack.set_clean()
    window._update_tab(tab)
    add_table(tab, "Data 2")
    assert window.isWindowModified()
    undo_redo(window)[0].trigger()
    assert not window.isWindowModified()


def test_undo_actions_disabled_on_launcher(window: MainWindow) -> None:
    undo, redo = undo_redo(window)
    tab = window.new_document()
    add_table(tab)
    tab.document.undo_stack.set_clean()
    window.close_tab(tab)
    assert not undo.isEnabled() and not redo.isEnabled()
