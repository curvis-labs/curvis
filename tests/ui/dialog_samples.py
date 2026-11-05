"""Every dialog of the app, filled with typical data: for the design tests and screenshots.

Each sample builds one dialog. A dialog that reads in the background (Import) is shown
only after its first result, so the tests and screenshots see what the user sees.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import QApplication, QComboBox, QDialog

from curvis.compute import Scheduler
from curvis.core import AddConstant, AddTable, Calculation, CalculationKind, Document
from curvis.core import calculation_formulas as cf
from curvis.core.calculation_commands import add_calculation
from curvis.core.constants import ProjectConstant
from curvis.data import Column, ColumnRole, Pairing, Table
from curvis.settings import Settings
from curvis.ui.analyze_dialog import AnalyzeDialog
from curvis.ui.calculate_dialog import CalculateDialog
from curvis.ui.close_table_dialog import CloseTableDialog
from curvis.ui.constants_dialog import ConstantEditor, ConstantsDialog
from curvis.ui.import_dialog import ImportDialog
from curvis.ui.settings_dialog import SettingsDialog
from curvis.ui.table.bulk_rename import BulkRenameDialog

CSV = Path(__file__).parents[1] / "data" / "csv" / "plate_reader.csv"


@dataclass
class Context:
    """What the dialogs need: settings and a scheduler for background reads."""

    settings: Settings
    scheduler: Scheduler


def sample_document() -> Document:
    """A dose-response table: Dose (X), three control and three drug replicates."""
    doses = [0.01, 0.03, 0.1, 0.3, 1, 3, 10, 30]
    columns = [Column.new("Dose", ColumnRole.X, doses, id="dose")]
    for name, base in (("Control", 10.0), ("Drug", 50.0)):
        for i in range(1, 4):
            values = [base + i * 0.4 + d for d in range(len(doses))]
            columns.append(Column.new(f"{name} {i}", ColumnRole.Y, values, id=f"{name}{i}"))
    doc = Document()
    doc.execute(AddTable(Table("Data 1", columns, id="t")))
    doc.execute(AddConstant(ProjectConstant("k_on", 1.2e6, "1/(M·s)")))
    calculation = Calculation(
        table_id="t",
        formula=cf.generate(
            CalculationKind.NORMALIZE,
            {"range": cf.OWN_RANGE},
            Pairing(("Drug1",), (), False),
            doc.table("t"),
        )
        or "",
        pairing=Pairing(("Drug1",), (), False),
        kind=CalculationKind.NORMALIZE,
        params={"range": cf.OWN_RANGE},
        suffix=cf.DEFAULT_SUFFIXES[CalculationKind.NORMALIZE],
    )
    doc.execute(add_calculation(doc, calculation))
    doc.undo_stack.clear()
    return doc


def _calculate(ctx: Context) -> QDialog:
    doc = sample_document()
    return CalculateDialog(
        doc, "t", selected=["Drug1", "Drug2", "Drug3"], manage_constants=lambda: None
    )


def _calculate_paired(ctx: Context) -> QDialog:
    doc = sample_document()
    dialog = CalculateDialog(doc, "t", manage_constants=lambda: None)
    dialog.set_kind(CalculationKind.FOLD_CHANGE)
    dialog.set_paired(True)
    return dialog


def _calculate_custom(ctx: Context) -> QDialog:
    doc = sample_document()
    dialog = CalculateDialog(doc, "t", selected=["Drug1"], manage_constants=lambda: None)
    dialog.set_kind(CalculationKind.CUSTOM)
    dialog.set_formula("{A} / MAX([Control 1]) * ")
    return dialog


def grouped_document() -> Document:
    """The sample document with its replicates grouped as "Control" and "Drug"."""
    from curvis.core import GroupColumns

    doc = sample_document()
    doc.execute(GroupColumns("t", ["Control1", "Control2", "Control3"], "Control"))
    doc.execute(GroupColumns("t", ["Drug1", "Drug2", "Drug3"], "Drug"))
    doc.undo_stack.clear()
    return doc


def _analyze(ctx: Context) -> QDialog:
    dialog = AnalyzeDialog(grouped_document(), "t", recent=["descriptive_statistics"])
    dialog.set_analysis_type("descriptive_statistics")
    return dialog


def _analyze_advanced(ctx: Context) -> QDialog:
    dialog = AnalyzeDialog(grouped_document(), "t")
    dialog.set_analysis_type("descriptive_statistics")
    form = dialog.forms["descriptive_statistics"]
    assert form.advanced is not None
    form.advanced.set_expanded(True)
    return dialog


def _analyze_nothing_ticked(ctx: Context) -> QDialog:
    dialog = AnalyzeDialog(grouped_document(), "t")
    dialog.set_analysis_type("descriptive_statistics")
    dialog.deselect_all_button.click()
    return dialog


def _analyze_t_test(ctx: Context) -> QDialog:
    dialog = AnalyzeDialog(grouped_document(), "t")
    dialog.set_analysis_type("t_test")
    return dialog


def _analyze_anova_dunnett(ctx: Context) -> QDialog:
    dialog = AnalyzeDialog(grouped_document(), "t")
    dialog.set_analysis_type("one_way_anova")
    combo = dialog.forms["one_way_anova"].fields["posthoc"]
    assert isinstance(combo, QComboBox)
    combo.setCurrentIndex(combo.findData("dunnett"))
    return dialog


def _analyze_fit(ctx: Context) -> QDialog:
    dialog = AnalyzeDialog(grouped_document(), "t")
    dialog.set_analysis_type("hill_4pl")
    return dialog


def _analyze_fit_advanced(ctx: Context) -> QDialog:
    dialog = AnalyzeDialog(grouped_document(), "t")
    dialog.set_analysis_type("hill_4pl")
    form = dialog.forms["hill_4pl"]
    assert form.advanced is not None
    form.advanced.set_expanded(True)
    return dialog


def _import(ctx: Context) -> QDialog:
    dialog = ImportDialog(CSV, ctx.scheduler)
    dialog.header_row.setValue(3)
    return dialog


def _bulk_rename(ctx: Context) -> QDialog:
    dialog = BulkRenameDialog(sample_document().table("t"), ["Control1", "Control2", "Control3"])
    dialog.find_edit.setText("Control")
    dialog.replace_edit.setText("Vehicle")
    return dialog


def _bulk_rename_numbered(ctx: Context) -> QDialog:
    dialog = BulkRenameDialog(sample_document().table("t"), ["Drug1", "Drug2", "Drug3"])
    dialog.set_numbered(True)
    dialog.pattern_edit.setText("Treated #")
    return dialog


def _constants(ctx: Context) -> QDialog:
    return ConstantsDialog(sample_document(), ctx.settings)


def _constant_editor(ctx: Context) -> QDialog:
    return ConstantEditor("Edit constant", ProjectConstant("k_on", 1.2e6, "1/(M·s)"))


def _settings(ctx: Context) -> QDialog:
    return SettingsDialog(ctx.settings)


def _close_table(_ctx: Context) -> QDialog:
    from curvis.analysis.base import Analysis
    from curvis.analysis.compare.ttest_analysis import TTestParams
    from curvis.charts.builder import default_spec
    from curvis.charts.spec import ChartFamily

    doc = grouped_document()
    table = doc.tables[0]
    group_ids = tuple(g.id for g in table.replicate_groups[:2])
    analysis = Analysis("t_test", table.id, group_ids, TTestParams())
    graph = default_spec(table, ChartFamily.BAR)
    return CloseTableDialog(table.name, [analysis], [graph])


def _close_table_plain(_ctx: Context) -> QDialog:
    return CloseTableDialog("Viability")


def graph_document(family_name: str = "xy") -> tuple[Document, str]:
    """The grouped sample document with one graph; returns it and the graph's id."""
    from curvis.charts.builder import default_spec
    from curvis.charts.spec import ChartFamily
    from curvis.core import add_graph

    doc = grouped_document()
    spec = default_spec(doc.table("t"), ChartFamily(family_name))
    doc.execute(add_graph(doc, spec))
    doc.undo_stack.clear()
    return doc, spec.id


