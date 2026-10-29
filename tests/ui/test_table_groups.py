"""Tests for groups in the table view: the grouped header, drag and drop, bulk rename."""

from collections.abc import Callable, Iterator

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QDialogButtonBox
from pytestqt.qtbot import QtBot

from curvis.core import AddTable, Document, GroupColumns, SetCategory
from curvis.data import Column, ColumnRole, NodeKind, Table
from curvis.ui.main_window import MainWindow
from curvis.ui.table import TableModel, TableView
from curvis.ui.table.bulk_rename import BulkRenameDialog


@pytest.fixture
def doc() -> Document:
    d = Document()
    columns = [Column.new("Dose", ColumnRole.X, [1, 2, 3], id="x")]
    columns += [
        Column.new(n, ColumnRole.Y, [1, 2, 3], id=n) for n in ["C1", "C2", "C3", "T1", "T2", "Z"]
    ]
    d.execute(AddTable(Table("Data 1", columns, id="t")))
    d.undo_stack.clear()
    return d


@pytest.fixture
def view(qtbot: QtBot, doc: Document) -> Iterator[TableView]:
    model = TableModel(doc, "t")
    v = TableView(model)
    model.setParent(v)
    qtbot.addWidget(v)
    v.resize(1000, 400)
    v.show()
    qtbot.waitExposed(v)
    yield v


@pytest.fixture
def grouped(doc: Document, view: TableView) -> TableView:
    """Control (C1-C3) and Treated (T1, T2) in category Female; Dose and Z ungrouped."""
    doc.execute(GroupColumns("t", ["C1", "C2", "C3"], "Control"))
    doc.execute(GroupColumns("t", ["T1", "T2"], "Treated"))
    ids = [g.id for g in doc.tables[0].replicate_groups]
    doc.execute(SetCategory("t", ids, "Female"))
    doc.undo_stack.clear()
    return view


def order(doc: Document) -> list[str]:
    return [c.id for c in doc.tables[0].columns]


def names(doc: Document) -> list[str]:
    return [c.name for c in doc.tables[0].columns]


def group_names(doc: Document) -> dict[str, list[str]]:
    t = doc.tables[0]
    return {g.name: [c.id for c in t.group_columns(g.id)] for g in t.replicate_groups}


def answer(view: TableView, monkeypatch: pytest.MonkeyPatch, *answers: str | None) -> list[str]:
    """Script the view's text prompts; returns the prompt titles asked."""
    asked: list[str] = []
    queue = list(answers)

    def ask(_self: TableView, title: str, _label: str, _current: str) -> str | None:
        asked.append(title)
        return queue.pop(0)

    monkeypatch.setattr(TableView, "_ask_text", ask)
    return asked


# --- header -------------------------------------------------------------------------


def section_center(view: TableView, col: int, band_row: int | None = None) -> QPoint:
    """Point in the header above column ``col``: in a band row, or in the column's own box."""
    header = view.grouped_header
    x = header.sectionViewportPosition(col) + header.sectionSize(col) // 2
    if band_row is None:
        y = header.height() - header.band_height // 2
    else:
        y = band_row * header.band_height + header.band_height // 2
    return QPoint(x, y)


