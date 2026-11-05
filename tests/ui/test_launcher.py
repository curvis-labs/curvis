"""Tests for the launcher screen (docs/DESIGN.md section 10)."""

import itertools
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QMenu, QWidget
from pytestqt.qtbot import QtBot

from curvis.settings import RecentProject, Settings
from curvis.ui.dialogs import MARGIN
from curvis.ui.launcher import (
    COLUMN_WIDTH,
    EMPTY_TEXT,
    MISSING_TEXT,
    TILE_WIDTH,
    FolderRole,
    LauncherWidget,
    Tile,
    WhenRole,
    blank_tile,
    draw_plus,
)
from curvis.ui.main_window import MainWindow

from .conftest import Prompts

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def make_launcher(qtbot: QtBot, tiles: list[Tile] | None = None) -> LauncherWidget:
    launcher = LauncherWidget(clock=lambda: NOW, tiles=tiles)
    qtbot.addWidget(launcher)
    launcher.resize(1000, 600)
    return launcher


def project_files(tmp_path: Path, *names: str) -> list[Path]:
    paths = [tmp_path / f"{name}.cvp" for name in names]
    for path in paths:
        path.touch()
    return paths


def row_center(launcher: LauncherWidget, row: int) -> QPoint:
    lst = launcher.recent_list
    return lst.visualItemRect(lst.item(row)).center()


def click_row(qtbot: QtBot, launcher: LauncherWidget, row: int) -> None:
    qtbot.mouseClick(
        launcher.recent_list.viewport(), Qt.MouseButton.LeftButton, pos=row_center(launcher, row)
    )


def inside(child: QWidget, ancestor: QWidget) -> QRect:
    return QRect(child.mapTo(ancestor, QPoint(0, 0)), child.size())


# --- tiles -----------------------------------------------------------------------------


def test_only_blank_project_tile_for_now(qtbot: QtBot) -> None:
    launcher = make_launcher(qtbot)
    assert [t.tile.name for t in launcher.tiles] == ["Blank project"]
    assert launcher.blank_tile.tile.detail == ""  # no key combination on the launcher
    assert launcher.blank_tile.sizeHint().width() == TILE_WIDTH


def test_new_project_from_the_tile(qtbot: QtBot) -> None:
    launcher = make_launcher(qtbot)
    launcher.show()
    with qtbot.waitSignal(launcher.new_requested, timeout=1000):
        qtbot.mouseClick(launcher.blank_tile, Qt.MouseButton.LeftButton)


def test_blank_project_has_the_focus_and_enter_starts_a_project(qtbot: QtBot) -> None:
    launcher = make_launcher(qtbot)
    launcher.show()
    qtbot.waitExposed(launcher)
    assert launcher.focusWidget() is launcher.blank_tile
    with qtbot.waitSignal(launcher.new_requested, timeout=1000):
        qtbot.keyClick(launcher.blank_tile, Qt.Key.Key_Return)


def test_window_enter_on_launcher_opens_a_new_project(window: MainWindow, qtbot: QtBot) -> None:
    assert window.launcher.focusWidget() is window.launcher.blank_tile
    qtbot.keyClick(window.launcher.blank_tile, Qt.Key.Key_Return)
    assert window.tabs.count() == 1


def test_other_tiles_are_reported_by_key(qtbot: QtBot) -> None:
    sample = Tile("dose", "Dose-response", "Sample", draw_plus)
    launcher = make_launcher(qtbot, [blank_tile(), sample])
    with qtbot.waitSignal(launcher.tile_chosen, timeout=1000) as blocker:
        launcher.tiles[1].click()
    assert blocker.args == ["dose"]


def test_arrow_keys_move_along_the_tiles(qtbot: QtBot) -> None:
    tiles = [blank_tile(), *(Tile(f"s{i}", f"Sample {i}", "Sample", draw_plus) for i in (1, 2))]
    launcher = make_launcher(qtbot, tiles)
    launcher.show()
    qtbot.waitExposed(launcher)
    first, second, _third = launcher.tiles
    first.setFocus()
    qtbot.keyClick(first, Qt.Key.Key_Right)
    assert launcher.focusWidget() is second
    qtbot.keyClick(second, Qt.Key.Key_Left)
    assert launcher.focusWidget() is first
    qtbot.keyClick(first, Qt.Key.Key_Left)  # stays on the first tile
    assert launcher.focusWidget() is first


def test_no_key_combinations_are_shown(qtbot: QtBot) -> None:
    sample = Tile("dose", "Dose-response", "Sample", draw_plus)
    launcher = make_launcher(qtbot, [blank_tile(), sample])
    shown = [t.tile.detail for t in launcher.tiles]
    shown += [launcher.open_button.text(), launcher.open_button.toolTip()]
    assert not any("Ctrl" in text or "⌘" in text for text in shown)
    # blank and sample tiles keep the same height, so their names line up
    assert launcher.tiles[0].sizeHint() == launcher.tiles[1].sizeHint()


