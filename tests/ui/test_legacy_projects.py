"""Projects saved by every older file format open correctly in this build.

The files in tests/data/projects/legacy were written by the Curvis commits that wrote
each format (scripts/make_legacy_projects.py), not faked from a new file: each has a
manifest of what that build had in memory when it saved. A file must read back with the
same tables, values, groups, constants, calculations, analyses (and their parameters),
graphs and change log; open in the main window with every calculation, analysis and
graph computed without an error; open in the Analyze dialog; and save again in the
current format without losing anything.
"""

from __future__ import annotations

import dataclasses
import json
import math
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pytestqt.qtbot import QtBot

from curvis.analysis.base import Analysis, input_param, param_fields, params_to_json
from curvis.core import Document
from curvis.io.project import PROJECT_JSON, SCHEMA_VERSION, load, save
from curvis.ui.analyze_dialog import AnalyzeDialog
from curvis.ui.document_tab import DocumentTab
from curvis.ui.main_window import MainWindow

LEGACY = Path(__file__).parents[1] / "data" / "projects" / "legacy"
VERSIONS = list(range(1, SCHEMA_VERSION))


def manifest(version: int) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((LEGACY / f"format-{version}.json").read_text("utf-8"))
    return data


def plain(values: Any) -> list[Any]:
    return [None if isinstance(v, float) and math.isnan(v) else v for v in values]


def test_there_is_a_file_for_every_older_format() -> None:
    for version in VERSIONS:
        path = LEGACY / f"format-{version}.cvp"
        assert path.is_file(), f"no project file of format {version}"
        with zipfile.ZipFile(path) as zf:
            assert json.loads(zf.read(PROJECT_JSON))["schema_version"] == version
        assert manifest(version)["schema_version"] == version


def check_same(document: Document, expected: dict[str, Any]) -> None:
    """``document`` holds everything the old build had in memory when it saved."""
    assert document.name == expected["name"]
    assert [t.id for t in document.tables] == [t["id"] for t in expected["tables"]]
    for table, want in zip(document.tables, expected["tables"], strict=True):
        assert table.name == want["name"]
        if want["category_factor"] is not None:
            assert table.category_factor == want["category_factor"]
        assert [c.id for c in table.columns] == [c["id"] for c in want["columns"]]
        for column, col in zip(table.columns, want["columns"], strict=True):
            where = f"{table.name}/{col['name']}"
            assert column.role.value == col["role"], where
            assert column.unit == col["unit"], where
            assert column.group_id == col["group_id"], where
            assert column.category_id == col["category_id"], where
            if col["values"] is not None:  # calculated columns are recomputed
                assert plain(column.values.tolist()) == col["values"], where
        # names: a column in a category may now show a shorter name (step 3.6)
        assert [table.full_name(c) for c in table.columns] == [
            c["name"] for c in want["columns"]
        ] or [c.name for c in table.columns] == [c["name"] for c in want["columns"]]
        assert [(g.id, g.name, g.category_id) for g in table.replicate_groups] == [
            (g["id"], g["name"], g["category_id"]) for g in want["replicate_groups"]
        ]
        assert [(g.id, g.name) for g in table.category_groups] == [
            (g["id"], g["name"]) for g in want["category_groups"]
        ]
    assert [(c.name, c.value, c.unit) for c in document.constants] == [
        (c["name"], c["value"], c["unit"]) for c in expected["constants"]
    ]
    for key, value in expected["settings"].items():
        assert document.settings[key] == value
    assert [(c.table_id, c.formula) for c in document.calculations] == [
        (c["table_id"], c["formula"]) for c in expected["calculations"]
    ]
    for calc, want in zip(document.calculations, expected["calculations"], strict=True):
        assert [[c.column_id, c.source_id] for c in calc.columns] == want["columns"]
    assert [a.id for a in document.analyses] == [a["id"] for a in expected["analyses"]]
    for analysis, want in zip(document.analyses, expected["analyses"], strict=True):
        assert (analysis.type, analysis.table_id) == (want["type"], want["table_id"])
        assert list(analysis.inputs) == want["inputs"], analysis.type
        params = params_to_json(analysis.params)
        # every saved parameter is read back; later formats may add new ones
        changed = {k: (v, params.get(k)) for k, v in want["params"].items() if params.get(k) != v}
        assert not changed, (analysis.type, changed)
    assert [g.id for g in document.graphs] == [g["id"] for g in expected["graphs"]]
    for graph, want in zip(document.graphs, expected["graphs"], strict=True):
        assert graph.table_id == want["table_id"]
        assert graph.family.value == want["family"]
        assert graph.variant.value == want["variant"]
        assert graph.title == want["title"]
        assert [s.source for s in graph.series] == want["series"]
        assert len(graph.overlays) == want["overlays"]
    log = [[e.command_type, e.summary] for e in document.change_log]
    assert log[: len(expected["change_log"])] == expected["change_log"]


