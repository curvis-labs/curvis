"""The Calculate dialog: add calculated columns, or change an existing calculation.

One page. On the left the calculation type; on the right the data (which columns, and
whether they are paired with A/B roles), the type's options, the formula and how the new
columns are named. The ready-made types write their formula into the formula box, where
it can be read and edited; editing it turns the calculation into a custom formula.

The dialog only builds a :class:`~curvis.core.Calculation`; the caller runs it with
:func:`~curvis.core.add_calculation` or :func:`~curvis.core.edit_calculation`.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QRegularExpression, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from curvis.core import Calculation, CalculationKind, Document
from curvis.core import calculation_formulas as cf
from curvis.core.constants import JsonValue, ProjectConstant
from curvis.data import Column, ColumnRole, Pairing, Table
from curvis.data.pairing import DEFAULT_ROLE_A, DEFAULT_ROLE_B
from curvis.formula import (
    BUILTIN_CONSTANTS,
    FormulaError,
    Function,
    check,
    parse,
    resolve_constants,
)
from curvis.ui.dialogs import (
    FIELD_WIDTH,
    SECTION_SPACING,
    DialogSkeleton,
    FittedStack,
    choice_tabs,
    columns,
    error_label,
    secondary_label,
    set_tab_order,
    size_workbench,
)

KINDS = (
    CalculationKind.CUSTOM,
    CalculationKind.BASELINE,
    CalculationKind.NORMALIZE,
    CalculationKind.FOLD_CHANGE,
)

KIND_DESCRIPTIONS = {
    CalculationKind.CUSTOM: "Any formula of columns, functions and constants.",
    CalculationKind.BASELINE: "Subtract a baseline: a value, a column or the first value.",
    CalculationKind.NORMALIZE: "Scale each column so 0% and 100% are its minimum and maximum, "
    "or values you choose.",
    CalculationKind.FOLD_CHANGE: "Divide by a reference: a value, a column or the first value.",
}

FUNCTION_DESCRIPTIONS = {
    Function.FIRST: "first value of the column",
    Function.LAST: "last value of the column",
    Function.ROW: "value of the column in row n",
    Function.MIN: "smallest value of the column",
    Function.MAX: "largest value of the column",
    Function.MEAN: "average of the column",
    Function.SUM: "total of the column",
    Function.COUNT: "number of values in the column",
}

ROLE_A, ROLE_B = "A", "B"
_ROLE_COLUMN = 1
_ID = Qt.ItemDataRole.UserRole
_INSERT = Qt.ItemDataRole.UserRole + 1


@dataclass(frozen=True)
class _PaletteEntry:
    insert: str
    label: str
    description: str


class FormulaHighlighter(QSyntaxHighlighter):
    """Colours column and role references, functions and constants; underlines an error."""

    _COLUMN = QRegularExpression(r"\[([^\]]|\]\])*\]")
    _ROLE = QRegularExpression(r"\{[^{}]*\}")
    _FUNCTION = QRegularExpression(
        r"\b(" + "|".join(f.value for f in Function) + r")\b(?=\s*\()",
        QRegularExpression.PatternOption.CaseInsensitiveOption,
    )
    _NAME = QRegularExpression(r"(?<![\w\[{])[^\W\d]\w*(?![\w(])")

    def __init__(self, document: QTextDocument, editor: QWidget) -> None:
        super().__init__(document)
        self._editor = editor
        self.error_span: tuple[int, int] | None = None

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt API)
        palette = self._editor.palette()
        reference = QTextCharFormat()
        reference.setForeground(palette.link())
        role = QTextCharFormat(reference)
        role.setFontWeight(QFont.Weight.Bold)
        function = QTextCharFormat()
        function.setFontWeight(QFont.Weight.Bold)
        constant = QTextCharFormat()
        constant.setFontItalic(True)
        for pattern, fmt in (
            (self._NAME, constant),
            (self._FUNCTION, function),
            (self._COLUMN, reference),
            (self._ROLE, role),
        ):
            matches = pattern.globalMatch(text)
            while matches.hasNext():
                match = matches.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)
        if self.error_span is not None:
            start, end = self.error_span
            start, end = min(start, len(text)), min(end, len(text))
            if start == end and start > 0:
                start -= 1
            for i in range(start, end):
                fmt = QTextCharFormat(self.format(i))
                fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
                self.setFormat(i, 1, fmt)


class _NumberEdit(QLineEdit):
    """A line edit for one number; :meth:`value` is None while the text is not a number."""

    def __init__(self, value: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.set_value(value)
        self.setMaximumWidth(FIELD_WIDTH)

    def value(self) -> float | None:
        try:
            number = float(self.text().strip().replace(",", "."))
        except ValueError:
            return None
        return number if math.isfinite(number) else None

    def set_value(self, value: float) -> None:
        self.setText(f"{value:g}" if value == int(value) and abs(value) < 1e15 else repr(value))


class _ReferenceOptions(QWidget):
    """Options of baseline subtraction and fold change: what to subtract or divide by."""

    changed = Signal()

    def __init__(self, verb: str, columns: Sequence[Column], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.stack = FittedStack(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)

        unpaired = QWidget()
        grid = QGridLayout(unpaired)
        grid.setContentsMargins(0, 0, 0, 0)
        self.first = QRadioButton(f"{verb} the f&irst value of each column")
        self.value_radio = QRadioButton(f"{verb} a &value:")
        self.value_edit = _NumberEdit(0.0)
        self.value_edit.setAccessibleName(f"Value to {verb.lower()}")
        self.column_radio = QRadioButton(f"{verb} another column, ro&w by row:")
        self.column_mean_radio = QRadioButton(f"{verb} the mean of anot&her column:")
        self.column_combo = QComboBox()
        self.column_combo.setAccessibleName("Reference column")
        for column in columns:
            self.column_combo.addItem(column.name, column.id)
        self.unpaired_group = QButtonGroup(self)
        for i, (radio, ref) in enumerate(
            (
                (self.first, cf.FIRST),
                (self.value_radio, cf.VALUE),
                (self.column_radio, cf.COLUMN),
                (self.column_mean_radio, cf.COLUMN_MEAN),
            )
        ):
            radio.setProperty("reference", ref)
            self.unpaired_group.addButton(radio)
            grid.addWidget(radio, i, 0)
        grid.addWidget(self.value_edit, 1, 1)
        grid.addWidget(self.column_combo, 2, 1, 2, 1)
        grid.setColumnStretch(2, 1)
        self.first.setChecked(True)
        self.stack.addWidget(unpaired)

        paired = QWidget()
        box = QVBoxLayout(paired)
        box.setContentsMargins(0, 0, 0, 0)
        self.matched = QRadioButton(f"{verb} the ma&tching role A column")
        self.role_mean = QRadioButton(f"{verb} the mean of the role A col&umns")
        self.matched.setProperty("reference", cf.MATCHED)
        self.role_mean.setProperty("reference", cf.ROLE_MEAN)
        self.paired_group = QButtonGroup(self)
        for radio in (self.matched, self.role_mean):
            self.paired_group.addButton(radio)
            box.addWidget(radio)
        self.role_mean.setChecked(True)
        self.stack.addWidget(paired)

        for group in (self.unpaired_group, self.paired_group):
            group.buttonToggled.connect(lambda *_: self._update())
        self.value_edit.textChanged.connect(self.changed)
        self.column_combo.currentIndexChanged.connect(self.changed)
        self._update()

    def set_paired(self, paired: bool) -> None:
        self.stack.setCurrentIndex(1 if paired else 0)
        self.changed.emit()

    def params(self) -> dict[str, JsonValue]:
        group = self.paired_group if self.stack.currentIndex() == 1 else self.unpaired_group
        checked = group.checkedButton()
        assert checked is not None  # one radio of each group is always checked
        params: dict[str, JsonValue] = {"reference": checked.property("reference")}
        value = self.value_edit.value()
        if value is not None:
            params["value"] = value
        if self.column_combo.currentData() is not None:
            params["column"] = self.column_combo.currentData()
        return params

    def set_params(self, params: dict[str, JsonValue]) -> None:
        reference = params.get("reference")
        for group in (self.unpaired_group, self.paired_group):
            for radio in group.buttons():
                if radio.property("reference") == reference:
                    radio.setChecked(True)
        value = params.get("value")
        if isinstance(value, int | float) and not isinstance(value, bool):
            self.value_edit.set_value(float(value))
        index = self.column_combo.findData(params.get("column"))
        if index >= 0:
            self.column_combo.setCurrentIndex(index)

    def _update(self) -> None:
        self.value_edit.setEnabled(self.value_radio.isChecked())
        self.column_combo.setEnabled(
            self.column_radio.isChecked() or self.column_mean_radio.isChecked()
        )
        self.changed.emit()


class _NormalizeOptions(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        self.own = QRadioButton("Each column's o&wn minimum (0%) and maximum (100%)")
        self.values = QRadioButton("These &values:")
        self.low = _NumberEdit(0.0)
        self.high = _NumberEdit(100.0)
        self.group = QButtonGroup(self)
        self.group.addButton(self.own)
        self.group.addButton(self.values)
        self.own.setChecked(True)
        grid.addWidget(self.own, 0, 0, 1, 5)
        grid.addWidget(self.values, 1, 0)
        low_label, high_label = QLabel("0% ="), QLabel("100% =")
        low_label.setBuddy(self.low)
        high_label.setBuddy(self.high)
        grid.addWidget(low_label, 1, 1)
        grid.addWidget(self.low, 1, 2)
        grid.addWidget(high_label, 1, 3)
        grid.addWidget(self.high, 1, 4)
        grid.setColumnStretch(5, 1)
        self.group.buttonToggled.connect(lambda *_: self._update())
        self.low.textChanged.connect(self.changed)
        self.high.textChanged.connect(self.changed)
        self._update()

    def params(self) -> dict[str, JsonValue]:
        params: dict[str, JsonValue] = {
            "range": cf.VALUES_RANGE if self.values.isChecked() else cf.OWN_RANGE
        }
        for key, edit in (("low", self.low), ("high", self.high)):
            value = edit.value()
            if value is not None:
                params[key] = value
        return params

    def set_params(self, params: dict[str, JsonValue]) -> None:
        (self.values if params.get("range") == cf.VALUES_RANGE else self.own).setChecked(True)
        for key, edit in (("low", self.low), ("high", self.high)):
            value = params.get(key)
            if isinstance(value, int | float) and not isinstance(value, bool):
                edit.set_value(float(value))

    def _update(self) -> None:
        self.low.setEnabled(self.values.isChecked())
        self.high.setEnabled(self.values.isChecked())
        self.changed.emit()


class CalculateDialog(QDialog):
    """Make a calculation for ``table``, or change ``calculation`` when one is given.

    ``selected`` are the ids of the columns selected in the table; they start checked
    (all Y columns when none is selected). ``global_constants`` are the user's global
    constants, listed with the project's constants.
    """

    def __init__(
        self,
        document: Document,
        table_id: str,
        *,
        calculation: Calculation | None = None,
        selected: Sequence[str] = (),
        global_constants: Sequence[ProjectConstant] = (),
        manage_constants: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.document = document
        self.table: Table = document.table(table_id)
        self.editing = calculation
        self._global_constants = list(global_constants)
        self._manage_constants = manage_constants
        self._updating = False
        self._suffix_edited = False
        self._formula_error: FormulaError | None = None
        own = {c.column_id for c in calculation.columns} if calculation else set()
        self._sources = [c for c in self.table.columns if c.role.is_numeric and c.id not in own]

        self.setWindowTitle("Edit calculation" if calculation else "Calculate")
        self._build()
        size_workbench(self)
        if calculation is not None:
            self._load(calculation)
        else:
            self._start(selected)
        self._regenerate()

    # --- building ----------------------------------------------------------------------

    def _build(self) -> None:
        self.skeleton = DialogSkeleton(self, "Update" if self.editing else "Calculate")
        self.buttons = self.skeleton.buttons
        assert self.skeleton.ok_button is not None
        self.ok_button = self.skeleton.ok_button
        self.problem_label = self.skeleton.feedback
        body = self.skeleton.body

        self.type_tabs = choice_tabs(self, "Calculation type")
        for kind in KINDS:
            index = self.type_tabs.addTab(kind.label)
            self.type_tabs.setTabData(index, kind.value)
            self.type_tabs.setTabToolTip(index, KIND_DESCRIPTIONS[kind])
        self.type_description = secondary_label(parent=self)
        body.addWidget(self.type_tabs)
        body.addWidget(self.type_description)

        left, right = columns(body)
        left.addWidget(self._build_data(), 1)
        right.addWidget(self._build_options())
        right.addWidget(self._build_formula(), 1)
        self._build_details()
        set_tab_order(
            [
                self.type_tabs,
                self.not_paired,
                self.paired,
                self.a_name,
                self.b_name,
                self.tree,
                self.select_all_button,
                self.deselect_all_button,
                self.role_a_button,
                self.role_b_button,
                self.options,
                self.formula_edit,
                *self.palette_buttons.values(),
                self.manage_constants_button,
                self.palette_list,
                self.suffix_edit,
                self.append_check,
            ]
        )
        self.tree.setFocus()

        self.type_tabs.currentChanged.connect(self._on_kind_changed)

    def _build_data(self) -> QGroupBox:
        box = QGroupBox("Data")
        layout = QVBoxLayout(box)

        pairing_row = QHBoxLayout()
        self.not_paired = QPushButton("&Not paired")
        self.paired = QPushButton("&Paired (A/B roles)")
        self.pairing_group = QButtonGroup(self)
        for button in (self.not_paired, self.paired):
            button.setCheckable(True)
            self.pairing_group.addButton(button)
            pairing_row.addWidget(button)
        self.not_paired.setChecked(True)
        layout.addLayout(pairing_row)

        names = QHBoxLayout()
        self.a_name = QLineEdit(DEFAULT_ROLE_A)
        self.b_name = QLineEdit(DEFAULT_ROLE_B)
        self.a_label = QLabel("Role &A name:")
        self.a_label.setBuddy(self.a_name)
        self.b_label = QLabel("Role &B name:")
        self.b_label.setBuddy(self.b_name)
        for widget in (self.a_label, self.a_name, self.b_label, self.b_name):
            names.addWidget(widget)
        layout.addLayout(names)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Column", "Role"])
        self.tree.setAccessibleName("Columns to calculate")
        self.tree.setRootIsDecorated(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setMinimumHeight(140)
        self._fill_tree()
        layout.addWidget(self.tree)

        buttons = QHBoxLayout()
        select_all = self.select_all_button = QPushButton("Select a&ll")
        deselect_all = self.deselect_all_button = QPushButton("&Deselect all")
        self.role_a_button = QPushButton("Set role A")
        self.role_b_button = QPushButton("Set role B")
        select_all.clicked.connect(lambda: self._check_all(True))
        deselect_all.clicked.connect(lambda: self._check_all(False))
        self.role_a_button.clicked.connect(lambda: self.set_role_of_selected(ROLE_A))
        self.role_b_button.clicked.connect(lambda: self.set_role_of_selected(ROLE_B))
        for button in (select_all, deselect_all):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.role_a_button)
        buttons.addWidget(self.role_b_button)
        layout.addLayout(buttons)

        self.pairing_group.buttonToggled.connect(lambda *_: self._on_pairing_changed())
        self.a_name.textChanged.connect(self._on_inputs_changed)
        self.b_name.textChanged.connect(self._on_inputs_changed)
        self.tree.itemChanged.connect(self._on_tree_changed)
        self.tree.itemClicked.connect(self._on_tree_clicked)
        return box

    def _fill_tree(self) -> None:
        self._items: dict[str, QTreeWidgetItem] = {}
        groups: dict[str, QTreeWidgetItem] = {}
        source_ids = {c.id for c in self._sources}
        for column in self.table.columns:
            if column.role is ColumnRole.TEXT:
                continue
            parent: QTreeWidgetItem | None = None
            if column.group_id is not None and column.id in source_ids:
                parent = groups.get(column.group_id)
                if parent is None:
                    group = self.table.replicate_group(column.group_id)
                    parent = QTreeWidgetItem([group.name, ""])
                    parent.setFlags(
                        Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsSelectable
                        | Qt.ItemFlag.ItemIsUserCheckable
                        | Qt.ItemFlag.ItemIsAutoTristate
                    )
                    parent.setCheckState(0, Qt.CheckState.Unchecked)
                    self.tree.addTopLevelItem(parent)
                    groups[column.group_id] = parent
            item = QTreeWidgetItem([column.name, ""])
            item.setData(0, _ID, column.id)
            if column.id in source_ids and column.role is ColumnRole.Y:
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                item.setCheckState(0, Qt.CheckState.Unchecked)
                self._items[column.id] = item
            else:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                item.setText(_ROLE_COLUMN, "–")
            if parent is not None:
                parent.addChild(item)
            else:
                self.tree.addTopLevelItem(item)
        self.tree.expandAll()
        self.tree.resizeColumnToContents(0)

    def _build_options(self) -> QGroupBox:
        self.options_box = QGroupBox("Options")
        layout = QVBoxLayout(self.options_box)
        self.options = FittedStack()
        reference_columns = [c for c in self.table.columns if c.role.is_numeric]
        self.custom_options = secondary_label()
        self.baseline_options = _ReferenceOptions("Subtract", reference_columns)
        self.normalize_options = _NormalizeOptions()
        self.fold_options = _ReferenceOptions("Divide by", reference_columns)
        for widget in (
            self.custom_options,
            self.baseline_options,
            self.normalize_options,
            self.fold_options,
        ):
            self.options.addWidget(widget)
        for options in (self.baseline_options, self.normalize_options, self.fold_options):
            options.changed.connect(self._regenerate)
        layout.addWidget(self.options)
        self.options_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        return self.options_box

    def _build_formula(self) -> QGroupBox:
        box = QGroupBox("Fo&rmula")
        layout = QVBoxLayout(box)
        self.formula_edit = QPlainTextEdit()
        self.formula_edit.setTabChangesFocus(True)
        self.formula_edit.setFixedHeight(self.fontMetrics().lineSpacing() * 3 + 12)
        self.formula_edit.setAccessibleName("Formula")
        self.highlighter = FormulaHighlighter(self.formula_edit.document(), self.formula_edit)
        layout.addWidget(self.formula_edit)
        self.formula_error = error_label()
        layout.addWidget(self.formula_error)

        tabs = QHBoxLayout()
        self.palette_group = QButtonGroup(self)
        self.palette_buttons: dict[str, QPushButton] = {}
        for key, label in (
            ("columns", "&Columns"),
            ("functions", "&Functions"),
            ("constants", "C&onstants"),
        ):
            button = QPushButton(label)
            button.setCheckable(True)
            self.palette_group.addButton(button)
            self.palette_buttons[key] = button
            tabs.addWidget(button)
            button.toggled.connect(functools.partial(self._on_palette_toggled, key))
        tabs.addStretch(1)
        self.manage_constants_button = QPushButton("&Manage constants…")
        self.manage_constants_button.setVisible(self._manage_constants is not None)
        self.manage_constants_button.clicked.connect(self._on_manage_constants)
        tabs.addWidget(self.manage_constants_button)
        layout.addLayout(tabs)
        self.palette_list = QListWidget()
        self.palette_list.setAccessibleName("Insert into the formula")
        self.palette_list.setToolTip("Double-click or press Enter to insert")
        self.palette_list.itemActivated.connect(self._insert_item)
        layout.addWidget(self.palette_list, 1)
        self.palette_buttons["columns"].setChecked(True)

        self.formula_edit.textChanged.connect(self._on_formula_edited)
        return box

    def _build_details(self) -> None:
        """Column suffix and placement, in the row with the dialog's buttons."""
        self.suffix_edit = QLineEdit(cf.DEFAULT_SUFFIXES[CalculationKind.BASELINE])
        self.suffix_edit.setMaximumWidth(FIELD_WIDTH)
        self.suffix_edit.textEdited.connect(self._on_suffix_edited)
        self.suffix_edit.textChanged.connect(self._validate)
        self.append_check = QCheckBox("Append at the &end")
        suffix_label = QLabel("Column &suffix:")
        suffix_label.setBuddy(self.suffix_edit)
        self.skeleton.add_secondary(suffix_label)
        self.skeleton.add_secondary(self.suffix_edit)
        self.skeleton.add_secondary(self.append_check, SECTION_SPACING)

    # --- starting state ----------------------------------------------------------------

    def _start(self, selected: Sequence[str]) -> None:
        checked = [c for c in selected if c in self._items]
        if not checked:
            checked = [
                c.id
                for c in self._sources
                if c.id in self._items and not self.document.is_calculated(c.id)
            ]
        with QSignalBlocker(self.tree):
            for column_id in checked:
                self._items[column_id].setCheckState(0, Qt.CheckState.Checked)
        self._sync_group_checks()
        self.set_kind(CalculationKind.BASELINE)

    def _load(self, calculation: Calculation) -> None:
        pairing = calculation.pairing
        with QSignalBlocker(self.pairing_group), QSignalBlocker(self.tree):
            (self.paired if pairing.paired else self.not_paired).setChecked(True)
            self.a_name.setText(pairing.a_name)
            self.b_name.setText(pairing.b_name)
            for column_id in pairing.a_columns:
                if column_id in self._items:
                    self._items[column_id].setCheckState(0, Qt.CheckState.Checked)
                    self._items[column_id].setText(_ROLE_COLUMN, ROLE_A)
            if pairing.paired:
                for column_id in pairing.b_columns:
                    if column_id in self._items:
                        self._items[column_id].setCheckState(0, Qt.CheckState.Checked)
                        self._items[column_id].setText(_ROLE_COLUMN, ROLE_B)
        self._sync_group_checks()
        options = self._options_for(calculation.kind)
        if isinstance(options, _ReferenceOptions):
            options.set_paired(pairing.paired)
        if options is not None:
            options.set_params(calculation.params)
        generated = cf.generate(calculation.kind, calculation.params, pairing, self.table)
        kind = calculation.kind if generated == calculation.formula else CalculationKind.CUSTOM
        self._suffix_edited = True
        self.suffix_edit.setText(calculation.suffix)
        self.append_check.setChecked(calculation.append_at_end)
        self.set_kind(kind)
        self._set_formula(calculation.formula)
        self._update_pairing_widgets()

    # --- public API (used by the buttons, and by tests) ---------------------------------

    @property
    def kind(self) -> CalculationKind:
        data = self.type_tabs.tabData(self.type_tabs.currentIndex())
        return CalculationKind(data) if data is not None else CalculationKind.CUSTOM

    def set_kind(self, kind: CalculationKind) -> None:
        self.type_tabs.setCurrentIndex(KINDS.index(kind))

    @property
    def formula(self) -> str:
        return self.formula_edit.toPlainText()

    def set_formula(self, text: str) -> None:
        """Type ``text`` as the formula (switches to a custom formula if it differs)."""
        self.formula_edit.setPlainText(text)

    def set_paired(self, paired: bool) -> None:
        (self.paired if paired else self.not_paired).setChecked(True)

    def set_checked(self, column_ids: Sequence[str]) -> None:
        """Check exactly ``column_ids``."""
        with QSignalBlocker(self.tree):
            for column_id, item in self._items.items():
                state = (
                    Qt.CheckState.Checked if column_id in column_ids else Qt.CheckState.Unchecked
                )
                item.setCheckState(0, state)
        self._sync_group_checks()
        self._on_inputs_changed()

    def set_role(self, column_ids: Sequence[str], role: str) -> None:
        for column_id in column_ids:
            self._items[column_id].setText(_ROLE_COLUMN, role)
        self._on_inputs_changed()

    def set_role_of_selected(self, role: str) -> None:
        ids: list[str] = []
        for item in self.tree.selectedItems():
            ids += self._column_ids_of(item)
        if ids:
            self.set_role(ids, role)

    def pairing(self) -> Pairing:
        checked = [
            c for c, item in self._items.items() if item.checkState(0) == Qt.CheckState.Checked
        ]
        paired = self.paired.isChecked() and self.kind in cf.PAIRABLE
        if not paired:
            return Pairing(
                tuple(checked), (), False, self.a_name.text().strip(), self.b_name.text().strip()
            )
        a = tuple(c for c in checked if self._items[c].text(_ROLE_COLUMN) != ROLE_B)
        b = tuple(c for c in checked if self._items[c].text(_ROLE_COLUMN) == ROLE_B)
        return Pairing(a, b, True, self.a_name.text().strip(), self.b_name.text().strip())

    def params(self) -> dict[str, JsonValue]:
        options = self._options_for(self.kind)
        return options.params() if options is not None else {}

    def calculation(self) -> Calculation:
        """The calculation as set up in the dialog (its columns are filled in by the command)."""
        fields = {
            "table_id": self.table.id,
            "formula": self.formula.strip(),
            "pairing": self.pairing(),
            "kind": self.kind,
            "params": self.params(),
            "suffix": self.suffix_edit.text(),
            "append_at_end": self.append_check.isChecked(),
        }
        if self.editing is not None:
            return Calculation(**fields, id=self.editing.id)  # type: ignore[arg-type]
        return Calculation(**fields)  # type: ignore[arg-type]

    def problem(self) -> str | None:
        """Why OK is disabled, or None."""
        pairing_problem = self.pairing().problem()
        if pairing_problem is not None:
            return pairing_problem
        if self._formula_error is not None:
            return self._formula_error.reason
        if not self.suffix_edit.text().strip():
            return "Enter a column suffix."
        return None

    # --- reacting to input -------------------------------------------------------------

    def _options_for(self, kind: CalculationKind) -> _ReferenceOptions | _NormalizeOptions | None:
        if kind is CalculationKind.BASELINE:
            return self.baseline_options
        if kind is CalculationKind.NORMALIZE:
            return self.normalize_options
        if kind is CalculationKind.FOLD_CHANGE:
            return self.fold_options
        return None

    def _on_kind_changed(self, _row: int) -> None:
        kind = self.kind
        self.options.setCurrentIndex(KINDS.index(kind))
        self.type_description.setText(KIND_DESCRIPTIONS[kind])
        if not self._suffix_edited:
            self.suffix_edit.setText(cf.DEFAULT_SUFFIXES[kind])
        self._update_pairing_widgets()
        self._regenerate()

    def _on_pairing_changed(self) -> None:
        if self.paired.isChecked():
            self._assign_default_roles()
        self._update_pairing_widgets()
        self._on_inputs_changed()

    def _update_pairing_widgets(self) -> None:
        pairable = self.kind in cf.PAIRABLE
        self.paired.setEnabled(pairable)
        self.not_paired.setEnabled(pairable)
        paired = pairable and self.paired.isChecked()
        for widget in (self.b_label, self.b_name, self.role_a_button, self.role_b_button):
            widget.setVisible(paired)
        self.tree.setColumnHidden(_ROLE_COLUMN, not paired)
        for options in (self.baseline_options, self.fold_options):
            with QSignalBlocker(options):
                options.set_paired(paired)
        self.paired.setToolTip("" if pairable else "Normalizing works on each column by itself.")
        a, b = self.a_name.text().strip() or "A", self.b_name.text().strip() or "B"
        self.custom_options.setText(
            f"Write the formula below. {{{b}}} is each role {b} column and {{{a}}} its "
            f"matching role {a} column."
            if paired
            else f"Write the formula below. {{{a}}} is each selected column."
        )

    def _assign_default_roles(self) -> None:
        """The first checked group (or column) gets role A, the others role B."""
        if any(item.text(_ROLE_COLUMN) == ROLE_B for item in self._items.values()):
            return
        first = True
        for top in self._top_items():
            ids = self._column_ids_of(top)
            if not ids:
                continue
            checked = any(self._items[c].checkState(0) == Qt.CheckState.Checked for c in ids)
            role = ROLE_A if first and checked else ROLE_B
            for column_id in ids:
                self._items[column_id].setText(_ROLE_COLUMN, role)
            if checked:
                first = False

    def _column_ids_of(self, item: QTreeWidgetItem) -> list[str]:
        column_id = item.data(0, _ID)
        if column_id is not None:
            return [column_id] if column_id in self._items else []
        return [c for child in _children(item) if (c := child.data(0, _ID)) in self._items]

    def _top_items(self) -> list[QTreeWidgetItem]:
        items = (self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount()))
        return [item for item in items if item is not None]

    def _check_all(self, checked: bool) -> None:
        self.set_checked(list(self._items) if checked else [])

    def _on_tree_changed(self, _item: QTreeWidgetItem, column: int) -> None:
        if column == 0:
            self._on_inputs_changed()

    def _on_tree_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if column != _ROLE_COLUMN or self.tree.isColumnHidden(_ROLE_COLUMN):
            return
        ids = self._column_ids_of(item)
        if ids:
            current = self._items[ids[0]].text(_ROLE_COLUMN)
            self.set_role(ids, ROLE_A if current == ROLE_B else ROLE_B)

    def _sync_group_checks(self) -> None:
        """Group rows show checked, unchecked or partly checked like their columns."""
        with QSignalBlocker(self.tree):
            for top in self._top_items():
                children = _children(top)
                if top.data(0, _ID) is None and children:
                    states = {child.checkState(0) for child in children}
                    state = states.pop() if len(states) == 1 else Qt.CheckState.PartiallyChecked
                    top.setCheckState(0, state)

    def _on_inputs_changed(self) -> None:
        self._show_role_texts()
        self._regenerate()

    def _show_role_texts(self) -> None:
        """Group rows show their columns' role."""
        with QSignalBlocker(self.tree):
            for top in self._top_items():
                children = _children(top)
                if top.data(0, _ID) is None and children:
                    roles = {child.text(_ROLE_COLUMN) for child in children}
                    top.setText(_ROLE_COLUMN, roles.pop() if len(roles) == 1 else "")

    def _on_suffix_edited(self, _text: str) -> None:
        self._suffix_edited = True

    def _regenerate(self) -> None:
        """Write the formula of a ready-made type into the formula box."""
        if self.kind is not CalculationKind.CUSTOM:
            text = cf.generate(self.kind, self.params(), self.pairing(), self.table)
            if text is not None:
                self._set_formula(text)
        self._fill_palette()
        self._validate()

    def _set_formula(self, text: str) -> None:
        if text == self.formula:
            return
        self._updating = True
        try:
            self.formula_edit.setPlainText(text)
        finally:
            self._updating = False
        self._validate()

    def _on_formula_edited(self) -> None:
        if not self._updating and self.kind is not CalculationKind.CUSTOM:
            generated = cf.generate(self.kind, self.params(), self.pairing(), self.table)
            if self.formula != generated:
                with QSignalBlocker(self.type_tabs):
                    self.set_kind(CalculationKind.CUSTOM)
                self.options.setCurrentIndex(KINDS.index(CalculationKind.CUSTOM))
                self.type_description.setText(KIND_DESCRIPTIONS[CalculationKind.CUSTOM])
                self._update_pairing_widgets()
        self._validate()

    def _constants(self) -> dict[str, float]:
        return resolve_constants(self._global_constants, self.document.constants)

    def _role_keys(self, pairing: Pairing) -> set[str]:
        keys: set[str] = set()
        for name in pairing.role_names:
            key = name.strip().casefold()
            keys |= {key, f"{key}:mean"}
        return keys

    def _validate(self) -> None:
        self._formula_error = None
        text = self.formula
        try:
            formula = parse(text)
            names = [c.name for c in self.table.columns if c.role.is_numeric]
            check(formula, names, self._constants(), self._role_keys(self.pairing()))
        except FormulaError as exc:
            self._formula_error = exc
        error = self._formula_error
        span = (error.start, error.end) if error and text.strip() else None
        if span != self.highlighter.error_span:
            self.highlighter.error_span = span
            # Changing formats emits textChanged, which would validate again.
            with QSignalBlocker(self.formula_edit):
                self.highlighter.rehighlight()
        self.formula_error.setText(error.reason + "." if error and text.strip() else "")
        problem = self.problem()
        under_formula = error is not None and problem == error.reason and bool(text.strip())
        self.skeleton.set_problem(problem, shown_in=self.formula_error if under_formula else None)

    # --- insert palette ----------------------------------------------------------------

    def _on_palette_toggled(self, key: str, on: bool) -> None:
        if on:
            self._show_palette(key)

    def _show_palette(self, key: str) -> None:
        self._palette_key = key
        self._fill_palette()

    def _fill_palette(self) -> None:
        key = getattr(self, "_palette_key", "columns")
        entries = {
            "columns": self._column_entries,
            "functions": self._function_entries,
            "constants": self._constant_entries,
        }[key]()
        self.palette_list.clear()
        for entry in entries:
            text = f"{entry.label}  –  {entry.description}" if entry.description else entry.label
            item = QListWidgetItem(text)
            item.setData(_INSERT, entry.insert)
            self.palette_list.addItem(item)

    def _column_entries(self) -> list[_PaletteEntry]:
        pairing = self.pairing()
        a = "{" + (pairing.a_name or "A") + "}"
        a_mean = "{" + (pairing.a_name or "A") + ":mean}"
        if pairing.paired:
            b = "{" + (pairing.b_name or "B") + "}"
            b_mean = "{" + (pairing.b_name or "B") + ":mean}"
            entries = [
                _PaletteEntry(b, b, "the role B column being calculated"),
                _PaletteEntry(a, a, "the matching role A column (index-matched replicate)"),
                _PaletteEntry(b_mean, b_mean, "row-wise mean of the role B columns"),
                _PaletteEntry(a_mean, a_mean, "row-wise mean of the role A columns"),
            ]
        else:
            entries = [
                _PaletteEntry(a, a, "the column being calculated"),
                _PaletteEntry(a_mean, a_mean, "row-wise mean of the selected columns"),
            ]
        for column in self.table.columns:
            if column.role.is_numeric:
                ref = "[" + column.name.replace("]", "]]") + "]"
                entries.append(_PaletteEntry(ref, ref, f"{column.role.value} column"))
        return entries

    def _function_entries(self) -> list[_PaletteEntry]:
        return [
            _PaletteEntry(
                "ROW(, )" if f is Function.ROW else f"{f.value}()",
                f.signature,
                FUNCTION_DESCRIPTIONS[f],
            )
            for f in Function
        ]

    def _constant_entries(self) -> list[_PaletteEntry]:
        entries: list[_PaletteEntry] = []
        for constant in self.document.constants:
            entries.append(
                _PaletteEntry(constant.name, constant.name, _constant_text(constant, "project"))
            )
        project = {c.name for c in self.document.constants}
        for constant in self._global_constants:
            if constant.name not in project:
                entries.append(
                    _PaletteEntry(constant.name, constant.name, _constant_text(constant, "global"))
                )
        for builtin in BUILTIN_CONSTANTS:
            unit = f" ({builtin.unit})" if builtin.unit else ""
            entries.append(_PaletteEntry(builtin.name, builtin.name, builtin.description + unit))
        return entries

    def _insert_item(self, item: QListWidgetItem) -> None:
        text = str(item.data(_INSERT))
        cursor = self.formula_edit.textCursor()
        cursor.insertText(text)
        if text.endswith("()") or text.endswith("(, )"):
            cursor.setPosition(cursor.position() - (1 if text.endswith("()") else 3))
            self.formula_edit.setTextCursor(cursor)
        self.formula_edit.setFocus()

    def _on_manage_constants(self) -> None:
        if self._manage_constants is not None:
            self._manage_constants()
            self._fill_palette()
            self._validate()

    def set_global_constants(self, constants: Sequence[ProjectConstant]) -> None:
        self._global_constants = list(constants)
        self._fill_palette()
        self._validate()


def _children(item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
    children = (item.child(i) for i in range(item.childCount()))
    return [child for child in children if child is not None]


def _constant_text(constant: ProjectConstant, scope: str) -> str:
    value = f"{constant.value:g}"
    unit = f" {constant.unit}" if constant.unit else ""
    return f"{value}{unit} ({scope})"


def selected_sources(table: Table, column_ids: Sequence[str]) -> list[str]:
    """The Y columns among ``column_ids`` (the table selection), for :class:`CalculateDialog`."""
    ids = set(column_ids)
    return [c.id for c in table.columns if c.id in ids and c.role is ColumnRole.Y]
