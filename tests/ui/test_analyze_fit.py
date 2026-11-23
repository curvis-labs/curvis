"""The curve fits in the Analyze dialog and the Results panel: XY data opens on the fits,
one click fits a line, a 4PL, a biphasic and a Michaelis-Menten fit run on their data,
and a parameter that cannot be read keeps Run disabled with a message."""

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QLineEdit
from pytestqt.qtbot import QtBot

from curvis.analysis.base import Analysis
from curvis.analysis.fit.analyses import Hill4Params, MichaelisMentenParams, TwoPhaseParams
from curvis.core import AddColumn, Document, GroupColumns, RemoveColumn, SetCellBlock
from curvis.data import Column
from curvis.ui.analyze_dialog import AnalyzeDialog
from curvis.ui.analyze_options import FixedTable, XRangeEditor
from curvis.ui.document_tab import DocumentTab
from curvis.ui.main_window import MainWindow
from tests.ui.test_analyze import run_dialog, settle
from tests.ui.test_analyze import tab as tab  # the fixture


def rows(doc_tab: DocumentTab) -> dict[str, tuple[str, ...]]:
    grid = doc_tab.results_panel.grids[0].grid
    return {row[0]: row[1:] for row in grid.shown}


def test_one_click_on_xy_data_fits_a_line(
    tab: DocumentTab,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    window: MainWindow,
) -> None:
    run_dialog(monkeypatch, analysis=None)  # keep what the dialog opens on
    window.registry.action("analyze.analyze").trigger()
    settle(qtbot, tab)
    panel = tab.results_panel
    assert panel.combo.currentText() == "Linear regression · Data 1"
    assert panel.grids[0].grid.title == "Best-fit values"
    r = rows(tab)
    assert r["Fit"] == ("OK",)
    assert r["Slope"] == ("1.000",) and r["Y-intercept"] == ("2.000",)
    assert r["Number of points"] == ("9",)
    assert panel.content is not None
    reading = panel.content.findChild(QLabel, "reading")
    assert reading is not None and reading.text().startswith("The best-fit line for A has slope")
    assert window.settings.recent_analyses == ["linear_regression"]


def dose_tab(window: MainWindow) -> DocumentTab:
    """X = 11 concentrations, two replicates of a rising curve grouped as "Drug"."""
    doc_tab = window.new_document()
    doc: Document = doc_tab.document
    table = doc.tables[0]
    conc = 10.0 ** np.arange(-9, -3.5, 0.5)
    rng = np.random.default_rng(0)
    curve = 5 + 90 / (1 + 10 ** ((-6.5 - np.log10(conc)) * 1.2))
    doc.execute(RemoveColumn(table.id, table.columns[1].id))
    for i in (1, 2):
        values = (curve + rng.normal(0, 2, len(conc))).tolist()
        doc.execute(AddColumn(table.id, Column.new(f"Drug{i}", "Y", values, id=f"d{i}")))
    doc.execute(GroupColumns(table.id, ["d1", "d2"], "Drug", group_id="g"))
    doc.execute(SetCellBlock(table.id, 0, 0, [[repr(float(c))] for c in conc]))
    return doc_tab


def test_a_4pl_fit_reports_the_ec50(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, window: MainWindow
) -> None:
    doc_tab = dose_tab(window)
    run_dialog(monkeypatch, analysis="hill_4pl")
    doc_tab.analyze()
    settle(qtbot, doc_tab)
    assert doc_tab.results_panel.combo.currentText() == "Hill 4PL (variable slope) · Data 1"
    r = rows(doc_tab)
    assert r["Fit"] == ("OK",)
    ec50 = float(r["EC50"][0])
    assert 1e-7 < ec50 < 1e-6
    assert float(r["Lower 95% CI of EC50"][0]) < ec50 < float(r["Upper 95% CI of EC50"][0])
    assert r["Number of points"] == ("22",)
    analysis = doc_tab.document.analyses[0]
    assert analysis.type == "hill_4pl"
    assert doc_tab.analysis_engine is not None
    result = doc_tab.analysis_engine.results[analysis.id]
    assert len(result.curves) == 1 and result.curves[0].log_x


def test_fixing_a_parameter_from_the_advanced_section(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, window: MainWindow
) -> None:
    doc_tab = dose_tab(window)

    def fix_slope(dialog: AnalyzeDialog) -> None:
        table = fixed_table(dialog, "hill_4pl")
        table.set_fixed("hill_slope", True)  # filled in with the example value, 1
        assert table.value("hill_slope") == "1"

    run_dialog(monkeypatch, fix_slope, analysis="hill_4pl")
    doc_tab.analyze()
    settle(qtbot, doc_tab)
    r = rows(doc_tab)
    assert r["Hill slope"] == ("1.000",) and r["SE of Hill slope"] == ("(fixed)",)


