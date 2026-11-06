"""Application entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QFileOpenEvent
from PySide6.QtWidgets import QApplication, QWidget

from curvis import __version__
from curvis.crash import CrashLog
from curvis.settings import Settings
from curvis.settings.store import APPLICATION, ORGANIZATION, ORGANIZATION_DOMAIN
from curvis.ui.main_window import FORMULA_WARM_UP_DELAY_MS, PROJECT_SUFFIX, MainWindow
from curvis.ui.resources import app_icon
from curvis.ui.theme import ThemeManager

DESKTOP_ID = "org.curvis.Curvis"
"""App id: the macOS bundle id and the name of the Linux .desktop file and icon."""

EXIT_AFTER_LAUNCHER = "CURVIS_EXIT_AFTER_LAUNCHER"
"""If this environment variable is set, the app quits as soon as the launcher has been
shown. Used by the start-up benchmark and the installer smoke tests."""


class FileOpenRouter(QObject):
    """Sends files opened from the macOS Finder (``QFileOpenEvent``) to the main window.

    Files that arrive before the window exists are opened once it is set.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._window: MainWindow | None = None
        self._queued: list[Path] = []

    def set_window(self, window: MainWindow) -> None:
        self._window = window
        for path in self._queued:
            window.open_path(path)
        self._queued.clear()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt API)
        if event.type() == QEvent.Type.FileOpen:
            assert isinstance(event, QFileOpenEvent)
            path = Path(event.file())
            if self._window is None:
                self._queued.append(path)
            else:
                self._window.open_path(path)
            return True
        return False


class _ExitWhenShown(QObject):
    """Quits the app after the window's first paint has been processed."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt API)
        if event.type() == QEvent.Type.Paint:
            QTimer.singleShot(0, QApplication.quit)
        return False


def _freeze_startup_objects() -> None:
    from curvis.compute import gc_main_thread  # the compute package is slow to import

    gc_main_thread.freeze_startup_objects()


def project_paths(args: list[str]) -> list[Path]:
    """The project files named on the command line (e.g. by a file association)."""
    return [Path(a) for a in args if not a.startswith("-") and a.lower().endswith(PROJECT_SUFFIX)]


def main(argv: list[str] | None = None) -> int:
    """Start the Curvis application and return its exit code."""
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    router = FileOpenRouter(app)
    app.installEventFilter(router)
    app.setOrganizationName(ORGANIZATION)
    app.setOrganizationDomain(ORGANIZATION_DOMAIN)
    app.setApplicationName(APPLICATION)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName(DESKTOP_ID)  # matches the Linux .desktop file
    app.setWindowIcon(app_icon())

    settings = Settings()
    crash_log = CrashLog(enabled=settings.crash_log)
    crash_log.install()
    themes = ThemeManager(app)
    themes.apply(settings.theme)
    settings.changed.connect(
        lambda key: themes.apply(settings.theme) if key == Settings.THEME else None
    )

    window = MainWindow(settings)
    crash_log.show = lambda report, path: show_crash_report(window, report, path)
    settings.changed.connect(
        lambda key: crash_log.set_enabled(settings.crash_log) if key == Settings.CRASH_LOG else None
    )
    if os.environ.get(EXIT_AFTER_LAUNCHER):
        probe = _ExitWhenShown(window)
        window.launcher.installEventFilter(probe)
    window.show()
    # Start-up ends with the formula warm-up: freeze what is alive by then, so later
    # full garbage collections (on the main thread) stay short.
    window.warm_up_done.connect(_freeze_startup_objects)
    QTimer.singleShot(FORMULA_WARM_UP_DELAY_MS, window.warm_up_formulas)
    router.set_window(window)
    for path in project_paths(argv[1:]):
        window.open_path(path)
    leftover = crash_log.take_leftover_fault()
    if leftover is not None:  # the last session crashed hard
        QTimer.singleShot(0, lambda: show_crash_report(window, *leftover))
    try:
        return app.exec()
    finally:
        crash_log.close()


def show_crash_report(parent: QWidget, report: str, path: Path) -> None:
    """Show a crash report (the crash log is on)."""
    from curvis.ui.crash_dialog import CrashDialog

    dialog = CrashDialog(report, path, parent)
    dialog.exec()
    dialog.deleteLater()
