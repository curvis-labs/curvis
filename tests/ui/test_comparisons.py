"""Comparison brackets in the app: *Show comparisons* in the Graph panel toolbar and the
Graph menu, the Format dialog's options, redrawing after a new run and a change of the
P value setting, and deleting the analysis."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMenu
from pytestqt.qtbot import QtBot

from curvis.analysis.base import Analysis
from curvis.analysis.compare.anova_analysis import AnovaParams
from curvis.analysis.compare.ttest_analysis import TTestParams
from curvis.analysis.result import PDisplay
from curvis.charts.spec import ChartFamily, ChartVariant, Comparisons, PLabel
from curvis.core import AddTable, SetCellValue, add_analysis, remove_analysis
from curvis.ui.document_tab import DocumentTab
from curvis.ui.format_dialog import COMPARISONS_KEY, SERIES, FormatDialog
from curvis.ui.main_window import HIDE_COMPARISONS, NO_COMPARISONS, MainWindow
from curvis.ui.toolbars import GRAPH_BUTTONS
from tests.ui.test_graph_panel import settle
from tests.unit.test_brackets import groups_table


@pytest.fixture
def tab(window: MainWindow) -> DocumentTab:
    tab = window.new_document()
    tab.document.execute(AddTable(groups_table(), 0))
    return tab


def wait_results(qtbot: QtBot, tab: DocumentTab) -> None:
    engine = tab.analysis_engine
    assert engine is not None
    qtbot.waitUntil(
        lambda: all(a.id in engine.results for a in tab.document.analyses), timeout=10_000
    )


def with_anova(qtbot: QtBot, window: MainWindow, tab: DocumentTab) -> str:
    """A bar graph of the table and a one-way ANOVA of its four groups; the graph id."""
    ids = ("c", "a", "b", "d")
    tab.document.execute(
        add_analysis(tab.document, Analysis("one_way_anova", "t", ids, AnovaParams(), id="anova"))
    )
    window.registry.action("graph.new.bar").trigger()
    wait_results(qtbot, tab)
    settle(qtbot, tab)
    graph_id = tab.current_graph_id
    assert graph_id is not None
    return graph_id


def menu_texts(window: MainWindow) -> list[str]:
    menu = window.comparisons_menu
    menu.aboutToShow.emit()
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def test_the_button_is_on_the_graph_toolbar_and_in_the_graph_menu(
    window: MainWindow, tab: DocumentTab
) -> None:
    assert "graph.comparisons" in [b.action_id for b in GRAPH_BUTTONS]
    menus = [m for m in window.findChildren(QMenu) if m.title() == "&Graph"]
    assert any(window.registry.action("graph.comparisons") in m.actions() for m in menus)


def test_show_comparisons_from_the_menu(qtbot: QtBot, window: MainWindow, tab: DocumentTab) -> None:
    graph_id = with_anova(qtbot, window, tab)
    window._update_state()
    assert window.registry.action("graph.comparisons").isEnabled()
    assert menu_texts(window) == ["One-way ANOVA"]
    window.comparisons_menu.actions()[0].trigger()
    spec = tab.document.graph(graph_id)
    assert spec.comparisons == Comparisons("anova")
    assert tab.document.change_log.entries[-1].summary == "Show comparisons"
    settle(qtbot, tab)
    chart = tab.graph_panel.chart
    assert chart is not None and chart.layout_ is not None and chart.layout_.brackets
    assert tab.graph_engine is not None and tab.graph_engine.data[graph_id].pairs
    # the menu now checks it and offers to hide it
    assert menu_texts(window) == ["One-way ANOVA", HIDE_COMPARISONS]
    assert window.comparisons_menu.actions()[0].isChecked()
    window.comparisons_menu.actions()[-1].trigger()
    assert tab.document.graph(graph_id).comparisons is None
    tab.document.undo()
    assert tab.document.graph(graph_id).comparisons == Comparisons("anova")


def test_without_a_test_the_menu_says_what_to_run(
    qtbot: QtBot, window: MainWindow, tab: DocumentTab
) -> None:
    window.registry.action("graph.new.bar").trigger()
    settle(qtbot, tab)
    assert menu_texts(window) == [NO_COMPARISONS]
    assert not window.comparisons_menu.actions()[0].isEnabled()


def test_only_upright_ungrouped_bar_family_graphs(
    qtbot: QtBot, window: MainWindow, tab: DocumentTab
) -> None:
    with_anova(qtbot, window, tab)
    tab.change_graph_type(ChartVariant.HORIZONTAL_BAR)
    window._update_state()
    action = window.registry.action("graph.comparisons")
    assert not action.isEnabled()
    assert "upright" in action.toolTip()
    tab.change_graph_type(ChartVariant.BOX)
    window._update_state()
    assert action.isEnabled()


def test_brackets_follow_a_new_run(qtbot: QtBot, window: MainWindow, tab: DocumentTab) -> None:
    graph_id = with_anova(qtbot, window, tab)
    tab.set_comparisons("anova")
    settle(qtbot, tab)
    engine = tab.graph_engine
    assert engine is not None
    before = engine.data[graph_id].pairs
    # Drug C becomes like Drug B: more pairs differ
    for row, value in enumerate([9.1, 8.5, 9.4, 8.9, 9.7]):
        tab.document.execute(SetCellValue("t", "d", row, value))
    wait_results(qtbot, tab)
    qtbot.waitUntil(lambda: engine.data[graph_id].pairs != before, timeout=10_000)


def test_p_values_follow_the_setting(qtbot: QtBot, window: MainWindow, tab: DocumentTab) -> None:
    graph_id = with_anova(qtbot, window, tab)
    tab.set_comparisons("anova")
    spec = tab.document.graph(graph_id)
    comparisons = spec.comparisons
    assert comparisons is not None
    from dataclasses import replace

    from curvis.core import edit_graph

    tab.document.execute(
        edit_graph(
            tab.document, replace(spec, comparisons=replace(comparisons, label=PLabel.P_VALUE))
        )
    )
    settle(qtbot, tab)
    engine = tab.graph_engine
    assert engine is not None
    assert "P < 0.001" in [p.text for p in engine.data[graph_id].pairs]
    tab.set_p_display(PDisplay.EXACT)
    qtbot.waitUntil(
        lambda: "P < 0.001" not in [p.text for p in engine.data[graph_id].pairs], timeout=10_000
    )
    assert any("× 10" in p.text for p in engine.data[graph_id].pairs)


def test_deleting_the_analysis_takes_the_brackets_off(
    qtbot: QtBot, window: MainWindow, tab: DocumentTab
) -> None:
    graph_id = with_anova(qtbot, window, tab)
    tab.set_comparisons("anova")
    tab.document.execute(remove_analysis(tab.document, "anova"))
    assert tab.document.graph(graph_id).comparisons is None


def test_a_second_test_replaces_the_first(
    qtbot: QtBot, window: MainWindow, tab: DocumentTab
) -> None:
    graph_id = with_anova(qtbot, window, tab)
    ttest = Analysis("t_test", "t", ("c", "b"), TTestParams(), id="ttest")
    tab.document.execute(add_analysis(tab.document, ttest))
    tab.set_comparisons("anova")
    names = [name for _id, name in tab.comparison_choices()]
    assert menu_texts(window) == [*names, HIDE_COMPARISONS]
    assert names[0] == "One-way ANOVA" and len(names) == 2
    tab.set_comparisons("ttest")
    assert tab.document.graph(graph_id).comparisons == Comparisons("ttest")


# --- the Format dialog ------------------------------------------------------------------------


def open_format(qtbot: QtBot, tab: DocumentTab, graph_id: str) -> FormatDialog:
    engine = tab.analysis_engine
    assert engine is not None
    dialog = FormatDialog(tab.document, graph_id, scheduler=tab.scheduler, results=engine.results)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.set_section(SERIES)
    qtbot.waitUntil(dialog.preview_ready, timeout=5000)
    return dialog


def test_add_comparisons_in_the_format_dialog(
    qtbot: QtBot, window: MainWindow, tab: DocumentTab
) -> None:
    graph_id = with_anova(qtbot, window, tab)
    dialog = open_format(qtbot, tab, graph_id)
    dialog.add_menu.aboutToShow.emit()
    texts = [a.text() for a in dialog.add_menu.actions() if not a.isSeparator()]
    assert texts == ["Comparisons: One-way ANOVA"]
    dialog.add_menu.actions()[-1].trigger()
    assert dialog.spec.comparisons == Comparisons("anova")
    assert dialog.comparisons_selected()
    item = dialog.series_list.selectedItems()[0]
    assert item.text() == "Comparisons: One-way ANOVA"
    assert dialog.options.currentIndex() == 2  # the comparison options
    assert dialog.selection_note.text() == "The comparisons selected."
    dialog.comparison_label.setCurrentIndex(dialog.comparison_label.findData(PLabel.P_VALUE))
    dialog.comparison_label.activated.emit(dialog.comparison_label.currentIndex())
    dialog.show_ns.click()
    assert dialog.spec.comparisons == Comparisons("anova", PLabel.P_VALUE, show_ns=True)
    qtbot.waitUntil(dialog.preview_ready, timeout=5000)
    assert dialog.preview.layout_ is not None and dialog.preview.layout_.brackets
    assert dialog.remove_button.isEnabled()
    dialog.remove_button.click()
    assert dialog.spec.comparisons is None
    assert COMPARISONS_KEY not in [
        dialog.series_list.item(i).data(256) for i in range(dialog.series_list.count())
    ]


def test_format_dialog_offers_no_comparisons_on_a_histogram(
    qtbot: QtBot, window: MainWindow, tab: DocumentTab
) -> None:
    graph_id = with_anova(qtbot, window, tab)
    tab.change_graph_type(ChartVariant.HISTOGRAM)
    dialog = open_format(qtbot, tab, graph_id)
    dialog.add_menu.aboutToShow.emit()
    assert not any(a.text().startswith("Comparisons") for a in dialog.add_menu.actions())


def test_saved_and_opened_again(
    qtbot: QtBot, window: MainWindow, tab: DocumentTab, tmp_path: Path
) -> None:
    from curvis.io.project import load, save

    graph_id = with_anova(qtbot, window, tab)
    tab.set_comparisons("anova")
    save(tab.document, tmp_path / "c.cvp")
    again = load(tmp_path / "c.cvp")
    assert again.graph(graph_id).comparisons == Comparisons("anova")
    assert again.graph(graph_id).family is ChartFamily.BAR
    QApplication.processEvents()
