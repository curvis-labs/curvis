"""Accessibility (step 4.2): what a screen reader announces, and full keyboard operation.

- Every control the keyboard can reach has a name that the accessibility interface
  reports (the name a screen reader reads out), in every dialog, the launcher and the
  main window with its Results and Graph panels.
- Tab reaches every such control of a dialog.
- F6 and Shift+F6 move the focus between the panes of a project, since Tab cannot
  leave the table (it moves between cells there).
- Focus and selection marks stand out from the backgrounds they are drawn on (WCAG
  non-text contrast); the text colours are checked in test_theme.py.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAccessible, QPalette
from PySide6.QtWidgets import QApplication, QDialog, QWidget
from pytestqt.qtbot import QtBot

from curvis.analysis.base import Analysis, get
from curvis.charts.spec import ChartFamily
from curvis.compute import Scheduler
from curvis.core import RenameTable, add_analysis
from curvis.settings import Settings
from curvis.ui.document_tab import DocumentTab
from curvis.ui.main_window import MainWindow
from curvis.ui.theme import BORDER_CONTRAST_MIN, contrast, dark_palette, light_palette
from tests.ui.dialog_samples import SAMPLES, Context, grouped_document, settle


def accessible_name(widget: QWidget) -> str:
    interface = QAccessible.queryAccessibleInterface(widget)
    return interface.text(QAccessible.Text.Name).strip() if interface is not None else ""


def focusable(root: QWidget) -> list[QWidget]:
    """The widgets of ``root`` that Tab can move to now."""
    return [
        w
        for w in root.findChildren(QWidget)
        if w.isVisibleTo(root)
        and w.isEnabled()
        and w.focusPolicy() & Qt.FocusPolicy.TabFocus
        and w.focusProxy() is None
    ]


def unnamed(root: QWidget) -> list[str]:
    return [
        f"{type(w).__name__} in {type(w.parentWidget()).__name__}"
        for w in focusable(root)
        if not accessible_name(w)
    ]


def tab_chain(root: QWidget) -> set[int]:
    seen: set[int] = set()
    widget = root.nextInFocusChain()
    while widget is not root and id(widget) not in seen:
        seen.add(id(widget))
        widget = widget.nextInFocusChain()
    return seen


# --- dialogs -------------------------------------------------------------------------------


@pytest.fixture
def context(qapp: QApplication, tmp_path: Path) -> Iterator[Context]:
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    ctx = Context(Settings(store), Scheduler(debounce_ms=0))
    yield ctx
    ctx.scheduler.shutdown()


def open_sample(name: str, qtbot: QtBot, context: Context, qapp: QApplication) -> QDialog:
    dialog = SAMPLES[name](context)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    settle(dialog, context, qapp)
    return dialog


@pytest.mark.parametrize("name", SAMPLES)
def test_dialog_controls_have_accessible_names(
    name: str, qtbot: QtBot, context: Context, qapp: QApplication
) -> None:
    dialog = open_sample(name, qtbot, context, qapp)
    assert not unnamed(dialog), f"{name}: no accessible name for {unnamed(dialog)}"


@pytest.mark.parametrize("name", SAMPLES)
def test_tab_reaches_every_dialog_control(
    name: str, qtbot: QtBot, context: Context, qapp: QApplication
) -> None:
    dialog = open_sample(name, qtbot, context, qapp)
    chain = tab_chain(dialog)
    missing = [accessible_name(w) for w in focusable(dialog) if id(w) not in chain]
    assert not missing, f"{name}: Tab never reaches {missing}"


# --- launcher and main window --------------------------------------------------------------


def settled(window: MainWindow, qtbot: QtBot) -> None:
    scheduler = window.scheduler
    qtbot.waitUntil(lambda: not scheduler.busy, timeout=10_000)
    for _ in range(5):
        QApplication.processEvents()


def project_with_results(window: MainWindow, qtbot: QtBot) -> DocumentTab:
    document = grouped_document()
    tab = window._add_tab(document, None)
    kind = get("hill_4pl")
    table = document.table("t")
    analysis = Analysis(kind.id, "t", kind.default_inputs(table), kind.default_params())
    document.execute(add_analysis(document, analysis))
    tab.plot(ChartFamily.XY)
    settled(window, qtbot)
    return tab


def test_launcher_controls_have_accessible_names(
    window: MainWindow, settings: Settings, tmp_path: Path
) -> None:
    recent = tmp_path / "Experiment 12.cvp"
    recent.touch()
    settings.add_recent_project(recent)
    assert window.current_tab is None
    assert not unnamed(window), unnamed(window)


@pytest.mark.parametrize("panel", ["results", "graph"])
def test_main_window_controls_have_accessible_names(
    panel: str, window: MainWindow, qtbot: QtBot
) -> None:
    tab = project_with_results(window, qtbot)
    tab.show_results() if panel == "results" else tab.show_graph()
    settled(window, qtbot)
    assert not unnamed(window), unnamed(window)


def test_the_table_is_named_after_its_table(window: MainWindow, qtbot: QtBot) -> None:
    tab = project_with_results(window, qtbot)
    assert tab.table_view is not None
    assert accessible_name(tab.table_view) == "Table Data 1"
    tab.document.execute(RenameTable("t", "Plate 2"))
    assert accessible_name(tab.table_view) == "Table Plate 2"


def press(qtbot: QtBot, key: Qt.Key, modifier: Qt.KeyboardModifier) -> QWidget:
    focus = QApplication.focusWidget()
    assert focus is not None
    qtbot.keyClick(focus, key, modifier)
    new = QApplication.focusWidget()
    assert new is not None
    return new


@pytest.mark.parametrize("panel", ["results", "graph"])
def test_f6_moves_between_the_table_the_side_panel_and_the_table_tabs(
    panel: str, window: MainWindow, qtbot: QtBot
) -> None:
    tab = project_with_results(window, qtbot)
    tab.show_results() if panel == "results" else tab.show_graph()
    side = tab.side_tabs.currentWidget()
    view = tab.table_view
    assert view is not None
    window.activateWindow()
    view.setFocus()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is view)
    no = Qt.KeyboardModifier.NoModifier
    shift = Qt.KeyboardModifier.ShiftModifier
    assert side.isAncestorOf(press(qtbot, Qt.Key.Key_F6, no))
    assert tab.table_tabs.isAncestorOf(press(qtbot, Qt.Key.Key_F6, no))
    assert press(qtbot, Qt.Key.Key_F6, no) is view
    assert tab.table_tabs.isAncestorOf(press(qtbot, Qt.Key.Key_F6, shift))
    assert side.isAncestorOf(press(qtbot, Qt.Key.Key_F6, shift))
    assert press(qtbot, Qt.Key.Key_F6, shift) is view


def test_the_pane_keys_are_in_the_view_menu_and_the_palette(window: MainWindow) -> None:
    window.new_document()  # the table actions are made with the first project
    for action_id, keys in (("view.next_pane", "F6"), ("view.previous_pane", "Shift+F6")):
        entry = next(e for e in window.registry.entries() if e.id == action_id)
        assert entry.action.shortcut().toString() == keys
        assert entry.category == "View"
        assert entry.action in window._view_menu.actions()


# --- contrast of focus and selection marks -------------------------------------------------


@pytest.mark.parametrize("make", [light_palette, dark_palette], ids=["light", "dark"])
def test_focus_and_selection_marks_stand_out(make: object) -> None:
    """Fusion draws the focus frame and the selection in Highlight (WCAG 1.4.11: 3:1)."""
    palette: QPalette = make()  # type: ignore[operator]
    mark = palette.color(QPalette.ColorRole.Highlight)
    for background in (
        QPalette.ColorRole.Window,
        QPalette.ColorRole.Base,
        QPalette.ColorRole.Button,
    ):
        ratio = contrast(mark, palette.color(background))
        assert ratio >= BORDER_CONTRAST_MIN, f"Highlight on {background.name}: {ratio:.2f} : 1"
