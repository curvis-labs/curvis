"""The main toolbar and the table toolbar (SPEC.md; docs/DESIGN.md, section 7; mockup
``docs/mockups/toolbars.html``).

- Both sit in one row under the menu bar, fixed in place. The main toolbar always
  shows; the table toolbar shows while a table is shown.
- Every button is a registered action (the same ``QAction`` as the menu item and the
  command palette entry); this module only places them.
- Main toolbar: icons only, the tooltip gives the name and shortcut. Table toolbar:
  icon with the menu text beside it (without "&" and "…").
- A disabled button's tooltip says why (:func:`set_tooltips`).
- Plot opens the chart-family menu.
- Buttons that do not fit move into Qt's overflow menu (»), in order.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QMainWindow, QToolBar, QToolButton

from curvis.ui.actions import ActionRegistry, RegisteredAction
from curvis.ui.toolbar_icons import ICON_SIZE, toolbar_icon

BUTTON_HEIGHT = 28


@dataclass(frozen=True)
class Button:
    action_id: str
    icon: str
    name: str = ""
    """Tooltip name, when it differs from the menu text (e.g. "Save project")."""


SEPARATOR = None

MAIN_BUTTONS: tuple[Button | None, ...] = (
    Button("file.new", "new_project", "New project"),
    Button("file.open", "open_project", "Open project"),
    Button("file.save", "save_project", "Save project"),
    SEPARATOR,
    Button("edit.undo", "undo", "Undo"),
    Button("edit.redo", "redo", "Redo"),
)
TABLE_BUTTONS: tuple[Button | None, ...] = (
    Button("file.import", "import_data"),
    SEPARATOR,
    Button("table.add_column", "add_column"),
    Button("table.group", "group_as_replicates"),
    Button("table.set_category", "set_category"),
    Button("table.calculate", "calculate"),
    SEPARATOR,
    Button("graph.plot", "plot"),
    Button("analyze.analyze", "analyze"),
)
FAMILY_ICONS = {
    "graph.new.xy": "chart_xy",
    "graph.new.bar": "chart_bar",
    "graph.new.survival": "chart_survival",
    "graph.new.interaction": "chart_interaction",
}


def button_name(entry: RegisteredAction, button: Button | None = None) -> str:
    """The name a toolbar shows for an action: the menu text without "&" and "…"."""
    if button is not None and button.name:
        return button.name
    return entry.text.rstrip(".…").strip()


def _make_toolbar(window: QMainWindow, title: str, name: str) -> QToolBar:
    toolbar = QToolBar(title, window)
    toolbar.setObjectName(name)
    toolbar.setMovable(False)
    toolbar.setFloatable(False)
    toolbar.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
    toolbar.toggleViewAction().setVisible(False)
    toolbar.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
    return toolbar


def _fill(
    toolbar: QToolBar,
    registry: ActionRegistry,
    buttons: tuple[Button | None, ...],
    style: Qt.ToolButtonStyle,
) -> None:
    toolbar.setToolButtonStyle(style)
    entries = {e.id: e for e in registry.entries()}
    for button in buttons:
        if button is None:
            toolbar.addSeparator()
            continue
        entry = entries[button.action_id]
        action = entry.action
        action.setIcon(toolbar_icon(button.icon))
        action.setIconText(button_name(entry, button))
        toolbar.addAction(action)
        widget = toolbar.widgetForAction(action)
        if isinstance(widget, QToolButton):
            widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # not in the Tab order
            widget.setMinimumHeight(BUTTON_HEIGHT)
            if action.menu() is not None:
                widget.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)


def build_main_toolbar(window: QMainWindow, registry: ActionRegistry) -> QToolBar:
    """Add the main toolbar to ``window`` (it always shows)."""
    main = _make_toolbar(window, "Main toolbar", "mainToolbar")
    _fill(main, registry, MAIN_BUTTONS, Qt.ToolButtonStyle.ToolButtonIconOnly)
    window.addToolBar(Qt.ToolBarArea.TopToolBarArea, main)
    return main


def build_table_toolbar(window: QMainWindow, registry: ActionRegistry) -> QToolBar:
    """Add the table toolbar to ``window``, in the main toolbar's row."""
    table = _make_toolbar(window, "Table toolbar", "tableToolbar")
    _fill(table, registry, TABLE_BUTTONS, Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    window.addToolBar(Qt.ToolBarArea.TopToolBarArea, table)
    for action_id, icon in FAMILY_ICONS.items():
        registry.action(action_id).setIcon(toolbar_icon(icon))
    return table


def toolbar_buttons() -> dict[str, Button]:
    """Every toolbar button by action id."""
    return {b.action_id: b for b in (*MAIN_BUTTONS, *TABLE_BUTTONS) if b is not None}


def set_tooltips(registry: ActionRegistry, reasons: Mapping[str, str]) -> None:
    """Give each toolbar action its tooltip: why it is disabled (``reasons``), or its
    name and shortcut ("Save project (Ctrl+S)")."""
    buttons = toolbar_buttons()
    for entry in registry.entries():
        button = buttons.get(entry.id)
        if button is None and not entry.id.startswith("graph.new."):
            continue
        action = entry.action
        if not action.isEnabled():
            action.setToolTip(reasons.get(entry.id, ""))
            continue
        name = button_name(entry, button)
        action.setToolTip(f"{name} ({entry.shortcut})" if entry.shortcut else name)
