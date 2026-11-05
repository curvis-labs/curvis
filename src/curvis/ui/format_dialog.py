"""The Format dialog: change how a graph looks, with a live preview.

A workbench dialog (docs/DESIGN.md §1, mockup in docs/mockups/index.html, board 3):

- the sections as tabs: **Graph** (title, size, legend), **Axes**, **Series** and
  **Colors and text**, each with one sentence on what it changes;
- the items to format on the left (the axes; the series, several at once, with Add…,
  Remove, Move up and Move down), their options on the right. Graph and Colors and text
  have nothing to pick, so their options take the left column;
- the **preview** on the right: the Graph panel's renderer drawing the edited spec.
  Options that change what is plotted (scales, error bars, series) recompute the plot
  data through the scheduler; the others redraw at once.

The dialog edits a copy of the graph's :class:`~curvis.charts.spec.ChartSpec` and
nothing else. The caller stores :attr:`FormatDialog.spec` with one
:func:`~curvis.core.edit_graph` command when the user clicks Apply; Cancel leaves the
graph as it was. A value that differs between the selected series shows "Mixed" and
stays as it is until it is changed.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from curvis.analysis.result import Result
from curvis.charts.builder import default_series, error_column_of, series_source
from curvis.charts.colors import SCHEMES, scheme
from curvis.charts.data import ChartData, compute, gather
from curvis.charts.spec import (
    ASPECT_LABELS,
    ASPECT_PRESETS,
    CUSTOM_ASPECT,
    MAX_TEXT_SIZE,
    MIN_TEXT_SIZE,
    AxisSpec,
    ChartFamily,
    ChartSpec,
    ChartVariant,
    ErrorBars,
    FitOverlay,
    LabelAngle,
    LineStyle,
    Scale,
    SeriesSpec,
    Symbol,
)
from curvis.core import Document
from curvis.data import Table, series
from curvis.ui.dialogs import (
    FIELD_WIDTH,
    DialogSkeleton,
    FittedStack,
    buddy_label,
    choice_tabs,
    columns,
    form_layout,
    secondary_label,
    set_tab_order,
    size_workbench,
)

if TYPE_CHECKING:
    from curvis.charts.render_qt.chart_view import ChartView
    from curvis.compute import Scheduler

GRAPH, AXES, SERIES, COLORS = range(4)
SECTIONS = ("Graph", "Axes", "Series", "Colors and text")
DESCRIPTIONS = (
    "The title, size and legend of the graph.",
    "Titles, ranges, labels and scales of the axes.",
    "Symbols, lines, colors, error bars and fitted curves. Select several series to change "
    "them together.",
    "The color scheme and the size of the graph's text.",
)
MIXED = "Mixed"
AUTO = "Auto"
OTHER_COLOR = "Other…"
SYMBOL_LABELS = {
    Symbol.CIRCLE: "● Circle",
    Symbol.SQUARE: "■ Square",
    Symbol.TRIANGLE: "▲ Triangle",
    Symbol.DIAMOND: "◆ Diamond",
    Symbol.NONE: "None",
}
LINE_LABELS = {
    LineStyle.NONE: "None",
    LineStyle.SOLID: "— Solid",
    LineStyle.DASH: "– – Dashed",
    LineStyle.DOT: "··· Dotted",
}
CATEGORY_NOTE = "Bar and box graphs use only the color, error bars and legend of a series."
BOX_NOTE = "Box plots use only the color and legend of a series."
CATEGORY_AXIS_NOTE = "The X axis of a bar or box graph shows the series by name."
BAR_LOG_NOTE = "Bars start at 0, which a log axis cannot show."
Y_ANGLE_NOTE = "Y axis labels are always horizontal."
FIT_PREFIX = "fit:"
"""Item data of a fitted curve in the series list: the prefix and the analysis id."""
FIT_NOTE = "A fitted curve is drawn in the color of its series. Remove takes it off the graph."
EVERY_SERIES_SHOWN = "Every series of the table is in the graph."
LAST_SERIES = "A graph needs at least one series."
PREVIEW_NODE = "format-preview"
SIZE_RANGE = (1.0, 50.0)
"""Smallest and largest custom page side, in cm."""

type Problem = str | None


def _swatch(color: str, size: int = 12) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(color))
    return QIcon(pixmap)


def _number(text: str) -> float | None:
    """The number typed in a range field, None when empty; ValueError otherwise."""
    text = text.strip().replace(",", ".")
    if not text or text.lower() == AUTO.lower():
        return None
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(text)
    return value


def _format_limit(value: float | None) -> str:
    return "" if value is None else f"{value:.10g}"


def data_key(spec: ChartSpec) -> object:
    """What decides the plot data of ``spec`` (curvis.charts.data reads these)."""
    return (
        spec.family,
        spec.variant,
        tuple((s.source, s.error_bars, s.error_column) for s in spec.series),
        spec.x_axis.scale,
        spec.y_axis.scale,
        tuple((o.analysis_id, o.show_band) for o in spec.overlays),
    )


class MixedSpinBox(QDoubleSpinBox):
    """A number field that can show "Mixed": its value is one step below the range."""

    def __init__(self, low: float, high: float, step: float, suffix: str = "") -> None:
        super().__init__()
        self._low = low
        self.setDecimals(2)
        self.setSingleStep(step)
        self.setRange(low - step, high)
        self.setSuffix(suffix)
        self.setSpecialValueText(MIXED)
        self.setMaximumWidth(FIELD_WIDTH)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)

    def set_mixed(self) -> None:
        self.setValue(self.minimum())

    @property
    def is_mixed(self) -> bool:
        return self.value() < self._low


class MixedCombo(QComboBox):
    """A combo box of values that shows "Mixed" when the selection has several.

    The values are kept here, not as Qt item data, so enums and strings compare as
    Python values."""

    def __init__(self) -> None:
        super().__init__()
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._values: list[object] = []

    def add_choice(self, text: str, value: object, icon: QIcon | None = None) -> None:
        self._values.append(value)
        if icon is None:
            self.addItem(text, len(self._values) - 1)
        else:
            self.addItem(icon, text, len(self._values) - 1)

    def clear_choices(self) -> None:
        self._values.clear()
        self.clear()

    def index_of(self, value: object) -> int:
        for row in range(self.count()):
            data = self.itemData(row)
            if isinstance(data, int) and data >= 0 and self._values[data] == value:
                return row
        return -1

    def set_value(self, value: object, *, mixed: bool = False) -> None:
        with QSignalBlocker(self):
            index = self.findData(-1)
            if index >= 0:
                self.removeItem(index)
            if mixed:
                self.insertItem(0, MIXED, -1)
                self.setCurrentIndex(0)
                return
            self.setCurrentIndex(max(self.index_of(value), 0))

    def value(self) -> object:
        data = self.currentData()
        return self._values[data] if isinstance(data, int) and data >= 0 else None


class FormatDialog(QDialog):
    """Format the graph ``graph_id`` of ``document``.

    ``results`` are the analysis results by id (for fitted curves in the preview) and
    ``data`` the graph's current plot data, if the caller has it. With a ``scheduler``
    the preview's plot data is recomputed off the UI thread.
    """

    def __init__(
        self,
        document: Document,
        graph_id: str,
        *,
        scheduler: Scheduler | None = None,
        results: Mapping[str, Result] | None = None,
        data: ChartData | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.document = document
        self.original: ChartSpec = document.graph(graph_id)
        self.spec: ChartSpec = self.original
        self.table: Table = document.table(self.original.table_id)
        self._scheduler = scheduler
        self._results: Mapping[str, Result] = results or {}
        self._data = data
        self._data_key = data_key(self.spec) if data is not None else None
        self._node = f"{PREVIEW_NODE}:{graph_id}"
        self._pending_key: object = None
        self._problems: dict[str, str] = {}
        self._names = {series_source(s): s.name for s in series(self.table)}
        self.setWindowTitle("Format graph")
        self._build()
        size_workbench(self)
        if scheduler is not None:
            scheduler.finished.connect(self._on_job_finished)
        self._load()
        self.set_section(GRAPH)
        self.title_edit.setFocus()
        self._refresh_preview()

    # --- building --------------------------------------------------------------------------

    def _build(self) -> None:
        self.skeleton = DialogSkeleton(self, "Apply")
        body = self.skeleton.body
        self.tabs = choice_tabs(self, "Format section")
        for name in SECTIONS:
            self.tabs.addTab(name)
        self.description = secondary_label(parent=self)
        body.addWidget(self.tabs)
        body.addWidget(self.description)
        left, right = columns(body)
        self.left_stack = QStackedWidget()
        self.left_stack.addWidget(self._build_graph())
        self.left_stack.addWidget(self._build_axes_list())
        self.left_stack.addWidget(self._build_series_list())
        self.left_stack.addWidget(self._build_colors())
        left.addWidget(self.left_stack, 1)
        self.options_box = QGroupBox("Options")
        self.options_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        options_layout = QVBoxLayout(self.options_box)
        self.options = FittedStack()
        self.options.addWidget(self._build_axis_options())
        self.options.addWidget(self._build_series_options())
        options_layout.addWidget(self.options)
        right.addWidget(self.options_box)
        right.addWidget(self._build_preview(), 1)
        self.tabs.currentChanged.connect(self._on_section_changed)

    def _build_graph(self) -> QGroupBox:
        box = QGroupBox("Graph")
        outer = QVBoxLayout(box)
        form = form_layout()
        self.title_edit = QLineEdit()
        form.addRow(buddy_label("&Title", self.title_edit), self.title_edit)
        row = QHBoxLayout()
        row.setSpacing(0)
        self.aspect_group = QButtonGroup(self)
        self.aspect_buttons: dict[str, QPushButton] = {}
        texts = {"tall": "Ta&ll", "wide": "&Wide", "square": "S&quare", CUSTOM_ASPECT: "&Custom"}
        for key in (*ASPECT_PRESETS, CUSTOM_ASPECT):
            button = QPushButton(texts[key])
            button.setCheckable(True)
            button.setAccessibleName(f"Size: {ASPECT_LABELS[key]}")
            if key in ASPECT_PRESETS:
                width, height = ASPECT_PRESETS[key]
                button.setToolTip(f"{width:g} × {height:g} cm")
            self.aspect_group.addButton(button)
            self.aspect_buttons[key] = button
            row.addWidget(button)
        row.addStretch(1)
        form.addRow(QLabel("Page"), row)
        size_row = QHBoxLayout()
        self.width_spin = QDoubleSpinBox()
        self.height_spin = QDoubleSpinBox()
        for spin, name in ((self.width_spin, "Width"), (self.height_spin, "Height")):
            spin.setRange(*SIZE_RANGE)
            spin.setDecimals(1)
            spin.setSingleStep(0.5)
            spin.setSuffix(" cm")
            spin.setMaximumWidth(FIELD_WIDTH)
            spin.setAccessibleName(f"{name} in cm")
        size_row.addWidget(self.width_spin)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self.height_spin)
        size_row.addStretch(1)
        form.addRow(buddy_label("Si&ze", self.width_spin), size_row)
        self.show_legend = QCheckBox("Show le&gend")
        form.addRow("", self.show_legend)
        outer.addLayout(form)
        outer.addStretch(1)
        self.title_edit.textEdited.connect(lambda text: self._edit(title=text))
        self.aspect_group.buttonClicked.connect(self._on_aspect_clicked)
        self.width_spin.valueChanged.connect(lambda _v: self._on_size_changed())
        self.height_spin.valueChanged.connect(lambda _v: self._on_size_changed())
        self.show_legend.toggled.connect(lambda on: self._edit(show_legend=on))
        return box

    def _build_axes_list(self) -> QGroupBox:
        box = QGroupBox("Axes")
        layout = QVBoxLayout(box)
        self.axes_list = QListWidget()
        self.axes_list.setAccessibleName("Axes")
        for name in ("X axis", "Y axis"):
            self.axes_list.addItem(name)
        self.axes_list.setCurrentRow(0)
        layout.addWidget(self.axes_list, 1)
        self.axes_list.currentRowChanged.connect(lambda _r: self._load_axis())
        return box

    def _build_axis_options(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        form = form_layout()
        self.axis_title = QLineEdit()
        form.addRow(buddy_label("&Title", self.axis_title), self.axis_title)
        range_row = QHBoxLayout()
        self.axis_from = QLineEdit()
        self.axis_to = QLineEdit()
        for edit, name in ((self.axis_from, "From"), (self.axis_to, "To")):
            edit.setPlaceholderText(AUTO)
            edit.setMaximumWidth(FIELD_WIDTH)
            edit.setAccessibleName(f"Range {name.lower()}")
        range_row.addWidget(self.axis_from)
        range_row.addWidget(buddy_label("t&o", self.axis_to))
        range_row.addWidget(self.axis_to)
        range_row.addStretch(1)
        form.addRow(buddy_label("&From", self.axis_from), range_row)
        self.axis_labels = QCheckBox("Show la&bels")
        form.addRow("", self.axis_labels)
        self.label_angle = QComboBox()
        self.label_angle.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        for angle in LabelAngle:
            self.label_angle.addItem(angle.label, angle.value)
        self.label_angle.setToolTip("Auto turns the labels when they do not fit side by side")
        form.addRow(buddy_label("Label a&ngle", self.label_angle), self.label_angle)
        scale_row = QHBoxLayout()
        self.linear = QRadioButton("L&inear")
        self.log = QRadioButton("Lo&g")
        self.scale_group = QButtonGroup(self)
        self.scale_group.addButton(self.linear)
        self.scale_group.addButton(self.log)
        scale_row.addWidget(self.linear)
        scale_row.addWidget(self.log)
        scale_row.addStretch(1)
        form.addRow(QLabel("Scale"), scale_row)
        layout.addLayout(form)
        self.axis_note = secondary_label()
        layout.addWidget(self.axis_note)
        self.axis_title.textEdited.connect(lambda _t: self._edit_axis())
        self.axis_from.textEdited.connect(lambda _t: self._edit_axis())
        self.axis_to.textEdited.connect(lambda _t: self._edit_axis())
        self.axis_labels.toggled.connect(lambda _on: self._edit_axis())
        self.label_angle.activated.connect(lambda _i: self._edit_axis())
        self.linear.toggled.connect(lambda _on: self._edit_axis())
        return page

    def _build_series_list(self) -> QGroupBox:
        box = QGroupBox("Series")
        layout = QVBoxLayout(box)
        self.series_list = QListWidget()
        self.series_list.setAccessibleName("Series")
        self.series_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.series_list, 1)
        self.selection_note = secondary_label()
        layout.addWidget(self.selection_note)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("&Add…")
        self.add_menu = QMenu(self.add_button)
        self.add_button.setMenu(self.add_menu)
        self.remove_button = QPushButton("&Remove")
        self.up_button = QPushButton("Move &up")
        self.down_button = QPushButton("Move &down")
        for button in (self.add_button, self.remove_button, self.up_button, self.down_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.series_list.itemSelectionChanged.connect(self._load_series_options)
        self.add_menu.aboutToShow.connect(self._fill_add_menu)
        self.remove_button.clicked.connect(self.remove_selected)
        self.up_button.clicked.connect(lambda: self.move_selected(-1))
        self.down_button.clicked.connect(lambda: self.move_selected(1))
        for keys, by in (("Alt+Up", -1), ("Alt+Down", 1)):
            shortcut = QShortcut(QKeySequence(keys), self.series_list)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(lambda by=by: self.move_selected(by))
        return box

    def _build_series_options(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        form = form_layout()
        self.in_legend = QCheckBox("Show in le&gend")
        form.addRow("", self.in_legend)
        symbol_row = QHBoxLayout()
        self.symbol = MixedCombo()
        for symbol, text in SYMBOL_LABELS.items():
            self.symbol.add_choice(text, symbol)
        self.symbol_size = MixedSpinBox(1.0, 30.0, 0.5, " pt")
        symbol_row.addWidget(self.symbol)
        symbol_row.addWidget(buddy_label("Si&ze", self.symbol_size))
        symbol_row.addWidget(self.symbol_size)
        symbol_row.addStretch(1)
        form.addRow(buddy_label("S&ymbol", self.symbol), symbol_row)
        line_row = QHBoxLayout()
        self.line_style = MixedCombo()
        for style, text in LINE_LABELS.items():
            self.line_style.add_choice(text, style)
        self.line_width = MixedSpinBox(0.0, 10.0, 0.25, " pt")
        line_row.addWidget(self.line_style)
        line_row.addWidget(buddy_label("&Width", self.line_width))
        line_row.addWidget(self.line_width)
        line_row.addStretch(1)
        form.addRow(buddy_label("&Line style", self.line_style), line_row)
        self.color = MixedCombo()
        form.addRow(buddy_label("C&olor", self.color), self.color)
        self.error_bars = MixedCombo()
        form.addRow(buddy_label("&Error bars", self.error_bars), self.error_bars)
        layout.addLayout(form)
        self.series_note = secondary_label()
        layout.addWidget(self.series_note)
        self.in_legend.clicked.connect(lambda on: self._edit_series(in_legend=bool(on)))
        self.symbol.activated.connect(lambda _i: self._edit_series(symbol=self.symbol.value()))
        self.symbol_size.valueChanged.connect(self._on_size_value)
        self.line_style.activated.connect(
            lambda _i: self._edit_series(line_style=self.line_style.value())
        )
        self.line_width.valueChanged.connect(self._on_width_value)
        self.color.activated.connect(self._on_color_chosen)
        self.error_bars.activated.connect(self._on_error_bars_chosen)
        return page

    def _build_colors(self) -> QGroupBox:
        box = QGroupBox("Colors and text")
        outer = QVBoxLayout(box)
        form = form_layout()
        self.scheme_combo = QComboBox()
        for scheme_id, found in SCHEMES.items():
            self.scheme_combo.addItem(found.name, scheme_id)
        form.addRow(buddy_label("Color sc&heme", self.scheme_combo), self.scheme_combo)
        self.text_size = QDoubleSpinBox()
        self.text_size.setRange(MIN_TEXT_SIZE, MAX_TEXT_SIZE)
        self.text_size.setDecimals(1)
        self.text_size.setSingleStep(0.5)
        self.text_size.setSuffix(" pt")
        self.text_size.setMaximumWidth(FIELD_WIDTH)
        form.addRow(buddy_label("Chart text si&ze", self.text_size), self.text_size)
        outer.addLayout(form)
        outer.addWidget(
            secondary_label("More color schemes and a palette editor come in a later version.")
        )
        outer.addStretch(1)
        self.scheme_combo.activated.connect(lambda _i: self._on_scheme_chosen())
        self.text_size.valueChanged.connect(lambda v: self._edit(text_size=float(v)))
        return box

    def _build_preview(self) -> QGroupBox:
        from curvis.charts.render_qt.chart_view import ChartView

        box = QGroupBox("Preview")
        layout = QVBoxLayout(box)
        self.preview: ChartView = ChartView()
        self.preview.setAccessibleName("Preview of the graph")
        self.preview.setMinimumHeight(100)
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.preview, 1)
        return box

    # --- sections --------------------------------------------------------------------------

    def set_section(self, section: int) -> None:
        self.tabs.setCurrentIndex(section)
        self._on_section_changed(section)

    def _on_section_changed(self, section: int) -> None:
        self.description.setText(DESCRIPTIONS[section])
        self.left_stack.setCurrentIndex(section)
        has_options = section in (AXES, SERIES)
        self.options_box.setVisible(has_options)
        if section == AXES:
            self.options.setCurrentIndex(0)
            self._load_axis()
        elif section == SERIES:
            self.options.setCurrentIndex(1)
            self._load_series_options()
        self._set_focus_order(section)

    def _set_focus_order(self, section: int) -> None:
        widgets: list[QWidget] = [self.tabs]
        if section == GRAPH:
            widgets += [
                self.title_edit,
                *self.aspect_buttons.values(),
                self.width_spin,
                self.height_spin,
                self.show_legend,
            ]
        elif section == AXES:
            widgets += [
                self.axes_list,
                self.axis_title,
                self.axis_from,
                self.axis_to,
                self.axis_labels,
                self.label_angle,
                self.linear,
                self.log,
            ]
        elif section == SERIES:
            widgets += [
                self.series_list,
                self.add_button,
                self.remove_button,
                self.up_button,
                self.down_button,
                self.in_legend,
                self.symbol,
                self.symbol_size,
                self.line_style,
                self.line_width,
                self.color,
                self.error_bars,
            ]
        else:
            widgets += [self.scheme_combo, self.text_size]
        assert self.skeleton.ok_button is not None
        widgets.append(self.skeleton.ok_button)
        set_tab_order(widgets)

    # --- loading the spec into the fields ---------------------------------------------------

    def _load(self) -> None:
        spec = self.spec
        with QSignalBlocker(self.title_edit):
            if self.title_edit.text() != spec.title:
                self.title_edit.setText(spec.title)
        self.aspect_buttons[spec.aspect_ratio].setChecked(True)
        width, height = spec.page_size_cm
        for spin, value in ((self.width_spin, width), (self.height_spin, height)):
            with QSignalBlocker(spin):
                spin.setValue(value)
            spin.setEnabled(spec.aspect_ratio == CUSTOM_ASPECT)
        with QSignalBlocker(self.show_legend):
            self.show_legend.setChecked(spec.show_legend)
        with QSignalBlocker(self.scheme_combo):
            self.scheme_combo.setCurrentIndex(max(0, self.scheme_combo.findData(spec.color_scheme)))
        with QSignalBlocker(self.text_size):
            self.text_size.setValue(spec.text_size)
        self._fill_series_list()

    def _current_axis(self) -> tuple[str, AxisSpec]:
        if self.axes_list.currentRow() == 1:
            return "y_axis", self.spec.y_axis
        return "x_axis", self.spec.x_axis

    def _load_axis(self) -> None:
        key, axis = self._current_axis()
        with QSignalBlocker(self.axis_title):
            self.axis_title.setText(axis.title)
        for edit, value in ((self.axis_from, axis.minimum), (self.axis_to, axis.maximum)):
            with QSignalBlocker(edit):
                edit.setText(_format_limit(value))
        with QSignalBlocker(self.axis_labels):
            self.axis_labels.setChecked(axis.show_labels)
        with QSignalBlocker(self.label_angle):
            self.label_angle.setCurrentIndex(
                max(0, self.label_angle.findData(axis.label_angle.value))
            )
        for button, checked in (
            (self.linear, axis.scale is Scale.LINEAR),
            (self.log, axis.scale is Scale.LOG),
        ):
            with QSignalBlocker(button):
                button.setChecked(checked)
        self._problems.pop("axis", None)
        self._update_axis_enabled(key)
        self._show_problem()

    def _update_axis_enabled(self, key: str) -> None:
        categorical_x = self.spec.family is ChartFamily.BAR and key == "x_axis"
        bars_y = self.spec.variant is ChartVariant.BAR and key == "y_axis"
        for widget in (self.axis_from, self.axis_to, self.linear, self.log):
            widget.setEnabled(not categorical_x)
        self.log.setEnabled(not (categorical_x or bars_y))
        self.label_angle.setEnabled(key == "x_axis")
        notes = [CATEGORY_AXIS_NOTE if categorical_x else BAR_LOG_NOTE if bars_y else ""]
        if key == "y_axis":
            notes.append(Y_ANGLE_NOTE)
        note = " ".join(n for n in notes if n)
        self.axis_note.setText(note)
        self.axis_note.setVisible(bool(note))

    def _fill_series_list(self, selected: Sequence[str] | None = None) -> None:
        keep = list(selected) if selected is not None else self.selected_sources()
        with QSignalBlocker(self.series_list):
            self.series_list.clear()
            for s in self.spec.series:
                item = QListWidgetItem(_swatch(s.color), self._names.get(s.source, s.source))
                item.setData(Qt.ItemDataRole.UserRole, s.source)
                self.series_list.addItem(item)
                item.setSelected(s.source in keep)
            for overlay in self.spec.overlays:
                key = FIT_PREFIX + overlay.analysis_id
                item = QListWidgetItem(f"Fitted curve: {self._fit_name(overlay)}")
                item.setData(Qt.ItemDataRole.UserRole, key)
                self.series_list.addItem(item)
                item.setSelected(key in keep)
            if not self.series_list.selectedItems() and self.series_list.count():
                self.series_list.item(0).setSelected(True)
                self.series_list.setCurrentRow(0)
        self._load_series_options()

    def _fit_name(self, overlay: FitOverlay) -> str:
        try:
            return self.document.analysis(overlay.analysis_id).analysis_type.name
        except KeyError:
            return "analysis no longer in the project"

    def selected_fits(self) -> list[str]:
        """Analysis ids of the selected fitted curves, in the graph's order."""
        chosen = {item.data(Qt.ItemDataRole.UserRole) for item in self.series_list.selectedItems()}
        return [o.analysis_id for o in self.spec.overlays if FIT_PREFIX + o.analysis_id in chosen]

    def selected_sources(self) -> list[str]:
        """Sources of the selected series, in the graph's order."""
        chosen = {item.data(Qt.ItemDataRole.UserRole) for item in self.series_list.selectedItems()}
        return [s.source for s in self.spec.series if s.source in chosen]

    def select_series(self, sources: Sequence[str]) -> None:
        with QSignalBlocker(self.series_list):
            for i in range(self.series_list.count()):
                item = self.series_list.item(i)
                item.setSelected(item.data(Qt.ItemDataRole.UserRole) in sources)
        self._load_series_options()

    def _selected_styles(self) -> list[SeriesSpec]:
        chosen = set(self.selected_sources())
        return [s for s in self.spec.series if s.source in chosen]

    def _load_series_options(self) -> None:
        styles = self._selected_styles()
        n = len(styles)
        fits = len(self.selected_fits())
        parts = [
            f"{n} series" if n else "",
            f"{fits} fitted curve{'s' if fits > 1 else ''}" if fits else "",
        ]
        picked = " and ".join(p for p in parts if p)
        self.selection_note.setText(f"{picked} selected." if picked else "No series selected.")
        widgets: list[QWidget] = [
            self.in_legend,
            self.symbol,
            self.symbol_size,
            self.line_style,
            self.line_width,
            self.color,
            self.error_bars,
        ]
        for widget in widgets:
            widget.setEnabled(n > 0)
        self._update_series_buttons()
        if not styles:
            self.series_note.setText(FIT_NOTE if fits else "")
            self.series_note.setVisible(bool(fits))
            return

        def common(get: Callable[[SeriesSpec], Any]) -> tuple[Any, bool]:
            values = {get(s) for s in styles}
            return next(iter(values)), len(values) > 1

        legend, mixed = common(lambda s: s.in_legend)
        with QSignalBlocker(self.in_legend):
            self.in_legend.setTristate(mixed)
            self.in_legend.setCheckState(
                Qt.CheckState.PartiallyChecked
                if mixed
                else Qt.CheckState.Checked
                if legend
                else Qt.CheckState.Unchecked
            )
        value, mixed = common(lambda s: s.symbol)
        self.symbol.set_value(value, mixed=mixed)
        value, mixed = common(lambda s: s.line_style)
        self.line_style.set_value(value, mixed=mixed)
        for spin, get in (
            (self.symbol_size, lambda s: s.size),
            (self.line_width, lambda s: s.line_width),
        ):
            value, mixed = common(get)
            with QSignalBlocker(spin):
                if mixed:
                    spin.set_mixed()
                else:
                    spin.setValue(value)
        self._fill_colors(styles)
        self._fill_error_bars(styles)
        category = self.spec.family is ChartFamily.BAR
        box = self.spec.variant is ChartVariant.BOX
        for widget in (self.symbol, self.symbol_size, self.line_style, self.line_width):
            widget.setEnabled(not category)
        self.error_bars.setEnabled(not box)
        note = BOX_NOTE if box else CATEGORY_NOTE if category else ""
        self.series_note.setText(note)
        self.series_note.setVisible(bool(note))

    def _fill_colors(self, styles: Sequence[SeriesSpec]) -> None:
        colors = scheme(self.spec.color_scheme).series
        current = {s.color for s in styles}
        with QSignalBlocker(self.color):
            self.color.clear_choices()
            for color in dict.fromkeys([*colors, *sorted(current)]):
                self.color.add_choice(color, color, _swatch(color))
            self.color.add_choice(OTHER_COLOR, OTHER_COLOR)
        self.color.set_value(next(iter(current)), mixed=len(current) > 1)

    def _error_choices(self, styles: Sequence[SeriesSpec]) -> list[ErrorBars]:
        choices = [ErrorBars.NONE, ErrorBars.SD, ErrorBars.SEM]
        if self.spec.family is ChartFamily.XY and all(
            self._error_column(s.source) is not None for s in styles
        ):
            choices.append(ErrorBars.ERROR_COLUMN)
        return choices

    def _error_column(self, source: str) -> str | None:
        if not any(c.id == source for c in self.table.columns):
            return None
        column = error_column_of(self.table, source)
        return column.id if column is not None else None

    def _fill_error_bars(self, styles: Sequence[SeriesSpec]) -> None:
        with QSignalBlocker(self.error_bars):
            self.error_bars.clear_choices()
            for choice in self._error_choices(styles):
                label = "Error column" if choice is ErrorBars.ERROR_COLUMN else choice.label
                self.error_bars.add_choice(label, choice)
        values = {s.error_bars for s in styles}
        self.error_bars.set_value(next(iter(values)), mixed=len(values) > 1)

    def _update_series_buttons(self) -> None:
        selected = self.selected_sources()
        order = [s.source for s in self.spec.series]
        missing = self._missing_sources()
        self.add_button.setEnabled(bool(missing))
        self.add_button.setToolTip("" if missing else EVERY_SERIES_SHOWN)
        fits = self.selected_fits()
        removable = bool(fits) or (bool(selected) and len(selected) < len(order))
        self.remove_button.setEnabled(removable)
        self.remove_button.setToolTip("" if removable or not selected else LAST_SERIES)
        indexes = [order.index(s) for s in selected]
        self.up_button.setEnabled(bool(indexes) and min(indexes) > 0)
        self.down_button.setEnabled(bool(indexes) and max(indexes) < len(order) - 1)

    # --- editing -------------------------------------------------------------------------

    def _apply(self, spec: ChartSpec) -> None:
        self.spec = spec
        self._refresh_preview()
        self._show_problem()

    def _edit(self, **changes: Any) -> None:
        try:
            spec = replace(self.spec, **changes)
        except ValueError as exc:
            self._problems["graph"] = f"{str(exc).capitalize()}."
            self._show_problem()
            return
        self._problems.pop("graph", None)
        self._apply(spec)

    def _on_aspect_clicked(self, button: QPushButton) -> None:
        key = next(k for k, b in self.aspect_buttons.items() if b is button)
        custom = key == CUSTOM_ASPECT
        if custom:
            size = self.spec.page_size_cm
            self._edit(aspect_ratio=key, custom_size=size)
        else:
            self._edit(aspect_ratio=key, custom_size=None)
        self._load()

    def _on_size_changed(self) -> None:
        if self.spec.aspect_ratio != CUSTOM_ASPECT:
            return
        self._edit(custom_size=(self.width_spin.value(), self.height_spin.value()))

    def _edit_axis(self) -> None:
        key, axis = self._current_axis()
        try:
            minimum = _number(self.axis_from.text())
            maximum = _number(self.axis_to.text())
        except ValueError:
            self._problems["axis"] = "Enter a number for the range, or leave it empty for Auto."
            self._show_problem()
            return
        scale = Scale.LOG if self.log.isChecked() else Scale.LINEAR
        problem: Problem = None
        if minimum is not None and maximum is not None and minimum >= maximum:
            problem = "Enter a From value below the To value."
        elif scale is Scale.LOG and minimum is not None and minimum <= 0:
            problem = "A log axis must start above 0. Enter a From value above 0."
        if problem is not None:
            self._problems["axis"] = problem
            self._show_problem()
            return
        self._problems.pop("axis", None)
        new = replace(
            axis,
            title=self.axis_title.text(),
            minimum=minimum,
            maximum=maximum,
            show_labels=self.axis_labels.isChecked(),
            scale=scale,
            label_angle=LabelAngle(str(self.label_angle.currentData())),
        )
        if key == "x_axis":
            self._apply(replace(self.spec, x_axis=new))
        else:
            self._apply(replace(self.spec, y_axis=new))

    def _edit_series(self, **changes: Any) -> None:
        if any(v is None for v in changes.values()):
            return  # "Mixed" chosen again: nothing changes
        chosen = set(self.selected_sources())
        styles = tuple(replace(s, **changes) if s.source in chosen else s for s in self.spec.series)
        self._apply(replace(self.spec, series=styles))
        if "color" in changes:
            self._fill_series_list(list(chosen))
        else:
            self._load_series_options()

    def _on_size_value(self, value: float) -> None:
        if not self.symbol_size.is_mixed:
            self._edit_series(size=float(value))

    def _on_width_value(self, value: float) -> None:
        if not self.line_width.is_mixed:
            self._edit_series(line_width=float(value))

    def _on_color_chosen(self, _index: int) -> None:
        value = self.color.value()
        if value == OTHER_COLOR:
            styles = self._selected_styles()
            start = QColor(styles[0].color) if styles else QColor("#000000")
            chosen = self._ask_color(start)
            if chosen is None:
                self._fill_colors(styles)
                return
            value = chosen
        if value in (None, MIXED):
            return
        self._edit_series(color=str(value))

    def _ask_color(self, start: QColor) -> str | None:
        """Ask for a colour (tests replace this)."""
        color = QColorDialog.getColor(start, self, "Series color")
        return color.name() if color.isValid() else None

    def _on_error_bars_chosen(self, _index: int) -> None:
        value = self.error_bars.value()
        if not isinstance(value, ErrorBars):
            return
        chosen = set(self.selected_sources())
        styles = []
        for s in self.spec.series:
            if s.source in chosen:
                column = self._error_column(s.source) if value is ErrorBars.ERROR_COLUMN else None
                s = replace(s, error_bars=value, error_column=column)
            styles.append(s)
        self._apply(replace(self.spec, series=tuple(styles)))
        self._load_series_options()

    def _on_scheme_chosen(self) -> None:
        scheme_id = str(self.scheme_combo.currentData())
        colors = scheme(scheme_id)
        styles = tuple(
            replace(s, color=colors.series_color(i)) for i, s in enumerate(self.spec.series)
        )
        self._apply(replace(self.spec, color_scheme=scheme_id, series=styles))
        self._fill_series_list()

    # --- series: add, remove, order ---------------------------------------------------------

    def _missing_sources(self) -> list[str]:
        shown = {s.source for s in self.spec.series}
        return [source for source in self._names if source not in shown]

    def _fill_add_menu(self) -> None:
        self.add_menu.clear()
        for source in self._missing_sources():
            action = self.add_menu.addAction(self._names[source])
            action.triggered.connect(lambda _c=False, source=source: self.add_series(source))

    def add_series(self, source: str) -> None:
        """Add the table's series ``source`` at the end, in the next colour and symbol."""
        defaults = {s.source: s for s in default_series(self.table)}
        style = defaults[source]
        index = len(self.spec.series)
        style = replace(
            style,
            color=scheme(self.spec.color_scheme).series_color(index),
            line_style=self.spec.series[-1].line_style if self.spec.series else style.line_style,
        )
        if self.spec.family is ChartFamily.BAR and style.error_bars is ErrorBars.ERROR_COLUMN:
            style = replace(style, error_bars=ErrorBars.SD, error_column=None)
        self._apply(replace(self.spec, series=(*self.spec.series, style)))
        self._fill_series_list([source])

    def remove_selected(self) -> None:
        """Remove the selected series (a graph keeps at least one) and fitted curves."""
        chosen = set(self.selected_sources())
        fits = set(self.selected_fits())
        kept = tuple(s for s in self.spec.series if s.source not in chosen)
        if not kept:
            if not fits:
                return
            kept, chosen = self.spec.series, set()
        if not chosen and not fits:
            return
        overlays = tuple(o for o in self.spec.overlays if o.analysis_id not in fits)
        self._apply(replace(self.spec, series=kept, overlays=overlays))
        self._fill_series_list([kept[0].source])

    def move_selected(self, by: int) -> None:
        """Move the selected series one place up (-1) or down (+1)."""
        chosen = set(self.selected_sources())
        order = list(self.spec.series)
        if not chosen:
            return
        indexes = [i for i, s in enumerate(order) if s.source in chosen]
        if (by < 0 and indexes[0] == 0) or (by > 0 and indexes[-1] == len(order) - 1):
            return
        for i in indexes if by < 0 else reversed(indexes):
            order[i], order[i + by] = order[i + by], order[i]
        self._apply(replace(self.spec, series=tuple(order)))
        self._fill_series_list(list(chosen))

    # --- problems and preview ------------------------------------------------------------

    def _show_problem(self) -> None:
        problem = next(iter(self._problems.values()), None)
        self.skeleton.set_problem(problem)

    def _refresh_preview(self) -> None:
        """Redraw the preview; recompute the plot data first if the spec needs it."""
        key = data_key(self.spec)
        if self._data is not None and key == self._data_key:
            self.preview.render_chart(self.spec, self._data)
            return
        inputs = gather(self.table, self.spec, self._results)
        self._pending_key = key
        if self._scheduler is None:
            self._set_data(compute(inputs), key)
            return
        from curvis.compute import Job

        self.preview.render_chart(self.spec, self._data if self._data_key == key else None)
        self._scheduler.submit(self._node, Job(compute, (inputs,)), delay_ms=0)

    def _on_job_finished(self, node: str, result: object) -> None:
        if node != self._node or not isinstance(result, ChartData):
            return
        self._set_data(result, self._pending_key)

    def _set_data(self, data: ChartData, key: object) -> None:
        self._data, self._data_key = data, key
        self.preview.render_chart(self.spec, data if key == data_key(self.spec) else None)

    @property
    def changed(self) -> bool:
        return self.spec != self.original

    def done(self, result: int) -> None:
        if self._scheduler is not None:
            self._scheduler.cancel(self._node)
            self._scheduler.finished.disconnect(self._on_job_finished)
        super().done(result)

    def preview_ready(self) -> bool:
        """True when the preview shows the current spec's plot data (for tests)."""
        return self._data is not None and self._data_key == data_key(self.spec)


__all__ = ("FormatDialog", "data_key")