@pytest.mark.parametrize("version", VERSIONS)
def test_old_files_read_back_what_was_saved(version: int) -> None:
    check_same(load(LEGACY / f"format-{version}.cvp"), manifest(version))


@pytest.mark.parametrize("version", VERSIONS)
def test_old_files_save_in_the_current_format(version: int, tmp_path: Path) -> None:
    document = load(LEGACY / f"format-{version}.cvp")
    path = tmp_path / "again.cvp"
    save(document, path)
    with zipfile.ZipFile(path) as zf:
        assert json.loads(zf.read(PROJECT_JSON))["schema_version"] == SCHEMA_VERSION
    again = load(path)
    check_same(again, manifest(version))
    assert again.tables == document.tables
    assert again.analyses == document.analyses
    assert again.graphs == document.graphs


@pytest.mark.parametrize("version", VERSIONS)
def test_every_saved_analysis_opens_in_the_analyze_dialog(version: int, qtbot: QtBot) -> None:
    document = load(LEGACY / f"format-{version}.cvp")
    for analysis in document.analyses:
        dialog = AnalyzeDialog(document, analysis.table_id, analysis=analysis)
        qtbot.addWidget(dialog)
        assert dialog.analysis_type is not None and dialog.analysis_type.id == analysis.type
        assert dialog.ok_button.isEnabled(), (analysis.type, dialog.skeleton.feedback.text())
        assert resolved(dialog.analysis()) == resolved(analysis), analysis.type
        dialog.deleteLater()


def resolved(analysis: Analysis) -> Analysis:
    """``analysis`` with each parameter picked from the inputs (such as the control group)
    written as the input it selects: "" means the first input, which the dialog names."""
    params = analysis.params
    for f, info in param_fields(type(params)):
        if info.from_inputs:
            chosen = input_param(params, f.name, analysis.inputs)
            params = dataclasses.replace(params, **{f.name: chosen or ""})
    return dataclasses.replace(analysis, params=params)


def opened(window: MainWindow, qtbot: QtBot, path: Path) -> DocumentTab:
    window.open_path(path)
    qtbot.waitUntil(lambda: window.current_tab is not None, timeout=10_000)
    tab = window.current_tab
    assert tab is not None and tab.engine is not None
    engine = tab.engine
    scheduler = engine.recalculator.scheduler
    qtbot.waitUntil(lambda: not engine.busy and not scheduler.busy, timeout=30_000)
    return tab


@pytest.mark.parametrize("version", VERSIONS)
def test_old_files_open_and_compute_in_the_main_window(
    version: int, window: MainWindow, qtbot: QtBot
) -> None:
    expected = manifest(version)
    tab = opened(window, qtbot, LEGACY / f"format-{version}.cvp")
    document = tab.document
    assert not tab.is_modified
    for calc in document.calculations:
        table = document.table(calc.table_id)
        for output in calc.columns:
            source = table.column(output.source_id).values
            result = table.column(output.column_id).values
            np.testing.assert_array_equal(result, source * 2)  # the formula is {A} * 2
    analyses = tab.analysis_engine
    assert analyses is not None
    assert analyses.errors == {}
    assert set(analyses.results) == {a["id"] for a in expected["analyses"]}
    graphs = tab.graph_engine
    if expected["graphs"]:
        assert graphs is not None
        assert graphs.errors == {}
        assert set(graphs.data) == {g["id"] for g in expected["graphs"]}