# --- Open project… -----------------------------------------------------------------------


def test_open_project_button(qtbot: QtBot) -> None:
    launcher = make_launcher(qtbot)
    assert launcher.open_button.text() == "&Open project…"
    with qtbot.waitSignal(launcher.open_requested, timeout=1000):
        qtbot.mouseClick(launcher.open_button, Qt.MouseButton.LeftButton)


def test_window_open_project_button_opens_the_chosen_file(
    window: MainWindow, qtbot: QtBot, prompts: Prompts, tmp_path: Path
) -> None:
    path = tmp_path / "p.cvp"
    window.new_document()
    assert window.current_tab is not None
    window.current_tab.path = None
    prompts.save_paths.append(path)
    assert window.save_tab(window.current_tab)
    assert window.close_tab(window.current_tab)
    prompts.open_paths.append(path)
    window.launcher.open_button.click()
    qtbot.waitUntil(lambda: window.tabs.count() == 1)
    assert window.current_tab is not None and window.current_tab.path == path


def test_tab_order_tiles_then_open_then_list(qtbot: QtBot) -> None:
    tiles = [blank_tile(), Tile("s", "Sample", "Sample", draw_plus)]
    launcher = make_launcher(qtbot, tiles)
    chain, widget = [], launcher.tiles[0]
    for _ in range(4):
        chain.append(widget)
        widget = widget.nextInFocusChain()
        while not widget.focusPolicy() & Qt.FocusPolicy.TabFocus:
            widget = widget.nextInFocusChain()
    assert chain == [*launcher.tiles, launcher.open_button, launcher.recent_list]


# --- recent list ------------------------------------------------------------------------


def test_recent_rows_show_name_folder_and_relative_time(qtbot: QtBot, tmp_path: Path) -> None:
    a, b, c = project_files(tmp_path, "Dose response", "Kinetics", "Old")
    launcher = make_launcher(qtbot)
    launcher.set_recent_projects(
        [
            RecentProject(a, NOW - timedelta(hours=3)),
            RecentProject(b, NOW - timedelta(days=1, hours=2)),
            RecentProject(c, NOW - timedelta(days=2)),
        ]
    )
    lst = launcher.recent_list
    assert [lst.item(i).text() for i in range(3)] == ["Dose response", "Kinetics", "Old"]
    assert [lst.item(i).data(WhenRole) for i in range(3)] == [
        "3 hours ago",
        "Yesterday",
        "2 days ago",
    ]
    assert lst.item(0).data(FolderRole) == str(tmp_path)
    assert lst.item(0).toolTip() == str(a)


def test_home_folder_is_written_as_tilde(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    if os.name != "posix":
        pytest.skip("~ is a POSIX convention")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / "Lab").mkdir()
    (path,) = project_files(tmp_path / "Lab", "x")
    launcher = make_launcher(qtbot)
    launcher.set_recent_projects([RecentProject(path, NOW)])
    assert launcher.recent_list.item(0).data(FolderRole) == "~/Lab"


def test_open_a_recent_row_with_one_click(qtbot: QtBot, tmp_path: Path) -> None:
    a, b = project_files(tmp_path, "a", "b")
    launcher = make_launcher(qtbot)
    launcher.set_recent_projects([RecentProject(a, NOW), RecentProject(b, NOW)])
    launcher.show()
    qtbot.waitExposed(launcher)
    with qtbot.waitSignal(launcher.recent_chosen, timeout=1000) as blocker:
        click_row(qtbot, launcher, 1)
    assert blocker.args == [b]


