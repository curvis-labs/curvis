"""Edge cases of violin densities and histogram bins that R fixtures cannot hold
(R's bw.nrd0 and hist() refuse them). The R definitions are checked in
tests/validation (violin_density, histogram_bins)."""

import math

import numpy as np
import pytest

from curvis.charts.stats import (
    BinMode,
    bin_counts,
    histogram_bins,
    kde,
    pretty,
    r_seq,
    violin_density,
)


def test_a_violin_needs_two_values() -> None:
    for values in ([], [math.nan], [4.2], [math.nan, 4.2]):
        found = violin_density(values)
        assert math.isnan(found.bw) and len(found.grid) == 0 and len(found.density) == 0
    assert violin_density([math.nan, 4.2]).n == 1


def test_untrimmed_violin_reaches_three_bandwidths_further() -> None:
    values = [1.0, 2.0, 4.0, 8.0]
    trimmed, full = violin_density(values), violin_density(values, trim=False)
    assert trimmed.grid[0] == 1.0 and trimmed.grid[-1] == 8.0
    assert full.grid[0] == pytest.approx(1.0 - 3 * full.bw)
    assert full.grid[-1] == pytest.approx(8.0 + 3 * full.bw)


def test_density_integrates_to_one_and_chunks_agree() -> None:
    rng = np.random.default_rng(1)
    x = rng.normal(size=30_000)
    grid = np.linspace(-8, 8, 2001)
    density = kde(x, 0.3, grid)
    assert np.trapezoid(density, grid) == pytest.approx(1.0, abs=1e-6)
    z = (grid[:, None] - x[None, :1000]) / 0.3
    direct = np.exp(-0.5 * z * z).sum(axis=1) / (1000 * 0.3 * math.sqrt(2 * math.pi))
    np.testing.assert_allclose(kde(x[:1000], 0.3, grid), direct, rtol=1e-12)


def test_histogram_without_values() -> None:
    found = histogram_bins([np.array([]), np.array([math.nan])])
    assert len(found.breaks) == 0
    assert [len(c) for c in found.counts] == [0, 0] and found.n_outside == (0, 0)


def test_infinite_values_are_not_binned() -> None:
    found = histogram_bins([np.array([1.0, 2.0, math.inf, -math.inf, 3.0])])
    assert found.counts[0].sum() == 3 and found.breaks[-1] >= 3.0


def test_values_on_edges_go_left_and_the_lowest_edge_is_included() -> None:
    counts, outside = bin_counts(np.array([0.0, 1.0, 1.5, 2.0, 3.0, -1.0]), np.array([0, 1, 2]))
    assert counts.tolist() == [2, 2] and outside == 2


def test_width_bins_reach_the_largest_value() -> None:
    found = histogram_bins([np.array([0.2, 3.1])], BinMode.WIDTH, width=1.0, start=0.0)
    assert found.breaks.tolist() == [0.0, 1.0, 2.0, 3.0, 4.0]


def test_count_bins_split_the_range_exactly() -> None:
    found = histogram_bins([np.array([2.0, 3.0, 11.0])], BinMode.COUNT, count=3)
    assert found.breaks.tolist() == [2.0, 5.0, 8.0, 11.0]
    assert found.counts[0].tolist() == [2, 0, 1]


def test_pretty_and_seq_small_cases() -> None:
    assert pretty(0.0, 0.0, 5, 1).tolist() == [-1.0, 0.0]  # as R
    assert pretty(0.0, 10.0).tolist() == [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]
    assert r_seq(1.0, 2.0, 1).tolist() == [1.0]
    assert r_seq(1.0, 3.0, 3).tolist() == [1.0, 2.0, 3.0]
