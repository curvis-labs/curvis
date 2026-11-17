"""The interaction graph in the window: the Plot menu offers it once the table has a
two-way ANOVA, it draws the ANOVA's cell means, keeps its type, and the Format dialog
lists its lines by category."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF
from pytestqt.qtbot import QtBot

from curvis.charts.builder import INTERACTION_ONLY, NEEDS_TWO_WAY
from curvis.charts.spec import ChartFamily
from curvis.core import AddTable, add_analysis
from curvis.ui.document_tab import DocumentTab
from curvis.ui.format_dialog import FormatDialog
from curvis.ui.main_window import MainWindow
from tests.ui.test_graph_panel import chart_of
from tests.unit.charts_tables import two_way_analysis, two_way_table


def settle_all(qtbot: QtBot, tab: DocumentTab) -> None:
    analyses, graphs = tab.analysis_engine, tab.graph_engine
    assert analyses is not None and graphs is not None
    qtbot.waitUntil(
        lambda: (
            all(a.id in analyses.results for a in tab.document.analyses)
            and not any(graphs.is_stale(g.id) for g in tab.document.graphs)
            and all(g.id in graphs.data for g in tab.document.graphs)
        ),
        timeout=10_000,
    )


@pytest.fixture
def tab(window: MainWindow) -> DocumentTab:
    tab = window.new_document()
    tab.document.execute(AddTable(two_way_table(), 0))
    return tab


def test_interaction_is_offered_once_the_table_has_a_two_way_anova(
    window: MainWindow, tab: DocumentTab, qtbot: QtBot
) -> None:
    action = window.registry.action("graph.new.interaction")
    assert not action.isEnabled()
    assert action.toolTip() == NEEDS_TWO_WAY
    table = tab.document.tables[0]
    tab.document.execute(add_analysis(tab.document, two_way_analysis(table)))
    qtbot.waitUntil(action.isEnabled, timeout=5_000)

    action.trigger()
    settle_all(qtbot, tab)
    (graph,) = tab.document.graphs
    assert graph.family is ChartFamily.INTERACTION
    chart = chart_of(tab)
    assert chart.legend_names() == ["Male", "Female"]
    assert [t.label for t in chart.layout_.x.major] == ["Control", "Low", "High"]
    # Hovering a point names its cell.
    male = chart._drawn.xy[0][0]
    at = chart.plot_item.vb.mapViewToScene(QPointF(float(male.px[2]), float(male.py[2])))
    tip = chart.tooltip_at(at)
    assert tip is not None and tip.splitlines()[0] == "Male · Dose: High"

    # An interaction graph keeps its type.
    for action_id in ("graph.type.scatter", "graph.type.bar"):
        type_action = window.registry.action(action_id)
        assert not type_action.isEnabled()
        assert type_action.toolTip() == INTERACTION_ONLY


def test_format_lists_the_lines_by_category(tab: DocumentTab, qtbot: QtBot) -> None:
    table = tab.document.tables[0]
    tab.document.execute(add_analysis(tab.document, two_way_analysis(table)))
    assert tab.plot(ChartFamily.INTERACTION)
    settle_all(qtbot, tab)
    graph = tab.document.graphs[0]
    dialog = FormatDialog(tab.document, graph.id)
    qtbot.addWidget(dialog)
    items = [dialog.series_list.item(i).text() for i in range(dialog.series_list.count())]
    assert items == ["Male", "Female"]
    # The X axis holds the levels of the first factor: no range or log scale.
    from curvis.ui.format_dialog import AXES, LEVEL_AXIS_NOTE

    dialog.set_section(AXES)
    dialog.show()
    assert not dialog.axis_from.isEnabled() and not dialog.log.isEnabled()
    assert dialog.axis_note.text() == LEVEL_AXIS_NOTE


def test_deleting_the_anova_deletes_the_graph_after_asking(
    tab: DocumentTab, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = tab.document.tables[0]
    analysis = two_way_analysis(table)
    tab.document.execute(add_analysis(tab.document, analysis))
    assert tab.plot(ChartFamily.INTERACTION)
    asked: list[list[str]] = []

    def confirm(dialog: object) -> bool:
        asked.append(list(getattr(dialog, "items", [])))
        return True

    monkeypatch.setattr(tab, "_confirm", confirm)
    assert tab.delete_analysis(analysis.id)
    assert asked == [["1 interaction graph drawn from it"]]
    assert tab.document.graphs == []
    tab.document.undo_stack.undo()
    assert len(tab.document.graphs) == 1
