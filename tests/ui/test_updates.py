"""Update checks and installs: the GitHub client against a local web server, the bar,
the Update dialog and the controller's install flow (curvis.update, curvis.ui.updates)."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QUrl, Signal
from pytestqt.qtbot import QtBot

from curvis.settings import Settings
from curvis.ui.main_window import MainWindow
from curvis.ui.updates import CLOSE_PROJECTS, State, UpdateController
from curvis.update.client import BAD_CHECKSUM, NO_CHECKSUMS, OFFLINE, Downloader, UpdateChecker
from curvis.update.install import WINDOWS_ARGUMENTS, Command, Method, Target
from curvis.update.releases import Asset, Release, Version
from tests.ui.conftest import Prompts

WINDOWS = Target(Method.RUN_INSTALLER, "windows", "AMD64")
SETUP = "Curvis-9.0.0-setup.exe"


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    """A web server for the files in a folder: (base URL, folder)."""
    root = tmp_path / "www"
    root.mkdir()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(root)))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", root
    httpd.shutdown()
    httpd.server_close()


def release(
    base: str = "https://example.org",
    *,
    files: dict[str, bytes] | None = None,
    sums: bool = True,
    digest: str | None = None,
) -> Release:
    files = files if files is not None else {SETUP: b"new installer"}
    assets = [Asset(name, f"{base}/{name}", len(data), digest) for name, data in files.items()]
    if sums:
        assets.append(Asset("SHA256SUMS.txt", f"{base}/SHA256SUMS.txt", 100))
    return Release(
        Version(9, 0, 0),
        "v9.0.0",
        "Curvis 9.0.0",
        "**Faster** fits.",
        "https://github.com/curvis-labs/curvis/releases/tag/v9.0.0",
        False,
        tuple(assets),
    )


def finish(qtbot: QtBot) -> None:
    """Let Qt finish with the network replies before the objects go (it aborts else)."""
    qtbot.wait(100)


def publish(root: Path, files: dict[str, bytes]) -> None:
    lines = []
    for name, data in files.items():
        (root / name).write_bytes(data)
        lines.append(f"{hashlib.sha256(data).hexdigest()}  {name}")
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n")


# --- the GitHub client -----------------------------------------------------------------


def release_list(*tags: str) -> list[dict[str, object]]:
    return [
        {"tag_name": t, "name": t, "body": "", "html_url": "https://github.com/x", "assets": []}
        for t in tags
    ]


def test_checker_finds_a_newer_release(qtbot: QtBot, server: tuple[str, Path]) -> None:
    base, root = server
    (root / "releases.json").write_text(json.dumps(release_list("v1.0.0", "v1.1.0")))
    checker = UpdateChecker("1.0.0", url=f"{base}/releases.json")
    with qtbot.waitSignal(checker.found, timeout=10_000) as found:
        checker.check()
    finish(qtbot)
    assert str(found.args[0].version) == "1.1.0"
    assert not checker.busy


def test_checker_up_to_date(qtbot: QtBot, server: tuple[str, Path]) -> None:
    base, root = server
    (root / "releases.json").write_text(json.dumps(release_list("v1.0.0")))
    checker = UpdateChecker("1.0.0", url=f"{base}/releases.json")
    with qtbot.waitSignal(checker.up_to_date, timeout=10_000):
        checker.check()
    finish(qtbot)


def test_checker_failure(qtbot: QtBot, server: tuple[str, Path]) -> None:
    base, _root = server
    checker = UpdateChecker("1.0.0", url=f"{base}/missing.json")
    with qtbot.waitSignal(checker.failed, timeout=10_000) as failed:
        checker.check()
    finish(qtbot)
    assert failed.args == [OFFLINE]


def test_download_is_checked(qtbot: QtBot, server: tuple[str, Path], tmp_path: Path) -> None:
    base, root = server
    files = {SETUP: b"new installer" * 1000}
    publish(root, files)
    rel = release(base, files=files)
    downloader = Downloader(rel, rel.assets[0], tmp_path / "cache" / "updates")
    with qtbot.waitSignal(downloader.finished, timeout=10_000) as done:
        downloader.start()
    finish(qtbot)
    path = done.args[0]
    assert path == tmp_path / "cache" / "updates" / SETUP
    assert path.read_bytes() == files[SETUP]
    assert [p.name for p in path.parent.iterdir()] == [SETUP]  # no .part left


def test_download_that_does_not_match_is_deleted(
    qtbot: QtBot, server: tuple[str, Path], tmp_path: Path
) -> None:
    base, root = server
    publish(root, {SETUP: b"what was released"})
    (root / SETUP).write_bytes(b"something else")
    rel = release(base, files={SETUP: b"x"})
    folder = tmp_path / "updates"
    downloader = Downloader(rel, rel.assets[0], folder)
    with qtbot.waitSignal(downloader.failed, timeout=10_000) as failed:
        downloader.start()
    finish(qtbot)
    assert failed.args == [BAD_CHECKSUM]
    assert list(folder.iterdir()) == []


def test_download_needs_checksums(qtbot: QtBot, tmp_path: Path) -> None:
    rel = release(sums=False)
    downloader = Downloader(rel, rel.assets[0], tmp_path)
    with qtbot.waitSignal(downloader.failed, timeout=1000) as failed:
        downloader.start()
    finish(qtbot)
    assert failed.args == [NO_CHECKSUMS]


def test_download_checks_githubs_digest_too(
    qtbot: QtBot, server: tuple[str, Path], tmp_path: Path
) -> None:
    base, root = server
    files = {SETUP: b"new installer"}
    publish(root, files)
    rel = release(base, files=files, digest="0" * 64)
    downloader = Downloader(rel, rel.assets[0], tmp_path)
    with qtbot.waitSignal(downloader.failed, timeout=10_000) as failed:
        downloader.start()
    finish(qtbot)
    assert failed.args == [BAD_CHECKSUM]


# --- the controller ----------------------------------------------------------------------


class FakeChecker(UpdateChecker):
    def __init__(self) -> None:
        super().__init__("1.0.0")
        self.checks = 0

    def check(self) -> None:
        self.checks += 1


class FakeDownloader(QObject):
    progress = Signal(int, int)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(b"installer")
        self.progress.emit(9, 9)
        self.finished.emit(self.path)

    def cancel(self) -> None:
        pass


class Harness:
    def __init__(
        self, window: MainWindow, settings: Settings, tmp_path: Path, target: Target = WINDOWS
    ) -> None:
        self.started: list[Command] = []
        self.opened: list[QUrl] = []
        self.quit = 0
        self.checker = FakeChecker()
        window.quit_for_update = self._quit  # type: ignore[method-assign]
        self.controller = UpdateController(
            window,
            settings,
            target=target,
            checker=self.checker,
            downloader=lambda rel, folder: FakeDownloader(folder / SETUP),  # type: ignore[arg-type,return-value]
            cache=tmp_path / "cache",
            start_detached=self._start,
            open_url=self.opened.append,
        )

    def _start(self, command: Command) -> bool:
        self.started.append(command)
        return True

    def _quit(self) -> None:
        self.quit += 1


@pytest.fixture
def harness(window: MainWindow, settings: Settings, tmp_path: Path) -> Harness:
    return Harness(window, settings, tmp_path)


def test_quiet_check_shows_the_bar(window: MainWindow, harness: Harness) -> None:
    harness.controller.check_at_start()
    assert harness.checker.checks == 1
    harness.checker.found.emit(release())
    bar = window.update_bar()
    assert bar.isVisible()
    assert bar.label.text() == "Curvis 9.0.0 is available."
    assert bar.install_button.text() == "Install and restart"
    assert harness.controller.dialog is None  # quiet: no dialog


def test_quiet_check_says_nothing_when_up_to_date_or_offline(
    window: MainWindow, harness: Harness
) -> None:
    harness.controller.check_at_start()
    harness.checker.up_to_date.emit()
    harness.checker.failed.emit(OFFLINE)
    assert not window.update_bar().isVisible()
    assert harness.controller.dialog is None


def test_skipped_version_is_not_offered_at_start(
    window: MainWindow, harness: Harness, settings: Settings
) -> None:
    harness.controller.check_at_start()
    harness.checker.found.emit(release())
    window.update_bar().skip_button.click()
    assert settings.skipped_update == "9.0.0"
    assert not window.update_bar().isVisible()
    harness.controller.check_at_start()
    harness.checker.found.emit(release())
    assert not window.update_bar().isVisible()


def test_setting_off_means_no_check(
    window: MainWindow, harness: Harness, settings: Settings
) -> None:
    settings.check_for_updates = False
    harness.controller.check_at_start()
    assert harness.checker.checks == 0


def test_check_now_says_up_to_date(harness: Harness) -> None:
    harness.controller.check_now()
    dialog = harness.controller.dialog
    assert dialog is not None and dialog.isVisible() and dialog.state is State.CHECKING
    assert not dialog.ok_button.isEnabled()
    harness.checker.up_to_date.emit()
    assert dialog.state is State.UP_TO_DATE
    assert dialog.heading.text() == "Curvis 1.0.0 is the newest version."


def test_check_now_shows_what_is_new(harness: Harness) -> None:
    harness.controller.check_now()
    harness.checker.found.emit(release())
    dialog = harness.controller.dialog
    assert dialog is not None and dialog.state is State.AVAILABLE
    assert "Faster" in dialog.notes.toPlainText()
    assert dialog.ok_button.text() == "Install and restart"


def test_check_now_reports_a_failure_and_retries(harness: Harness) -> None:
    harness.controller.check_now()
    harness.checker.failed.emit(OFFLINE)
    dialog = harness.controller.dialog
    assert dialog is not None and dialog.state is State.FAILED
    assert dialog.skeleton.feedback.text() == OFFLINE
    dialog.ok_button.click()
    assert harness.checker.checks == 2


def test_install_downloads_asks_to_save_then_runs_the_installer(
    window: MainWindow, harness: Harness, prompts: Prompts
) -> None:
    window.new_document()
    harness.controller.check_at_start()
    harness.checker.found.emit(release())
    window.update_bar().install_button.click()
    (command,) = harness.started
    assert command.program.endswith(SETUP)
    assert command.arguments == WINDOWS_ARGUMENTS
    assert harness.quit == 1
    assert window.document_tabs() == []  # closed first, as when quitting


def test_cancel_saving_keeps_curvis_open(
    window: MainWindow, harness: Harness, prompts: Prompts, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(window, "close_all", lambda: False)
    harness.controller.release = release()
    harness.controller.install()
    dialog = harness.controller.dialog
    assert dialog is not None and dialog.state is State.FAILED
    assert dialog.skeleton.feedback.text() == CLOSE_PROJECTS
    assert harness.started == [] and harness.quit == 0
    # Trying again does not download again.
    monkeypatch.setattr(window, "close_all", lambda: True)
    dialog.ok_button.click()
    assert len(harness.started) == 1 and harness.quit == 1


def test_from_source_opens_the_release_page(
    window: MainWindow, settings: Settings, tmp_path: Path
) -> None:
    harness = Harness(window, settings, tmp_path, Target(Method.OPEN_PAGE, "source", "x86_64"))
    harness.controller.release = release()
    harness.controller.install()
    assert [u.toString() for u in harness.opened] == [release().page]
    assert harness.started == [] and harness.quit == 0


def test_help_menu_has_check_for_updates(window: MainWindow) -> None:
    action = window.registry.action("help.check_updates")
    assert action.text() == "Check for &updates…"
    menus = [a.text() for a in window.menuBar().actions()]
    assert menus[-1] == "&Help"


# --- the setting -------------------------------------------------------------------------


def test_update_checks_are_on_by_default_and_reset(settings: Settings) -> None:
    assert settings.check_for_updates
    settings.check_for_updates = False
    settings.skipped_update = "9.0.0"
    settings.reset_to_defaults()
    assert settings.check_for_updates
    assert settings.skipped_update == "9.0.0"  # not a setting of the dialog


def test_settings_dialog_turns_update_checks_off(qtbot: QtBot, settings: Settings) -> None:
    from curvis.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    assert dialog.check_updates.isChecked()
    dialog.check_updates.setChecked(False)
    dialog.accept()
    assert not settings.check_for_updates
