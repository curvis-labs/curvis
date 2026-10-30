"""Tests for the Import dialog and importing through the main window."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QDialog, QLabel
from pytestqt.qtbot import QtBot

from curvis.compute import Scheduler
from curvis.data import ColumnRole
from curvis.io.importers import CsvOptions
from curvis.ui.import_dialog import ImportDialog
from curvis.ui.main_window import MainWindow
from tests.ui.conftest import Prompts

DATA = Path(__file__).parents[1] / "data" / "csv"


@pytest.fixture
def scheduler(qapp: QApplication) -> Iterator[Scheduler]:
    s = Scheduler(debounce_ms=10)
    yield s
    s.shutdown()


def open_dialog(qtbot: QtBot, scheduler: Scheduler, name: str) -> ImportDialog:
    dialog = ImportDialog(DATA / name, scheduler)
    qtbot.addWidget(dialog)
    wait_preview(qtbot, dialog)
    return dialog


def wait_preview(qtbot: QtBot, dialog: ImportDialog) -> None:
    qtbot.waitUntil(lambda: not dialog._scheduler.busy)


def header(dialog: ImportDialog, section: int) -> str:
    return str(dialog.model.headerData(section, Qt.Orientation.Horizontal))


def test_preview_shows_the_file_with_names_and_roles(qtbot: QtBot, scheduler: Scheduler) -> None:
    dialog = open_dialog(qtbot, scheduler, "basic.csv")
    assert dialog.preview is not None
    assert dialog.model.rowCount() == 3  # the header row is not repeated in the grid
    assert dialog.model.columnCount() == 3
    assert header(dialog, 0) == "Dose\nY"
    assert header(dialog, 2) == "Label\nText"
    assert dialog.model.index(0, 0).data() == "0.1"
    assert dialog.model.index(1, 1).data() == "30.25"
    assert dialog.model.headerData(0, Qt.Orientation.Vertical) == "2"
    assert dialog.delimiter.itemText(0) == "Auto-detect (Comma)"
    assert dialog.status.text() == "Comma-separated, 3 columns, 3 rows."
    assert dialog.ok_button.isEnabled()
    assert dialog.options() == CsvOptions()


def test_header_row_label_has_a_keyboard_shortcut(qtbot: QtBot, scheduler: Scheduler) -> None:
    dialog = open_dialog(qtbot, scheduler, "basic.csv")
    labels = [w for w in dialog.findChildren(QLabel) if w.buddy() is dialog.header_row]
    assert [label.text() for label in labels] == ["&Header row"]


def test_first_column_is_x_updates_the_preview(qtbot: QtBot, scheduler: Scheduler) -> None:
    dialog = open_dialog(qtbot, scheduler, "basic.csv")
    dialog.first_column_is_x.setChecked(True)
    wait_preview(qtbot, dialog)
    assert header(dialog, 0) == "Dose\nX"
    assert dialog.options().first_column_is_x


def test_choosing_a_delimiter_updates_the_preview(qtbot: QtBot, scheduler: Scheduler) -> None:
    dialog = open_dialog(qtbot, scheduler, "basic.csv")
    dialog.delimiter.setCurrentIndex(dialog.delimiter.findData(";"))
    wait_preview(qtbot, dialog)
    assert dialog.options().delimiter == ";"
    assert dialog.model.columnCount() == 1
    assert header(dialog, 0) == "Dose,Response,Label\nText"


def test_header_row_skips_and_dims_the_rows_above(qtbot: QtBot, scheduler: Scheduler) -> None:
    dialog = open_dialog(qtbot, scheduler, "header_row_3.csv")
    dialog.header_row.setValue(3)
    wait_preview(qtbot, dialog)
    assert dialog.options().header_row == 3
    assert [header(dialog, i) for i in range(2)] == ["Well\nText", "OD600\nY"]
    dim = QApplication.palette().brush(QPalette.ColorRole.PlaceholderText)
    assert dialog.model.rowCount() == 4  # two rows above the header, two data rows
    assert dialog.model.index(0, 0).data(Qt.ItemDataRole.ForegroundRole) == dim
    assert dialog.model.index(2, 0).data() == "A1"
    assert dialog.model.index(2, 0).data(Qt.ItemDataRole.ForegroundRole) is None
    vertical = [dialog.model.headerData(r, Qt.Orientation.Vertical) for r in range(4)]
    assert vertical == ["1", "2", "4", "5"]


def test_no_header_names_columns_by_letter(qtbot: QtBot, scheduler: Scheduler) -> None:
    dialog = open_dialog(qtbot, scheduler, "no_header.csv")
    dialog.no_header.setChecked(True)
    wait_preview(qtbot, dialog)
    assert not dialog.header_row.isEnabled()
    assert dialog.options().header_row is None
    assert header(dialog, 0) == "A\nY"
    assert dialog.model.rowCount() == 2


def test_decimal_comma_follows_the_delimiter_until_chosen(
    qtbot: QtBot, scheduler: Scheduler
) -> None:
    dialog = open_dialog(qtbot, scheduler, "semicolon_decimal_comma.csv")
    assert dialog.decimal_comma.isChecked()
    assert dialog.decimal_comma.isEnabled()
    assert header(dialog, 1) == "Wert\nY"
    assert "decimal comma" in dialog.status.text()

    dialog.advanced_toggle.click()
    assert not dialog.advanced.isHidden()
    dialog.decimal_comma.click()  # untick
    wait_preview(qtbot, dialog)
    assert dialog.options().decimal_comma is False
    assert header(dialog, 1) == "Wert\nText"

    dialog.delimiter.setCurrentIndex(dialog.delimiter.findData(","))
    wait_preview(qtbot, dialog)
    assert dialog.options().decimal_comma is None
    assert not dialog.decimal_comma.isEnabled()


def test_unreadable_file_shows_the_message_and_disables_import(
    qtbot: QtBot, scheduler: Scheduler
) -> None:
    dialog = open_dialog(qtbot, scheduler, "ragged.csv")
    assert dialog.preview is None
    assert dialog.error is not None and dialog.error.startswith("Row 3 has 3 values")
    assert dialog.status.text() == dialog.error
    assert not dialog.ok_button.isEnabled()
    assert dialog.model.rowCount() == 0


def test_fixing_the_options_recovers_from_an_error(qtbot: QtBot, scheduler: Scheduler) -> None:
    dialog = open_dialog(qtbot, scheduler, "basic.csv")
    dialog.header_row.setValue(9)
    wait_preview(qtbot, dialog)
    assert not dialog.ok_button.isEnabled()
    dialog.header_row.setValue(1)
    wait_preview(qtbot, dialog)
    assert dialog.ok_button.isEnabled()


def test_closing_the_dialog_stops_listening(qtbot: QtBot, scheduler: Scheduler) -> None:
    dialog = open_dialog(qtbot, scheduler, "basic.csv")
    dialog.header_row.setValue(2)  # a preview job is pending
    dialog.reject()
    assert not scheduler.is_busy(dialog._node)
    scheduler.finished.emit(dialog._node, None)  # nobody is connected any more


def test_row_and_column_ranges_update_the_preview(qtbot: QtBot, scheduler: Scheduler) -> None:
    dialog = open_dialog(qtbot, scheduler, "ranges.csv")
    assert dialog.first_row.value() == 2  # the first data row
    assert dialog.last_row.text() == "Last"
    assert dialog.last_column.text() == "Last"
    assert dialog.error is not None and dialog.error.startswith("Row 2 has 4 values")

    dialog.header_row.setValue(2)
    dialog.first_row.setValue(4)
    dialog.last_row.setValue(5)
    dialog.first_column.setValue(2)
    dialog.last_column.setValue(3)
    wait_preview(qtbot, dialog)
    options = dialog.options()
    assert (options.first_row, options.last_row) == (4, 5)
    assert (options.first_column, options.last_column) == (2, 3)
    assert dialog.ok_button.isEnabled()
    vertical = [dialog.model.headerData(r, Qt.Orientation.Vertical) for r in range(3)]
    assert vertical == ["1", "4", "5"]
    assert header(dialog, 0) == ""  # outside the column range
    assert header(dialog, 1) == "Dose\nY"
    dim = QApplication.palette().brush(QPalette.ColorRole.PlaceholderText)
    assert dialog.model.index(1, 0).data(Qt.ItemDataRole.ForegroundRole) == dim
    assert dialog.model.index(1, 1).data(Qt.ItemDataRole.ForegroundRole) is None
    assert dialog.status.text() == "Comma-separated, 2 columns, 2 rows."


def test_first_row_follows_the_header_row(qtbot: QtBot, scheduler: Scheduler) -> None:
    dialog = open_dialog(qtbot, scheduler, "header_row_3.csv")
    assert (dialog.first_row.minimum(), dialog.first_row.value()) == (2, 2)
    dialog.header_row.setValue(3)
    assert (dialog.first_row.minimum(), dialog.first_row.value()) == (4, 4)
    assert dialog.options().first_row is None  # the first data row is the default
    dialog.first_row.setValue(5)
    assert dialog.options().first_row == 5
    dialog.header_row.setValue(1)
    assert (dialog.first_row.minimum(), dialog.first_row.value()) == (2, 5)  # kept
    dialog.no_header.setChecked(True)
    assert dialog.first_row.minimum() == 1
    assert dialog.options().first_row == 5


def test_a_bad_range_shows_its_message(qtbot: QtBot, scheduler: Scheduler) -> None:
    dialog = open_dialog(qtbot, scheduler, "basic.csv")
    dialog.last_column.setValue(9)
    wait_preview(qtbot, dialog)
    assert dialog.error == "The file has only 3 columns."
    assert not dialog.ok_button.isEnabled()


# --- main window ---------------------------------------------------------------------


@pytest.fixture
def importing(
    qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> list[Path | None]:
    """Paths the file dialog returns; the Import dialog is accepted once its preview is in."""
    paths: list[Path | None] = []
    monkeypatch.setattr(MainWindow, "_ask_import_path", lambda self: paths.pop(0))

    def accept(self: MainWindow, dialog: QDialog) -> bool:
        assert isinstance(dialog, ImportDialog)
        qtbot.waitUntil(lambda: dialog.preview is not None or dialog.error is not None)
        dialog.first_column_is_x.setChecked(True)
        wait_preview(qtbot, dialog)
        dialog.accept()
        return dialog.preview is not None

    monkeypatch.setattr(MainWindow, "_exec_import_dialog", accept)
    return paths


def wait_import(qtbot: QtBot, window: MainWindow) -> None:
    qtbot.waitUntil(lambda: not window._pending_imports, timeout=30_000)


def test_import_without_a_project_makes_one(
    qtbot: QtBot, window: MainWindow, importing: list[Path | None]
) -> None:
    importing.append(DATA / "basic.csv")
    window.registry.action("file.import").trigger()
    wait_import(qtbot, window)

    tab = window.current_tab
    assert tab is not None and tab.table_view is not None
    table = tab.document.tables[0]
    assert [(c.name, c.role) for c in table.columns] == [
        ("Dose", ColumnRole.X),
        ("Response", ColumnRole.Y),
        ("Label", ColumnRole.TEXT),
    ]
    assert tab.table_view.model().rowCount() >= 3
    assert window.statusBar().currentMessage() == "Imported 3 rows and 3 columns from basic.csv."
    assert tab.document.undo_stack.undo_text == "Import 'basic.csv'"

    window.undo()
    assert [c.name for c in table.columns] == ["X", "A"]
    assert table.n_rows == 0


def test_import_fills_empty_columns_of_the_current_table(
    qtbot: QtBot, window: MainWindow, importing: list[Path | None]
) -> None:
    tab = window.new_document()
    table = tab.document.tables[0]
    importing.extend([DATA / "tab.tsv", DATA / "bom.csv"])
    window.import_data()
    wait_import(qtbot, window)
    assert [c.name for c in table.columns] == ["Dose", "Resp A", "Resp B"]

    window.import_data()  # nothing is empty now: appended
    wait_import(qtbot, window)
    assert [c.name for c in table.columns] == ["Dose", "Resp A", "Resp B", "Time", "Signal"]
    assert table.n_rows == 3
    assert window.tabs.count() == 1
    assert tab.table_view is not None
    assert tab.table_view.model().columnCount() >= 5


def test_cancelled_file_dialog_does_nothing(
    window: MainWindow, importing: list[Path | None]
) -> None:
    importing.append(None)
    window.import_data()
    assert window.tabs.count() == 0
    assert not window._pending_imports


def test_failed_import_shows_an_error(
    qtbot: QtBot, window: MainWindow, prompts: Prompts, tmp_path: Path
) -> None:
    path = tmp_path / "gone.csv"
    window.start_import(path, CsvOptions())
    wait_import(qtbot, window)
    assert prompts.errors and prompts.errors[0][0] == "Cannot import data"
    assert "could not be opened" in prompts.errors[0][1]
    assert window.tabs.count() == 0


def test_import_with_ranges(qtbot: QtBot, window: MainWindow) -> None:
    options = CsvOptions(header_row=2, first_row=4, last_row=6, first_column=2, last_column=3)
    window.start_import(DATA / "ranges.csv", options)
    wait_import(qtbot, window)
    tab = window.current_tab
    assert tab is not None
    table = tab.document.tables[0]
    assert [c.name for c in table.columns] == ["Dose", "Resp"]
    assert table.columns[0].values.tolist() == [2.0, 3.0, 4.0]


def test_import_is_in_the_file_menu_and_command_palette(window: MainWindow) -> None:
    action = window.registry.action("file.import")
    assert action.shortcut().toString() == "Ctrl+Shift+I"
    assert any(entry.id == "file.import" for entry in window.registry.entries())
