"""Dose-response curves with two transitions: biphasic and bell-shaped.

On X' = log10(concentration) (docs/models.md):

    biphasic:  Y = Bottom + (Top - Bottom) * [      Frac  / (1 + 10^((LogEC50_1 - X') nH1))
                                              + (1 - Frac) / (1 + 10^((LogEC50_2 - X') nH2)) ]
    bell:      Y = Dip + (Plateau1 - Dip) / (1 + 10^((X' - LogEC50_1) nH1))
                       + (Plateau2 - Dip) / (1 + 10^((LogEC50_2 - X') nH2))

Biphasic fits are reported with Top >= Bottom and LogEC50_1 <= LogEC50_2 (see
:meth:`Biphasic.canonical`). The bell-shaped curve also reports its peak: center
(LogEC50_1 + LogEC50_2) / 2, width LogEC50_2 - LogEC50_1 and amplitude
Dip - (Plateau1 + Plateau2) / 2. Concentration 0 (X' = -inf) takes the curve's limit.
Validated against minpack.lm (``validation/r/scripts/biphasic.R``, ``bell_shaped.R``).

Automatic initial values: with both LogEC50s and slopes fixed, either curve is linear
in its three levels, so pairs LogEC50_1 < LogEC50_2 at nine quantiles of the X values
(slopes 1) are searched with the levels solved exactly.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt

from curvis.analysis.fit.engine import FitModel
from curvis.analysis.fit.nonlinear import CurveSpec, Derived, best_linear, finite_levels, ranked

type FloatArray = npt.NDArray[np.float64]

LN10 = math.log(10.0)
GRID_QUANTILES = np.linspace(0.05, 0.95, 9)

BOTTOM, TOP, FRAC, L1, L2, H1, H2 = range(7)


def _g(z: FloatArray) -> FloatArray:
    """1 / (1 + 10^z) with its limits at z = -/+inf (and 1/2 where z is 0 * inf). Call
    inside ``np.errstate(all="ignore")`` (10^z may overflow)."""
    z = np.where(np.isnan(z), 0.0, z)
    return np.asarray(1.0 / (1.0 + np.power(10.0, z)), dtype=np.float64)


def _dg(g: FloatArray) -> FloatArray:
    """d g / d z in terms of g (finite when 10^z overflows)."""
    return np.asarray(-g * (1.0 - g) * LN10, dtype=np.float64)


def _finite(j: FloatArray) -> FloatArray:
    """0 where a Jacobian term is (X' - L) times a weight that underflowed to 0 (at
    X' = -inf): the limit is 0."""
    j[~np.isfinite(j)] = 0.0
    return j


def _pairs(x: FloatArray) -> tuple[FloatArray, FloatArray]:
    levels = finite_levels(x)
    if len(levels) == 0:
        return np.array([-1.0]), np.array([1.0])
    at = np.unique(np.quantile(levels, GRID_QUANTILES))
    if len(at) < 2:
        at = np.array([at[0] - 1.0, at[0] + 1.0])
    i, j = np.triu_indices(len(at), k=1)
    return at[i], at[j]


class Biphasic(FitModel):
    name = "Biphasic dose-response"
    parameters = ("bottom", "top", "frac", "log_ec50_1", "log_ec50_2", "hill_slope_1",
                  "hill_slope_2")  # fmt: skip

    def f(self, x: FloatArray, p: FloatArray) -> FloatArray:
        b, t, fr, l1, l2, h1, h2 = (float(v) for v in p)
        with np.errstate(all="ignore"):
            g1, g2 = _g((l1 - x) * h1), _g((l2 - x) * h2)
            return np.asarray(b + (t - b) * (fr * g1 + (1.0 - fr) * g2), dtype=np.float64)

    def jacobian(self, x: FloatArray, p: FloatArray) -> FloatArray:
        b, t, fr, l1, l2, h1, h2 = (float(v) for v in p)
        span = t - b
        with np.errstate(all="ignore"):
            g1, g2 = _g((l1 - x) * h1), _g((l2 - x) * h2)
            d1, d2 = _dg(g1), _dg(g2)
            s = fr * g1 + (1.0 - fr) * g2
            out = np.column_stack([
                1.0 - s, s, span * (g1 - g2),
                span * fr * d1 * h1, span * (1.0 - fr) * d2 * h2,
                span * fr * d1 * (l1 - x), span * (1.0 - fr) * d2 * (l2 - x),
            ])  # fmt: skip
        return _finite(out)

    def guess(self, x: FloatArray, y: FloatArray) -> FloatArray:
        return self.starts(x, y)[0]

    def starts(self, x: FloatArray, y: FloatArray) -> list[FloatArray]:
        l1, l2 = _pairs(x)
        with np.errstate(all="ignore"):
            g1 = _g(l1[:, None] - x[None, :])
            g2 = _g(l2[:, None] - x[None, :])
        coef, rss = best_linear(np.stack([np.ones_like(g1), g1, g2], axis=2), y)
        bottom, s1, s2 = coef[:, 0], coef[:, 1], coef[:, 2]
        span = s1 + s2
        with np.errstate(divide="ignore", invalid="ignore"):
            frac = np.where(np.abs(span) > 0, s1 / span, 0.5)
        ones = np.ones_like(l1)
        candidates = np.column_stack([bottom, bottom + span, frac, l1, l2, ones, ones])
        return ranked(candidates, rss) or [np.array([0.0, 1.0, 0.5, -1.0, 1.0, 1.0, 1.0])]

    def canonical(self, p: FloatArray, free: tuple[bool, ...]) -> FloatArray:
        """Top >= Bottom (swap them and negate both slopes), then LogEC50_1 <= LogEC50_2
        (swap the phases, Frac -> 1 - Frac); each only with the parameters it changes
        free (both slopes free, or both fixed to the same value, for the second)."""
        q = p.copy()
        if free[BOTTOM] and free[TOP] and free[H1] and free[H2] and q[TOP] < q[BOTTOM]:
            q[[BOTTOM, TOP]] = q[[TOP, BOTTOM]]
            q[[H1, H2]] = -q[[H1, H2]]
        slopes_ok = (free[H1] and free[H2]) or (not free[H1] and not free[H2] and q[H1] == q[H2])
        if free[FRAC] and free[L1] and free[L2] and slopes_ok and q[L1] > q[L2]:
            q[[L1, L2]] = q[[L2, L1]]
            q[[H1, H2]] = q[[H2, H1]]
            q[FRAC] = 1.0 - q[FRAC]
        return q


class BellShaped(FitModel):
    name = "Bell-shaped dose-response"
    parameters = ("plateau_1", "dip", "plateau_2", "log_ec50_1", "log_ec50_2", "hill_slope_1",
                  "hill_slope_2")  # fmt: skip

    def f(self, x: FloatArray, p: FloatArray) -> FloatArray:
        p1, d, p2, l1, l2, h1, h2 = (float(v) for v in p)
        with np.errstate(all="ignore"):
            g1, g2 = _g((x - l1) * h1), _g((l2 - x) * h2)
            return np.asarray(d + (p1 - d) * g1 + (p2 - d) * g2, dtype=np.float64)

    def jacobian(self, x: FloatArray, p: FloatArray) -> FloatArray:
        p1, d, p2, l1, l2, h1, h2 = (float(v) for v in p)
        s1, s2 = p1 - d, p2 - d
        with np.errstate(all="ignore"):
            g1, g2 = _g((x - l1) * h1), _g((l2 - x) * h2)
            d1, d2 = _dg(g1), _dg(g2)
            out = np.column_stack([
                g1, 1.0 - g1 - g2, g2,
                -s1 * d1 * h1, s2 * d2 * h2,
                s1 * d1 * (x - l1), s2 * d2 * (l2 - x),
            ])  # fmt: skip
        return _finite(out)

    def guess(self, x: FloatArray, y: FloatArray) -> FloatArray:
        return self.starts(x, y)[0]

    def starts(self, x: FloatArray, y: FloatArray) -> list[FloatArray]:
        l1, l2 = _pairs(x)
        with np.errstate(all="ignore"):
            g1 = _g(x[None, :] - l1[:, None])
            g2 = _g(l2[:, None] - x[None, :])
        coef, rss = best_linear(np.stack([np.ones_like(g1), g1, g2], axis=2), y)
        dip = coef[:, 0]
        ones = np.ones_like(l1)
        candidates = np.column_stack([dip + coef[:, 1], dip, dip + coef[:, 2], l1, l2, ones, ones])
        return ranked(candidates, rss) or [np.array([0.0, 1.0, 0.0, -1.0, 1.0, 1.0, 1.0])]


def _ten_to(v: float) -> float:
    return float(10.0**v)


SLOPE_ALIASES = {"nh1": "hill_slope_1", "nh2": "hill_slope_2", "slope1": "hill_slope_1",
                 "slope2": "hill_slope_2"}  # fmt: skip

BIPHASIC = CurveSpec(
    key="biphasic",
    model=Biphasic(),
    labels={
        "bottom": "Bottom",
        "top": "Top",
        "frac": "Frac",
        "log_ec50_1": "LogEC50 1",
        "log_ec50_2": "LogEC50 2",
        "hill_slope_1": "Hill slope 1",
        "hill_slope_2": "Hill slope 2",
    },
    derived=(
        Derived("ec50_1", "EC50 1", of="log_ec50_1", transform=_ten_to),
        Derived("ec50_2", "EC50 2", of="log_ec50_2", transform=_ten_to),
    ),
    log_x=True,
    aliases={"fraction": "frac", **SLOPE_ALIASES},
)

BELL_SHAPED = CurveSpec(
    key="bell_shaped",
    model=BellShaped(),
    labels={
        "plateau_1": "Plateau 1",
        "dip": "Dip",
        "plateau_2": "Plateau 2",
        "log_ec50_1": "LogEC50 1",
        "log_ec50_2": "LogEC50 2",
        "hill_slope_1": "Hill slope 1",
        "hill_slope_2": "Hill slope 2",
    },
    derived=(
        Derived("ec50_1", "EC50 1", of="log_ec50_1", transform=_ten_to),
        Derived("ec50_2", "EC50 2", of="log_ec50_2", transform=_ten_to),
        Derived("center", "Peak center", linear={"log_ec50_1": 0.5, "log_ec50_2": 0.5}),
        Derived("width", "Peak width", linear={"log_ec50_1": -1.0, "log_ec50_2": 1.0}),
        Derived(
            "amplitude", "Peak amplitude", linear={"dip": 1.0, "plateau_1": -0.5, "plateau_2": -0.5}
        ),
    ),
    log_x=True,
    aliases=SLOPE_ALIASES,
)
