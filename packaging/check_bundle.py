"""Check that the PyInstaller build bundles the modules Curvis imports by name.

The built-in analyses are imported by name (curvis.analysis.base.BUILTIN_MODULES), and
heavy libraries are imported inside functions so start-up stays fast. A module that
PyInstaller misses only fails when it is used (e.g. every curve fit), so the installers'
smoke tests, which only start the app, would not notice. Run after the build:

    uv run --no-sync python packaging/check_bundle.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOC = ROOT / "build" / "curvis" / "PYZ-00.toc"

LIBRARIES = (
    "lmfit",
    "matplotlib",
    "matplotlib.backends.backend_agg",  # PNG export
    "matplotlib.backends.backend_pdf",  # PDF export (loaded by name in savefig)
    "matplotlib.backends.backend_svg",  # SVG export (loaded by name in savefig)
    "scipy.optimize",
    "polars",
)
"""Imported inside functions (CLAUDE.md rule 9) or by name; the app needs them all."""


def builtin_analysis_modules() -> list[str]:
    """``BUILTIN_MODULES`` from curvis/analysis/base.py, read without importing Curvis."""
    tree = ast.parse((ROOT / "src" / "curvis" / "analysis" / "base.py").read_text("utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "BUILTIN_MODULES" for t in node.targets
        ):
            return [str(v) for v in ast.literal_eval(node.value)]
    raise SystemExit("BUILTIN_MODULES not found in curvis/analysis/base.py")


def bundled_modules(toc: Path) -> set[str]:
    entries = ast.literal_eval(toc.read_text(encoding="utf-8"))
    # The PYZ table of contents: ('PYZ-00.pyz', [(name, path, kind), ...]) in recent
    # PyInstaller versions; older ones list the entries directly.
    items = entries[1] if isinstance(entries, tuple) and len(entries) == 2 else entries
    return {item[0] for item in items if isinstance(item, tuple) and item}


def main() -> int:
    if not TOC.is_file():
        print(f"{TOC} not found: build with PyInstaller first.", file=sys.stderr)
        return 2
    bundled = bundled_modules(TOC)
    wanted = [*builtin_analysis_modules(), "curvis.samples", "curvis.crash", *LIBRARIES]
    missing = [m for m in wanted if m not in bundled]
    for module in wanted:
        print(f"{'missing' if module in missing else 'ok':8} {module}")
    if missing:
        print(f"\n{len(missing)} module(s) missing from the bundle.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
