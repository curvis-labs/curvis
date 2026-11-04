"""Dose-response curves: the Hill four-parameter logistic (4PL) and the 3PL with the
bottom fixed at 0.

On X' = log10(concentration):

    Y = Bottom + (Top - Bottom) / (1 + 10^((logEC50 - X') * HillSlope))

A positive Hill slope rises with the concentration, a negative one falls. A 4PL curve
can also be written with Top and Bottom swapped and the slope negated; Curvis reports
the form with Top >= Bottom. Concentration 0 (X' = -inf) is the curve's limit: Bottom
for a rising curve, Top for a falling one. Bounds on Bottom or Top keep the form they
were given in (no swap); note that the swapped form of a curve may meet such a bound.
Validated against drc and minpack.lm
(``validation/r/scripts/dose_response.R``).

Standard errors are asymptotic, s² (JᵀJ)⁻¹, as in Prism, R's ``nls`` and minpack.lm,
which the fixtures check them against. drc's ``vcov()`` inverts the numerical Hessian
of the RSS instead (it includes the residuals' second-order term), so its SEs differ by
a few percent (median 3.9%, at most 14% on the fixtures). That is a different method,
not an error to correct.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from curvis.analysis.fit.engine import (
    Constraints,
    FitModel,
    FitStatus,
    NonlinearFit,
    fit_model,
)
from curvis.analysis.fit.points import Points, XRange, fit_points

type FloatArray = npt.NDArray[np.float64]

LN10 = math.log(10.0)
PARAMETERS = ("bottom", "top", "log_ec50", "hill_slope")
MAX_NFEV = 500
"""Model evaluations per attempt. With the analytic Jacobian a fit that converges needs
far fewer (< 100 on every fixture); the limit keeps a fit that cannot converge (and is
retried from 12 other starts) from taking seconds."""


class Logistic(FitModel):
    """The 4PL on log10 X. The 3PL is this model with Bottom fixed at 0."""

    name = "Hill four-parameter logistic"
    parameters = PARAMETERS

    def f(self, x: FloatArray, p: FloatArray) -> FloatArray:
        b, t, lec, h = (float(v) for v in p)
        with np.errstate(over="ignore", invalid="ignore"):
            u = np.power(10.0, (lec - x) * h)
            y = b + (t - b) / (1.0 + u)
        limit = np.isneginf(x)
        if np.any(limit):
            y = np.where(limit, b if h > 0 else t if h < 0 else (b + t) / 2, y)
        return np.asarray(y, dtype=np.float64)

    def jacobian(self, x: FloatArray, p: FloatArray) -> FloatArray:
        b, t, lec, h = (float(v) for v in p)
        with np.errstate(over="ignore", invalid="ignore"):
            u = np.power(10.0, (lec - x) * h)
            g = 1.0 / (1.0 + u)
            # u / (1 + u)² = g (1 - g); stays finite when u overflows.
            w = g * (1.0 - g) * LN10
            out = np.column_stack([1.0 - g, g, -(t - b) * w * h, -(t - b) * w * (lec - x)])
        limit = np.isneginf(x)
        if np.any(limit):
            out[limit] = [
                1.0 if h > 0 else 0.5 if h == 0 else 0.0,
                1.0 if h < 0 else 0.5 if h == 0 else 0.0,
                0.0,
                0.0,
            ]
        bad = ~np.isfinite(out)  # (lec - x) * w with w underflowed to 0: the limit is 0
        out[bad] = 0.0
        return out

    def guess(self, x: FloatArray, y: FloatArray) -> FloatArray:
        levels, inverse = np.unique(x, return_inverse=True)
        means = np.bincount(inverse, weights=y) / np.bincount(inverse)
        low_end, high_end = float(means[0]), float(means[-1])
        rising = high_end >= low_end
        bottom, top = float(np.min(means)), float(np.max(means))
        finite = np.isfinite(levels)
        xs, ms = levels[finite], means[finite]
        log_ec50 = float(np.median(xs)) if len(xs) else 0.0
        if len(xs) >= 2:
            mid = (bottom + top) / 2
            for i in range(len(xs) - 1):
                a, c = ms[i] - mid, ms[i + 1] - mid
                if a == 0:
                    log_ec50 = float(xs[i])
                    break
                if a * c < 0:
                    log_ec50 = float(xs[i] + (xs[i + 1] - xs[i]) * a / (a - c))
                    break
        return np.array([bottom, top, log_ec50, 1.0 if rising else -1.0])

    def canonical(self, p: FloatArray, free: tuple[bool, ...]) -> FloatArray:
        if free[0] and free[1] and p[1] < p[0]:
            return np.array([p[1], p[0], p[2], -p[3]])
        return p


LOGISTIC = Logistic()


@dataclass(frozen=True)
class DoseResponseFit:
    """A 4PL or 3PL fit with EC50 (from logEC50) and its CI."""

    fit: NonlinearFit
    three_parameter: bool
    x_is_log: bool

    @property
    def ec50(self) -> float:
        return float(10.0 ** self.fit.value("log_ec50"))

    @property
    def ec50_ci(self) -> tuple[float, float]:
        i = PARAMETERS.index("log_ec50")
        return 10.0 ** float(self.fit.ci_lower[i]), 10.0 ** float(self.fit.ci_upper[i])

    def to_log_x(self, x: npt.ArrayLike) -> FloatArray:
        """X values (in the units of the X column) on the model's log10 scale."""
        return log_x(x, self.x_is_log)

    def band(self, at: npt.ArrayLike) -> tuple[FloatArray, FloatArray, FloatArray]:
        """Curve and confidence band at ``at`` (in the units of the X column)."""
        return self.fit.band(self.to_log_x(at))


