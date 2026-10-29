"""Tests for the data table view: editing, navigation, copy and paste, undo, the header menu."""

import math
from collections.abc import Iterator

import numpy as np
import pytest
from PySide6.QtCore import QItemSelection, QItemSelectionModel, Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget
from pytestqt.qtbot import QtBot

from curvis.core import AddTable, Document
from curvis.data import Column, ColumnRole, Table
from curvis.ui.main_window import MainWindow
from curvis.ui.table import SPARE_COLUMNS, SPARE_ROWS, TableModel, TableView


@pytest.fixture
def doc() -> Document:
    d = Document()
    d.execute(
        AddTable(
            Table(
                "Data 1",
                [
                    Column.new("Dose", ColumnRole.X, [1, 2, 3]),
                    Column.new("Resp", ColumnRole.Y, [10.5, 20, math.nan]),
                    Column.new("Label", ColumnRole.TEXT, ["a", "b", None]),
                ],
            )
        )
    )
    d.undo_stack.clear()
    return d


@pytest.fixture
def view(qtbot: QtBot, doc: Document) -> Iterator[TableView]:
    model = TableModel(doc, doc.tables[0].id, decimal_places=2)
    v = TableView(model)
    model.setParent(v)
    qtbot.addWidget(v)
    v.resize(800, 600)
    v.show()
    qtbot.waitExposed(v)
    v.activateWindow()
    v.setFocus()
    yield v


def values(doc: Document, name: str) -> list[object]:
    column = doc.tables[0].column_by_name(name)
    assert column is not None
    return [None if isinstance(v, float) and math.isnan(v) else v for v in column.values.tolist()]


def focus(view: TableView) -> QWidget:
    widget = QApplication.focusWidget()
    return widget if widget is not None else view


def key(qtbot: QtBot, view: TableView, k: Qt.Key, mods: Qt.KeyboardModifier | None = None) -> None:
    qtbot.keyClick(focus(view), k, mods if mods is not None else Qt.KeyboardModifier.NoModifier)


def type_text(qtbot: QtBot, view: TableView, text: str) -> None:
    for ch in text:
        qtbot.keyClicks(focus(view), ch)


def go(view: TableView, row: int, col: int) -> None:
    view.selectionModel().setCurrentIndex(
        view.model().index(row, col), QItemSelectionModel.SelectionFlag.ClearAndSelect
    )


def current(view: TableView) -> tuple[int, int]:
    return view.currentIndex().row(), view.currentIndex().column()


def select(view: TableView, *blocks: tuple[int, int, int, int]) -> None:
    model = view.model()
    selection = QItemSelection()
    for top, left, bottom, right in blocks:
        selection.select(model.index(top, left), model.index(bottom, right))
    view.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
    view.selectionModel().setCurrentIndex(
        model.index(blocks[0][0], blocks[0][1]), QItemSelectionModel.SelectionFlag.NoUpdate
    )


# --- display -----------------------------------------------------------------------


def test_model_shows_values_spare_cells_and_headers(view: TableView) -> None:
    model = view.model()
    assert model.rowCount() == 3 + SPARE_ROWS
    assert model.columnCount() == 3 + SPARE_COLUMNS
    shown = [[model.index(r, c).data() for c in range(4)] for r in range(4)]
    assert shown == [
        ["1", "10.5", "a", None],
        ["2", "20", "b", None],
        ["3", "", None, None],
        [None, None, None, None],
    ]
    assert model.headerData(0, Qt.Orientation.Horizontal) == "Dose\nX"
    assert model.headerData(1, Qt.Orientation.Horizontal) == "Resp\nY"
    assert model.headerData(3, Qt.Orientation.Horizontal) == ""
    assert model.headerData(0, Qt.Orientation.Vertical) == "1"


def test_header_is_tall_enough_for_name_and_role(view: TableView) -> None:
    assert view.horizontalHeader().height() >= 2 * view.fontMetrics().height()


def test_numbers_are_right_aligned_and_text_left(view: TableView) -> None:
    model = view.model()
    align = Qt.ItemDataRole.TextAlignmentRole
    assert model.index(0, 0).data(align) & Qt.AlignmentFlag.AlignRight
    assert model.index(0, 2).data(align) & Qt.AlignmentFlag.AlignLeft


def test_decimal_places_apply_to_calculated_columns_only(view: TableView) -> None:
    model = view.model()
    model.set_is_calculated(lambda column: column.name == "Resp")
    assert model.index(0, 1).data() == "10.50"
    assert model.index(0, 0).data() == "1"  # typed column: as entered
    assert model.index(0, 1).data(Qt.ItemDataRole.EditRole) == "10.5"  # full precision
    model.set_decimal_places(0)
    assert model.index(0, 1).data() == "10"  # 10.5 rounds half to even


