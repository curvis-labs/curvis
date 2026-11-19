"""Survival graphs in the Graph panel: plotting a Time + Event table, legend clicks,
redrawing after an edit, and export (step 3.4)."""

from pathlib import Path

import pyqtgraph as pg
import pytest
from pytestqt.qtbot import QtBot

from curvis.charts.render_mpl.export import ExportFormat, export_graph
from curvis.core import AddTable, SetCellValue
from curvis.ui.document_tab import DocumentTab
from curvis.ui.main_window import MainWindow
from tests.ui.test_graph_panel import chart_of, click_legend, settle
from tests.unit.charts_tables import column_table, survival_table


@pytest.fixture
def tab(window: MainWindow) -> DocumentTab:
    tab = window.new_document()
    tab.document.execute(AddTable(survival_table(), 0))
    return tab


def curves(tab: DocumentTab) -> list[pg.PlotDataItem]:
    return [i for i in chart_of(tab).plot_item.items if isinstance(i, pg.PlotDataItem)]


def test_plot_survival_from_the_menu(window: MainWindow, tab: DocumentTab, qtbot: QtBot) -> None:
    action = window.registry.action("graph.new.survival")
    assert action.isEnabled()
    action.trigger()
    settle(qtbot, tab)
    (graph,) = tab.document.graphs
    chart = chart_of(tab)
    assert [source for source, _ in chart.legend_entries()] == [s.source for s in graph.series]
    assert chart.legend_names() == ["Drug", "Placebo"]
    assert chart.message == ""
    bands = [i for i in chart.plot_item.items if isinstance(i, pg.FillBetweenItem)]
    ticks = [i for i in chart.plot_item.items if isinstance(i, pg.ScatterPlotItem)]
    assert len(curves(tab)) == 2 and len(bands) == 2 and len(ticks) == 2
    data = tab.graph_engine.data[graph.id]  # type: ignore[union-attr]
    assert data.survival[0].censor_px.tolist() == [3, 5, 8, 9, 12, 15, 20, 22, 25]
    assert tab.graph_panel.combo.currentText() == "Survival graph · Survival"


def test_survival_needs_a_time_event_pair(window: MainWindow, qtbot: QtBot) -> None:
    tab = window.new_document()
    tab.document.execute(AddTable(column_table(), 0))
    action = window.registry.action("graph.new.survival")
    assert not action.isEnabled()
    assert "Needs a Time + Event pair" in action.toolTip()


def test_legend_click_hides_a_curve_with_its_band(
    window: MainWindow, tab: DocumentTab, qtbot: QtBot
) -> None:
    window.registry.action("graph.new.survival").trigger()
    settle(qtbot, tab)
    click_legend(qtbot, chart_of(tab), 0)
    assert tab.document.undo_stack.undo_text == "Hide series 'Drug'"
    settle(qtbot, tab)
    chart = chart_of(tab)
    hidden = [i for i in chart.plot_item.items if not i.isVisible()]
    assert len(hidden) == 3  # the step line, its band and its censor ticks
    assert [c.isVisible() for c in curves(tab)] == [False, True]
    assert chart.legend_names() == ["Drug", "Placebo"]  # dimmed, so it can come back


def test_editing_an_event_redraws_the_curve(
    window: MainWindow, tab: DocumentTab, qtbot: QtBot
) -> None:
    window.registry.action("graph.new.survival").trigger()
    settle(qtbot, tab)
    table = tab.document.tables[0]
    graph = tab.document.graphs[0]
    tab.document.execute(SetCellValue(table.id, table.columns[1].id, 1, 1))  # day 3 censored→event
    settle(qtbot, tab)
    data = tab.graph_engine.data[graph.id]  # type: ignore[union-attr]
    assert 3 not in data.survival[0].censor_px.tolist()


@pytest.mark.parametrize("fmt", list(ExportFormat))
def test_survival_graph_exports(
    window: MainWindow, tab: DocumentTab, qtbot: QtBot, tmp_path: Path, fmt: ExportFormat
) -> None:
    window.registry.action("graph.new.survival").trigger()
    settle(qtbot, tab)
    graph = tab.document.graphs[0]
    data = tab.graph_engine.data[graph.id]  # type: ignore[union-attr]
    path = tmp_path / f"survival.{fmt.value}"
    export_graph(graph, data, str(path), fmt.value, 300)
    assert path.stat().st_size > 1000
