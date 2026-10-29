"""Bulk rename: rename many columns, replicate groups and categories in one step.

The dialog lists the table's categories, groups and columns as a tree. Ticked items
get new names, either by find and replace or from a numbered pattern ("Rep #" gives
Rep 1, Rep 2, …; each kind is numbered on its own). A preview shows the new names
and why they are not allowed, if they are not. The dialog only collects names; the
table view sends them as one command.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from curvis.data import NodeKind, Table, name_error
from curvis.data.layout import Node, layout_tree
from curvis.data.rename import NUMBER_MARK, find_replace, numbered_name

_KIND_LABELS = {
    NodeKind.CATEGORY: "Category",
    NodeKind.GROUP: "Replicate group",
    NodeKind.COLUMN: "Column",
}


@dataclass(frozen=True)
class RenameItem:
    """A column, replicate group or category that can be renamed."""

    kind: NodeKind
    id: str
    name: str


@dataclass(frozen=True)
class Renames:
    """New names by id, for :class:`~curvis.core.BulkRename`."""

    columns: dict[str, str]
    groups: dict[str, str]
    categories: dict[str, str]

    def __bool__(self) -> bool:
        return bool(self.columns or self.groups or self.categories)


class BulkRenameDialog(QDialog):
    """Pick items and a renaming rule; :meth:`renames` gives the result."""

    def __init__(
        self, table: Table, checked_columns: Iterable[str] = (), parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bulk rename")
        self.setObjectName("bulkRenameDialog")
        self.table = table
        checked = set(checked_columns) or {c.id for c in table.columns}

        self.find_mode = QRadioButton("&Find and replace", self)
        self.number_mode = QRadioButton("&Numbered names", self)
        self.find_mode.setChecked(True)
        modes = QHBoxLayout()
        modes.addWidget(self.find_mode)
        modes.addWidget(self.number_mode)
        modes.addStretch()

        self.find_edit = QLineEdit(self)
        self.replace_edit = QLineEdit(self)
        self.match_case = QCheckBox("Match &case", self)
        find_page = QWidget(self)
        find_form = QFormLayout(find_page)
        find_form.setContentsMargins(0, 0, 0, 0)
        find_form.addRow("Find:", self.find_edit)
        find_form.addRow("Replace with:", self.replace_edit)
        find_form.addRow("", self.match_case)

        self.pattern_edit = QLineEdit(self)
        self.pattern_edit.setPlaceholderText(f"e.g. Replicate {NUMBER_MARK}")
        self.pattern_edit.setToolTip(
            f"{NUMBER_MARK} is replaced by the number; {NUMBER_MARK * 2} pads it to two digits."
        )
        self.start_spin = QSpinBox(self)
        self.start_spin.setRange(-1_000_000, 1_000_000)
        self.start_spin.setValue(1)
        self.step_spin = QSpinBox(self)
        self.step_spin.setRange(-1000, 1000)
        self.step_spin.setValue(1)
        number_page = QWidget(self)
        number_form = QFormLayout(number_page)
        number_form.setContentsMargins(0, 0, 0, 0)
        number_form.addRow("Pattern:", self.pattern_edit)
        number_form.addRow("Start at:", self.start_spin)
        number_form.addRow("Step:", self.step_spin)

        self.pages = QStackedWidget(self)
        self.pages.addWidget(find_page)
        self.pages.addWidget(number_page)

        self.tree = QTreeWidget(self)
        self.tree.setObjectName("bulkRenameItems")
        self.tree.setHeaderLabels(["Name", "New name", "Kind"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.items: list[tuple[RenameItem, QTreeWidgetItem]] = []
        self._fill_tree(checked)
        self.tree.expandAll()

        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Rename")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(modes)
        layout.addWidget(self.pages)
        layout.addWidget(QLabel("Tick what to rename:", self))
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.buttons)
        self.resize(520, 480)

        for edit in (self.find_edit, self.replace_edit, self.pattern_edit):
            edit.textChanged.connect(self._update)
        for spin in (self.start_spin, self.step_spin):
            spin.valueChanged.connect(self._update)
        self.match_case.toggled.connect(self._update)
        self.number_mode.toggled.connect(self._on_mode_changed)
        self.tree.itemChanged.connect(self._update)
        self.find_edit.setFocus()
        self._update()

    # --- items -------------------------------------------------------------------------

    def _fill_tree(self, checked_columns: set[str]) -> None:
        table = self.table

        def name_of(node: Node) -> str:
            if node.kind is NodeKind.CATEGORY:
                return table.category_group(node.id).name
            if node.kind is NodeKind.GROUP:
                return table.replicate_group(node.id).name
            return table.column(node.id).name

        def add(node: Node, parent: QTreeWidget | QTreeWidgetItem) -> None:
            item = RenameItem(node.kind, node.id, name_of(node))
            widget = QTreeWidgetItem(parent, [item.name, item.name, _KIND_LABELS[node.kind]])
            widget.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            is_checked = node.kind is NodeKind.COLUMN and node.id in checked_columns
            widget.setCheckState(
                0, Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked
            )
            self.items.append((item, widget))
            for child in node.children:
                add(child, widget)

        for node in layout_tree(table):
            add(node, self.tree)

    def set_checked(self, kind: NodeKind, ids: Iterable[str] | None = None) -> None:
        """Tick the items of ``kind`` with ``ids`` (all of that kind if None); untick others."""
        wanted = None if ids is None else set(ids)
        self.tree.blockSignals(True)
        for item, widget in self.items:
            on = item.kind is kind and (wanted is None or item.id in wanted)
            widget.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)
        self._update()

    def checked_items(self) -> list[RenameItem]:
        """The ticked items, in tree order."""
        return [i for i, w in self.items if w.checkState(0) == Qt.CheckState.Checked]

    # --- names -------------------------------------------------------------------------

    def new_names(self) -> dict[str, str]:
        """New name by id for every ticked item (unchanged names included)."""
        checked = self.checked_items()
        if self.number_mode.isChecked():
            pattern = self.pattern_edit.text()
            if not pattern.strip():
                return {}
            start, step = self.start_spin.value(), self.step_spin.value()
            numbers = dict.fromkeys(NodeKind, start)
            names: dict[str, str] = {}
            for item in checked:
                names[item.id] = numbered_name(pattern, numbers[item.kind])
                numbers[item.kind] += step
            return names
        find, replace = self.find_edit.text(), self.replace_edit.text()
        case = self.match_case.isChecked()
        return {i.id: find_replace(i.name, find, replace, match_case=case) for i in checked}

    def renames(self) -> Renames:
        """The names that change, by kind and id."""
        new = self.new_names()
        result = Renames({}, {}, {})
        target = {
            NodeKind.COLUMN: result.columns,
            NodeKind.GROUP: result.groups,
            NodeKind.CATEGORY: result.categories,
        }
        for item, _ in self.items:
            name = new.get(item.id)
            if name is not None and name != item.name:
                target[item.kind][item.id] = name
        return result

    def error(self) -> str | None:
        """Why the new names are not allowed, or None."""
        r = self.renames()
        return name_error(self.table, r.columns, r.groups, r.categories)

    def _on_mode_changed(self) -> None:
        numbered = self.number_mode.isChecked()
        self.pages.setCurrentIndex(1 if numbered else 0)
        (self.pattern_edit if numbered else self.find_edit).setFocus()
        self._update()

    def _update(self) -> None:
        new = self.new_names()
        self.tree.blockSignals(True)
        for item, widget in self.items:
            name = new.get(item.id, item.name)
            widget.setText(1, name)
            font = widget.font(1)
            font.setBold(name != item.name)
            widget.setFont(1, font)
        self.tree.blockSignals(False)
        renames = self.renames()
        error = self.error() if renames else None
        n = len(renames.columns) + len(renames.groups) + len(renames.categories)
        if error:
            self.status.setText(error)
        elif n == 0:
            self.status.setText("No names change yet.")
        else:
            self.status.setText("1 name will change." if n == 1 else f"{n} names will change.")
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(n > 0 and error is None)