def test_right_click_does_not_open(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (a,) = project_files(tmp_path, "a")
    launcher = make_launcher(qtbot)
    launcher.set_recent_projects([RecentProject(a, NOW)])
    launcher.show()
    qtbot.waitExposed(launcher)
    monkeypatch.setattr(launcher, "_exec_menu", lambda menu, pos: None)
    chosen: list[Path] = []
    launcher.recent_chosen.connect(chosen.append)
    qtbot.mouseClick(
        launcher.recent_list.viewport(), Qt.MouseButton.RightButton, pos=row_center(launcher, 0)
    )
    assert chosen == []


def test_open_a_recent_row_with_enter(qtbot: QtBot, tmp_path: Path) -> None:
    a, b = project_files(tmp_path, "a", "b")
    launcher = make_launcher(qtbot)
    launcher.set_recent_projects([RecentProject(a, NOW), RecentProject(b, NOW)])
    launcher.show()
    qtbot.waitExposed(launcher)
    launcher.recent_list.setFocus()
    assert launcher.recent_list.currentRow() == 0
    qtbot.keyClick(launcher.recent_list, Qt.Key.Key_Down)
    with qtbot.waitSignal(launcher.recent_chosen, timeout=1000) as blocker:
        qtbot.keyClick(launcher.recent_list, Qt.Key.Key_Return)
    assert blocker.args == [b]


def test_window_opens_a_recent_project_by_click(
    window: MainWindow, qtbot: QtBot, prompts: Prompts, settings: Settings, tmp_path: Path
) -> None:
    path = tmp_path / "saved.cvp"
    window.new_document()
    assert window.current_tab is not None
    prompts.save_paths.append(path)
    assert window.save_tab(window.current_tab)
    assert window.close_tab(window.current_tab)
    assert window.launcher.recent_list.count() == 1
    click_row(qtbot, window.launcher, 0)
    qtbot.waitUntil(lambda: window.tabs.count() == 1)
    assert window.current_tab is not None and window.current_tab.path == path


def test_missing_file_is_dimmed_and_does_not_open(qtbot: QtBot, tmp_path: Path) -> None:
    gone = tmp_path / "gone.cvp"
    launcher = make_launcher(qtbot)
    launcher.set_recent_projects([RecentProject(gone, NOW)])
    launcher.show()
    qtbot.waitExposed(launcher)
    item = launcher.recent_list.item(0)
    assert item.data(WhenRole) == MISSING_TEXT == "File not found"
    assert launcher.is_missing(0)
    assert item.flags() & Qt.ItemFlag.ItemIsEnabled  # still reachable, so it can be removed
    chosen: list[Path] = []
    launcher.recent_chosen.connect(chosen.append)
    click_row(qtbot, launcher, 0)
    launcher.recent_list.setFocus()
    qtbot.keyClick(launcher.recent_list, Qt.Key.Key_Return)
    menu = launcher.recent_menu(0)
    assert not menu.actions()[0].isEnabled()
    menu.actions()[0].trigger()
    assert chosen == []


def test_context_menu_has_open_and_remove(qtbot: QtBot, tmp_path: Path) -> None:
    (a,) = project_files(tmp_path, "a")
    launcher = make_launcher(qtbot)
    launcher.set_recent_projects([RecentProject(a, NOW)])
    menu = launcher.recent_menu(0)
    assert [action.text() for action in menu.actions()] == ["Open", "Remove from list"]
    with qtbot.waitSignal(launcher.recent_chosen, timeout=1000) as blocker:
        menu.actions()[0].trigger()
    assert blocker.args == [a]


@pytest.mark.parametrize("how", ["mouse", "menu key", "shift+f10"])
def test_context_menu_opens_by_mouse_and_keyboard(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    a, b = project_files(tmp_path, "a", "b")
    launcher = make_launcher(qtbot)
    launcher.set_recent_projects([RecentProject(a, NOW), RecentProject(b, NOW)])
    launcher.show()
    qtbot.waitExposed(launcher)
    shown: list[list[str]] = []

    def record(menu: QMenu, pos: QPoint) -> None:
        shown.append([action.text() for action in menu.actions()])

    monkeypatch.setattr(launcher, "_exec_menu", record)
    lst = launcher.recent_list
    lst.setFocus()
    lst.setCurrentRow(1)
    if how == "mouse":
        pos = row_center(launcher, 1)
        event = QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse, pos, lst.viewport().mapToGlobal(pos)
        )
        QApplication.sendEvent(lst.viewport(), event)
    elif how == "menu key":
        event = QContextMenuEvent(QContextMenuEvent.Reason.Keyboard, QPoint(0, 0), QPoint(0, 0))
        QApplication.sendEvent(lst.viewport(), event)
    else:
        qtbot.keyClick(lst, Qt.Key.Key_F10, Qt.KeyboardModifier.ShiftModifier)
    assert shown == [["Open", "Remove from list"]]


def test_window_remove_from_list_keeps_the_file(
    window: MainWindow, settings: Settings, tmp_path: Path
) -> None:
    a, b = project_files(tmp_path, "a", "b")
    settings.add_recent_project(a, NOW - timedelta(hours=1))
    settings.add_recent_project(b, NOW)
    assert window.launcher.recent_list.count() == 2
    menu = window.launcher.recent_menu(0)
    menu.actions()[1].trigger()
    assert [r.path for r in settings.recent_projects] == [a]
    assert window.launcher.recent_list.count() == 1
    assert b.is_file()  # the file is not touched


def test_window_remove_a_missing_file_from_list(
    window: MainWindow, settings: Settings, tmp_path: Path
) -> None:
    settings.add_recent_project(tmp_path / "gone.cvp")
    window.launcher.recent_menu(0).actions()[1].trigger()
    assert settings.recent_projects == []


def test_window_launcher_follows_recent_setting(
    window: MainWindow, settings: Settings, tmp_path: Path
) -> None:
    (path,) = project_files(tmp_path, "p")
    assert window.launcher.recent_list.count() == 0
    settings.add_recent_project(path)
    assert window.launcher.recent_list.count() == 1
    assert window.launcher.recent_list.item(0).data(WhenRole) == "Just now"


# --- empty state and layout -------------------------------------------------------------


def test_empty_state(qtbot: QtBot, tmp_path: Path) -> None:
    launcher = make_launcher(qtbot)
    launcher.show()
    qtbot.waitExposed(launcher)
    label = launcher.empty_state.label
    assert label.text() == EMPTY_TEXT == "Projects you open or save will appear here."
    assert label.isVisible()
    assert launcher.recent_list.isVisible()
    (path,) = project_files(tmp_path, "p")
    launcher.set_recent_projects([RecentProject(path, NOW)])
    assert not label.isVisible()
    launcher.set_recent_projects([])
    assert label.isVisible()


def test_layout_is_a_centred_column_760_wide(qtbot: QtBot, tmp_path: Path) -> None:
    launcher = make_launcher(qtbot)
    launcher.set_recent_projects([RecentProject(p, NOW) for p in project_files(tmp_path, "a")])
    launcher.resize(1000, 640)
    launcher.show()
    qtbot.waitExposed(launcher)
    column = inside(launcher.column, launcher)
    assert column.width() == COLUMN_WIDTH
    assert column.left() == (1000 - COLUMN_WIDTH) // 2
    heading = inside(launcher.recent_label, launcher)
    button = inside(launcher.open_button, launcher)
    assert button.right() == column.right()  # Open project… at the heading's right
    assert abs(button.center().y() - heading.center().y()) <= button.height() // 2
    lst = inside(launcher.recent_list, launcher)
    assert lst.top() > button.bottom()
    assert lst.height() == launcher.recent_list.content_height()  # takes its rows' height


def test_layout_at_the_narrow_size_uses_full_width_and_wraps_tiles(qtbot: QtBot) -> None:
    tiles = [blank_tile(), *(Tile(f"s{i}", f"Sample {i}", "Sample", draw_plus) for i in range(3))]
    launcher = make_launcher(qtbot, tiles)
    launcher.resize(480, 600)
    launcher.show()
    qtbot.waitExposed(launcher)
    column = inside(launcher.column, launcher)
    assert column.left() == MARGIN
    assert column.width() == 480 - 2 * MARGIN
    rects = [inside(t, launcher) for t in launcher.tiles]
    assert all(column.contains(r) for r in rects)  # nothing cut off
    assert rects[0].top() == rects[1].top()  # two tiles per row at this width
    assert rects[2].top() > rects[0].bottom()  # the others wrapped
    assert rects[2].left() == rects[0].left()
    heading = inside(launcher.recent_label, launcher)
    assert heading.top() > rects[3].bottom()  # the heading moved down under the tiles
    button = inside(launcher.open_button, launcher)
    assert button.right() <= column.right() and not button.intersects(heading)
    for a, b in itertools.pairwise(rects):
        assert not a.intersects(b)


def test_empty_list_fills_the_column(qtbot: QtBot) -> None:
    launcher = make_launcher(qtbot)
    launcher.resize(1000, 640)
    launcher.show()
    qtbot.waitExposed(launcher)
    lst = inside(launcher.recent_list, launcher)
    column = inside(launcher.column, launcher)
    assert lst.bottom() == column.bottom()
    assert lst.height() >= 120


def test_hovered_row_is_shaded_with_text_at_7_percent(qtbot: QtBot, tmp_path: Path) -> None:
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

    from curvis.ui.launcher import HOVER_ALPHA

    (a,) = project_files(tmp_path, "a")
    launcher = make_launcher(qtbot)
    launcher.set_recent_projects([RecentProject(a, NOW)])
    lst = launcher.recent_list
    palette = lst.palette()
    index = lst.model().index(0, 0)

    def pixel(hovered: bool) -> QColor:
        image = QImage(400, 48, QImage.Format.Format_ARGB32)
        image.fill(palette.base().color())
        option = QStyleOptionViewItem()
        option.initFrom(lst)
        option.rect = QRect(0, 0, 400, 48)
        option.widget = lst
        option.state = QStyle.StateFlag.State_Enabled
        if hovered:
            option.state |= QStyle.StateFlag.State_MouseOver
        painter = QPainter(image)
        lst.itemDelegate().paint(painter, option, index)
        painter.end()
        return image.pixelColor(300, 4)  # an empty spot of the row

    base, text = palette.base().color(), palette.text().color()
    assert pixel(False) == base
    shaded = pixel(True)
    expected = base.redF() + (text.redF() - base.redF()) * HOVER_ALPHA
    assert abs(shaded.redF() - expected) < 0.01
