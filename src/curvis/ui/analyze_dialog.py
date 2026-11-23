"""The Analyze dialog: pick an analysis, its data and its options, then run it.

A workbench dialog (docs/DESIGN.md, section 11; mockup in docs/mockups/analyze-dialog.html):

- the families as tabs, only those the table supports, opening on the one that best
  fits the data (:func:`curvis.analysis.base.opening_family`);
- on the left the data (the series to analyze, with their number of values) and under it
  the options of the chosen analysis, with a collapsed Advanced section;
- on the right the analyses of the family that the table supports, as one list grouped in
  the sections of the analysis types with the recently used ones on top and a filter
  field, and *About this analysis*: the equation and a sketch of the model (fits) or the
  null hypothesis and assumptions (tests), the sentence that says what will run, and when
  to use it.

All the text comes from the analysis types (:attr:`AnalysisType.about`,
:attr:`CurveFit.equation`); the dialog only lays it out. The dialog builds an
:class:`~curvis.analysis.base.Analysis`; the caller stores it with
:func:`~curvis.core.add_analysis` or :func:`~curvis.core.edit_analysis`.
"""

from __future__ import annotations

import dataclasses
import html
from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from curvis.analysis.base import (
    Analysis,
    AnalysisError,
    AnalysisType,
    Family,
    Params,
    available_families,
    check_param,
    opening_family,
    param_fields,
    rows_match_x,
    runnable,
)
from curvis.core import Document
from curvis.data import ColumnRole, Table
from curvis.ui.analyze_options import FixedTable, XRangeEditor
from curvis.ui.dialogs import (
    FIELD_WIDTH,
    CollapsibleSection,
    DialogSkeleton,
    EmptyState,
    FittedStack,
    buddy_label,
    choice_tabs,
    columns,
    form_layout,
    secondary_label,
    set_tab_order,
    size_workbench,
)
from curvis.ui.sketch_view import SketchView

_ID = Qt.ItemDataRole.UserRole
_N_COLUMN = 1
RECENT = "Recent"
RECENT_MAX = 3
COMBO_CHARACTERS = 14
"""Characters an option's drop-down list shows at least; its popup shows them all."""
LIST_ROWS = 6
"""The analysis list is about this many rows high, then scrolls."""


def _curve_fit(kind: AnalysisType) -> Any:
    """``kind`` if it is a curve fit (which has an equation and a sketch), else None."""
    from curvis.analysis.fit.analyses import CurveFit

    return kind if isinstance(kind, CurveFit) else None


