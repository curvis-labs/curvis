"""pytest-qt tests for the Calculate dialog, calculated columns in the table, and constants."""

import math
from collections.abc import Callable

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot

from curvis.core import CalculationKind, Document, GroupColumns
from curvis.core import calculation_formulas as cf
from curvis.data import Column, Table
from curvis.ui.calculate_dialog import CalculateDialog
from curvis.ui.constants_dialog import ConstantsDialog, Scope
from curvis.ui.document_tab import DocumentTab
from curvis.ui.main_window import MainWindow
from curvis.ui.table.model import CALCULATED_MARK, ERROR_MARK, TableModel

NAN = math.nan

type Setup = Callable[[CalculateDialog], None]


@pytest.fixture
def tab(window: MainWindow) -> DocumentTab:
    """A new document; its table holds X = 0, 1, 2, 3 and A = 2, 4, 6, 10."""
    tab = window.new_document()
    assert tab.table_view is not None
    tab.table_view.model().write_block(0, 0, [["0", "2"], ["1", "4"], ["2", "6"], ["3", "10"]])
    return tab


def model_of(tab: DocumentTab) -> TableModel:
    assert tab.table_view is not None
    return tab.table_view.model()


def table_of(tab: DocumentTab) -> Table:
    return tab.document.tables[0]


def settle(qtbot: QtBot, tab: DocumentTab) -> None:
    assert tab.engine is not None
    engine = tab.engine
    qtbot.waitUntil(lambda: not engine.busy and not engine.recalculator.scheduler.busy)


def run_dialog(
    monkeypatch: pytest.MonkeyPatch, setup: Setup | None = None, accept: bool = True
) -> list[CalculateDialog]:
    """Make the tab's dialogs run ``setup`` and press OK (or Cancel); returns the dialogs."""
    shown: list[CalculateDialog] = []

    def exec_dialog(self: DocumentTab, dialog: QDialog) -> int:
        assert isinstance(dialog, CalculateDialog)
        shown.append(dialog)
        if setup is not None:
            setup(dialog)
        if accept and dialog.ok_button.isEnabled():
            return QDialog.DialogCode.Accepted
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(DocumentTab, "_exec_dialog", exec_dialog)
    return shown


def column_values(tab: DocumentTab, name: str) -> list[float]:
    column = table_of(tab).column_by_name(name)
    assert column is not None, name
    return list(column.values)


def select_column(tab: DocumentTab, name: str) -> None:
    assert tab.table_view is not None
    table = table_of(tab)
    column = table.column_by_name(name)
    assert column is not None
    position = table.column_index(column.id)
    tab.table_view.select_columns(position, position)


# --- the four options ------------------------------------------------------------------


