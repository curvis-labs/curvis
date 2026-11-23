"""Shared parts of the nonlinear curve fits of step 3.3 (docs/models.md): derived values
with their confidence intervals, the description of a curve (:class:`CurveSpec`), the
fit from several automatic starts, and the batched linear least squares the automatic
initial values are built on.

Derived values (as ``validation/r/R/nonlinear.R``):

- A value linear in the parameters, cᵀp: SE = sqrt(cᵀ V c) with V the covariance of the
  free parameters, CI = value -/+ t(0.975, df) SE.
- A monotone function of one parameter: the same function of the parameter's CI
  limits, sorted. For a reciprocal of a rate constant K (half-life, tau): defined for
  K > 0 only, and when the lower CI limit of K is <= 0 the upper limit is infinite.

A derived value that uses a fixed parameter is not reported.

Automatic initial values: every model here is linear in some of its parameters once
the others are fixed (plateaus, amplitudes, Vmax). The models search a grid of the
nonlinear parameters, solve the linear ones exactly at each grid point
(:func:`best_linear`), and rank the grid points by their residual sum of squares. The
best one starts the fit; when that fit fails, the next ones are tried.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from curvis.analysis.fit.engine import Constraints, FitModel, NonlinearFit, fit_model
from curvis.analysis.fit.points import Points, XRange, fit_points

type FloatArray = npt.NDArray[np.float64]

TRIES = 5
"""Starts tried at most (the best grid point, then the next ones) when a fit fails."""


@dataclass(frozen=True)
class Derived:
    """A value computed from the fitted parameters. Set ``linear`` (coefficients by
    parameter name) for a linear combination, or ``of`` and ``transform`` for a
    monotone function of one parameter (``reciprocal`` for functions of 1/K)."""

    name: str
    label: str
    linear: Mapping[str, float] | None = None
    of: str = ""
    transform: Callable[[float], float] | None = None
    reciprocal: bool = False


@dataclass(frozen=True)
class DerivedValue:
    value: float
    se: float
    """NaN for a function of one parameter (it has a CI, not an SE)."""
    ci_lower: float
    ci_upper: float


def uses_fixed(d: Derived, fixed: Mapping[str, float]) -> bool:
    """True when ``d`` uses a parameter in ``fixed`` (it is then not reported)."""
    if d.linear is not None:
        return any(name in fixed for name, c in d.linear.items() if c != 0)
    return d.of in fixed


def derived_value(fit: NonlinearFit, d: Derived) -> DerivedValue | None:
    """``d`` for a converged ``fit``, or None when it uses a fixed parameter."""
    names = fit.names
    nan = math.nan
    if d.linear is not None:
        c = np.zeros(len(names))
        for name, coefficient in d.linear.items():
            c[names.index(name)] = coefficient
        free = np.array(fit.free)
        if np.any(c[~free] != 0):
            return None
        value = float(c @ fit.values)
        if fit.df <= 0 or not np.all(np.isfinite(fit.cov)):
            return DerivedValue(value, nan, nan, nan)
        cf = c[free]
        se = math.sqrt(max(float(cf @ fit.cov @ cf), 0.0))
        from scipy import stats  # lazy: the sketches use the models

        q = float(stats.t.ppf(0.5 + fit.confidence / 2, fit.df))
        return DerivedValue(value, se, value - q * se, value + q * se)
    assert d.transform is not None
    i = names.index(d.of)
    if not fit.free[i]:
        return None
    p, low, high = float(fit.values[i]), float(fit.ci_lower[i]), float(fit.ci_upper[i])
    f = d.transform
    if d.reciprocal and p <= 0:
        return DerivedValue(nan, nan, nan, nan)
    with np.errstate(over="ignore"):
        value = f(p)
        if math.isnan(low):
            return DerivedValue(value, nan, nan, nan)
        if d.reciprocal and low <= 0:
            return DerivedValue(value, nan, f(high), math.inf)
        a, b = sorted((f(low), f(high)))
    return DerivedValue(value, nan, a, b)


@dataclass(frozen=True)
class CurveSpec:
    """A curve-fit model as the analyses and the validation adapters use it."""

    key: str
    """Analysis id and fixture directory."""
    model: FitModel
    labels: Mapping[str, str]
    """Display name of each parameter."""
    derived: tuple[Derived, ...] = ()
    log_x: bool = False
    """Written on X' = log10(concentration) unless the X values are logs already."""
    aliases: Mapping[str, str] = field(default_factory=dict)
    """More names a user may write to fix a parameter (normalised, see :func:`name_key`)."""
    max_nfev: int = 500
    """Model evaluations per start (with the analytic Jacobian a fit that converges needs
    far fewer); keeps a fit that cannot converge from taking long."""

    def names(self) -> dict[str, str]:
        """Every accepted name (normalised) of each parameter."""
        found = {name_key(p): p for p in self.model.parameters}
        found.update({name_key(label): p for p, label in self.labels.items()})
        found.update(self.aliases)
        return found