def log_x(x: npt.ArrayLike, x_is_log: bool) -> FloatArray:
    """log10 of concentrations (0 gives -inf, a negative value NaN), or ``x`` itself."""
    values = np.asarray(x, dtype=np.float64)
    if x_is_log:
        return values
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.log10(values)


def dose_response_points(
    x: npt.ArrayLike,
    columns: list[npt.ArrayLike] | tuple[npt.ArrayLike, ...],
    x_is_log: bool,
    x_range: XRange | None = None,
) -> Points:
    """The points on the log10 scale; negative concentrations are left out."""
    raw = fit_points(x, list(columns), x_range)
    if x_is_log:
        return raw
    keep = raw.x >= 0
    return Points(log_x(raw.x[keep], False), raw.y[keep])


def fit_dose_response(
    points: Points,
    *,
    three_parameter: bool = False,
    x_is_log: bool = False,
    fixed: dict[str, float] | None = None,
    bounds: dict[str, tuple[float, float]] | None = None,
    confidence: float = 0.95,
) -> DoseResponseFit:
    """Fit the 4PL (or the 3PL, Bottom = 0) to ``points`` (X on the log10 scale)."""
    fixed = dict(fixed or {})
    if three_parameter:
        fixed["bottom"] = 0.0
    constraints = Constraints(fixed, dict(bounds or {}))
    fit = fit_model(LOGISTIC, points, constraints, confidence=confidence, max_nfev=MAX_NFEV)
    if not fit.ok and fit.status is not FitStatus.TOO_FEW_POINTS:
        fit = _retry(points, constraints, confidence, fit)
    return DoseResponseFit(fit, three_parameter, x_is_log)


def _retry(
    points: Points, constraints: Constraints, confidence: float, first: NonlinearFit
) -> NonlinearFit:
    """Other starting values when the first fit failed: slopes of either sign and size,
    the EC50 at the quartiles of X. A fit from these replaces the first only if it is OK
    and, when the first converged (to an ambiguous fit), has a lower RSS."""
    finite = points.x[np.isfinite(points.x)]
    if len(finite) == 0:
        return first
    base = LOGISTIC.guess(points.x, points.y)
    best = first
    for q in (0.25, 0.5, 0.75):
        for h in (0.5, 2.0, -0.5, -2.0):
            start = base.copy()
            start[2] = float(np.quantile(finite, q))
            start[3] = h
            fit = fit_model(LOGISTIC, points, constraints, start, confidence, MAX_NFEV)
            if fit.ok and (not best.converged or fit.rss < best.rss):
                best = fit
    return best
