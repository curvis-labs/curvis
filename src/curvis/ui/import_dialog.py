"""The Import dialog: preview a CSV or TSV file and choose how to read it.

Each change of an option reads the file again as a scheduler job, and the preview shows
the rows as the import would split them: rows above the header dimmed, then the first
data rows of the row range. The header row itself is not repeated in the grid: each
imported column's name and role are in the column header (columns outside the column
range are dimmed). Rows are numbered as in the file. The dialog only collects options;
the main window runs the import.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from curvis.compute import Job
from curvis.io.importers import (
    DELIMITER_NAMES,
    PREVIEW_ROWS,
    CsvOptions,
    CsvPreview,
    Delimiter,
    read_preview,
)

if TYPE_CHECKING:
    from curvis.compute import JobError, Scheduler

type _Index = QModelIndex | QPersistentModelIndex

HEADER_ROW_MAX = 1_000_000
ROW_MAX = 100_000_000
COLUMN_MAX = 100_000


class PreviewModel(QAbstractTableModel):
    """The rows of a :class:`CsvPreview` except the header row, read-only."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.preview: CsvPreview | None = None
        self._rows: list[int] = []  # index in preview.rows of each row shown
        self._columns = 0

    def set_preview(self, preview: CsvPreview | None) -> None:
        self.beginResetModel()
        self.preview = preview
        self._rows = []
        self._columns = 0
        if preview is not None:
            self._rows = [i for i in range(len(preview.rows)) if i != preview.header_index]
            widest = max((len(preview.rows[i]) for i in self._rows), default=0)
            self._columns = max(widest, len(preview.names))
        self.endResetModel()

    def rowCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: B008, N802 (Qt API)
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: B008, N802 (Qt API)
        return 0 if parent.isValid() else self._columns

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        preview = self.preview
        if preview is None or not index.isValid():
            return None
        row, col = self._rows[index.row()], index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            record = preview.rows[row]
            return record[col] if col < len(record) else ""
        skipped = row < preview.data_start
        in_range = preview.column_start <= col < preview.column_start + len(preview.names)
        if role == Qt.ItemDataRole.ForegroundRole and (skipped or not in_range):
            return QApplication.palette().brush(QPalette.ColorRole.PlaceholderText)
        return None

    def headerData(  # noqa: N802 (Qt API)
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if role != Qt.ItemDataRole.DisplayRole or self.preview is None:
            return None
        if orientation == Qt.Orientation.Vertical:
            return str(self.preview.row_numbers[self._rows[section]])
        column = section - self.preview.column_start
        if 0 <= column < len(self.preview.names):
            return f"{self.preview.names[column]}\n{self.preview.roles[column].value}"
        return ""


class ImportDialog(QDialog):
    """Options for importing ``path``, with a live preview; :meth:`options` gives them."""

    def __init__(self, path: Path, scheduler: Scheduler, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import data")
        self.setObjectName("importDialog")
        self.resize(760, 560)
        self.path = Path(path)
        self.preview: CsvPreview | None = None
        self.error: str | None = None
        self._scheduler = scheduler
        self._node = f"import-preview:{id(self)}"
        self._decimal_comma: bool | None = None  # None until the user ticks or unticks it

        self.file_label = QLabel(self.path.name, self)
        self.file_label.setToolTip(str(self.path))

        self.delimiter = QComboBox(self)
        self.delimiter.addItem("Auto-detect", None)
        for delimiter, name in DELIMITER_NAMES.items():
            self.delimiter.addItem(name, delimiter)

        self.header_row = QSpinBox(self)
        self.header_row.setRange(1, HEADER_ROW_MAX)
        self.header_row.setToolTip(
            "The row that holds the column names. Rows above it are skipped."
        )
        self.no_header = QCheckBox("&No header row", self)
        self.no_header.setToolTip("Name the columns A, B, C, …")
        header_box = QHBoxLayout()
        header_box.addWidget(self.header_row)
        header_box.addWidget(self.no_header)
        header_box.addStretch()

        self.first_column_is_x = QCheckBox("&First column is X", self)
        self.first_column_is_x.setToolTip(
            "The first number column in the column range becomes the X column."
        )

        # 0 is shown as "Last": to the end of the file, whatever its size.
        # The first row starts at the first data row and follows the header row.
        self.first_row = _spin_box(self, 2, ROW_MAX)
        self.first_row.setToolTip("The first row to import, numbered as in the file.")
        self.last_row = _spin_box(self, 0, ROW_MAX, "Last")
        self.last_row.setToolTip("The last row to import, numbered as in the file.")
        self.first_column = _spin_box(self, 1, COLUMN_MAX)
        self.first_column.setToolTip("The first column to import; column 1 is the first.")
        self.last_column = _spin_box(self, 0, COLUMN_MAX, "Last")
        self.last_column.setToolTip("The last column to import.")

        self.advanced_toggle = QToolButton(self)
        self.advanced_toggle.setText("Advanced")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_toggle.setAutoRaise(True)
        self.decimal_comma = QCheckBox("Decimal &comma (1,5 means 1.5)", self)
        self.decimal_comma.setToolTip("On by default when the delimiter is a semicolon.")
        self.advanced = QWidget(self)
        advanced_layout = QVBoxLayout(self.advanced)
        advanced_layout.setContentsMargins(16, 0, 0, 0)
        advanced_layout.addWidget(self.decimal_comma)
        self.advanced.setVisible(False)

        form = QFormLayout()
        form.addRow("File", self.file_label)
        form.addRow("&Delimiter", self.delimiter)
        header_label = QLabel("&Header row", self)
        header_label.setBuddy(self.header_row)  # a layout row gets no buddy by itself
        form.addRow(header_label, header_box)
        rows_label = QLabel("&Rows", self)
        rows_label.setBuddy(self.first_row)
        form.addRow(rows_label, _range_box(self, self.first_row, self.last_row))
        columns_label = QLabel("C&olumns", self)
        columns_label.setBuddy(self.first_column)
        form.addRow(columns_label, _range_box(self, self.first_column, self.last_column))
        form.addRow("", self.first_column_is_x)

        self.model = PreviewModel(self)
        self.table = QTableView(self)
        self.table.setObjectName("importPreview")
        self.table.setModel(self.model)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.status = QLabel("Reading the file…", self)
        self.status.setWordWrap(True)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Import")
        self.ok_button.setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.advanced_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.advanced)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.buttons)

        self.delimiter.currentIndexChanged.connect(self._on_delimiter_changed)
        self.header_row.valueChanged.connect(self._on_header_changed)
        self.no_header.toggled.connect(self._on_no_header_toggled)
        self.first_column_is_x.toggled.connect(self._refresh)
        for spin in (self.first_row, self.last_row, self.first_column, self.last_column):
            spin.valueChanged.connect(self._refresh)
        self.decimal_comma.clicked.connect(self._on_decimal_comma_clicked)
        self.advanced_toggle.toggled.connect(self._on_advanced_toggled)
        scheduler.finished.connect(self._on_job_finished)
        scheduler.failed.connect(self._on_job_failed)
        self._refresh(delay_ms=0)

    def options(self) -> CsvOptions:
        """The options as set in the dialog."""
        delimiter: Delimiter | None = self.delimiter.currentData()
        return CsvOptions(
            delimiter=delimiter,
            header_row=None if self.no_header.isChecked() else self.header_row.value(),
            first_column_is_x=self.first_column_is_x.isChecked(),
            decimal_comma=self._decimal_comma,
            first_row=self._first_row(),
            last_row=self.last_row.value() or None,
            first_column=self.first_column.value(),
            last_column=self.last_column.value() or None,
        )

    def done(self, result: int) -> None:
        self._scheduler.cancel(self._node)
        self._scheduler.finished.disconnect(self._on_job_finished)
        self._scheduler.failed.disconnect(self._on_job_failed)
        super().done(result)

    # --- options ---------------------------------------------------------------------

    def _refresh(self, *_args: object, delay_ms: int | None = None) -> None:
        """Read the preview again with the current options (debounced)."""
        job = Job(read_preview, (str(self.path), self.options(), PREVIEW_ROWS))
        self._scheduler.submit(self._node, job, delay_ms=delay_ms)

    def _on_delimiter_changed(self, _index: int) -> None:
        self._decimal_comma = None  # back to the default for the new delimiter
        self.decimal_comma.setEnabled(self.delimiter.currentData() != ",")
        self._refresh()

    def _on_header_changed(self, _value: int) -> None:
        self._follow_header()
        self._refresh()

    def _on_no_header_toggled(self, checked: bool) -> None:
        self.header_row.setEnabled(not checked)
        self._follow_header()
        self._refresh()

    def _data_row(self) -> int:
        """The first row under the header (row 1 without one)."""
        return 1 if self.no_header.isChecked() else self.header_row.value() + 1

    def _first_row(self) -> int | None:
        """The first row option: ``None`` when it is the first data row anyway."""
        value = self.first_row.value()
        return value if value > self._data_row() else None

    def _follow_header(self) -> None:
        """Keep the first row at the first data row, unless the user moved it further."""
        spin = self.first_row
        at_start = spin.value() == spin.minimum()
        spin.blockSignals(True)  # one refresh for the header change is enough
        spin.setMinimum(self._data_row())
        if at_start:
            spin.setValue(spin.minimum())
        spin.blockSignals(False)

    def _on_decimal_comma_clicked(self, checked: bool) -> None:
        self._decimal_comma = checked
        self._refresh()

    def _on_advanced_toggled(self, shown: bool) -> None:
        self.advanced.setVisible(shown)
        arrow = Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow
        self.advanced_toggle.setArrowType(arrow)

    # --- results ---------------------------------------------------------------------

    def _on_job_finished(self, node: str, result: object) -> None:
        if node != self._node or not isinstance(result, CsvPreview):
            return
        self.preview = result
        self.error = None
        self.model.set_preview(result)
        self.delimiter.setItemText(0, f"Auto-detect ({DELIMITER_NAMES[result.delimiter]})")
        self.decimal_comma.setEnabled(result.delimiter != ",")
        self.decimal_comma.setChecked(result.decimal_comma)
        self.status.setText(_describe(result))
        self.ok_button.setEnabled(True)

    def _on_job_failed(self, node: str, error: JobError) -> None:
        if node != self._node:
            return
        self.preview = None
        if error.error_type == "CsvImportError":
            self.error = error.message
        else:
            self.error = f"The file could not be read: {error.message}"
        self.model.set_preview(None)
        self.delimiter.setItemText(0, "Auto-detect")
        self.status.setText(self.error)
        self.ok_button.setEnabled(False)


