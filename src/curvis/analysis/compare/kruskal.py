"""Kruskal-Wallis test and Dunn's multiple comparisons test, Holm-adjusted.

Validated against R (validation/r/scripts/kruskal_wallis.R): ``kruskal.test``,
``dunn.test::dunn.test(altp = TRUE)`` and ``p.adjust(method = "holm")``.

- Missing values are left out; all values are ranked together, ties get midranks.
- H is corrected for ties; P is the upper tail of chi-square on k - 1 df (no exact P).
  With every value the same H and P are not defined (NaN), as in R.
- Dunn's z for groups i < j is (Rbar_j - Rbar_i) / sqrt(S2 (1/n_i + 1/n_j)) with
  S2 = N (N + 1) / 12 - sum(t^3 - t) / (12 (N - 1)); P = 2 P(Z >= |z|), from
  ``scikit_posthocs.posthoc_dunn``.
- Holm: over all k (k - 1) / 2 pairs, or over the k - 1 comparisons with the control.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import numpy as np
import numpy.typing as npt

from curvis.analysis.compare.anova import holm_adjust, pairs
from curvis.analysis.compare.samples import FloatArray, Sample, sample

METHOD = "Kruskal-Wallis test"
DUNN = "Dunn's multiple comparisons test"


@dataclass(frozen=True)
class KruskalStats:
    samples: tuple[Sample, ...]
    mean_ranks: FloatArray
    h: float
    df: int
    p_value: float
    ties: bool
    s2: float
    """The variance factor of Dunn's z, corrected for ties (0 when all values tie)."""

    @property
    def k(self) -> int:
        return len(self.samples)

    @property
    def n_total(self) -> int:
        return sum(s.n for s in self.samples)

    @property
    def defined(self) -> bool:
        """False when every value is the same (H, z and P are NaN)."""
        return not math.isnan(self.h)


@dataclass(frozen=True)
class DunnComparison:
    """Groups i and j (indices); the difference is mean rank j - mean rank i."""

    i: int
    j: int
    mean_rank_diff: float
    z: float
    p_unadjusted: float
    p_value: float
    """Holm-adjusted."""


def kruskal_wallis(groups: Sequence[npt.ArrayLike]) -> KruskalStats:
    """Kruskal-Wallis test of ``groups``; missing values are left out. Needs at least
    two groups with at least one value each."""
    from scipy import stats

    samples = tuple(sample(g) for g in groups)
    if len(samples) < 2 or any(s.n < 1 for s in samples):
        raise ValueError("the Kruskal-Wallis test needs at least one value in each group")
    values = np.concatenate([s.values for s in samples])
    n_total = len(values)
    ranks = stats.rankdata(values)
    bounds = np.cumsum([0] + [s.n for s in samples])
    mean_ranks = np.array([float(np.mean(ranks[a:b])) for a, b in pairwise(bounds)])
    _, counts = np.unique(values, return_counts=True)
    tie_sum = float(np.sum(counts.astype(np.float64) ** 3 - counts))
    s2 = n_total * (n_total + 1) / 12 - tie_sum / (12 * (n_total - 1)) if n_total > 1 else 0.0
    df = len(samples) - 1
    if len(counts) == 1:  # every value the same: R gives H = 0 / 0
        h = p = math.nan
        s2 = 0.0
    else:
        result = stats.kruskal(*(s.values for s in samples))
        h, p = float(result.statistic), float(result.pvalue)
    return KruskalStats(samples, mean_ranks, h, df, p, len(counts) < n_total, s2)


def dunn(kw: KruskalStats, control: int | None = None) -> list[DunnComparison]:
    """Dunn's test of all pairs (``control`` None) or of each group against ``control``,
    Holm-adjusted over the comparisons made. Against a control, i is the control and j
    the other group, in group order."""
    k = kw.k
    if control is None:
        chosen = pairs(k)
    else:
        if not 0 <= control < k:
            raise ValueError(f"control {control} is not a group")
        chosen = [(control, g) for g in range(k) if g != control]
    raw = _dunn_p_matrix(kw)
    rows: list[tuple[int, int, float, float, float]] = []
    for i, j in chosen:
        diff = float(kw.mean_ranks[j] - kw.mean_ranks[i])
        if kw.defined:
            ni, nj = kw.samples[i].n, kw.samples[j].n
            z = diff / math.sqrt(kw.s2 * (1 / ni + 1 / nj))
        else:
            z = math.nan
        rows.append((i, j, diff, z, float(raw[i, j])))
    adjusted = holm_adjust([r[4] for r in rows]) if kw.defined else [math.nan] * len(rows)
    return [
        DunnComparison(i, j, diff, z, p, adj)
        for (i, j, diff, z, p), adj in zip(rows, adjusted, strict=True)
    ]


def _dunn_p_matrix(kw: KruskalStats) -> FloatArray:
    """Unadjusted two-sided P values of Dunn's test for every pair, as a k x k matrix."""
    if not kw.defined:
        return np.full((kw.k, kw.k), np.nan)
    import scikit_posthocs

    # A list of groups is labelled 1..k in order.
    table: Any = scikit_posthocs.posthoc_dunn(
        [s.values for s in kw.samples], p_adjust=None, sort=False
    )
    order = list(range(1, kw.k + 1))
    return np.asarray(table.loc[order, order].to_numpy(), dtype=np.float64)
