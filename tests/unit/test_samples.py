"""The sample projects: each builds a fresh document whose analysis runs cleanly and
whose graph shows it, and survives a save and open unchanged."""

from pathlib import Path

import pytest

from curvis import samples
from curvis.analysis.base import Analysis
from curvis.charts.spec import ChartFamily, ErrorBars, Scale
from curvis.core.document import Document
from curvis.io import project
from curvis.ui.launcher import SAMPLE_NAMES, SAMPLE_PICTURES

KEYS = [s.key for s in samples.SAMPLES]


def run(document: Document) -> object:
    analysis: Analysis = document.analyses[0]
    analysis_type = analysis.analysis_type
    table = document.table(analysis.table_id)
    return type(analysis_type).run(analysis_type.gather(table, analysis), analysis.params)


def test_launcher_names_match_the_samples() -> None:
    """The launcher keeps its own copy of the names so start-up does not import
    curvis.samples."""
    assert {s.key: s.name for s in samples.SAMPLES} == SAMPLE_NAMES
    assert set(SAMPLE_PICTURES) == set(KEYS)


@pytest.mark.parametrize("key", KEYS)
def test_sample_is_a_new_untitled_copy(key: str) -> None:
    sample = samples.sample(key)
    first, second = sample.build(), sample.build()
    assert first.name == sample.name
    assert first.undo_stack.is_clean
    assert not first.undo_stack.can_undo
    assert len(first.tables) == len(first.analyses) == len(first.graphs) == 1
    assert first.id != second.id
    assert first.tables[0].id != second.tables[0].id
    first.tables[0].check()


@pytest.mark.parametrize("key", KEYS)
def test_analysis_runs_without_warnings(key: str) -> None:
    result = run(samples.sample(key).build())
    assert result.warnings == ()  # type: ignore[attr-defined]


def test_group_comparison_is_anova_with_dunnett_against_control() -> None:
    document = samples.group_comparison()
    result = run(document)
    assert result.method == "Ordinary one-way ANOVA"  # type: ignore[attr-defined]
    assert "Dunnett's multiple comparisons test" in result.summary  # type: ignore[attr-defined]
    table = document.tables[0]
    assert document.analyses[0].params.control == table.column_by_name("Control").id  # type: ignore[attr-defined, union-attr]
    graph = document.graphs[0]
    assert graph.family is ChartFamily.BAR
    assert {s.error_bars for s in graph.series} == {ErrorBars.SD}


def test_dose_response_is_a_4pl_on_a_log_axis_with_the_curve() -> None:
    document = samples.dose_response()
    result = run(document)
    assert "EC50" in result.summary  # type: ignore[attr-defined]
    graph = document.graphs[0]
    assert graph.family is ChartFamily.XY
    assert graph.x_axis.scale is Scale.LOG
    assert [o.analysis_id for o in graph.overlays] == [document.analyses[0].id]
    assert {s.error_bars for s in graph.series} == {ErrorBars.SEM}


def test_xy_with_replicates_fits_a_line_to_each_series() -> None:
    document = samples.xy_replicates()
    assert len(document.analyses[0].inputs) == 2
    graph = document.graphs[0]
    assert len(graph.series) == 2
    assert [o.analysis_id for o in graph.overlays] == [document.analyses[0].id]


@pytest.mark.parametrize("key", KEYS)
def test_sample_saves_and_opens_unchanged(key: str, tmp_path: Path) -> None:
    document = samples.sample(key).build()
    path = tmp_path / "sample.cvp"
    project.save(document, path)
    loaded = project.load(path)
    assert loaded.analyses == document.analyses
    assert loaded.graphs == document.graphs
    assert [c.name for c in loaded.tables[0].columns] == [
        c.name for c in document.tables[0].columns
    ]


def test_unknown_sample() -> None:
    with pytest.raises(KeyError):
        samples.sample("nothing")