def _format(section: int) -> Callable[[Context], QDialog]:
    def make(ctx: Context) -> QDialog:
        from curvis.ui.format_dialog import FormatDialog

        doc, graph_id = graph_document()
        dialog = FormatDialog(doc, graph_id, scheduler=ctx.scheduler)
        dialog.set_section(section)
        return dialog

    return make


def _format_bar_series(ctx: Context) -> QDialog:
    from curvis.ui.format_dialog import SERIES, FormatDialog

    doc, graph_id = graph_document("bar")
    dialog = FormatDialog(doc, graph_id, scheduler=ctx.scheduler)
    dialog.set_section(SERIES)
    dialog.select_series([s.source for s in dialog.spec.series])
    return dialog


def _format_bad_range(ctx: Context) -> QDialog:
    from curvis.ui.format_dialog import AXES, FormatDialog

    doc, graph_id = graph_document()
    dialog = FormatDialog(doc, graph_id, scheduler=ctx.scheduler)
    dialog.set_section(AXES)
    dialog.axis_from.setText("10")
    dialog.axis_to.setText("1")
    dialog.axis_from.textEdited.emit("10")
    return dialog


def _export(_ctx: Context) -> QDialog:
    from curvis.ui.export_dialog import ExportDialog

    doc, graph_id = graph_document()
    return ExportDialog(doc.graph(graph_id))


SAMPLES: dict[str, Callable[[Context], QDialog]] = {
    "import": _import,
    "bulk_rename": _bulk_rename,
    "bulk_rename_numbered": _bulk_rename_numbered,
    "calculate": _calculate,
    "calculate_paired": _calculate_paired,
    "calculate_custom": _calculate_custom,
    "analyze": _analyze,
    "analyze_advanced": _analyze_advanced,
    "analyze_nothing_ticked": _analyze_nothing_ticked,
    "analyze_t_test": _analyze_t_test,
    "analyze_anova_dunnett": _analyze_anova_dunnett,
    "analyze_fit": _analyze_fit,
    "analyze_fit_advanced": _analyze_fit_advanced,
    "constants": _constants,
    "constant_editor": _constant_editor,
    "settings": _settings,
    "close_table": _close_table,
    "close_table_plain": _close_table_plain,
    "format_graph": _format(0),
    "format_axes": _format(1),
    "format_series": _format(2),
    "format_colors": _format(3),
    "format_bar_series": _format_bar_series,
    "format_bad_range": _format_bad_range,
    "export_graph": _export,
}


def settle(dialog: QDialog, ctx: Context, app: QApplication) -> None:
    """Wait until background reads are done and the layout is final."""
    app.processEvents()
    ctx.scheduler.wait_idle()
    for _ in range(3):
        app.processEvents()
