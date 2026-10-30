"""Every dialog of the app, filled with typical data: for the design tests and screenshots.

Each sample builds one dialog. A dialog that reads in the background (Import) is shown
only after its first result, so the tests and screenshots see what the user sees.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import QApplication, QDialog

from curvis.compute import Scheduler
from curvis.core import AddConstant, AddTable, Calculation, CalculationKind, Document
from curvis.core import calculation_formulas as cf
from curvis.core.calculation_commands import add_calculation
from curvis.core.constants import ProjectConstant
from curvis.data import Column, ColumnRole, Pairing, Table
from curvis.settings import Settings
from curvis.ui.calculate_dialog import CalculateDialog
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


SAMPLES: dict[str, Callable[[Context], QDialog]] = {
    "import": _import,
    "bulk_rename": _bulk_rename,
    "bulk_rename_numbered": _bulk_rename_numbered,
    "calculate": _calculate,
    "calculate_paired": _calculate_paired,
    "calculate_custom": _calculate_custom,
    "constants": _constants,
    "constant_editor": _constant_editor,
    "settings": _settings,
}


def settle(dialog: QDialog, ctx: Context, app: QApplication) -> None:
    """Wait until background reads are done and the layout is final."""
    app.processEvents()
    ctx.scheduler.wait_idle()
    for _ in range(3):
        app.processEvents()
