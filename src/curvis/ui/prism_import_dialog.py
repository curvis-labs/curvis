"""The Prism import dialog: pick which data tables of a .pzfx or .prism file to import.

The file is read as a scheduler job. Every data table is listed, ticked, with its kind
and size ("XY, 2 series, 10 rows"); tables that cannot be imported are listed unticked
and greyed, with the reason. The panel on the right says how the selected table is
laid out in Curvis. A note says that analyses and graph styling are not imported. The
dialog only collects the choice; the main window adds the tables.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from curvis.compute import Job
from curvis.io.prism import NOT_IMPORTED_NOTE, ImportedTable, PrismData, read_prism
from curvis.ui.dialogs import (
    DialogSkeleton,
    Severity,
    columns,
    secondary_label,
    set_tab_order,
    size_workbench,
)

if TYPE_CHECKING:
    from curvis.compute import JobError, Scheduler

NOTHING_TICKED = "Tick at least one table to import."


class PrismImportDialog(QDialog):
    """The data tables of the Prism file at ``path``; :meth:`chosen` gives the ticked ones."""

    def __init__(self, path: Path, scheduler: Scheduler, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import Prism file")
        self.setObjectName("prismImportDialog")
        self.path = Path(path)
        self.data: PrismData | None = None
        self.error: str | None = None
        self._scheduler = scheduler
        self._node = f"prism-preview:{id(self)}"

        self.file_label = QLabel(self.path.name, self)
        self.file_label.setToolTip(str(self.path))
        self.file_label.setWordWrap(True)
        self.tables = QListWidget(self)
        self.tables.setObjectName("prismTables")
        self.tables.setAccessibleName("Data tables in the file")
        self.details = QLabel(self)
        self.details.setObjectName("prismTableDetails")
        self.details.setWordWrap(True)
        self.details.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.note = secondary_label(NOT_IMPORTED_NOTE, self)

        self.skeleton = DialogSkeleton(self, "Import")
        assert self.skeleton.ok_button is not None
        self.ok_button = self.skeleton.ok_button
        self.status = self.skeleton.feedback
        left, right = columns(self.skeleton.body, 1, 1)
        tables_box = QGroupBox("&Tables", self)
        tables_layout = QVBoxLayout(tables_box)
        tables_layout.addWidget(self.file_label)
        tables_layout.addWidget(self.tables, 1)
        tables_layout.addWidget(self.note)
        left.addWidget(tables_box, 1)
        details_box = QGroupBox("In Curvis", self)
        details_layout = QVBoxLayout(details_box)
        details_layout.addWidget(self.details)
        details_layout.addStretch(1)
        right.addWidget(details_box, 1)
        self.skeleton.set_problem("Reading the file…")
        set_tab_order([self.tables])
        self.tables.setFocus()
        size_workbench(self)

        self.tables.itemChanged.connect(self._update_problem)
        self.tables.currentItemChanged.connect(self._show_details)
        scheduler.finished.connect(self._on_job_finished)
        scheduler.failed.connect(self._on_job_failed)
        job = Job(read_prism, (str(self.path),))
        scheduler.submit(self._node, job, delay_ms=0)

    def chosen(self) -> list[ImportedTable]:
        """The ticked tables, in the file's order."""
        if self.data is None:
            return []
        picked: list[ImportedTable] = []
        for row in range(self.tables.count()):
            item = self.tables.item(row)
            index = item.data(Qt.ItemDataRole.UserRole)
            if index is not None and item.checkState() == Qt.CheckState.Checked:
                picked.append(self.data.tables[index])
        return picked

    def done(self, result: int) -> None:
        self._scheduler.cancel(self._node)
        self._scheduler.finished.disconnect(self._on_job_finished)
        self._scheduler.failed.disconnect(self._on_job_failed)
        super().done(result)

    # --- results ---------------------------------------------------------------------

    def _on_job_finished(self, node: str, result: object) -> None:
        if node != self._node or not isinstance(result, PrismData):
            return
        self.data = result
        self.error = None
        self.tables.blockSignals(True)
        self.tables.clear()
        for index, table in enumerate(result.tables):
            item = QListWidgetItem(f"{table.title}  ({table.summary})", self.tables)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setData(Qt.ItemDataRole.ToolTipRole, _details(table))
        for skipped in result.skipped:
            item = QListWidgetItem(f"{skipped.title}  (not imported)", self.tables)
            item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            item.setData(Qt.ItemDataRole.UserRole, None)
            item.setData(Qt.ItemDataRole.ToolTipRole, skipped.reason)
            item.setForeground(self.palette().placeholderText())
        self.tables.blockSignals(False)
        if self.tables.count():
            self.tables.setCurrentRow(0)
        self._update_problem()

    def _on_job_failed(self, node: str, error: JobError) -> None:
        if node != self._node:
            return
        self.data = None
        if error.error_type == "PrismImportError":
            self.error = error.message
        else:
            self.error = f"The file could not be read: {error.message}"
        self.tables.clear()
        self.details.clear()
        self.skeleton.set_problem(self.error, Severity.ERROR)

    def _update_problem(self, *_args: object) -> None:
        if self.data is None:
            return
        if not self.data.tables:
            self.skeleton.set_problem("None of the tables in this file can be imported.")
            return
        picked = len(self.chosen())
        if not picked:
            self.skeleton.set_problem(NOTHING_TICKED)
            return
        self.skeleton.set_problem(None)
        tables = f"{picked} table{'s' if picked != 1 else ''}"
        skipped = len(self.data.skipped)
        text = f"{tables} will be added to the project."
        if skipped:
            text += f" {skipped} cannot be imported."
        self.skeleton.set_status(text)

    def _show_details(self, item: QListWidgetItem | None, _previous: object = None) -> None:
        if item is None:
            self.details.clear()
            return
        self.details.setText(str(item.data(Qt.ItemDataRole.ToolTipRole) or ""))


def _details(table: ImportedTable) -> str:
    """What the right-hand panel says about a table that imports."""
    lines = [f"{table.summary}."]
    lines += list(table.notes)
    return "\n\n".join(lines)
