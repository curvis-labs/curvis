"""Group-comparison statistics: checks that do not need R.

The R fixtures (tests/validation) are the main validation. These tests check exact
identities, compare with brute force, and push the numerical integration to extremes
the fixtures do not reach.
"""

import itertools
import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from scipy import special, stats

import curvis.analysis.compare.distributions as dist
from curvis.analysis.compare.anova import holm_adjust, one_way_anova, tukey
from curvis.analysis.compare.mann_whitney import mann_whitney, rank_sum_distribution
from curvis.analysis.compare.samples import sample
from curvis.analysis.compare.ttest import Problem, TTestKind, t_test

# --- distributions -------------------------------------------------------------------


@pytest.mark.parametrize("df", [1, 2, 3, 7, 30, 295, 10_000])
@pytest.mark.parametrize("q", [0.2, 1.0, 3.0, 8.0, 20.0, 60.0])
def test_studentized_range_of_two_means_is_a_t_test(df: float, q: float) -> None:
    """For k = 2, P(Q > q) = P(|T| > q / sqrt(2)) exactly."""
    expected = 2 * special.stdtr(df, -q / math.sqrt(2))
    assert dist.studentized_range_sf(q, 2, df) == pytest.approx(expected, rel=1e-10)


@pytest.mark.parametrize("df", [1, 4, 40, 5_000])
@pytest.mark.parametrize("c", [0.5, 2.0, 6.0, 25.0])
def test_dunnett_with_one_comparison_is_a_t_test(df: float, c: float) -> None:
    expected = 2 * special.stdtr(df, -c)
    assert dist.dunnett_sf(c, np.array([7.0]), 4.0, df) == pytest.approx(expected, rel=1e-10)


def test_dunnett_with_independent_limit_matches_sidak() -> None:
    """With a huge control group the comparisons are nearly independent and, with df
    large, normal: P = 1 - (1 - p1)^m for the one-comparison p1."""
    n = np.array([5.0, 5.0, 5.0])
    c, m = 2.2, 3
    p1 = 2 * special.ndtr(-c)
    got = dist.dunnett_sf(c, n, 1e9, 1e7)
    assert got == pytest.approx(1 - (1 - p1) ** m, rel=1e-5)


