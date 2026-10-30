"""The Constants dialog: add, edit and delete project and global constants.

Project constants are saved in the project and changed through undoable commands.
Global constants are saved in the settings and available in every project; a project
constant with the same name wins. Built-in constants are listed for reference.
"""

from __future__ import annotations

import math
from enum import StrEnum

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from curvis.core import AddConstant, CommandError, Document, EditConstant, RemoveConstant
from curvis.core.constants import ProjectConstant
from curvis.formula import BUILTIN_CONSTANTS, constant_name_problem
from curvis.settings import Settings
from curvis.ui.dialogs import (
    SPACING,
    DialogSkeleton,
    EmptyState,
    Severity,
    form_layout,
    secondary_label,
    size_simple,
)

_ID = Qt.ItemDataRole.UserRole


class Scope(StrEnum):
    PROJECT = "project"
    GLOBAL = "global"


class ConstantEditor(QDialog):
    """Ask for a constant's name, value and unit."""

    def __init__(
        self, title: str, constant: ProjectConstant | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.skeleton = DialogSkeleton(self, "Save" if constant else "Add")
        self.name = QLineEdit(constant.name if constant else "")
        self.value = QLineEdit(repr(constant.value) if constant else "")
        self.unit = QLineEdit(constant.unit if constant else "")
        self.unit.setPlaceholderText("Optional, e.g. 1/s")
        form = form_layout()
        form.addRow("&Name", self.name)
        form.addRow("&Value", self.value)
        form.addRow("&Unit", self.unit)
        self.skeleton.body.addLayout(form)
        self.skeleton.body.addStretch(1)
        assert self.skeleton.ok_button is not None
        self.ok = self.skeleton.ok_button
        for edit in (self.name, self.value):
            edit.textChanged.connect(self._validate)
        self._validate()
        size_simple(self)

    def number(self) -> float | None:
        try:
            value = float(self.value.text().strip().replace(",", "."))
        except ValueError:
            return None
        return value if math.isfinite(value) else None

    def _validate(self) -> None:
        name = self.name.text().strip()
        problem = constant_name_problem(name) if name else "Enter a name."
        if problem is None and self.number() is None:
            problem = "Enter a number for the value."
        self.skeleton.set_problem(problem)

    @property
    def problem(self) -> str:
        """Why Add or Save is disabled, or ""."""
        return self.skeleton.feedback.text()


class ConstantsDialog(QDialog):
    """Manage the constants of ``document`` (may be None) and the global ones in ``settings``."""

    def __init__(
        self, document: Document | None, settings: Settings, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.document = document
        self.settings = settings
        self.setWindowTitle("Constants")
        self.skeleton = DialogSkeleton(self, close_only=True)
        body = self.skeleton.body
        intro = QLabel(
            "Use constants in formulas by name, e.g. [Rate] * k_on. A project constant "
            "overrides a global one with the same name."
        )
        intro.setWordWrap(True)
        body.addWidget(intro)
        self.tabs = QTabWidget()
        self.tabs.tabBar().setAccessibleName("Kind of constants")
        body.addWidget(self.tabs, 1)
        self.tables: dict[Scope, QTableWidget] = {}
        if document is not None:
            self.tabs.addTab(self._scope_page(Scope.PROJECT), "&Project")
        self.tabs.addTab(self._scope_page(Scope.GLOBAL), "&Global")
        self.tabs.addTab(self._builtin_page(), "&Built-in")
        self.error = self.skeleton.feedback  # why the last change was refused
        self.setMinimumSize(460, 340)
        self.resize(560, 420)
        self.refresh()

    # --- pages -----------------------------------------------------------------------------

    def _scope_page(self, scope: Scope) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SPACING, SPACING, SPACING, SPACING)
        layout.setSpacing(SPACING)
        table = _constants_table()
        table.setAccessibleName(f"{scope.value.capitalize()} constants")
        table.itemDoubleClicked.connect(lambda *_: self._edit_selected(scope))
        self.tables[scope] = table
        layout.addWidget(table, 1)
        EmptyState(table, f"No {scope.value} constants yet. Click Add to make one.")
        buttons = QHBoxLayout()
        add = QPushButton("&Add…")
        edit = QPushButton("&Edit…")
        delete = QPushButton("&Delete")
        add.clicked.connect(lambda: self._add(scope))
        edit.clicked.connect(lambda: self._edit_selected(scope))
        delete.clicked.connect(lambda: self._delete_selected(scope))
        for button in (add, edit, delete):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        if scope is Scope.GLOBAL:
            layout.addWidget(
                secondary_label("Global constants are saved on this computer, for every project.")
            )
        return page

    def _builtin_page(self) -> QWidget:
        table = _constants_table(description=True)
        table.setAccessibleName("Built-in constants")
        table.setRowCount(len(BUILTIN_CONSTANTS))
        for row, constant in enumerate(BUILTIN_CONSTANTS):
            values = (constant.name, f"{constant.value:.10g}", constant.unit, constant.description)
            for col, text in enumerate(values):
                table.setItem(row, col, QTableWidgetItem(text))
        table.resizeColumnsToContents()
        return table

    # --- reading ---------------------------------------------------------------------------

    def constants(self, scope: Scope) -> list[ProjectConstant]:
        if scope is Scope.PROJECT:
            return list(self.document.constants) if self.document is not None else []
        return self.settings.global_constants

    def refresh(self) -> None:
        for scope, table in self.tables.items():
            constants = self.constants(scope)
            table.setRowCount(len(constants))
            for row, constant in enumerate(constants):
                for col, text in enumerate((constant.name, repr(constant.value), constant.unit)):
                    item = QTableWidgetItem(text)
                    item.setData(_ID, constant.id)
                    table.setItem(row, col, item)
            table.resizeColumnsToContents()

    # --- changing (public, so tests can call them directly) ----------------------------------

    def add(self, scope: Scope, name: str, value: float, unit: str = "") -> bool:
        return self._apply(scope, None, ProjectConstant(name, value, unit))

    def edit(self, scope: Scope, constant_id: str, name: str, value: float, unit: str = "") -> bool:
        return self._apply(scope, constant_id, ProjectConstant(name, value, unit, constant_id))

    def delete(self, scope: Scope, constant_id: str) -> bool:
        if scope is Scope.PROJECT:
            assert self.document is not None
            return self._run(RemoveConstant(constant_id))
        self.settings.global_constants = [
            c for c in self.settings.global_constants if c.id != constant_id
        ]
        self.refresh()
        return True

    def _apply(self, scope: Scope, constant_id: str | None, constant: ProjectConstant) -> bool:
        if scope is Scope.PROJECT:
            assert self.document is not None
            if constant_id is None:
                return self._run(AddConstant(constant))
            return self._run(
                EditConstant(constant_id, constant.name, constant.value, constant.unit)
            )
        problem = constant_name_problem(constant.name)
        others = [c for c in self.settings.global_constants if c.id != constant_id]
        if problem is None and any(c.name == constant.name for c in others):
            problem = f"There is already a global constant called '{constant.name}'."
        if problem is not None:
            self.error.show_message(problem, Severity.ERROR)
            return False
        constants = self.settings.global_constants
        if constant_id is None:
            constants.append(constant)
        else:
            constants = [constant if c.id == constant_id else c for c in constants]
        self.settings.global_constants = constants
        self.error.clear()
        self.refresh()
        return True

    def _run(self, command: AddConstant | EditConstant | RemoveConstant) -> bool:
        assert self.document is not None
        try:
            self.document.execute(command)
        except CommandError as exc:
            self.error.show_message(str(exc), Severity.ERROR)
            return False
        self.error.clear()
        self.refresh()
        return True

    # --- buttons ---------------------------------------------------------------------------

    def _selected_id(self, scope: Scope) -> str | None:
        table = self.tables[scope]
        item = table.item(table.currentRow(), 0) if table.currentRow() >= 0 else None
        return None if item is None else str(item.data(_ID))

    def _add(self, scope: Scope) -> None:
        answer = self._ask("Add constant", None)
        if answer is not None:
            self.add(scope, *answer)

    def _edit_selected(self, scope: Scope) -> None:
        constant_id = self._selected_id(scope)
        if constant_id is None:
            return
        current = next(c for c in self.constants(scope) if c.id == constant_id)
        answer = self._ask("Edit constant", current)
        if answer is not None:
            self.edit(scope, constant_id, *answer)

    def _delete_selected(self, scope: Scope) -> None:
        constant_id = self._selected_id(scope)
        if constant_id is not None:
            self.delete(scope, constant_id)

    def _ask(self, title: str, constant: ProjectConstant | None) -> tuple[str, float, str] | None:
        editor = ConstantEditor(title, constant, self)
        try:
            if editor.exec() != QDialog.DialogCode.Accepted:
                return None
            value = editor.number()
            assert value is not None
            return editor.name.text().strip(), value, editor.unit.text().strip()
        finally:
            editor.deleteLater()


def _constants_table(description: bool = False) -> QTableWidget:
    headers = ["Name", "Value", "Unit"] + (["Description"] if description else [])
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setStretchLastSection(True)
    return table