def drag(qtbot: QtBot, view: TableView, start: QPoint, end: QPoint) -> None:
    target = view.grouped_header.viewport()
    qtbot.mousePress(target, Qt.MouseButton.LeftButton, pos=start)
    middle = QPoint((start.x() + end.x()) // 2, start.y())
    qtbot.mouseMove(target, middle)
    qtbot.mouseMove(target, end)
    qtbot.mouseRelease(target, Qt.MouseButton.LeftButton, pos=end)


def boundary_point(view: TableView, boundary: int, y: int) -> QPoint:
    """A point just right of the line left of column ``boundary``."""
    return QPoint(view.grouped_header.boundary_x(boundary) + 3, y)


def test_header_shows_bands_only_when_used(view: TableView, doc: Document) -> None:
    header = view.grouped_header
    plain = header.sizeHint().height()
    assert view.model().bands() == []
    doc.execute(GroupColumns("t", ["C1", "C2"], "Control"))
    assert len(view.model().bands()) == 1
    assert header.sizeHint().height() == plain + header.band_height
    assert view.horizontalHeader().height() == header.sizeHint().height()
    gid = doc.tables[0].replicate_groups[0].id
    doc.execute(SetCategory("t", [gid], "Female"))
    bands = view.model().bands()
    assert len(bands) == 2
    category, group = bands[0][1], bands[1][1]
    assert category is not None and category.name == "Female"
    assert group is not None and (group.name, group.start, group.end) == ("Control", 1, 3)
    assert bands[0][0] is None and bands[1][0] is None  # Dose has no group
    assert view.model().band_tooltip(category) == "Category: Female"
    assert view.model().band_tooltip(group) == "Replicate group 'Control' (2 columns)"
    doc.undo()
    doc.undo()
    assert view.model().bands() == []
    assert header.sizeHint().height() == plain
    view.grab()  # paints without errors


def test_band_at(grouped: TableView) -> None:
    header = grouped.grouped_header
    band = header.band_at(section_center(grouped, 2, band_row=1))
    assert band is not None and band.name == "Control"
    band = header.band_at(section_center(grouped, 4, band_row=0))
    assert band is not None and band.name == "Female"
    assert header.band_at(section_center(grouped, 0, band_row=0)) is None
    assert header.band_at(section_center(grouped, 2)) is None  # the column's own box


def test_clicking_a_band_selects_its_columns(qtbot: QtBot, grouped: TableView) -> None:
    target = grouped.grouped_header.viewport()
    qtbot.mouseClick(target, Qt.MouseButton.LeftButton, pos=section_center(grouped, 4, 1))
    assert grouped.selected_columns() == [4, 5]
    qtbot.mouseClick(target, Qt.MouseButton.LeftButton, pos=section_center(grouped, 1, 0))
    assert grouped.selected_columns() == [1, 2, 3, 4, 5]


def test_double_clicking_a_band_renames_it(
    qtbot: QtBot, grouped: TableView, doc: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = answer(grouped, monkeypatch, "Vehicle", "Male")
    target = grouped.grouped_header.viewport()
    qtbot.mouseDClick(target, Qt.MouseButton.LeftButton, pos=section_center(grouped, 2, 1))
    qtbot.mouseDClick(target, Qt.MouseButton.LeftButton, pos=section_center(grouped, 2, 0))
    assert asked == ["Rename replicate group", "Rename category"]
    t = doc.tables[0]
    assert [g.name for g in t.replicate_groups] == ["Vehicle", "Treated"]
    assert t.category_groups[0].name == "Male"
    band = grouped.model().band_at(1, 2)
    assert band is not None and band.name == "Vehicle"


# --- drag and drop ------------------------------------------------------------------


def test_drag_a_selected_column(qtbot: QtBot, view: TableView, doc: Document) -> None:
    view.select_columns(6, 6)  # Z
    y = section_center(view, 6).y()
    drag(qtbot, view, section_center(view, 6), boundary_point(view, 1, y))
    assert order(doc) == ["x", "Z", "C1", "C2", "C3", "T1", "T2"]
    assert view.selected_columns() == [1]  # the moved column stays selected
    assert doc.change_log[-1].summary == "Move column 'Z'"
    doc.undo()
    assert order(doc) == ["x", "C1", "C2", "C3", "T1", "T2", "Z"]


def test_drag_on_an_unselected_column_selects_a_range(
    qtbot: QtBot, view: TableView, doc: Document
) -> None:
    view.select_columns(0, 0)
    drag(qtbot, view, section_center(view, 2), section_center(view, 4))
    assert order(doc) == ["x", "C1", "C2", "C3", "T1", "T2", "Z"]
    assert view.selected_columns() == [2, 3, 4]
    assert not doc.undo_stack.can_undo


def test_a_replicate_stays_in_its_group(qtbot: QtBot, grouped: TableView, doc: Document) -> None:
    grouped.select_columns(1, 1)  # C1
    y = section_center(grouped, 1).y()
    # Dropped far right (past the group): it lands at the end of its group.
    drag(qtbot, grouped, section_center(grouped, 1), boundary_point(grouped, 6, y))
    assert order(doc) == ["x", "C2", "C3", "C1", "T1", "T2", "Z"]
    assert group_names(doc)["Control"] == ["C2", "C3", "C1"]
    doc.tables[0].check()


def test_drop_indicator_snaps(qtbot: QtBot, grouped: TableView) -> None:
    header = grouped.grouped_header
    grouped.select_columns(6, 6)  # Z
    target = header.viewport()
    y = section_center(grouped, 6).y()
    qtbot.mousePress(target, Qt.MouseButton.LeftButton, pos=section_center(grouped, 6))
    qtbot.mouseMove(target, boundary_point(grouped, 3, y))  # inside category Female
    assert header._drag is not None and header._drag.active
    assert header._drag.boundary == 1  # nearest edge of the category
    grouped.grab()  # paints the drop line
    qtbot.mouseRelease(target, Qt.MouseButton.LeftButton, pos=boundary_point(grouped, 3, y))
    assert order(grouped.model().document)[1] == "Z"


def test_drag_a_group(qtbot: QtBot, grouped: TableView, doc: Document) -> None:
    y = section_center(grouped, 4, 1).y()
    drag(qtbot, grouped, section_center(grouped, 4, 1), boundary_point(grouped, 1, y))
    assert order(doc) == ["x", "T1", "T2", "C1", "C2", "C3", "Z"]
    assert grouped.selected_columns() == [1, 2]
    assert doc.change_log[-1].summary == "Move group 'Treated'"
    doc.undo()
    assert order(doc) == ["x", "C1", "C2", "C3", "T1", "T2", "Z"]
    doc.redo()
    assert order(doc) == ["x", "T1", "T2", "C1", "C2", "C3", "Z"]


def test_drag_a_category(qtbot: QtBot, grouped: TableView, doc: Document) -> None:
    y = section_center(grouped, 2, 0).y()
    drag(
        qtbot, grouped, section_center(grouped, 2, 0), boundary_point(grouped, 7, y) - QPoint(6, 0)
    )
    assert order(doc) == ["x", "Z", "C1", "C2", "C3", "T1", "T2"]
    assert doc.change_log[-1].summary == "Move category 'Female'"
    assert grouped.selected_columns() == [2, 3, 4, 5, 6]
    doc.undo()
    assert order(doc) == ["x", "C1", "C2", "C3", "T1", "T2", "Z"]


def test_click_on_selected_column_without_drag_selects_it(
    qtbot: QtBot, view: TableView, doc: Document
) -> None:
    view.select_columns(1, 3)
    qtbot.mouseClick(
        view.grouped_header.viewport(), Qt.MouseButton.LeftButton, pos=section_center(view, 2)
    )
    assert view.selected_columns() == [2]
    assert not doc.undo_stack.can_undo


# --- group actions ------------------------------------------------------------------


def test_group_and_ungroup(view: TableView, doc: Document, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = answer(view, monkeypatch, "Control")
    view.select_columns(1, 3)
    assert view.group_selected()
    assert asked == ["Group as replicates"]
    assert group_names(doc) == {"Control": ["C1", "C2", "C3"]}
    assert view.selected_columns() == [1, 2, 3]
    view.select_columns(2, 2)
    assert view.ungroup_selected()
    assert group_names(doc) == {"Control": ["C1", "C3"]}
    assert order(doc)[1:4] == ["C1", "C3", "C2"]
    doc.undo()
    doc.undo()
    assert group_names(doc) == {}


def test_grouping_an_x_column_is_refused(
    qtbot: QtBot, view: TableView, doc: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer(view, monkeypatch, "G")
    view.select_columns(0, 1)
    with qtbot.waitSignal(view.error) as blocker:
        assert not view.group_selected()
    assert "Only Y columns" in blocker.args[0]
    assert group_names(doc) == {}


def test_set_category_groups_single_columns(
    view: TableView, doc: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(TableView, "_ask_category", lambda self, current, existing: "Male")
    view.select_columns(5, 6)  # T2 and Z, not in groups
    assert view.set_category_of_selected()
    t = doc.tables[0]
    assert group_names(doc) == {"T2": ["T2"], "Z": ["Z"]}
    assert [c.name for c in t.category_groups] == ["Male"]
    doc.undo()  # one step
    assert group_names(doc) == {} and t.category_groups == []


def test_remove_category_and_rename_factor(
    grouped: TableView, doc: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer(grouped, monkeypatch, "Sex")
    assert grouped.rename_category_factor()
    assert doc.tables[0].category_factor == "Sex"
    grouped.select_columns(4, 4)
    assert grouped.remove_category_of_selected()
    t = doc.tables[0]
    assert [
        t.replicate_group(g).category_id is None for g in [x.id for x in t.replicate_groups]
    ] == [
        False,
        True,
    ]


def test_inserting_beside_a_replicate_adds_a_replicate(grouped: TableView, doc: Document) -> None:
    grouped.select_columns(2, 2)
    assert grouped.insert_column_right()
    assert len(group_names(doc)["Control"]) == 4
    grouped.select_columns(0, 0)
    assert grouped.insert_column_right()  # beside Dose: not in a group
    assert len(group_names(doc)["Control"]) == 4
    doc.tables[0].check()


def test_header_menu_has_group_actions(grouped: TableView) -> None:
    menu = grouped.header_menu(2)
    texts = [a.text() for a in menu.actions()]
    for text in ["&Group as replicates…", "&Ungroup", "Set &category…", "&Bulk rename…"]:
        assert text in texts
    remove = next(a for a in menu.actions() if a.text() == "Remove from categor&y")
    assert remove.isEnabled()
    menu = grouped.header_menu(0)  # Dose: an X column
    group = next(a for a in menu.actions() if a.text() == "&Group as replicates…")
    assert not group.isEnabled()


# --- bulk rename --------------------------------------------------------------------


def run_bulk_rename(
    view: TableView, monkeypatch: pytest.MonkeyPatch, fill: Callable[[BulkRenameDialog], None]
) -> bool:
    def exec_dialog(_self: TableView, dialog: BulkRenameDialog) -> bool:
        fill(dialog)
        ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if not ok.isEnabled():
            return False
        ok.click()
        return dialog.result() == BulkRenameDialog.DialogCode.Accepted

    monkeypatch.setattr(TableView, "_exec_dialog", exec_dialog)
    return view.bulk_rename()


def test_bulk_find_and_replace(
    grouped: TableView, doc: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fill(dialog: BulkRenameDialog) -> None:
        dialog.find_edit.setText("c")
        dialog.replace_edit.setText("Ctrl ")
        assert dialog.renames().columns == {"C1": "Ctrl 1", "C2": "Ctrl 2", "C3": "Ctrl 3"}
        assert dialog.status.text() == "3 names will change."

    log = len(doc.change_log)
    assert run_bulk_rename(grouped, monkeypatch, fill)
    assert names(doc) == ["Dose", "Ctrl 1", "Ctrl 2", "Ctrl 3", "T1", "T2", "Z"]
    assert len(doc.change_log) == log + 1
    doc.undo()
    assert names(doc) == ["Dose", "C1", "C2", "C3", "T1", "T2", "Z"]


def test_bulk_rename_columns_and_groups_in_one_step(
    grouped: TableView, doc: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = doc.tables[0]
    control, treated = (g.id for g in t.replicate_groups)

    def fill(dialog: BulkRenameDialog) -> None:
        dialog.number_mode.setChecked(True)
        assert dialog.pages.currentIndex() == 1
        dialog.set_checked(NodeKind.COLUMN, ["C1", "C2", "C3"])
        for item, widget in dialog.items:
            if item.kind is NodeKind.GROUP:
                widget.setCheckState(0, Qt.CheckState.Checked)
        dialog.pattern_edit.setText("Rep ##")
        dialog.start_spin.setValue(1)

    assert run_bulk_rename(grouped, monkeypatch, fill)
    assert names(doc)[1:4] == ["Rep 01", "Rep 02", "Rep 03"]
    # Groups are numbered on their own.
    assert t.replicate_group(control).name == "Rep 01"
    assert t.replicate_group(treated).name == "Rep 02"
    doc.undo()  # one undo step for all of it
    assert names(doc)[1:4] == ["C1", "C2", "C3"]
    assert [g.name for g in t.replicate_groups] == ["Control", "Treated"]


def test_bulk_rename_shows_conflicts(
    view: TableView, doc: Document, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def fill(dialog: BulkRenameDialog) -> None:
        dialog.find_edit.setText("C1")
        dialog.replace_edit.setText("Z")
        seen.append(dialog.status.text())
        ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert not ok.isEnabled()
        new_names = {item.id: widget.text(1) for item, widget in dialog.items}
        assert new_names["C1"] == "Z"

    assert not run_bulk_rename(view, monkeypatch, fill)
    assert seen == ["There is already a column called 'Z'."]
    assert names(doc)[1] == "C1"


def test_bulk_rename_ticks_selected_columns(view: TableView) -> None:
    view.select_columns(2, 3)
    model = view.model()
    dialog = BulkRenameDialog(model.table, ["C2", "C3"], view)
    assert [i.id for i in dialog.checked_items()] == ["C2", "C3"]
    dialog = BulkRenameDialog(model.table, [], view)
    assert len(dialog.checked_items()) == 7  # nothing given: every column
    assert dialog.status.text() == "No names change yet."
    dialog.close()


# --- main window --------------------------------------------------------------------


def test_actions_are_in_the_palette_and_work(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    tab = window.new_document()
    assert tab.table_view is not None
    for action_id in [
        "table.group",
        "table.ungroup",
        "table.set_category",
        "table.remove_category",
        "table.rename_category_factor",
        "table.bulk_rename",
    ]:
        assert window.registry.action(action_id).isEnabled()
    assert window.registry.action("table.group").shortcut().toString() == "Ctrl+G"
    answer(tab.table_view, monkeypatch, "Group 1")
    tab.table_view.select_columns(1, 1)
    window.registry.action("table.group").trigger()
    table = tab.document.tables[0]
    assert [g.name for g in table.replicate_groups] == ["Group 1"]


# --- moving by keyboard -------------------------------------------------------------


def test_move_column_group_and_category_by_keyboard(grouped: TableView, doc: Document) -> None:
    grouped.select_columns(2, 2)  # C2, inside Control
    assert grouped.move_selected_left()
    assert order(doc)[1:4] == ["C2", "C1", "C3"]
    assert not grouped.move_selected_left()  # already first in its group
    grouped.select_columns(4, 5)  # all of Treated
    assert grouped.move_selected_left()
    assert order(doc) == ["x", "T1", "T2", "C2", "C1", "C3", "Z"]
    assert grouped.selected_columns() == [1, 2]
    grouped.select_columns(1, 5)  # all of Female
    assert grouped.move_selected_right()
    assert order(doc) == ["x", "Z", "T1", "T2", "C2", "C1", "C3"]
    assert doc.change_log[-1].summary == "Move category 'Female'"


def test_moving_a_partial_group_is_refused(qtbot: QtBot, grouped: TableView) -> None:
    grouped.select_columns(1, 2)
    with qtbot.waitSignal(grouped.error):
        assert not grouped.move_selected_right()


def test_move_actions_have_shortcuts(window: MainWindow) -> None:
    window.new_document()
    action = window.registry.action("table.move_right")
    assert action.shortcut().toString() == "Alt+Shift+Right"
    assert action.isEnabled()
