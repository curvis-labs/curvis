"""Linear regression and polynomials of degree 2-6, by least squares.

Y = b0 + b1 X + ... + bd X^d, solved exactly (no iterations) with a QR decomposition.
X is centred and scaled first (z = (X - c) / s) so that high powers stay well
conditioned; the coefficients and their covariance are then converted back to powers
of X. Validated against R's ``lm``, ``confint`` and ``predict(interval =
"confidence")`` (``validation/r/scripts/linear_fit.R``) and the NIST StRD linear
datasets (``nist_strd.R``).

Definitions (as in linear_fit.R):

- n points, k = d + 1 coefficients, df = n - k.
- SE from s² (VᵀV)⁻¹ with s² = RSS / df; CI = estimate -/+ t(0.975, df) SE.
- R² = 1 - RSS / TSS; Sy.x = sqrt(RSS / df).
- Band: fitted value -/+ t(0.975, df) SE of the fitted value.
- No fit with fewer than k points or fewer than k different X values.
- df = 0: the curve goes through every point; R² = 1; no SE, CI, Sy.x or band limits.
- Constant Y: the exact flat fit (b0 = Y, other coefficients 0, SE 0, Sy.x 0);
  R² is not defined.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from math import comb

import numpy as np
import numpy.typing as npt
from scipy import stats

from curvis.analysis.fit.points import Points

type FloatArray = npt.NDArray[np.float64]

MAX_DEGREE = 6


@dataclass(frozen=True)
class PolynomialFit:
    """A fitted polynomial. Without a fit (``ok`` is False) every number is NaN and
    ``problem`` says why."""

    degree: int
    n: int
    ok: bool
    problem: str
    coefficients: FloatArray
    """b0 (intercept), b1 (slope), b2, ..., in powers of X."""
    se: FloatArray
    ci_lower: FloatArray
    ci_upper: FloatArray
    df: int
    rss: float
    r_squared: float
    sy_x: float
    confidence: float = 0.95
    # The fit in the scaled basis z = (X - center) / scale, for predictions.
    _center: float = 0.0
    _scale: float = 1.0
    _coef_z: FloatArray = field(default_factory=lambda: np.empty(0))
    _rinv: FloatArray = field(default_factory=lambda: np.empty((0, 0)))
    """R⁻¹ of the QR decomposition of the scaled design matrix (VᵀV)⁻¹ = R⁻¹R⁻ᵀ."""
    _s2: float = math.nan

    @property
    def n_coefficients(self) -> int:
        return self.degree + 1

    def predict(self, at: npt.ArrayLike) -> FloatArray:
        """The fitted curve at ``at``."""
        x = np.asarray(at, dtype=np.float64)
        if not self.ok:
            return np.full(x.shape, np.nan)
        v = _vander((x - self._center) / self._scale, self.n_coefficients)
        return np.asarray(v @ self._coef_z, dtype=np.float64)

    def band(self, at: npt.ArrayLike) -> tuple[FloatArray, FloatArray, FloatArray]:
        """The fitted curve and the limits of its confidence band at ``at``."""
        x = np.asarray(at, dtype=np.float64)
        fit = self.predict(x)
        if not self.ok or self.df <= 0:
            nan = np.full(x.shape, np.nan)
            return fit, nan, nan.copy()
        v = _vander((x - self._center) / self._scale, self.n_coefficients)
        se_fit = np.sqrt(np.sum((v @ self._rinv) ** 2, axis=1) * self._s2)
        q = float(stats.t.ppf(0.5 + self.confidence / 2, self.df))
        return fit, fit - q * se_fit, fit + q * se_fit


def _vander(z: FloatArray, k: int) -> FloatArray:
    return np.vander(np.atleast_1d(z), k, increasing=True)


def _no_fit(degree: int, n: int, problem: str) -> PolynomialFit:
    nan = np.full(degree + 1, np.nan)
    return PolynomialFit(
        degree, n, False, problem, nan, nan.copy(), nan.copy(), nan.copy(), -1,
        math.nan, math.nan, math.nan,
    )  # fmt: skip


def fit_polynomial(points: Points, degree: int, confidence: float = 0.95) -> PolynomialFit:
    """Least-squares polynomial of ``degree`` (1 is a straight line) through ``points``."""
    if not 1 <= degree <= MAX_DEGREE:
        raise ValueError(f"degree must be 1 to {MAX_DEGREE}")
    x, y = points.x, points.y
    n = len(x)
    k = degree + 1
    what = "a line" if degree == 1 else f"a polynomial of degree {degree}"
    if n < k:
        return _no_fit(degree, n, f"Fitting {what} needs at least {k} points; there are {n}.")
    if len(np.unique(x)) < k:
        return _no_fit(degree, n, f"Fitting {what} needs at least {k} different X values.")

    center = float(np.mean(x))
    scale = float(np.max(np.abs(x - center)))
    v = _vander((x - center) / scale, k)
    q, r = np.linalg.qr(v)
    diag = np.abs(np.diag(r))
    if diag.min() <= 1e-12 * diag.max():
        return _no_fit(degree, n, f"The X values cannot determine {what}.")
    rinv = np.linalg.solve(r, np.eye(k))
    df = n - k
    constant = bool(np.all(y == y[0]))
    if constant:
        coef_z = np.zeros(k)
        coef_z[0] = y[0]
        rss = 0.0
    else:
        coef_z = rinv @ (q.T @ y)
        rss = float(np.sum((y - v @ coef_z) ** 2))

    # Coefficients of X^j from those of z^m: z^m = s^-m sum_j C(m, j) X^j (-c)^(m-j).
    t = np.zeros((k, k))
    for m in range(k):
        for j in range(m + 1):
            t[j, m] = comb(m, j) * (-center) ** (m - j) / scale**m
    coefficients = t @ coef_z
    if constant:
        coefficients = np.zeros(k)
        coefficients[0] = y[0]

    tss = float(np.sum((y - np.mean(y)) ** 2))
    if constant:
        r_squared = math.nan
    elif df == 0:
        r_squared = 1.0
    else:
        r_squared = 1.0 - rss / tss

    nan = np.full(k, np.nan)
    se, low, high = nan, nan.copy(), nan.copy()
    sy_x = s2 = math.nan
    if df > 0:
        s2 = rss / df
        sy_x = math.sqrt(s2)
        cov_z = (rinv @ rinv.T) * s2
        se = np.sqrt(np.diag(t @ cov_z @ t.T))
        qt = float(stats.t.ppf(0.5 + confidence / 2, df))
        low, high = coefficients - qt * se, coefficients + qt * se
    return PolynomialFit(
        degree=degree, n=n, ok=True, problem="", coefficients=coefficients, se=se,
        ci_lower=low, ci_upper=high, df=df, rss=rss, r_squared=r_squared, sy_x=sy_x,
        confidence=confidence, _center=center, _scale=scale, _coef_z=coef_z, _rinv=rinv,
        _s2=s2,
    )  # fmt: skip
