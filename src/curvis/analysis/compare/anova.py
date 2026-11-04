"""Ordinary one-way ANOVA and its post-hoc tests: Tukey's HSD (Tukey-Kramer for unequal
n), Dunnett's test against a control, and pairwise t tests with Holm's correction.

Validated against R (validation/r/scripts/one_way_anova.R): ``aov``, ``TukeyHSD``,
``multcomp::glht`` and ``pairwise.t.test(pool.sd = TRUE, p.adjust.method = "holm")``.
All post-hoc tests use the ANOVA's pooled error (MS_within on df_within). Tukey and
Dunnett P values come from the exact integrals in
:mod:`curvis.analysis.compare.distributions`. Pairs are (i, j) with i < j in group
order, and the difference of a pair is mean_j - mean_i.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from curvis.analysis.compare.samples import Sample, mean, sample

CONFIDENCE = 0.95


@dataclass(frozen=True)
class AnovaStats:
    samples: tuple[Sample, ...]
    df_between: int
    df_within: int
    ss_between: float
    ss_within: float
    ms_between: float
    ms_within: float
    f: float
    p_value: float

    @property
    def k(self) -> int:
        return len(self.samples)

    @property
    def defined(self) -> bool:
        """False when there is no variation within groups (F and P are NaN), or no
        degrees of freedom for it."""
        return self.df_within > 0 and self.ss_within > 0

    @property
    def n(self) -> npt.NDArray[np.float64]:
        return np.array([s.n for s in self.samples], dtype=np.float64)

    @property
    def means(self) -> npt.NDArray[np.float64]:
        return np.array([s.mean for s in self.samples], dtype=np.float64)


def one_way_anova(groups: Sequence[npt.ArrayLike]) -> AnovaStats:
    """Ordinary one-way ANOVA of ``groups``; missing values are left out. Every group
    needs at least one value, and there must be at least two groups."""
    from scipy import special

    samples = tuple(sample(g) for g in groups)
    if len(samples) < 2:
        raise ValueError("one-way ANOVA needs at least two groups")
    if any(s.n == 0 for s in samples):
        raise ValueError("every group needs at least one value")
    k = len(samples)
    n_total = sum(s.n for s in samples)
    df_b, df_w = k - 1, n_total - k
    means = [s.mean for s in samples]
    grand = mean(np.concatenate([s.values for s in samples]))
    if all(m == means[0] for m in means):
        ss_b = 0.0
    else:
        ss_b = float(sum(s.n * (s.mean - grand) ** 2 for s in samples))
    ss_w = float(sum(s.ss for s in samples))
    ms_b = ss_b / df_b
    ms_w = ss_w / df_w if df_w > 0 else math.nan
    if df_w > 0 and ss_w > 0:
        f = ms_b / ms_w
        p = float(special.fdtrc(df_b, df_w, f))
    else:
        f = p = math.nan
    return AnovaStats(samples, df_b, df_w, ss_b, ss_w, ms_b, ms_w, f, p)


def pairs(k: int) -> list[tuple[int, int]]:
    """All pairs (i, j), i < j, in the order of R's TukeyHSD and pairwise.t.test."""
    return [(i, j) for i in range(k - 1) for j in range(i + 1, k)]


@dataclass(frozen=True)
class Comparison:
    """One post-hoc comparison: group ``j`` against group ``i`` (difference mean_j - mean_i)."""

    i: int
    j: int
    difference: float
    se: float
    """Standard error of the difference (for Tukey: sqrt(MS_within (1/n_i + 1/n_j)))."""
    statistic: float
    """q for Tukey, t for Dunnett and Holm."""
    p_value: float
    ci_lower: float = math.nan
    ci_upper: float = math.nan
    p_unadjusted: float = math.nan
    """Holm only: the P value before the correction."""


def tukey(anova: AnovaStats) -> list[Comparison]:
    """Tukey's honestly significant difference test (Tukey-Kramer for unequal n)."""
    from curvis.analysis.compare.distributions import (
        studentized_range_critical,
        studentized_range_sf,
    )

    k, df = anova.k, anova.df_within
    crit = studentized_range_critical(1 - CONFIDENCE, k, df) if anova.defined else math.nan
    out: list[Comparison] = []
    for i, j in pairs(k):
        si, sj = anova.samples[i], anova.samples[j]
        diff = sj.mean - si.mean
        se = math.sqrt(anova.ms_within * (1 / si.n + 1 / sj.n)) if df > 0 else math.nan
        if not anova.defined:
            out.append(Comparison(i, j, diff, se, math.nan, math.nan))
            continue
        half = se / math.sqrt(2)
        q = abs(diff) / half
        p = studentized_range_sf(q, k, df)
        out.append(Comparison(i, j, diff, se, q, p, diff - crit * half, diff + crit * half))
    return out


def dunnett(anova: AnovaStats, control: int) -> list[Comparison]:
    """Dunnett's test of every group against group ``control`` (two-sided)."""
    from curvis.analysis.compare.distributions import dunnett_critical, dunnett_sf

    k, df = anova.k, anova.df_within
    if not 0 <= control < k:
        raise ValueError(f"control group {control} does not exist")
    others = [g for g in range(k) if g != control]
    n = anova.n
    nc = n[control]
    n_others = n[others]
    crit = dunnett_critical(1 - CONFIDENCE, n_others, nc, df) if anova.defined else math.nan
    sc = anova.samples[control]
    out: list[Comparison] = []
    for g in others:
        s = anova.samples[g]
        diff = s.mean - sc.mean
        se = math.sqrt(anova.ms_within * (1 / s.n + 1 / sc.n)) if df > 0 else math.nan
        if not anova.defined:
            out.append(Comparison(control, g, diff, se, math.nan, math.nan))
            continue
        t = diff / se
        p = dunnett_sf(abs(t), n_others, nc, df)
        out.append(Comparison(control, g, diff, se, t, p, diff - crit * se, diff + crit * se))
    return out


def holm_adjust(p: Sequence[float]) -> list[float]:
    """Holm's step-down adjustment (R's ``p.adjust(method = "holm")``)."""
    values = np.asarray(p, dtype=np.float64)
    m = len(values)
    order = np.argsort(values, kind="stable")
    scaled = (m - np.arange(m)) * values[order]
    adjusted = np.minimum(1.0, np.maximum.accumulate(scaled))
    out = np.empty(m)
    out[order] = adjusted
    return [float(v) for v in out]


def holm_pairwise(anova: AnovaStats) -> list[Comparison]:
    """t tests of all pairs with the pooled error, P values adjusted with Holm's method."""
    from scipy import special

    df = anova.df_within
    rows: list[tuple[int, int, float, float, float, float]] = []
    for i, j in pairs(anova.k):
        si, sj = anova.samples[i], anova.samples[j]
        diff = sj.mean - si.mean
        se = math.sqrt(anova.ms_within * (1 / si.n + 1 / sj.n)) if df > 0 else math.nan
        if anova.defined:
            t = diff / se
            p = 2 * float(special.stdtr(df, -abs(t)))
        else:
            t = p = math.nan
        rows.append((i, j, diff, se, t, p))
    raw = [r[5] for r in rows]
    adjusted = holm_adjust(raw) if anova.defined else [math.nan] * len(rows)
    return [
        Comparison(i, j, diff, se, t, adj, p_unadjusted=p)
        for (i, j, diff, se, t, p), adj in zip(rows, adjusted, strict=True)
    ]
