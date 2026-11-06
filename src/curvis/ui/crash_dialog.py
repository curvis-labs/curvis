"""The dialog shown when Curvis runs into a problem and the crash log is on."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from curvis.ui.dialogs import DialogSkeleton, Severity, secondary_label, size_simple

TITLE = "Curvis ran into a problem"
INTRO = (
    "Curvis saved a report on this computer. Nothing was sent. To help fix the problem, "
    "copy the report and send it to the developers. Save your work: Curvis may not "
    "work as expected until you restart it."
)
COPIED = "The report was copied to the clipboard."


class CrashDialog(QDialog):
    """Shows the crash report read-only, where it was saved, and *Copy to clipboard*."""

    def __init__(self, report: str, path: Path | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.report = report
        self.skeleton = DialogSkeleton(self, close_only=True)
        intro = QLabel(INTRO, self)
        intro.setWordWrap(True)
        self.text = QPlainTextEdit(report, self)
        self.text.setReadOnly(True)
        self.text.setAccessibleName("Crash report")
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.text.setMinimumHeight(12 * self.text.fontMetrics().height())
        self.where = secondary_label(f"Saved as {path}" if path is not None else "", self)
        self.where.setVisible(path is not None)
        self.skeleton.body.addWidget(intro)
        self.skeleton.body.addWidget(self.text, 1)
        self.skeleton.body.addWidget(self.where)
        self.copy_button = QPushButton("&Copy to clipboard", self)
        self.copy_button.setAutoDefault(False)
        self.copy_button.clicked.connect(self.copy)
        self.skeleton.add_secondary(self.copy_button)
        size_simple(self)
        self.resize(max(self.width(), 560), self.height())

    def copy(self) -> None:
        clipboard = QApplication.clipboard()
        clipboard.setText(self.report)
        self.skeleton.set_status(COPIED, Severity.INFO)
