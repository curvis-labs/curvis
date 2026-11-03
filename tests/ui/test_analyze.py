"""The Analyze dialog and the Results panel: filtering, running, rerunning and copying."""

from collections.abc import Callable, Iterator

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QCheckBox, QDialog, QLabel
from pytestqt.qtbot import QtBot

from curvis.analysis import base
from curvis.analysis.base import Family
from curvis.analysis.descriptive import DescriptiveParams
from curvis.core import AddTable, Document, GroupColumns, RemoveColumn, SetCellValue
from curvis.data import Column, Table
from curvis.ui.analyze_dialog import AnalyzeDialog
from curvis.ui.document_tab import DocumentTab
from curvis.ui.main_window import MainWindow
from curvis.ui.results_panel import EMPTY_TEXT, RECALCULATING, ResultGrid
from tests.unit.test_analysis_base import FakeFit, FakeSurvival

DESCRIPTIVE = "descriptive_statistics"

type Setup = Callable[[AnalyzeDialog], None]


@pytest.fixture
def fakes() -> Iterator[None]:
    base.register(FakeFit())
    base.register(FakeSurvival())
    yield
    base.unregister(FakeFit.id)
    base.unregister(FakeSurvival.id)


def doc_with(*columns: Column) -> Document:
    doc = Document()
    doc.execute(AddTable(Table("Data 1", list(columns), id="T")))
    return doc


def dialog_for(qtbot: QtBot, doc: Document, **kwargs: object) -> AnalyzeDialog:
    dialog = AnalyzeDialog(doc, "T", **kwargs)  # type: ignore[arg-type]
    qtbot.addWidget(dialog)
    return dialog


def tab_names(dialog: AnalyzeDialog) -> list[str]:
    return [dialog.family_tabs.tabText(i) for i in range(dialog.family_tabs.count())]


@pytest.fixture
def tab(window: MainWindow) -> DocumentTab:
    """A new document: X = 1, 2, 3 and three replicates of A grouped as "A"."""
    tab = window.new_document()
    doc = tab.document
    table = doc.tables[0]
    doc.execute(RemoveColumn(table.id, table.columns[1].id))
    for name, values in (("A1", [1, 2, 3]), ("A2", [3, 4, 5]), ("A3", [5, 6, 7])):
        from curvis.core import AddColumn

        doc.execute(AddColumn(table.id, Column.new(name, "Y", values, id=name)))
    doc.execute(GroupColumns(table.id, ["A1", "A2", "A3"], "A", group_id="gA"))
    from curvis.core import SetCellBlock

    doc.execute(SetCellBlock(table.id, 0, 0, [["1"], ["2"], ["3"]]))
    return tab


def run_dialog(
    monkeypatch: pytest.MonkeyPatch, setup: Setup | None = None, accept: bool = True
) -> list[AnalyzeDialog]:
    """Make the tab's dialogs run ``setup`` and press Run (or Cancel)."""
    shown: list[AnalyzeDialog] = []

    def exec_dialog(self: DocumentTab, dialog: QDialog) -> int:
        assert isinstance(dialog, AnalyzeDialog)
        shown.append(dialog)
        if setup is not None:
            setup(dialog)
        if accept and dialog.ok_button.isEnabled():
            return QDialog.DialogCode.Accepted
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(DocumentTab, "_exec_dialog", exec_dialog)
    return shown


def settle(qtbot: QtBot, tab: DocumentTab) -> None:
    engine = tab.analysis_engine
    assert engine is not None and tab.engine is not None
    scheduler = tab.engine.recalculator.scheduler
    qtbot.waitUntil(lambda: not tab.engine.busy and not scheduler.busy)  # type: ignore[union-attr]


def means(tab: DocumentTab) -> list[str]:
    grid = tab.results_panel.grids[0].grid
    return [row[grid.header.index("Mean")] for row in grid.shown]


# --- filtering -----------------------------------------------------------------------------


