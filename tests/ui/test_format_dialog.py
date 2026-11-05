"""The Format dialog, the Export dialog and the Graph panel's toolbar."""

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog
from pytestqt.qtbot import QtBot

from curvis.charts.render_mpl.export import ExportFormat
from curvis.charts.spec import (
    ChartFamily,
    ChartVariant,
    ErrorBars,
    LineStyle,
    Scale,
    Symbol,
)
from curvis.compute import Scheduler
from curvis.core import AddTable
from curvis.settings import Settings
from curvis.ui.document_tab import DocumentTab
from curvis.ui.export_dialog import ExportDialog
from curvis.ui.format_dialog import AXES, COLORS, GRAPH, MIXED, SERIES, FormatDialog
from curvis.ui.main_window import MainWindow
from curvis.ui.toolbars import GRAPH_BUTTONS
from tests.ui.dialog_samples import Context, graph_document
from tests.ui.test_graph_panel import settle
from tests.unit.charts_tables import column_table, replicate_table


@pytest.fixture
def context(qapp: QApplication, tmp_path: Path) -> Iterator[Context]:
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    ctx = Context(Settings(store), Scheduler(debounce_ms=0))
    yield ctx
    ctx.scheduler.shutdown()


def open_dialog(qtbot: QtBot, context: Context, family: str = "xy") -> tuple[FormatDialog, object]:
    doc, graph_id = graph_document(family)
    dialog = FormatDialog(doc, graph_id, scheduler=context.scheduler)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(dialog.preview_ready, timeout=5000)
    return dialog, doc


# --- Format dialog ------------------------------------------------------------------------


def test_opens_on_graph_with_the_title_focused(qtbot: QtBot, context: Context) -> None:
    dialog, _doc = open_dialog(qtbot, context)
    assert dialog.windowTitle() == "Format graph"
    assert dialog.tabs.currentIndex() == GRAPH
    assert dialog.focusWidget() is dialog.title_edit
    assert [dialog.tabs.tabText(i) for i in range(4)] == [
        "Graph",
        "Axes",
        "Series",
        "Colors and text",
    ]
    assert dialog.aspect_buttons["wide"].isChecked()
    assert not dialog.width_spin.isEnabled()
    assert not dialog.changed


def test_graph_tab_title_size_and_legend(qtbot: QtBot, context: Context) -> None:
    dialog, _doc = open_dialog(qtbot, context)
    qtbot.keyClick(dialog.title_edit, Qt.Key.Key_End)
    qtbot.keyClicks(dialog.title_edit, " v2")
    assert dialog.spec.title == "Data 1 v2"
    dialog.aspect_buttons["tall"].click()
    assert dialog.spec.aspect_ratio == "tall" and dialog.width_spin.value() == 4.0
    dialog.aspect_buttons["custom"].click()
    assert dialog.width_spin.isEnabled()
    assert dialog.spec.custom_size == (4.0, 6.0)  # starts from the size shown
    dialog.width_spin.setValue(12.5)
    assert dialog.spec.page_size_cm == (12.5, 6.0)
    dialog.show_legend.click()
    assert dialog.spec.show_legend is False
    assert dialog.preview.spec == dialog.spec  # the preview follows at once


def test_axes_tab(qtbot: QtBot, context: Context) -> None:
    dialog, _doc = open_dialog(qtbot, context)
    dialog.set_section(AXES)
    assert dialog.options_box.isVisible()
    dialog.axis_title.setText("Dose (nM)")
    dialog.axis_title.textEdited.emit("Dose (nM)")
    dialog.log.click()
    assert dialog.spec.x_axis.title == "Dose (nM)" and dialog.spec.x_axis.scale is Scale.LOG
    qtbot.waitUntil(dialog.preview_ready, timeout=5000)  # new plot data for the log axis
    dialog.axes_list.setCurrentRow(1)
    assert dialog.axis_title.text() == dialog.spec.y_axis.title
    dialog.axis_from.setText("0")
    dialog.axis_from.textEdited.emit("0")
    dialog.axis_to.setText("100")
    dialog.axis_to.textEdited.emit("100")
    assert (dialog.spec.y_axis.minimum, dialog.spec.y_axis.maximum) == (0.0, 100.0)
    dialog.axis_labels.click()
    assert dialog.spec.y_axis.show_labels is False


