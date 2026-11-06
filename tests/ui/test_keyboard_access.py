"""Every action can be reached by keyboard: each menu and toolbar action is a registered
action (so the command palette lists it), and no two shortcuts clash."""

from collections import Counter

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMenu, QToolBar, QToolButton

from curvis.ui.command_palette import CommandPalette
from curvis.ui.main_window import MainWindow


def menu_actions(menu: QMenu) -> list[QAction]:
    found: list[QAction] = []
    for action in menu.actions():
        if action.isSeparator():
            continue
        sub = action.menu()
        if isinstance(sub, QMenu):
            found += menu_actions(sub)
            # a submenu's own entry is a registered action when it also does something
            # (Plot, Change graph type); a plain submenu title is not an action
        else:
            found.append(action)
    return found


def all_ui_actions(window: MainWindow) -> list[QAction]:
    window.ensure_sample_actions()  # made when the File menu first opens
    actions: list[QAction] = []
    for top in window.menuBar().actions():
        menu = top.menu()
        assert isinstance(menu, QMenu)
        actions += menu_actions(menu)
    for toolbar in window.findChildren(QToolBar):
        actions += [a for a in toolbar.actions() if not a.isSeparator() and a.isVisible()]
    tab = window.current_tab
    assert tab is not None
    for button in tab.graph_panel.findChildren(QToolButton):
        if button.defaultAction() is not None:
            actions.append(button.defaultAction())
    return [a for a in actions if a.text()]


def test_every_menu_and_toolbar_action_is_registered(window: MainWindow) -> None:
    window.new_document()
    unregistered = [a.text() for a in all_ui_actions(window) if a not in window.registry]
    assert unregistered == []


def test_the_palette_lists_every_menu_and_toolbar_action(window: MainWindow) -> None:
    window.new_document()
    window.ensure_sample_actions()  # as show_command_palette does
    palette = CommandPalette(window.registry, window)
    listed = {id(e.action) for e in palette.visible_entries()}
    missing = [a.text() for a in all_ui_actions(window) if id(a) not in listed]
    assert missing == []
    palette.deleteLater()


def test_no_two_actions_share_a_shortcut(window: MainWindow) -> None:
    window.new_document()
    keys = [
        key.toString(QKeySequence.SequenceFormat.PortableText)
        for entry in window.registry.entries()
        for key in entry.action.shortcuts()
        if not key.isEmpty()
    ]
    clashes = [key for key, n in Counter(keys).items() if n > 1]
    assert clashes == []


def test_custom_shortcuts_do_not_take_platform_standard_keys(window: MainWindow) -> None:
    """A shortcut set from a string must not be one the platform uses for something else
    (e.g. Ctrl+F for Find, Ctrl+P for Print)."""
    window.new_document()
    standard: dict[str, str] = {}
    for name, key in QKeySequence.StandardKey.__members__.items():
        for seq in QKeySequence.keyBindings(key):
            standard.setdefault(seq.toString(QKeySequence.SequenceFormat.PortableText), name)
    # Standard keys an action is meant to use (it was registered with that StandardKey).
    own = {
        "file.new": "New",
        "file.open": "Open",
        "file.save": "Save",
        "file.save_as": "SaveAs",
        "file.close": "Close",
        "file.quit": "Quit",
        "edit.undo": "Undo",
        "edit.redo": "Redo",
        "edit.copy": "Copy",
        "edit.paste": "Paste",
        "edit.clear": "Delete",
    }
    taken = []
    for entry in window.registry.entries():
        for seq in entry.action.shortcuts():
            text = seq.toString(QKeySequence.SequenceFormat.PortableText)
            name = standard.get(text)
            if name is not None and own.get(entry.id) != name:
                taken.append(f"{entry.id}: {text} is {name}")
    # The standard keys differ by platform, so only new clashes fail.
    assert [t for t in taken if t not in KNOWN_CLASHES] == []


KNOWN_CLASHES = [
    # Chosen in earlier steps, before this check existed; Curvis has no zoom or find yet.
    # Open question for step 2.9: keep them, or move them before find and zoom arrive.
    "table.delete_columns: Ctrl+- is ZoomOut",
    "table.group: Ctrl+G is FindNext",
    "table.ungroup: Ctrl+Shift+G is FindPrevious",
]
