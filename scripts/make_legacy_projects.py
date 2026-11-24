"""Save a sample project with every older project file format, using the Curvis that wrote it.

    uv run python scripts/make_legacy_projects.py

For each format from 1 to SCHEMA_VERSION - 1, this checks out the last commit that wrote
that format (in a temporary git worktree), runs ``scripts/legacy_project_builder.py``
against its ``src`` folder, and writes

    tests/data/projects/legacy/format-<n>.cvp    the project, as that build saved it
    tests/data/projects/legacy/format-<n>.json   what that build had in memory

``tests/unit/test_legacy_projects.py`` opens each file with the current build and
compares it with the manifest. The files are committed: run this again only to add the
format that a new ``SCHEMA_VERSION`` leaves behind (add its commit to ``FORMAT_COMMITS``).
The old commits run with this checkout's virtual environment, so they need no install.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "data" / "projects" / "legacy"
BUILDER = ROOT / "scripts" / "legacy_project_builder.py"

FORMAT_COMMITS = {
    # format: the last commit that wrote it (the parent of the commit that bumped it)
    1: "2b11f33",
    2: "a8fc6ee",
    3: "88c80cf",
    4: "00169d1",
    5: "0d2793d",
    6: "48448d3",
    7: "d0f81cf",
    8: "e561466",
    9: "dc2e5e2",
    10: "6384734",
}

RUN = """
import runpy, sys
sys.path.insert(0, sys.argv[1])
sys.argv = [sys.argv[2], sys.argv[3], sys.argv[4]]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


def make(version: int, commit: str, scratch: Path) -> None:
    tree = scratch / f"format-{version}"
    git("worktree", "add", "--detach", str(tree), commit)
    try:
        written = git("show", f"{commit}:src/curvis/io/project.py")
        if f"SCHEMA_VERSION = {version}\n" not in written:
            raise SystemExit(f"commit {commit} does not write format {version}")
        OUT.mkdir(parents=True, exist_ok=True)
        cvp, manifest = OUT / f"format-{version}.cvp", OUT / f"format-{version}.json"
        # -I: the old sources come only from the worktree, not from this checkout
        subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                RUN,
                str(tree / "src"),
                str(BUILDER),
                str(cvp),
                str(manifest),
            ],
            cwd=scratch,
            check=True,
        )
        print(f"format {version}: {commit} -> {cvp.relative_to(ROOT)}")
    finally:
        git("worktree", "remove", "--force", str(tree))


def main() -> None:
    with tempfile.TemporaryDirectory() as scratch:
        for version, commit in FORMAT_COMMITS.items():
            make(version, commit, Path(scratch))


if __name__ == "__main__":
    main()
