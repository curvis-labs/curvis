"""Smoke tests for the package layout."""

import importlib

import pytest

import curvis

SUBPACKAGES = [
    "core",
    "data",
    "formula",
    "analysis",
    "analysis.compare",
    "analysis.fit",
    "analysis.survival",
    "compute",
    "charts",
    "charts.render_qt",
    "charts.render_mpl",
    "io",
    "ui",
    "settings",
]


def test_version_is_set() -> None:
    assert curvis.__version__


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports_and_has_docstring(name: str) -> None:
    module = importlib.import_module(f"curvis.{name}")
    assert module.__doc__