def name_key(text: str) -> str:
    """``text`` lower-cased with everything but letters and digits removed, so that
    "Hill slope 1", "hill_slope_1" and "HillSlope1" are the same name."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def curve_points(
    spec: CurveSpec,
    x: npt.ArrayLike,
    columns: Sequence[npt.ArrayLike],
    x_is_log: bool,
    x_range: XRange | None = None,
) -> Points:
    """The points ``spec`` is fitted to (on the log10 scale for a dose-response model)."""
    if spec.log_x:
        from curvis.analysis.fit.dose_response import dose_response_points

        return dose_response_points(x, list(columns), x_is_log, x_range)
    return fit_points(x, list(columns), x_range)


def fit_curve(
    spec: CurveSpec,
    points: Points,
    fixed: Mapping[str, float] | None = None,
    confidence: float = 0.95,
) -> NonlinearFit:
    """Fit ``spec`` from its automatic starts: the best one first, and when that fit is
    not OK, up to :data:`TRIES` in all. The result is the OK fit with the lowest RSS, or
    else the converged one with the lowest RSS, or else the first."""
    constraints = Constraints(dict(fixed or {}))
    model = spec.model
    k = len(model.parameters) - len(constraints.fixed)
    if points.n < max(k, 1):
        return fit_model(model, points, constraints, confidence=confidence, labels=spec.labels)
    starts = model.starts(points.x, points.y)
    best: NonlinearFit | None = None
    for start in starts[:TRIES]:
        fit = fit_model(model, points, constraints, start, confidence, spec.max_nfev, spec.labels)
        if best is None or _better(fit, best):
            best = fit
        if fit.ok:
            break
    assert best is not None
    return best


def _better(fit: NonlinearFit, than: NonlinearFit) -> bool:
    if fit.ok != than.ok:
        return fit.ok
    if fit.converged != than.converged:
        return fit.converged
    return fit.converged and fit.rss < than.rss


def best_linear(basis: FloatArray, y: FloatArray) -> tuple[FloatArray, FloatArray]:
    """Least squares y ~ basis[g] @ c for every grid point g at once.

    ``basis`` has shape (G, n, m). Returns the coefficients (G, m) and the residual sums
    of squares (G,); a grid point whose basis is not finite gets RSS = inf."""
    ok = np.all(np.isfinite(basis), axis=(1, 2))
    b = np.where(ok[:, None, None], basis, 0.0)
    coef = np.einsum("gmn,n->gm", np.linalg.pinv(b), y)
    r = y[None, :] - np.einsum("gnm,gm->gn", b, coef)
    rss = np.einsum("gn,gn->g", r, r)
    rss = np.where(ok & np.isfinite(rss), rss, np.inf)
    return coef, rss


def ranked(candidates: FloatArray, rss: FloatArray, count: int = TRIES) -> list[FloatArray]:
    """The ``count`` candidate parameter vectors (rows) with the lowest RSS, best first;
    rows that are not finite are left out."""
    finite = np.all(np.isfinite(candidates), axis=1) & np.isfinite(rss)
    order = [int(i) for i in np.argsort(np.where(finite, rss, np.inf)) if finite[i]]
    return [candidates[i].copy() for i in order[:count]]


def finite_levels(x: FloatArray) -> FloatArray:
    """The distinct finite X values, sorted."""
    return np.unique(x[np.isfinite(x)])