def _children(item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
    children = (item.child(i) for i in range(item.childCount()))
    return [c for c in children if c is not None]


class ParamsForm(QWidget):
    """The options of one analysis type: a form generated from its parameters dataclass.

    Parameters marked ``advanced`` go in a collapsed Advanced section at the bottom. A
    parameter picked ``from_inputs`` is a list of the ticked series
    (:meth:`set_inputs`), and one with ``enabled_when`` is enabled only while the other
    parameter has the given value. A parameter with an ``editor`` gets its own widget: the
    X range of a fit two number fields (last in the main form), its fixed parameters a
    table.
    """

    changed = Signal()

    def __init__(self, analysis_type: AnalysisType, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.analysis_type = analysis_type
        self.fields: dict[str, QWidget] = {}
        self.labels: dict[str, QLabel] = {}
        self.infos = {f.name: info for f, info in param_fields(analysis_type.params_type)}
        self._wanted: dict[str, str] = {}  # value of each from_inputs field, kept while unticked
        self.x_range: XRangeEditor | None = None
        self.fixed: FixedTable | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        main = form_layout()
        layout.addLayout(main)
        self.advanced: CollapsibleSection | None = None
        advanced_form = form_layout()
        defaults = analysis_type.default_params()
        for f, info in param_fields(analysis_type.params_type):
            if info.editor == "x_range":
                self.x_range = XRangeEditor(info.label)
                self.x_range.changed.connect(self.changed)
                self.fields[f.name] = self.x_range
                continue
            if info.editor == "fixed":
                self.fixed = self._fixed_table(defaults)
                self.fields[f.name] = self.fixed
                (advanced_form if info.advanced else main).addRow(self.fixed)
                continue
            widget = self._field_widget(f, info.label, info.choices, info.minimum, info.maximum)
            if info.from_inputs:
                assert isinstance(widget, QComboBox)
                widget.currentIndexChanged.connect(lambda _i, n=f.name: self._remember(n))
            self.fields[f.name] = widget
            form = advanced_form if info.advanced else main
            if isinstance(widget, QCheckBox):
                form.addRow(widget)
            else:
                label = buddy_label(info.label, widget)
                self.labels[f.name] = label
                form.addRow(label, widget)
        if self.x_range is not None:
            main.addRow(self.x_range)  # directly under the log check box (DESIGN.md)
        for f, _info in param_fields(analysis_type.params_type):
            self._set(f.name, getattr(defaults, f.name))
        self.changed.connect(self._update_enabled)
        self._update_enabled()
        if advanced_form.rowCount():
            self.advanced = CollapsibleSection(parent=self)
            self.advanced.content_layout.addLayout(advanced_form)
            layout.addWidget(self.advanced)
        if not main.rowCount() and self.advanced is None:
            main.addRow(secondary_label("This analysis has no options."))

    def _fixed_table(self, defaults: Params) -> FixedTable:
        fit = _curve_fit(self.analysis_type)
        table = FixedTable()
        table.setObjectName("param_fixed")
        if fit is not None:
            sketch = fit.sketch_for(defaults)
            table.set_parameters(fit.parameters(defaults), sketch.values if sketch else {})
        table.changed.connect(self.changed)
        return table

    def _field_widget(
        self,
        f: dataclasses.Field[Any],
        label: str,
        choices: Sequence[tuple[str, str]],
        minimum: float | None,
        maximum: float | None,
    ) -> QWidget:
        default = f.default
        widget: QWidget
        if self.infos[f.name].from_inputs:
            combo = QComboBox()
            combo.currentIndexChanged.connect(self.changed)
            widget = combo
        elif isinstance(default, bool):
            check = QCheckBox(label)
            check.toggled.connect(self.changed)
            widget = check
        elif choices:
            combo = QComboBox()
            for value, text in choices:
                combo.addItem(text, value)
            combo.currentIndexChanged.connect(self.changed)
            widget = combo
        elif isinstance(default, int):
            spin = QSpinBox()
            spin.setRange(int(minimum if minimum is not None else -(10**9)), int(maximum or 10**9))
            spin.setMaximumWidth(FIELD_WIDTH)
            spin.valueChanged.connect(self.changed)
            widget = spin
        elif isinstance(default, float):
            double = QDoubleSpinBox()
            double.setDecimals(4)
            double.setRange(
                minimum if minimum is not None else -1e12, maximum if maximum is not None else 1e12
            )
            double.setMaximumWidth(FIELD_WIDTH)
            double.valueChanged.connect(self.changed)
            widget = double
        else:
            edit = QLineEdit()
            edit.textChanged.connect(self.changed)
            widget = edit
        if isinstance(widget, QComboBox):
            # Long choices must not widen the left column: the combo shows what fits.
            widget.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            widget.setMinimumContentsLength(COMBO_CHARACTERS)
        widget.setObjectName(f"param_{f.name}")
        return widget

    def set_inputs(self, choices: Sequence[tuple[str, str]]) -> None:
        """The ticked series as (id, name), for the parameters picked from the inputs.
        Each keeps its value while that series is ticked, else shows the first one."""
        for name, info in self.infos.items():
            if not info.from_inputs:
                continue
            combo = self.fields[name]
            assert isinstance(combo, QComboBox)
            current = [combo.itemData(i) for i in range(combo.count())]
            if current == [value for value, _text in choices]:
                continue
            with QSignalBlocker(combo):
                combo.clear()
                for value, text in choices:
                    combo.addItem(text, value)
                combo.setCurrentIndex(max(0, combo.findData(self._wanted.get(name, ""))))

    def set_point_count(self, count: tuple[int, int] | None) -> None:
        if self.x_range is not None:
            self.x_range.set_count(count)

    def _remember(self, name: str) -> None:
        combo = self.fields[name]
        assert isinstance(combo, QComboBox)
        if combo.count():
            self._wanted[name] = combo.currentData()

    def _update_enabled(self) -> None:
        for name, info in self.infos.items():
            if info.enabled_when is None:
                continue
            other, value = info.enabled_when
            enabled = self._get(other) == value
            self.fields[name].setEnabled(enabled)
            if name in self.labels:
                self.labels[name].setEnabled(enabled)

    def _set(self, name: str, value: Any) -> None:
        widget = self.fields[name]
        if self.infos[name].from_inputs:
            self._wanted[name] = str(value)
        with QSignalBlocker(widget):
            if isinstance(widget, XRangeEditor):
                widget.set_text(str(value))
            elif isinstance(widget, FixedTable):
                widget.set_text(str(value), self._fixed_values(str(value)))
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QComboBox):
                widget.setCurrentIndex(max(0, widget.findData(value)))
            elif isinstance(widget, QSpinBox | QDoubleSpinBox):
                widget.setValue(value)
            elif isinstance(widget, QLineEdit):
                widget.setText(str(value))

    def _fixed_values(self, text: str) -> dict[str, float] | None:
        """The values of a ``fixed`` text, read by the analysis' own parser (None if it
        cannot be read)."""
        fit = _curve_fit(self.analysis_type)
        if fit is None:
            return None
        params: Any = self.analysis_type.default_params()
        try:
            return dict(fit.fixed_values(dataclasses.replace(params, fixed=text)))
        except AnalysisError:
            return None

    def _get(self, name: str) -> Any:
        widget = self.fields[name]
        if isinstance(widget, XRangeEditor | FixedTable):
            return widget.text()
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QComboBox):
            data = widget.currentData()
            return "" if data is None else data
        if isinstance(widget, QSpinBox | QDoubleSpinBox):
            return widget.value()
        assert isinstance(widget, QLineEdit)
        return widget.text()

    def set_params(self, params: Params) -> None:
        for f, _info in param_fields(self.analysis_type.params_type):
            self._set(f.name, getattr(params, f.name))
        self._update_enabled()

    def params(self) -> Params:
        values = {
            f.name: check_param(f, info, self._get(f.name))
            for f, info in param_fields(self.analysis_type.params_type)
        }
        return self.analysis_type.params_type(**values)

    def focus_order(self) -> list[QWidget]:
        widgets: list[QWidget] = []
        for f, info in param_fields(self.analysis_type.params_type):
            if info.advanced and self.advanced is not None and self.advanced.toggle not in widgets:
                widgets.append(self.advanced.toggle)
            if f.name == "x_range" and self.x_range is not None:
                continue  # last in the main form
            widgets.append(self.fields[f.name])
        if self.x_range is not None:
            position = widgets.index(self.advanced.toggle) if self.advanced else len(widgets)
            widgets[position:position] = self.x_range.focus_order()
        return widgets


