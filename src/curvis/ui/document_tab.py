"""One open document: its model, file path and the widget shown in its tab."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout, QWidget

from curvis.core import CommandError, Document, UndoEvent
from curvis.core.constants import ProjectConstant
from curvis.settings.store import DECIMAL_PLACES_DEFAULT
from curvis.ui.table import TableModel, TableView

if TYPE_CHECKING:
    from curvis.compute import Scheduler
    from curvis.compute.calc_engine import CalcEngine
    from curvis.ui.calculate_dialog import CalculateDialog


class DocumentTab(QWidget):
    """The tab of one document: the view of its first table, or a placeholder.

    ``message`` sends short notes for the status bar, such as why an edit was refused.
    With a ``scheduler``, the tab keeps the document's calculated columns up to date.
    """

    message = Signal(str)
    manage_constants_requested = Signal()

    def __init__(
        self,
        document: Document,
        path: Path | None,
        parent: QWidget | None = None,
        *,
        decimal_places: int = DECIMAL_PLACES_DEFAULT,
        scheduler: Scheduler | None = None,
        global_constants: Callable[[], Sequence[ProjectConstant]] = list,
    ) -> None:
        super().__init__(parent)
        self.document = document
        self.path = path
        self._decimal_places = decimal_places
        self._global_constants = global_constants
        self.engine: CalcEngine | None = None
        if scheduler is not None:
            from curvis.compute.calc_engine import CalcEngine

            self.engine = CalcEngine(document, scheduler, global_constants, self)
            self.engine.values_changed.connect(self._on_values_changed)
        self.table_view: TableView | None = None
        self.placeholder = QLabel("This project has no tables yet.", self)
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setEnabled(False)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(self.placeholder)
        self._show_first_table()
        self._unsubscribe = document.undo_stack.subscribe(self._on_undo_event)
        self.destroyed.connect(lambda *_: self._unsubscribe())

    @property
    def title(self) -> str:
        """The file name without extension, or the document name for a new project."""
        return self.path.stem if self.path is not None else self.document.name

    @property
    def is_modified(self) -> bool:
        """True when there are changes since the last save (or since it was created)."""
        return not self.document.undo_stack.is_clean

    def set_decimal_places(self, places: int) -> None:
        self._decimal_places = places
        if self.table_view is not None:
            self.table_view.model().set_decimal_places(places)

    def close_engine(self) -> None:
        """Stop recomputing (the tab is closing)."""
        if self.engine is not None:
            self.engine.close()
            self.engine = None

    # --- calculations ----------------------------------------------------------------------

    def calculate(self) -> bool:
        """Open the Calculate dialog for the shown table; True if columns were added."""
        if self.table_view is None:
            return False
        from curvis.ui.calculate_dialog import CalculateDialog, selected_sources

        model = self.table_view.model()
        selected = [c.id for c in model.columns_at(self.table_view.selected_columns())]
        dialog = CalculateDialog(
            self.document,
            model.table_id,
            selected=selected_sources(model.table, selected),
            global_constants=self._global_constants(),
            manage_constants=self.manage_constants_requested.emit,
            parent=self,
        )
        return self._run_dialog(dialog, editing=False)

    def edit_calculation(self, column_id: str | None = None) -> bool:
        """Reopen the Calculate dialog for the calculation of ``column_id`` (default: current)."""
        if self.table_view is None:
            return False
        column_id = column_id or self.table_view.current_calculated_column()
        calculation = self.document.calculation_of(column_id) if column_id else None
        if calculation is None:
            self.message.emit("This column is not calculated.")
            return False
        from curvis.ui.calculate_dialog import CalculateDialog

        dialog = CalculateDialog(
            self.document,
            calculation.table_id,
            calculation=calculation,
            global_constants=self._global_constants(),
            manage_constants=self.manage_constants_requested.emit,
            parent=self,
        )
        return self._run_dialog(dialog, editing=True)

    def _run_dialog(self, dialog: CalculateDialog, *, editing: bool) -> bool:
        from curvis.core import add_calculation, edit_calculation

        try:
            if self._exec_dialog(dialog) != QDialog.DialogCode.Accepted:
                return False
            calculation = dialog.calculation()
            try:
                build = edit_calculation if editing else add_calculation
                self.document.execute(build(self.document, calculation))
            except CommandError as exc:
                self.message.emit(str(exc))
                return False
            return True
        finally:
            dialog.deleteLater()

    def _exec_dialog(self, dialog: QDialog) -> int:
        """Show ``dialog`` modally (tests replace this)."""
        return dialog.exec()

    def _on_values_changed(self, table_id: str) -> None:
        if self.table_view is not None and self.table_view.model().table_id == table_id:
            self.table_view.model().refresh_values()

    # --- table view ------------------------------------------------------------------------

    def _shown_table_id(self) -> str | None:
        return self.table_view.model().table_id if self.table_view is not None else None

    def _on_undo_event(self, _event: UndoEvent) -> None:
        first = self.document.tables[0].id if self.document.tables else None
        if first != self._shown_table_id():
            self._show_first_table()

    def _show_first_table(self) -> None:
        if self.table_view is not None:
            self.table_view.model().detach()
            self._layout.removeWidget(self.table_view)
            self.table_view.deleteLater()
            self.table_view = None
        if self.document.tables:
            model = TableModel(
                self.document, self.document.tables[0].id, decimal_places=self._decimal_places
            )
            if self.engine is not None:
                model.set_calculation_errors(self.engine.errors.get)
            self.table_view = TableView(model, self)
            model.setParent(self.table_view)
            self.table_view.error.connect(self.message)
            self.table_view.calculate_requested.connect(self.calculate)
            self.table_view.edit_calculation_requested.connect(self.edit_calculation)
            self._layout.addWidget(self.table_view)
        self.placeholder.setVisible(self.table_view is None)
