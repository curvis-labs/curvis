"""Two-way ANOVA with interaction (Type III sums of squares) and Tukey's test on the
marginal means of each factor.

Validated against R (validation/r/scripts/two_way_anova.R): ``car::Anova(type = 3)``
with sum-to-zero contrasts, and ``emmeans`` with ``adjust = "tukey"``.

- ``cells[i][j]`` holds the values of level i of factor A at level j of factor B;
  missing values are left out. Every cell needs a value, and N - a b must be positive.
- Each effect is the hypothesis L mu = 0 on the cell means mu (A-major), with
  L = C_a (x) 1_b'/b for A, 1_a'/a (x) C_b for B and C_a (x) C_b for the interaction
  (C_m = [I, -1]). Its sum of squares is (L mu)' (L D L')^-1 (L mu) with D = diag(1/n),
  exactly 0 when L mu = 0. SS_residual is the pooled within-cell sum of squares.
- Marginal means are unweighted (the mean of the cell means over the other factor).
  Tukey compares them with SE = sqrt(MS_residual / m^2 sum_l (1/n_il + 1/n_jl)) over
  the m levels of the other factor, q = |difference| / (SE / sqrt 2), and the exact
  studentized range on the residual df (Tukey-Kramer for unequal SEs).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from curvis.analysis.compare.anova import CONFIDENCE, Comparison, pairs
from curvis.analysis.compare.samples import Sample, sample

type FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class Effect:
    """One row of the ANOVA table: a main effect or the interaction."""

    ss: float
    df: int
    f: float
    p_value: float

    @property
    def ms(self) -> float:
        return self.ss / self.df


@dataclass(frozen=True)
class TwoWayStats:
    cells: tuple[tuple[Sample, ...], ...]
    """cells[i][j]: level i of factor A at level j of factor B."""
    a: Effect
    b: Effect
    interaction: Effect
    ss_residual: float
    df_residual: int

    @property
    def levels_a(self) -> int:
        return len(self.cells)

    @property
    def levels_b(self) -> int:
        return len(self.cells[0])

    @property
    def ms_residual(self) -> float:
        return self.ss_residual / self.df_residual

    @property
    def defined(self) -> bool:
        """False when no value varies within its cell (F and P are NaN)."""
        return self.ss_residual > 0

    @property
    def n(self) -> npt.NDArray[np.int64]:
        """Cell counts, a x b."""
        return np.array([[s.n for s in row] for row in self.cells], dtype=np.int64)

    @property
    def cell_means(self) -> FloatArray:
        """a x b."""
        return np.array([[s.mean for s in row] for row in self.cells], dtype=np.float64)

    @property
    def marginal_means_a(self) -> FloatArray:
        return np.asarray(self.cell_means.mean(axis=1), dtype=np.float64)

    @property
    def marginal_means_b(self) -> FloatArray:
        return np.asarray(self.cell_means.mean(axis=0), dtype=np.float64)


def _contrasts(m: int) -> FloatArray:
    return np.hstack([np.eye(m - 1), -np.ones((m - 1, 1))])


def _effect(lmat: FloatArray, mu: FloatArray, inv_n: FloatArray, ms_r: float, df_r: int) -> Effect:
    from scipy import special

    # An exactly rounded sum per contrast, so equal means give exactly 0 whatever the
    # summation order (a matrix product can leave ~1e-32).
    est = np.array([math.fsum(row * mu) for row in lmat])
    if np.all(est == 0):
        ss = 0.0
    else:
        cov = (lmat * inv_n) @ lmat.T
        ss = float(est @ np.linalg.solve(cov, est))
    df = lmat.shape[0]
    if ms_r > 0:
        f = (ss / df) / ms_r
        p = float(special.fdtrc(df, df_r, f))
    else:
        f = p = math.nan
    return Effect(ss, df, f, p)


def two_way_anova(cells: Sequence[Sequence[npt.ArrayLike]]) -> TwoWayStats:
    """Two-way ANOVA with interaction of ``cells`` (a rows of b cells each)."""
    a = len(cells)
    if a < 2:
        raise ValueError("two-way ANOVA needs at least two levels of each factor")
    b = len(cells[0])
    if b < 2 or any(len(row) != b for row in cells):
        raise ValueError("two-way ANOVA needs at least two levels of each factor")
    samples = tuple(tuple(sample(v) for v in row) for row in cells)
    flat = [s for row in samples for s in row]
    if any(s.n == 0 for s in flat):
        raise ValueError("every cell needs at least one value")
    n_total = sum(s.n for s in flat)
    df_r = n_total - a * b
    if df_r <= 0:
        raise ValueError("two-way ANOVA needs more values than cells")
    mu = np.array([s.mean for s in flat])
    inv_n = 1.0 / np.array([s.n for s in flat], dtype=np.float64)
    ss_r = float(sum(s.ss for s in flat))
    ms_r = ss_r / df_r
    ca, cb = _contrasts(a), _contrasts(b)

    def kron(x: FloatArray, y: FloatArray) -> FloatArray:
        return np.asarray(np.kron(x, y), dtype=np.float64)

    effect_a = _effect(kron(ca, np.full((1, b), 1.0 / b)), mu, inv_n, ms_r, df_r)
    effect_b = _effect(kron(np.full((1, a), 1.0 / a), cb), mu, inv_n, ms_r, df_r)
    effect_ab = _effect(kron(ca, cb), mu, inv_n, ms_r, df_r)
    return TwoWayStats(samples, effect_a, effect_b, effect_ab, ss_r, df_r)


def tukey_marginal(stats: TwoWayStats, factor: str) -> list[Comparison]:
    """Tukey's test on the unweighted marginal means of ``factor`` ("a" or "b")."""
    from curvis.analysis.compare.distributions import (
        studentized_range_critical,
        studentized_range_sf,
    )

    inv_n = 1.0 / stats.n.astype(np.float64)
    if factor == "a":
        marg = stats.marginal_means_a
    elif factor == "b":
        marg = stats.marginal_means_b
        inv_n = inv_n.T
    else:
        raise ValueError(f"no factor {factor!r}")
    m, other = inv_n.shape
    df = stats.df_residual
    crit = studentized_range_critical(1 - CONFIDENCE, m, df) if stats.defined else math.nan
    out: list[Comparison] = []
    for i, j in pairs(m):
        diff = float(marg[j] - marg[i])
        se = math.sqrt(stats.ms_residual / other**2 * float(np.sum(inv_n[i]) + np.sum(inv_n[j])))
        if not stats.defined:
            out.append(Comparison(i, j, diff, se, math.nan, math.nan))
            continue
        half = se / math.sqrt(2)
        q = abs(diff) / half
        p = studentized_range_sf(q, m, df)
        out.append(Comparison(i, j, diff, se, q, p, diff - crit * half, diff + crit * half))
    return out