def test_only_supported_families_and_analyses_are_shown(qtbot: QtBot) -> None:
    dialog = dialog_for(qtbot, doc_with(Column.new("a", "Y", [1, 2])))
    assert tab_names(dialog) == ["Compare groups"]
    assert list(dialog.radios) == [DESCRIPTIVE]
    assert dialog.analysis_type is not None and dialog.analysis_type.id == DESCRIPTIVE


@pytest.mark.usefixtures("fakes")
def test_it_opens_on_curve_fits_for_xy_data(qtbot: QtBot) -> None:
    dialog = dialog_for(qtbot, doc_with(Column.new("x", "X", [1, 2]), Column.new("a", "Y", [1, 2])))
    assert tab_names(dialog) == ["Compare groups", "Fit a curve"]
    assert dialog.family is Family.FIT
    assert dialog.analysis_type is not None and dialog.analysis_type.id == FakeFit.id
    assert FakeSurvival.id not in dialog.radios  # the table has no time/event pair
    assert dialog.family_description.text() == Family.FIT.description


@pytest.mark.usefixtures("fakes")
def test_it_opens_on_survival_for_time_event_pairs(qtbot: QtBot) -> None:
    doc = doc_with(
        Column.new("t", "Time", [1, 2]),
        Column.new("e", "Event", [0, 1]),
        Column.new("x", "X", [1, 2]),
        Column.new("a", "Y", [1, 2]),
    )
    dialog = dialog_for(qtbot, doc)
    assert tab_names(dialog) == ["Compare groups", "Fit a curve", "Survival"]
    assert dialog.family is Family.SURVIVAL
    dialog.family_tabs.setCurrentIndex(0)
    assert dialog.analysis_type is not None and dialog.analysis_type.id == DESCRIPTIVE


def test_a_table_without_data_to_analyze_says_so(qtbot: QtBot) -> None:
    dialog = dialog_for(qtbot, doc_with(Column.new("x", "X", [1, 2])))
    assert tab_names(dialog) == []
    assert not dialog.ok_button.isEnabled()
    assert dialog.skeleton.reason_shown()


def test_the_data_list_shows_series_with_n_and_dims_other_columns(qtbot: QtBot) -> None:
    doc = doc_with(
        Column.new("Dose", "X", [1, 2, 3]),
        Column.new("a1", "Y", [1, 2, None], id="a1"),
        Column.new("a2", "Y", [1, None, None], id="a2"),
        Column.new("b", "Y", [4, 5, 6], id="b"),
    )
    doc.execute(GroupColumns("T", ["a1", "a2"], "A", group_id="gA"))
    dialog = dialog_for(qtbot, doc)
    rows = [
        (item.text(0), item.text(1), bool(item.flags() & Qt.ItemFlag.ItemIsEnabled))
        for item in (dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount()))
        if item is not None
    ]
    assert rows == [("A", "3", True), ("b", "3", True), ("Dose (X)", "–", False)]
    assert dialog.checked_inputs() == ("gA", "b")


def test_run_is_disabled_with_a_reason_when_nothing_is_ticked(qtbot: QtBot) -> None:
    dialog = dialog_for(qtbot, doc_with(Column.new("a", "Y", [1, 2], id="a")))
    assert dialog.ok_button.isEnabled()
    assert dialog.summary.text().startswith("Descriptive statistics (n, mean, SD, SEM and 95% CI)")
    dialog.deselect_all_button.click()
    assert not dialog.ok_button.isEnabled()
    assert dialog.skeleton.feedback.text() == "Tick at least one series."
    dialog.select_all_button.click()
    assert dialog.ok_button.isEnabled()


def test_options_have_defaults_and_a_closed_advanced_section(qtbot: QtBot) -> None:
    dialog = dialog_for(qtbot, doc_with(Column.new("a", "Y", [1, 2], id="a")))
    form = dialog.forms[DESCRIPTIVE]
    assert form.advanced is not None and not form.advanced.expanded
    assert dialog.params() == DescriptiveParams()
    toggle_ci(dialog)
    assert dialog.params() == DescriptiveParams(confidence_interval=False)
    assert "95% CI" not in dialog.summary.text()


