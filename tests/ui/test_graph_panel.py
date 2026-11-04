"""The Graph panel: plotting a table, redrawing after edits and fits, legend clicks and
hover tooltips."""

from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtCore import QPointF, Qt
from pytestqt.qtbot import QtBot

from curvis.analysis.base import Analysis
from curvis.analysis.fit.analyses import Hill4Params
from curvis.charts.render_qt.chart_view import BoxItem, ChartView
from curvis.charts.spec import ChartFamily, ChartVariant, FitOverlay, Scale
from curvis.core import AddTable, SetCellValue, add_analysis, edit_graph
from curvis.ui.document_tab import GRAPH_TAB, RESULTS_TAB, DocumentTab
from curvis.ui.graph_panel import EMPTY_TEXT
from curvis.ui.main_window import MainWindow
from tests.unit.charts_tables import column_table, replicate_table, sigmoid_table


def settle(qtbot: QtBot, tab: DocumentTab) -> None:
    engine = tab.graph_engine
    assert engine is not None
    qtbot.waitUntil(
        lambda: (
            not any(engine.is_stale(g.id) for g in tab.document.graphs)
            and all(g.id in engine.data for g in tab.document.graphs)
        ),
        timeout=10_000,
    )


@pytest.fixture
def tab(window: MainWindow) -> DocumentTab:
    tab = window.new_document()
    tab.document.execute(AddTable(replicate_table(), 0))
    return tab


def chart_of(tab: DocumentTab) -> ChartView:
    chart = tab.graph_panel.chart
    assert chart is not None
    return chart


def test_empty_state_until_a_graph_is_made(tab: DocumentTab) -> None:
    tab.show_graph()
    assert tab.graph_panel.stack.currentWidget() is tab.graph_panel.empty
    assert tab.graph_panel.empty.text() == EMPTY_TEXT


def test_plot_xy_from_the_menu(window: MainWindow, tab: DocumentTab, qtbot: QtBot) -> None:
    window.registry.action("graph.new.xy").trigger()
    settle(qtbot, tab)
    assert tab.side_tabs.currentIndex() == GRAPH_TAB
    (graph,) = tab.document.graphs
    assert tab.current_graph_id == graph.id
    chart = chart_of(tab)
    assert [s.name for s, _item in chart._drawn.xy] == ["Control", "Drug"]
    assert [source for source, _ in chart.legend_entries()] == [s.source for s in graph.series]
    assert window.registry.action("graph.delete").isEnabled()
    assert tab.graph_panel.combo.currentText() == "XY graph · Assay"


def test_graph_redraws_after_a_data_edit(
    window: MainWindow, tab: DocumentTab, qtbot: QtBot
) -> None:
    tab.plot(ChartFamily.XY)
    settle(qtbot, tab)
    table = tab.document.tables[0]
    first = table.group_columns(table.replicate_groups[0].id)[0]
    tab.document.execute(SetCellValue(table.id, first.id, 0, 40.0))
    assert tab.graph_panel.badge.isVisibleTo(tab.graph_panel)
    settle(qtbot, tab)
    series, item = chart_of(tab)._drawn.xy[0]
    assert series.y[0] == pytest.approx((40 + 11 + 12) / 3)
    _x, y = item.getData()
    assert y[0] == pytest.approx(21.0)
    assert not tab.graph_panel.badge.isVisibleTo(tab.graph_panel)


def click_legend(qtbot: QtBot, chart: ChartView, index: int) -> None:
    _source, sample = chart.legend_entries()[index]
    view = chart.plot_widget
    pos = view.mapFromScene(sample.sceneBoundingRect().center())
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=pos)


def test_legend_click_hides_a_series_as_an_undo_step(
    window: MainWindow, tab: DocumentTab, qtbot: QtBot
) -> None:
    tab.plot(ChartFamily.XY)
    settle(qtbot, tab)
    chart = chart_of(tab)
    control = tab.document.tables[0].replicate_groups[0]
    click_legend(qtbot, chart, 0)
    graph = tab.document.graphs[0]
    assert graph.series_for(control.id).visible is False  # type: ignore[union-attr]
    assert tab.document.undo_stack.undo_text == "Hide series 'Control'"
    settle(qtbot, tab)
    assert not chart._drawn.xy[0][1].isVisible()
    assert chart._drawn.xy[1][1].isVisible()

    tab.document.undo()
    settle(qtbot, tab)
    assert chart._drawn.xy[0][1].isVisible()
    assert tab.document.graphs[0].series_for(control.id).visible is True  # type: ignore[union-attr]


