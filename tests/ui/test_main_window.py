"""Tests for the main window: launcher, tabs, saving, opening and closing."""

from pathlib import Path

from pytestqt.qtbot import QtBot

from curvis.core import Document
from curvis.io import project
from curvis.settings import Settings
from curvis.ui.document_tab import DocumentTab
from curvis.ui.main_window import MainWindow, SaveChoice
from tests.ui.conftest import Prompts
from tests.ui.helpers import add_table


def open_and_wait(qtbot: QtBot, window: MainWindow, path: Path) -> None:
    before = window.tabs.count()
    window.open_path(path)
    qtbot.waitUntil(lambda: window.tabs.count() > before and not window._pending_opens)


def saved_project(tmp_path: Path, name: str = "Assay") -> Path:
    path = tmp_path / f"{name}.cvp"
    project.save(Document(name=name), path)
    return path


def test_starts_on_launcher(window: MainWindow) -> None:
    assert window.stack.currentWidget() is window.launcher
    assert window.windowTitle() == "Curvis"
    assert not window.registry.action("file.save").isEnabled()
    assert not window.registry.action("edit.undo").isEnabled()


def test_new_opens_a_tab_and_last_close_returns_to_launcher(window: MainWindow) -> None:
    window.launcher.new_button.click()
    assert window.tabs.count() == 1
    assert window.stack.currentWidget() is window.tabs
    assert window.tabs.tabText(0) == "Untitled"
    window.registry.action("file.new").trigger()
    assert [window.tabs.tabText(i) for i in range(2)] == ["Untitled", "Untitled 2"]
    assert window.tabs.currentIndex() == 1

    window.registry.action("file.close").trigger()
    window.registry.action("file.close").trigger()
    assert window.tabs.count() == 0
    assert window.stack.currentWidget() is window.launcher


def test_modified_marker_in_tab_and_window_title(window: MainWindow) -> None:
    tab = window.new_document()
    assert not window.isWindowModified()
    add_table(tab)
    assert window.tabs.tabText(0) == "Untitled*"
    assert window.isWindowModified()
    tab.document.undo()
    assert window.tabs.tabText(0) == "Untitled"
    assert not window.isWindowModified()


def test_save_new_document_asks_for_path_and_adds_suffix(
    window: MainWindow, prompts: Prompts, tmp_path: Path, settings: Settings
) -> None:
    tab = window.new_document()
    add_table(tab)
    prompts.save_paths.append(tmp_path / "experiment")
    window.registry.action("file.save").trigger()
    target = tmp_path / "experiment.cvp"
    assert target.is_file()
    assert tab.path == target
    assert not tab.is_modified
    assert window.tabs.tabText(0) == "experiment"
    assert settings.recent_projects[0].path == target
    assert [t.name for t in project.load(target).tables] == ["Data 1"]

    # Save again: no dialog, same file.
    add_table(tab, "Data 2")
    window.registry.action("file.save").trigger()
    assert [t.name for t in project.load(target).tables] == ["Data 1", "Data 2"]


def test_save_as_cancelled_keeps_document_unsaved(window: MainWindow, prompts: Prompts) -> None:
    tab = window.new_document()
    add_table(tab)
    prompts.save_paths.append(None)
    assert window.save_current_as() is False
    assert tab.path is None
    assert tab.is_modified


def test_save_error_is_reported(window: MainWindow, prompts: Prompts, tmp_path: Path) -> None:
    tab = window.new_document()
    prompts.save_paths.append(tmp_path / "missing-folder" / "x.cvp")
    assert window.save_current() is False
    assert prompts.errors and prompts.errors[0][0] == "Cannot save project"
    assert tab.path is None


def test_open_project_from_dialog(
    qtbot: QtBot, window: MainWindow, prompts: Prompts, tmp_path: Path, settings: Settings
) -> None:
    path = saved_project(tmp_path)
    prompts.open_paths.append(path)
    window.registry.action("file.open").trigger()
    qtbot.waitUntil(lambda: window.tabs.count() == 1)
    tab = window.current_tab
    assert tab is not None and tab.path == path
    assert window.tabs.tabText(0) == "Assay"
    assert not tab.is_modified
    assert settings.recent_projects[0].path == path


def test_open_cancelled_does_nothing(window: MainWindow, prompts: Prompts) -> None:
    prompts.open_paths.append(None)
    window.open_document()
    assert window.tabs.count() == 0


