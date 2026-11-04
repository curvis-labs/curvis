"""Benchmarks for the curve fits. Target: a 4PL fit of 12 points × 20 series in < 200 ms
(CLAUDE.md), run as the app runs it (``AnalysisType.run``: the fits, the Result table
and the fitted curves with their confidence bands for graphs)."""

from typing import Any

import numpy as np
import pytest

from curvis.analysis import base
from curvis.analysis.base import AnalysisInputs, SeriesData
from curvis.analysis.fit.dose_response import LOGISTIC

TARGET_S = 0.200
N_SERIES = 20
CONC = 10.0 ** np.linspace(-9, -4, 12)


def dose_series(three: bool = False) -> AnalysisInputs:
    """20 series of 12 noisy points, with EC50s and slopes that differ per series."""
    rng = np.random.default_rng(0)
    series = []
    for i in range(N_SERIES):
        p = np.array(
            [
                0.0 if three else 5.0,
                95.0,
                -6.5 + rng.normal(0, 0.4),
                rng.choice([-1, 1]) * rng.uniform(0.7, 1.6),
            ]
        )
        y = LOGISTIC.f(np.log10(CONC), p) + rng.normal(0, 3, len(CONC))
        series.append(SeriesData(f"s{i}", f"Series {i}", (y,)))
    return AnalysisInputs("T", len(CONC), tuple(series), CONC, "Concentration")


@pytest.fixture(autouse=True)
def warm() -> None:
    """Import lmfit and SciPy and fit once, so the timings leave out start-up."""
    kind = base.get("hill_4pl")
    kind.run(dose_series(), kind.default_params())


def fit_20_series(benchmark: Any, type_id: str) -> None:
    kind = base.get(type_id)
    data = dose_series(three=type_id == "hill_3pl")
    result = benchmark(kind.run, data, kind.default_params())
    fit_row = next(r for r in result.tables[0].rows if r[0] == "Fit")
    assert fit_row[1:] == ("OK",) * N_SERIES
    assert len(result.curves) == N_SERIES
    assert benchmark.stats.stats.mean < TARGET_S


def test_4pl_12_points_by_20_series(benchmark: Any) -> None:
    fit_20_series(benchmark, "hill_4pl")


def test_3pl_12_points_by_20_series(benchmark: Any) -> None:
    fit_20_series(benchmark, "hill_3pl")


def test_linear_regression_12_points_by_20_series(benchmark: Any) -> None:
    rng = np.random.default_rng(1)
    x = np.arange(12.0)
    series = tuple(
        SeriesData(f"s{i}", f"S{i}", (2 + 0.5 * x + rng.normal(0, 1, 12),)) for i in range(N_SERIES)
    )
    kind = base.get("linear_regression")
    result = benchmark(kind.run, AnalysisInputs("T", 12, series, x, "X"), kind.default_params())
    assert len(result.curves) == N_SERIES
    assert benchmark.stats.stats.mean < TARGET_S