@pytest.mark.parametrize(
    ("low", "high", "log", "reason"),
    [
        ("10", "1", False, "Enter a From value below the To value."),
        ("abc", "", False, "Enter a number for the range, or leave it empty for Auto."),
        ("0", "", True, "A log axis must start above 0. Enter a From value above 0."),
    ],
)
def test_a_bad_range_disables_apply_and_says_why(
    qtbot: QtBot, context: Context, low: str, high: str, log: bool, reason: str
) -> None:
    dialog, _doc = open_dialog(qtbot, context)
    dialog.set_section(AXES)
    if log:
        dialog.log.click()
    dialog.axis_to.setText(high)
    dialog.axis_from.setText(low)
    dialog.axis_from.textEdited.emit(low)
    ok = dialog.skeleton.ok_button
    assert ok is not None and not ok.isEnabled()
    assert dialog.skeleton.feedback.text() == reason
    dialog.axis_from.setText("")
    dialog.axis_to.setText("")
    dialog.axis_from.textEdited.emit("")
    assert ok.isEnabled() and not dialog.skeleton.feedback.text()


def test_series_style_and_mixed_values(qtbot: QtBot, context: Context) -> None:
    dialog, _doc = open_dialog(qtbot, context)
    dialog.set_section(SERIES)
    control, drug = (s.source for s in dialog.spec.series[:2])
    dialog.select_series([control, drug])
    assert dialog.selection_note.text() == "2 series selected."
    assert dialog.symbol.currentText() == MIXED  # circle and square
    assert dialog.symbol_size.value() == 7.0  # the same in both
    dialog.symbol.setCurrentIndex(dialog.symbol.index_of(Symbol.DIAMOND))
    dialog.symbol.activated.emit(dialog.symbol.currentIndex())
    assert {s.symbol for s in dialog.spec.series[:2]} == {Symbol.DIAMOND}
    dialog.symbol_size.setValue(9.0)
    dialog.line_style.setCurrentIndex(dialog.line_style.index_of(LineStyle.DASH))
    dialog.line_style.activated.emit(dialog.line_style.currentIndex())
    dialog.line_width.setValue(2.5)
    first, second = dialog.spec.series[:2]
    assert (first.size, first.line_style, first.line_width) == (9.0, LineStyle.DASH, 2.5)
    assert first == replace(second, source=first.source, color=first.color)
    dialog.select_series([control])
    dialog.error_bars.setCurrentIndex(dialog.error_bars.index_of(ErrorBars.SEM))
    dialog.error_bars.activated.emit(dialog.error_bars.currentIndex())
    assert dialog.spec.series[0].error_bars is ErrorBars.SEM
    qtbot.waitUntil(dialog.preview_ready, timeout=5000)


