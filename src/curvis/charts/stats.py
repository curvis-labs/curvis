"""Statistics drawn by violin plots and histograms, to R's definitions.

- :func:`violin_density`: a Gaussian kernel density estimate with R's default bandwidth
  (:func:`bw_nrd0`), on an even grid over the data range (trimmed) or 3 bandwidths past
  it (not trimmed, ``density()``'s ``cut = 3``). The sum is exact, where ``density()``
  bins and uses an FFT. Validated in ``violin_density``.
- :func:`histogram_bins`: bin edges and counts. Auto is ``hist()``'s default
  (:func:`pretty` with Sturges' number of classes); a number of bins splits the range
  into exactly that many; a width from a start counts the values from the start on.
  Bins hold their right edge, and the first also its left one. Validated in
  ``histogram_bins``.

Pure NumPy: the scheduler runs these with the rest of :func:`curvis.charts.data.compute`.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from curvis.charts.spec import BinMode

type FloatArray = npt.NDArray[np.float64]

GRID_POINTS = 101
"""Points on which a violin's outline is computed."""
CUT = 3.0
"""Bandwidths an untrimmed violin reaches past the smallest and largest value."""
_SQRT_2PI = math.sqrt(2.0 * math.pi)
_KDE_CHUNK = 1 << 20
"""Largest values × grid points summed at once (memory of the KDE)."""


def r_seq(start: float, stop: float, n: int) -> FloatArray:
    """``seq.int(start, stop, length.out = n)`` as R computes it: ``start + i * by``,
    with the last value exactly ``stop``."""
    out = np.empty(n, dtype=np.float64)
    if n == 0:
        return out
    out[0] = start
    if n > 1:
        by = (stop - start) / (n - 1)
        out[1:-1] = start + np.arange(1, n - 1) * by
        out[-1] = stop
    return out


def r_sd(x: FloatArray) -> float:
    """R's ``sd``: the mean with R's second pass (so equal values have SD 0 exactly),
    then the sum of squared deviations over n − 1."""
    n = len(x)
    mean = math.fsum(x) / n
    mean += math.fsum(x - mean) / n
    return math.sqrt(math.fsum((x - mean) ** 2) / (n - 1))


# --- violins -----------------------------------------------------------------------------


def bw_nrd0(x: FloatArray) -> float:
    """R's ``bw.nrd0``: 0.9 min(SD, IQR / 1.34) n^(-1/5), falling back to the SD, then
    ``|x[0]|``, then 1 when the smaller one is 0. Needs two values or more."""
    n = len(x)
    if n < 2:
        raise ValueError("need at least 2 data points")
    hi = r_sd(x)
    q1, q3 = np.quantile(x, [0.25, 0.75])  # R's type 7
    lo = min(hi, float(q3 - q1) / 1.34)
    if not lo:
        lo = hi or abs(float(x[0])) or 1.0
    return 0.9 * lo * math.pow(n, -0.2)


