"""t tests: unpaired (Student, or with Welch's correction) and paired.

:func:`t_test` computes the statistics; :class:`TTest` is the analysis. Validated
against R's ``t.test`` (validation/r/scripts/t_test.R): the difference is B - A (the
second group minus the first), the test is two-sided and the confidence interval 95%.
When the test is not defined (too few values, or no variation) the SE, t, P value and
CI are NaN and :attr:`TTestStats.problem` says why.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import numpy.typing as npt

from curvis.analysis.compare.samples import Sample, sample

CONFIDENCE = 0.95
_CONSTANT = 10 * np.finfo(np.float64).eps
"""t.test's rule for "data are essentially constant": SE < this times the means' size."""


class TTestKind(StrEnum):
    STUDENT = "student"
    WELCH = "welch"
    PAIRED = "paired"


METHODS = {
    TTestKind.STUDENT: "Unpaired t test",
    TTestKind.WELCH: "Unpaired t test with Welch's correction",
    TTestKind.PAIRED: "Paired t test",
}


class Problem(StrEnum):
    TOO_FEW = "too_few"
    NO_VARIATION = "no_variation"


@dataclass(frozen=True)
class TTestStats:
    kind: TTestKind
    a: Sample
    """Group A (for a paired test: the values of complete pairs)."""
    b: Sample
    n_pairs: int
    """Number of complete pairs (paired test only, else 0)."""
    difference: float
    """mean(B) - mean(A); for a paired test, the mean of the differences B - A."""
    se: float
    t: float
    df: float
    p_value: float
    ci_lower: float
    ci_upper: float
    problem: Problem | None = None


def t_test(a: npt.ArrayLike, b: npt.ArrayLike, kind: TTestKind) -> TTestStats:
    """Two-sided t test of B against A. Missing values are left out; for a paired test
    the values are paired by position and a pair with a missing value is left out."""
    from scipy import special

    xa = np.asarray(a, dtype=np.float64).ravel()
    xb = np.asarray(b, dtype=np.float64).ravel()
    if kind is TTestKind.PAIRED:
        if len(xa) != len(xb):
            raise ValueError("a paired t test needs groups of the same length")
        keep = ~np.isnan(xa) & ~np.isnan(xb)
        xa, xb = xa[keep], xb[keep]
    sa, sb = sample(xa), sample(xb)
    nan = math.nan
    se = df = scale = nan
    if kind is TTestKind.PAIRED:
        d = sample(xb - xa)
        n_pairs = d.n
        difference = d.mean if d.n else nan
        too_few = d.n < 2
        if not too_few:
            df = d.n - 1
            se = math.sqrt(d.variance / d.n)
            scale = abs(d.mean)
    else:
        n_pairs = 0
        difference = sb.mean - sa.mean
        if kind is TTestKind.WELCH:
            too_few = sa.n < 2 or sb.n < 2
            if not too_few:
                va, vb = sa.variance / sa.n, sb.variance / sb.n
                se = math.sqrt(va + vb)
                with np.errstate(invalid="ignore", divide="ignore"):
                    df = float(np.float64(se) ** 4 / (va**2 / (sa.n - 1) + vb**2 / (sb.n - 1)))
        else:
            too_few = sa.n < 1 or sb.n < 1 or sa.n + sb.n < 3
            if not too_few:
                df = sa.n + sb.n - 2
                pooled = ((sa.ss if sa.n > 1 else 0.0) + (sb.ss if sb.n > 1 else 0.0)) / df
                se = math.sqrt(pooled * (1 / sa.n + 1 / sb.n))
        if not too_few:
            scale = max(abs(sa.mean), abs(sb.mean))
    if too_few:
        return TTestStats(
            kind, sa, sb, n_pairs, difference, nan, nan, nan, nan, nan, nan, Problem.TOO_FEW
        )
    if not math.isfinite(df):
        df = nan
    if not (math.isfinite(se) and se > 0 and se >= _CONSTANT * scale):
        return TTestStats(
            kind, sa, sb, n_pairs, difference, nan, nan, df, nan, nan, nan, Problem.NO_VARIATION
        )
    t = difference / se
    p = 2 * float(special.stdtr(df, -abs(t)))
    margin = float(special.stdtrit(df, 1 - (1 - CONFIDENCE) / 2)) * se
    return TTestStats(
        kind, sa, sb, n_pairs, difference, se, t, df, p, difference - margin, difference + margin
    )
