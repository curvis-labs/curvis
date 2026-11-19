"""Settings in the app (step 3.5): new graphs copy the graph settings into their chart
spec, existing graphs never change when a setting changes, P values follow the
setting, and the Export dialog starts from the export defaults."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QLabel
from pytestqt.qtbot import QtBot

from curvis.analysis.result import PDisplay
from curvis.charts.colors import COLOR_BLIND_SAFE, CURVIS, ColorScheme
from curvis.charts.spec import ChartSpec
from curvis.core import AddTable
from curvis.io import load, save
from curvis.settings import Settings
from curvis.ui.document_tab import DocumentTab
from curvis.ui.export_dialog import ExportDialog
from curvis.ui.main_window import MainWindow
from tests.ui.test_graph_panel import settle
from tests.unit.charts_tables import column_table, replicate_table

MINE = ColorScheme("user:lab", "Lab", "#ffffff", "#1a1a1a", ("#aa0000", "#00aa00", "#0000aa"))


def _graph_settings(settings: Settings) -> None:
    settings.graph_palette = MINE
    settings.set_graph_size("custom", (12.0, 7.5))
    settings.graph_font = "liberation_serif"
    settings.graph_text_size = 8.0


def _new_graph(window: MainWindow, tab: DocumentTab, family: str, qtbot: QtBot) -> ChartSpec:
    window.registry.action(f"graph.new.{family}").trigger()
    settle(qtbot, tab)
    return tab.document.graphs[-1]


def test_a_new_graph_copies_the_graph_settings(
    window: MainWindow, settings: Settings, qtbot: QtBot
) -> None:
    _graph_settings(settings)
    tab = window.new_document()
    tab.document.execute(AddTable(replicate_table(), 0))
    for family in ("xy", "bar"):
        graph = _new_graph(window, tab, family, qtbot)
        assert graph.palette == MINE
        assert (graph.aspect_ratio, graph.custom_size) == ("custom", (12.0, 7.5))
        assert graph.page_size_cm == (12.0, 7.5)
        assert graph.font == "liberation_serif" and graph.text_size == 8.0
        assert [s.color for s in graph.series] == list(MINE.series[: len(graph.series)])


def test_existing_graphs_never_change_when_a_setting_changes(
    window: MainWindow, settings: Settings, qtbot: QtBot, tmp_path: Path
) -> None:
    tab = window.new_document()
    tab.document.execute(AddTable(column_table(), 0))
    before = _new_graph(window, tab, "bar", qtbot)
    path = tmp_path / "graphs.cvp"
    save(tab.document, path)

    _graph_settings(settings)
    settings.reset_to_defaults()
    _graph_settings(settings)
    settings.graph_palette = COLOR_BLIND_SAFE

    assert tab.document.graphs == [before]
    assert load(path).graphs == [before]
    assert before.palette == CURVIS and before.font == "dejavu_sans"
    # A graph made now takes the new settings; the old one is untouched.
    after = _new_graph(window, tab, "bar", qtbot)
    assert after.palette == COLOR_BLIND_SAFE and tab.document.graphs[0] == before


def test_p_values_follow_the_setting_without_a_new_run(
    window: MainWindow, settings: Settings, qtbot: QtBot
) -> None:
    from curvis.analysis.base import Analysis
    from curvis.analysis.compare.ttest_analysis import TTestParams
    from curvis.core import add_analysis
    from curvis.data import Column, ColumnRole, Table

    table = Table(
        "T",
        [
            Column.new("A", ColumnRole.Y, [1.0, 1.1, 0.9, 1.05, 0.95, 1.0]),
            Column.new("B", ColumnRole.Y, [9.0, 9.2, 8.9, 9.1, 9.05, 8.95]),
        ],
    )
    tab = window.new_document()
    tab.document.execute(AddTable(table, 0))
    analysis = Analysis("t_test", table.id, tuple(c.id for c in table.columns), TTestParams())
    tab.document.execute(add_analysis(tab.document, analysis))
    engine = tab.analysis_engine
    assert engine is not None
    qtbot.waitUntil(lambda: analysis.id in engine.results, timeout=10_000)
    tab.results_panel.set_current(analysis.id)
    runs = engine.results[analysis.id]

    def reading() -> str:
        content = tab.results_panel.content
        assert content is not None
        label = content.findChild(QLabel, "reading")
        assert label is not None
        return label.text()

    assert "P < 0.001" in reading()
    settings.p_display = PDisplay.EXACT
    assert "P = " in reading() and "× 10" in reading()
    p_cells = [
        g for g in tab.results_panel.grids if any("× 10" in c for r in g.grid.shown for c in r)
    ]
    assert p_cells, "the P value in the table is shown exactly too"
    settings.p_display = PDisplay.BELOW_00001
    assert "P < 0.0001" in reading()
    # The same result object: nothing ran again, and the stars did not change.
    assert engine.results[analysis.id] is runs


def test_export_starts_from_the_export_defaults(
    window: MainWindow, settings: Settings, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.export_format = "pdf"
    settings.png_dpi = 600
    tab = window.new_document()
    tab.document.execute(AddTable(column_table(), 0))
    _new_graph(window, tab, "bar", qtbot)
    seen: list[tuple[str, int]] = []

    def look(self: DocumentTab, dialog: QDialog) -> bool:
        assert isinstance(dialog, ExportDialog)
        seen.append((dialog.export_format.value, dialog.dpi.value()))
        return False

    monkeypatch.setattr(DocumentTab, "_exec_dialog", look)
    tab.export_graph()
    assert seen == [("pdf", 600)]


def test_format_dialog_offers_the_users_palettes(
    window: MainWindow, settings: Settings, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.palettes = [MINE]
    tab = window.new_document()
    tab.document.execute(AddTable(column_table(), 0))
    graph = _new_graph(window, tab, "bar", qtbot)
    names: list[str] = []

    def look(self: DocumentTab, dialog: QDialog) -> bool:
        combo = dialog.scheme_combo  # type: ignore[attr-defined]
        names.extend(combo.itemText(i) for i in range(combo.count()))
        index = next(i for i in range(combo.count()) if combo.itemText(i) == "Lab")
        combo.setCurrentIndex(index)
        combo.activated.emit(index)
        return True

    monkeypatch.setattr(DocumentTab, "_exec_dialog", look)
    tab.format_graph(graph.id)
    assert "Lab" in names and "Color-blind safe" in names
    styled = tab.document.graph(graph.id)
    assert styled.palette == MINE
    assert [s.color for s in styled.series] == list(MINE.series[: len(styled.series)])
    # One undo step puts the old colours back.
    tab.document.undo_stack.undo()
    assert tab.document.graph(graph.id) == replace(graph)
