"""The Analyze dialog: pick an analysis, its data and its options, then run it.

A workbench dialog (docs/DESIGN.md, mockup in docs/mockups/index.html):

- the families as tabs, only those the table supports, opening on the one that best
  fits the data (:func:`curvis.analysis.base.opening_family`);
- on the left the data: the series to analyze, with their number of values;
- on the right the analyses of the family that the table supports (recently used ones
  marked), the options of the selected analysis with a collapsed Advanced section, and
  one sentence that says what will run.

The dialog only builds an :class:`~curvis.analysis.base.Analysis`; the caller stores it
with :func:`~curvis.core.add_analysis` or :func:`~curvis.core.edit_analysis`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from curvis.analysis.base import (
    Analysis,
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

_ID = Qt.ItemDataRole.UserRole
_N_COLUMN = 1


class ParamsForm(QWidget):
    """The options of one analysis type: a form generated from its parameters dataclass.

    Parameters marked ``advanced`` go in a collapsed Advanced section at the bottom.
    """

    changed = Signal()

    def __init__(self, analysis_type: AnalysisType, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.analysis_type = analysis_type
        self.fields: dict[str, QWidget] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        main = form_layout()
        layout.addLayout(main)
        self.advanced: CollapsibleSection | None = None
        advanced_form = form_layout()
        defaults = analysis_type.default_params()
        for f, info in param_fields(analysis_type.params_type):
            widget = self._field_widget(f, info.label, info.choices, info.minimum, info.maximum)
            self.fields[f.name] = widget
            form = advanced_form if info.advanced else main
            if isinstance(widget, QCheckBox):
                form.addRow(widget)
            else:
                form.addRow(buddy_label(info.label, widget), widget)
            self._set(f.name, getattr(defaults, f.name))
        if advanced_form.rowCount():
            self.advanced = CollapsibleSection(parent=self)
            self.advanced.content_layout.addLayout(advanced_form)
            layout.addWidget(self.advanced)
        if not main.rowCount() and self.advanced is None:
            main.addRow(secondary_label("This analysis has no options."))

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
        if isinstance(default, bool):
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
        widget.setObjectName(f"param_{f.name}")
        return widget

    def _set(self, name: str, value: Any) -> None:
        widget = self.fields[name]
        with QSignalBlocker(widget):
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QComboBox):
                widget.setCurrentIndex(max(0, widget.findData(value)))
            elif isinstance(widget, QSpinBox | QDoubleSpinBox):
                widget.setValue(value)
            elif isinstance(widget, QLineEdit):
                widget.setText(str(value))

    def _get(self, name: str) -> Any:
        widget = self.fields[name]
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QComboBox):
            return widget.currentData()
        if isinstance(widget, QSpinBox | QDoubleSpinBox):
            return widget.value()
        assert isinstance(widget, QLineEdit)
        return widget.text()

    def set_params(self, params: Params) -> None:
        for f, _info in param_fields(self.analysis_type.params_type):
            self._set(f.name, getattr(params, f.name))

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
            widgets.append(self.fields[f.name])
        return widgets


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
        right.addWidget(self._build_analyses())
        right.addWidget(self._build_options())
        right.addWidget(self._build_summary(), 1)

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
        self.tree.setMinimumHeight(140)
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
        self.analyses_box = QGroupBox("Analysis")
        self.analyses_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self.analyses_box)
        self.analysis_stack = FittedStack()
        self.radio_group = QButtonGroup(self)
        self.radios: dict[str, QRadioButton] = {}
        self.badges: dict[str, QLabel] = {}
        self._family_pages: dict[Family, int] = {}
        for family, kinds in self.types.items():
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            for kind in kinds:
                row = QHBoxLayout()
                radio = QRadioButton(kind.label)
                radio.setProperty("analysis_type", kind.id)
                self.radio_group.addButton(radio)
                self.radios[kind.id] = radio
                row.addWidget(radio)
                if kind.id in self.recent:
                    badge = secondary_label("recent")
                    badge.setWordWrap(False)
                    badge.setAccessibleName(f"{kind.name} was used recently")
                    self.badges[kind.id] = badge
                    row.addWidget(badge)
                row.addStretch(1)
                page_layout.addLayout(row)
            self._family_pages[family] = self.analysis_stack.addWidget(page)
        layout.addWidget(self.analysis_stack)
        self.radio_group.buttonToggled.connect(self._on_radio_toggled)
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

    def _build_summary(self) -> QGroupBox:
        box = QGroupBox("What will run")
        layout = QVBoxLayout(box)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary, 1)
        return box

    # --- state -------------------------------------------------------------------------

    @property
    def family(self) -> Family | None:
        data = self.family_tabs.tabData(self.family_tabs.currentIndex())
        return Family(data) if data else None

    @property
    def analysis_type(self) -> AnalysisType | None:
        button = self.radio_group.checkedButton()
        if button is None:
            return None
        type_id = button.property("analysis_type")
        return next(k for kinds in self.types.values() for k in kinds if k.id == type_id)

    def set_family(self, family: Family) -> None:
        for i in range(self.family_tabs.count()):
            if self.family_tabs.tabData(i) == family.value:
                self.family_tabs.setCurrentIndex(i)
                return
        raise ValueError(f"The family {family.label} is not available for this table.")

    def set_analysis_type(self, type_id: str) -> None:
        kind = next(k for kinds in self.types.values() for k in kinds if k.id == type_id)
        self.set_family(kind.family)
        self.radios[type_id].setChecked(True)

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
        self.analysis_stack.setCurrentIndex(self._family_pages[kind.family])
        self._started.add(kind.id)
        self.forms[kind.id].set_params(analysis.params)
        self._checked = set(analysis.inputs)
        self.radios[kind.id].setChecked(True)
        self._fill_tree(kind)
        self.set_checked(analysis.inputs)
        self._set_focus_order()

    def _set_focus_order(self) -> None:
        widgets: list[QWidget] = [
            self.family_tabs,
            self.tree,
            self.select_all_button,
            self.deselect_all_button,
            *self.radios.values(),
        ]
        for form in self.forms.values():
            widgets += form.focus_order()
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
            self.summary.setText("")
            return
        self.analysis_stack.setCurrentIndex(self._family_pages[family])
        current = self.analysis_type
        if current is not None and current.family is family:
            return
        kind = self._default_type(family)
        if kind is not None:
            self.radios[kind.id].setChecked(True)

    def _on_radio_toggled(self, button: QRadioButton, checked: bool) -> None:
        if not checked:
            return
        kind = self.analysis_type
        assert kind is not None
        self.options.setCurrentWidget(self.forms[kind.id])
        self._fill_tree(kind)
        if kind.id not in self._started and self.editing is None and not self._checked:
            self._checked = set(kind.default_inputs(self.table))
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
            return
        try:
            params = self.forms[kind.id].params()
        except ValueError as exc:
            self.skeleton.set_problem(f"{str(exc).capitalize()}.")
            return
        inputs = self.checked_inputs()
        problem = kind.problem(self.table) or kind.check(self.table, inputs, params)
        self.summary.setText(kind.describe(self.table, inputs, params) if not problem else "")
        self.skeleton.set_problem(problem)
