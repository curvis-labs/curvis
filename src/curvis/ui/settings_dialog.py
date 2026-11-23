"""The Settings dialog. Changes apply when you click Save.

A simple dialog (docs/DESIGN.md §1) with its settings in three tabs, as SPEC.md groups
them: **General** (theme, decimal places, recent projects, the crash log), **New graphs**
(palette, size, font and text size that new graphs copy) and **Results and export** (how
P values are shown, the Export dialog's starting format and PNG resolution).

*Clear recent projects…* and *Reset to defaults…* ask first and act at once. Reset
restores every setting here; recent projects, global constants and recovery copies are
kept. Settings only give starting values: graphs that exist keep their own palette,
size, font and text size.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from curvis.analysis.result import PDisplay
from curvis.charts.colors import SCHEMES, USER_PREFIX, ColorScheme, new_palette_id
from curvis.charts.fonts import FONTS
from curvis.charts.spec import (
    ASPECT_LABELS,
    ASPECT_PRESETS,
    CUSTOM_ASPECT,
    MAX_TEXT_SIZE,
    MIN_TEXT_SIZE,
)
from curvis.settings import Settings, Theme
from curvis.settings.store import (
    DECIMAL_PLACES_MAX,
    DECIMAL_PLACES_MIN,
    EXPORT_FORMATS,
    GRAPH_SIZE_RANGE,
    PNG_DPI_MAX,
    PNG_DPI_MIN,
)
from curvis.ui.dialogs import (
    FIELD_WIDTH,
    SPACING,
    DialogSkeleton,
    buddy_label,
    form_layout,
    secondary_label,
    size_simple,
)
from curvis.ui.palette_dialog import PaletteDialog, swatch

CRASH_LOG_TEXT = "Keep a &crash log on this computer"
CRASH_LOG_NOTE = (
    "Nothing is sent. When Curvis runs into a problem, it saves a report you can copy "
    "and send yourself."
)
NEW_GRAPHS_NOTE = (
    "New graphs start with these. Graphs you have made keep their own colors, size and font."
)
P_NOTE = "Only how P values are shown: results keep full precision and the stars stay the same."
CLEAR_RECENT_QUESTION = "Clear the list of recent projects? The project files are kept."
RESET_QUESTION = (
    "Reset every setting to its default? Recent projects, global constants and recovery "
    "copies are kept."
)
DELETE_PALETTE_QUESTION = "Delete the palette “{name}”? Graphs that use its colors keep them."
EXPORT_LABELS = {"png": "PNG image", "svg": "SVG drawing", "pdf": "PDF document"}


class SettingsDialog(QDialog):
    """Edit ``settings``; Save applies the changes."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self._settings = settings
        self._palettes: list[ColorScheme] = settings.palettes
        self._palette: ColorScheme = settings.graph_palette

        self.skeleton = DialogSkeleton(self, "Save")
        self.buttons = self.skeleton.buttons
        self.tabs = QTabWidget(self)
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setAccessibleName("Settings section")
        self.tabs.addTab(self._build_general(), "&General")
        self.tabs.addTab(self._build_graphs(), "&New graphs")
        self.tabs.addTab(self._build_results(), "Res&ults and export")
        self.skeleton.body.addWidget(self.tabs, 1)
        self.reset_button = QPushButton("R&eset to defaults…", self)
        self.reset_button.clicked.connect(self.reset_to_defaults)
        self.skeleton.add_secondary(self.reset_button)
        self._load()
        size_simple(self)

    # --- building ------------------------------------------------------------------

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, SPACING, 0, 0)
        layout.setSpacing(SPACING)
        return page, layout

    def _build_general(self) -> QWidget:
        page, layout = self._page()
        self.theme = QComboBox(page)
        for theme in Theme:
            self.theme.addItem(theme.label, theme)
        self.decimal_places = QSpinBox(page)
        self.decimal_places.setRange(DECIMAL_PLACES_MIN, DECIMAL_PLACES_MAX)
        self.decimal_places.setToolTip("Number of decimal places shown in calculated columns.")
        self.decimal_places.setMaximumWidth(FIELD_WIDTH)
        self.import_decimal = QComboBox(page)
        self.import_decimal.addItem("Point (1.5)", False)
        self.import_decimal.addItem("Comma (1,5)", True)
        self.import_decimal.setToolTip(
            "Used on import when auto-detect cannot tell the decimal mark from the data."
        )
        self.import_decimal.setMaximumWidth(FIELD_WIDTH * 2)
        form = form_layout()
        form.addRow(buddy_label("&Theme", self.theme), self.theme)
        form.addRow(
            buddy_label("&Decimal places in calculated columns", self.decimal_places),
            self.decimal_places,
        )
        form.addRow(
            buddy_label("Decimal &mark for import", self.import_decimal), self.import_decimal
        )
        self.clear_recent_button = QPushButton("C&lear recent projects…", page)
        self.clear_recent_button.clicked.connect(self.clear_recent_projects)
        recent = QHBoxLayout()
        recent.addWidget(self.clear_recent_button)
        recent.addStretch(1)
        form.addRow(QLabel("Recent projects"), recent)
        layout.addLayout(form)
        self.crash_log = QCheckBox(CRASH_LOG_TEXT, page)
        self.crash_log_note = secondary_label(CRASH_LOG_NOTE, page)
        layout.addWidget(self.crash_log)
        layout.addWidget(self.crash_log_note)
        layout.addStretch(1)
        return page

    def _build_graphs(self) -> QWidget:
        page, layout = self._page()
        form = form_layout()
        self.palette_combo = QComboBox(page)
        self.palette_combo.setAccessibleName("Palette of new graphs")
        self.new_palette = QPushButton("Ne&w…", page)
        self.new_palette.setToolTip("Make a palette from the one chosen")
        self.edit_palette = QPushButton("Ed&it…", page)
        self.delete_palette = QPushButton("Dele&te", page)
        self.delete_palette.setToolTip("Delete this palette of yours")
        palette_row = QHBoxLayout()
        palette_row.addWidget(self.palette_combo, 1)
        for button in (self.new_palette, self.edit_palette, self.delete_palette):
            palette_row.addWidget(button)
        form.addRow(buddy_label("&Palette", self.palette_combo), palette_row)
        self.size_combo = QComboBox(page)
        for key in (*ASPECT_PRESETS, CUSTOM_ASPECT):
            label = ASPECT_LABELS[key]
            if key in ASPECT_PRESETS:
                width, height = ASPECT_PRESETS[key]
                label = f"{label} ({width:g} × {height:g} cm)"
            self.size_combo.addItem(label, key)
        self.width_spin = QDoubleSpinBox(page)
        self.height_spin = QDoubleSpinBox(page)
        for spin, name in ((self.width_spin, "Width"), (self.height_spin, "Height")):
            spin.setRange(*GRAPH_SIZE_RANGE)
            spin.setDecimals(1)
            spin.setSingleStep(0.5)
            spin.setSuffix(" cm")
            spin.setMaximumWidth(FIELD_WIDTH)
            spin.setAccessibleName(f"Custom {name.lower()} in cm")
        size_row = QHBoxLayout()
        size_row.addWidget(self.size_combo)
        size_row.addWidget(self.width_spin)
        size_row.addWidget(QLabel("×", page))
        size_row.addWidget(self.height_spin)
        size_row.addStretch(1)
        form.addRow(buddy_label("Si&ze", self.size_combo), size_row)
        self.font_combo = QComboBox(page)
        for font in FONTS.values():
            text = f"{font.family} ({font.note})" if font.note else font.family
            self.font_combo.addItem(text, font.id)
        form.addRow(buddy_label("Chart &font", self.font_combo), self.font_combo)
        self.text_size = QDoubleSpinBox(page)
        self.text_size.setRange(MIN_TEXT_SIZE, MAX_TEXT_SIZE)
        self.text_size.setDecimals(1)
        self.text_size.setSingleStep(0.5)
        self.text_size.setSuffix(" pt")
        self.text_size.setMaximumWidth(FIELD_WIDTH)
        form.addRow(buddy_label("Chart te&xt size", self.text_size), self.text_size)
        layout.addLayout(form)
        layout.addWidget(secondary_label(NEW_GRAPHS_NOTE, page))
        layout.addStretch(1)
        self.palette_combo.currentIndexChanged.connect(lambda _i: self._on_palette_chosen())
        self.new_palette.clicked.connect(self.make_palette)
        self.edit_palette.clicked.connect(self.change_palette)
        self.delete_palette.clicked.connect(self.remove_palette)
        self.size_combo.currentIndexChanged.connect(lambda _i: self._update_size())
        return page

    def _build_results(self) -> QWidget:
        page, layout = self._page()
        form = form_layout()
        self.p_display = QComboBox(page)
        for choice in PDisplay:
            self.p_display.addItem(choice.label, choice)
        form.addRow(buddy_label("P &values", self.p_display), self.p_display)
        self.export_format = QComboBox(page)
        for fmt in EXPORT_FORMATS:
            self.export_format.addItem(f"{fmt.upper()} · {EXPORT_LABELS[fmt]}", fmt)
        form.addRow(buddy_label("E&xport format", self.export_format), self.export_format)
        self.png_dpi = QSpinBox(page)
        self.png_dpi.setRange(PNG_DPI_MIN, PNG_DPI_MAX)
        self.png_dpi.setSingleStep(50)
        self.png_dpi.setSuffix(" dpi")
        self.png_dpi.setMaximumWidth(FIELD_WIDTH)
        form.addRow(buddy_label("PNG &resolution", self.png_dpi), self.png_dpi)
        layout.addLayout(form)
        layout.addWidget(secondary_label(P_NOTE, page))
        layout.addStretch(1)
        return page

    # --- loading -------------------------------------------------------------------

    def _load(self) -> None:
        s = self._settings
        self.theme.setCurrentIndex(self.theme.findData(s.theme))
        self.decimal_places.setValue(s.decimal_places)
        self.import_decimal.setCurrentIndex(self.import_decimal.findData(s.import_decimal_comma))
        self.crash_log.setChecked(s.crash_log)
        self._palettes = s.palettes
        self._palette = s.graph_palette
        self._fill_palettes()
        preset, custom = s.graph_size
        self.size_combo.setCurrentIndex(self.size_combo.findData(preset))
        width, height = custom or ASPECT_PRESETS.get(preset, ASPECT_PRESETS["wide"])
        self.width_spin.setValue(width)
        self.height_spin.setValue(height)
        self._update_size()
        self.font_combo.setCurrentIndex(self.font_combo.findData(s.graph_font))
        self.text_size.setValue(s.graph_text_size)
        self.p_display.setCurrentIndex(self.p_display.findData(s.p_display))
        self.export_format.setCurrentIndex(self.export_format.findData(s.export_format))
        self.png_dpi.setValue(s.png_dpi)
        self.clear_recent_button.setEnabled(bool(s.recent_projects))

    def _choices(self) -> list[ColorScheme]:
        """The palettes to pick from: built-in, the user's, and the chosen one if it is
        neither (a palette edited without a name of its own)."""
        found = [*SCHEMES.values(), *self._palettes]
        if not any(p.same_colors(self._palette) and p.name == self._palette.name for p in found):
            found.append(self._palette)
        return found

    def _fill_palettes(self) -> None:
        self.palette_combo.blockSignals(True)
        self.palette_combo.clear()
        chosen = 0
        for index, palette in enumerate(self._choices()):
            mine = palette.id.startswith(USER_PREFIX)
            self.palette_combo.addItem(
                swatch(palette.series_color(0)),
                f"{palette.name} (yours)" if mine else palette.name,
                index,
            )
            if palette.same_colors(self._palette) and palette.name == self._palette.name:
                chosen = index
        self.palette_combo.setCurrentIndex(chosen)
        self.palette_combo.blockSignals(False)
        self._update_palette_buttons()

    def _current_palette(self) -> ColorScheme:
        return self._choices()[max(0, self.palette_combo.currentIndex())]

    def _on_palette_chosen(self) -> None:
        self._palette = self._current_palette()
        self._update_palette_buttons()

    def _update_palette_buttons(self) -> None:
        mine = self._current_palette().id.startswith(USER_PREFIX)
        self.edit_palette.setEnabled(mine)
        self.edit_palette.setToolTip(
            "" if mine else "Built-in palettes cannot be changed; use New…"
        )
        self.delete_palette.setEnabled(mine)

    def _update_size(self) -> None:
        custom = self.size_combo.currentData() == CUSTOM_ASPECT
        self.width_spin.setEnabled(custom)
        self.height_spin.setEnabled(custom)

    # --- palettes --------------------------------------------------------------------

    def _edit_palette(self, palette: ColorScheme, title: str) -> ColorScheme | None:
        """Show the palette editor; the palette as edited, or None (tests replace it)."""
        dialog = PaletteDialog(palette, title=title, ok_text="Save", parent=self)
        try:
            return dialog.edited if dialog.exec() == QDialog.DialogCode.Accepted else None
        finally:
            dialog.deleteLater()

    def make_palette(self) -> None:
        """A new palette of the user's, starting from the chosen one."""
        base = self._current_palette()
        start = replace(base, id=new_palette_id(), name=f"{base.name} copy")
        made = self._edit_palette(start, "New palette")
        if made is None:
            return
        made = replace(made, id=start.id)
        self._palettes.append(made)
        self._palette = made
        self._fill_palettes()

    def change_palette(self) -> None:
        current = self._current_palette()
        if not current.id.startswith(USER_PREFIX):
            return
        changed = self._edit_palette(current, "Edit palette")
        if changed is None:
            return
        changed = replace(changed, id=current.id)
        self._palettes = [changed if p.id == current.id else p for p in self._palettes]
        self._palette = changed
        self._fill_palettes()

    def remove_palette(self) -> None:
        """Delete the chosen palette of the user's, after asking. Graphs made with it keep
        their colors (each has its own copy)."""
        current = self._current_palette()
        if not current.id.startswith(USER_PREFIX):
            return
        if not self._ask("Delete palette", DELETE_PALETTE_QUESTION.format(name=current.name)):
            return
        self._palettes = [p for p in self._palettes if p.id != current.id]
        self._palette = next(iter(SCHEMES.values()))
        self._fill_palettes()

    # --- actions that apply at once ----------------------------------------------------

    def _ask(self, title: str, question: str) -> bool:
        """Ask a yes-or-no question (tests replace it)."""
        buttons = QMessageBox.StandardButton
        answer = QMessageBox.question(self, title, question, buttons.Yes | buttons.No, buttons.No)
        return answer == buttons.Yes

    def clear_recent_projects(self) -> None:
        if self._ask("Clear recent projects", CLEAR_RECENT_QUESTION):
            self._settings.clear_recent_projects()
            self.clear_recent_button.setEnabled(False)

    def reset_to_defaults(self) -> None:
        if self._ask("Reset to defaults", RESET_QUESTION):
            self._settings.reset_to_defaults()
            self._load()

    # --- saving --------------------------------------------------------------------

    def accept(self) -> None:
        s = self._settings
        theme = Theme(self.theme.currentData())
        if theme != s.theme:
            s.theme = theme
        if self.decimal_places.value() != s.decimal_places:
            s.decimal_places = self.decimal_places.value()
        if bool(self.import_decimal.currentData()) != s.import_decimal_comma:
            s.import_decimal_comma = bool(self.import_decimal.currentData())
        if self.crash_log.isChecked() != s.crash_log:
            s.crash_log = self.crash_log.isChecked()
        if self._palettes != s.palettes:
            s.palettes = self._palettes
        if self._palette != s.graph_palette:
            s.graph_palette = self._palette
        preset = str(self.size_combo.currentData())
        custom = (
            (self.width_spin.value(), self.height_spin.value()) if preset == CUSTOM_ASPECT else None
        )
        if (preset, custom) != s.graph_size:
            s.set_graph_size(preset, custom)
        font = str(self.font_combo.currentData())
        if font != s.graph_font:
            s.graph_font = font
        if self.text_size.value() != s.graph_text_size:
            s.graph_text_size = self.text_size.value()
        display = PDisplay(self.p_display.currentData())
        if display is not s.p_display:
            s.p_display = display
        fmt = str(self.export_format.currentData())
        if fmt != s.export_format:
            s.export_format = fmt
        if self.png_dpi.value() != s.png_dpi:
            s.png_dpi = self.png_dpi.value()
        super().accept()