def test_baseline_subtraction_with_one_click(
    qtbot: QtBot, tab: DocumentTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown = run_dialog(monkeypatch)
    select_column(tab, "A")
    assert tab.calculate()
    dialog = shown[0]
    assert dialog.kind is CalculationKind.BASELINE
    assert dialog.formula == "{A} - FIRST({A})"
    settle(qtbot, tab)
    assert column_values(tab, "A_bl") == [0, 2, 4, 8]
    assert tab.document.undo_stack.undo_text == "Calculate baseline subtraction for 1 column"


def test_baseline_subtraction_of_a_value(
    qtbot: QtBot, tab: DocumentTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    def setup(dialog: CalculateDialog) -> None:
        dialog.baseline_options.value_radio.setChecked(True)
        dialog.baseline_options.value_edit.setText("1.5")

    run_dialog(monkeypatch, setup)
    assert tab.calculate()
    settle(qtbot, tab)
    assert tab.document.calculations[0].formula == "{A} - 1.5"
    assert column_values(tab, "A_bl") == [0.5, 2.5, 4.5, 8.5]


def test_normalize_to_own_minimum_and_maximum(
    qtbot: QtBot, tab: DocumentTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dialog(monkeypatch, lambda d: d.set_kind(CalculationKind.NORMALIZE))
    assert tab.calculate()
    settle(qtbot, tab)
    assert column_values(tab, "A_norm") == [0, 25, 50, 100]


def test_normalize_to_chosen_values(
    qtbot: QtBot, tab: DocumentTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    def setup(dialog: CalculateDialog) -> None:
        dialog.set_kind(CalculationKind.NORMALIZE)
        dialog.normalize_options.values.setChecked(True)
        dialog.normalize_options.low.setText("2")
        dialog.normalize_options.high.setText("6")

    run_dialog(monkeypatch, setup)
    assert tab.calculate()
    settle(qtbot, tab)
    assert tab.document.calculations[0].formula == "({A} - 2) / (6 - 2) * 100"
    assert column_values(tab, "A_norm") == [0, 50, 100, 200]


def test_fold_change_relative_to_a_reference_value(
    qtbot: QtBot, tab: DocumentTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    def setup(dialog: CalculateDialog) -> None:
        dialog.set_kind(CalculationKind.FOLD_CHANGE)
        dialog.fold_options.value_radio.setChecked(True)
        dialog.fold_options.value_edit.setText("4")

    run_dialog(monkeypatch, setup)
    assert tab.calculate()
    settle(qtbot, tab)
    assert column_values(tab, "A_fc") == [0.5, 1, 1.5, 2.5]


def test_custom_formula_with_columns_functions_and_constants(
    qtbot: QtBot, tab: DocumentTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    def setup(dialog: CalculateDialog) -> None:
        dialog.set_kind(CalculationKind.CUSTOM)
        dialog.set_formula("{A} * [X] + MAX([A]) + pi")

    run_dialog(monkeypatch, setup)
    assert tab.calculate()
    settle(qtbot, tab)
    np.testing.assert_allclose(
        column_values(tab, "A_calc"), [10 + math.pi, 14 + math.pi, 22 + math.pi, 40 + math.pi]
    )


def test_editing_a_generated_formula_makes_it_custom(
    tab: DocumentTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    def setup(dialog: CalculateDialog) -> None:
        assert dialog.kind is CalculationKind.BASELINE
        dialog.set_formula("{A} - FIRST({A}) + 1")
        assert dialog.kind is CalculationKind.CUSTOM
        assert dialog.formula == "{A} - FIRST({A}) + 1"

    run_dialog(monkeypatch, setup)
    assert tab.calculate()
    assert tab.document.calculations[0].kind is CalculationKind.CUSTOM


def test_invalid_formula_disables_ok_and_explains(
    tab: DocumentTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    def setup(dialog: CalculateDialog) -> None:
        dialog.set_formula("{A} + [Nope]")
        assert not dialog.ok_button.isEnabled()
        assert "Unknown column 'Nope'" in dialog.formula_error.text()
        assert dialog.highlighter.error_span == (6, 12)

    shown = run_dialog(monkeypatch, setup)
    assert not tab.calculate()
    assert shown and tab.document.calculations == []


def test_palette_inserts_at_the_cursor(tab: DocumentTab, monkeypatch: pytest.MonkeyPatch) -> None:
    def setup(dialog: CalculateDialog) -> None:
        dialog.set_kind(CalculationKind.CUSTOM)
        dialog.set_formula("")
        dialog.palette_buttons["functions"].setChecked(True)
        mean = next(
            dialog.palette_list.item(i)
            for i in range(dialog.palette_list.count())
            if dialog.palette_list.item(i).text().startswith("MEAN")
        )
        dialog.palette_list.itemActivated.emit(mean)
        dialog.palette_buttons["columns"].setChecked(True)
        dialog.palette_list.itemActivated.emit(dialog.palette_list.item(0))
        assert dialog.formula == "MEAN({A})"
        dialog.palette_buttons["constants"].setChecked(True)
        texts = [dialog.palette_list.item(i).text() for i in range(dialog.palette_list.count())]
        assert any(t.startswith("N_A") and "Avogadro constant" in t for t in texts)

    run_dialog(monkeypatch, setup, accept=False)
    tab.calculate()


# --- several columns and pairing -------------------------------------------------------


@pytest.fixture
def lab_tab(window: MainWindow) -> DocumentTab:
    """Time, replicate groups baseline (b1..b3) and sample (s1..s3)."""
    doc = Document()
    columns = [Column.new("Time", "X", [0, 1], id="t")]
    columns += [Column.new(f"baseline {i}", "Y", [i, 2 * i], id=f"b{i}") for i in (1, 2, 3)]
    columns += [Column.new(f"sample {i}", "Y", [10 * i, 20 * i], id=f"s{i}") for i in (1, 2, 3)]
    doc.tables.append(Table("Data", columns, id="T"))
    doc.execute(GroupColumns("T", ["b1", "b2", "b3"], "baseline"))
    doc.execute(GroupColumns("T", ["s1", "s2", "s3"], "sample"))
    return window._add_tab(doc, None)


def test_all_selected_columns_get_a_calculated_column(
    qtbot: QtBot, lab_tab: DocumentTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    def setup(dialog: CalculateDialog) -> None:
        dialog.set_kind(CalculationKind.FOLD_CHANGE)
        assert dialog.pairing().a_columns == ("b1", "b2", "b3", "s1", "s2", "s3")

    run_dialog(monkeypatch, setup)
    assert lab_tab.calculate()  # nothing selected but a cell: every Y column
    settle(qtbot, lab_tab)
    assert column_values(lab_tab, "sample 2_fc") == [1, 2]
    table = table_of(lab_tab)
    group_names = [g.name for g in table.replicate_groups]
    assert group_names == ["baseline", "sample", "baseline_fc", "sample_fc"]


def test_paired_baseline_subtracts_the_mean_of_role_a(
    qtbot: QtBot, lab_tab: DocumentTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    def setup(dialog: CalculateDialog) -> None:
        dialog.set_paired(True)  # the first group gets role A, the other role B
        pairing = dialog.pairing()
        assert pairing.a_columns == ("b1", "b2", "b3")
        assert pairing.b_columns == ("s1", "s2", "s3")
        assert dialog.formula == "{B} - {A:mean}"
        texts = [dialog.palette_list.item(i).text() for i in range(dialog.palette_list.count())]
        assert texts[0].startswith("{B}") and any("{A:mean}" in t for t in texts)

    run_dialog(monkeypatch, setup)
    assert lab_tab.calculate()
    settle(qtbot, lab_tab)
    # Mean of the baselines is (2, 4).
    assert column_values(lab_tab, "sample 1_bl") == [8, 16]
    assert column_values(lab_tab, "sample 3_bl") == [28, 56]
    assert table_of(lab_tab).column_by_name("baseline 1_bl") is None


def test_paired_index_matched_with_renamed_roles(
    qtbot: QtBot, lab_tab: DocumentTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    def setup(dialog: CalculateDialog) -> None:
        dialog.set_paired(True)
        dialog.a_name.setText("Ctrl")
        dialog.b_name.setText("Drug")
        dialog.baseline_options.matched.setChecked(True)
        assert dialog.formula == "{Drug} - {Ctrl}"

    run_dialog(monkeypatch, setup)
    assert lab_tab.calculate()
    settle(qtbot, lab_tab)
    assert column_values(lab_tab, "sample 2_bl") == [18, 36]


def test_pairing_problems_disable_ok(lab_tab: DocumentTab, monkeypatch: pytest.MonkeyPatch) -> None:
    def setup(dialog: CalculateDialog) -> None:
        dialog.set_paired(True)
        dialog.set_role(["s3"], "A")  # role A: 4 columns, role B: 2
        assert not dialog.ok_button.isEnabled()
        assert "same number" in dialog.problem_label.text()
        dialog.set_checked(["b1", "s1", "s2"])  # one A column goes with every B column
        assert dialog.problem() is None
        dialog.set_kind(CalculationKind.NORMALIZE)
        assert not dialog.paired.isEnabled() and not dialog.pairing().paired

    run_dialog(monkeypatch, setup, accept=False)
    lab_tab.calculate()


# --- live updates, editing and read-only --------------------------------------------------


def test_editing_a_source_cell_updates_the_calculated_column(
    qtbot: QtBot, tab: DocumentTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dialog(monkeypatch)
    select_column(tab, "A")
    assert tab.calculate()
    settle(qtbot, tab)
    model = model_of(tab)
    assert model.index(3, 2).data() == "8.0000"  # calculated columns use the decimal places

    assert tab.table_view is not None
    tab.table_view.setCurrentIndex(model.index(0, 1))
    qtbot.keyClicks(tab.table_view, "0")
    qtbot.keyClick(tab.table_view.focusWidget(), Qt.Key.Key_Return)
    settle(qtbot, tab)
    assert column_values(tab, "A_bl") == [0, 4, 6, 10]
    assert model.index(3, 2).data() == "10.0000"

    tab.document.undo()
    settle(qtbot, tab)
    assert column_values(tab, "A_bl") == [0, 2, 4, 8]


def test_calculated_columns_are_marked_and_read_only(
    qtbot: QtBot, tab: DocumentTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dialog(monkeypatch)
    assert tab.calculate()
    settle(qtbot, tab)
    model = model_of(tab)
    assert tab.table_view is not None
    header = model.headerData(2, Qt.Orientation.Horizontal)
    assert header == f"A_bl\n{CALCULATED_MARK} Y"
    tip = model.headerData(2, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole)
    assert "Calculated: {A} - FIRST({A})" in str(tip)
    assert not model.flags(model.index(0, 2)) & Qt.ItemFlag.ItemIsEditable
    assert model.flags(model.index(0, 1)) & Qt.ItemFlag.ItemIsEditable

    with qtbot.waitSignal(tab.table_view.error) as blocker:
        tab.table_view.setCurrentIndex(model.index(0, 2))
        qtbot.keyClicks(tab.table_view, "5")
    assert "is calculated" in blocker.args[0]
    assert column_values(tab, "A_bl")[0] == 0

    tab.table_view.select_columns(1, 2)  # Delete clears the typed column only
    tab.table_view.clear_selected_cells()
    settle(qtbot, tab)
    assert np.isnan(column_values(tab, "A")).all()
    assert tab.document.is_calculated(table_of(tab).columns[2].id)


def test_formula_error_shows_in_the_header(
    qtbot: QtBot, tab: DocumentTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dialog(monkeypatch, lambda d: d.set_formula("{A} * [X]"))
    assert tab.calculate()
    settle(qtbot, tab)
    model = model_of(tab)
    model.remove_columns([0])  # remove X: the formula now fails
    settle(qtbot, tab)
    header = model.headerData(1, Qt.Orientation.Horizontal)
    assert str(header).endswith(ERROR_MARK)
    tip = str(model.headerData(1, Qt.Orientation.Horizontal, Qt.ItemDataRole.ToolTipRole))
    assert "Unknown column 'X'" in tip


def test_double_click_reopens_the_dialog_with_its_definition(
    qtbot: QtBot, tab: DocumentTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    def create(dialog: CalculateDialog) -> None:
        dialog.set_kind(CalculationKind.FOLD_CHANGE)
        dialog.fold_options.value_radio.setChecked(True)
        dialog.fold_options.value_edit.setText("2")

    run_dialog(monkeypatch, create)
    assert tab.calculate()
    settle(qtbot, tab)

    def change(dialog: CalculateDialog) -> None:
        assert dialog.windowTitle() == "Edit calculation"
        assert dialog.ok_button.text() == "Update"
        assert dialog.kind is CalculationKind.FOLD_CHANGE
        assert dialog.fold_options.value_radio.isChecked()
        assert dialog.fold_options.value_edit.text() == "2"
        assert dialog.formula == "{A} / 2"
        dialog.fold_options.value_edit.setText("4")

    shown = run_dialog(monkeypatch, change)
    view = tab.table_view
    assert view is not None
    model = model_of(tab)
    index = model.index(1, 2)
    rect = view.visualRect(index)
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    qtbot.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert len(shown) == 1
    settle(qtbot, tab)
    assert column_values(tab, "A_fc") == [0.5, 1, 1.5, 2.5]
    assert len(tab.document.calculations) == 1
    tab.document.undo()
    settle(qtbot, tab)
    assert column_values(tab, "A_fc") == [1, 2, 3, 5]


def test_reopened_custom_formula_stays_custom(
    tab: DocumentTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dialog(monkeypatch, lambda d: d.set_formula("{A} - FIRST({A}) + 1"))
    assert tab.calculate()
    output = tab.document.calculations[0].columns[0].column_id

    def check(dialog: CalculateDialog) -> None:
        assert dialog.kind is CalculationKind.CUSTOM
        assert dialog.formula == "{A} - FIRST({A}) + 1"

    shown = run_dialog(monkeypatch, check, accept=False)
    assert not tab.edit_calculation(output)
    assert shown


def test_calculate_actions_are_in_the_command_palette(window: MainWindow) -> None:
    window.new_document()
    ids = {entry.id for entry in window.registry.entries()}
    assert {"table.calculate", "table.edit_calculation", "table.constants"} <= ids


# --- constants ------------------------------------------------------------------------


def test_constants_dialog_project_constants_are_undoable_and_recompute(
    qtbot: QtBot, window: MainWindow, tab: DocumentTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = ConstantsDialog(tab.document, window.settings)
    qtbot.addWidget(dialog)
    assert dialog.add(Scope.PROJECT, "k", 2.0, "1/s")
    assert dialog.tables[Scope.PROJECT].item(0, 0).text() == "k"
    run_dialog(monkeypatch, lambda d: d.set_formula("{A} * k"))
    assert tab.calculate()
    settle(qtbot, tab)
    assert column_values(tab, "A_bl") == [4, 8, 12, 20]

    constant_id = tab.document.constants[0].id
    assert dialog.edit(Scope.PROJECT, constant_id, "k", 3.0)
    settle(qtbot, tab)
    assert column_values(tab, "A_bl") == [6, 12, 18, 30]
    assert not dialog.add(Scope.PROJECT, "k", 1.0)
    assert "already a project constant" in dialog.error.text()
    assert not dialog.add(Scope.PROJECT, "R", 1.0)
    assert "built-in" in dialog.error.text()

    assert dialog.delete(Scope.PROJECT, constant_id)
    settle(qtbot, tab)
    assert np.isnan(column_values(tab, "A_bl")).all()
    tab.document.undo()  # the constant is back
    settle(qtbot, tab)
    assert column_values(tab, "A_bl") == [6, 12, 18, 30]


def test_constants_dialog_global_constants_reach_open_projects(
    qtbot: QtBot, window: MainWindow, tab: DocumentTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = ConstantsDialog(tab.document, window.settings)
    qtbot.addWidget(dialog)
    assert dialog.add(Scope.GLOBAL, "g", 10.0)
    assert [c.name for c in window.settings.global_constants] == ["g"]
    run_dialog(monkeypatch, lambda d: d.set_formula("{A} + g"))
    assert tab.calculate()
    settle(qtbot, tab)
    assert column_values(tab, "A_bl") == [12, 14, 16, 20]

    constant_id = window.settings.global_constants[0].id
    assert dialog.edit(Scope.GLOBAL, constant_id, "g", 100.0)
    settle(qtbot, tab)
    assert column_values(tab, "A_bl") == [102, 104, 106, 110]
    assert dialog.add(Scope.PROJECT, "g", 0.0)  # a project constant wins
    settle(qtbot, tab)
    assert column_values(tab, "A_bl") == [2, 4, 6, 10]
    assert not dialog.add(Scope.GLOBAL, "g", 1.0)
    assert dialog.delete(Scope.GLOBAL, constant_id)
    assert window.settings.global_constants == []


def test_constants_dialog_from_the_menu(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[QDialog] = []
    monkeypatch.setattr(MainWindow, "_exec_constants_dialog", lambda self, d: seen.append(d))
    window.new_document()
    window.registry.action("table.constants").trigger()
    assert isinstance(seen[0], ConstantsDialog)
    builtin = seen[0].tabs.widget(2)
    assert seen[0].tabs.tabText(2) == "&Built-in" and builtin is not None


def test_generated_params_reach_the_document(
    tab: DocumentTab, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dialog(monkeypatch)
    assert tab.calculate()
    (calculation,) = tab.document.calculations
    assert calculation.params["reference"] == cf.FIRST
    assert calculation.suffix == "_bl"
