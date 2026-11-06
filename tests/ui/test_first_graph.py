"""The beta's main path, as docs/getting-started.md describes it: import a CSV, group
the replicates, fit a Hill 4PL, plot it and export a 300 dpi PNG."""

from pathlib import Path

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot

from curvis.charts.spec import ChartFamily, Scale
from curvis.samples import DOSES_UM, INHIBITION
from curvis.ui.analyze_dialog import AnalyzeDialog
from curvis.ui.document_tab import DocumentTab
from curvis.ui.export_dialog import ExportDialog
from curvis.ui.main_window import MainWindow
from curvis.ui.table import TableView
from tests.ui import test_import_dialog
from tests.ui.test_import_dialog import wait_import

importing = test_import_dialog.importing  # scripts the file dialog and the Import dialog


def write_csv(path: Path) -> Path:
    lines = ["Concentration (µM),Rep 1,Rep 2,Rep 3"]
    for i, dose in enumerate(DOSES_UM):
        lines.append(",".join(str(v) for v in [dose, *(rep[i] for rep in INHIBITION)]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_csv_to_an_exported_dose_response_graph(
    qtbot: QtBot,
    window: MainWindow,
    importing: list[Path | None],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Import data (the Import dialog with "First column is X" ticked).
    importing.append(write_csv(tmp_path / "inhibition.csv"))
    window.registry.action("file.import").trigger()
    wait_import(qtbot, window)
    tab = window.current_tab
    assert tab is not None and tab.table_view is not None
    table = tab.current_table
    assert table is not None
    assert [c.role.value for c in table.columns[:4]] == ["X", "Y", "Y", "Y"]

    # 2. Group the three replicates (OK on the suggested name).
    monkeypatch.setattr(TableView, "_ask_text", lambda self, title, label, current: current)
    tab.table_view.select_columns(1, 3)
    window.registry.action("table.group").trigger()
    assert len(table.replicate_groups) == 1

    # 3. Analyze: choose Hill 4PL and click Run; 4. Export: OK on PNG at 300 dpi.
    def run_dialog(self: DocumentTab, dialog: QDialog) -> int:
        if isinstance(dialog, AnalyzeDialog):
            dialog.radios["hill_4pl"].setChecked(True)
        assert isinstance(dialog, AnalyzeDialog | ExportDialog)
        return QDialog.DialogCode.Accepted.value

    out = tmp_path / "inhibition.png"
    monkeypatch.setattr(DocumentTab, "_exec_dialog", run_dialog)
    monkeypatch.setattr(DocumentTab, "_ask_export_path", lambda self, spec, fmt: out)
    window.registry.action("analyze.analyze").trigger()
    engine = tab.analysis_engine
    assert engine is not None
    (fit,) = tab.document.analyses
    qtbot.waitUntil(lambda: fit.id in engine.results, timeout=20_000)
    assert "EC50" in engine.results[fit.id].summary

    window.registry.action("graph.new.xy").trigger()
    (graph,) = tab.document.graphs
    assert graph.family is ChartFamily.XY
    assert graph.x_axis.scale is Scale.LOG  # concentrations: a log axis for the 4PL
    assert [o.analysis_id for o in graph.overlays] == [fit.id]

    window.registry.action("graph.export").trigger()
    qtbot.waitUntil(out.is_file, timeout=30_000)
    image = QImage(str(out))
    assert not image.isNull()
    assert image.dotsPerMeterX() == pytest.approx(300 / 0.0254, rel=0.01)
