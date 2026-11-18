"""Shape-based fits: the Gaussian peak with a baseline.

    Y = Baseline + Amplitude * exp(-0.5 * ((X - Center) / SD)^2)

SD is reported positive (SD and -SD give the same curve); FWHM = 2 sqrt(2 ln 2) SD.
Every parameter is unbounded. Validated against minpack.lm
(``validation/r/scripts/gaussian.R``); equations in docs/models.md. The bell-shaped
dose-response is in :mod:`curvis.analysis.fit.multiphase`.

Automatic initial values: for a fixed Center and SD the curve is linear in Baseline and
Amplitude, so a grid of centers (at the X values) and widths (log-spaced from half the
smallest X step to the X span) is searched with those two solved exactly.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt

from curvis.analysis.fit.engine import FitModel
from curvis.analysis.fit.nonlinear import CurveSpec, Derived, best_linear, finite_levels, ranked

type FloatArray = npt.NDArray[np.float64]

FWHM_FACTOR = 2.0 * math.sqrt(2.0 * math.log(2.0))
MAX_CENTERS = 60
"""Grid centers at most (quantiles of the X values when there are more)."""
GRID_VALUES = 1_000_000
"""Basis values evaluated at once by the grid search."""


class Gaussian(FitModel):
    name = "Gaussian"
    parameters = ("baseline", "amplitude", "center", "sd")

    def f(self, x: FloatArray, p: FloatArray) -> FloatArray:
        b, a, c, s = (float(v) for v in p)
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            return np.asarray(b + a * np.exp(-0.5 * ((x - c) / s) ** 2), dtype=np.float64)

    def jacobian(self, x: FloatArray, p: FloatArray) -> FloatArray:
        _b, a, c, s = (float(v) for v in p)
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            u = (x - c) / s
            e = np.exp(-0.5 * u**2)
            return np.column_stack([np.ones_like(x), e, a * e * u / s, a * e * u**2 / s])

    def guess(self, x: FloatArray, y: FloatArray) -> FloatArray:
        return self.starts(x, y)[0]

    def starts(self, x: FloatArray, y: FloatArray) -> list[FloatArray]:
        levels = finite_levels(x)
        low, height = (float(np.min(y)), float(np.ptp(y))) if len(y) else (0.0, 1.0)
        fallback = np.array([low, height, float(np.mean(levels)) if len(levels) else 0.0, 1.0])
        if len(levels) < 2:
            return [fallback]
        width = float(levels[-1] - levels[0])
        step = float(np.min(np.diff(levels)))
        if len(levels) > MAX_CENTERS:
            levels = np.quantile(levels, np.linspace(0, 1, MAX_CENTERS))
        sds = np.logspace(math.log10(step / 2), math.log10(width), 25)
        centers, widths = (a.ravel() for a in np.meshgrid(levels, sds))
        candidates, scores = [], []
        chunk = max(1, GRID_VALUES // max(len(x), 1))  # bounded memory on big data
        for i in range(0, len(centers), chunk):
            c, w = centers[i : i + chunk], widths[i : i + chunk]
            e = np.exp(-0.5 * ((x[None, :] - c[:, None]) / w[:, None]) ** 2)
            coef, rss = best_linear(np.stack([np.ones_like(e), e], axis=2), y)
            candidates.append(np.column_stack([coef, c, w]))
            scores.append(rss)
        return ranked(np.vstack(candidates), np.concatenate(scores)) or [fallback]

    def canonical(self, p: FloatArray, free: tuple[bool, ...]) -> FloatArray:
        if free[3] and p[3] < 0:
            q = p.copy()
            q[3] = -p[3]
            return q
        return p


GAUSSIAN = CurveSpec(
    key="gaussian",
    model=Gaussian(),
    labels={"baseline": "Baseline", "amplitude": "Amplitude", "center": "Center", "sd": "SD"},
    derived=(Derived("fwhm", "FWHM", linear={"sd": FWHM_FACTOR}),),
    aliases={"centre": "center", "width": "sd", "height": "amplitude"},
)
