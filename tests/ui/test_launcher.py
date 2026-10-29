"""Tests for the launcher screen."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from pytestqt.qtbot import QtBot

from curvis.settings import RecentProject, Settings
from curvis.ui.launcher import LauncherWidget
from curvis.ui.main_window import MainWindow

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def make_launcher(qtbot: QtBot) -> LauncherWidget:
    launcher = LauncherWidget(clock=lambda: NOW)
    qtbot.addWidget(launcher)
    return launcher


def test_buttons_emit_signals(qtbot: QtBot) -> None:
    launcher = make_launcher(qtbot)
    with qtbot.waitSignal(launcher.new_requested, timeout=1000):
        launcher.new_button.click()
    with qtbot.waitSignal(launcher.open_requested, timeout=1000):
        launcher.open_button.click()


def test_empty_recent_list_shows_hint(qtbot: QtBot) -> None:
    launcher = make_launcher(qtbot)
    launcher.show()
    assert launcher.empty_label.isVisible()
    assert not launcher.recent_list.isVisible()


def test_recent_projects_show_name_folder_and_relative_time(qtbot: QtBot, tmp_path: Path) -> None:
    a, b = tmp_path / "Dose response.cvp", tmp_path / "Kinetics.cvp"
    a.touch()
    b.touch()
    launcher = make_launcher(qtbot)
    launcher.show()
    launcher.set_recent_projects(
        [RecentProject(a, NOW - timedelta(days=2)), RecentProject(b, NOW - timedelta(hours=3))]
    )
    assert launcher.recent_list.isVisible()
    texts = [launcher.recent_list.item(i).text() for i in range(2)]
    assert texts[0] == f"Dose response\n{tmp_path}  ·  2 days ago"
    assert texts[1] == f"Kinetics\n{tmp_path}  ·  3 hours ago"


def test_missing_file_is_disabled_and_cannot_be_chosen(qtbot: QtBot, tmp_path: Path) -> None:
    launcher = make_launcher(qtbot)
    launcher.set_recent_projects([RecentProject(tmp_path / "gone.cvp", NOW)])
    item = launcher.recent_list.item(0)
    assert "File not found" in item.text()
    chosen: list[Path] = []
    launcher.recent_chosen.connect(chosen.append)
    launcher.recent_list.itemActivated.emit(item)
    assert chosen == []


def test_choosing_a_recent_project_emits_its_path(qtbot: QtBot, tmp_path: Path) -> None:
    path = tmp_path / "x.cvp"
    path.touch()
    launcher = make_launcher(qtbot)
    launcher.set_recent_projects([RecentProject(path, NOW)])
    with qtbot.waitSignal(launcher.recent_chosen, timeout=1000) as blocker:
        launcher.recent_list.itemClicked.emit(launcher.recent_list.item(0))
    assert blocker.args == [path]


def test_window_launcher_follows_recent_setting(
    window: MainWindow, settings: Settings, tmp_path: Path
) -> None:
    path = tmp_path / "p.cvp"
    path.touch()
    assert window.launcher.recent_list.count() == 0
    settings.add_recent_project(path)
    assert window.launcher.recent_list.count() == 1
    assert window.launcher.recent_list.item(0).text().endswith("just now")
