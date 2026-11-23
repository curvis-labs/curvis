"""The redesigned Analyze dialog (step 3.8): the grouped list and its filter, keyboard
use, the X range fields and the Fix table, About this analysis, and analyses saved
before the redesign."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLineEdit
from pytestqt.qtbot import QtBot

from curvis.analysis.fit.analyses import Hill4Params
from curvis.io.project import PROJECT_JSON, load, save
from curvis.ui.analyze_dialog import AnalyzeDialog
from curvis.ui.analyze_options import FixedTable, XRangeEditor
from tests.ui.dialog_samples import grouped_document

BEFORE = Path(__file__).parents[1] / "data" / "projects" / "before_analyze_redesign.cvp"


@pytest.fixture
def fit_dialog(qtbot: QtBot) -> AnalyzeDialog:
    dialog = AnalyzeDialog(grouped_document(), "t", recent=["gaussian", "hill_4pl"])
    qtbot.addWidget(dialog)
    dialog.set_analysis_type("hill_4pl")
    return dialog


def x_range(dialog: AnalyzeDialog, type_id: str = "hill_4pl") -> XRangeEditor:
    editor = dialog.forms[type_id].x_range
    assert editor is not None
    return editor


def fixed(dialog: AnalyzeDialog, type_id: str = "hill_4pl") -> FixedTable:
    table = dialog.forms[type_id].fixed
    assert table is not None
    return table


def type_into(qtbot: QtBot, edit: QLineEdit, text: str) -> None:
    edit.selectAll()
    qtbot.keyClick(edit, Qt.Key.Key_Delete)
    qtbot.keyClicks(edit, text)


# --- the list ------------------------------------------------------------------------------


def test_fits_are_grouped_in_the_sections_of_their_types(fit_dialog: AnalyzeDialog) -> None:
    sections = [s.text(0) for s in fit_dialog._sections()]
    assert sections == ["Recent", "Linear", "Dose-response", "Kinetics", "Shape-based"]
    assert fit_dialog.recent_types() == ["gaussian", "hill_4pl"]
    assert fit_dialog.listed_types()[:3] == [
        "linear_regression",
        "polynomial_regression",
        "hill_4pl",
    ]
    row = fit_dialog._items_of("hill_4pl")[1]
    assert (row.text(0), row.text(1)) == (
        "Hill 4PL (variable slope)",
        "EC50, Hill slope, top, bottom",
    )


def test_recent_shows_at_most_three(qtbot: QtBot) -> None:
    recent = ["gaussian", "hill_4pl", "exp_decay", "linear_regression"]
    dialog = AnalyzeDialog(grouped_document(), "t", recent=recent)
    qtbot.addWidget(dialog)
    assert dialog.family is not None and dialog.family.value == "fit"
    assert dialog.recent_types() == recent[:3]
    assert dialog.analysis_type is not None and dialog.analysis_type.id == "gaussian"


def test_the_filter_matches_names_and_what_they_report(
    qtbot: QtBot, fit_dialog: AnalyzeDialog
) -> None:
    qtbot.keyClicks(fit_dialog.filter_edit, "ec50")
    assert fit_dialog.visible_types() == ["hill_4pl", "hill_3pl", "biphasic", "bell_shaped"]
    type_into(qtbot, fit_dialog.filter_edit, "Decay")
    assert fit_dialog.visible_types() == ["exp_decay"]
    assert fit_dialog.analysis_type is not None and fit_dialog.analysis_type.id == "exp_decay"
    assert not fit_dialog.no_match.isVisibleTo(fit_dialog)
    type_into(qtbot, fit_dialog.filter_edit, "zzz")
    assert fit_dialog.visible_types() == []
    assert fit_dialog.no_match.isVisibleTo(fit_dialog)
    assert fit_dialog.no_match.text() == "No analysis matches “zzz”."
    assert fit_dialog.analysis_type is not None and fit_dialog.analysis_type.id == "exp_decay"
    fit_dialog.filter_edit.clear()
    assert "gaussian" in fit_dialog.recent_types()
    assert len(fit_dialog.visible_types()) == len(fit_dialog.listed_types()) + 2


def test_arrow_keys_move_from_the_filter_into_the_list_and_enter_runs(
    qtbot: QtBot, fit_dialog: AnalyzeDialog
) -> None:
    fit_dialog.show()
    qtbot.waitExposed(fit_dialog)
    fit_dialog.filter_edit.setFocus()
    qtbot.keyClicks(fit_dialog.filter_edit, "exponential")
    assert fit_dialog.analysis_type is not None
    assert fit_dialog.analysis_type.id == "exp_association"
    qtbot.keyClick(fit_dialog.filter_edit, Qt.Key.Key_Down)
    assert fit_dialog.analysis_type is not None and fit_dialog.analysis_type.id == "exp_decay"
    qtbot.keyClick(fit_dialog.filter_edit, Qt.Key.Key_Down)  # stays on the last one
    assert fit_dialog.analysis_type.id == "exp_decay"
    qtbot.keyClick(fit_dialog.filter_edit, Qt.Key.Key_Up)
    assert fit_dialog.analysis_type.id == "exp_association"
    assert fit_dialog.focusWidget() is fit_dialog.filter_edit  # the arrows did not move it
    with qtbot.waitSignal(fit_dialog.accepted, timeout=1000):
        qtbot.keyClick(fit_dialog.filter_edit, Qt.Key.Key_Return)
    assert fit_dialog.result() == QDialog.DialogCode.Accepted
    assert fit_dialog.analysis().type == "exp_association"


def test_arrow_keys_in_the_list_skip_the_section_headers(
    qtbot: QtBot, fit_dialog: AnalyzeDialog
) -> None:
    fit_dialog.show()
    qtbot.waitExposed(fit_dialog)
    fit_dialog.set_analysis_type("polynomial_regression")
    fit_dialog.analysis_list.setFocus()
    qtbot.keyClick(fit_dialog.analysis_list, Qt.Key.Key_Down)  # over "Dose-response"
    assert fit_dialog.analysis_type is not None and fit_dialog.analysis_type.id == "hill_4pl"
    qtbot.keyClick(fit_dialog.analysis_list, Qt.Key.Key_Up)
    qtbot.keyClick(fit_dialog.analysis_list, Qt.Key.Key_Up)  # over "Linear"
    assert fit_dialog.analysis_type.id == "linear_regression"
    with qtbot.waitSignal(fit_dialog.accepted, timeout=1000):
        qtbot.keyClick(fit_dialog.analysis_list, Qt.Key.Key_Return)


def test_the_list_scrolls_after_about_six_rows(fit_dialog: AnalyzeDialog) -> None:
    rows = fit_dialog.analysis_list.sizeHint().height() / fit_dialog.analysis_list._row_height()
    assert 6 <= rows < 7.5
    assert len(fit_dialog._analysis_items()) + len(fit_dialog._sections()) > 7


def test_choosing_an_analysis_shows_its_options_and_about(
    qtbot: QtBot, fit_dialog: AnalyzeDialog
) -> None:
    fit_dialog.analysis_list.setCurrentItem(fit_dialog._items_of("michaelis_menten")[0])
    assert fit_dialog.options.currentWidget() is fit_dialog.forms["michaelis_menten"]
    assert fit_dialog.equation.text() == "Y = Vmax&nbsp;·&nbsp;X&nbsp;/&nbsp;(Km + X)"
    assert not fit_dialog.equation_note.isVisibleTo(fit_dialog)
    assert fit_dialog.use_when.text().startswith("Use when enzyme velocity")
    assert not fit_dialog.hypothesis.isVisibleTo(fit_dialog)
    assert fit_dialog.sketch.data is not None


# --- About this analysis -----------------------------------------------------------------


def test_a_test_shows_its_hypothesis_and_assumptions(qtbot: QtBot) -> None:
    dialog = AnalyzeDialog(grouped_document(), "t")
    qtbot.addWidget(dialog)
    dialog.set_analysis_type("t_test")
    assert "Null hypothesis:" in dialog.hypothesis.text()
    assert dialog.hypothesis.text().endswith("The two means are equal.")
    assert "the SDs may differ" in dialog.assumptions.text()
    assert not dialog.sketch.isVisibleTo(dialog) and not dialog.equation.isVisibleTo(dialog)
    assert "Mann-Whitney" in dialog.use_when.text()
    combo = dialog.forms["t_test"].fields["test"]
    assert hasattr(combo, "findData")
    combo.setCurrentIndex(combo.findData("paired"))  # type: ignore[attr-defined]
    assert dialog.hypothesis.text().endswith("The mean of the differences is 0.")
    dialog.set_analysis_type("descriptive_statistics")
    assert not dialog.hypothesis.isVisibleTo(dialog)
    assert not dialog.assumptions.isVisibleTo(dialog)


def test_the_sketch_follows_a_fixed_value(qtbot: QtBot, fit_dialog: AnalyzeDialog) -> None:
    table = fixed(fit_dialog)
    data = fit_dialog.sketch.data
    assert data is not None and not any(m.fixed for m in data.markers)
    table.set_fixed("top", True)
    table.set_value("top", "80")
    data = fit_dialog.sketch.data
    assert data is not None
    top = next(m for m in data.markers if m.fixed)
    assert (top.position, top.label) == (80, "Top = 80 (fixed)")
    assert max(data.y) == pytest.approx(80, rel=1e-3)
    table.set_fixed("top", False)
    data = fit_dialog.sketch.data
    assert data is not None and not any(m.fixed for m in data.markers)


# --- the X range and the Fix table round-trip to the stored strings ----------------------


def test_the_x_range_fields_write_the_stored_text(qtbot: QtBot, fit_dialog: AnalyzeDialog) -> None:
    editor = x_range(fit_dialog)
    assert editor.count.text() == "all 48 points"
    type_into(qtbot, editor.low, "0.03")
    type_into(qtbot, editor.high, "10")
    assert fit_dialog.params() == Hill4Params(x_range="0.03 to 10")
    assert editor.count.text() == "36 of 48 points"
    type_into(qtbot, editor.high, "")
    editor.high.clear()
    editor._on_edited()
    assert fit_dialog.params() == Hill4Params(x_range="0.03 to")
    editor.low.clear()
    type_into(qtbot, editor.high, "1e-1")
    assert fit_dialog.params() == Hill4Params(x_range="to 1e-1")
    assert editor.count.text() == "18 of 48 points"
    editor.high.clear()
    editor._on_edited()
    assert fit_dialog.params() == Hill4Params()


def test_the_fix_table_writes_the_stored_text(fit_dialog: AnalyzeDialog) -> None:
    table = fixed(fit_dialog)
    assert [table.labels[n] for n in table.names] == ["Bottom", "Top", "LogEC50", "Hill slope"]
    table.set_fixed("bottom", True)
    assert table.value("bottom") == "0"  # the example value
    table.set_fixed("hill_slope", True)
    table.set_value("hill_slope", "1.5")
    assert fit_dialog.params() == Hill4Params(fixed="Bottom = 0, Hill slope = 1.5")
    table.set_fixed("bottom", False)
    assert fit_dialog.params() == Hill4Params(fixed="Hill slope = 1.5")
    table.set_fixed("bottom", True)  # it keeps the value typed before
    table.set_value("bottom", "5")
    table.set_fixed("bottom", False)
    table.set_fixed("bottom", True)
    assert table.value("bottom") == "5"


def test_every_parameter_but_one_can_be_fixed(fit_dialog: AnalyzeDialog) -> None:
    table = fixed(fit_dialog)
    for name in ("bottom", "top", "log_ec50", "hill_slope"):
        table.set_fixed(name, True)
    assert [table.is_fixed(n) for n in table.names] == [True, True, True, False]
    item = table.item(3, 0)
    assert item is not None and not item.flags() & Qt.ItemFlag.ItemIsUserCheckable
    assert fit_dialog.ok_button.isEnabled()


@pytest.mark.parametrize(
    ("stored_range", "low", "high", "stored_fixed", "shown"),
    [
        (
            "1e-9 … 1e-5",
            "1e-9",
            "1e-5",
            "top = 100; hill slope = 1",
            {"top": "100", "hill_slope": "1"},
        ),
        ("to 5", "", "5", "Bottom=0", {"bottom": "0"}),
        ("-9 to -5", "-9", "-5", "", {}),
    ],
)
def test_stored_texts_show_in_the_fields_and_stay_as_they_were(
    qtbot: QtBot,
    stored_range: str,
    low: str,
    high: str,
    stored_fixed: str,
    shown: dict[str, str],
) -> None:
    from curvis.analysis.base import Analysis

    doc = grouped_document()
    inputs = (doc.table("t").replicate_groups[0].id,)
    params = Hill4Params(x_is_log=True, x_range=stored_range, fixed=stored_fixed)
    stored = Analysis("hill_4pl", "t", inputs, params)
    dialog = AnalyzeDialog(doc, "t", analysis=stored)
    qtbot.addWidget(dialog)
    editor, table = x_range(dialog), fixed(dialog)
    assert (editor.low.text(), editor.high.text()) == (low, high)
    assert {n: table.value(n) for n in table.names if table.is_fixed(n)} == shown
    assert dialog.analysis() == stored  # nothing rewritten until a field changes


# --- projects saved before this step --------------------------------------------------------


def test_a_project_saved_before_the_redesign_edits_unchanged(qtbot: QtBot, tmp_path: Path) -> None:
    doc = load(BEFORE)
    assert len(doc.analyses) == 8
    for analysis in doc.analyses:
        dialog = AnalyzeDialog(doc, analysis.table_id, analysis=analysis)
        qtbot.addWidget(dialog)
        assert dialog.analysis_type is not None and dialog.analysis_type.id == analysis.type
        assert dialog.ok_button.isEnabled(), (analysis.type, dialog.skeleton.feedback.text())
        assert dialog.analysis() == analysis, analysis.type
    out = tmp_path / "again.cvp"
    save(doc, out)
    with zipfile.ZipFile(BEFORE) as a, zipfile.ZipFile(out) as b:
        before = json.loads(a.read(PROJECT_JSON))
        after = json.loads(b.read(PROJECT_JSON))
    assert after["schema_version"] == before["schema_version"]
    assert after["document"]["analyses"] == before["document"]["analyses"]


def test_editing_a_saved_fit_rewrites_only_what_changed(qtbot: QtBot) -> None:
    doc = load(BEFORE)
    analysis = next(a for a in doc.analyses if a.id == "a1")
    dialog = AnalyzeDialog(doc, analysis.table_id, analysis=analysis)
    qtbot.addWidget(dialog)
    table = fixed(dialog)
    assert table.is_fixed("top") and table.is_fixed("hill_slope")
    table.set_fixed("hill_slope", False)
    edited = dialog.analysis()
    assert edited.params == Hill4Params(x_range="1e-9 … 1e-5", fixed="Top = 100")
    assert edited.id == analysis.id
