"""Updates in the window: the bar that says a new version is out, the Update dialog, and
the controller that checks, downloads and installs.

At start-up (when Settings allow it) the check is quiet: only a newer release that the
user has not skipped shows the bar. *Check for updates…* (Help menu) always opens the dialog
and says what it found. *Install and restart* downloads the file for this computer,
checks it against the release's SHA-256 checksums, asks to save unsaved projects (as
closing does), then installs and starts the new version (see :mod:`curvis.update.install`).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QProcess, QStandardPaths, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPalette
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QToolButton,
    QWidget,
)

from curvis import __version__
from curvis.settings import Settings
from curvis.ui.dialogs import (
    MARGIN,
    SIMPLE_MINIMUM_WIDTH,
    SPACING,
    DialogSkeleton,
    Severity,
    secondary_label,
)
from curvis.update.client import Downloader, UpdateChecker
from curvis.update.install import (
    MAC_SCRIPT,
    Command,
    Method,
    Target,
    detect,
    download_folder,
    install_command,
    replace_appimage,
    restart_command,
)
from curvis.update.releases import RELEASES_PAGE, Release

if TYPE_CHECKING:
    from curvis.ui.main_window import MainWindow

TITLE = "Update Curvis"
CHECKING = "Checking for updates…"
UP_TO_DATE = "Curvis {version} is the newest version."
AVAILABLE = "Curvis {new} is available. You have {current}."
BAR_TEXT = "Curvis {new} is available."
DOWNLOADING = "Downloading Curvis {new}…"
INSTALLING = "Installing Curvis {new}…"
CLOSE_PROJECTS = "Save or close your projects to install the update."
NOT_INSTALLED = "Curvis was not updated. {reason}"
NO_FILE = "This release has no file for this computer. Download it from the release page."

INSTALL_NOTES = {
    Method.RUN_INSTALLER: (
        "Curvis will close, install the update and start again. "
        "Your settings and projects are kept."
    ),
    Method.REPLACE_APP_BUNDLE: (
        "Curvis will close, replace itself with the new version and start again. "
        "Your settings and projects are kept."
    ),
    Method.REPLACE_APPIMAGE: (
        "Curvis will replace its AppImage with the new one and start again. "
        "Your settings and projects are kept."
    ),
    Method.APT: (
        "Curvis will ask for your password to install the new package, then start "
        "again. Your settings and projects are kept."
    ),
    Method.OPEN_FILE: (
        "Curvis cannot replace itself here, so it downloads the new version and opens "
        "it for you to install. Your settings and projects are kept."
    ),
    Method.OPEN_PAGE: "Download the new version from the release page.",
}


def install_label(target: Target) -> str:
    """The text of the button that installs (or downloads) the update."""
    if target.method.restarts:
        return "Install and restart"
    if target.method is Method.OPEN_FILE:
        return "Download and open"
    return "Open download page"


def _size(n: int) -> str:
    return f"{n / 1_000_000:.0f} MB"


class UpdateBar(QFrame):
    """A slim bar across the top of the window: "Curvis 1.1.0 is available." with
    *Install and restart*, *What's new*, *Skip this version* and a close button."""

    install_requested = Signal()
    details_requested = Signal()
    skip_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("updateBar")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAutoFillBackground(True)
        self.setBackgroundRole(QPalette.ColorRole.AlternateBase)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(MARGIN, SPACING // 2, SPACING, SPACING // 2)
        layout.setSpacing(SPACING)
        self.label = QLabel(self)
        layout.addWidget(self.label, 1)
        self.install_button = QPushButton(self)
        self.install_button.clicked.connect(self.install_requested)
        self.details_button = QPushButton("What's new", self)
        self.details_button.clicked.connect(self.details_requested)
        self.skip_button = QPushButton("Skip this version", self)
        self.skip_button.clicked.connect(self.skip_requested)
        self.close_button = QToolButton(self)
        self.close_button.setText("✕")
        self.close_button.setAutoRaise(True)
        self.close_button.setToolTip("Not now")
        self.close_button.setAccessibleName("Not now")
        self.close_button.clicked.connect(self.hide)
        for button in (self.install_button, self.details_button, self.skip_button):
            layout.addWidget(button)
        layout.addWidget(self.close_button)
        self.hide()

    def show_release(self, release: Release, target: Target) -> None:
        self.label.setText(BAR_TEXT.format(new=release.version))
        self.install_button.setText(install_label(target))
        self.show()


class State(Enum):
    CHECKING = auto()
    UP_TO_DATE = auto()
    AVAILABLE = auto()
    DOWNLOADING = auto()
    INSTALLING = auto()
    FAILED = auto()


class UpdateDialog(QDialog):
    """Shows what the check found, the download's progress and any problem.

    The controller drives it; OK (*Install and restart*) emits ``install_requested``,
    *Skip this version* ``skip_requested``, and Cancel during a download
    ``cancel_requested``.
    """

    install_requested = Signal()
    skip_requested = Signal()
    cancel_requested = Signal()
    retry_requested = Signal()

    def __init__(self, target: Target, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.target = target
        self.state = State.CHECKING
        self.release: Release | None = None
        self.skeleton = DialogSkeleton(self, install_label(target))
        self.heading = QLabel(self)
        self.heading.setWordWrap(True)
        font = self.heading.font()
        font.setBold(True)
        self.heading.setFont(font)
        self.notes = QTextBrowser(self)
        self.notes.setOpenExternalLinks(True)
        self.notes.setAccessibleName("What's new")
        self.notes.setMinimumHeight(12 * self.notes.fontMetrics().height())
        self.how = secondary_label(INSTALL_NOTES[target.method], self)
        self.progress = QProgressBar(self)
        self.progress.setAccessibleName("Download progress")
        self.progress.setTextVisible(False)
        self.skeleton.body.addWidget(self.heading)
        self.skeleton.body.addWidget(self.notes, 1)
        self.skeleton.body.addWidget(self.how)
        self.skeleton.body.addWidget(self.progress)
        self.skip_button = QPushButton("&Skip this version", self)
        self.skip_button.setAutoDefault(False)
        self.skip_button.clicked.connect(self.skip_requested)
        self.page_button = QPushButton("Release &page", self)
        self.page_button.setAutoDefault(False)
        self.page_button.clicked.connect(self.open_page)
        self.skeleton.add_secondary(self.skip_button)
        self.skeleton.add_secondary(self.page_button)
        self.resize(560, 420)
        self.show_checking()

    @property
    def ok_button(self) -> QPushButton:
        button = self.skeleton.ok_button
        assert button is not None
        return button

    @property
    def cancel_button(self) -> QPushButton:
        from PySide6.QtWidgets import QDialogButtonBox

        button = self.skeleton.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        assert button is not None
        return button

    def _set(self, state: State, heading: str, *, notes: bool = False) -> None:
        self.state = state
        self.heading.setText(heading)
        self.notes.setVisible(notes)
        self.how.setVisible(state in (State.AVAILABLE, State.FAILED) and self.release is not None)
        self.progress.setVisible(state in (State.DOWNLOADING, State.INSTALLING))
        has_release = self.release is not None
        self.skip_button.setVisible(has_release and state in (State.AVAILABLE, State.FAILED))
        self.page_button.setVisible(has_release)
        self.ok_button.setVisible(state is not State.UP_TO_DATE)
        self.cancel_button.setText("Cancel" if state is State.DOWNLOADING else "Close")
        self.cancel_button.setEnabled(state is not State.INSTALLING)
        self._fit()

    def _fit(self) -> None:
        """The minimum size is what the shown controls need (wrapped text included)."""
        layout = self.layout()
        assert layout is not None
        layout.activate()
        hint = self.minimumSizeHint()
        width = max(SIMPLE_MINIMUM_WIDTH, hint.width())
        height = hint.height()
        if layout.hasHeightForWidth():
            height = max(height, layout.totalHeightForWidth(width))
        self.setMinimumSize(width, height)
        # Without the release notes there is nothing to grow: keep the dialog compact.
        tall = max(self.height(), height) if self.notes.isVisibleTo(self) else height
        self.resize(max(self.width(), width), tall)

    def show_checking(self) -> None:
        self._set(State.CHECKING, CHECKING)
        self.skeleton.set_problem(CHECKING)

    def show_up_to_date(self, version: str = __version__) -> None:
        self._set(State.UP_TO_DATE, UP_TO_DATE.format(version=version))
        self.skeleton.set_problem(None)

    def show_available(self, release: Release) -> None:
        self.release = release
        self._set(
            State.AVAILABLE, AVAILABLE.format(new=release.version, current=__version__), notes=True
        )
        self.notes.setMarkdown(release.notes or release.title)
        self.ok_button.setText(install_label(self.target))
        self.skeleton.set_problem(None)
        self._fit()

    def show_downloading(self, received: int = 0, total: int = 0) -> None:
        assert self.release is not None
        if self.state is not State.DOWNLOADING:
            self._set(State.DOWNLOADING, DOWNLOADING.format(new=self.release.version))
        self.progress.setRange(0, max(total, 0))
        if total > 0:
            self.progress.setValue(min(received, total))
            status = f"{_size(received)} of {_size(total)}"
        else:
            status = DOWNLOADING.format(new=self.release.version)
        self.skeleton.set_problem(status)

    def show_installing(self) -> None:
        assert self.release is not None
        self._set(State.INSTALLING, INSTALLING.format(new=self.release.version))
        self.progress.setRange(0, 0)
        self.skeleton.set_problem(INSTALLING.format(new=self.release.version))

    def show_problem(self, message: str, *, ok_text: str | None = None) -> None:
        """Something went wrong (or the user must act): say what, and offer OK again."""
        heading = (
            AVAILABLE.format(new=self.release.version, current=__version__)
            if self.release is not None
            else TITLE
        )
        self._set(State.FAILED, heading, notes=self.release is not None)
        self.ok_button.setText(
            ok_text or ("Try again" if self.release is None else install_label(self.target))
        )
        self.skeleton.set_problem(None)
        self.skeleton.set_status(message, Severity.WARNING)
        self._fit()

    def open_page(self) -> None:
        url = self.release.page if self.release is not None else RELEASES_PAGE
        QDesktopServices.openUrl(QUrl(url))

    def accept(self) -> None:  # OK does not close: the controller decides
        if self.state is State.FAILED and self.release is None:
            self.retry_requested.emit()
        elif self.state in (State.AVAILABLE, State.FAILED):
            self.install_requested.emit()

    def reject(self) -> None:
        if self.state is State.INSTALLING:
            return
        if self.state is State.DOWNLOADING:
            self.cancel_requested.emit()
        super().reject()


def _start_detached(command: Command) -> bool:
    process = QProcess()
    process.setProgram(command.program)
    process.setArguments(list(command.arguments))
    result = process.startDetached()
    return bool(result[0] if isinstance(result, tuple) else result)


def default_cache() -> Path:
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation))


