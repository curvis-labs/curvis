"""Repeated-measures one-way ANOVA (rows are subjects) with the Greenhouse-Geisser
correction.

Validated against R (validation/r/scripts/rm_anova.R): ``aov`` with an ``Error``
term, ``car::Anova`` on the multivariate linear model, ``emmeans`` and ``multcomp``.

- A row with no values is ignored; a row with some but not all values is an
  incomplete subject and is left out (listwise). The analysis uses the complete ones.
- SS_treatment = n sum_j (mean_j - m)^2, SS_subjects = k sum_i (mean_i - m)^2 and
  SS_error = sum (y_ij - mean_i - mean_j + m)^2, on k - 1, n - 1 and (n - 1)(k - 1) df.
  A residual SS at the rounding level of the data (exactly additive data) counts as 0:
  F and P are then not defined (NaN).
- Greenhouse-Geisser epsilon = trace(S_c)^2 / ((k - 1) trace(S_c^2)) with S_c the
  double-centred covariance matrix of the treatments; the corrected test uses the same
  F on epsilon times both df.
- Post-hoc tests use the pooled error (MS_error on (n - 1)(k - 1) df) for every
  comparison: :attr:`RMAnovaStats.pooled` presents the result as a one-way ANOVA with
  n values per treatment, so :mod:`curvis.analysis.compare.anova`'s Tukey, Dunnett and
  Holm tests apply unchanged.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from curvis.analysis.compare.anova import AnovaStats
from curvis.analysis.compare.samples import Sample, mean

type FloatArray = npt.NDArray[np.float64]

ROUNDING = 64 * np.finfo(np.float64).eps
"""Residuals below this times the largest |value| are rounding noise."""


@dataclass(frozen=True)
class RMAnovaStats:
    values: FloatArray
    """The complete subjects: an n x k matrix."""
    n_incomplete: int
    """Rows with some but not all values, left out."""
    means: FloatArray
    df_treatment: int
    df_subjects: int
    df_error: int
    ss_treatment: float
    ss_subjects: float
    ss_error: float
    f: float
    p_value: float
    gg_epsilon: float
    gg_p_value: float

    @property
    def k(self) -> int:
        return int(self.values.shape[1])

    @property
    def n_subjects(self) -> int:
        return int(self.values.shape[0])

    @property
    def ms_treatment(self) -> float:
        return self.ss_treatment / self.df_treatment

    @property
    def ms_subjects(self) -> float:
        return self.ss_subjects / self.df_subjects

    @property
    def ms_error(self) -> float:
        return self.ss_error / self.df_error

    @property
    def gg_df_treatment(self) -> float:
        return self.gg_epsilon * self.df_treatment

    @property
    def gg_df_error(self) -> float:
        return self.gg_epsilon * self.df_error

    @property
    def defined(self) -> bool:
        """False when nothing varies beyond subject and treatment effects."""
        return self.ss_error > 0

    @property
    def samples(self) -> tuple[Sample, ...]:
        out = []
        for j in range(self.k):
            column = self.values[:, j]
            m = float(self.means[j])
            out.append(Sample(column, self.n_subjects, m, float(np.sum((column - m) ** 2))))
        return tuple(out)

    @property
    def pooled(self) -> AnovaStats:
        """The treatments as a one-way ANOVA whose error is the RM error, for the
        post-hoc tests."""
        return AnovaStats(
            self.samples,
            self.df_treatment,
            self.df_error,
            self.ss_treatment,
            self.ss_error,
            self.ms_treatment,
            self.ms_error,
            self.f,
            self.p_value,
        )


def complete_rows(treatments: Sequence[npt.ArrayLike]) -> tuple[FloatArray, int]:
    """The rows with a value in every treatment (n x k), and the number of rows with
    some but not all values."""
    columns = [np.asarray(t, dtype=np.float64).ravel() for t in treatments]
    if len({len(c) for c in columns}) > 1:
        raise ValueError("every treatment needs the same number of rows")
    full = np.column_stack(columns) if columns else np.zeros((0, 0))
    present = (~np.isnan(full)).sum(axis=1)
    k = full.shape[1]
    complete = present == k
    incomplete = int(np.count_nonzero((present > 0) & ~complete))
    return full[complete], incomplete


def _means(y: FloatArray, axis: int) -> FloatArray:
    """Means along ``axis``, refined with a second pass as :func:`mean` does."""
    count = y.shape[axis]
    first = np.sum(y, axis=axis) / count
    centred = y - np.expand_dims(first, axis)
    return np.asarray(first + np.sum(centred, axis=axis) / count, dtype=np.float64)


def rm_anova(treatments: Sequence[npt.ArrayLike]) -> RMAnovaStats:
    """Repeated-measures ANOVA of ``treatments`` (equal-length columns; row i is
    subject i). Needs at least two treatments and two complete subjects."""
    from scipy import special

    if len(treatments) < 2:
        raise ValueError("repeated-measures ANOVA needs at least two treatments")
    y, incomplete = complete_rows(treatments)
    n, k = y.shape
    if n < 2:
        raise ValueError("repeated-measures ANOVA needs at least two complete subjects")
    col_means = _means(y, axis=0)
    row_means = _means(y, axis=1)
    grand = mean(y.ravel(order="F"))
    ss_t = 0.0 if np.all(col_means == col_means[0]) else n * float(np.sum((col_means - grand) ** 2))
    ss_s = 0.0 if np.all(row_means == row_means[0]) else k * float(np.sum((row_means - grand) ** 2))
    resid = y - row_means[:, None] - col_means[None, :] + grand
    ss_e = float(np.sum(resid**2))
    scale = float(np.max(np.abs(y)))
    if not ss_e > (ROUNDING * scale) ** 2 * n * k:
        ss_e = 0.0
    df_t, df_s = k - 1, n - 1
    df_e = df_t * df_s
    if ss_e > 0:
        f = (ss_t / df_t) / (ss_e / df_e)
        p = float(special.fdtrc(df_t, df_e, f))
        if k == 2:
            eps = 1.0
        else:
            centre = np.eye(k) - 1.0 / k
            sc = centre @ np.cov(y, rowvar=False) @ centre
            eps = float(np.trace(sc)) ** 2 / (df_t * float(np.sum(sc * sc)))
        gg_p = float(special.fdtrc(eps * df_t, eps * df_e, f))
    else:
        f = p = eps = gg_p = math.nan
    return RMAnovaStats(
        y, incomplete, col_means, df_t, df_s, df_e, ss_t, ss_s, ss_e, f, p, eps, gg_p
    )