def fixed_table(dialog: AnalyzeDialog, type_id: str) -> FixedTable:
    table = dialog.forms[type_id].fixed
    assert table is not None
    return table


def range_editor(dialog: AnalyzeDialog, type_id: str) -> XRangeEditor:
    editor = dialog.forms[type_id].x_range
    assert editor is not None
    return editor


def type_into(qtbot: QtBot, edit: QLineEdit, text: str) -> None:
    edit.selectAll()
    qtbot.keyClick(edit, Qt.Key.Key_Delete)
    if text:
        qtbot.keyClicks(edit, text)


@pytest.mark.parametrize(
    ("low", "high", "message"),
    [
        ("5", "1", "The low end of the X range is above the high end."),
        ("1e", "", "The low end of the X range must be a number"),
        ("", "-", "The high end of the X range must be a number"),
    ],
)
def test_run_is_disabled_until_the_x_range_can_be_read(
    qtbot: QtBot, window: MainWindow, low: str, high: str, message: str
) -> None:
    doc_tab = dose_tab(window)
    dialog = AnalyzeDialog(doc_tab.document, doc_tab.document.tables[0].id)
    qtbot.addWidget(dialog)
    dialog.set_analysis_type("hill_4pl")
    assert dialog.ok_button.isEnabled()
    editor = range_editor(dialog, "hill_4pl")
    type_into(qtbot, editor.low, low)
    type_into(qtbot, editor.high, high)
    assert not dialog.ok_button.isEnabled()
    assert message in dialog.skeleton.feedback.text()
    type_into(qtbot, editor.low, "")
    type_into(qtbot, editor.high, "")
    assert dialog.ok_button.isEnabled()
    assert dialog.params() == Hill4Params()


def test_a_fixed_value_that_is_not_a_number_keeps_run_disabled(
    qtbot: QtBot, window: MainWindow
) -> None:
    doc_tab = dose_tab(window)
    dialog = AnalyzeDialog(doc_tab.document, doc_tab.document.tables[0].id)
    qtbot.addWidget(dialog)
    dialog.set_analysis_type("hill_4pl")
    table = fixed_table(dialog, "hill_4pl")
    table.set_fixed("top", True)
    table.set_value("top", "a lot")
    assert not dialog.ok_button.isEnabled()
    assert "The value of Top must be a number" in dialog.skeleton.feedback.text()
    table.set_value("top", "100")
    assert dialog.ok_button.isEnabled()
    assert dialog.params() == Hill4Params(fixed="Top = 100")


def kinetics_tab(window: MainWindow) -> DocumentTab:
    """X = 9 substrate concentrations, two replicates of a Michaelis-Menten curve."""
    doc_tab = window.new_document()
    doc: Document = doc_tab.document
    table = doc.tables[0]
    s = np.array([0.25, 0.5, 1, 2, 4, 8, 16, 32, 64])
    rng = np.random.default_rng(1)
    doc.execute(RemoveColumn(table.id, table.columns[1].id))
    for i in (1, 2):
        values = (120 * s / (6 + s) + rng.normal(0, 3, len(s))).tolist()
        doc.execute(AddColumn(table.id, Column.new(f"v{i}", "Y", values, id=f"v{i}")))
    doc.execute(GroupColumns(table.id, ["v1", "v2"], "Enzyme", group_id="g"))
    doc.execute(SetCellBlock(table.id, 0, 0, [[repr(float(c))] for c in s]))
    return doc_tab


def test_a_michaelis_menten_fit_reports_vmax_and_km(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, window: MainWindow
) -> None:
    doc_tab = kinetics_tab(window)
    run_dialog(monkeypatch, analysis="michaelis_menten")
    doc_tab.analyze()
    settle(qtbot, doc_tab)
    assert doc_tab.results_panel.combo.currentText() == "Michaelis-Menten · Data 1"
    r = rows(doc_tab)
    assert r["Fit"] == ("OK",)
    assert 100 < float(r["Vmax"][0]) < 140 and 3 < float(r["Km"][0]) < 10
    assert float(r["Lower 95% CI of Km"][0]) < float(r["Km"][0]) < float(r["Upper 95% CI of Km"][0])
    panel = doc_tab.results_panel
    assert panel.content is not None
    reading = panel.content.findChild(QLabel, "reading")
    assert reading is not None and reading.text().startswith("Enzyme has Vmax =")
    assert doc_tab.analysis_engine is not None
    result = doc_tab.analysis_engine.results[doc_tab.document.analyses[0].id]
    assert result.method == "Michaelis-Menten fit (least squares)"
    assert len(result.curves) == 1 and not result.curves[0].log_x