def test_series_color_and_legend(qtbot: QtBot, context: Context, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dialog, _doc = open_dialog(qtbot, context)
    dialog.set_section(SERIES)
    dialog.select_series([dialog.spec.series[0].source])
    monkeypatch.setattr(dialog, "_ask_color", lambda start: "#123456")
    dialog.color.setCurrentIndex(dialog.color.index_of("Other…"))
    dialog.color.activated.emit(dialog.color.currentIndex())
    assert dialog.spec.series[0].color == "#123456"
    dialog.in_legend.click()
    assert dialog.spec.series[0].in_legend is False
    assert dialog.series_list.item(0).text() == "Control"


def test_add_remove_and_reorder_series(qtbot: QtBot, context: Context) -> None:
    dialog, _doc = open_dialog(qtbot, context)
    dialog.set_section(SERIES)
    sources = [s.source for s in dialog.spec.series]
    dialog.select_series([sources[0]])
    dialog.remove_selected()
    assert [s.source for s in dialog.spec.series] == sources[1:]
    assert dialog.add_button.isEnabled()
    dialog.add_series(sources[0])
    assert [s.source for s in dialog.spec.series] == [*sources[1:], sources[0]]
    assert not dialog.add_button.isEnabled()
    assert dialog.add_button.toolTip() == "Every series of the table is in the graph."
    assert dialog.selected_sources() == [sources[0]]  # the added series is selected
    dialog.move_selected(-1)
    dialog.move_selected(-1)
    assert [s.source for s in dialog.spec.series] == sources
    assert not dialog.up_button.isEnabled()
    dialog.series_list.setFocus()
    qtbot.keyClick(dialog.series_list, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    assert [s.source for s in dialog.spec.series][1] == sources[0]
    dialog.select_series(sources)
    assert not dialog.remove_button.isEnabled()  # a graph keeps one series


def test_bar_graphs_disable_what_they_do_not_use(qtbot: QtBot, context: Context) -> None:
    dialog, _doc = open_dialog(qtbot, context, "bar")
    dialog.set_section(SERIES)
    assert not dialog.symbol.isEnabled() and not dialog.line_width.isEnabled()
    assert dialog.error_bars.isEnabled() and dialog.color.isEnabled()
    assert "Bar and box graphs use only" in dialog.series_note.text()
    dialog.set_section(AXES)
    assert not dialog.axis_from.isEnabled()
    assert dialog.axis_note.text().startswith("The X axis of a bar or box graph")
    dialog.axes_list.setCurrentRow(1)
    assert not dialog.log.isEnabled() and dialog.axis_from.isEnabled()


def test_colors_and_text(qtbot: QtBot, context: Context) -> None:
    dialog, _doc = open_dialog(qtbot, context)
    dialog.set_section(COLORS)
    dialog.text_size.setValue(14)
    assert dialog.spec.text_size == 14.0
    dialog.set_section(SERIES)
    dialog.select_series([dialog.spec.series[0].source])
    dialog.color.setCurrentIndex(dialog.color.index_of("#e69f00"))
    dialog.color.activated.emit(dialog.color.currentIndex())
    dialog.set_section(COLORS)
    dialog.scheme_combo.activated.emit(0)  # the scheme again: its colours in order
    assert dialog.spec.series[0].color == "#1d3557"


# --- in the window: one undo step per Apply ---------------------------------------------


@pytest.fixture
def tab(window: MainWindow, qtbot: QtBot) -> DocumentTab:
    tab = window.new_document()
    tab.document.execute(AddTable(replicate_table(), 0))
    tab.plot(ChartFamily.XY)
    settle(qtbot, tab)
    return tab


def run_format(tab: DocumentTab, monkeypatch, change, accept: bool = True) -> None:  # type: ignore[no-untyped-def]
    def exec_dialog(dialog: QDialog) -> int:
        if isinstance(dialog, FormatDialog):
            change(dialog)
        return 1 if accept else 0

    monkeypatch.setattr(tab, "_exec_dialog", exec_dialog)


def test_apply_is_one_undo_step(
    window: MainWindow, tab: DocumentTab, qtbot: QtBot, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    before = tab.document.graphs[0]

    def change(dialog: FormatDialog) -> None:
        dialog.title_edit.setText("Kinetics")
        dialog.title_edit.textEdited.emit("Kinetics")
        dialog.show_legend.click()
        dialog.set_section(AXES)
        dialog.log.click()

    run_format(tab, monkeypatch, change)
    window.registry.action("graph.format").trigger()
    after = tab.document.graphs[0]
    assert (after.title, after.show_legend, after.x_axis.scale) == ("Kinetics", False, Scale.LOG)
    assert tab.document.undo_stack.undo_text == "Format graph"
    tab.document.undo()
    assert tab.document.graphs[0] == before
    settle(qtbot, tab)


def test_cancel_and_no_change_add_no_step(
    window: MainWindow, tab: DocumentTab, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    steps = tab.document.undo_stack.undo_text

    def change(dialog: FormatDialog) -> None:
        dialog.show_legend.click()

    run_format(tab, monkeypatch, change, accept=False)
    assert tab.format_graph() is False
    run_format(tab, monkeypatch, lambda dialog: None)
    assert tab.format_graph() is False
    assert tab.document.undo_stack.undo_text == steps


def test_double_click_on_the_graph_opens_format(tab: DocumentTab, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    opened: list[str] = []
    monkeypatch.setattr(tab, "format_graph", lambda graph_id=None: opened.append("format"))
    tab.graph_panel.format_requested.disconnect()
    tab.graph_panel.format_requested.connect(tab.format_graph)
    chart = tab.graph_panel.chart
    assert chart is not None
    chart.double_clicked.emit()
    assert opened == ["format"]


# --- the Graph panel's toolbar and Change graph type -------------------------------------


def test_graph_toolbar_buttons_are_registered_actions(window: MainWindow, tab: DocumentTab) -> None:
    actions = tab.graph_panel.toolbar.actions()
    assert [a.objectName() for a in actions] == [b.action_id for b in GRAPH_BUTTONS]
    for action in actions:
        assert window.registry.action(action.objectName()) is action
        assert action.isEnabled()
    assert window.registry.action("graph.format").toolTip() == "Format graph (Ctrl+Shift+F)"
    assert window.registry.action("graph.export").toolTip() == "Export graph (Ctrl+Shift+X)"


def test_disabled_graph_buttons_say_why(window: MainWindow) -> None:
    tab = window.new_document()
    tab.show_graph()
    for action_id, reason in (
        ("graph.format", "There is no graph to format"),
        ("graph.export", "There is no graph to export"),
        ("graph.type", "There is no graph to change"),
        ("graph.delete", "There is no graph to delete"),
    ):
        action = window.registry.action(action_id)
        assert not action.isEnabled() and action.toolTip() == reason


def test_change_graph_type_is_one_undo_step(
    window: MainWindow, tab: DocumentTab, qtbot: QtBot
) -> None:
    r = window.registry
    assert r.action("graph.type.scatter").isChecked()
    r.action("graph.type.box").trigger()
    graph = tab.document.graphs[0]
    assert graph.family is ChartFamily.BAR and graph.variant is ChartVariant.BOX
    assert tab.document.undo_stack.undo_text == "Change graph type to box"
    assert r.action("graph.type.box").isChecked()
    settle(qtbot, tab)
    tab.document.undo()
    assert tab.document.graphs[0].variant is ChartVariant.SCATTER
    settle(qtbot, tab)


def test_a_type_the_table_cannot_draw_says_why(window: MainWindow, qtbot: QtBot) -> None:
    tab = window.new_document()
    tab.document.execute(AddTable(column_table(), 0))
    tab.plot(ChartFamily.BAR)
    settle(qtbot, tab)
    window._update_state()
    scatter = window.registry.action("graph.type.scatter")
    assert not scatter.isEnabled()
    assert scatter.text().endswith("Needs an X column with values")
    assert scatter.toolTip() == "Needs an X column with values"


# --- export -----------------------------------------------------------------------------


def test_export_dialog(qtbot: QtBot) -> None:
    doc, graph_id = graph_document()
    dialog = ExportDialog(doc.graph(graph_id))
    qtbot.addWidget(dialog)
    assert dialog.windowTitle() == "Export graph"
    assert dialog.export_format is ExportFormat.PNG and dialog.dpi.value() == 300
    assert "9 × 6 cm, 1,062 × 708 pixels" in dialog.size_note.text()
    dialog.format_combo.setCurrentIndex(dialog.format_combo.findData(ExportFormat.PDF))
    assert not dialog.dpi.isEnabled()
    assert dialog.size_note.text() == "The graph is exported at its size: 9 × 6 cm."


@pytest.mark.parametrize("fmt", list(ExportFormat))
def test_export_writes_the_file_in_a_worker(
    window: MainWindow,
    tab: DocumentTab,
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch,
    fmt: ExportFormat,
) -> None:  # type: ignore[no-untyped-def]
    target = tmp_path / f"graph.{fmt.value}"

    def exec_dialog(dialog: QDialog) -> int:
        assert isinstance(dialog, ExportDialog)
        dialog.format_combo.setCurrentIndex(dialog.format_combo.findData(fmt))
        dialog.dpi.setValue(150)
        return 1

    messages: list[str] = []
    tab.message.connect(messages.append)
    monkeypatch.setattr(tab, "_exec_dialog", exec_dialog)
    monkeypatch.setattr(tab, "_ask_export_path", lambda spec, chosen: target)
    window.registry.action("graph.export").trigger()
    qtbot.waitUntil(lambda: f"Exported graph.{fmt.value}." in messages, timeout=30_000)
    assert target.stat().st_size > 1000
    assert messages[0] == f"Exporting graph.{fmt.value}…"
    assert tab._export_settings == (fmt.value, 150)  # offered next time


def test_a_failed_export_says_why(tab: DocumentTab, qtbot: QtBot, tmp_path: Path) -> None:
    messages: list[str] = []
    tab.message.connect(messages.append)
    target = tmp_path / "missing" / "graph.png"
    graph_id = tab.document.graphs[0].id
    tab.start_export(graph_id, target, ExportFormat.PNG, 300)
    qtbot.waitUntil(lambda: any(m.startswith("Could not export") for m in messages), timeout=30_000)
    assert not target.exists()