class _AboutContent(QWidget):
    """The inside of *About this analysis*. It asks the scroll area for the height its
    text needs with the sketch at its smallest, so it scrolls only when even that
    doesn't fit; any room left over goes to the sketch."""

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt API)
        layout = self.layout()
        if layout is None:
            return super().heightForWidth(width)
        margins = layout.contentsMargins()
        inner = width - margins.left() - margins.right()
        heights = []
        for i in range(layout.count()):
            item = layout.itemAt(i)
            widget = item.widget() if item is not None else None
            if widget is None or widget.isHidden():
                continue
            if widget.hasHeightForWidth():
                heights.append(widget.heightForWidth(inner))
            else:
                heights.append(max(widget.minimumHeight(), widget.minimumSizeHint().height()))
        spacing = layout.spacing() * max(len(heights) - 1, 0)
        return sum(heights) + spacing + margins.top() + margins.bottom()


class AnalysisList(QTreeWidget):
    """The analyses of a family, in sections; about :data:`LIST_ROWS` rows high."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setColumnCount(2)
        self.setHeaderHidden(True)
        self.setRootIsDecorated(False)
        self.setItemsExpandable(False)
        self.setIndentation(12)
        self.setUniformRowHeights(True)
        self.setAccessibleName("Analyses")
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)

    def _row_height(self) -> int:
        return max(self.fontMetrics().height() + 6, 18)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        hint = super().sizeHint()
        hint.setHeight(int((LIST_ROWS + 0.5) * self._row_height()) + 2 * self.frameWidth())
        return hint

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        hint = super().minimumSizeHint()
        hint.setHeight(3 * self._row_height() + 2 * self.frameWidth())
        return hint


class AnalyzeDialog(QDialog):
    """Choose and set up an analysis of the table ``table_id``.

    With ``analysis`` the dialog edits that analysis and opens as it was stored.
    ``recent`` are the ids of recently used analysis types, newest first.
    """

    def __init__(
        self,
        document: Document,
        table_id: str,
        *,
        analysis: Analysis | None = None,
        recent: Sequence[str] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.document = document
        self.table: Table = document.table(table_id)
        self.editing = analysis
        self.recent = list(recent)
        self._checked: set[str] = set()
        self._started: set[str] = set()  # analysis types whose inputs were filled in
        self._type_id: str | None = None
        self._shown_family: Family | None = None
        self.types: dict[Family, list[AnalysisType]] = {}
        for family in available_families(self.table):
            self.types[family] = runnable(self.table, family)
        if analysis is not None:
            kind = analysis.analysis_type
            family_types = self.types.setdefault(kind.family, [])
            if kind not in family_types:
                family_types.append(kind)
        self.setWindowTitle("Edit analysis" if analysis else "Analyze")
        self._build()
        size_workbench(self)
        if analysis is not None:
            self._load(analysis)
        else:
            self._start()

    # --- building ----------------------------------------------------------------------

    def _build(self) -> None:
        self.skeleton = DialogSkeleton(self, "Update" if self.editing else "Run")
        assert self.skeleton.ok_button is not None
        self.ok_button = self.skeleton.ok_button
        body = self.skeleton.body

        self.family_tabs = choice_tabs(self, "Analysis family")
        for family in Family:
            if family in self.types:
                index = self.family_tabs.addTab(family.label)
                self.family_tabs.setTabData(index, family.value)
                self.family_tabs.setTabToolTip(index, family.description)
        self.family_description = secondary_label(parent=self)
        body.addWidget(self.family_tabs)
        body.addWidget(self.family_description)

        left, right = columns(body)
        left.addWidget(self._build_data(), 1)
        left.addWidget(self._build_options())
        right.addWidget(self._build_analyses())
        right.addWidget(self._build_about(), 1)

        self.family_tabs.currentChanged.connect(lambda _i: self._on_family_changed())

    def _build_data(self) -> QGroupBox:
        box = QGroupBox("Data")
        layout = QVBoxLayout(box)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Series", "n"])
        self.tree.setAccessibleName("Data to analyze")
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setMinimumHeight(100)
        self.empty = EmptyState(self.tree, "This table has no Y columns to analyze.")
        layout.addWidget(self.tree, 1)
        buttons = QHBoxLayout()
        self.select_all_button = QPushButton("Select a&ll")
        self.deselect_all_button = QPushButton("&Deselect all")
        self.select_all_button.clicked.connect(lambda: self._check_all(True))
        self.deselect_all_button.clicked.connect(lambda: self._check_all(False))
        buttons.addWidget(self.select_all_button)
        buttons.addWidget(self.deselect_all_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.tree.itemChanged.connect(self._on_item_changed)
        return box

    def _build_analyses(self) -> QGroupBox:
        self.analyses_box = QGroupBox("A&nalysis")
        self.analyses_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self.analyses_box)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Type to filter…")
        self.filter_edit.setAccessibleName("Filter analyses")
        self.filter_edit.installEventFilter(self)
        self.analysis_list = AnalysisList()
        self.no_match = secondary_label("", self.analysis_list.viewport())
        self.no_match.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_match.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.no_match.hide()
        layout.addWidget(self.filter_edit)
        layout.addWidget(self.analysis_list)
        self.filter_edit.textChanged.connect(self._apply_filter)
        self.analysis_list.currentItemChanged.connect(self._on_current_item_changed)
        self.analysis_list.itemDoubleClicked.connect(lambda _item, _col: self._run_if_ready())
        return self.analyses_box

    def _build_options(self) -> QGroupBox:
        self.options_box = QGroupBox("Options")
        self.options_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self.options_box)
        self.options = FittedStack()
        self.forms: dict[str, ParamsForm] = {}
        for kinds in self.types.values():
            for kind in kinds:
                form = ParamsForm(kind)
                form.changed.connect(self._update)
                if form.advanced is not None:
                    form.advanced.toggled.connect(lambda _shown: self.options.updateGeometry())
                self.forms[kind.id] = form
                self.options.addWidget(form)
        layout.addWidget(self.options)
        return self.options_box

    def _build_about(self) -> QGroupBox:
        box = QGroupBox("About this analysis")
        # The text scrolls when it doesn't fit: long equations and wide fonts (macOS)
        # need more height than the right column has at the minimum size.
        self.about_scroll = QScrollArea()
        self.about_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.about_scroll.setWidgetResizable(True)
        self.about_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.about_scroll.setAccessibleName("About this analysis")
        self.about_scroll.viewport().setAutoFillBackground(False)
        content = _AboutContent()
        self.about_scroll.setWidget(content)
        QVBoxLayout(box).addWidget(self.about_scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        selectable = Qt.TextInteractionFlag.TextSelectableByMouse

        def label(secondary: bool = False) -> QLabel:
            widget = secondary_label() if secondary else QLabel()
            widget.setWordWrap(True)
            widget.setTextInteractionFlags(selectable)
            widget.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            return widget

        self.equation = label()
        self.equation.setTextFormat(Qt.TextFormat.RichText)
        self.equation.setAccessibleName("Equation")
        font = QFont(self.equation.font())
        font.setPointSizeF(font.pointSizeF() + 1)
        self.equation.setFont(font)
        self.equation_note = label(secondary=True)
        self.hypothesis = label()
        self.hypothesis.setTextFormat(Qt.TextFormat.RichText)
        self.assumptions = label()
        self.assumptions.setTextFormat(Qt.TextFormat.RichText)
        self.sketch = SketchView()
        self.summary = label()
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.use_when = label(secondary=True)
        for widget in (
            self.equation,
            self.equation_note,
            self.hypothesis,
            self.assumptions,
            self.sketch,
            self.summary,
            self.use_when,
        ):
            layout.addWidget(widget)
        layout.addStretch(1)
        return box

    # --- state -------------------------------------------------------------------------

    @property
    def family(self) -> Family | None:
        data = self.family_tabs.tabData(self.family_tabs.currentIndex())
        return Family(data) if data else None

    def _kind(self, type_id: str) -> AnalysisType:
        return next(k for kinds in self.types.values() for k in kinds if k.id == type_id)

    @property
    def analysis_type(self) -> AnalysisType | None:
        return self._kind(self._type_id) if self._type_id is not None else None

    def set_family(self, family: Family) -> None:
        for i in range(self.family_tabs.count()):
            if self.family_tabs.tabData(i) == family.value:
                self.family_tabs.setCurrentIndex(i)
                return
        raise ValueError(f"The family {family.label} is not available for this table.")

    def set_analysis_type(self, type_id: str) -> None:
        kind = self._kind(type_id)
        self.set_family(kind.family)
        self._fill_list(kind.family)
        item = self._items_of(type_id)[0]
        self.analysis_list.setCurrentItem(item)
        if self._type_id != type_id:  # the item was current already
            self._choose(type_id)

    def listed_types(self) -> list[str]:
        """The ids of the analyses in the list's sections (not Recent), in order."""
        return [i.data(0, _ID) for i in self._analysis_items() if not self._in_recent(i)]

    def recent_types(self) -> list[str]:
        """The ids in the Recent section, newest first."""
        return [i.data(0, _ID) for i in self._analysis_items() if self._in_recent(i)]

    def visible_types(self) -> list[str]:
        """The ids of the rows the filter leaves, in list order."""
        return [i.data(0, _ID) for i in self._analysis_items() if not i.isHidden()]

    def _checkable_items(self) -> list[QTreeWidgetItem]:
        items = (self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount()))
        return [i for i in items if i is not None and i.flags() & Qt.ItemFlag.ItemIsUserCheckable]

    def checked_inputs(self) -> tuple[str, ...]:
        """The ticked inputs, in table order."""
        checked = Qt.CheckState.Checked
        return tuple(i.data(0, _ID) for i in self._checkable_items() if i.checkState(0) == checked)

    def set_checked(self, ids: Sequence[str]) -> None:
        wanted = set(ids)
        with QSignalBlocker(self.tree):
            for item in self._checkable_items():
                checked = item.data(0, _ID) in wanted
                item.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._checked = set(self.checked_inputs())
        self._update()

    def params(self) -> Params | None:
        kind = self.analysis_type
        return self.forms[kind.id].params() if kind is not None else None

    def analysis(self) -> Analysis:
        """The analysis as set up in the dialog (the edited one keeps its id)."""
        kind = self.analysis_type
        params = self.params()
        assert kind is not None and params is not None
        if self.editing is not None:
            return dataclasses.replace(
                self.editing,
                type=kind.id,
                inputs=self.checked_inputs(),
                params=params,
            )
        return Analysis(kind.id, self.table.id, self.checked_inputs(), params)

    # --- the analysis list ---------------------------------------------------------------

    def _sections(self) -> list[QTreeWidgetItem]:
        items = (
            self.analysis_list.topLevelItem(i)
            for i in range(self.analysis_list.topLevelItemCount())
        )
        return [i for i in items if i is not None]

    def _analysis_items(self) -> list[QTreeWidgetItem]:
        found: list[QTreeWidgetItem] = []
        for section in self._sections():
            found += _children(section)
        return found

    def _items_of(self, type_id: str) -> list[QTreeWidgetItem]:
        return [i for i in self._analysis_items() if i.data(0, _ID) == type_id]

    @staticmethod
    def _in_recent(item: QTreeWidgetItem) -> bool:
        parent = item.parent()
        return parent is not None and parent.data(0, _ID) == RECENT

    def _fill_list(self, family: Family) -> None:
        if self._shown_family is family:
            return
        self._shown_family = family
        kinds = self.types.get(family, [])
        quiet = self.palette().color(QPalette.ColorRole.PlaceholderText)
        with QSignalBlocker(self.analysis_list), QSignalBlocker(self.filter_edit):
            self.filter_edit.clear()
            self.analysis_list.clear()
            recent = [k for t in self.recent for k in kinds if k.id == t][:RECENT_MAX]
            sections: dict[str, list[AnalysisType]] = {}
            if recent:
                sections[RECENT] = recent
            for kind in kinds:
                sections.setdefault(kind.section or family.label, []).append(kind)
            for title, members in sections.items():
                header = QTreeWidgetItem([title])
                header.setData(0, _ID, RECENT if title == RECENT and members is recent else "")
                header.setFlags(Qt.ItemFlag.ItemIsEnabled)  # children of a disabled item are too
                font = QFont(self.analysis_list.font())
                font.setBold(True)
                font.setPointSizeF(max(font.pointSizeF() - 1, 7))
                header.setFont(0, font)
                header.setForeground(0, quiet)
                header.setFirstColumnSpanned(True)
                self.analysis_list.addTopLevelItem(header)
                for kind in members:
                    item = QTreeWidgetItem([kind.name, kind.about.reports])
                    item.setData(0, _ID, kind.id)
                    item.setTextAlignment(
                        1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                    item.setForeground(1, quiet)
                    item.setToolTip(0, kind.about.use_when)
                    header.addChild(item)
            self.analysis_list.expandAll()
        self.no_match.hide()

    def _apply_filter(self, text: str) -> None:
        needle = " ".join(text.lower().split())
        for section in self._sections():
            recent = section.data(0, _ID) == RECENT
            shown = 0
            for item in _children(section):
                kind = self._kind(item.data(0, _ID))
                haystack = f"{kind.name} {kind.about.reports}".lower()
                hide = bool(needle) and (recent or needle not in haystack)
                item.setHidden(hide)
                shown += not hide
            section.setHidden(shown == 0)
        visible = [i for i in self._analysis_items() if not i.isHidden()]
        current = self.analysis_list.currentItem()
        if visible and (current is None or current.isHidden()):
            chosen = [i for i in visible if i.data(0, _ID) == self._type_id]
            self.analysis_list.setCurrentItem(chosen[0] if chosen else visible[0])
        self.no_match.setVisible(not visible)
        if not visible:
            self.no_match.setText(f"No analysis matches “{text.strip()}”.")
            self.no_match.setGeometry(self.analysis_list.viewport().rect())

    def _move_in_list(self, step: int) -> None:
        visible = [i for i in self._analysis_items() if not i.isHidden()]
        if not visible:
            return
        current = self.analysis_list.currentItem()
        index = visible.index(current) if current in visible else (-1 if step > 0 else 0)
        self.analysis_list.setCurrentItem(visible[max(0, min(len(visible) - 1, index + step))])

    def _on_current_item_changed(
        self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None
    ) -> None:
        if current is None:
            return
        type_id = current.data(0, _ID)
        if current.parent() is None or not type_id:
            # A section header: move on to the analysis next to it, the way the user went.
            items = [i for i in self._analysis_items() if not i.isHidden()]
            if not items:
                return
            below = [i for i in items if self._list_index(i) > self._list_index(current)]
            above = [i for i in items if self._list_index(i) < self._list_index(current)]
            going_up = previous is not None and self._list_index(previous) > self._list_index(
                current
            )
            target = (
                (above[-1] if above else below[0])
                if going_up
                else (below[0] if below else above[-1])
            )
            self.analysis_list.setCurrentItem(target)
            return
        if type_id != self._type_id:
            self._choose(type_id)

    def _list_index(self, item: QTreeWidgetItem) -> int:
        order = []
        for section in self._sections():
            order.append(section)
            order += _children(section)
        return order.index(item)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt API)
        if watched is self.filter_edit and event.type() == QEvent.Type.KeyPress:
            assert isinstance(event, QKeyEvent)
            if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self._move_in_list(1 if event.key() == Qt.Key.Key_Down else -1)
                return True
        return super().eventFilter(watched, event)

    def _run_if_ready(self) -> None:
        if self.ok_button.isEnabled():
            self.accept()

    # --- starting ----------------------------------------------------------------------

    def _start(self) -> None:
        family = opening_family(self.table)
        if family is None:
            self._on_family_changed()
            self._set_focus_order()
            return
        with QSignalBlocker(self.family_tabs):
            self.set_family(family)
        self._on_family_changed()
        self._set_focus_order()

    def _load(self, analysis: Analysis) -> None:
        kind = analysis.analysis_type
        with QSignalBlocker(self.family_tabs):
            self.set_family(kind.family)
        self._fill_list(kind.family)
        self._started.add(kind.id)
        self.forms[kind.id].set_params(analysis.params)
        self._checked = set(analysis.inputs)
        with QSignalBlocker(self.analysis_list):
            self.analysis_list.setCurrentItem(self._items_of(kind.id)[0])
        self._choose(kind.id)
        self._fill_tree(kind)
        self.set_checked(analysis.inputs)
        self._set_focus_order()

    def _set_focus_order(self) -> None:
        widgets: list[QWidget] = [
            self.family_tabs,
            self.tree,
            self.select_all_button,
            self.deselect_all_button,
        ]
        for form in self.forms.values():
            widgets += form.focus_order()
        widgets += [self.filter_edit, self.analysis_list]
        set_tab_order(widgets)
        self.tree.setFocus()

    def _default_type(self, family: Family) -> AnalysisType | None:
        kinds = self.types.get(family, [])
        for type_id in self.recent:
            for kind in kinds:
                if kind.id == type_id and kind.can_run(self.table):
                    return kind
        return kinds[0] if kinds else None

    # --- reacting ----------------------------------------------------------------------

    def _on_family_changed(self) -> None:
        family = self.family
        self.family_description.setText(family.description if family else "")
        if family is None:
            self.skeleton.set_problem("This table has no data that Curvis can analyze yet.")
            self._show_about(None, None)
            return
        self._fill_list(family)
        current = self.analysis_type
        if current is not None and current.family is family:
            items = self._items_of(current.id)
            if items and self.analysis_list.currentItem() not in items:
                with QSignalBlocker(self.analysis_list):
                    self.analysis_list.setCurrentItem(items[0])
            return
        kind = self._default_type(family)
        if kind is not None:
            self.analysis_list.setCurrentItem(self._items_of(kind.id)[0])

    def _choose(self, type_id: str) -> None:
        """Make ``type_id`` the chosen analysis: its options, its data, its About."""
        self._type_id = type_id
        kind = self._kind(type_id)
        self.options.setCurrentWidget(self.forms[kind.id])
        self._fill_tree(kind)
        if kind.id not in self._started and self.editing is None:
            # A new analysis starts from its own default inputs when nothing is ticked or
            # the ticked series do not suit it (e.g. three ticked for a t test).
            ticked = [c.id for c in kind.selected(self.table, sorted(self._checked))]
            params = self.forms[kind.id].params()
            if not ticked or kind.check(self.table, ticked, params) is not None:
                calculated = {
                    c.column_id for calc in self.document.calculations for c in calc.columns
                }
                self._checked = set(kind.default_inputs(self.table, calculated))
        self._started.add(kind.id)
        self.set_checked(sorted(self._checked))

    def _fill_tree(self, kind: AnalysisType) -> None:
        candidates = kind.candidates(self.table)
        used = {c for choice in candidates for c in choice.column_ids}
        by_x = rows_match_x(self.table)
        header = self.tree.headerItem()
        if header is not None:
            tip = "Number of replicates" if by_x else "Number of values"
            header.setToolTip(_N_COLUMN, tip)
        with QSignalBlocker(self.tree):
            self.tree.clear()
            for choice in candidates:
                item = QTreeWidgetItem([choice.name, str(choice.n(by_x))])
                item.setData(0, _ID, choice.id)
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                item.setCheckState(0, Qt.CheckState.Unchecked)
                item.setTextAlignment(_N_COLUMN, Qt.AlignmentFlag.AlignRight)
                self.tree.addTopLevelItem(item)
            for column in self.table.columns:
                if column.id in used or column.role is ColumnRole.TEXT:
                    continue
                item = QTreeWidgetItem([f"{column.name} ({column.role.value})", "–"])
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                item.setTextAlignment(_N_COLUMN, Qt.AlignmentFlag.AlignRight)
                self.tree.addTopLevelItem(item)
        self.tree.resizeColumnToContents(0)
        self.empty.update()

    def _on_item_changed(self, _item: QTreeWidgetItem, _column: int) -> None:
        self._checked = set(self.checked_inputs())
        self._update()

    def _check_all(self, checked: bool) -> None:
        ids = [item.data(0, _ID) for item in self._checkable_items()]
        self.set_checked(ids if checked else [])

    def _update(self) -> None:
        kind = self.analysis_type
        if kind is None:
            if self.family is not None:
                self.skeleton.set_problem("Choose an analysis.")
            self._show_about(None, None)
            return
        inputs = self.checked_inputs()
        form = self.forms[kind.id]
        form.set_inputs([(c.id, c.name) for c in kind.selected(self.table, inputs)])
        try:
            params = form.params()
        except ValueError as exc:
            self.skeleton.set_problem(f"{str(exc).capitalize()}.")
            return
        problem = kind.problem(self.table) or kind.check(self.table, inputs, params)
        fit = _curve_fit(kind)
        if fit is not None:
            form.set_point_count(fit.point_count(self.table, inputs, params))
        self._show_about(kind, params)
        self.summary.setText(kind.describe(self.table, inputs, params) if not problem else "")
        self.summary.setVisible(bool(self.summary.text()))
        self.skeleton.set_problem(problem)

    # --- About this analysis -----------------------------------------------------------

    def _show_about(self, kind: AnalysisType | None, params: Params | None) -> None:
        fit = _curve_fit(kind) if kind is not None else None
        if fit is not None and params is not None:
            from curvis.formula.display import render_equation

            self.equation.setText(render_equation(fit.equation_for(params)))
            self.equation_note.setText(fit.equation_note)
            self.sketch.set_sketch(fit.sketch_data(params))
        else:
            self.equation.setText("")
            self.equation_note.setText("")
            self.sketch.set_sketch(None)
        hypothesis = kind.about.hypothesis_for(params) if kind and params else None
        self.hypothesis.setText(
            self._keyed("Null hypothesis", hypothesis.null if hypothesis else "")
        )
        self.assumptions.setText(self._keyed("Assumes", hypothesis.assumes if hypothesis else ""))
        self.use_when.setText(kind.about.use_when if kind else "")
        if kind is None:
            self.summary.setText("")
        for label in (
            self.equation,
            self.equation_note,
            self.hypothesis,
            self.assumptions,
            self.use_when,
        ):
            label.setVisible(bool(label.text()))

    def _keyed(self, key: str, text: str) -> str:
        """``key: text`` with the key in the PlaceholderText colour (rich text)."""
        if not text:
            return ""
        colour = self.palette().color(QPalette.ColorRole.PlaceholderText).name()
        return f'<span style="color:{colour}">{key}:</span> {html.escape(text)}'

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt API)
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "summary"):
            self._shown_family = None  # the list's quiet text takes the new palette
            if self.family is not None:
                self._fill_list(self.family)
                items = self._items_of(self._type_id) if self._type_id else []
                if items:
                    with QSignalBlocker(self.analysis_list):
                        self.analysis_list.setCurrentItem(items[0])
            self._update()