def biphasic_tab(window: MainWindow) -> DocumentTab:
    """X = 15 concentrations, three replicates of a two-phase curve (EC50s 1e-8 and 1e-5)."""
    doc_tab = window.new_document()
    doc: Document = doc_tab.document
    table = doc.tables[0]
    conc = 10.0 ** np.arange(-10, -2.9, 0.5)
    rng = np.random.default_rng(2)
    x = np.log10(conc)
    curve = 100 * (0.4 / (1 + 10 ** (-8 - x)) + 0.6 / (1 + 10 ** (-5 - x)))
    doc.execute(RemoveColumn(table.id, table.columns[1].id))
    for i in (1, 2, 3):
        values = (curve + rng.normal(0, 2, len(conc))).tolist()
        doc.execute(AddColumn(table.id, Column.new(f"R{i}", "Y", values, id=f"r{i}")))
    doc.execute(GroupColumns(table.id, ["r1", "r2", "r3"], "Drug", group_id="g"))
    doc.execute(SetCellBlock(table.id, 0, 0, [[repr(float(c))] for c in conc]))
    return doc_tab


def test_a_biphasic_fit_with_fixed_slopes_draws_on_log_x(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, window: MainWindow
) -> None:
    doc_tab = biphasic_tab(window)

    def fix_slopes(dialog: AnalyzeDialog) -> None:
        table = fixed_table(dialog, "biphasic")
        table.set_fixed("hill_slope_1", True)
        table.set_fixed("hill_slope_2", True)
        assert dialog.params() == TwoPhaseParams(
            x_is_log=False, fixed="Hill slope 1 = 1, Hill slope 2 = 1"
        )

    run_dialog(monkeypatch, fix_slopes, analysis="biphasic")
    doc_tab.analyze()
    settle(qtbot, doc_tab)
    r = rows(doc_tab)
    assert r["Hill slope 1"] == ("1.000",) and r["SE of Hill slope 1"] == ("(fixed)",)
    assert r["SE of Hill slope 2"] == ("(fixed)",)
    assert r["Fit"] == ("OK",)
    assert 0.3 < float(r["Frac"][0]) < 0.5
    assert 1e-9 < float(r["EC50 1"][0]) < 1e-7 and 1e-6 < float(r["EC50 2"][0]) < 1e-4
    assert doc_tab.analysis_engine is not None
    result = doc_tab.analysis_engine.results[doc_tab.document.analyses[0].id]
    assert result.curves and result.curves[0].log_x


def test_the_last_free_parameter_cannot_be_fixed(qtbot: QtBot, window: MainWindow) -> None:
    doc_tab = kinetics_tab(window)
    dialog = AnalyzeDialog(doc_tab.document, doc_tab.document.tables[0].id)
    qtbot.addWidget(dialog)
    dialog.set_analysis_type("michaelis_menten")
    table = fixed_table(dialog, "michaelis_menten")
    table.set_fixed("km", True)
    table.set_value("km", "6")
    table.set_fixed("vmax", True)  # refused: Vmax is the last free parameter
    assert not table.is_fixed("vmax")
    assert dialog.ok_button.isEnabled()
    assert dialog.params() == MichaelisMentenParams(fixed="Km = 6")
    table.set_fixed("km", False)
    table.set_fixed("vmax", True)
    assert table.is_fixed("vmax") and not table.is_fixed("km")


def test_a_stored_fixed_text_that_cannot_be_read_keeps_its_message(
    qtbot: QtBot, window: MainWindow
) -> None:
    """A text the table cannot show (it never came from the table) keeps the parser's
    message, and is kept as it is until the table is changed."""
    doc_tab = kinetics_tab(window)
    table_id = doc_tab.document.tables[0].id
    stored = Analysis(
        "michaelis_menten", table_id, ("g",), MichaelisMentenParams(fixed="top = 100")
    )
    dialog = AnalyzeDialog(doc_tab.document, table_id, analysis=stored)
    qtbot.addWidget(dialog)
    assert not dialog.ok_button.isEnabled()
    assert "where the name is Vmax or Km" in dialog.skeleton.feedback.text()
    table = fixed_table(dialog, "michaelis_menten")
    assert not table.is_fixed("vmax") and not table.is_fixed("km")
    table.set_fixed("km", True)
    assert dialog.ok_button.isEnabled()
    assert dialog.params() == MichaelisMentenParams(fixed="Km = 2")
