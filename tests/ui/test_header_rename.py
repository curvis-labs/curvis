"""Renaming in the column header (docs/DESIGN.md section 9)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QFocusEvent, QKeySequence
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu
from pytestqt.qtbot import QtBot

from curvis.core import Document, GroupColumns
from curvis.data import Column, ColumnRole, NodeKind, Table
from curvis.ui.document_tab import DocumentTab
from curvis.ui.inline_edit import InlineEditor
from curvis.ui.main_window import MainWindow
from curvis.ui.table import TableView


def lab_table() -> Table:
    return Table(
        "Viability",
        [
            Column.new("Dose", ColumnRole.X, [0.1, 1, 10], id="dose"),
            Column.new("Control", ColumnRole.Y, [10, 11, 12], id="c1"),
            Column.new("Control 2", ColumnRole.Y, [10, 12, 13], id="c2"),
            Column.new("Drug", ColumnRole.Y, [20, 51, 82], id="d1"),
            Column.new("Drug 2", ColumnRole.Y, [21, 53, 80], id="d2"),
        ],
        id="t",
    )


@pytest.fixture
def tab(window: MainWindow) -> DocumentTab:
    return window._add_tab(Document(tables=[lab_table()]), None)


@pytest.fixture
def view(tab: DocumentTab) -> TableView:
    assert tab.table_view is not None
    return tab.table_view


@pytest.fixture
def status(window: MainWindow) -> list[str]:
    messages: list[str] = []
    window.statusBar().messageChanged.connect(messages.append)
    return messages


def names(tab: DocumentTab) -> list[str]:
    return [c.name for c in tab.document.tables[0].columns]


def editor(view: TableView) -> InlineEditor:
    field = view.grouped_header.editor
    assert field is not None and not field.is_closed
    return field


def header_point(view: TableView, section: int) -> QPoint:
    return view.grouped_header.name_rect(section).center()


def double_click(qtbot: QtBot, view: TableView, section: int) -> InlineEditor:
    header = view.grouped_header
    qtbot.mouseDClick(header.viewport(), Qt.MouseButton.LeftButton, pos=header_point(view, section))
    return editor(view)


def type_name(qtbot: QtBot, field: InlineEditor, text: str, key: Qt.Key) -> None:
    field.selectAll()
    qtbot.keyClicks(field, text)
    if key == Qt.Key.Key_Backtab:
        qtbot.keyClick(field, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
    else:
        qtbot.keyClick(field, key)


def test_double_click_opens_a_field_over_the_name(
    qtbot: QtBot, view: TableView, tab: DocumentTab
) -> None:
    field = double_click(qtbot, view, 3)
    assert field.text() == "Drug" and field.selectedText() == "Drug"
    name_rect = view.grouped_header.name_rect(3)
    assert field.geometry() == name_rect
    # the role line under the name stays visible
    assert field.geometry().bottom() <= view.grouped_header.role_line_top()


def test_enter_keeps_the_name_as_one_undo_step(
    qtbot: QtBot, view: TableView, tab: DocumentTab
) -> None:
    type_name(qtbot, double_click(qtbot, view, 3), "Drug A", Qt.Key.Key_Return)
    assert view.grouped_header.editor is not None and view.grouped_header.editor.is_closed
    assert names(tab)[3] == "Drug A"
    stack = tab.document.undo_stack
    assert stack.count == 1 and stack.undo_text == "Rename column 'Drug' to 'Drug A'"
    assert view.model().headerData(3, Qt.Orientation.Horizontal) == "Drug A\nY"


def test_esc_cancels(qtbot: QtBot, view: TableView, tab: DocumentTab) -> None:
    type_name(qtbot, double_click(qtbot, view, 1), "Vehicle", Qt.Key.Key_Escape)
    assert names(tab)[1] == "Control" and tab.document.undo_stack.count == 0


def test_click_elsewhere_keeps_the_name(qtbot: QtBot, view: TableView, tab: DocumentTab) -> None:
    field = double_click(qtbot, view, 1)
    field.selectAll()
    qtbot.keyClicks(field, "Vehicle")
    QApplication.sendEvent(
        field, QFocusEvent(QFocusEvent.Type.FocusOut, Qt.FocusReason.MouseFocusReason)
    )
    assert field.is_closed and names(tab)[1] == "Vehicle"


def test_tab_moves_across_several_headers(qtbot: QtBot, view: TableView, tab: DocumentTab) -> None:
    type_name(qtbot, double_click(qtbot, view, 1), "Vehicle 1", Qt.Key.Key_Tab)
    field = editor(view)
    assert field.text() == "Control 2" and field.selectedText() == "Control 2"
    assert field.geometry() == view.grouped_header.name_rect(2)
    type_name(qtbot, field, "Vehicle 2", Qt.Key.Key_Tab)
    type_name(qtbot, editor(view), "Drug", Qt.Key.Key_Tab)  # unchanged: no undo step
    type_name(qtbot, editor(view), "Drug B", Qt.Key.Key_Return)
    assert names(tab) == ["Dose", "Vehicle 1", "Vehicle 2", "Drug", "Drug B"]
    stack = tab.document.undo_stack
    assert stack.count == 3
    # one undo step per name
    tab.document.undo()
    assert names(tab) == ["Dose", "Vehicle 1", "Vehicle 2", "Drug", "Drug 2"]
    tab.document.undo()
    assert names(tab) == ["Dose", "Vehicle 1", "Control 2", "Drug", "Drug 2"]
    tab.document.undo()
    assert names(tab) == ["Dose", "Control", "Control 2", "Drug", "Drug 2"]


def test_shift_tab_moves_to_the_previous_header(
    qtbot: QtBot, view: TableView, tab: DocumentTab
) -> None:
    type_name(qtbot, double_click(qtbot, view, 2), "Vehicle 2", Qt.Key.Key_Backtab)
    field = editor(view)
    assert field.text() == "Control"
    type_name(qtbot, field, "Vehicle", Qt.Key.Key_Backtab)
    field = editor(view)
    assert field.text() == "Dose"
    type_name(qtbot, field, "Dose (µM)", Qt.Key.Key_Backtab)  # the first column: stops
    assert view.grouped_header.editor is not None and view.grouped_header.editor.is_closed
    assert names(tab)[:3] == ["Dose (µM)", "Vehicle", "Vehicle 2"]


def test_tab_on_the_last_column_keeps_the_name_and_stops(
    qtbot: QtBot, view: TableView, tab: DocumentTab
) -> None:
    type_name(qtbot, double_click(qtbot, view, 4), "Drug B", Qt.Key.Key_Tab)
    assert view.grouped_header.editor is not None and view.grouped_header.editor.is_closed
    assert names(tab) == ["Dose", "Control", "Control 2", "Drug", "Drug B"]  # no new column


def test_esc_after_tab_keeps_the_names_already_kept(
    qtbot: QtBot, view: TableView, tab: DocumentTab
) -> None:
    type_name(qtbot, double_click(qtbot, view, 1), "Vehicle", Qt.Key.Key_Tab)
    type_name(qtbot, editor(view), "Something", Qt.Key.Key_Escape)
    assert names(tab)[1:3] == ["Vehicle", "Control 2"]
    assert tab.document.undo_stack.count == 1


def test_invalid_names_keep_the_field_open(
    qtbot: QtBot, view: TableView, tab: DocumentTab, window: MainWindow
) -> None:
    field = double_click(qtbot, view, 3)
    type_name(qtbot, field, "control", Qt.Key.Key_Return)
    assert not field.is_closed and field.selectedText() == "control"
    message = "'Control' is already used in this table. Enter another name."
    assert window.statusBar().currentMessage() == message
    field.selectAll()
    qtbot.keyClick(field, Qt.Key.Key_Delete)
    qtbot.keyClick(field, Qt.Key.Key_Tab)  # Tab does not move on either
    assert editor(view) is field
    assert window.statusBar().currentMessage() == "Enter a column name."
    qtbot.keyClick(field, Qt.Key.Key_Escape)  # gives up and keeps the old name
    assert field.is_closed and names(tab)[3] == "Drug"
    assert tab.document.undo_stack.count == 0


def test_rename_column_action_edits_the_current_column(
    qtbot: QtBot, view: TableView, tab: DocumentTab, window: MainWindow
) -> None:
    action = window.registry.action("table.rename_column")
    assert action.text() == "Re&name column"  # opens no dialog: no ellipsis
    assert action.shortcut() == QKeySequence("Ctrl+Shift+R")
    view.setCurrentIndex(view.model().index(0, 2))
    action.trigger()
    field = editor(view)
    assert field.text() == "Control 2"
    type_name(qtbot, field, "Vehicle", Qt.Key.Key_Return)
    assert names(tab)[2] == "Vehicle"


def test_rename_column_from_the_right_click_menu_opens_no_dialog(
    qtbot: QtBot, view: TableView, tab: DocumentTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the real popup menu: closing the menu must not close the field."""
    asked: list[object] = []
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: asked.append(a) or ("", False))
    view.window().activateWindow()
    view.setFocus()
    qtbot.waitUntil(view.hasFocus)
    header = view.grouped_header
    pos = QPoint(header.sectionViewportPosition(2) + 10, header.height() // 2)

    def pick() -> None:
        menu = QApplication.activePopupWidget()
        assert isinstance(menu, QMenu)
        action = next(a for a in menu.actions() if a.text() == "Re&name column")
        menu.setActiveAction(action)
        qtbot.keyClick(menu, Qt.Key.Key_Return)

    QTimer.singleShot(100, pick)
    view._show_header_menu(pos)
    qtbot.waitUntil(lambda: header.editor is not None and header.editor.hasFocus())
    qtbot.wait(50)
    field = editor(view)
    assert asked == [] and field.text() == "Control 2"
    type_name(qtbot, field, "Vehicle", Qt.Key.Key_Return)
    assert names(tab)[2] == "Vehicle"


# --- group and category bands --------------------------------------------------------------


@pytest.fixture
def grouped(tab: DocumentTab, view: TableView) -> TableView:
    doc = tab.document
    doc.execute(GroupColumns("t", ["c1", "c2"], "Control", group_id="gC"))
    doc.execute(GroupColumns("t", ["d1", "d2"], "Drug", group_id="gD"))
    doc.undo_stack.clear()
    return view


def band_names(tab: DocumentTab) -> list[str]:
    return [g.name for g in tab.document.tables[0].replicate_groups]


def test_bands_rename_in_place_and_tab_moves_within_their_row(
    qtbot: QtBot, grouped: TableView, tab: DocumentTab
) -> None:
    header = grouped.grouped_header
    band = header.band_rect(grouped.model().band_at(0, 1), 0)  # type: ignore[arg-type]
    qtbot.mouseDClick(header.viewport(), Qt.MouseButton.LeftButton, pos=band.center())
    field = editor(grouped)
    assert field.text() == "Control"
    type_name(qtbot, field, "Vehicle", Qt.Key.Key_Tab)
    field = editor(grouped)
    assert field.text() == "Drug"  # the next band in the same row
    type_name(qtbot, field, "Drug A", Qt.Key.Key_Tab)  # the last band: stops
    assert header.editor is not None and header.editor.is_closed
    assert band_names(tab) == ["Vehicle", "Drug A"]
    assert tab.document.undo_stack.count == 2
    assert tab.document.undo_stack.undo_text == "Rename group 'Drug' to 'Drug A'"
    tab.document.undo()
    assert band_names(tab) == ["Vehicle", "Drug"]


def test_band_esc_and_invalid_name(
    qtbot: QtBot, grouped: TableView, tab: DocumentTab, window: MainWindow
) -> None:
    assert grouped.rename_band(NodeKind.GROUP, "gD")
    field = editor(grouped)
    type_name(qtbot, field, "control", Qt.Key.Key_Return)
    assert not field.is_closed
    assert window.statusBar().currentMessage() == (
        "There is already a replicate group called 'Control'. Enter another name."
    )
    type_name(qtbot, field, "x", Qt.Key.Key_Escape)
    assert band_names(tab) == ["Control", "Drug"] and tab.document.undo_stack.count == 0
