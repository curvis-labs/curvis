"""The release notes script (scripts/release_notes.py) that release.yml runs."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

import curvis

ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def notes() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "release_notes", ROOT / "scripts" / "release_notes.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_notes"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "version, tag",
    [
        ("1.0.0", "v1.0.0"),
        ("1.0.0rc1", "v1.0.0-rc1"),
        ("1.1.0a2", "v1.1.0-alpha2"),
        ("0.1.0b1", "v0.1.0-beta"),
        ("0.2.0b2", "v0.2.0-beta2"),
    ],
)
def test_tag_for_version(notes: ModuleType, version: str, tag: str) -> None:
    assert notes.tag_for(version) == tag


def test_package_version_matches_curvis(notes: ModuleType) -> None:
    assert notes.package_version() == curvis.__version__


def test_the_current_version_has_a_changelog_section(notes: ModuleType) -> None:
    heading, body = notes.changelog_section(
        curvis.__version__, (ROOT / "CHANGELOG.md").read_text("utf-8")
    )
    assert curvis.__version__.startswith(heading.split()[0])
    assert body


CHANGELOG = """# Changelog

## 1.0.0 — unreleased

New things.

## 0.1.0-beta

Old things.
"""


def test_section_stops_at_the_next_version(notes: ModuleType) -> None:
    assert notes.changelog_section("1.0.0", CHANGELOG) == ("1.0.0 — unreleased", "New things.")
    assert notes.changelog_section("0.1.0b1", CHANGELOG) == ("0.1.0-beta", "Old things.")
    with pytest.raises(SystemExit):
        notes.changelog_section("2.0.0", CHANGELOG)


def test_a_wrong_tag_is_refused(notes: ModuleType) -> None:
    with pytest.raises(SystemExit, match="does not match"):
        notes.notes("v9.9.9")
