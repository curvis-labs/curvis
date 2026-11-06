"""Opening the sample projects from the launcher tiles and from the File menu (New from sample)."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPalette
from PySide6.QtWidgets import QMenu
from pytestqt.qtbot import QtBot

from curvis.ui.document_tab import DocumentTab
from curvis.ui.launcher import SAMPLE_NAMES, TileButton
from curvis.ui.main_window import MainWindow

from .conftest import Prompts


def settle(qtbot: QtBot, tab: DocumentTab) -> None:
    analyses, graphs = tab.analysis_engine, tab.graph_engine
    assert analyses is not None and graphs is not None
    document = tab.document
    qtbot.waitUntil(
        lambda: (
            all(a.id in analyses.results for a in document.analyses)
            and all(g.id in graphs.data and not graphs.is_stale(g.id) for g in document.graphs)
        ),
        timeout=20_000,
    )


@pytest.mark.parametrize("index", [1, 2, 3])
def test_a_sample_tile_opens_a_new_untitled_copy(
    window: MainWindow, qtbot: QtBot, index: int
) -> None:
    tile = window.launcher.tiles[index]
    qtbot.mouseClick(tile, Qt.MouseButton.LeftButton)
    tab = window.current_tab
    assert tab is not None
    assert tab.path is None
    assert tab.title == tile.tile.name
    assert not tab.is_modified
    assert window.settings.recent_projects == []
    settle(qtbot, tab)
    assert not tab.analysis_engine.errors  # type: ignore[union-attr]


def test_a_sample_opens_by_keyboard_from_the_tiles(window: MainWindow, qtbot: QtBot) -> None:
    first = window.launcher.blank_tile
    qtbot.keyClick(first, Qt.Key.Key_Right)
    second = window.launcher.focusWidget()
    assert isinstance(second, TileButton)
    qtbot.keyClick(second, Qt.Key.Key_Return)
    assert window.current_tab is not None
    assert window.current_tab.title == "Group comparison"


def test_opening_a_sample_twice_gives_two_copies(window: MainWindow) -> None:
    first = window.new_from_sample("dose_response")
    second = window.new_from_sample("dose_response")
    assert first is not second
    assert first.document.id != second.document.id


def test_save_asks_where_to_save_the_copy(window: MainWindow, prompts: Prompts) -> None:
    window.new_from_sample("xy_replicates")
    prompts.save_paths.append(None)  # cancel the Save dialog
    assert not window.save_current()
    assert prompts.save_paths == []  # the Save dialog was asked


def test_samples_are_in_the_file_menu_and_the_palette(window: MainWindow) -> None:
    window.ensure_sample_actions()
    for key, name in SAMPLE_NAMES.items():
        action = window.registry.action(f"file.sample.{key}")
        entry = next(e for e in window.registry.entries() if e.action is action)
        assert entry.category == "New from sample"
        assert entry.text == name
    file_menu = window.menuBar().actions()[0].menu()
    submenu = next(a.menu() for a in file_menu.actions() if a.menu() is not None)
    assert submenu.title().replace("&", "") == "New from sample"
    assert [a.text().replace("&", "") for a in submenu.actions()] == list(SAMPLE_NAMES.values())


def test_sample_actions_are_made_when_first_needed(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not at start-up (the window shell stays quick), but when the File menu or the
    command palette opens."""
    assert not any(e.id.startswith("file.sample.") for e in window.registry.entries())
    file_menu = window.menuBar().actions()[0].menu()
    assert isinstance(file_menu, QMenu)
    file_menu.aboutToShow.emit()
    texts = [a.text() for a in file_menu.actions() if not a.isSeparator()]
    assert texts[:3] == ["&New project", "New from sa&mple", "&Open…"]
    assert len([e for e in window.registry.entries() if e.category == "New from sample"]) == 3
    file_menu.aboutToShow.emit()  # made once
    assert len([e for e in window.registry.entries() if e.category == "New from sample"]) == 3


def test_palette_lists_the_samples(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    from curvis.ui.command_palette import CommandPalette

    listed: list[str] = []
    monkeypatch.setattr(
        CommandPalette, "exec", lambda self: listed.extend(e.id for e in self.visible_entries())
    )
    window.show_command_palette()
    assert {f"file.sample.{key}" for key in SAMPLE_NAMES} <= set(listed)


def test_file_menu_order_with_a_project_open(window: MainWindow) -> None:
    window.new_document()
    window.ensure_sample_actions()
    file_menu = window.menuBar().actions()[0].menu()
    assert isinstance(file_menu, QMenu)
    texts = [a.text() for a in file_menu.actions() if not a.isSeparator()]
    assert texts[:5] == [
        "&New project",
        "New &table",
        "&Rename table",
        "New from sa&mple",
        "&Open…",
    ]


def test_sample_menu_works_while_a_project_is_open(window: MainWindow) -> None:
    window.new_document()
    window.ensure_sample_actions()
    window.registry.action("file.sample.group_comparison").trigger()
    assert [t.title for t in window.document_tabs()] == ["Untitled", "Group comparison"]


@pytest.mark.parametrize("index", [1, 2, 3])
def test_sample_pictures_draw_with_the_link_colour(window: MainWindow, index: int) -> None:
    tile = window.launcher.tiles[index]
    image = QImage(tile.size(), QImage.Format.Format_ARGB32)
    image.fill(0)
    tile.render(image)
    link = tile.palette().color(QPalette.ColorRole.Link)
    colours = {image.pixelColor(x, y).rgb() for x in range(image.width()) for y in range(104)}
    assert link.rgb() in colours
