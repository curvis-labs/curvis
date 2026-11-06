"""Opt-in local crash log. Nothing is ever sent anywhere.

When the user turns on *Keep a crash log on this computer* (Settings), an uncaught
exception is written to a report in the app's data folder and shown in a dialog with
*Copy to clipboard*, so the user can send it themselves. A hard crash (a segfault, which
Python cannot catch) is caught by :mod:`faulthandler` into ``fault.log``; it is shown at
the next start and then removed.

A report holds the Curvis, Python, Qt and OS versions and the traceback, with the home
folder written as ``~``. It holds no project names, data or settings. When the log is
off, uncaught exceptions are printed to stderr only, as before.
"""

from __future__ import annotations

import faulthandler
import os
import platform
import sys
import threading
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import IO

from curvis import __version__

MAX_REPORTS = 10
"""How many crash reports are kept; older ones are removed."""
REPORT_PREFIX = "curvis-crash-"
FAULT_LOG = "fault.log"

ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]


def default_directory() -> Path:
    """``<app data>/crash``, e.g. ``~/.local/share/Curvis/Curvis/crash`` on Linux."""
    from PySide6.QtCore import QStandardPaths

    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    return Path(base) / "crash"


def _versions() -> list[str]:
    try:
        import PySide6
        from PySide6.QtCore import qVersion

        qt = f"Qt {qVersion()} (PySide6 {PySide6.__version__})"
    except Exception:  # pragma: no cover (Qt is always there in the app)
        qt = "Qt unknown"
    return [
        f"Curvis {__version__}",
        f"Python {platform.python_version()} ({platform.python_implementation()})",
        qt,
        f"{platform.system()} {platform.release()} ({platform.machine()})",
    ]


def scrub(text: str, home: Path | None = None) -> str:
    """``text`` with the home folder written as ``~`` (it often holds the user's name)."""
    home_text = str(home if home is not None else Path.home())
    if home_text in ("", os.sep):
        return text
    return text.replace(home_text, "~")


def format_report(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: TracebackType | None,
    *,
    when: datetime | None = None,
    home: Path | None = None,
) -> str:
    """The text of a crash report: versions, time and the traceback."""
    when = when or datetime.now(UTC)
    lines = [*_versions(), f"Time {when.strftime('%Y-%m-%d %H:%M:%S %Z')}", ""]
    lines += traceback.format_exception(exc_type, exc, tb)
    return scrub("\n".join(line.rstrip("\n") for line in lines) + "\n", home)


class CrashLog:
    """Catches uncaught exceptions; while ``enabled``, saves them and calls ``show``.

    ``show`` gets the report and the file it was saved to; the app shows the crash
    dialog. Only one report is shown at a time, so an error that repeats (in a paint
    event, say) does not stack up dialogs; later ones are still saved.
    """

    def __init__(
        self,
        directory: Path | None = None,
        *,
        enabled: bool = False,
        show: Callable[[str, Path], None] | None = None,
        home: Path | None = None,
    ) -> None:
        self.directory = directory if directory is not None else default_directory()
        self.show = show
        self._home = home
        self._enabled = False
        self._showing = False
        self._fault_file: IO[str] | None = None
        self._previous_hooks: tuple[object, object] | None = None
        self.leftover_fault: str | None = None
        """A hard crash from the last run, read before the fault log is reopened."""
        self.set_enabled(enabled)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def install(self) -> None:
        """Replace ``sys.excepthook`` and ``threading.excepthook``."""
        if self._previous_hooks is None:
            self._previous_hooks = (sys.excepthook, threading.excepthook)
        sys.excepthook = self.handle
        threading.excepthook = self._handle_thread

    def uninstall(self) -> None:
        if self._previous_hooks is not None:
            sys.excepthook, threading.excepthook = self._previous_hooks  # type: ignore[assignment]
            self._previous_hooks = None
        self._close_fault_log()

    def set_enabled(self, enabled: bool) -> None:
        """Turn the log on or off. On: hard crashes go to ``fault.log`` from now on."""
        if enabled == self._enabled and (not enabled or self._fault_file is not None):
            return
        self._enabled = enabled
        if enabled:
            self._open_fault_log()
        else:
            self._close_fault_log()

    # --- handling ----------------------------------------------------------------------

    def handle(
        self, exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
    ) -> None:
        """The excepthook: print to stderr as usual, then save and show if enabled."""
        sys.__excepthook__(exc_type, exc, tb)
        if not self._enabled or issubclass(exc_type, KeyboardInterrupt):
            return
        try:
            report = format_report(exc_type, exc, tb, home=self._home)
            path = self.save(report)
        except OSError as error:  # the log must never make things worse
            print(f"Curvis could not save the crash log: {error}", file=sys.stderr)
            return
        self._show(report, path)

    def _handle_thread(self, args: threading.ExceptHookArgs) -> None:
        if args.exc_value is None:  # pragma: no cover (Python always sets it)
            return
        self.handle(args.exc_type, args.exc_value, args.exc_traceback)

    def _show(self, report: str, path: Path) -> None:
        if self.show is None or self._showing:
            return
        self._showing = True
        try:
            self.show(report, path)
        except Exception as error:  # pragma: no cover (defensive)
            print(f"Curvis could not show the crash log: {error}", file=sys.stderr)
        finally:
            self._showing = False

    def save(self, report: str, when: datetime | None = None) -> Path:
        """Write ``report`` to a new file and keep only the newest :data:`MAX_REPORTS`."""
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = (when or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S-%f")
        # Two reports can get the same time (the clock is coarse on Windows): number
        # them, and never overwrite one. The names sort oldest first.
        number = 1
        while True:
            path = self.directory / f"{REPORT_PREFIX}{stamp}-{number:03d}.log"
            try:
                with path.open("x", encoding="utf-8") as file:
                    file.write(report)
                break
            except FileExistsError:
                number += 1
        for old in self.reports()[:-MAX_REPORTS]:
            old.unlink(missing_ok=True)
        return path

    def reports(self) -> list[Path]:
        """The saved reports, oldest first."""
        if not self.directory.is_dir():
            return []
        return sorted(self.directory.glob(f"{REPORT_PREFIX}*.log"))

    # --- hard crashes ------------------------------------------------------------------

    @property
    def fault_path(self) -> Path:
        return self.directory / FAULT_LOG

    def _open_fault_log(self) -> None:
        path = self.fault_path
        try:
            if path.is_file() and self.leftover_fault is None:
                text = path.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    lines = [*_versions(), "", "A previous session stopped unexpectedly.", ""]
                    self.leftover_fault = scrub("\n".join(lines) + text + "\n", self._home)
            self.directory.mkdir(parents=True, exist_ok=True)
            self._fault_file = path.open("w", encoding="utf-8")
            faulthandler.enable(self._fault_file, all_threads=True)
        except OSError as error:
            print(f"Curvis could not open the crash log: {error}", file=sys.stderr)
            self._fault_file = None

    def _close_fault_log(self) -> None:
        if self._fault_file is None:
            return
        faulthandler.disable()
        self._fault_file.close()
        self._fault_file = None
        self.fault_path.unlink(missing_ok=True)

    def take_leftover_fault(self) -> tuple[str, Path] | None:
        """The report of a hard crash in the last session (once), saved as a report."""
        report, self.leftover_fault = self.leftover_fault, None
        if report is None:
            return None
        try:
            return report, self.save(report)
        except OSError:
            return report, self.fault_path

    def close(self) -> None:
        """At a normal exit: stop the fault handler and remove the (empty) fault log."""
        self._close_fault_log()