def kde(x: FloatArray, bw: float, grid: FloatArray) -> FloatArray:
    """The Gaussian kernel density estimate (1 / n) Σ φ((g − xᵢ) / bw) / bw at ``grid``."""
    out = np.zeros(len(grid), dtype=np.float64)
    step = max(1, _KDE_CHUNK // max(1, len(grid)))
    for start in range(0, len(x), step):
        z = (grid[:, None] - x[None, start : start + step]) / bw
        out += np.exp(-0.5 * z * z).sum(axis=1)
    return out / (len(x) * bw * _SQRT_2PI)


@dataclass(frozen=True)
class ViolinDensity:
    n: int
    bw: float
    """Bandwidth (NaN for fewer than 2 values: no outline)."""
    grid: FloatArray
    """Values at which the density is computed (empty without an outline)."""
    density: FloatArray


def violin_density(
    values: npt.ArrayLike, *, trim: bool = True, points: int = GRID_POINTS
) -> ViolinDensity:
    """A violin's outline: the density of ``values`` (NaN left out) from the smallest to
    the largest value (``trim``), or ``CUT`` bandwidths past them."""
    x = np.asarray(values, dtype=np.float64).ravel()
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 2:
        empty = np.empty(0, dtype=np.float64)
        return ViolinDensity(n, math.nan, empty, empty)
    bw = bw_nrd0(x)
    low, high = float(x.min()), float(x.max())
    if not trim:
        low, high = low - CUT * bw, high + CUT * bw
    grid = np.full(points, low) if low == high else r_seq(low, high, points)
    return ViolinDensity(n, bw, grid, kde(x, bw, grid))


# --- histograms --------------------------------------------------------------------------


_ROUNDING_EPS = 1e-10
_MAX_F = 1.25


def pretty(
    low: float,
    high: float,
    n: int = 5,
    min_n: int | None = None,
    shrink_sml: float = 0.75,
    high_u_bias: float = 1.5,
    u5_bias: float | None = None,
    f_min: float = 2.0**-20,
) -> FloatArray:
    """R's ``pretty.default(c(low, high), n, min.n)``: about ``n`` + 1 equally spaced
    round values covering the range (``R_pretty`` in R's src/appl/pretty.c, with
    ``eps.correct = 0`` and ``bounds = TRUE``)."""
    if min_n is None:
        min_n = n // 3
    h = high_u_bias
    h5 = 0.5 + 1.5 * h if u5_bias is None else u5_bias
    eps, dbl_min, dbl_max = sys.float_info.epsilon, sys.float_info.min, sys.float_info.max
    lo, up = float(low), float(high)
    ndiv = n
    dx = up - lo
    if dx == 0 and up == 0:
        cell, i_small = 1.0, True
    else:
        cell = max(abs(lo), abs(up))
        u = 1 + (1 / (1 + h) if h5 >= 1.5 * h + 0.5 else 1.5 / (1 + h5))
        u *= max(1, ndiv) * eps
        i_small = dx < cell * u * 3
    if i_small:
        if cell > 10:
            cell = 9 + cell / 10
        cell *= shrink_sml
        if min_n > 1:
            cell /= min_n
    else:
        cell = dx
        if ndiv > 1:
            cell /= ndiv
    subsmall = f_min * dbl_min or dbl_min
    if cell < subsmall:
        cell = subsmall
    elif cell > dbl_max / _MAX_F:
        cell = dbl_max / _MAX_F
    base = 10.0 ** math.floor(math.log10(cell))
    unit = base
    if (ns := 2 * base) - cell < h * (cell - unit):
        unit = ns
        if (ns := 5 * base) - cell < h5 * (cell - unit):
            unit = ns
            if (ns := 10 * base) - cell < h * (cell - unit):
                unit = ns
    nsf = math.floor(lo / unit + _ROUNDING_EPS)
    nuf = math.ceil(up / unit - _ROUNDING_EPS)
    while nsf * unit > lo + _ROUNDING_EPS * unit:
        nsf -= 1
    while not math.isfinite(nsf * unit):
        nsf += 1
    while nuf * unit < up - _ROUNDING_EPS * unit:
        nuf += 1
    while not math.isfinite(nuf * unit):
        nuf -= 1
    k = int(0.5 + nuf - nsf)
    if k < min_n:
        k = min_n - k
        if nsf >= 0:
            nuf += k // 2
            nsf -= k // 2 + k % 2
        else:
            nsf -= k // 2
            nuf += k // 2 + k % 2
        ndiv = min_n
    else:
        ndiv = k
    # bounds = TRUE: the ends move out only where that covers more of the range.
    if nsf * unit < lo:
        lo = nsf * unit
    if nuf * unit > up:
        up = nuf * unit
    s = r_seq(lo, up, ndiv + 1)
    if ndiv:
        delta = (up - lo) / ndiv
        s[np.abs(s) < 1e-14 * delta] = 0.0
    return s


def sturges(n: int) -> int:
    """R's ``nclass.Sturges``: ⌈log₂ n + 1⌉ classes."""
    return math.ceil(math.log2(n) + 1)


@dataclass(frozen=True)
class Histogram:
    breaks: FloatArray
    """Bin edges, increasing; empty when there are no values."""
    counts: tuple[npt.NDArray[np.int64], ...]
    """Per group, the number of values in each bin."""
    n_outside: tuple[int, ...]
    """Per group, the values left out (below the start of width bins)."""


def histogram_breaks(
    pooled: FloatArray, mode: BinMode, count: int = 10, width: float = 1.0, start: float = 0.0
) -> FloatArray:
    """The bin edges for ``pooled`` (finite values, at least one)."""
    low, high = float(pooled.min()), float(pooled.max())
    if mode is BinMode.COUNT and low != high:
        return r_seq(low, high, count + 1)
    if mode is BinMode.WIDTH:
        m = max(1, math.ceil((high - start) / width))
        if start + m * width < high:
            m += 1
        return start + width * np.arange(m + 1, dtype=np.float64)
    return pretty(low, high, n=sturges(len(pooled)), min_n=1)


def bin_counts(values: FloatArray, breaks: FloatArray) -> tuple[npt.NDArray[np.int64], int]:
    """Values per bin (a, b], the first [a, b], and how many lie outside the bins."""
    x = values[~np.isnan(values)]
    index = np.searchsorted(breaks, x, side="left")
    index[x == breaks[0]] = 1
    inside = (index >= 1) & (index < len(breaks))
    counts = np.bincount(index[inside] - 1, minlength=len(breaks) - 1).astype(np.int64)
    return counts, int((~inside).sum())


def histogram_bins(
    groups: list[FloatArray],
    mode: BinMode = BinMode.AUTO,
    count: int = 10,
    width: float = 1.0,
    start: float = 0.0,
) -> Histogram:
    """Shared bins for ``groups`` (from their values pooled, NaN left out) and each
    group's counts."""
    arrays = [np.asarray(g, dtype=np.float64).ravel() for g in groups]
    pooled = np.concatenate(arrays) if arrays else np.empty(0)
    pooled = pooled[np.isfinite(pooled)]
    if len(pooled) == 0:
        empty = np.empty(0, dtype=np.float64)
        return Histogram(
            empty, tuple(np.empty(0, np.int64) for _ in arrays), tuple(0 for _ in arrays)
        )
    breaks = histogram_breaks(pooled, mode, count, width, start)
    found = [bin_counts(a[np.isfinite(a) | np.isnan(a)], breaks) for a in arrays]
    return Histogram(breaks, tuple(c for c, _ in found), tuple(o for _, o in found))


__all__ = (
    "CUT",
    "GRID_POINTS",
    "BinMode",
    "Histogram",
    "ViolinDensity",
    "bin_counts",
    "bw_nrd0",
    "histogram_bins",
    "histogram_breaks",
    "kde",
    "pretty",
    "r_seq",
    "sturges",
    "violin_density",
)
