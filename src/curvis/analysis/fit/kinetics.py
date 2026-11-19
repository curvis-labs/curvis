"""Kinetics: one-phase exponential association and decay, and Michaelis-Menten.

Equations (docs/models.md):

    association:       Y = Y0 + (Plateau - Y0) * (1 - exp(-K * X))
    decay:             Y = Plateau + (Y0 - Plateau) * exp(-K * X)
    Michaelis-Menten:  Y = Vmax * X / (Km + X)

The two exponentials are the same family of curves, reported with Span = Plateau - Y0
(association) or Y0 - Plateau (decay), and half-time / half-life = ln 2 / K and
tau = 1 / K for K > 0. Every parameter is unbounded. Validated against minpack.lm,
stats::nls with SSasymp / SSmicmen (``validation/r/scripts/exp_*.R``,
``michaelis_menten.R``) and the NIST StRD datasets Misra1a, BoxBOD and Misra1d.

Automatic initial values: for a fixed K (or Km) the curve is linear in Y0 and Plateau
(or Vmax), so a log-spaced grid of K over the X span (or of Km over the X values) is
searched with the linear parameters solved exactly at each point.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt

from curvis.analysis.fit.engine import FitModel
from curvis.analysis.fit.nonlinear import CurveSpec, Derived, best_linear, ranked

type FloatArray = npt.NDArray[np.float64]

LN2 = math.log(2.0)


class _OnePhase(FitModel):
    """Parameters (Y0, Plateau, K); the Jacobian of both exponential forms."""

    parameters = ("y0", "plateau", "k")

    def jacobian(self, x: FloatArray, p: FloatArray) -> FloatArray:
        y0, plateau, k = (float(v) for v in p)
        with np.errstate(over="ignore", invalid="ignore"):
            e = np.exp(-k * x)
            return np.column_stack([e, 1.0 - e, -(y0 - plateau) * x * e])

    def guess(self, x: FloatArray, y: FloatArray) -> FloatArray:
        return self.starts(x, y)[0]

    def starts(self, x: FloatArray, y: FloatArray) -> list[FloatArray]:
        finite = x[np.isfinite(x)]
        x0 = float(np.min(finite)) if len(finite) else 0.0
        width = float(np.ptp(finite)) if len(finite) else 0.0
        fallback = np.array([float(y[0]) if len(y) else 0.0, float(np.mean(y)), 1.0])
        if width <= 0 or len(y) < 2:
            return [fallback]
        rates = np.logspace(-1.5, 2.0, 36) / width
        k = np.concatenate([rates, -rates[:12]])
        with np.errstate(over="ignore", invalid="ignore"):
            e = np.exp(-k[:, None] * (x[None, :] - x0))  # shifted to the first X
            basis = np.stack([np.ones_like(e), e], axis=2)
            coef, rss = best_linear(basis, y)
            plateau = coef[:, 0]
            y0 = plateau + coef[:, 1] * np.exp(k * x0)
        return ranked(np.column_stack([y0, plateau, k]), rss) or [fallback]


class ExpAssociation(_OnePhase):
    name = "One-phase exponential association"

    def f(self, x: FloatArray, p: FloatArray) -> FloatArray:
        y0, plateau, k = (float(v) for v in p)
        with np.errstate(over="ignore", invalid="ignore"):
            return np.asarray(y0 + (plateau - y0) * (1.0 - np.exp(-k * x)), dtype=np.float64)


class ExpDecay(_OnePhase):
    name = "One-phase exponential decay"

    def f(self, x: FloatArray, p: FloatArray) -> FloatArray:
        y0, plateau, k = (float(v) for v in p)
        with np.errstate(over="ignore", invalid="ignore"):
            return np.asarray(plateau + (y0 - plateau) * np.exp(-k * x), dtype=np.float64)


class MichaelisMenten(FitModel):
    name = "Michaelis-Menten"
    parameters = ("vmax", "km")

    def f(self, x: FloatArray, p: FloatArray) -> FloatArray:
        vmax, km = (float(v) for v in p)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.asarray(vmax * x / (km + x), dtype=np.float64)

    def jacobian(self, x: FloatArray, p: FloatArray) -> FloatArray:
        vmax, km = (float(v) for v in p)
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            d = km + x
            return np.column_stack([x / d, -vmax * x / d**2])

    def guess(self, x: FloatArray, y: FloatArray) -> FloatArray:
        return self.starts(x, y)[0]

    def starts(self, x: FloatArray, y: FloatArray) -> list[FloatArray]:
        top = float(np.max(np.abs(x))) if len(x) else 0.0
        fallback = np.array([float(np.max(y)) if len(y) else 1.0, top / 2 or 1.0])
        if top <= 0 or not math.isfinite(top):
            return [fallback]
        km = np.logspace(math.log10(top) - 4, math.log10(top) + 2, 49)
        with np.errstate(divide="ignore", invalid="ignore"):
            h = x[None, :] / (km[:, None] + x[None, :])
        coef, rss = best_linear(h[:, :, None], y)
        return ranked(np.column_stack([coef[:, 0], km]), rss) or [fallback]


def _ln2_over(k: float) -> float:
    return LN2 / k


def _one_over(k: float) -> float:
    return 1.0 / k


EXP_LABELS = {"y0": "Y0", "plateau": "Plateau", "k": "K"}

EXP_ASSOCIATION = CurveSpec(
    key="exp_association",
    model=ExpAssociation(),
    labels=EXP_LABELS,
    derived=(
        Derived("span", "Span", linear={"y0": -1.0, "plateau": 1.0}),
        Derived("half_time", "Half-time", of="k", transform=_ln2_over, reciprocal=True),
        Derived("tau", "Tau", of="k", transform=_one_over, reciprocal=True),
    ),
    aliases={"rateconstant": "k"},
)

EXP_DECAY = CurveSpec(
    key="exp_decay",
    model=ExpDecay(),
    labels=EXP_LABELS,
    derived=(
        Derived("span", "Span", linear={"y0": 1.0, "plateau": -1.0}),
        Derived("half_life", "Half-life", of="k", transform=_ln2_over, reciprocal=True),
        Derived("tau", "Tau", of="k", transform=_one_over, reciprocal=True),
    ),
    aliases={"rateconstant": "k"},
)

MICHAELIS_MENTEN = CurveSpec(
    key="michaelis_menten",
    model=MichaelisMenten(),
    labels={"vmax": "Vmax", "km": "Km"},
)