def test_decimal_places_setting_reaches_open_tables(window: MainWindow) -> None:
    tab = window.new_document()
    assert tab.table_view is not None
    model = tab.table_view.model()
    model.set_is_calculated(lambda _column: True)
    model.write_block(0, 0, [["1.23456"]])
    window.settings.decimal_places = 3
    assert model.decimal_places == 3
    assert model.index(0, 0).data() == "1.235"


# --- typing and navigation ---------------------------------------------------------


def test_typing_replaces_the_cell_and_enter_moves_down(
    qtbot: QtBot, view: TableView, doc: Document
) -> None:
    go(view, 0, 1)
    type_text(qtbot, view, "42")
    key(qtbot, view, Qt.Key.Key_Return)
    assert values(doc, "Resp") == [42.0, 20.0, None]
    assert current(view) == (1, 1)
    assert doc.undo_stack.undo_text == "Set cell in column 'Resp', row 1"


def test_tab_moves_right_and_shift_tab_left(qtbot: QtBot, view: TableView, doc: Document) -> None:
    go(view, 1, 0)
    type_text(qtbot, view, "7")
    key(qtbot, view, Qt.Key.Key_Tab)
    type_text(qtbot, view, "8")
    key(qtbot, view, Qt.Key.Key_Tab)
    assert values(doc, "Dose")[1] == 7.0
    assert values(doc, "Resp")[1] == 8.0
    assert current(view) == (1, 2)
    key(qtbot, view, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
    assert current(view) == (1, 1)
    key(qtbot, view, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert current(view) == (0, 1)


def test_arrows_save_and_move_after_typing(qtbot: QtBot, view: TableView, doc: Document) -> None:
    go(view, 0, 0)
    type_text(qtbot, view, "5")
    key(qtbot, view, Qt.Key.Key_Right)
    assert values(doc, "Dose")[0] == 5.0
    assert current(view) == (0, 1)
    key(qtbot, view, Qt.Key.Key_Down)
    assert current(view) == (1, 1)


def test_f2_edits_in_place_and_arrows_move_the_text_cursor(
    qtbot: QtBot, view: TableView, doc: Document
) -> None:
    go(view, 0, 1)
    key(qtbot, view, Qt.Key.Key_F2)
    editor = focus(view)
    assert isinstance(editor, QLineEdit)
    assert editor.text() == "10.5"
    key(qtbot, view, Qt.Key.Key_End)
    key(qtbot, view, Qt.Key.Key_Left)
    type_text(qtbot, view, "9")
    assert current(view) == (0, 1)  # still editing the same cell
    key(qtbot, view, Qt.Key.Key_Return)
    assert values(doc, "Resp")[0] == 10.95


def test_escape_cancels_the_edit(qtbot: QtBot, view: TableView, doc: Document) -> None:
    go(view, 0, 0)
    type_text(qtbot, view, "99")
    key(qtbot, view, Qt.Key.Key_Escape)
    assert values(doc, "Dose")[0] == 1.0
    assert doc.undo_stack.count == 0


def test_text_in_a_number_column_is_refused(qtbot: QtBot, view: TableView, doc: Document) -> None:
    messages: list[str] = []
    view.error.connect(messages.append)
    go(view, 0, 0)
    type_text(qtbot, view, "abc")
    key(qtbot, view, Qt.Key.Key_Return)
    assert values(doc, "Dose")[0] == 1.0
    assert messages == ["'abc' is not a number."]
    assert doc.undo_stack.count == 0


def test_typing_below_the_data_adds_rows(qtbot: QtBot, view: TableView, doc: Document) -> None:
    go(view, 5, 1)
    type_text(qtbot, view, "1")
    key(qtbot, view, Qt.Key.Key_Return)
    assert doc.tables[0].n_rows == 6
    assert values(doc, "Resp")[3:] == [None, None, 1.0]
    assert view.model().rowCount() == 6 + SPARE_ROWS
    assert current(view) == (6, 1)
    doc.undo()
    assert doc.tables[0].n_rows == 3
    assert view.model().rowCount() == 3 + SPARE_ROWS


def test_typing_in_a_spare_column_adds_a_y_column(
    qtbot: QtBot, view: TableView, doc: Document
) -> None:
    go(view, 0, 4)  # two columns past the last: the new column goes right after it
    type_text(qtbot, view, "3")
    key(qtbot, view, Qt.Key.Key_Return)
    table = doc.tables[0]
    assert [c.name for c in table.columns] == ["Dose", "Resp", "Label", "A"]
    assert table.columns[3].role == ColumnRole.Y
    assert values(doc, "A") == [3.0, None, None]
    assert view.model().headerData(3, Qt.Orientation.Horizontal) == "A\nY"
    doc.undo()
    assert len(table.columns) == 3
    assert not doc.undo_stack.can_undo


def test_delete_clears_the_selection_in_one_step(
    qtbot: QtBot, view: TableView, doc: Document
) -> None:
    select(view, (0, 0, 1, 1), (2, 2, 2, 2))
    key(qtbot, view, Qt.Key.Key_Delete)
    assert values(doc, "Dose") == [None, None, 3.0]
    assert values(doc, "Resp") == [None, None, None]
    assert values(doc, "Label") == ["a", "b", None]
    assert doc.undo_stack.count == 1
    assert doc.undo_stack.undo_text == "Clear 5 cells"
    doc.undo()
    assert values(doc, "Dose") == [1.0, 2.0, 3.0]


def test_delete_while_editing_deletes_text_not_cells(qtbot: QtBot, window: MainWindow) -> None:
    tab = window.new_document()
    view = tab.table_view
    assert view is not None
    view.setFocus()
    go(view, 0, 1)
    type_text(qtbot, view, "12")
    key(qtbot, view, Qt.Key.Key_Left)  # in enter mode the arrow saves and moves
    go(view, 0, 1)
    key(qtbot, view, Qt.Key.Key_F2)
    key(qtbot, view, Qt.Key.Key_Home)
    key(qtbot, view, Qt.Key.Key_Delete)
    key(qtbot, view, Qt.Key.Key_Return)
    assert tab.document.tables[0].columns[1].values.tolist() == [2.0]


# --- copy and paste ----------------------------------------------------------------


def test_copy_a_block_as_tab_separated_text(qtbot: QtBot, view: TableView) -> None:
    select(view, (0, 1, 2, 2))
    key(qtbot, view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == "10.5\ta\r\n20\tb\r\n\t\r\n"


def test_copy_uses_full_precision_and_leaves_out_spare_cells(view: TableView) -> None:
    model = view.model()
    model.write_block(0, 1, [[str(1 / 3)]])
    model.set_is_calculated(lambda _column: True)
    view.select_columns(1, 1)
    assert view.selection_tsv() == "0.3333333333333333\r\n20\r\n\r\n"


def test_copy_several_blocks_as_their_bounding_box(view: TableView) -> None:
    select(view, (0, 0, 0, 0), (1, 2, 1, 2))
    assert view.selection_tsv() == "1\t\t\r\n\t\tb\r\n"


def test_paste_a_block_is_one_undo_step(qtbot: QtBot, view: TableView, doc: Document) -> None:
    QApplication.clipboard().setText("7\t70\r\n8\t80\r\n")
    go(view, 1, 0)
    key(qtbot, view, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert values(doc, "Dose") == [1.0, 7.0, 8.0]
    assert values(doc, "Resp") == [10.5, 70.0, 80.0]
    assert doc.undo_stack.count == 1
    assert doc.undo_stack.undo_text == "Paste 4 cells from row 2"
    sel = view.selected_ranges()
    assert sel == [(1, 0, 2, 1)]
    doc.undo()
    assert values(doc, "Dose") == [1.0, 2.0, 3.0]
    assert values(doc, "Resp") == [10.5, 20.0, None]
    doc.redo()
    assert values(doc, "Resp") == [10.5, 70.0, 80.0]


def test_paste_past_the_edges_adds_rows_and_columns(view: TableView, doc: Document) -> None:
    go(view, 2, 2)
    assert view.paste("x\t1\t2\r\ny\t3\t4\r\n")
    table = doc.tables[0]
    assert [c.name for c in table.columns] == ["Dose", "Resp", "Label", "A", "B"]
    assert table.n_rows == 4
    assert values(doc, "Label") == ["a", "b", "x", "y"]
    assert values(doc, "B") == [None, None, 2.0, 4.0]
    assert view.model().columnCount() == 5 + SPARE_COLUMNS
    assert doc.undo_stack.count == 1
    doc.undo()
    assert [c.name for c in table.columns] == ["Dose", "Resp", "Label"]
    assert table.n_rows == 3
    assert view.model().columnCount() == 3 + SPARE_COLUMNS


def test_paste_with_text_in_a_number_column_changes_nothing(view: TableView, doc: Document) -> None:
    messages: list[str] = []
    view.error.connect(messages.append)
    go(view, 0, 0)
    assert not view.paste("Dose\tResp\r\n5\t6\r\n")
    assert messages == ["'Dose' is not a number."]
    assert values(doc, "Dose") == [1.0, 2.0, 3.0]
    assert doc.undo_stack.count == 0


def test_copy_and_paste_round_trip(view: TableView, doc: Document) -> None:
    select(view, (0, 0, 2, 1))
    view.copy_selection()
    go(view, 0, 3)
    assert view.paste()
    table = doc.tables[0]
    np.testing.assert_array_equal(table.columns[3].values, table.columns[0].values)
    np.testing.assert_array_equal(table.columns[4].values, table.columns[1].values)


def test_copy_and_paste_through_the_edit_menu(window: MainWindow) -> None:
    tab = window.new_document()
    view = tab.table_view
    assert view is not None
    view.model().write_block(0, 0, [["1", "2"]])
    view.selectRow(0)
    window.registry.action("edit.copy").trigger()
    go(view, 1, 0)
    window.registry.action("edit.paste").trigger()
    assert tab.document.tables[0].columns[1].values.tolist() == [2.0, 2.0]


# --- undo reaches the view ---------------------------------------------------------


def test_undo_and_redo_update_the_view(view: TableView, doc: Document) -> None:
    model = view.model()
    model.write_block(0, 1, [["99"]])
    assert model.index(0, 1).data() == "99"
    doc.undo()
    assert model.index(0, 1).data() == "10.5"
    doc.redo()
    assert model.index(0, 1).data() == "99"


def test_edits_are_logged(view: TableView, doc: Document) -> None:
    view.model().write_block(0, 0, [["5"]])
    assert doc.change_log.entries[-1].command_type == "SetCellValue"


# --- header menu -------------------------------------------------------------------


def menu_action(view: TableView, section: int, text: str) -> None:
    menu = view.header_menu(section)
    for action in menu.actions():
        if action.menu() is not None:
            for sub in action.menu().actions():
                if sub.text() == text:
                    sub.trigger()
                    return
        if action.text().replace("&", "") == text:
            assert action.isEnabled()
            action.trigger()
            return
    raise AssertionError(f"no {text!r} in the header menu")


def header(view: TableView, section: int) -> str:
    return view.model().headerData(section, Qt.Orientation.Horizontal)


def test_header_menu_inserts_columns(view: TableView, doc: Document) -> None:
    menu_action(view, 1, "Insert column left")
    menu_action(view, 3, "Insert column right")
    assert [c.name for c in doc.tables[0].columns] == ["Dose", "A", "Resp", "Label", "B"]
    assert header(view, 1) == "A\nY"
    assert values(doc, "A") == [None, None, None]
    doc.undo()
    doc.undo()
    assert header(view, 1) == "Resp\nY"


def test_header_menu_on_a_spare_column_adds_a_column(view: TableView, doc: Document) -> None:
    menu_action(view, 4, "Add column")
    assert [c.name for c in doc.tables[0].columns] == ["Dose", "Resp", "Label", "A"]


def test_header_menu_renames_a_column(
    view: TableView, doc: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(view, "_ask_column_name", lambda current: "Response")
    menu_action(view, 1, "Rename column…")
    assert header(view, 1) == "Response\nY"
    doc.undo()
    assert header(view, 1) == "Resp\nY"


def test_rename_to_a_taken_name_is_refused(
    view: TableView, doc: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages: list[str] = []
    view.error.connect(messages.append)
    monkeypatch.setattr(view, "_ask_column_name", lambda current: "dose")
    menu_action(view, 1, "Rename column…")
    assert messages == ["There is already a column called 'Dose'."]
    assert header(view, 1) == "Resp\nY"


def test_header_menu_deletes_selected_columns_in_one_step(view: TableView, doc: Document) -> None:
    select(view, (0, 0, 0, 1))
    menu_action(view, 1, "Delete 2 columns")
    assert [c.name for c in doc.tables[0].columns] == ["Label"]
    assert header(view, 0) == "Label\nText"
    assert doc.undo_stack.count == 1
    doc.undo()
    assert [c.name for c in doc.tables[0].columns] == ["Dose", "Resp", "Label"]
    assert header(view, 1) == "Resp\nY"


def test_header_menu_sets_the_role(view: TableView, doc: Document) -> None:
    menu = view.header_menu(1)
    roles = next(a.menu() for a in menu.actions() if a.menu() is not None)
    checked = [a.text() for a in roles.actions() if a.isChecked()]
    assert checked == ["Y"]
    menu_action(view, 1, "Error")
    assert doc.tables[0].columns[1].role == ColumnRole.ERROR
    assert header(view, 1) == "Resp\nError"
    doc.undo()
    assert header(view, 1) == "Resp\nY"


def test_role_change_to_text_shows_numbers_as_text(view: TableView, doc: Document) -> None:
    menu_action(view, 0, "Text")
    assert view.model().index(0, 0).data() == "1"
    assert view.model().index(0, 0).data(Qt.ItemDataRole.TextAlignmentRole) & (
        Qt.AlignmentFlag.AlignLeft
    )


# --- document tab and main window --------------------------------------------------


def test_new_project_shows_an_empty_table(window: MainWindow) -> None:
    tab = window.new_document()
    assert tab.table_view is not None
    assert not tab.placeholder.isVisible()
    model = tab.table_view.model()
    assert model.headerData(0, Qt.Orientation.Horizontal) == "X\nX"
    assert model.headerData(1, Qt.Orientation.Horizontal) == "A\nY"
    assert model.rowCount() == SPARE_ROWS
    assert not tab.is_modified


def test_refused_edits_show_in_the_status_bar(window: MainWindow) -> None:
    tab = window.new_document()
    assert tab.table_view is not None
    tab.table_view.model().write_block(0, 0, [["abc"]])
    assert window.statusBar().currentMessage() == "'abc' is not a number."


def test_table_actions_are_in_the_command_palette(window: MainWindow) -> None:
    def ids() -> set[str]:
        return {entry.id for entry in window.registry.entries()}

    assert "edit.copy" not in ids()  # made with the first document, not for the launcher
    window.new_document()
    assert {"edit.copy", "edit.paste", "edit.clear", "table.rename_column"} <= ids()
    assert {f"table.role.{role.name.lower()}" for role in ColumnRole} <= ids()
    assert window.registry.action("edit.copy").isEnabled()
    window.close_current()
    assert not window.registry.action("edit.copy").isEnabled()  # no table open
    window.new_document()  # the actions are made only once


def test_table_menu_and_edit_menu_entries(window: MainWindow) -> None:
    window.new_document()
    menus = {a.text().replace("&", ""): a.menu() for a in window.menuBar().actions()}
    assert list(menus) == ["File", "Edit", "Table", "View"]
    edit = [a.text().replace("&", "") for a in menus["Edit"].actions()]
    assert edit[edit.index("Copy") : edit.index("Copy") + 3] == ["Copy", "Paste", "Clear cells"]
    assert edit[-1] == "Settings…"


def test_set_role_action_acts_on_the_current_column(window: MainWindow) -> None:
    tab = window.new_document()
    assert tab.table_view is not None
    go(tab.table_view, 0, 1)
    window.registry.action("table.role.error").trigger()
    assert tab.document.tables[0].columns[1].role == ColumnRole.ERROR


def test_empty_columns_can_be_deleted(window: MainWindow) -> None:
    tab = window.new_document()
    assert tab.table_view is not None
    go(tab.table_view, 0, 1)
    window.registry.action("table.delete_columns").trigger()
    assert [c.name for c in tab.document.tables[0].columns] == ["X"]


def test_view_paints_cells_selection_and_current_cell(view: TableView) -> None:
    select(view, (0, 0, 1, 1))
    image = view.viewport().grab().toImage()
    assert not image.isNull()
    base = view.palette().color(view.palette().ColorRole.Base)
    highlight = view.palette().color(view.palette().ColorRole.Highlight)
    x = view.columnViewportPosition(1) + 3
    y = view.rowViewportPosition(1) + 3
    assert image.pixelColor(x, y) == highlight
    y_unselected = view.rowViewportPosition(5) + 3
    assert image.pixelColor(x, y_unselected) == base


def test_long_numbers_are_rounded_to_fit_but_kept_in_full(view: TableView) -> None:
    model = view.model()
    model.write_block(0, 1, [["107.64052345967664"]])
    width = (view.columnWidth(1) - 8) // view.fontMetrics().horizontalAdvance("0")
    shown = model.display_texts(1, 0, 1, width)[0]
    assert len(shown) <= width and shown.startswith("107.6")
    assert model.index(0, 1).data(Qt.ItemDataRole.EditRole) == "107.64052345967664"
    assert model.index(0, 1).data(Qt.ItemDataRole.ToolTipRole) == "107.64052345967664"
