"""Autosave and crash recovery in the main window and the launcher (step 4.1)."""

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from pytestqt.qtbot import QtBot

from curvis.core import Document, RenameTable
from curvis.data import Table
from curvis.io import project
from curvis.io.recovery import CopyInfo, RecoveryStore
from curvis.settings import Settings
from curvis.ui.autosave import MINUTE_MS
from curvis.ui.document_tab import DocumentTab
from curvis.ui.launcher import UNRECOVERABLE_TEXT
from curvis.ui.main_window import MainWindow, SaveChoice
from curvis.ui.settings_dialog import SettingsDialog
from tests.ui.conftest import Prompts
from tests.ui.helpers import add_table


class Answers:
    def __init__(self) -> None:
        self.discard: list[bool] = []
        self.overwrite: list[bool] = []
        self.asked_overwrite = 0


@pytest.fixture
def answers() -> Answers:
    return Answers()


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    return tmp_path / "recovery"


@pytest.fixture
def make_window(
    qtbot: QtBot,
    settings: Settings,
    prompts: Prompts,
    answers: Answers,
    folder: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[object]:
    """Makes main windows with a recovery store in ``folder`` and scripted dialogs."""

    def ask_save(self: MainWindow, tab: DocumentTab) -> SaveChoice:
        prompts.asked_save.append(tab.title)
        return prompts.save_choices.pop(0) if prompts.save_choices else SaveChoice.DISCARD

    def ask_overwrite(self: MainWindow, tab: DocumentTab) -> bool:
        answers.asked_overwrite += 1
        return answers.overwrite.pop(0)

    monkeypatch.setattr(MainWindow, "_ask_save", ask_save)
    monkeypatch.setattr(MainWindow, "_ask_save_path", lambda self, tab: prompts.save_paths.pop(0))
    monkeypatch.setattr(
        MainWindow, "_show_error", lambda self, title, msg: prompts.errors.append((title, msg))
    )
    monkeypatch.setattr(
        MainWindow, "_ask_discard_recovered", lambda self, copy: answers.discard.pop(0)
    )
    monkeypatch.setattr(MainWindow, "_ask_overwrite_changed", ask_overwrite)
    made: list[MainWindow] = []

    def make(session: str | None = None) -> MainWindow:
        store = RecoveryStore(folder) if session is None else RecoveryStore(folder, session)
        win = MainWindow(settings, recovery=store)
        qtbot.addWidget(win)
        win.show()
        qtbot.waitExposed(win)
        made.append(win)
        return win

    yield make
    for win in made:
        if win._scheduler is not None:
            win._scheduler.shutdown()
        if win.recovery is not None and win.recovery.started:
            win.recovery.close()


def copies(folder: Path) -> list[Path]:
    return sorted(folder.rglob("*.cvp"))


def autosave_and_wait(window: MainWindow) -> None:
    assert window.autosave is not None
    window.autosave.write_all()
    assert window.scheduler.wait_idle()


def crash(window: MainWindow) -> None:
    """Leave the window's copies behind as a killed process does: no clean close."""
    assert window.recovery is not None
    window.recovery._session_lock.unlock()
    window.recovery._started = False
    window.recovery = None  # the window is gone; nothing will clean up


def test_copies_are_written_only_when_there_are_changes(make_window, folder: Path) -> None:
    window = make_window()
    tab = window.new_document()
    autosave_and_wait(window)
    assert copies(folder) == []  # a new, unchanged project needs no copy
    add_table(tab)
    autosave_and_wait(window)
    [copy] = copies(folder)
    written = copy.stat().st_mtime_ns
    assert project.load(copy).tables[-1].name == "Data 2"
    assert window.autosave is not None
    assert not window.autosave.needs_copy(tab)
    assert not window.autosave.write_if_needed(tab)  # nothing changed since the copy
    assert window.scheduler.wait_idle()
    assert copy.stat().st_mtime_ns == written
    tab.document.execute(RenameTable(tab.document.tables[-1].id, "Renamed"))
    autosave_and_wait(window)
    assert project.load(copy).tables[-1].name == "Renamed"


def test_the_timer_writes_copies_at_the_interval(
    make_window, folder: Path, qtbot: QtBot, settings: Settings
) -> None:
    window = make_window()
    tab = window.new_document()
    assert window.autosave is not None
    assert window.autosave.interval_ms == 3 * MINUTE_MS  # the default
    window.autosave.set_interval(50)
    add_table(tab)
    qtbot.waitUntil(lambda: bool(copies(folder)), timeout=5000)


def test_a_settings_change_applies_to_open_documents_at_once(
    make_window, settings: Settings
) -> None:
    window = make_window()
    window.new_document()
    window.new_document()
    assert window.autosave is not None
    settings.autosave_minutes = 10
    assert window.autosave.interval_ms == 10 * MINUTE_MS
    timers = [e.timer for e in window.autosave._entries.values()]
    assert [t.interval() for t in timers] == [10 * MINUTE_MS] * 2
    assert all(t.isActive() for t in timers)


def test_save_deletes_the_copy(make_window, folder: Path, prompts: Prompts, tmp_path) -> None:
    window = make_window()
    tab = window.new_document()
    add_table(tab)
    autosave_and_wait(window)
    assert copies(folder)
    prompts.save_paths.append(tmp_path / "saved.cvp")
    assert window.save_tab(tab)
    assert copies(folder) == []
    assert (tmp_path / "saved.cvp").is_file()  # the project file, not a copy
    autosave_and_wait(window)
    assert copies(folder) == []  # saved: nothing new to copy


def test_a_save_drops_a_copy_still_being_written(
    make_window, folder: Path, prompts: Prompts, tmp_path
) -> None:
    window = make_window()
    tab = window.new_document()
    add_table(tab)
    assert window.autosave is not None
    assert window.autosave.write_if_needed(tab)  # submitted, not written yet
    prompts.save_paths.append(tmp_path / "saved.cvp")
    assert window.save_tab(tab)
    assert window.scheduler.wait_idle()
    assert copies(folder) == []


def test_close_and_discard_delete_the_copy(make_window, folder: Path, prompts: Prompts) -> None:
    window = make_window()
    tab = window.new_document()
    add_table(tab)
    autosave_and_wait(window)
    assert copies(folder)
    prompts.save_choices.append(SaveChoice.DISCARD)
    assert window.close_tab(tab)
    assert copies(folder) == []


def test_undo_back_to_the_saved_state_deletes_the_copy(make_window, folder: Path) -> None:
    window = make_window()
    tab = window.new_document()
    add_table(tab)
    autosave_and_wait(window)
    tab.document.undo()
    autosave_and_wait(window)
    assert copies(folder) == []


def test_a_clean_exit_leaves_the_folder_empty(make_window, folder: Path) -> None:
    window = make_window()
    for _ in range(2):
        add_table(window.new_document())
    autosave_and_wait(window)
    assert len(copies(folder)) == 2
    window.close()  # discards both (the scripted answer)
    assert list(folder.iterdir()) == []


def test_a_second_window_does_not_offer_the_copies_of_the_first(make_window) -> None:
    first = make_window("first")
    add_table(first.new_document())
    autosave_and_wait(first)
    second = make_window("second")
    assert second.launcher.recovered == []
    assert not second.launcher.recovered_section.isVisible()


def recovered_window(make_window, saved: Path | None = None) -> tuple[MainWindow, MainWindow]:
    """A window that crashed with one changed project, and the window started after it."""
    before = make_window("crashed")
    if saved is None:
        tab = before.new_document()
    else:
        before.open_path(saved)
        assert before.scheduler.wait_idle()
        tab = before.current_tab
        assert tab is not None
    add_table(tab, "Unsaved work")
    autosave_and_wait(before)
    crash(before)
    return before, make_window("after")


def test_the_launcher_offers_a_recovered_project(make_window, qtbot: QtBot) -> None:
    _, window = recovered_window(make_window)
    [copy] = window.launcher.recovered
    assert copy.recoverable and copy.name == "Untitled"
    section = window.launcher.recovered_section
    assert section.isVisible()
    [row] = window.launcher.recovered_rows
    assert row.name_label.text() == "Untitled"
    assert row.folder_label.text() == "Never saved"
    assert row.when_label.text() == "Just now"
    assert row.open_button.isVisible() and row.discard_button.isVisible()
    recent = window.launcher.recent_label
    assert (
        section.mapTo(window.launcher, section.rect().topLeft()).y()
        < recent.mapTo(window.launcher, recent.rect().topLeft()).y()
    )


def test_open_restores_a_never_saved_project_as_untitled(
    make_window, qtbot: QtBot, folder: Path
) -> None:
    _, window = recovered_window(make_window)
    [row] = window.launcher.recovered_rows
    row.open_button.click()
    qtbot.waitUntil(lambda: window.current_tab is not None)
    tab = window.current_tab
    assert tab is not None
    assert tab.path is None
    assert tab.is_modified
    assert tab.document.tables[-1].name == "Unsaved work"
    assert not tab.document.undo_stack.can_undo  # the undo history starts empty
    assert [e.command_type for e in tab.document.change_log] == ["AddTable"]  # kept
    assert window.launcher.recovered == []
    assert window.tabs.tabText(window.tabs.indexOf(tab)).endswith("*")
    # The copy now belongs to this session: still safe, and not offered again.
    assert window.recovery is not None
    assert [p.parent.name for p in copies(folder)] == [window.recovery.session]


def test_open_restores_a_saved_project_with_its_file(
    make_window, qtbot: QtBot, tmp_path: Path, prompts: Prompts, answers: Answers
) -> None:
    saved = tmp_path / "Assay.cvp"
    project.save(Document(name="Assay", tables=[Table.blank()]), saved)
    _, window = recovered_window(make_window, saved)
    [copy] = window.launcher.recovered
    assert copy.info is not None and copy.info.original_path == saved
    assert copy.name == "Assay"
    window.open_recovered(copy)
    qtbot.waitUntil(lambda: window.current_tab is not None)
    tab = window.current_tab
    assert tab is not None and tab.path == saved and tab.is_modified
    assert project.load(saved).tables[-1].name != "Unsaved work"  # the file is untouched
    assert window.save_tab(tab)  # unchanged since: no warning
    assert answers.asked_overwrite == 0
    assert project.load(saved).tables[-1].name == "Unsaved work"
    assert window.recovery is not None
    assert copies(tmp_path / "recovery") == []


def test_save_warns_when_the_file_changed_since_the_copy(
    make_window, qtbot: QtBot, tmp_path: Path, answers: Answers
) -> None:
    saved = tmp_path / "Assay.cvp"
    project.save(Document(name="Assay", tables=[Table.blank()]), saved)
    _, window = recovered_window(make_window, saved)
    # Someone saves the file after the copy was made.
    other = Document(name="Assay", tables=[Table.blank("Theirs")])
    project.save(other, saved)
    stat = saved.stat()
    os.utime(saved, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    window.open_recovered(window.launcher.recovered[0])
    qtbot.waitUntil(lambda: window.current_tab is not None)
    tab = window.current_tab
    assert tab is not None
    answers.overwrite.append(False)
    assert not window.save_tab(tab)
    assert answers.asked_overwrite == 1
    assert project.load(saved).tables[0].name == "Theirs"
    assert tab.is_modified
    answers.overwrite.append(True)
    assert window.save_tab(tab)
    assert project.load(saved).tables[-1].name == "Unsaved work"
    assert not tab.check_file_changed
    assert window.save_tab(tab)  # saved by us now: no more warnings
    assert answers.asked_overwrite == 2


def test_discard_asks_and_deletes_the_copy(make_window, answers: Answers, folder: Path) -> None:
    _, window = recovered_window(make_window)
    [row] = window.launcher.recovered_rows
    answers.discard.append(False)
    row.discard_button.click()
    assert len(window.launcher.recovered) == 1
    assert len(copies(folder)) == 1
    answers.discard.append(True)
    window.launcher.recovered_rows[0].discard_button.click()
    assert window.launcher.recovered == []
    assert copies(folder) == []
    assert not window.launcher.recovered_section.isVisible()


def damaged_copy(folder: Path) -> None:
    dead = folder / "dead"
    dead.mkdir(parents=True)
    (dead / "abc.cvp").write_bytes(b"PK\x03\x04 truncated")
    info = CopyInfo("Broken", None, RecoveryStore(folder).clock(), None)
    (dead / "abc.json").write_text(json.dumps(info.to_json("dead")), "utf-8")


def test_a_damaged_copy_can_only_be_discarded(make_window, folder: Path, answers: Answers) -> None:
    damaged_copy(folder)
    window = make_window()
    [row] = window.launcher.recovered_rows
    assert row.when_label.text() == UNRECOVERABLE_TEXT
    assert not row.open_button.isVisible()
    answers.discard.append(True)
    row.discard_button.click()
    assert window.launcher.recovered == []
    assert not (folder / "dead").exists()


def test_a_copy_that_fails_to_open_becomes_unrecoverable(
    make_window, folder: Path, qtbot: QtBot, prompts: Prompts
) -> None:
    _, window = recovered_window(make_window)
    [copy] = window.launcher.recovered
    copy.project_path.write_bytes(b"PK\x05\x06" + bytes(18))  # an empty zip: no project.json
    window.open_recovered(copy)
    qtbot.waitUntil(lambda: bool(prompts.errors))
    assert prompts.errors[0][0] == "Cannot recover project"
    [row] = window.launcher.recovered_rows
    assert row.when_label.text() == UNRECOVERABLE_TEXT
    assert window.current_tab is None


def test_an_unusable_recovery_folder_does_not_stop_the_app(
    make_window, qtbot: QtBot, settings: Settings, tmp_path: Path
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", "utf-8")
    window = MainWindow(settings, recovery=RecoveryStore(blocker / "recovery"))
    qtbot.addWidget(window)
    assert window.recovery is None
    tab = window.new_document()
    add_table(tab)
    assert window.autosave is None
    window.close()
    window.scheduler.shutdown()


def test_the_settings_dialog_sets_the_interval(qtbot: QtBot, tmp_path: Path) -> None:
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    assert dialog.autosave.value() == 3
    assert (dialog.autosave.minimum(), dialog.autosave.maximum()) == (1, 30)
    dialog.autosave.setValue(7)
    dialog.accept()
    assert settings.autosave_minutes == 7
