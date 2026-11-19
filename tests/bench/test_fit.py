"""Benchmarks for the curve fits. Target: 12 points × 20 series in < 200 ms for the 4PL
(CLAUDE.md) and for every other nonlinear curve, run as the app runs it
(``AnalysisType.run``: the fits, the Result table and the fitted curves with their
confidence bands for graphs)."""

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


def curve_series(type_id: str) -> AnalysisInputs:
    """20 series of 12 points of the curve of ``type_id``, parameters varied per series,
    with realistic noise (3% of the span for the 7-parameter models, 5% otherwise)."""
    from curvis.analysis.fit.analyses import NonlinearCurve

    kind = base.get(type_id)
    assert isinstance(kind, NonlinearCurve)
    model = kind.spec().model
    rng = np.random.default_rng(2)
    designs: dict[str, tuple[Any, Any, float]] = {
        "biphasic": (np.linspace(-10, -3, 12), [0, 100, 0.4, -8, -5, 1, 1], 3),
        "bell_shaped": (np.linspace(-10, -3, 12), [0, 100, 10, -8, -5, 1, 1], 3),
        "exp_association": (np.linspace(0, 30, 12), [5, 80, 0.2], 4),
        "exp_decay": (np.linspace(0, 30, 12), [100, 10, 0.15], 4.5),
        "michaelis_menten": (2.0 ** np.arange(-2, 10), [120, 6], 6),
        "gaussian": (np.linspace(0, 20, 12), [5, 50, 10, 2.5], 2.5),
    }
    x, truth, sd = designs[type_id]
    series = []
    for i in range(N_SERIES):
        p = np.asarray(truth, dtype=np.float64) * rng.uniform(0.95, 1.05, len(truth))
        y = model.f(x, p) + rng.normal(0, sd, len(x))
        series.append(SeriesData(f"s{i}", f"Series {i}", (y,)))
    shown_x = 10.0**x if kind.spec().log_x else x
    return AnalysisInputs("T", len(x), tuple(series), shown_x, "X")


def fit_curves(benchmark: Any, type_id: str) -> None:
    kind = base.get(type_id)
    result = benchmark(kind.run, curve_series(type_id), kind.default_params())
    fit_row = next(r for r in result.tables[0].rows if r[0] == "Fit")
    assert fit_row[1:].count("OK") >= N_SERIES - 1
    assert benchmark.stats.stats.mean < TARGET_S


def test_biphasic_12_points_by_20_series(benchmark: Any) -> None:
    fit_curves(benchmark, "biphasic")


def test_bell_shaped_12_points_by_20_series(benchmark: Any) -> None:
    fit_curves(benchmark, "bell_shaped")


def test_exp_association_12_points_by_20_series(benchmark: Any) -> None:
    fit_curves(benchmark, "exp_association")


def test_exp_decay_12_points_by_20_series(benchmark: Any) -> None:
    fit_curves(benchmark, "exp_decay")


def test_michaelis_menten_12_points_by_20_series(benchmark: Any) -> None:
    fit_curves(benchmark, "michaelis_menten")


def test_gaussian_12_points_by_20_series(benchmark: Any) -> None:
    fit_curves(benchmark, "gaussian")