def test_opening_an_open_file_switches_to_its_tab(
    qtbot: QtBot, window: MainWindow, tmp_path: Path
) -> None:
    path = saved_project(tmp_path)
    open_and_wait(qtbot, window, path)
    window.new_document()
    assert window.tabs.currentIndex() == 1
    window.open_path(tmp_path / "." / "Assay.cvp")
    assert window.tabs.count() == 2
    assert window.tabs.currentIndex() == 0


def test_open_damaged_file_shows_error(
    qtbot: QtBot, window: MainWindow, prompts: Prompts, tmp_path: Path
) -> None:
    bad = tmp_path / "bad.cvp"
    bad.write_bytes(b"not a zip file")
    window.open_path(bad)
    qtbot.waitUntil(lambda: bool(prompts.errors))
    title, message = prompts.errors[0]
    assert title == "Cannot open project"
    assert "'bad.cvp' could not be opened" in message
    assert window.tabs.count() == 0


def test_open_missing_file_shows_error_and_drops_it_from_recent(
    window: MainWindow, prompts: Prompts, tmp_path: Path, settings: Settings
) -> None:
    gone = tmp_path / "gone.cvp"
    settings.add_recent_project(gone)
    window.open_path(gone)
    assert prompts.errors[0][0] == "Cannot open project"
    assert settings.recent_projects == []


def test_open_from_recent_list(
    qtbot: QtBot, window: MainWindow, tmp_path: Path, settings: Settings
) -> None:
    path = saved_project(tmp_path)
    settings.add_recent_project(path)
    item = window.launcher.recent_list.item(0)
    window.launcher.recent_list.itemActivated.emit(item)
    qtbot.waitUntil(lambda: window.tabs.count() == 1)
    assert window.current_tab is not None and window.current_tab.path == path


# --- unsaved-changes prompt ------------------------------------------------------


def modified_tab(window: MainWindow) -> DocumentTab:
    tab = window.new_document()
    add_table(tab)
    return tab


def test_close_unmodified_tab_does_not_ask(window: MainWindow, prompts: Prompts) -> None:
    window.new_document()
    assert window.close_current()
    assert prompts.asked_save == []


def test_close_tab_cancel_keeps_it(window: MainWindow, prompts: Prompts) -> None:
    modified_tab(window)
    prompts.save_choices.append(SaveChoice.CANCEL)
    assert window.close_current() is False
    assert prompts.asked_save == ["Untitled"]
    assert window.tabs.count() == 1


def test_close_tab_discard_closes_without_saving(window: MainWindow, prompts: Prompts) -> None:
    modified_tab(window)
    prompts.save_choices.append(SaveChoice.DISCARD)
    assert window.close_current()
    assert window.tabs.count() == 0


def test_close_tab_save_saves_then_closes(
    window: MainWindow, prompts: Prompts, tmp_path: Path
) -> None:
    modified_tab(window)
    prompts.save_choices.append(SaveChoice.SAVE)
    prompts.save_paths.append(tmp_path / "kept.cvp")
    assert window.close_current()
    assert (tmp_path / "kept.cvp").is_file()
    assert window.tabs.count() == 0


def test_close_tab_save_then_cancel_save_dialog_keeps_it(
    window: MainWindow, prompts: Prompts
) -> None:
    modified_tab(window)
    prompts.save_choices.append(SaveChoice.SAVE)
    prompts.save_paths.append(None)
    assert window.close_current() is False
    assert window.tabs.count() == 1


def test_tab_close_button_asks_too(window: MainWindow, prompts: Prompts) -> None:
    modified_tab(window)
    prompts.save_choices.append(SaveChoice.CANCEL)
    window.tabs.tabCloseRequested.emit(0)
    assert prompts.asked_save == ["Untitled"]
    assert window.tabs.count() == 1


def test_window_close_asks_for_each_modified_document(window: MainWindow, prompts: Prompts) -> None:
    modified_tab(window)
    window.new_document()  # unmodified: not asked about
    modified_tab(window)
    prompts.save_choices += [SaveChoice.DISCARD, SaveChoice.CANCEL]
    assert window.close() is False
    assert prompts.asked_save == ["Untitled", "Untitled 3"]
    assert window.isVisible()
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == ["Untitled 3*"]


def test_quit_closes_window_when_nothing_is_modified(window: MainWindow) -> None:
    window.new_document()
    window.registry.action("file.quit").trigger()
    assert not window.isVisible()
