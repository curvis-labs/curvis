"""Benchmarks for the group comparisons. Target: a t test or one-way ANOVA of 10,000
points in < 20 ms (CLAUDE.md).

Each runs the analysis as the app does (``AnalysisType.run``, which builds the Result).
The ANOVA target is for the ANOVA itself; the post-hoc tests, whose P values are
numerical integrals, are timed separately and gated only against slowdowns.
"""

from dataclasses import replace
from typing import Any

import numpy as np
import pytest

from curvis.analysis import base
from curvis.analysis.base import AnalysisInputs, SeriesData
from curvis.analysis.compare.anova import one_way_anova

TARGET_S = 0.020
N_POINTS = 10_000


def groups(k: int, n: int) -> AnalysisInputs:
    rng = np.random.default_rng(0)
    series = tuple(
        SeriesData(f"s{i}", f"Group {i}", (rng.normal(10 + 0.1 * i, 2, n),)) for i in range(k)
    )
    return AnalysisInputs("T", n, series)


@pytest.fixture(autouse=True)
def warm() -> None:
    """Import SciPy and build the result once, so the timings leave out start-up."""
    base.get("t_test").run(groups(2, 10), base.get("t_test").default_params())


def test_t_test_10k_points(benchmark: Any) -> None:
    kind = base.get("t_test")
    data = groups(2, N_POINTS // 2)
    result = benchmark(kind.run, data, kind.default_params())
    assert result.method == "Unpaired t test with Welch's correction"
    assert benchmark.stats.stats.mean < TARGET_S


def test_one_way_anova_10k_points(benchmark: Any) -> None:
    data = groups(4, N_POINTS // 4)
    columns = [s.columns[0] for s in data.series]
    anova = benchmark(one_way_anova, columns)
    assert anova.df_within == N_POINTS - 4
    assert benchmark.stats.stats.mean < TARGET_S


def run_with_posthoc(benchmark: Any, posthoc: str) -> None:
    kind = base.get("one_way_anova")
    data = groups(4, N_POINTS // 4)
    params = replace(kind.default_params(), posthoc=posthoc)
    result = benchmark.pedantic(kind.run, (data, params), rounds=5, iterations=1)
    assert len(result.tables[-1].rows) == (3 if posthoc == "dunnett" else 6)


def test_one_way_anova_with_tukey_10k_points(benchmark: Any) -> None:
    run_with_posthoc(benchmark, "tukey")


def test_one_way_anova_with_dunnett_10k_points(benchmark: Any) -> None:
    run_with_posthoc(benchmark, "dunnett")


def test_one_way_anova_with_holm_10k_points(benchmark: Any) -> None:
    run_with_posthoc(benchmark, "holm")


def test_mann_whitney_exact_49_by_49(benchmark: Any) -> None:
    """The largest exact case: counting rank sums of 49 of 98 tied ranks."""
    rng = np.random.default_rng(1)
    data = AnalysisInputs(
        "T",
        49,
        (
            SeriesData("a", "A", (np.round(rng.normal(5, 2, 49)),)),
            SeriesData("b", "B", (np.round(rng.normal(6, 2, 49)),)),
        ),
    )
    kind = base.get("mann_whitney")
    result = benchmark(kind.run, data, kind.default_params())
    assert result.notes[0].startswith("The P value is exact")


def test_repeated_measures_anova_10k_points(benchmark: Any) -> None:
    """2,500 subjects × 4 treatments, Greenhouse-Geisser correction (the default)."""
    from curvis.analysis.compare.rm_anova import rm_anova

    rng = np.random.default_rng(2)
    subject = rng.normal(0, 2, N_POINTS // 4)
    columns = [subject + 10 + 0.1 * j + rng.normal(0, 1, N_POINTS // 4) for j in range(4)]
    r = benchmark(rm_anova, columns)
    assert r.n_subjects == N_POINTS // 4
    assert benchmark.stats.stats.mean < TARGET_S


def test_two_way_anova_10k_points(benchmark: Any) -> None:
    """A 4 × 5 design of 500 values per cell, Type III sums of squares."""
    from curvis.analysis.compare.two_way_anova import two_way_anova

    rng = np.random.default_rng(3)
    cells = [[rng.normal(10 + i + 0.5 * j, 2, N_POINTS // 20) for j in range(5)] for i in range(4)]
    r = benchmark(two_way_anova, cells)
    assert r.df_residual == N_POINTS - 20
    assert benchmark.stats.stats.mean < TARGET_S


def test_two_way_anova_analysis_10k_points(benchmark: Any) -> None:
    """The whole analysis as the app runs it, with Tukey's test for both factors."""
    from curvis.analysis.compare.two_way_analysis import TwoWayParams

    rng = np.random.default_rng(4)
    series = tuple(
        SeriesData(f"s{i}{j}", f"A{i}", (rng.normal(10 + i + 0.5 * j, 2, N_POINTS // 20),), f"B{j}")
        for j in range(5)
        for i in range(4)
    )
    data = AnalysisInputs("T", N_POINTS // 20, series, category_factor="B")
    kind = base.get("two_way_anova")
    result = benchmark.pedantic(kind.run, (data, TwoWayParams()), rounds=5, iterations=1)
    assert sum(t.title.startswith("Tukey") for t in result.tables) == 2


def run_kruskal(benchmark: Any, posthoc: str) -> None:
    kind = base.get("kruskal_wallis")
    kind.run(groups(4, 10), kind.default_params())  # import scikit-posthocs first
    data = groups(4, N_POINTS // 4)
    params = replace(kind.default_params(), posthoc=posthoc)
    result = benchmark(kind.run, data, params)
    assert len(result.tables[-1].rows) == (6 if posthoc == "all_pairs" else 3)
    assert benchmark.stats.stats.mean < TARGET_S


def test_kruskal_wallis_with_dunn_10k_points(benchmark: Any) -> None:
    """Kruskal-Wallis and Dunn's test of all pairs, 4 groups of 2,500."""
    run_kruskal(benchmark, "all_pairs")


def test_kruskal_wallis_with_dunn_vs_control_10k_points(benchmark: Any) -> None:
    run_kruskal(benchmark, "control")