def _spin_box(parent: QWidget, low: int, high: int, special: str = "") -> QSpinBox:
    """A spin box from ``low`` to ``high``; ``special`` is shown for ``low``."""
    spin = QSpinBox(parent)
    spin.setRange(low, high)
    spin.setSpecialValueText(special)
    spin.setMinimumWidth(90)
    return spin


def _range_box(parent: QWidget, first: QSpinBox, last: QSpinBox) -> QHBoxLayout:
    """``first`` "to" ``last`` on one line."""
    box = QHBoxLayout()
    box.addWidget(first)
    to = QLabel("to", parent)
    to.setBuddy(last)
    box.addWidget(to)
    box.addWidget(last)
    box.addStretch()
    return box


def _describe(preview: CsvPreview) -> str:
    """One line on how the file is read, for under the preview."""
    parts = [f"{DELIMITER_NAMES[preview.delimiter]}-separated"]
    if preview.decimal_comma:
        parts.append("decimal comma")
    if preview.encoding != "UTF-8":
        parts.append(f"read as {preview.encoding} text")
    n_data = len(preview.rows) - preview.data_start
    columns = len(preview.names)
    rows = f"{n_data} row{'s' if n_data != 1 else ''}"
    shown = f"first {rows} shown" if preview.more_rows else rows
    parts.append(f"{columns} column{'s' if columns != 1 else ''}, {shown}")
    return ", ".join(parts) + "."
