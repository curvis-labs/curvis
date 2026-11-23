"""Tests for the Prism import dialog and adding Prism tables through the main window."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog
from pytestqt.qtbot import QtBot

from curvis.compute import Scheduler
from curvis.data import ColumnRole
from curvis.io.prism import NOT_IMPORTED_NOTE
from curvis.ui.main_window import MainWindow
from curvis.ui.prism_import_dialog import NOTHING_TICKED, PrismImportDialog

PRISM = Path(__file__).parents[1] / "data" / "prism"


@pytest.fixture
def scheduler(qapp: QApplication) -> Iterator[Scheduler]:
    s = Scheduler(debounce_ms=10)
    yield s
    s.shutdown()


def open_dialog(qtbot: QtBot, scheduler: Scheduler, name: str) -> PrismImportDialog:
    dialog = PrismImportDialog(PRISM / name, scheduler)
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog.data is not None or dialog.error is not None, timeout=30_000)
    return dialog


def test_lists_every_table_ticked_with_its_size(qtbot: QtBot, scheduler: Scheduler) -> None:
    dialog = open_dialog(qtbot, scheduler, "2025-03-21 Analysis, LacY-GFP, ONPG.prism")
    assert dialog.tables.count() == 13
    first = dialog.tables.item(0)
    assert first.text() == "Sensor 1, ONPG  (XY, 9 series, 3000 rows)"
    assert first.checkState() == Qt.CheckState.Checked
    assert len(dialog.chosen()) == 13
    assert dialog.ok_button.isEnabled()
    assert dialog.status.text() == "13 tables will be added to the project."
    assert dialog.note.text() == NOT_IMPORTED_NOTE


def test_unticking_every_table_disables_import(qtbot: QtBot, scheduler: Scheduler) -> None:
    dialog = open_dialog(qtbot, scheduler, "Column data_Rep.pzfx")
    dialog.tables.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert not dialog.ok_button.isEnabled()
    assert dialog.status.text() == NOTHING_TICKED
    assert dialog.chosen() == []


def test_details_say_how_the_table_is_laid_out(qtbot: QtBot, scheduler: Scheduler) -> None:
    dialog = open_dialog(qtbot, scheduler, "Column data_SEM.pzfx")
    assert dialog.tables.currentRow() == 0
    assert dialog.details.text().startswith("Grouped, 3 series, 1 row.")
    assert "N values are kept as text columns." in dialog.details.text()


def test_a_file_that_is_not_prism_shows_why(
    qtbot: QtBot, scheduler: Scheduler, tmp_path: Path
) -> None:
    path = tmp_path / "broken.pzfx"
    path.write_text("not xml", encoding="utf-8")
    dialog = open_dialog(qtbot, scheduler, str(path))
    assert dialog.error is not None and "not a Prism file" in dialog.error
    assert not dialog.ok_button.isEnabled()


# --- main window ---------------------------------------------------------------------


@pytest.fixture
def importing(
    qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> list[Path | None]:
    """Paths the file dialog returns; the Prism dialog is accepted once it has read."""
    paths: list[Path | None] = []
    monkeypatch.setattr(MainWindow, "_ask_import_path", lambda self: paths.pop(0))

    def accept(self: MainWindow, dialog: QDialog) -> bool:
        assert isinstance(dialog, PrismImportDialog)
        qtbot.waitUntil(lambda: dialog.data is not None or dialog.error is not None)
        dialog.accept()
        return dialog.data is not None

    monkeypatch.setattr(MainWindow, "_exec_import_dialog", accept)
    return paths


def test_prism_tables_replace_a_blank_table(
    window: MainWindow, importing: list[Path | None]
) -> None:
    importing.append(PRISM / "Survival data.pzfx")
    window.import_data()
    tab = window.current_tab
    assert tab is not None
    document = tab.document
    assert [t.name for t in document.tables] == ["Survival: Three groups"]
    table = document.tables[0]
    assert [c.role for c in table.columns] == [
        ColumnRole.TEXT,
        ColumnRole.TIME,
        ColumnRole.EVENT,
        ColumnRole.EVENT,
        ColumnRole.EVENT,
    ]
    assert tab.current_table_id == table.id
    assert document.undo_stack.undo_text == "Import 'Survival data.pzfx'"
    window.undo()
    assert [t.name for t in document.tables] == ["Data 1"]


def test_prism_tables_are_added_after_a_table_in_use(
    window: MainWindow, importing: list[Path | None]
) -> None:
    tab = window.new_document()
    document = tab.document
    first = document.tables[0]
    from curvis.core import SetCellValue

    document.execute(SetCellValue(first.id, first.columns[1].id, 0, 1.5))
    importing.extend([PRISM / "XY data_Rep.prism", PRISM / "XY data_Rep.pzfx"])
    window.import_data()
    window.import_data()
    assert [t.name for t in document.tables] == [
        "Data 1",
        "XY: Compare slopes (simple lin. reg.)",
        "XY: Compare slopes (simple lin. reg.) (2)",
    ]
    imported = document.tables[1]
    assert [g.name for g in imported.replicate_groups] == ["Control", "Treated"]
    assert window.statusBar().currentMessage().startswith("Imported 1 table from XY data_Rep")
