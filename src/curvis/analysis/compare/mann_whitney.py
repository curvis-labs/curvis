"""Mann-Whitney U test (Wilcoxon rank sum test), two-sided.

The method is chosen as R's ``wilcox.test`` (R >= 4.6) chooses it: exact when both
groups have fewer than 50 values, also with ties (then the exact distribution of U given
the tied ranks); otherwise the normal approximation with continuity correction and the
tie correction of the variance. Validated against R
(validation/r/scripts/mann_whitney.R).

U is the number of pairs (a, b) with a > b, ties counting 1/2: the rank sum of group A
minus n_a (n_a + 1) / 2 (R's W for x = A).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from curvis.analysis.compare.samples import Sample, sample

EXACT_LIMIT = 50
"""The exact test is used when both groups have fewer values than this."""

METHOD = "Mann-Whitney test"
EXACT = "exact"
NORMAL = "normal approximation with continuity correction"

_FUZZ = 1e-8
"""R compares U with the support of its distribution with this fuzz (in .pwilcox)."""


@dataclass(frozen=True)
class MannWhitneyStats:
    a: Sample
    b: Sample
    u: float
    p_value: float
    exact: bool
    ties: bool

    @property
    def method_detail(self) -> str:
        return EXACT if self.exact else NORMAL


def mann_whitney(a: npt.ArrayLike, b: npt.ArrayLike) -> MannWhitneyStats:
    """Two-sided Mann-Whitney test of A against B; missing values are left out. Each
    group needs at least one value."""
    from scipy import special, stats

    sa, sb = sample(a), sample(b)
    if sa.n < 1 or sb.n < 1:
        raise ValueError("the Mann-Whitney test needs at least one value per group")
    na, nb = sa.n, sb.n
    ranks = stats.rankdata(np.concatenate([sa.values, sb.values]))
    u = float(np.sum(ranks[:na])) - na * (na + 1) / 2
    ties = len(np.unique(ranks)) != len(ranks)
    exact = na < EXACT_LIMIT and nb < EXACT_LIMIT
    if exact:
        p = _exact_p(u, ranks, na)
    else:
        _, counts = np.unique(ranks, return_counts=True)
        n = na + nb
        sigma = math.sqrt(
            (na * nb / 12) * ((n + 1) - float(np.sum(counts**3 - counts)) / (n * (n - 1)))
        )
        z = u - na * nb / 2
        z = (z - math.copysign(0.5, z) * (z != 0)) / sigma if sigma > 0 else math.nan
        p = 2 * min(float(special.ndtr(z)), float(special.ndtr(-z))) if sigma > 0 else math.nan
    return MannWhitneyStats(sa, sb, u, p, exact, ties)


def _exact_p(u: float, ranks: npt.NDArray[np.float64], na: int) -> float:
    """Two-sided exact P value: min(1, 2 P(U <= u), 2 P(U >= u)) under the permutation
    distribution of the (possibly tied) ranks."""
    density, scale = rank_sum_distribution(ranks, na)
    # Index w of density: the rank sum times `scale`; U = w / scale - na (na + 1) / 2.
    support = np.arange(len(density)) / scale - na * (na + 1) / 2
    lower = float(np.sum(density[support < u + _FUZZ]))
    upper = float(np.sum(density[support >= u - _FUZZ]))
    return min(1.0, 2 * lower, 2 * upper)


def rank_sum_distribution(
    ranks: npt.NDArray[np.float64], m: int
) -> tuple[npt.NDArray[np.float64], int]:
    """The distribution of the sum of ``m`` ranks drawn without replacement from
    ``ranks`` (all choices equally likely), as probabilities indexed by the sum times
    ``scale`` (2 when there are half ranks, else 1). Counts subsets by dynamic
    programming, as R's C code (dpermdist2) does."""
    scale = 1 if np.all(ranks == np.floor(ranks)) else 2
    scores = np.sort(np.rint(ranks * scale).astype(np.int64))
    total = int(np.sum(scores[-m:])) if m else 0
    # counts[j, s]: number of subsets of j scores (so far) with sum s.
    counts = np.zeros((m + 1, total + 1))
    counts[0, 0] = 1.0
    for score in scores:
        if score <= total:
            counts[1:, score:] += counts[:-1, : total + 1 - score]
    distribution = counts[m]
    return distribution / distribution.sum(), scale
