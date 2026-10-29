"""One open document: its model, file path and the widget shown in its tab."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from curvis.core import Document, UndoEvent
from curvis.settings.store import DECIMAL_PLACES_DEFAULT
from curvis.ui.table import TableModel, TableView


class DocumentTab(QWidget):
    """The tab of one document: the view of its first table, or a placeholder.

    ``message`` sends short notes for the status bar, such as why an edit was refused.
    """

    message = Signal(str)

    def __init__(
        self,
        document: Document,
        path: Path | None,
        parent: QWidget | None = None,
        *,
        decimal_places: int = DECIMAL_PLACES_DEFAULT,
    ) -> None:
        super().__init__(parent)
        self.document = document
        self.path = path
        self._decimal_places = decimal_places
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
            self.table_view = TableView(model, self)
            model.setParent(self.table_view)
            self.table_view.error.connect(self.message)
            self._layout.addWidget(self.table_view)
        self.placeholder.setVisible(self.table_view is None)
