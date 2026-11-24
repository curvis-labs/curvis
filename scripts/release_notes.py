"""Print the GitHub release notes for a tag: the version's section of CHANGELOG.md and
the list of downloads.

    python3 scripts/release_notes.py v1.0.0 > notes.md

Fails if the tag does not match curvis.__version__ (v1.0.0 for 1.0.0, v1.0.0-rc1 for
1.0.0rc1), if CHANGELOG.md has no section for the version, or if a final release's
section is still marked "unreleased". Standard library only, so release.yml can run it
without installing Curvis.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def package_version() -> str:
    text = (ROOT / "src" / "curvis" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__ = "([^"]+)"', text)
    if not match:
        raise SystemExit("no __version__ in src/curvis/__init__.py")
    return match.group(1)


def tag_for(version: str) -> str:
    """The tag of a PEP 440 version: 1.0.0 -> v1.0.0, 1.0.0rc1 -> v1.0.0-rc1,
    0.1.0b1 -> v0.1.0-beta (the beta's tag)."""
    match = re.fullmatch(r"(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?", version)
    if not match:
        raise SystemExit(f"unsupported version {version!r}")
    base, pre, number = match.groups()
    if pre is None:
        return f"v{base}"
    if pre == "b" and number == "1":
        return f"v{base}-beta"
    names = {"a": "alpha", "b": "beta", "rc": "rc"}
    return f"v{base}-{names[pre]}{number}"


def changelog_section(version: str, changelog: str) -> tuple[str, str]:
    """The heading and body of the version's section (up to the next ``## ``)."""
    base = re.match(r"\d+\.\d+\.\d+", version)
    assert base is not None
    pattern = rf"^## ({re.escape(base.group(0))}\b[^\n]*)\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, changelog, flags=re.M | re.S)
    if not match:
        raise SystemExit(f"CHANGELOG.md has no section for {base.group(0)}")
    return match.group(1).strip(), match.group(2).strip()


def notes(tag: str, repository: str = "curvis-labs/curvis") -> str:
    version = package_version()
    if tag_for(version) != tag:
        raise SystemExit(f"tag {tag} does not match version {version} ({tag_for(version)})")
    heading, body = changelog_section(version, (ROOT / "CHANGELOG.md").read_text("utf-8"))
    if "-" not in tag and "unreleased" in heading.lower():
        raise SystemExit(f"CHANGELOG.md still says {heading!r}; give it the release date")
    return render(version, tag, body, repository)


def render(version: str, tag: str, body: str, repository: str) -> str:
    """The notes: the changelog section, then downloads, how to open and how to check."""
    base = f"https://github.com/{repository}/blob/{tag}"
    return f"""{body}

## Download

- **Windows**: `Curvis-{version}-setup.exe`
- **macOS** (Apple silicon): `Curvis-{version}-macos-arm64.dmg`
- **Linux**: `curvis_{version}_amd64.deb` (Debian, Ubuntu) or `Curvis-{version}-x86_64.AppImage`

## How to open

The installers are not signed, so Windows and macOS warn before the first start.

- **macOS**: open the app once. When it's blocked, go to **System Settings → Privacy &
  Security** and click **Open Anyway**.
- **Windows**: when SmartScreen appears, click **More info**, then **Run anyway**.

## Check your download

`SHA256SUMS.txt` lists the SHA-256 checksum of every file of this release. Compare it
with the checksum of the file you downloaded:

- **Windows** (PowerShell): `Get-FileHash Curvis-{version}-setup.exe`
- **macOS**: `shasum -a 256 Curvis-{version}-macos-arm64.dmg`
- **Linux**: `sha256sum --check --ignore-missing SHA256SUMS.txt`

Each file also has a build provenance attestation, which records the commit and the
workflow run that built it. With the [GitHub CLI](https://cli.github.com):
`gh attestation verify <file> --repo {repository}`.

[Getting started]({base}/docs/getting-started.md) explains how to install, and how to go
from a CSV file to an exported graph; the [user guide]({base}/docs/user-guide) covers
every feature. The validation report is attached as `curvis-validation-report.pdf`.
"""


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    import os

    print(notes(sys.argv[1], os.environ.get("GITHUB_REPOSITORY", "curvis-labs/curvis")))
