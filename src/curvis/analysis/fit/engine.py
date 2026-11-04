"""The nonlinear curve-fit engine, on lmfit (Levenberg-Marquardt, MINPACK).

A :class:`FitModel` defines a curve: its parameters, its value and (optionally) its
analytic Jacobian, data-driven initial values and, where a curve can be written in two
ways, a canonical form. :func:`fit_model` fits it to points by unweighted least squares
with fixed parameters and bounds, and reports each free parameter with its asymptotic
standard error and confidence interval.

Definitions (as in ``validation/r/scripts/dose_response.R``):

- df = n - number of free parameters; s² = RSS / df.
- Covariance: s² (JᵀJ)⁻¹, with J the Jacobian of the model in the free parameters at
  the minimum (analytic if the model has one, else central differences).
- CI: estimate -/+ t(0.975, df) SE (for 95%). R² = 1 - RSS / TSS; Sy.x = sqrt(RSS / df).
- Confidence band: fitted value -/+ t(0.975, df) sqrt(g C gᵀ), g the gradient of the
  model in the free parameters (the delta method).

After lmfit stops, a few Gauss-Newton steps (each halved until it lowers the RSS)
finish the minimisation, so the parameters match R to well within 1e-6.

A fit is **ambiguous** when it converged but the data do not determine the parameters:
a parameter has no effect on the curve, JᵀJ is numerically singular, two parameters
are correlated with |r| > 0.9999, or a parameter ended on one of its bounds. Its
numbers are kept (for diagnostics and the NIST tests) but are not reported as results.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
import numpy.typing as npt
from scipy import stats

from curvis.analysis.fit.points import Points

type FloatArray = npt.NDArray[np.float64]

MAX_CORRELATION = 0.9999
"""Two parameters correlated beyond this make a fit ambiguous."""
NOT_CONVERGED_MESSAGE = (
    "The fit did not converge. Check the data, or fix a parameter to a known value."
)
BAD_RESIDUAL = 1e100
"""Residual used where the curve is not defined, so the minimiser steps back."""
MIN_RCOND = 1e-12
"""Smallest reciprocal condition number of the column-scaled JᵀJ."""


class FitModel(ABC):
    """A curve Y = f(X; p). Subclasses set :attr:`parameters` and implement :meth:`f`."""

    name: str = ""
    parameters: tuple[str, ...] = ()

    @abstractmethod
    def f(self, x: FloatArray, p: FloatArray) -> FloatArray:
        """The curve at ``x`` for parameter values ``p`` (in :attr:`parameters` order)."""

    def jacobian(self, x: FloatArray, p: FloatArray) -> FloatArray | None:
        """d f / d p, shape (len(x), len(p)), or None to use central differences."""
        return None

    def guess(self, x: FloatArray, y: FloatArray) -> FloatArray:
        """Initial values from the data."""
        raise NotImplementedError(f"{type(self).__name__} has no automatic initial values")

    def canonical(self, p: FloatArray, free: tuple[bool, ...]) -> FloatArray:
        """The same curve in its reported form (default: ``p`` itself). ``free`` marks
        the parameters that are free and unbounded, which alone may change."""
        return p


class FitStatus(StrEnum):
    OK = "ok"
    AMBIGUOUS = "ambiguous"
    NOT_CONVERGED = "not converged"
    TOO_FEW_POINTS = "too few points"


@dataclass(frozen=True)
class Constraints:
    """Fixed parameter values and (low, high) bounds, by parameter name."""

    fixed: Mapping[str, float] = field(default_factory=dict)
    bounds: Mapping[str, tuple[float, float]] = field(default_factory=dict)


NO_CONSTRAINTS = Constraints()


@dataclass(frozen=True)
class NonlinearFit:
    """A fitted model. ``values`` holds every parameter (fixed ones too); ``se`` and the
    CI are NaN for fixed parameters and when they cannot be computed."""

    model: FitModel
    status: FitStatus
    message: str
    """Why the fit is not OK (one plain sentence), or ""."""
    names: tuple[str, ...]
    free: tuple[bool, ...]
    values: FloatArray
    se: FloatArray
    ci_lower: FloatArray
    ci_upper: FloatArray
    n: int
    df: int
    rss: float
    r_squared: float
    sy_x: float
    confidence: float
    cov: FloatArray
    """Covariance of the free parameters (NaN when JᵀJ is singular)."""
    max_correlation: float = math.nan

    @property
    def ok(self) -> bool:
        return self.status is FitStatus.OK

    @property
    def converged(self) -> bool:
        """The minimisation converged (the fit may still be ambiguous)."""
        return self.status in (FitStatus.OK, FitStatus.AMBIGUOUS)

    def value(self, name: str) -> float:
        return float(self.values[self.names.index(name)])

    def predict(self, at: npt.ArrayLike) -> FloatArray:
        x = np.asarray(at, dtype=np.float64)
        if not self.converged:
            return np.full(x.shape, np.nan)
        return self.model.f(x, self.values)

    def band(self, at: npt.ArrayLike) -> tuple[FloatArray, FloatArray, FloatArray]:
        """The fitted curve and the limits of its confidence band at ``at``."""
        x = np.asarray(at, dtype=np.float64)
        fit = self.predict(x)
        if self.df <= 0 or not np.all(np.isfinite(self.cov)) or not self.converged:
            nan = np.full(x.shape, np.nan)
            return fit, nan, nan.copy()
        g = _jacobian(self.model, x, self.values)[:, np.array(self.free)]
        se_fit = np.sqrt(np.maximum(np.sum((g @ self.cov) * g, axis=1), 0.0))
        q = float(stats.t.ppf(0.5 + self.confidence / 2, self.df))
        return fit, fit - q * se_fit, fit + q * se_fit


def _jacobian(model: FitModel, x: FloatArray, p: FloatArray) -> FloatArray:
    jac = model.jacobian(x, p)
    if jac is not None:
        return np.asarray(jac, dtype=np.float64).reshape(len(x), len(p))
    # Central differences: error ~ h², h ~ eps^(1/3) relative to each parameter.
    out = np.empty((len(x), len(p)))
    for j in range(len(p)):
        h = 6e-6 * max(abs(p[j]), 1e-8)
        up, down = p.copy(), p.copy()
        up[j] += h
        down[j] -= h
        out[:, j] = (model.f(x, up) - model.f(x, down)) / (up[j] - down[j])
    return out


def _rss(model: FitModel, x: FloatArray, y: FloatArray, p: FloatArray) -> float:
    r = y - model.f(x, p)
    value = float(np.dot(r, r))
    return value if math.isfinite(value) else math.inf


def _polish(
    model: FitModel,
    x: FloatArray,
    y: FloatArray,
    p: FloatArray,
    free: npt.NDArray[np.bool_],
    low: FloatArray,
    high: FloatArray,
) -> FloatArray:
    """Gauss-Newton steps from ``p``, each halved until it lowers the RSS (and stays in
    bounds); stops when a step no longer lowers it."""
    rss = _rss(model, x, y, p)
    for _ in range(50):
        jac = _jacobian(model, x, p)[:, free]
        r = y - model.f(x, p)
        try:
            step, *_ = np.linalg.lstsq(jac, r, rcond=None)
        except np.linalg.LinAlgError:
            break
        if not np.all(np.isfinite(step)):
            break
        if np.all(np.abs(step) <= 1e-12 * np.maximum(np.abs(p[free]), 1e-300)):
            break  # at the minimum to working precision
        for k in range(30):
            q = p.copy()
            q[free] = p[free] + step / 2.0**k
            if np.any(q < low) or np.any(q > high):
                continue
            rss_q = _rss(model, x, y, q)
            if rss_q < rss:
                break
        else:
            break
        done = rss - rss_q <= 1e-15 * rss
        p, rss = q, rss_q
        if done:
            break
    return p


def fit_model(
    model: FitModel,
    points: Points,
    constraints: Constraints | None = None,
    start: npt.ArrayLike | None = None,
    confidence: float = 0.95,
    max_nfev: int | None = None,
) -> NonlinearFit:
    """Fit ``model`` to ``points`` from ``start`` (default: the model's guess).

    ``max_nfev`` limits the model evaluations (default 1000 per free parameter + 1;
    a fit that reaches it has not converged)."""
    import lmfit

    constraints = constraints or NO_CONSTRAINTS
    x, y = points.x, points.y
    names = model.parameters
    k_all = len(names)
    unknown = (set(constraints.fixed) | set(constraints.bounds)) - set(names)
    if unknown:
        raise ValueError(f"{model.name} has no parameters {sorted(unknown)}")
    free_t = tuple(name not in constraints.fixed for name in names)
    free = np.array(free_t)
    k = int(free.sum())
    n = len(x)
    low = np.array([constraints.bounds.get(nm, (-math.inf, math.inf))[0] for nm in names])
    high = np.array([constraints.bounds.get(nm, (-math.inf, math.inf))[1] for nm in names])
    for nm, value in constraints.fixed.items():
        i = names.index(nm)
        low[i] = high[i] = value

    def result(
        status: FitStatus, message: str, p: FloatArray, *, rss: float = math.nan
    ) -> NonlinearFit:
        nan = np.full(k_all, np.nan)
        return NonlinearFit(
            model, status, message, names, free_t, p, nan, nan.copy(), nan.copy(), n,
            n - k, rss, math.nan, math.nan, confidence, np.full((k, k), np.nan),
        )  # fmt: skip

    if n < max(k, 1):
        return result(
            FitStatus.TOO_FEW_POINTS,
            f"The fit needs at least {k} points; there are {n}.",
            np.full(k_all, np.nan),
        )

    p0 = np.asarray(model.guess(x, y) if start is None else start, dtype=np.float64).copy()
    for nm, value in constraints.fixed.items():
        p0[names.index(nm)] = value
    p0 = np.clip(p0, low, high)

    params = lmfit.Parameters()
    for i, nm in enumerate(names):
        if free[i]:
            params.add(nm, value=float(p0[i]), min=low[i], max=high[i])
        else:
            params.add(nm, value=float(p0[i]), vary=False)
    var_index = np.flatnonzero(free)

    def values_of(pars: lmfit.Parameters) -> FloatArray:
        return np.array([pars[nm].value for nm in names], dtype=np.float64)

    def residual(pars: lmfit.Parameters) -> FloatArray:
        r = model.f(x, values_of(pars)) - y
        # A step into a region where the curve is not defined (NaN, overflow) must
        # look bad, not stop the fit: Levenberg-Marquardt then takes a shorter step.
        return np.where(np.isfinite(r), r, BAD_RESIDUAL)

    has_jac = model.jacobian(x[:1], p0) is not None

    def jac(pars: lmfit.Parameters) -> FloatArray:
        j = _jacobian(model, x, values_of(pars))[:, var_index]
        return np.where(np.isfinite(j), j, 0.0)

    kws: dict[str, object] = {"ftol": 1e-12, "xtol": 1e-12}
    if has_jac:
        kws["Dfun"] = jac
    with np.errstate(all="ignore"):
        try:
            out = lmfit.minimize(
                residual, params, method="leastsq", max_nfev=max_nfev or 1000 * (k + 1), **kws
            )
        except (ValueError, ArithmeticError, np.linalg.LinAlgError):
            return result(FitStatus.NOT_CONVERGED, NOT_CONVERGED_MESSAGE, p0)
        p = values_of(out.params)
        # MINPACK's info 6-8 mean the tolerances were too small to reach: it stopped at
        # the best point it could find, which the polish then checks.
        converged = out.ier in (1, 2, 3, 4, 6, 7, 8) and bool(np.all(np.isfinite(p)))
        if not converged:
            return result(
                FitStatus.NOT_CONVERGED,
                NOT_CONVERGED_MESSAGE,
                p,
            )
        p = _polish(model, x, y, p, free, low, high)
        # Only parameters that are free and unbounded may change form: a bound or a fixed
        # value refers to the parameter as the user wrote it.
        unbounded = tuple(
            bool(free[i]) and nm not in constraints.bounds for i, nm in enumerate(names)
        )
        p = np.asarray(model.canonical(p, unbounded), dtype=np.float64)
        rss = _rss(model, x, y, p)
        if not math.isfinite(rss):
            return result(FitStatus.NOT_CONVERGED, NOT_CONVERGED_MESSAGE, p)
        jac_free = _jacobian(model, x, p)[:, free]

    df = n - k
    tss = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - rss / tss if tss > 0 else math.nan
    se = np.full(k_all, np.nan)
    ci_low, ci_high = se.copy(), se.copy()
    cov = np.full((k, k), np.nan)
    status, message = FitStatus.OK, ""
    max_corr = math.nan

    norms = np.sqrt(np.sum(jac_free**2, axis=0))
    if k and (not np.all(np.isfinite(norms)) or np.any(norms <= 1e-300)):
        flat = [names[i] for i, z in zip(var_index, norms <= 1e-300, strict=True) if z]
        status = FitStatus.AMBIGUOUS
        message = (
            f"The fit is ambiguous: {', '.join(flat) or 'a parameter'} does not change the curve."
        )
    elif k:
        sv = np.linalg.svd(jac_free / norms, compute_uv=False)
        if sv.min() < math.sqrt(MIN_RCOND) * sv.max():
            status = FitStatus.AMBIGUOUS
            message = "The fit is ambiguous: the data cannot determine all the parameters."
        else:
            _u, s, vt = np.linalg.svd(jac_free / norms, full_matrices=False)
            c_scaled = (vt.T / s**2) @ vt  # (JsᵀJs)⁻¹
            d = np.sqrt(np.diag(c_scaled))
            corr = c_scaled / np.outer(d, d)
            max_corr = float(np.max(np.abs(corr - np.eye(k)))) if k > 1 else 0.0
            if max_corr > MAX_CORRELATION:
                pair = np.unravel_index(np.argmax(np.abs(corr - np.eye(k))), corr.shape)
                a, b = (names[var_index[i]] for i in pair)
                status = FitStatus.AMBIGUOUS
                message = (
                    f"The fit is ambiguous: {a} and {b} depend on each other too strongly "
                    "for the data to determine them."
                )
            if df > 0:
                s2 = rss / df
                cov = c_scaled / np.outer(norms, norms) * s2
                se_free = np.sqrt(np.diag(cov))
                qt = float(stats.t.ppf(0.5 + confidence / 2, df))
                se[var_index] = se_free
                ci_low[var_index] = p[var_index] - qt * se_free
                ci_high[var_index] = p[var_index] + qt * se_free
    if status is FitStatus.OK:
        at_bound = [names[i] for i in var_index if _near(p[i], low[i]) or _near(p[i], high[i])]
        if at_bound:
            status = FitStatus.AMBIGUOUS
            message = f"The fit is ambiguous: {', '.join(at_bound)} ended on its limit."
    sy_x = math.sqrt(rss / df) if df > 0 else math.nan
    return NonlinearFit(
        model, status, message, names, free_t, p, se, ci_low, ci_high, n, df, rss,
        r_squared, sy_x, confidence, cov, max_corr,
    )  # fmt: skip


def _near(value: float, limit: float) -> bool:
    return math.isfinite(limit) and abs(value - limit) <= 1e-9 * max(1.0, abs(limit))
