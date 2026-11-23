"""The option editors of the Analyze dialog that replace a syntax with fields.

- :class:`XRangeEditor`: "Fit only X from [ ] to [ ]" with the count of points used. It
  reads and writes the ``x_range`` text of a fit ("low to high", either end may be left
  out), so the stored parameters do not change.
- :class:`FixedTable`: one row per model parameter with a *Fix* check box and a value. It
  reads and writes the ``fixed`` text ("Top = 100, Hill slope = 1"). The columns are a
  list (:data:`FIXED_COLUMNS`), so more (an initial value, bounds) can be added later.

Both keep the stored text exactly as it was until the user changes a field, so opening
and saving an analysis made before these editors changes nothing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from PySide6.QtCore import QEvent, QRegularExpression, QSize, Qt, Signal
from PySide6.QtGui import QPalette, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from curvis.ui.dialogs import SPACING, buddy_label, secondary_label

NUMBER_WIDTH = 72
"""Width of a number field on the X range line."""
RANGE_SEPARATOR = re.compile(r"\s*(?:\bto\b|…|\.\.|;)\s*", re.IGNORECASE)
"""As :data:`curvis.analysis.fit.analyses.RANGE_SEPARATOR`."""
_NUMBER = QRegularExpression(r"[0-9eE+\-−.\s]*")


def number_field(name: str) -> QLineEdit:
    """A field for one number, such as 1e-9; empty means "no value"."""
    edit = QLineEdit()
    edit.setAccessibleName(name)
    edit.setAlignment(Qt.AlignmentFlag.AlignRight)
    edit.setValidator(QRegularExpressionValidator(_NUMBER, edit))
    edit.setFixedWidth(NUMBER_WIDTH)
    return edit


def value_text(value: float) -> str:
    """A number as a field shows it: 100, 0.5, 1e-09."""
    return f"{value:.15g}"


class XRangeEditor(QWidget):
    """Fit only X from [low] to [high], then "30 of 35 points"."""

    changed = Signal()

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.low = number_field("Fit only X from")
        self.high = number_field("Fit only X to")
        self.low.setPlaceholderText("all")
        self.high.setPlaceholderText("all")
        self.label = buddy_label(label, self.low)
        self.count = secondary_label()
        self.count.setWordWrap(False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING)
        layout.addWidget(self.label)
        layout.addWidget(self.low)
        layout.addWidget(QLabel("to"))
        layout.addWidget(self.high)
        layout.addWidget(self.count)
        layout.addStretch(1)
        self._text = ""
        self._edited = False
        for edit in (self.low, self.high):
            edit.textEdited.connect(self._on_edited)

    def _on_edited(self) -> None:
        self._edited = True
        self.changed.emit()

    def set_text(self, text: str) -> None:
        """Show a stored ``x_range``; :meth:`text` returns it unchanged until an edit."""
        self._text = text
        self._edited = False
        stripped = text.strip()
        parts = RANGE_SEPARATOR.split(stripped) if stripped else ["", ""]
        low, high = (parts[0], parts[1]) if len(parts) == 2 else (stripped, "")
        self.low.setText(low)
        self.high.setText(high)

    def text(self) -> str:
        if not self._edited:
            return self._text
        low, high = self.low.text().strip(), self.high.text().strip()
        if not low and not high:
            return ""
        return f"{low} to {high}".strip()

    def set_count(self, count: tuple[int, int] | None) -> None:
        """Show (points used, points with values), or nothing for None."""
        if count is None:
            self.count.setText("")
            return
        used, total = count
        self.count.setText(f"{used} of {total} points" if used != total else f"all {total} points")

    def focus_order(self) -> list[QWidget]:
        return [self.low, self.high]


FIX, PARAMETER, VALUE = 0, 1, 2
FIXED_COLUMNS = ("Fix", "Parameter", "Value")
"""The columns of :class:`FixedTable`, in order; later steps may add more."""


class FixedTable(QTableWidget):
    """The model's parameters, each with a *Fix* check box and a value.

    Ticking *Fix* fills in the value last typed for it, else its example value; the last
    free parameter cannot be ticked, so something is always fitted.
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, len(FIXED_COLUMNS), parent)
        self.setAccessibleName("Fix parameters")
        self.setHorizontalHeaderLabels(list(FIXED_COLUMNS))
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 8)
        header = self.horizontalHeader()
        header.setSectionResizeMode(FIX, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(PARAMETER, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(VALUE, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(VALUE, 110)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.names: list[str] = []
        self.labels: dict[str, str] = {}
        self.examples: dict[str, float] = {}
        self._typed: dict[str, str] = {}  # the value of each parameter, kept while unticked
        self._text = ""
        self._edited = False
        self._filling = False
        self.itemChanged.connect(self._on_item_changed)

    # --- set up ------------------------------------------------------------------------

    def set_parameters(
        self, parameters: Sequence[tuple[str, str]], examples: Mapping[str, float]
    ) -> None:
        """The rows: (name, display name) of each parameter, and its example value."""
        self.names = [name for name, _label in parameters]
        self.labels = dict(parameters)
        self.examples = dict(examples)
        self._filling = True
        self.setRowCount(len(self.names))
        for row, name in enumerate(self.names):
            fix = QTableWidgetItem()
            fix.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            fix.setCheckState(Qt.CheckState.Unchecked)
            fix.setData(Qt.ItemDataRole.AccessibleTextRole, f"Fix {self.labels[name]}")
            label = QTableWidgetItem(self.labels[name])
            label.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.setItem(row, FIX, fix)
            self.setItem(row, PARAMETER, label)
            self.setItem(row, VALUE, QTableWidgetItem())
            self._show_value(row)
        self._filling = False
        self._update_free()
        self.updateGeometry()

    def set_text(self, text: str, fixed: Mapping[str, float] | None) -> None:
        """Show a stored ``fixed`` text with its values (None when it cannot be read: the
        rows stay unticked, and :meth:`text` returns ``text`` until an edit)."""
        self._text = text
        self._edited = False
        self._filling = True
        values = fixed or {}
        for row, name in enumerate(self.names):
            if name in values:
                self._typed[name] = value_text(values[name])
            fix = self.item(row, FIX)
            assert fix is not None
            fix.setCheckState(Qt.CheckState.Checked if name in values else Qt.CheckState.Unchecked)
            self._show_value(row)
        self._filling = False
        self._update_free()

    # --- reading -----------------------------------------------------------------------

    def is_fixed(self, name: str) -> bool:
        item = self.item(self.names.index(name), FIX)
        return item is not None and item.checkState() == Qt.CheckState.Checked

    def set_fixed(self, name: str, fixed: bool) -> None:
        """Tick or untick *Fix* for ``name``, as a click would."""
        item = self.item(self.names.index(name), FIX)
        assert item is not None
        if fixed and not item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            return  # the last free parameter
        item.setCheckState(Qt.CheckState.Checked if fixed else Qt.CheckState.Unchecked)

    def set_value(self, name: str, text: str) -> None:
        """Type ``text`` as the value of ``name`` (which must be fixed)."""
        item = self.item(self.names.index(name), VALUE)
        assert item is not None
        item.setText(text)

    def value(self, name: str) -> str:
        return self._typed.get(name, "") if self.is_fixed(name) else ""

    def text(self) -> str:
        """The ``fixed`` text: "Top = 100, Hill slope = 1" (the stored text until an edit)."""
        if not self._edited:
            return self._text
        pairs = [
            f"{self.labels[n]} = {self._typed.get(n, '')}" for n in self.names if self.is_fixed(n)
        ]
        return ", ".join(pairs)

    # --- reacting ----------------------------------------------------------------------

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling:
            return
        row, column = item.row(), item.column()
        name = self.names[row]
        if column == FIX:
            if item.checkState() == Qt.CheckState.Checked and name not in self._typed:
                self._typed[name] = value_text(self.examples.get(name, 0.0))
            self._filling = True
            self._show_value(row)
            self._filling = False
            self._update_free()
        elif column == VALUE:
            if not self.is_fixed(name):
                return
            self._typed[name] = item.text().strip()
        self._edited = True
        self.changed.emit()

    def _show_value(self, row: int) -> None:
        name = self.names[row]
        item = self.item(row, VALUE)
        assert item is not None
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if self.is_fixed(name):
            item.setText(self._typed.get(name, ""))
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsEditable
            )
            item.setData(Qt.ItemDataRole.ForegroundRole, None)
        else:
            item.setText("fitted")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setForeground(self.palette().brush(QPalette.ColorRole.PlaceholderText))
        item.setData(Qt.ItemDataRole.AccessibleTextRole, f"Value of {self.labels[name]}")

    def _update_free(self) -> None:
        """The last free parameter's *Fix* box cannot be ticked."""
        free = [n for n in self.names if not self.is_fixed(n)]
        self._filling = True
        for row, name in enumerate(self.names):
            item = self.item(row, FIX)
            assert item is not None
            last = free == [name]
            flags = (
                Qt.ItemFlag.ItemIsEnabled
                if last
                else (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            )
            item.setFlags(flags)
            item.setToolTip("At least one parameter must be fitted." if last else "")
        self._filling = False

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt API)
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self._filling = True
            for row in range(self.rowCount()):
                self._show_value(row)
            self._filling = False

    # --- size --------------------------------------------------------------------------

    def _height_for(self, rows: int) -> int:
        frame = 2 * self.frameWidth()
        header = self.horizontalHeader().sizeHint().height()
        row = self.verticalHeader().defaultSectionSize()
        return frame + header + rows * row

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        hint = super().sizeHint()
        hint.setHeight(self._height_for(max(len(self.names), 1)))
        return hint

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        hint = super().minimumSizeHint()
        hint.setHeight(self._height_for(min(len(self.names), 2)))
        return hint
