"""The opt-in local crash log (curvis.crash)."""

import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from curvis import __version__
from curvis.crash import FAULT_LOG, MAX_REPORTS, CrashLog, format_report, scrub


def raised(message: str = "boom") -> tuple[type[BaseException], BaseException, object]:
    try:
        raise ValueError(message)
    except ValueError:
        exc_type, exc, tb = sys.exc_info()
    assert exc_type is not None and exc is not None
    return exc_type, exc, tb


def test_report_has_versions_and_the_traceback_but_not_the_home_folder(tmp_path: Path) -> None:
    home = tmp_path / "home" / "alice"
    exc_type, exc, tb = raised(f"cannot read {home}/Experiments/secret.cvp")
    report = format_report(exc_type, exc, tb, home=home)  # type: ignore[arg-type]
    assert report.startswith(f"Curvis {__version__}\nPython ")
    assert "Qt " in report
    assert "Traceback (most recent call last):" in report
    assert "ValueError: cannot read ~/Experiments/secret.cvp" in report
    assert str(home) not in report
    assert "alice" not in report


def test_scrub_leaves_text_alone_without_a_home() -> None:
    assert scrub("/x/y", Path("/")) == "/x/y"


def test_off_by_default_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shown: list[str] = []
    log = CrashLog(tmp_path / "crash", show=lambda report, path: shown.append(report))
    log.handle(*raised())  # type: ignore[arg-type]
    assert not (tmp_path / "crash").exists()
    assert shown == []
    assert "ValueError: boom" in capsys.readouterr().err  # still printed, as before


def test_on_saves_and_shows_the_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shown: list[tuple[str, Path]] = []
    log = CrashLog(tmp_path / "crash", enabled=True, show=lambda r, p: shown.append((r, p)))
    try:
        log.handle(*raised())  # type: ignore[arg-type]
        ((report, path),) = shown
        assert path.read_text(encoding="utf-8") == report
        assert "ValueError: boom" in report
        assert log.reports() == [path]
        assert "ValueError: boom" in capsys.readouterr().err
    finally:
        log.close()


def test_keyboard_interrupt_is_not_logged(tmp_path: Path) -> None:
    log = CrashLog(tmp_path, enabled=True, show=lambda r, p: pytest.fail("shown"))
    try:
        try:
            raise KeyboardInterrupt
        except KeyboardInterrupt:
            log.handle(*sys.exc_info())  # type: ignore[arg-type]
        assert log.reports() == []
    finally:
        log.close()


def test_only_the_newest_reports_are_kept(tmp_path: Path) -> None:
    log = CrashLog(tmp_path)
    start = datetime(2026, 11, 5, tzinfo=UTC)
    paths = [log.save(f"report {i}", start + timedelta(seconds=i)) for i in range(MAX_REPORTS + 3)]
    assert log.reports() == paths[3:]


def test_one_dialog_at_a_time(tmp_path: Path) -> None:
    """An error raised while the dialog is open (e.g. in a paint event) is saved but
    does not open a second dialog."""
    calls: list[str] = []

    def show(report: str, path: Path) -> None:
        calls.append(report)
        log.handle(*raised("again"))  # type: ignore[arg-type]

    log = CrashLog(tmp_path, enabled=True, show=show)
    try:
        log.handle(*raised())  # type: ignore[arg-type]
        assert len(calls) == 1
        assert len(log.reports()) == 2
    finally:
        log.close()


def test_install_replaces_and_restores_the_hooks(tmp_path: Path) -> None:
    before = (sys.excepthook, threading.excepthook)
    log = CrashLog(tmp_path, enabled=True)
    log.install()
    try:
        assert sys.excepthook == log.handle
        thread = threading.Thread(target=lambda: 1 / 0)
        thread.start()
        thread.join()
        (path,) = log.reports()
        assert "ZeroDivisionError" in path.read_text(encoding="utf-8")
    finally:
        log.uninstall()
    assert (sys.excepthook, threading.excepthook) == before


def test_turning_it_on_and_off(tmp_path: Path) -> None:
    log = CrashLog(tmp_path)
    assert not log.enabled and not (tmp_path / FAULT_LOG).exists()
    log.set_enabled(True)
    assert log.enabled and (tmp_path / FAULT_LOG).exists()
    log.set_enabled(False)
    assert not (tmp_path / FAULT_LOG).exists()


def test_a_hard_crash_is_shown_at_the_next_start_once(tmp_path: Path) -> None:
    home = tmp_path / "home" / "bob"
    (tmp_path / FAULT_LOG).write_text(
        f'Fatal Python error: Segmentation fault\n  File "{home}/x.py", line 3\n'
    )
    log = CrashLog(tmp_path, enabled=True, home=home)
    try:
        taken = log.take_leftover_fault()
        assert taken is not None
        report, path = taken
        assert "Segmentation fault" in report
        assert "~/x.py" in report and "bob" not in report
        assert path.read_text(encoding="utf-8") == report
        assert log.take_leftover_fault() is None
        assert (tmp_path / FAULT_LOG).read_text() == ""  # reopened for this session
    finally:
        log.close()
    assert not (tmp_path / FAULT_LOG).exists()  # a normal exit leaves no fault log


def test_an_empty_fault_log_is_not_a_crash(tmp_path: Path) -> None:
    (tmp_path / FAULT_LOG).write_text("")
    log = CrashLog(tmp_path, enabled=True)
    try:
        assert log.take_leftover_fault() is None
    finally:
        log.close()


@pytest.mark.skipif(sys.platform == "win32", reason="abort() opens an error box on Windows")
def test_a_real_hard_crash_is_caught(tmp_path: Path) -> None:
    import subprocess

    code = (
        "import os, sys; from pathlib import Path; from curvis.crash import CrashLog; "
        "CrashLog(Path(sys.argv[1]), enabled=True); os.abort()"
    )
    done = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True)
    assert done.returncode != 0
    log = CrashLog(tmp_path, enabled=True)
    try:
        taken = log.take_leftover_fault()
        assert taken is not None
        assert "Fatal Python error: Aborted" in taken[0]
    finally:
        log.close()


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="turns the log on through XDG_CONFIG_HOME, which only Linux uses for app "
    "settings; on macOS and Windows that would mean writing the user's real preferences",
)
def test_the_app_starts_and_quits_cleanly_with_the_log_on(tmp_path: Path) -> None:
    import os
    import subprocess

    config, data = tmp_path / "config", tmp_path / "data"
    (config / "Curvis").mkdir(parents=True)
    (config / "Curvis" / "Curvis.conf").write_text("[privacy]\ncrash_log=true\n")
    env = dict(os.environ)
    env.update(
        CURVIS_EXIT_AFTER_LAUNCHER="1",
        QT_QPA_PLATFORM=env.get("QT_QPA_PLATFORM", "offscreen"),
        HOME=str(tmp_path),
        XDG_CONFIG_HOME=str(config),
        XDG_DATA_HOME=str(data),
        APPDATA=str(tmp_path / "appdata"),
    )
    done = subprocess.run(
        [sys.executable, "-m", "curvis"], env=env, capture_output=True, timeout=60
    )
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    folders = [p for p in tmp_path.rglob("crash") if p.is_dir()]
    assert folders, "the crash log was not turned on"
    assert not list(tmp_path.rglob(FAULT_LOG))  # removed at a normal exit