class UpdateController(QObject):
    """Checks for updates and installs them for ``window``. The keyword arguments are
    for tests."""

    def __init__(
        self,
        window: MainWindow,
        settings: Settings,
        *,
        target: Target | None = None,
        checker: UpdateChecker | None = None,
        downloader: Callable[[Release, Path], Downloader] | None = None,
        cache: Path | None = None,
        start_detached: Callable[[Command], bool] = _start_detached,
        open_url: Callable[[QUrl], object] = QDesktopServices.openUrl,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.settings = settings
        self.target = target if target is not None else detect()
        self.checker = checker if checker is not None else UpdateChecker(parent=self)
        self._make_downloader = downloader or self._default_downloader
        self.cache = cache if cache is not None else default_cache()
        self.start_detached = start_detached
        self.open_url = open_url
        self.release: Release | None = None
        self.downloaded: Path | None = None
        self.dialog: UpdateDialog | None = None
        self._downloader: Downloader | None = None
        self._process: QProcess | None = None
        self._quiet = True
        self.checker.found.connect(self._found)
        self.checker.up_to_date.connect(self._up_to_date)
        self.checker.failed.connect(self._failed)
        self.bar = window.update_bar()
        self.bar.install_requested.connect(self.install)
        self.bar.details_requested.connect(self.show_details)
        self.bar.skip_requested.connect(self.skip)

    # --- checking ------------------------------------------------------------------

    def check_at_start(self) -> None:
        """A quiet check: only a release not skipped shows the bar."""
        if not self.settings.check_for_updates or self.checker.busy:
            return
        self._quiet = True
        self.checker.check()

    def check_now(self) -> None:
        """*Check for updates…*: opens the dialog and says what was found."""
        dialog = self._dialog()
        if self._downloader is None and self._process is None:
            if self.release is not None:
                dialog.show_available(self.release)
            else:
                self._quiet = False
                dialog.show_checking()
                self.checker.check()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _found(self, release: Release) -> None:
        self.release = release
        if self._quiet:
            if str(release.version) != self.settings.skipped_update:
                self.bar.show_release(release, self.target)
            return
        self._dialog().show_available(release)

    def _up_to_date(self) -> None:
        if not self._quiet:
            self._dialog().show_up_to_date()

    def _failed(self, message: str) -> None:
        if not self._quiet:
            self._dialog().show_problem(message)

    # --- choices ---------------------------------------------------------------------

    def show_details(self) -> None:
        if self.release is not None:
            dialog = self._dialog()
            dialog.show_available(self.release)
            dialog.show()

    def skip(self) -> None:
        if self.release is not None:
            self.settings.skipped_update = str(self.release.version)
        self.bar.hide()
        if self.dialog is not None:
            self.dialog.close()

    def install(self) -> None:
        """Download the update (unless already done), then install it."""
        release = self.release
        if release is None:
            return
        self.bar.hide()
        dialog = self._dialog()
        dialog.release = release
        dialog.show()
        asset = self.target.asset_for(release)
        if self.target.method is Method.OPEN_PAGE or asset is None:
            if asset is None and self.target.method is not Method.OPEN_PAGE:
                dialog.show_problem(NO_FILE, ok_text="Open download page")
                self.target = Target(Method.OPEN_PAGE, "source", self.target.machine)
                return
            self.open_url(QUrl(release.page))
            dialog.close()
            return
        if self.downloaded is not None and self.downloaded.is_file():
            self._install(self.downloaded)
            return
        downloader = self._make_downloader(release, download_folder(self.target, self.cache))
        self._downloader = downloader
        downloader.progress.connect(dialog.show_downloading)
        downloader.finished.connect(self._downloaded)
        downloader.failed.connect(self._download_failed)
        dialog.show_downloading(0, asset.size)
        downloader.start()

    def _default_downloader(self, release: Release, folder: Path) -> Downloader:
        asset = self.target.asset_for(release)
        assert asset is not None
        return Downloader(release, asset, folder, self)

    def _cancel(self) -> None:
        if self._downloader is not None:
            self._downloader.cancel()
            self._downloader = None

    def _download_failed(self, message: str) -> None:
        self._downloader = None
        self._dialog().show_problem(message)

    def _downloaded(self, path: Path) -> None:
        self._downloader = None
        self.downloaded = path
        self._install(path)

    # --- installing ------------------------------------------------------------------

    def _install(self, path: Path) -> None:
        dialog = self._dialog()
        target = self.target
        if target.method is Method.OPEN_FILE:
            self.open_url(QUrl.fromLocalFile(str(path)))
            dialog.close()
            return
        # Unsaved work first: the same questions as quitting. Cancel keeps Curvis open.
        if not self.window.close_all():
            dialog.show_problem(CLOSE_PROJECTS)
            return
        try:
            mac = target.method is Method.REPLACE_APP_BUNDLE
            script = self._write_mac_script() if mac else None
            if target.method is Method.REPLACE_APPIMAGE:
                assert target.location is not None
                replace_appimage(path, target.location)
            command = install_command(target, path, pid=os.getpid(), script=script)
        except (OSError, ValueError) as error:
            dialog.show_problem(NOT_INSTALLED.format(reason=str(error)))
            return
        if command.wait:
            self._run_and_restart(command)
            return
        if not self.start_detached(command):
            dialog.show_problem(NOT_INSTALLED.format(reason="The installer did not start."))
            return
        self.window.quit_for_update()

    def _write_mac_script(self) -> Path:
        folder = self.cache / "updates"
        folder.mkdir(parents=True, exist_ok=True)
        script = folder / "install-update.sh"
        script.write_text(MAC_SCRIPT, encoding="utf-8")
        script.chmod(0o755)
        return script

    def _run_and_restart(self, command: Command) -> None:
        dialog = self._dialog()
        dialog.show_installing()
        process = QProcess(self)
        self._process = process
        process.setProgram(command.program)
        process.setArguments(list(command.arguments))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.finished.connect(lambda code, _status: self._installed(process, code))
        process.errorOccurred.connect(
            lambda error: (
                self._installed(process, -1)
                if error is QProcess.ProcessError.FailedToStart
                else None
            )
        )
        process.start()

    def _installed(self, process: QProcess, code: int) -> None:
        if self._process is not process:
            return
        self._process = None
        output = bytes(process.readAll().data()).decode("utf-8", "replace").strip()
        process.deleteLater()
        if code == 0:
            self.downloaded = None
            self.start_detached(restart_command(self.target))
            self.window.quit_for_update()
            return
        if code in (126, 127):  # pkexec: the password was not given
            reason = "The password was not given."
        elif code == -1:
            reason = "pkexec could not be started."
        else:
            last = output.splitlines()[-1] if output else f"Exit code {code}."
            reason = last
        self._dialog().show_problem(NOT_INSTALLED.format(reason=reason))

    # --- the dialog ------------------------------------------------------------------

    def _dialog(self) -> UpdateDialog:
        if self.dialog is None:
            # Not modal: work goes on while the update downloads.
            dialog = UpdateDialog(self.target, self.window)
            dialog.install_requested.connect(self.install)
            dialog.skip_requested.connect(self.skip)
            dialog.cancel_requested.connect(self._cancel)
            dialog.retry_requested.connect(self.check_now)
            self.dialog = dialog
        return self.dialog
