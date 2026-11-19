"""Benchmarks for Kaplan-Meier. CLAUDE.md has no survival target; the analysis is held
to the group-comparison target (10,000 points in < 20 ms) and the graph's plot data to
the redraw target (< 50 ms), and both are gated against slowdowns.

Times are rounded to whole days, so there are many ties, as in real follow-up data.
"""

from typing import Any

import numpy as np
import pytest

from curvis.analysis import base
from curvis.analysis.base import AnalysisInputs, SeriesData
from curvis.charts.builder import default_spec
from curvis.charts.data import compute, gather
from curvis.charts.spec import ChartFamily
from curvis.data import Column, ColumnRole, Table

ANALYSIS_TARGET_S = 0.020
REDRAW_TARGET_S = 0.050
N_SUBJECTS = 10_000


def subjects(n: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    time = np.round(rng.weibull(1.3, n) * 400)
    event = (rng.random(n) < 0.7).astype(np.float64)
    return time, event


@pytest.fixture(autouse=True)
def warm() -> None:
    """Import lifelines and build a result once, so the timings leave out start-up."""
    kind = base.get("kaplan_meier")
    kind.run(
        AnalysisInputs("T", 3, (SeriesData("e", "A", (np.array([1.0, 2, 3]), np.ones(3))),)),
        kind.default_params(),
    )


def test_kaplan_meier_10k_subjects(benchmark: Any) -> None:
    kind = base.get("kaplan_meier")
    time, event = subjects(N_SUBJECTS, 0)
    data = AnalysisInputs("T", N_SUBJECTS, (SeriesData("e", "A", (time, event)),))
    result = benchmark(kind.run, data, kind.default_params())
    assert result.method == "Kaplan-Meier survival estimate"
    assert benchmark.stats.stats.mean < ANALYSIS_TARGET_S


def test_survival_graph_data_4_curves_of_500(benchmark: Any) -> None:
    columns = [Column.new("Days", ColumnRole.TIME, subjects(2000, 1)[0])]
    rng = np.random.default_rng(2)
    group = rng.integers(0, 4, 2000)
    event = subjects(2000, 3)[1]
    for g in range(4):
        values = np.where(group == g, event, np.nan)
        columns.append(Column.new(f"Group {g}", ColumnRole.EVENT, values))
    table = Table("T", columns)
    spec = default_spec(table, ChartFamily.SURVIVAL)
    inputs = gather(table, spec, {})
    data = benchmark(compute, inputs)
    assert len(data.survival) == 4
    assert benchmark.stats.stats.mean < REDRAW_TARGET_S