@pytest.mark.parametrize(
    ("n", "n_control", "df", "c"),
    [
        (np.array([200.0, 2.0]), 2.0, 200.0, 3.0),  # very unequal groups: sharp edges in z
        (np.array([3.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0]), 3.0, 1.0, 5.0),  # df = 1
        (np.array([10.0, 12.0]), 11.0, 50_000.0, 2.5),  # very large df
        (np.array([6.0, 6.0]), 6.0, 15.0, 40.0),  # P far below 1e-10
    ],
)
def test_dunnett_does_not_change_with_finer_integration(
    n: np.ndarray, n_control: float, df: float, c: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    coarse = dist.dunnett_sf(c, n, n_control, df)
    for name in ("_U_PANELS", "_Z_PANELS_MIN"):
        monkeypatch.setattr(dist, name, getattr(dist, name) * 3)
    for name in ("_U_PANEL_WIDTH", "_Z_PANEL_WIDTH"):
        monkeypatch.setattr(dist, name, getattr(dist, name) / 3)
    monkeypatch.setattr(dist, "_U_PANELS_MAX", 512)
    monkeypatch.setattr(dist, "_Z_PANELS_MAX", 512)
    monkeypatch.setattr(dist, "DROP", 80.0)
    fine = dist.dunnett_sf(c, n, n_control, df)
    assert 0 < fine < 1
    assert coarse == pytest.approx(fine, rel=1e-10)


@pytest.mark.parametrize(("k", "df"), [(3, 2), (5, 12), (10, 100)])
def test_critical_values_have_the_wanted_tail(k: int, df: float) -> None:
    q = dist.studentized_range_critical(0.05, k, df)
    assert dist.studentized_range_sf(q, k, df) == pytest.approx(0.05, rel=1e-10)
    n = np.full(k - 1, 4.0)
    c = dist.dunnett_critical(0.05, n, 4.0, df)
    assert dist.dunnett_sf(c, n, 4.0, df) == pytest.approx(0.05, rel=1e-10)


def test_studentized_range_agrees_with_scipy_where_scipy_is_accurate() -> None:
    for k, df, q in [(3, 10, 2.5), (6, 40, 4.0), (4, 8, 1.0)]:
        expected = stats.studentized_range.sf(q, k, df)
        assert dist.studentized_range_sf(q, k, df) == pytest.approx(expected, rel=1e-8)


def test_zero_and_missing_statistics() -> None:
    assert dist.studentized_range_sf(0.0, 3, 10) == 1.0
    assert math.isnan(dist.studentized_range_sf(math.nan, 3, 10))
    assert dist.dunnett_sf(0.0, np.array([3.0]), 3.0, 4) == 1.0
    # df = 4: heavy tails, so even c = 1e6 leaves P ~ 1e-23, between the one-comparison
    # P and the Bonferroni bound.
    one = 2 * special.stdtr(4, -1e6)
    assert one <= dist.dunnett_sf(1e6, np.array([3.0, 3.0]), 3.0, 4) <= 2 * one


# --- t test and ANOVA ----------------------------------------------------------------


def test_anova_of_two_groups_is_students_t_test_squared() -> None:
    a, b = [1.0, 2.5, 3.1, 4.0], [2.2, 3.9, 4.8, 5.5, 6.1]
    t = t_test(a, b, TTestKind.STUDENT)
    anova = one_way_anova([a, b])
    assert anova.f == pytest.approx(t.t**2, rel=1e-12)
    assert anova.p_value == pytest.approx(t.p_value, rel=1e-10)


def test_paired_t_test_is_a_one_sample_test_of_the_differences() -> None:
    a = np.array([5.0, 6.1, np.nan, 7.3, 4.4])
    b = np.array([5.9, 6.0, 8.0, 8.1, np.nan])
    r = t_test(a, b, TTestKind.PAIRED)
    expected = stats.ttest_1samp([0.9, -0.1, 0.8], 0.0)
    assert r.n_pairs == 3
    assert r.t == pytest.approx(expected.statistic, rel=1e-12)
    assert r.p_value == pytest.approx(expected.pvalue, rel=1e-10)


def test_undefined_t_tests_say_why() -> None:
    assert t_test([1.0], [2.0], TTestKind.STUDENT).problem is Problem.TOO_FEW
    assert t_test([1.0], [2.0, 3.0], TTestKind.WELCH).problem is Problem.TOO_FEW
    r = t_test([2.0, 2.0], [3.0, 3.0], TTestKind.WELCH)
    assert r.problem is Problem.NO_VARIATION and math.isnan(r.p_value)
    assert r.difference == 1.0


def test_tukey_on_equal_groups_gives_p_one() -> None:
    a = one_way_anova([[1.0, 2.0, 3.0]] * 3)
    assert [c.p_value for c in tukey(a)] == [1.0, 1.0, 1.0]


def test_holm_adjust_matches_r() -> None:
    # p.adjust(c(0.01, 0.04, 0.03, 0.005), "holm") in R: 0.03 0.06 0.06 0.02
    assert holm_adjust([0.01, 0.04, 0.03, 0.005]) == pytest.approx([0.03, 0.06, 0.06, 0.02])
    assert holm_adjust([0.5, 0.9]) == [1.0, 1.0]


def test_the_mean_keeps_its_precision_with_a_large_offset() -> None:
    s = sample([1e9 + 0.1, 1e9 + 0.2, 1e9 + 0.3, np.nan])
    assert s.n == 3
    assert s.mean == 1e9 + 0.2
    assert sample([4.2, 4.2, 4.2]).ss == 0.0


# --- Mann-Whitney ----------------------------------------------------------------------


def brute_force_p(a: list[float], b: list[float]) -> float:
    """Two-sided exact P by enumerating every split of the ranks (R's definition)."""
    ranks = stats.rankdata(a + b)
    u = sum(ranks[: len(a)]) - len(a) * (len(a) + 1) / 2
    us = [
        sum(ranks[list(c)]) - len(a) * (len(a) + 1) / 2
        for c in itertools.combinations(range(len(ranks)), len(a))
    ]
    lower = sum(x <= u + 1e-8 for x in us) / len(us)
    upper = sum(x >= u - 1e-8 for x in us) / len(us)
    return min(1.0, 2 * lower, 2 * upper)


@settings(max_examples=60, deadline=None)
@given(
    st.lists(st.integers(0, 6), min_size=1, max_size=6),
    st.lists(st.integers(0, 6), min_size=1, max_size=6),
)
def test_exact_p_with_ties_matches_enumeration(a: list[int], b: list[int]) -> None:
    fa, fb = [float(v) for v in a], [float(v) for v in b]
    r = mann_whitney(fa, fb)
    assert r.exact
    assert r.p_value == pytest.approx(brute_force_p(fa, fb), rel=1e-12)


def test_rank_sum_distribution_with_half_ranks() -> None:
    density, scale = rank_sum_distribution(np.array([1.5, 1.5, 3.0, 4.0]), 2)
    assert scale == 2
    assert density.sum() == pytest.approx(1.0)
    # 6 pairs: sums 3 (1.5+1.5), 4.5 (x2), 5.5 (x2), 7.
    support = np.flatnonzero(density) / scale
    assert list(support) == [3.0, 4.5, 5.5, 7.0]
    assert density[np.flatnonzero(density)] == pytest.approx([1 / 6, 2 / 6, 2 / 6, 1 / 6])


def test_large_groups_use_the_normal_approximation() -> None:
    rng = np.random.default_rng(5)
    a, b = rng.normal(0, 1, 60), rng.normal(0.4, 1, 70)
    r = mann_whitney(a, b)
    assert not r.exact
    expected = stats.mannwhitneyu(a, b, method="asymptotic", use_continuity=True)
    assert r.u == expected.statistic
    assert r.p_value == pytest.approx(expected.pvalue, rel=1e-10)