def test_recently_used_analyses_are_marked(qtbot: QtBot) -> None:
    doc = doc_with(Column.new("a", "Y", [1, 2]))
    assert DESCRIPTIVE not in dialog_for(qtbot, doc).badges
    assert DESCRIPTIVE in dialog_for(qtbot, doc, recent=[DESCRIPTIVE]).badges


def toggle_ci(dialog: AnalyzeDialog) -> None:
    check = dialog.forms[DESCRIPTIVE].fields["confidence_interval"]
    assert isinstance(check, QCheckBox)
    check.click()


# --- running -------------------------------------------------------------------------------


def test_one_click_on_run_shows_results_in_the_panel(
    tab: DocumentTab, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, window: MainWindow
) -> None:
    panel = tab.results_panel
    assert panel.empty.isVisible() and panel.empty.text() == EMPTY_TEXT
    run_dialog(monkeypatch)
    window.registry.action("analyze.analyze").trigger()
    settle(qtbot, tab)
    assert panel.combo.currentText() == "Descriptive statistics · Data 1"
    assert not panel.empty.isVisible()
    assert panel.content is not None
    reading = panel.content.findChild(QLabel, "reading")
    assert reading is not None and reading.text().startswith("The mean, SD, SEM and 95%")
    assert means(tab) == ["3.000", "4.000", "5.000"]
    assert window.settings.recent_analyses == [DESCRIPTIVE]
    assert window.registry.action("analyze.edit").isEnabled()
    assert tab.document.undo_stack.undo_text == "Analyze 'Data 1': descriptive statistics"


