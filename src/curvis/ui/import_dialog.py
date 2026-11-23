"""The Import dialog: preview a CSV or TSV file or an Excel sheet and choose how to read it.

For an Excel workbook a Sheet picker replaces the delimiter. The decimal mark is
auto-detected (falling back to the Settings default, which the status line then
mentions) or chosen: point or comma.

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
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QTableView,
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
    is_excel,
    read_preview,
)
from curvis.ui.dialogs import (
    SPACING,
    DialogSkeleton,
    Severity,
    buddy_label,
    columns,
    form_layout,
    set_tab_order,
    size_workbench,
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

    def __init__(
        self,
        path: Path,
        scheduler: Scheduler,
        parent: QWidget | None = None,
        *,
        default_decimal_comma: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import data")
        self.setObjectName("importDialog")
        self.path = Path(path)
        self.preview: CsvPreview | None = None
        self.error: str | None = None
        self._scheduler = scheduler
        self._node = f"import-preview:{id(self)}"
        self.excel = is_excel(self.path)
        self._default_decimal_comma = default_decimal_comma

        self.file_label = QLabel(self.path.name, self)
        self.file_label.setToolTip(str(self.path))
        self.file_label.setWordWrap(True)

        self.sheet = QComboBox(self)
        self.sheet.setToolTip("The sheet of the workbook to import.")
        self.sheet.setAccessibleName("Sheet")

        self.delimiter = QComboBox(self)
        self.delimiter.addItem("Auto-detect", None)
        for delimiter, name in DELIMITER_NAMES.items():
            self.delimiter.addItem(name, delimiter)

        self.decimal_mark = QComboBox(self)
        self.decimal_mark.addItem("Auto-detect", None)
        self.decimal_mark.addItem("Point (1.5)", False)
        self.decimal_mark.addItem("Comma (1,5)", True)
        self.decimal_mark.setToolTip(
            "How decimals are written in the file. Auto-detect uses the default from "
            "Settings when the data does not show it."
        )

        self.header_row = QSpinBox(self)
        self.header_row.setRange(1, HEADER_ROW_MAX)
        self.header_row.setToolTip(
            "The row that holds the column names. Rows above it are skipped."
        )
        self.no_header = QCheckBox("&No header row", self)
        self.no_header.setToolTip("Name the columns A, B, C, …")

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
        self.last_row.setAccessibleName("Last row")
        self.first_column = _spin_box(self, 1, COLUMN_MAX)
        self.first_column.setToolTip("The first column to import; column 1 is the first.")
        self.last_column = _spin_box(self, 0, COLUMN_MAX, "Last")
        self.last_column.setToolTip("The last column to import.")
        self.last_column.setAccessibleName("Last column")

        form = form_layout()
        form.addRow("File", self.file_label)
        if self.excel:
            form.addRow(buddy_label("&Sheet", self.sheet, self), self.sheet)
            self.delimiter.hide()
        else:
            form.addRow(buddy_label("&Delimiter", self.delimiter, self), self.delimiter)
            self.sheet.hide()
        form.addRow(buddy_label("Deci&mal mark", self.decimal_mark, self), self.decimal_mark)
        form.addRow(buddy_label("&Header row", self.header_row, self), self.header_row)
        form.addRow("", self.no_header)
        form.addRow(
            buddy_label("&Rows", self.first_row, self),
            _range_box(self, self.first_row, self.last_row),
        )
        form.addRow(
            buddy_label("C&olumns", self.first_column, self),
            _range_box(self, self.first_column, self.last_column),
        )
        form.addRow("", self.first_column_is_x)

        self.model = PreviewModel(self)
        self.table = QTableView(self)
        self.table.setObjectName("importPreview")
        self.table.setAccessibleName("Preview of the import")
        self.table.setModel(self.model)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)

        self.skeleton = DialogSkeleton(self, "Import")
        self.buttons = self.skeleton.buttons
        assert self.skeleton.ok_button is not None
        self.ok_button = self.skeleton.ok_button
        self.status = self.skeleton.feedback
        left, right = columns(self.skeleton.body, 2, 3)
        options = QGroupBox("Options", self)
        options_layout = QVBoxLayout(options)
        options_layout.addLayout(form)
        options_layout.addStretch(1)
        left.addWidget(options, 1)
        preview = QGroupBox("Preview", self)
        preview_layout = QVBoxLayout(preview)
        preview_layout.addWidget(self.table)
        right.addWidget(preview, 1)
        self.skeleton.set_problem("Reading the file…")
        set_tab_order(
            [
                self.sheet if self.excel else self.delimiter,
                self.decimal_mark,
                self.header_row,
                self.no_header,
                self.first_row,
                self.last_row,
                self.first_column,
                self.last_column,
                self.first_column_is_x,
                self.table,
            ]
        )
        (self.sheet if self.excel else self.delimiter).setFocus()
        size_workbench(self)

        self.delimiter.currentIndexChanged.connect(self._on_delimiter_changed)
        self.sheet.currentIndexChanged.connect(self._refresh)
        self.decimal_mark.currentIndexChanged.connect(self._refresh)
        self.header_row.valueChanged.connect(self._on_header_changed)
        self.no_header.toggled.connect(self._on_no_header_toggled)
        self.first_column_is_x.toggled.connect(self._refresh)
        for spin in (self.first_row, self.last_row, self.first_column, self.last_column):
            spin.valueChanged.connect(self._refresh)
        scheduler.finished.connect(self._on_job_finished)
        scheduler.failed.connect(self._on_job_failed)
        self._refresh(delay_ms=0)

    def options(self) -> CsvOptions:
        """The options as set in the dialog."""
        delimiter: Delimiter | None = None if self.excel else self.delimiter.currentData()
        decimal_comma: bool | None = self.decimal_mark.currentData()
        sheet = self.sheet.currentText() or None if self.excel else None
        return CsvOptions(
            delimiter=delimiter,
            header_row=None if self.no_header.isChecked() else self.header_row.value(),
            first_column_is_x=self.first_column_is_x.isChecked(),
            decimal_comma=decimal_comma,
            first_row=self._first_row(),
            last_row=self.last_row.value() or None,
            first_column=self.first_column.value(),
            last_column=self.last_column.value() or None,
            default_decimal_comma=self._default_decimal_comma,
            sheet=sheet,
        )

    def done(self, result: int) -> None:
        self._scheduler.cancel(self._node)
        self._scheduler.finished.disconnect(self._on_job_finished)
        self._scheduler.failed.disconnect(self._on_job_failed)
        super().done(result)

    # --- options ---------------------------------------------------------------------

    def _refresh(self, *_args: object, delay_ms: int | None = None) -> None:
        """Read the preview again with the current options (debounced)."""
        imports = ("polars", "fastexcel", "xlrd") if self.excel else ("polars",)
        job = Job(read_preview, (str(self.path), self.options(), PREVIEW_ROWS), imports=imports)
        self._scheduler.submit(self._node, job, delay_ms=delay_ms)

    def _on_delimiter_changed(self, _index: int) -> None:
        comma_delimiter = self.delimiter.currentData() == ","
        if comma_delimiter:
            self.decimal_mark.setCurrentIndex(0)  # a comma delimiter means a decimal point
        self.decimal_mark.setEnabled(not comma_delimiter)
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

    # --- results ---------------------------------------------------------------------

    def _on_job_finished(self, node: str, result: object) -> None:
        if node != self._node or not isinstance(result, CsvPreview):
            return
        self.preview = result
        self.error = None
        self.model.set_preview(result)
        if result.delimiter is not None:
            self.delimiter.setItemText(0, f"Auto-detect ({DELIMITER_NAMES[result.delimiter]})")
        self.decimal_mark.setEnabled(result.delimiter != ",")
        mark = "comma" if result.decimal_comma else "point"
        self.decimal_mark.setItemText(0, f"Auto-detect ({mark})")
        if result.sheets and [self.sheet.itemText(i) for i in range(self.sheet.count())] != list(
            result.sheets
        ):
            self.sheet.blockSignals(True)
            self.sheet.clear()
            self.sheet.addItems(list(result.sheets))
            self.sheet.blockSignals(False)
        if result.sheet is not None:
            self.sheet.setCurrentText(result.sheet)
        self.skeleton.set_problem(None)
        self.skeleton.set_status(_describe(result))

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
        self.decimal_mark.setItemText(0, "Auto-detect")
        self.skeleton.set_problem(self.error, Severity.ERROR)


def _spin_box(parent: QWidget, low: int, high: int, special: str = "") -> QSpinBox:
    """A spin box from ``low`` to ``high``; ``special`` is shown for ``low``."""
    spin = QSpinBox(parent)
    spin.setRange(low, high)
    spin.setSpecialValueText(special)
    return spin


def _range_box(parent: QWidget, first: QSpinBox, last: QSpinBox) -> QHBoxLayout:
    """``first`` "to" ``last`` on one line."""
    box = QHBoxLayout()
    box.setSpacing(SPACING)
    box.addWidget(first)
    box.addWidget(QLabel("to", parent))
    box.addWidget(last)
    box.addStretch()
    return box


def _describe(preview: CsvPreview) -> str:
    """One line on how the file is read, for under the preview."""
    if preview.delimiter is None:
        parts = [f"Sheet '{preview.sheet}'"]
    else:
        parts = [f"{DELIMITER_NAMES[preview.delimiter]}-separated"]
    if preview.decimal_comma:
        parts.append("decimal comma")
    if preview.delimiter is not None and preview.encoding != "UTF-8":
        parts.append(f"read as {preview.encoding} text")
    n_data = len(preview.rows) - preview.data_start
    columns = len(preview.names)
    rows = f"{n_data} row{'s' if n_data != 1 else ''}"
    shown = f"first {rows} shown" if preview.more_rows else rows
    parts.append(f"{columns} column{'s' if columns != 1 else ''}, {shown}")
    text = ", ".join(parts) + "."
    if preview.decimal_unsure:
        mark = "comma" if preview.decimal_comma else "point"
        text += (
            f" The data does not show the decimal mark, so a {mark} is used "
            "(the default in Settings)."
        )
    return text
