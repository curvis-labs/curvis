"""The group comparisons in the Analyze dialog and the Results panel: the control group
follows the ticked series, options switch on and off, and one click on Run works."""

import pytest
from PySide6.QtWidgets import QComboBox, QLabel
from pytestqt.qtbot import QtBot

from curvis.analysis.compare.anova_analysis import AnovaParams
from curvis.core import AddColumn, Document, GroupColumns
from curvis.data import Column
from curvis.ui.analyze_dialog import AnalyzeDialog
from curvis.ui.document_tab import DocumentTab
from curvis.ui.main_window import MainWindow
from tests.ui.test_analyze import dialog_for, doc_with, run_dialog, settle

ANOVA = "one_way_anova"


def three_groups() -> Document:
    return doc_with(
        Column.new("Control", "Y", [4.1, 5.2, 4.8, 5.0], id="c"),
        Column.new("Drug", "Y", [5.9, 6.4, 5.7, 6.8], id="d"),
        Column.new("Other", "Y", [4.9, 5.1, 5.6, 5.4], id="o"),
    )


def combo(dialog: AnalyzeDialog, name: str) -> QComboBox:
    widget = dialog.forms[ANOVA].fields[name]
    assert isinstance(widget, QComboBox)
    return widget


def items(box: QComboBox) -> list[str]:
    return [box.itemText(i) for i in range(box.count())]


def choose(box: QComboBox, data: str) -> None:
    box.setCurrentIndex(box.findData(data))


def test_the_comparisons_are_offered_for_two_or_more_series(qtbot: QtBot) -> None:
    dialog = dialog_for(qtbot, three_groups())
    assert list(dialog.radios) == [
        "descriptive_statistics",
        "t_test",
        ANOVA,
        "mann_whitney",
    ]


def test_the_control_lists_the_ticked_series_and_is_only_for_dunnett(qtbot: QtBot) -> None:
    dialog = dialog_for(qtbot, three_groups())
    dialog.set_analysis_type(ANOVA)
    control = combo(dialog, "control")
    assert items(control) == ["Control", "Drug", "Other"]
    assert not control.isEnabled()
    choose(combo(dialog, "posthoc"), "dunnett")
    assert control.isEnabled()
    assert "against Control" in dialog.summary.text()
    choose(control, "o")
    assert "against Other" in dialog.summary.text()
    dialog.set_checked(["c", "d"])
    assert items(control) == ["Control", "Drug"]
    assert control.currentText() == "Control"
    dialog.set_checked(["c", "d", "o"])
    assert control.currentText() == "Other"  # the choice comes back with its series
    params = dialog.params()
    assert params == AnovaParams(posthoc="dunnett", control="o")


def test_editing_reopens_with_the_stored_control(qtbot: QtBot) -> None:
    doc = three_groups()
    dialog = dialog_for(qtbot, doc)
    dialog.set_analysis_type(ANOVA)
    choose(combo(dialog, "posthoc"), "dunnett")
    choose(combo(dialog, "control"), "d")
    analysis = dialog.analysis()
    again = dialog_for(qtbot, doc, analysis=analysis)
    assert combo(again, "control").currentText() == "Drug"
    assert combo(again, "control").isEnabled()


def test_t_test_needs_exactly_two_series(qtbot: QtBot) -> None:
    dialog = dialog_for(qtbot, three_groups())
    dialog.set_analysis_type("t_test")
    assert dialog.checked_inputs() == ("c", "d")  # the first two are ticked
    assert dialog.ok_button.isEnabled()
    dialog.set_checked(["c", "d", "o"])
    assert not dialog.ok_button.isEnabled()
    assert dialog.skeleton.feedback.text() == "Tick exactly 2 series to compare."


def test_paired_t_test_needs_matching_columns(qtbot: QtBot) -> None:
    doc = three_groups()
    doc.execute(AddColumn("T", Column.new("Drug2", "Y", [6.0, 6.1, 6.2, 6.3], id="d2")))
    doc.execute(GroupColumns("T", ["d", "d2"], "Drug", group_id="gD"))
    dialog = dialog_for(qtbot, doc)
    dialog.set_analysis_type("t_test")
    dialog.set_checked(["c", "gD"])
    test = dialog.forms["t_test"].fields["test"]
    assert isinstance(test, QComboBox)
    choose(test, "paired")
    assert not dialog.ok_button.isEnabled()
    assert "same number of columns" in dialog.skeleton.feedback.text()


@pytest.fixture
def two_series_tab(window: MainWindow) -> DocumentTab:
    tab = window.new_document()
    doc = tab.document
    table = doc.tables[0]
    for column in list(table.columns):
        from curvis.core import RemoveColumn

        doc.execute(RemoveColumn(table.id, column.id))
    doc.execute(AddColumn(table.id, Column.new("Control", "Y", [4.1, 5.2, 4.8, 5.0], id="c")))
    doc.execute(AddColumn(table.id, Column.new("Drug", "Y", [5.9, 6.4, 5.7, 6.8], id="d")))
    return tab


def test_one_click_runs_a_t_test(
    two_series_tab: DocumentTab, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    tab = two_series_tab
    run_dialog(monkeypatch, lambda dialog: dialog.set_analysis_type("t_test"))
    tab.analyze()
    settle(qtbot, tab)
    panel = tab.results_panel
    assert panel.combo.currentText().startswith("t test")
    assert panel.content is not None
    reading = panel.content.findChild(QLabel, "reading")
    assert reading is not None
    assert reading.text().startswith("The mean of Drug is 1.425 higher than the mean of Control")
    grid = panel.grids[0].grid
    assert grid.title == "Unpaired t test with Welch's correction"
    assert grid.shown[0][0] == "P value" and grid.shown[0][2] == "**"
