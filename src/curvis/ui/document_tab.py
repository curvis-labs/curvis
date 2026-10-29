"""One open document: its model, file path and the widget shown in its tab."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from curvis.core import Document


class DocumentTab(QWidget):
    """The tab of one document. The table view replaces the placeholder in step 1.1."""

    def __init__(self, document: Document, path: Path | None, parent: QWidget | None = None):
        super().__init__(parent)
        self.document = document
        self.path = path
        self.placeholder = QLabel("This project has no tables yet.", self)
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setEnabled(False)
        layout = QVBoxLayout(self)
        layout.addWidget(self.placeholder)

    @property
    def title(self) -> str:
        """The file name without extension, or the document name for a new project."""
        return self.path.stem if self.path is not None else self.document.name

    @property
    def is_modified(self) -> bool:
        """True when there are changes since the last save (or since it was created)."""
        return not self.document.undo_stack.is_clean
