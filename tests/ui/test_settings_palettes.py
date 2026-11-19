"""The Settings dialog's new settings, the palette editor, and the Format dialog's
bar-family, colour and font options."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from curvis.analysis.result import PDisplay
from curvis.charts.builder import default_spec, with_variant
from curvis.charts.colors import COLOR_BLIND_SAFE, CURVIS, CUSTOM_ID, ColorScheme
from curvis.charts.spec import BinMode, ChartFamily, ChartVariant, Grouping
from curvis.compute import Scheduler
from curvis.core import AddTable, Document, add_graph
from curvis.settings import Settings
from curvis.ui.format_dialog import COLORS, GRAPH, FormatDialog
from curvis.ui.palette_dialog import NEEDS_COLOR, NEEDS_NAME, PaletteDialog
from curvis.ui.settings_dialog import SettingsDialog
from tests.unit.charts_tables import spread_table, two_way_table

# --- Settings dialog -------------------------------------------------------------------


def test_save_writes_the_new_settings(qtbot: QtBot, settings: Settings) -> None:
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    dialog.palette_combo.setCurrentIndex(
        next(
            i
            for i in range(dialog.palette_combo.count())
            if dialog.palette_combo.itemText(i) == "Color-blind safe"
        )
    )
    dialog.size_combo.setCurrentIndex(dialog.size_combo.findData("custom"))
    assert dialog.width_spin.isEnabled()
    dialog.width_spin.setValue(11.0)
    dialog.height_spin.setValue(7.0)
    dialog.font_combo.setCurrentIndex(dialog.font_combo.findData("inter"))
    dialog.text_size.setValue(9.0)
    dialog.p_display.setCurrentIndex(dialog.p_display.findData(PDisplay.EXACT))
    dialog.export_format.setCurrentIndex(dialog.export_format.findData("svg"))
    dialog.png_dpi.setValue(450)
    dialog.accept()
    assert settings.graph_palette == COLOR_BLIND_SAFE
    assert settings.graph_size == ("custom", (11.0, 7.0))
    assert settings.graph_font == "inter" and settings.graph_text_size == 9.0
    assert settings.p_display is PDisplay.EXACT
    assert (settings.export_format, settings.png_dpi) == ("svg", 450)


def test_palettes_are_made_changed_and_deleted(
    qtbot: QtBot, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    edits: list[ColorScheme] = []

    def edit(self: SettingsDialog, palette: ColorScheme, title: str) -> ColorScheme:
        edits.append(palette)
        return ColorScheme(palette.id, "Lab", palette.page, palette.ink, ("#aa0000", "#00aa00"))

    monkeypatch.setattr(SettingsDialog, "_edit_palette", edit)
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    assert not dialog.edit_palette.isEnabled() and not dialog.delete_palette.isEnabled()
    dialog.new_palette.click()  # starts from the chosen built-in one
    assert edits[0].series == CURVIS.series and edits[0].id.startswith("user:")
    assert dialog.palette_combo.currentText() == "Lab (yours)"
    assert dialog.edit_palette.isEnabled() and dialog.delete_palette.isEnabled()
    dialog.edit_palette.click()
    dialog.accept()
    (mine,) = settings.palettes
    assert mine.name == "Lab" and mine.series == ("#aa0000", "#00aa00")
    assert settings.graph_palette == mine

    again = SettingsDialog(settings)
    qtbot.addWidget(again)
    assert again.palette_combo.currentText() == "Lab (yours)"
    again.delete_palette.click()
    again.accept()
    assert settings.palettes == [] and settings.graph_palette == CURVIS


def test_clear_recent_and_reset_ask_first(
    qtbot: QtBot, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.add_recent_project(tmp_path / "a.cvp")
    settings.graph_font = "inter"
    answers = [False, True, False, True]
    asked: list[str] = []

    def ask(self: SettingsDialog, title: str, question: str) -> bool:
        asked.append(title)
        return answers.pop(0)

    monkeypatch.setattr(SettingsDialog, "_ask", ask)
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    dialog.clear_recent_button.click()
    assert len(settings.recent_projects) == 1  # declined
    dialog.clear_recent_button.click()
    assert settings.recent_projects == [] and not dialog.clear_recent_button.isEnabled()
    dialog.reset_button.click()
    assert settings.graph_font == "inter"  # declined
    dialog.reset_button.click()
    assert settings.graph_font == "dejavu_sans"
    assert dialog.font_combo.currentData() == "dejavu_sans"  # the form shows the defaults
    assert asked == ["Clear recent projects"] * 2 + ["Reset to defaults"] * 2


# --- palette editor --------------------------------------------------------------------


def test_palette_editor_adds_changes_moves_and_removes(qtbot: QtBot) -> None:
    dialog = PaletteDialog(CURVIS)
    qtbot.addWidget(dialog)
    assert dialog.series() == CURVIS.series
    dialog.colors.setCurrentRow(0)
    dialog.add_color("#ABCDEF")  # after the selected one
    assert dialog.series()[1] == "#abcdef"
    dialog.change_color("#123456")
    assert dialog.series()[1] == "#123456"
    dialog.move_color(-1)
    assert dialog.series()[0] == "#123456" and dialog.colors.currentRow() == 0
    assert not dialog.up_button.isEnabled()
    dialog.remove_color()
    assert dialog.series() == CURVIS.series
    edited = dialog.edited
    assert edited == CURVIS  # unchanged: still the built-in scheme
    dialog.name_edit.setText("Mine")
    assert dialog.edited.id == CUSTOM_ID and dialog.edited.name == "Mine"


def test_palette_editor_says_why_it_cannot_save(qtbot: QtBot) -> None:
    dialog = PaletteDialog(ColorScheme("x", "One", "#ffffff", "#000000", ("#ff0000",)))
    qtbot.addWidget(dialog)
    ok = dialog.skeleton.ok_button
    assert ok is not None and ok.isEnabled()
    dialog.remove_color()
    assert not ok.isEnabled() and dialog.skeleton.feedback.text() == NEEDS_COLOR
    dialog.add_color("#00ff00")
    dialog.name_edit.setText(" ")
    assert not ok.isEnabled() and dialog.skeleton.feedback.text() == NEEDS_NAME


# --- Format dialog ---------------------------------------------------------------------


def _format(qtbot: QtBot, variant: ChartVariant, table_name: str = "two_way") -> FormatDialog:
    table = two_way_table() if table_name == "two_way" else spread_table()
    doc = Document()
    doc.execute(AddTable(table))
    spec = with_variant(default_spec(table, ChartFamily.BAR), variant, table)
    doc.execute(add_graph(doc, spec))
    scheduler = Scheduler()
    dialog = FormatDialog(doc, spec.id, scheduler=scheduler)
    qtbot.addWidget(dialog)
    dialog.destroyed.connect(scheduler.shutdown)
    return dialog


def test_clusters_switch_and_recolor(qtbot: QtBot) -> None:
    dialog = _format(qtbot, ChartVariant.BAR)
    dialog.set_section(GRAPH)
    assert not dialog.grouping.isHidden() and dialog.grouping.isEnabled()
    assert dialog.spec.grouping is Grouping.BY_SERIES
    colors = {s.color for s in dialog.spec.series}
    assert len(colors) == 2  # one per category (Male, Female)
    index = dialog.grouping.findData(Grouping.BY_CATEGORY)
    dialog.grouping.setCurrentIndex(index)
    dialog.grouping.activated.emit(index)
    assert dialog.spec.grouping is Grouping.BY_CATEGORY
    assert len({s.color for s in dialog.spec.series}) == 3  # one per series name
    assert dialog.grouping.itemText(index) == "By Sex, colored by series"


def test_violin_trim_points_and_histogram_bins(qtbot: QtBot) -> None:
    violin = _format(qtbot, ChartVariant.VIOLIN, "spread")
    violin.set_section(GRAPH)
    assert not violin.violin_trim.isHidden() and not violin.show_points.isHidden()
    assert violin.bin_mode.isHidden()
    violin.violin_trim.setChecked(False)
    violin.show_points.setChecked(True)
    assert not violin.spec.violin_trim and violin.spec.show_points

    hist = _format(qtbot, ChartVariant.HISTOGRAM, "spread")
    hist.set_section(GRAPH)
    assert not hist.bin_mode.isHidden() and hist.violin_trim.isHidden()
    assert not hist.bin_count.isEnabled()
    index = hist.bin_mode.findData(BinMode.WIDTH)
    hist.bin_mode.setCurrentIndex(index)
    hist.bin_mode.activated.emit(index)
    assert hist.bin_width.isEnabled() and not hist.bin_count.isEnabled()
    hist.bin_width.setText("0")
    hist.bin_width.textEdited.emit("0")
    assert hist.skeleton.ok_button is not None and not hist.skeleton.ok_button.isEnabled()
    hist.bin_width.setText("2.5")
    hist.bin_width.textEdited.emit("2.5")
    hist.bin_start.setText("-1")
    hist.bin_start.textEdited.emit("-1")
    assert hist.spec.bins.mode is BinMode.WIDTH
    assert (hist.spec.bins.width, hist.spec.bins.start) == (2.5, -1.0)
    assert hist.skeleton.ok_button.isEnabled()


def test_font_scheme_and_palette_editor(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    dialog = _format(qtbot, ChartVariant.BOX, "spread")
    dialog.set_section(COLORS)
    index = dialog.font_combo.findData("liberation_sans")
    dialog.font_combo.setCurrentIndex(index)
    dialog.font_combo.activated.emit(index)
    assert dialog.spec.font == "liberation_sans"
    mine = ColorScheme(CUSTOM_ID, "Mine", "#ffffff", "#1a1a1a", ("#111111", "#222222"))
    monkeypatch.setattr(FormatDialog, "_ask_palette", lambda self, palette: mine)
    dialog.edit_colors.click()
    assert dialog.spec.palette == mine
    assert [s.color for s in dialog.spec.series] == ["#111111", "#222222", "#111111"]
    assert dialog.scheme_combo.currentText() == "This graph's colors (Mine)"


def test_horizontal_bars_name_the_series_on_the_y_axis(qtbot: QtBot) -> None:
    from curvis.ui.format_dialog import AXES

    dialog = _format(qtbot, ChartVariant.HORIZONTAL_BAR, "spread")
    dialog.set_section(AXES)
    dialog.axes_list.setCurrentRow(0)  # X: the values
    assert dialog.axis_from.isEnabled() and not dialog.log.isEnabled()
    dialog.axes_list.setCurrentRow(1)  # Y: the series
    assert not dialog.axis_from.isEnabled()
    assert dialog.axis_note.text().startswith("This axis shows the series by name")
