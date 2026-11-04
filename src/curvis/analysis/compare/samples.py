"""Sample statistics shared by the group comparisons.

Means are refined with a second pass over the deviations, as R's ``mean`` does (and
:func:`curvis.data.row_stats`), so data with a large offset keep their precision and
equal values have a sum of squares of exactly 0.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

type FloatArray = npt.NDArray[np.float64]


def present(values: npt.ArrayLike) -> FloatArray:
    """``values`` as a float array without missing values (NaN)."""
    array = np.asarray(values, dtype=np.float64).ravel()
    return array[~np.isnan(array)]


def mean(x: FloatArray) -> float:
    """The mean of ``x`` (NaN if empty), refined with a second pass."""
    n = len(x)
    if n == 0:
        return float("nan")
    first = float(np.sum(x)) / n
    if not np.isfinite(first):
        return first
    return first + float(np.sum(x - first)) / n


@dataclass(frozen=True)
class Sample:
    """n, mean and sum of squared deviations of one group's values."""

    values: FloatArray
    n: int
    mean: float
    ss: float
    """Sum of squared deviations from the mean; NaN when n = 0."""

    @property
    def variance(self) -> float:
        """Sample variance (n - 1 in the denominator); NaN when n < 2."""
        return self.ss / (self.n - 1) if self.n >= 2 else float("nan")

    @property
    def sd(self) -> float:
        return float(np.sqrt(self.variance))

    @property
    def median(self) -> float:
        return float(np.median(self.values)) if self.n else float("nan")


def sample(values: npt.ArrayLike) -> Sample:
    """The :class:`Sample` of ``values``, leaving out missing values."""
    x = present(values)
    m = mean(x)
    ss = float(np.sum((x - m) ** 2)) if len(x) else float("nan")
    return Sample(x, len(x), m, ss)
