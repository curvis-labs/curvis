"""Several tables per project: the table tabs along the bottom (docs/DESIGN.md section 8)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFocusEvent, QKeySequence
from PySide6.QtWidgets import QApplication, QDialog
from pytestqt.qtbot import QtBot

from curvis.analysis.base import Analysis
from curvis.analysis.fit.analyses import LinearParams
from curvis.charts.builder import default_spec
from curvis.charts.spec import ChartFamily
from curvis.core import LAST_TABLE, AddTable, Document, SetCellValue, add_analysis, add_graph
from curvis.data import Column, ColumnRole, Table
from curvis.ui.close_table_dialog import CloseTableDialog
from curvis.ui.document_tab import DocumentTab
from curvis.ui.main_window import MainWindow
from tests.ui import test_import_dialog
from tests.ui.test_import_dialog import DATA, wait_import

importing = test_import_dialog.importing  # the fixture that scripts the Import dialog


def names(tab: DocumentTab) -> list[str]:
    return [t.name for t in tab.document.tables]


def tab_texts(tab: DocumentTab) -> list[str]:
    bar = tab.table_tabs.bar
    return [bar.tabText(i) for i in range(bar.count())]


def shown(tab: DocumentTab) -> str:
    """Name of the table shown in the table view."""
    assert tab.table_view is not None
    return tab.document.table(tab.table_view.model().table_id).name


def assay(name: str, table_id: str) -> Table:
    return Table(
        name,
        [
            Column.new("Dose", ColumnRole.X, [1, 2, 3, 4], id=f"{table_id}x"),
            Column.new("Response", ColumnRole.Y, [2, 4, 7, 8], id=f"{table_id}y"),
        ],
        id=table_id,
    )


@pytest.fixture
def project(window: MainWindow) -> DocumentTab:
    """A project with tables Viability (tV) and Survival (tS); Viability has a linear fit
    and an XY graph. Viability is selected."""
    doc = Document(name="Experiment", tables=[assay("Viability", "tV"), assay("Survival", "tS")])
    tab = window._add_tab(doc, None)
    fit = Analysis("linear_regression", "tV", ("tVy",), LinearParams(), id="fitV")
    doc.execute(add_analysis(doc, fit))
    doc.execute(add_graph(doc, default_spec(doc.table("tV"), ChartFamily.XY, doc.analyses)))
    doc.undo_stack.clear()
    return tab


class Dialogs:
    """Records the Close table warnings and answers them."""

    def __init__(self) -> None:
        self.shown: list[CloseTableDialog] = []
        self.answers: list[bool] = []

    def exec(self, dialog: QDialog) -> int:
        assert isinstance(dialog, CloseTableDialog)
        self.shown.append(dialog)
        accept = self.answers.pop(0)
        return int(QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected)


@pytest.fixture
def dialogs(monkeypatch: pytest.MonkeyPatch) -> Dialogs:
    recorder = Dialogs()
    monkeypatch.setattr(DocumentTab, "_exec_dialog", recorder.exec)
    return recorder


# --- new table -----------------------------------------------------------------------------


def test_new_table_from_the_menu_the_plus_and_the_shortcut(
    qtbot: QtBot, window: MainWindow
) -> None:
    tab = window.new_document()
    action = window.registry.action("table.new")
    assert action.shortcut() == QKeySequence("Ctrl+Shift+N")
    action.trigger()
    assert names(tab) == ["Data 1", "Table 2"]
    assert tab_texts(tab) == ["Data 1", "Table 2"]
    assert shown(tab) == "Table 2" and tab.table_tabs.current_id == tab.document.tables[1].id
    tab.select_table(tab.document.tables[0].id)
    qtbot.mouseClick(tab.table_tabs.plus, Qt.MouseButton.LeftButton)
    assert names(tab) == ["Data 1", "Table 3", "Table 2"]  # after the selected tab
    assert shown(tab) == "Table 3"
    assert tab.document.undo_stack.undo_text == "Add table 'Table 3'"
    window.undo()
    # back to the table that was selected when "Table 3" was added
    assert names(tab) == ["Data 1", "Table 2"] and shown(tab) == "Data 1"
    window.redo()
    assert shown(tab) == "Table 3"
    window.undo()
    assert shown(tab) == "Data 1"
    window.undo()  # "Table 2" was added while "Data 1" was selected
    assert names(tab) == ["Data 1"] and shown(tab) == "Data 1"


def test_file_menu_has_new_table_and_close_table(window: MainWindow) -> None:
    window.new_document()  # the table actions are made with the first project
    file_menu = window.menuBar().actions()[0].menu()
    assert file_menu is not None
    texts = [a.text() for a in file_menu.actions() if not a.isSeparator()]
    assert texts[:2] == ["&New project", "New &table"]
    assert texts.index("Close ta&ble…") + 1 == texts.index("&Close project")
    assert window.registry.action("file.close").shortcut() == QKeySequence("Ctrl+W")
    assert window.registry.action("table.close").shortcut().isEmpty()
    toolbar_actions = window._ensure_main_toolbar().actions()
    table_toolbar = window.table_toolbar.actions() if window.table_toolbar else []
    assert window.registry.action("table.new") not in toolbar_actions + table_toolbar
    palette_ids = {e.id for e in window.registry.entries()}
    assert {"table.new", "table.close", "table.next", "table.previous"} <= palette_ids


# --- switching -----------------------------------------------------------------------------


def test_switch_with_the_tabs_and_the_keys(project: DocumentTab, window: MainWindow) -> None:
    assert shown(project) == "Viability"
    assert window.registry.action("table.next").shortcut() == QKeySequence("Ctrl+PgDown")
    assert window.registry.action("table.previous").shortcut() == QKeySequence("Ctrl+PgUp")
    assert not window.registry.action("table.previous").isEnabled()
    window.registry.action("table.next").trigger()
    assert shown(project) == "Survival"
    assert not window.registry.action("table.next").isEnabled()
    assert window.registry.action("table.next").toolTip() == "This is the last table"
    window.registry.action("table.previous").trigger()
    assert shown(project) == "Viability"
    project.table_tabs.bar.setCurrentIndex(1)  # a click on the tab
    assert shown(project) == "Survival"


def test_table_toolbar_acts_on_the_selected_table(project: DocumentTab, window: MainWindow) -> None:
    project.select_table("tS")
    window.registry.action("table.add_column").trigger()
    assert len(project.document.table("tS").columns) == 3
    assert len(project.document.table("tV").columns) == 2


def test_panels_follow_the_selected_table(project: DocumentTab) -> None:
    assert project.current_analysis_id == "fitV"
    graph = project.document.graphs[0].id
    assert project.current_graph_id == graph
    project.select_table("tS")
    assert project.current_analysis_id is None and project.current_graph_id is None
    project.select_table("tV")
    assert project.current_analysis_id == "fitV" and project.current_graph_id == graph


def test_selection_is_kept_when_commands_change_the_document(project: DocumentTab) -> None:
    project.select_table("tS")
    project.document.execute(SetCellValue("tV", "tVy", 0, 5.0))
    project.document.execute(AddTable(assay("Other", "tO")))  # added tables are selected
    assert shown(project) == "Other"
    project.select_table("tS")
    project.document.undo()  # removes "Other", which is not selected
    assert shown(project) == "Survival"
    project.document.undo()
    assert shown(project) == "Survival"


# --- renaming ------------------------------------------------------------------------------


def double_click_tab(qtbot: QtBot, tab: DocumentTab, index: int) -> None:
    bar = tab.table_tabs.bar
    qtbot.mouseDClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(index).center())


def test_rename_a_tab_in_place(qtbot: QtBot, project: DocumentTab) -> None:
    double_click_tab(qtbot, project, 1)
    editor = project.table_tabs.editor
    assert editor is not None and editor.text() == "Survival" and editor.selectedText()
    qtbot.keyClicks(editor, "Survival 24 h")
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    assert editor.is_closed
    assert names(project) == ["Viability", "Survival 24 h"]
    assert tab_texts(project) == names(project)
    assert project.document.undo_stack.count == 1
    project.document.undo()
    assert tab_texts(project) == ["Viability", "Survival"]


def test_esc_cancels_a_tab_rename(qtbot: QtBot, project: DocumentTab) -> None:
    double_click_tab(qtbot, project, 0)
    editor = project.table_tabs.editor
    assert editor is not None
    qtbot.keyClicks(editor, "Nope")
    qtbot.keyClick(editor, Qt.Key.Key_Escape)
    assert editor.is_closed and names(project) == ["Viability", "Survival"]
    assert project.document.undo_stack.count == 0


def test_a_taken_tab_name_keeps_the_old_name(
    qtbot: QtBot, project: DocumentTab, window: MainWindow
) -> None:
    project.rename_table("tS")
    editor = project.table_tabs.editor
    assert editor is not None
    qtbot.keyClicks(editor, "viability")
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    assert editor.is_closed and names(project) == ["Viability", "Survival"]
    assert "'Viability' is already used" in window.statusBar().currentMessage()


def test_tab_context_menu(project: DocumentTab) -> None:
    menu = project.table_tabs.context_menu(1)
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts == ["&Rename", "&Close table…", "&New table"]
    rename = menu.actions()[0]
    rename.trigger()
    assert project.table_tabs.editor is not None
    assert project.table_tabs.editor.text() == "Survival"
    assert shown(project) == "Survival"  # the tab being renamed is selected
    project.table_tabs.editor.cancel()
    project.select_table("tV")
    new = next(a for a in menu.actions() if a.text() == "&New table")
    new.trigger()
    assert names(project) == ["Viability", "Table 2", "Survival"]


def test_drag_a_tab_to_reorder(qtbot: QtBot, project: DocumentTab) -> None:
    bar = project.table_tabs.bar
    start = bar.tabRect(0).center()
    end = bar.tabRect(1).center() + QPoint(bar.tabRect(1).width() // 2, 0)
    qtbot.mousePress(bar, Qt.MouseButton.LeftButton, pos=start)
    for step in range(1, 11):
        qtbot.mouseMove(bar, start + (end - start) * step / 10)
    qtbot.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=end)
    qtbot.waitUntil(lambda: names(project) == ["Survival", "Viability"])
    assert tab_texts(project) == ["Survival", "Viability"]
    assert project.document.undo_stack.count == 1
    assert project.document.undo_stack.undo_text == "Move table 'Viability'"
    project.document.undo()
    assert tab_texts(project) == ["Viability", "Survival"]


def test_move_table_keeps_the_tabs_in_step(project: DocumentTab) -> None:
    project.move_table("tS", 0)
    assert tab_texts(project) == ["Survival", "Viability"]
    assert shown(project) == "Viability"


# --- closing -------------------------------------------------------------------------------


def test_close_table_warns_and_undo_brings_it_back(
    project: DocumentTab, window: MainWindow, dialogs: Dialogs
) -> None:
    dialogs.answers = [False, True]
    window.registry.action("table.close").trigger()
    (dialog,) = dialogs.shown
    assert dialog.windowTitle() == "Close table"
    assert dialog.lead.text() == "Closing “Viability” deletes the table from the project."
    assert dialog.items == ["1 analysis (Linear regression)", "1 graph (XY)"]
    assert dialog.undo_hint.text() == "You can bring it back with Undo (Ctrl+Z)."
    assert dialog.skeleton.ok_button is not None
    assert dialog.skeleton.ok_button.text() == "Delete table"
    assert dialog.cancel_button.isDefault() and not dialog.skeleton.ok_button.isDefault()
    assert names(project) == ["Viability", "Survival"]  # cancelled

    window.registry.action("table.close").trigger()
    assert names(project) == ["Survival"]
    assert project.document.analyses == [] and project.document.graphs == []
    assert shown(project) == "Survival"
    assert project.current_analysis_id is None
    summary = "Delete table 'Viability' with 1 analysis and 1 graph"
    assert project.document.undo_stack.undo_text == summary
    assert project.document.change_log.entries[-1].summary == summary

    project.select_table("tS")
    window.undo()
    assert names(project) == ["Viability", "Survival"]
    assert shown(project) == "Viability"  # the restored table is selected
    assert project.current_analysis_id == "fitV"
    assert project.current_graph_id == project.document.graphs[0].id


def test_close_from_the_tab_button(qtbot: QtBot, project: DocumentTab, dialogs: Dialogs) -> None:
    dialogs.answers = [True]
    bar = project.table_tabs.bar
    assert bar.tabsClosable()
    button = bar.tabButton(1, bar.ButtonPosition.RightSide) or bar.tabButton(
        1, bar.ButtonPosition.LeftSide
    )
    assert button is not None
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert names(project) == ["Viability"]


def test_an_empty_table_closes_without_asking(
    project: DocumentTab, window: MainWindow, dialogs: Dialogs
) -> None:
    window.registry.action("table.new").trigger()
    assert shown(project) == "Table 2"
    window.registry.action("table.close").trigger()
    assert dialogs.shown == []
    assert names(project) == ["Viability", "Survival"]
    assert shown(project) == "Survival"  # the neighbour that took its place


def test_the_last_table_cannot_be_closed(window: MainWindow, dialogs: Dialogs) -> None:
    tab = window.new_document()
    action = window.registry.action("table.close")
    assert not action.isEnabled()
    assert LAST_TABLE in action.toolTip()
    assert not tab.table_tabs.bar.tabsClosable()
    menu = tab.table_tabs.context_menu(0)
    close = next(a for a in menu.actions() if "Close table" in a.text())
    assert not close.isEnabled()
    messages: list[str] = []
    tab.message.connect(messages.append)
    assert not tab.close_table()
    assert messages == [LAST_TABLE] and len(tab.document.tables) == 1
    window.registry.action("table.new").trigger()
    assert action.isEnabled() and tab.table_tabs.bar.tabsClosable()


# --- import --------------------------------------------------------------------------------


def test_import_goes_into_the_selected_table(
    qtbot: QtBot, project: DocumentTab, window: MainWindow, importing: list[Path | None]
) -> None:
    project.select_table("tS")
    window.registry.action("table.new").trigger()  # an empty table between the two
    assert shown(project) == "Table 2"
    importing.append(DATA / "basic.csv")
    window.registry.action("file.import").trigger()
    wait_import(qtbot, window)
    table = project.document.tables[2]
    assert table.name == "Table 2"
    assert [c.name for c in table.columns] == ["Dose", "Response", "Label"]
    assert [c.name for c in project.document.table("tS").columns] == ["Dose", "Response"]
    assert window.statusBar().currentMessage() == (
        "Imported 3 rows and 3 columns from basic.csv into Table 2."
    )
    assert shown(project) == "Table 2"


# --- the editor is shared ------------------------------------------------------------------


def test_click_elsewhere_keeps_a_tab_name(qtbot: QtBot, project: DocumentTab) -> None:
    project.rename_table("tV")
    editor = project.table_tabs.editor
    assert editor is not None
    qtbot.keyClicks(editor, "Cells")
    QApplication.sendEvent(
        editor, QFocusEvent(QFocusEvent.Type.FocusOut, Qt.FocusReason.MouseFocusReason)
    )
    assert editor.is_closed and names(project) == ["Cells", "Survival"]


def test_every_tab_is_on_screen(qtbot: QtBot, project: DocumentTab) -> None:
    project.new_table()
    bar = project.table_tabs.bar
    assert bar.height() >= bar.sizeHint().height()
    for i in range(bar.count()):
        assert bar.rect().contains(bar.tabRect(i)), bar.tabText(i)
    QApplication.processEvents()
    for i in range(bar.count()):
        assert bar.rect().contains(bar.tabRect(i)), bar.tabText(i)
    plus = project.table_tabs.plus.geometry()
    # the + is drawn as one more tab: right after the last tab, just as high
    last = bar.tabRect(bar.count() - 1).translated(bar.pos())
    assert plus.left() == last.right() + 1
    assert plus.top() == last.top() and plus.height() == last.height()
    assert plus.width() >= plus.height()
    # the + is set like the tab names: same font, same line
    label = bar.label_rect(0)
    plus_label = project.table_tabs.plus.label_rect()
    assert (plus_label.top(), plus_label.height()) == (label.top(), label.height())
    assert project.table_tabs.plus.font() == bar.font()