def test_cancel_adds_nothing(tab: DocumentTab, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dialog(monkeypatch, accept=False)
    assert not tab.analyze()
    assert tab.document.analyses == []


def test_it_reruns_when_the_data_changes_and_shows_a_badge(
    tab: DocumentTab, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dialog(monkeypatch)
    tab.analyze()
    settle(qtbot, tab)
    panel = tab.results_panel
    tab.document.execute(SetCellValue(tab.document.tables[0].id, "A1", 0, 10))
    assert panel.badge.isVisible() and panel.badge.text() == RECALCULATING
    assert means(tab) == ["3.000", "4.000", "5.000"]  # the old numbers stay visible
    settle(qtbot, tab)
    qtbot.waitUntil(lambda: not panel.badge.isVisible())
    assert means(tab) == ["6.000", "4.000", "5.000"]
    tab.document.undo()
    settle(qtbot, tab)
    assert means(tab) == ["3.000", "4.000", "5.000"]


def test_edit_reopens_with_the_stored_parameters(
    tab: DocumentTab, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dialog(monkeypatch, toggle_ci)
    tab.analyze()
    settle(qtbot, tab)
    assert len(tab.results_panel.grids[0].grid.header) == 5
    shown = run_dialog(monkeypatch, toggle_ci)
    tab.results_panel.edit_button.click()
    (dialog,) = shown
    assert dialog.windowTitle() == "Edit analysis"
    assert dialog.ok_button.text() == "Update"
    settle(qtbot, tab)
    assert len(tab.document.analyses) == 1
    assert tab.document.analyses[0].params == DescriptiveParams()
    assert len(tab.results_panel.grids[0].grid.header) == 7


def test_an_analysis_that_cannot_run_says_what_to_change(
    tab: DocumentTab, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dialog(monkeypatch)
    tab.analyze()
    settle(qtbot, tab)
    table_id = tab.document.tables[0].id
    from curvis.core import UngroupColumns

    tab.document.execute(UngroupColumns(table_id, ["A1", "A2", "A3"]))
    settle(qtbot, tab)
    panel = tab.results_panel
    assert panel.error_row is not None and panel.grids == []
    assert "Click Edit…" in tab.analysis_engine.errors[tab.document.analyses[0].id]  # type: ignore[union-attr]


def test_delete_analysis_is_undoable(
    tab: DocumentTab, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, window: MainWindow
) -> None:
    run_dialog(monkeypatch)
    tab.analyze()
    settle(qtbot, tab)
    window.registry.action("analyze.delete").trigger()
    assert tab.results_panel.current_id is None
    assert not window.registry.action("analyze.edit").isEnabled()
    tab.document.undo()
    settle(qtbot, tab)
    assert tab.results_panel.current_id == tab.document.analyses[0].id


def test_the_combo_box_switches_between_analyses(
    tab: DocumentTab, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dialog(monkeypatch)
    tab.analyze()
    run_dialog(monkeypatch, lambda d: d.set_checked(["gA"]))
    tab.analyze()
    settle(qtbot, tab)
    panel = tab.results_panel
    assert panel.combo.count() == 2
    assert panel.current_id == tab.document.analyses[1].id  # the new one is shown
    panel.combo.setCurrentIndex(0)
    assert panel.current_id == tab.document.analyses[0].id


# --- copying -------------------------------------------------------------------------------


def first_grid(tab: DocumentTab) -> ResultGrid:
    return tab.results_panel.grids[0]


def test_ctrl_c_copies_the_selected_cells_as_shown(
    tab: DocumentTab, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    table_id = tab.document.tables[0].id
    tab.document.execute(SetCellValue(table_id, "A1", 0, 1.1))
    run_dialog(monkeypatch)
    tab.analyze()
    settle(qtbot, tab)
    grid = first_grid(tab)
    model = grid.model()
    selection = grid.selectionModel()
    from PySide6.QtCore import QItemSelectionModel

    flag = QItemSelectionModel.SelectionFlag.Select
    for column in (2, 3):  # Mean, SD of the first row
        selection.select(model.index(0, column), flag)
    grid.setFocus()
    qtbot.waitUntil(grid.hasFocus)
    QGuiApplication.clipboard().clear()
    qtbot.keyClick(grid, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert grid.grid.shown[0][2:4] == ("3.033", "1.950")
    assert QGuiApplication.clipboard().text() == "3.033\t1.950\r\n"  # as shown
    assert grid.model().index(0, 2).data(Qt.ItemDataRole.ToolTipRole) == repr((1.1 + 3 + 5) / 3)


def test_selecting_everything_copies_the_headers_too(
    tab: DocumentTab, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, window: MainWindow
) -> None:
    run_dialog(monkeypatch)
    tab.analyze()
    settle(qtbot, tab)
    grid = first_grid(tab)
    grid.selectAll()
    grid.setFocus()
    qtbot.waitUntil(grid.hasFocus)
    window.registry.action("edit.copy").trigger()  # Copy from the Edit menu
    lines = QGuiApplication.clipboard().text().split("\r\n")
    assert lines[0].split("\t") == [
        "X", "n", "Mean", "SD", "SEM", "Lower 95% CI", "Upper 95% CI"
    ]  # fmt: skip
    assert lines[1].split("\t")[:4] == ["1", "3", "3.000", "2.000"]
    assert len(lines) == 5  # header, three rows, and the final line break


def test_copy_in_the_table_still_copies_table_cells(
    tab: DocumentTab, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, window: MainWindow
) -> None:
    run_dialog(monkeypatch)
    tab.analyze()
    settle(qtbot, tab)
    assert tab.table_view is not None
    tab.table_view.setFocus()
    from PySide6.QtCore import QItemSelectionModel

    tab.table_view.selectionModel().setCurrentIndex(
        tab.table_view.model().index(0, 1),
        QItemSelectionModel.SelectionFlag.ClearAndSelect,
    )
    qtbot.waitUntil(tab.table_view.hasFocus)
    window.registry.action("edit.copy").trigger()
    assert QGuiApplication.clipboard().text() == "1\r\n"
