"""Start-up: heavy libraries stay unloaded until they are needed."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Imported lazily by design (CLAUDE.md rule 9, plus other large libraries).
HEAVY = [
    "pyarrow",
    "scipy",
    "polars",
    "statsmodels",
    "lifelines",
    "matplotlib",
    "lmfit",
    "pyqtgraph",
    "curvis.io.project",
    "curvis.compute.scheduler",
]

PROBE = """
import sys
from curvis.app import main
code = main(["curvis"])
heavy = {heavy!r}
print("LOADED:" + ",".join(m for m in heavy if m in sys.modules))
sys.exit(code)
"""


def run_to_launcher(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(
        CURVIS_EXIT_AFTER_LAUNCHER="1",
        QT_QPA_PLATFORM="offscreen",
        # Keep the real user's settings out of it.
        HOME=str(tmp_path),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        APPDATA=str(tmp_path / "appdata"),
    )
    return subprocess.run(
        [sys.executable, *args], env=env, capture_output=True, text=True, timeout=60
    )


def test_heavy_libraries_are_not_loaded_at_launcher(tmp_path: Path) -> None:
    result = run_to_launcher(tmp_path, "-c", PROBE.format(heavy=HEAVY))
    assert result.returncode == 0, result.stderr
    line = next(ln for ln in result.stdout.splitlines() if ln.startswith("LOADED:"))
    assert line == "LOADED:", f"loaded at start-up: {line.removeprefix('LOADED:')}"


@pytest.mark.skipif(sys.platform == "win32" and "CI" not in os.environ, reason="slow locally")
def test_python_m_curvis_reaches_launcher_and_exits(tmp_path: Path) -> None:
    result = run_to_launcher(tmp_path, "-m", "curvis")
    assert result.returncode == 0, result.stderr