def point_scene_pos(chart: ChartView, series: int, index: int) -> QPointF:
    data, _item = chart._drawn.xy[series]
    vb = chart.plot_item.vb
    return vb.mapViewToScene(QPointF(float(data.px[index]), float(data.py[index])))


def test_hovering_a_point_shows_its_table_values(tab: DocumentTab, qtbot: QtBot) -> None:
    tab.plot(ChartFamily.XY)
    settle(qtbot, tab)
    chart = chart_of(tab)
    chart.resize(640, 480)
    qtbot.wait(10)
    text = chart.tooltip_at(point_scene_pos(chart, 1, 2))
    assert text is not None
    assert text.splitlines() == [
        "Drug · row 3",
        "Dose (µM): 0.1",
        "Mean: 55.00",
        "SD: 1.000",
        "n: 3",
        "Values: 54, 55, 56",
    ]
    far = point_scene_pos(chart, 1, 2) + QPointF(40, 40)
    assert chart.tooltip_at(far) is None


def test_tooltip_on_a_log_axis_shows_the_typed_x(tab: DocumentTab, qtbot: QtBot) -> None:
    tab.plot(ChartFamily.XY)
    settle(qtbot, tab)
    graph = tab.document.graphs[0]
    log = replace(graph, x_axis=replace(graph.x_axis, scale=Scale.LOG))
    tab.document.execute(edit_graph(tab.document, log))
    settle(qtbot, tab)
    chart = chart_of(tab)
    data, _ = chart._drawn.xy[0]
    np.testing.assert_allclose(data.px, np.log10(data.x))
    text = chart.tooltip_at(point_scene_pos(chart, 0, 0))
    assert text is not None and "Dose (µM): 0.001" in text.splitlines()


def test_bar_and_box(window: MainWindow, qtbot: QtBot) -> None:
    tab = window.new_document()
    tab.document.execute(AddTable(column_table(), 0))
    assert not window.registry.action("graph.new.xy").isEnabled()
    window.registry.action("graph.new.bar").trigger()
    settle(qtbot, tab)
    chart = chart_of(tab)
    assert [c.name for c in chart._drawn.categories] == ["Control", "Treated"]
    vb = chart.plot_item.vb
    inside = vb.mapViewToScene(QPointF(0.0, 1.0))
    assert chart.tooltip_at(inside) == "Control\nMean: 3.750\nSD: 3.096\nn: 4"
    assert chart.legend_entries() == []  # bars are named on the axis

    box = replace(tab.document.graphs[0], variant=ChartVariant.BOX)
    tab.document.execute(edit_graph(tab.document, box))
    settle(qtbot, tab)
    assert any(isinstance(item, BoxItem) for item in chart.plot_item.items)
    text = chart.tooltip_at(vb.mapViewToScene(QPointF(1.0, 6.0)))
    assert text is not None and text.startswith("Treated\nMaximum: 7")


def test_a_new_fit_is_overlaid_on_the_xy_graphs(window: MainWindow, qtbot: QtBot) -> None:
    tab = window.new_document()
    tab.document.execute(AddTable(sigmoid_table(), 0))
    tab.plot(ChartFamily.XY)
    tab.plot(ChartFamily.BAR)
    settle(qtbot, tab)
    table = tab.document.tables[0]
    fit = Analysis("hill_4pl", table.id, (table.replicate_groups[0].id,), Hill4Params())
    command = tab._with_overlays(add_analysis(tab.document, fit), fit)
    tab.document.execute(command)
    xy, bar = tab.document.graphs
    assert xy.overlays == (FitOverlay(fit.id),) and xy.x_axis.scale is Scale.LOG
    assert bar.overlays == ()
    tab.graph_panel.set_current(xy.id)
    qtbot.waitUntil(lambda: bool(chart_of(tab).data and chart_of(tab).data.curves), timeout=10_000)
    tab.document.undo()  # one step: the fit and its overlay
    assert tab.document.analyses == [] and tab.document.graphs[0].overlays == ()


def test_delete_graph_and_switch_panels(window: MainWindow, tab: DocumentTab, qtbot: QtBot) -> None:
    tab.plot(ChartFamily.XY)
    settle(qtbot, tab)
    window.registry.action("view.results").trigger()
    assert tab.side_tabs.currentIndex() == RESULTS_TAB
    window.registry.action("view.graph").trigger()
    assert tab.side_tabs.currentIndex() == GRAPH_TAB
    window.registry.action("graph.delete").trigger()
    assert tab.document.graphs == []
    assert tab.graph_panel.stack.currentWidget() is tab.graph_panel.empty
    assert not window.registry.action("graph.delete").isEnabled()
    tab.document.undo()
    settle(qtbot, tab)
    assert tab.graph_panel.stack.currentWidget() is chart_of(tab)
