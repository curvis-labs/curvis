"""Kaplan-Meier in the Analyze dialog and the Results panel (step 3.4)."""

import pytest
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from curvis.core import AddColumn, Document, RemoveColumn
from curvis.data import Column
from curvis.ui.document_tab import DocumentTab
from curvis.ui.main_window import MainWindow
from tests.ui.test_analyze import dialog_for, doc_with, run_dialog, settle, tab_names

TIMES = [2, 3, 3, 5, 8, 8, 10, 12, 15, 20]


def survival_doc() -> Document:
    return doc_with(
        Column.new("Days", "Time", TIMES, unit="days", id="t"),
        Column.new("Drug", "Event", [1, 1, 0, 1, 1, 0, 1, 0, 1, 0], id="d"),
        Column.new("Placebo", "Event", [1, 1, 1, 1, 1, 1, 0, 1, 1, 1], id="p"),
    )


def test_the_dialog_opens_on_survival_with_every_pair_ticked(qtbot: QtBot) -> None:
    dialog = dialog_for(qtbot, survival_doc())
    assert tab_names(dialog) == ["Survival"]
    assert dialog.listed_types() == ["kaplan_meier"]
    analysis = dialog.analysis()
    assert analysis.type == "kaplan_meier" and analysis.inputs == ("d", "p")
    assert "survival curves of Drug, Placebo" in dialog.summary.text()


@pytest.fixture
def survival_tab(window: MainWindow) -> DocumentTab:
    tab = window.new_document()
    doc = tab.document
    table = doc.tables[0]
    for column in list(table.columns):
        doc.execute(RemoveColumn(table.id, column.id))
    for column in survival_doc().tables[0].columns:
        doc.execute(AddColumn(table.id, column))
    return tab


def test_one_click_runs_kaplan_meier(
    survival_tab: DocumentTab, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    tab = survival_tab
    run_dialog(monkeypatch, analysis=None)
    tab.analyze()
    settle(qtbot, tab)
    panel = tab.results_panel
    assert panel.combo.currentText().startswith("Kaplan-Meier")
    assert panel.content is not None
    reading = panel.content.findChild(QLabel, "reading")
    assert reading is not None
    assert reading.text() == "Median survival: Drug 10 days, Placebo 8 days."
    titles = [g.grid.title for g in panel.grids]
    assert titles == [
        "Median survival",
        "Survival of Drug at each event time",
        "Survival of Placebo at each event time",
        "Number at risk",
    ]
    median = panel.grids[0].grid
    assert median.shown[0] == ("Drug", "10", "6", "4", "10", "2", "Not reached")
    risk = panel.grids[-1].grid
    assert risk.header == ("Time (days)", "Drug", "Placebo")
    assert risk.shown[0] == ("0", "10", "10")
