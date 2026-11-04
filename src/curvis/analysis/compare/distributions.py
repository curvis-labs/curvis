"""Upper tail probabilities and critical values of the studentized range (Tukey) and of
Dunnett's many-to-one comparisons, by deterministic numerical integration.

SciPy's ``studentized_range`` underflows to 0 for small P values, and
``scipy.stats.dunnett`` (like R's multcomp) uses randomized quasi-Monte Carlo, so its P
values change from run to run. These functions integrate the defining formulas instead
(the same ones validation/r/scripts/one_way_anova.R uses for the reference values):

- s = chi(df) / sqrt(df) has density 2 (df/2)^(df/2) / Gamma(df/2) s^(df-1) e^(-df s^2 / 2).
- Studentized range of k means: P(Q > q) = E_s[k ∫ phi(z) (Phi(z)^(k-1)
  - (Phi(z) - Phi(z - q s))^(k-1)) dz].
- Dunnett (two-sided), with lambda_j = sqrt(n_j / (n_j + n_control)):
  P(max |T_j| >= c) = E_s[∫ phi(z) (1 - prod_j P(|lambda_j z + sqrt(1 - lambda_j^2) E| < c s)) dz].

Upper tails are computed directly, in logs, so small P values keep their relative
accuracy. The integral runs over u = log s and z. Where the integrand matters is found
on coarse grids (zooming in until the peak is resolved), then integrated with
composite Gauss-Legendre rules. The integrand is smooth, so the result is accurate to
about 1e-13 relative.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
import numpy.typing as npt
from scipy import optimize, special

type FloatArray = npt.NDArray[np.float64]
type LogIntegrand = Callable[[FloatArray, FloatArray], FloatArray]
"""log of the inner integrand phi(z) h(z, w) for w = c s; arrays broadcast."""

_GL_X, _GL_W = np.polynomial.legendre.leggauss(16)

DROP = 45.0
"""Parts of the integrand below e^-DROP times its largest value are left out."""

_COARSE_U = 65
_U_PANELS = 8
_U_PANELS_MAX = 128
_U_PANEL_WIDTH = 0.8
"""Widest u panel. For small df the density of u = log s has a long exponential tail
to the left (about 45 / df wide), which then needs more panels."""
_Z_PANELS_MIN = 8
_Z_PANELS_MAX = 64
_Z_PANEL_WIDTH = 2.5
"""Widest z panel, in units of the integrand's feature width."""
_Z_STEP = 0.25
Z_LIMIT = 40.0


def _log_chi_scaled_density_u(u: FloatArray, df: float) -> FloatArray:
    """log of the density of u = log s (that is, of s times the density of s)."""
    x = df / 2
    if x >= 10:
        # log 2 + x log x - x - lgamma(x), without the cancellation of the direct form.
        correction = 1 / (12 * x) - 1 / (360 * x**3) + 1 / (1260 * x**5) - 1 / (1680 * x**7)
        constant = math.log(2) + 0.5 * math.log(x) - 0.5 * math.log(2 * math.pi) - correction
    else:
        constant = math.log(2) + x * math.log(x) - x - math.lgamma(x)
    # df (u - (e^(2u) - 1) / 2) is 0 at the mode u = 0.
    return constant + df * (u - np.expm1(2 * u) / 2)


def _composite(lo: FloatArray, hi: FloatArray, panels: int) -> tuple[FloatArray, FloatArray]:
    """Gauss-Legendre nodes and weights on [lo, hi] (arrays of intervals), ``panels``
    panels of 16 nodes each. Output shape: lo.shape + (panels * 16,)."""
    t = np.linspace(0.0, 1.0, panels + 1)
    edges = lo[..., None] + (hi - lo)[..., None] * t
    a = edges[..., :-1, None]
    b = edges[..., 1:, None]
    nodes = (a + b) / 2 + (b - a) / 2 * _GL_X
    weights = (b - a) / 2 * _GL_W * np.ones_like(nodes)
    shape = (*lo.shape, panels * len(_GL_X))
    return nodes.reshape(shape), weights.reshape(shape)


def _z_grid() -> tuple[FloatArray, float]:
    """The coarse z grid. Both integrands are phi(z) times a factor of at most k, so
    beyond |z| = Z_LIMIT they are below e^-800: smaller than any P value a double holds."""
    count = math.ceil(2 * Z_LIMIT / _Z_STEP) + 1
    return np.linspace(-Z_LIMIT, Z_LIMIT, count), 2 * Z_LIMIT / (count - 1)


def _log_rows(log_f: LogIntegrand, w: FloatArray) -> tuple[FloatArray, FloatArray, FloatArray]:
    """For each w (one row each): the log of the inner integral on a coarse grid, and
    the z range where the integrand is within e^-DROP of its row maximum."""
    z, step = _z_grid()
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        values = log_f(z[None, :], w[:, None])
    values = np.where(np.isnan(values), -np.inf, values)
    top = values.max(axis=1)
    finite = np.isfinite(top)
    safe_top = np.where(finite, top, 0.0)
    with np.errstate(divide="ignore"):
        log_integral = safe_top + np.log(np.exp(values - safe_top[:, None]).sum(axis=1) * step)
    log_integral[~finite] = -np.inf
    inside = values > (safe_top - DROP)[:, None]
    inside[~finite] = False
    first = np.where(inside.any(axis=1), inside.argmax(axis=1), 0)
    last = np.where(inside.any(axis=1), len(z) - 1 - inside[:, ::-1].argmax(axis=1), 0)
    lo = z[np.maximum(first - 1, 0)] - 1.0
    hi = z[np.minimum(last + 1, len(z) - 1)] + 1.0
    return log_integral, lo, hi


def _upper_tail(log_f: LogIntegrand, c: float, df: float, z_scale: float = 1.0) -> float:
    """E_s[∫ exp(log_f(z, c s)) dz] for s = chi(df) / sqrt(df).

    ``z_scale`` is the width of the narrowest feature of the integrand in z (1 for the
    normal density; less where it has sharper edges), which sets the number of panels.
    """
    if not c > 0:
        return 1.0
    # The mass in u lies between the density's mode (u = 0) and the region where c s is
    # of order one; the bounds widen below if the integrand is still large at an edge.
    lo = math.log(0.01 / (c + 1.0)) - 1.0
    hi = 2.0
    for _ in range(60):
        u = np.linspace(lo, hi, _COARSE_U)
        log_inner, z_lo_coarse, z_hi_coarse = _log_rows(log_f, c * np.exp(u))
        g = _log_chi_scaled_density_u(u, df) + log_inner
        top = float(np.max(g))
        if not math.isfinite(top):
            return 0.0
        inside = np.flatnonzero(g > top - DROP)
        first, last = int(inside[0]), int(inside[-1])
        width = hi - lo
        if first == 0 or last == _COARSE_U - 1:  # still large at an edge: widen
            # To the left the density falls exponentially (as s^df), to the right
            # faster than exponentially (as e^(-df s^2 / 2)), so widen less there.
            lo = lo - width if first == 0 else lo
            hi = hi + 1.0 if last == _COARSE_U - 1 else hi
            continue
        new_lo, new_hi = u[first - 1], u[last + 1]
        if last - first >= 16:  # the peak is resolved
            lo, hi = new_lo, new_hi
            break
        lo, hi = new_lo, new_hi
    u_panels = min(max(math.ceil((hi - lo) / _U_PANEL_WIDTH), _U_PANELS), _U_PANELS_MAX)
    u_nodes, u_weights = _composite(np.array(lo), np.array(hi), u_panels)
    w = c * np.exp(u_nodes)
    # The z range of each node, interpolated from the coarse rows (it changes smoothly
    # with u), with one more unit of margin.
    z_lo = np.interp(u_nodes, u, z_lo_coarse) - 1.0
    z_hi = np.interp(u_nodes, u, z_hi_coarse) + 1.0
    widest = float(np.max(z_hi - z_lo))
    panels = math.ceil(widest / (_Z_PANEL_WIDTH * min(z_scale, 1.0)))
    panels = min(max(panels, _Z_PANELS_MIN), _Z_PANELS_MAX)
    z_nodes, z_weights = _composite(z_lo, z_hi, panels)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        values = log_f(z_nodes, w[:, None]) + _log_chi_scaled_density_u(u_nodes, df)[:, None]
    values = np.where(np.isnan(values), -np.inf, values)
    top = float(np.max(values))
    if not math.isfinite(top):
        return 0.0
    total = float(np.sum(u_weights[:, None] * z_weights * np.exp(values - top)))
    return min(1.0, math.exp(top) * total)


# --- studentized range ---------------------------------------------------------------


def _tukey_log_integrand(k: int) -> LogIntegrand:
    log_k = math.log(k)
    log_norm = -0.5 * math.log(2 * math.pi)

    def log_f(z: FloatArray, w: FloatArray) -> FloatArray:
        log_phi = log_norm - z * z / 2
        log_cdf = special.log_ndtr(z)
        ratio = np.exp(special.log_ndtr(z - w) - log_cdf)  # Phi(z - w) / Phi(z), <= 1
        # Phi^(k-1) - (Phi - Phi(z - w))^(k-1) = Phi^(k-1) (1 - (1 - ratio)^(k-1))
        rest = np.log(-np.expm1((k - 1) * np.log1p(-np.minimum(ratio, 1.0))))
        result: FloatArray = log_k + log_phi + (k - 1) * log_cdf + rest
        return result

    return log_f


def studentized_range_sf(q: float, k: int, df: float) -> float:
    """P(Q > q) for the studentized range of ``k`` means with ``df`` degrees of freedom."""
    if k < 2 or not df > 0:
        raise ValueError("the studentized range needs k >= 2 and df > 0")
    if math.isnan(q):
        return math.nan
    return _upper_tail(_tukey_log_integrand(k), q, df)


def studentized_range_critical(alpha: float, k: int, df: float) -> float:
    """The q with P(Q > q) = ``alpha``."""
    # Bracket: one comparison, and Bonferroni over all k (k - 1) / 2 pairs.
    pairs = k * (k - 1) / 2
    lo = math.sqrt(2) * float(special.stdtrit(df, 1 - alpha / 2))
    hi = math.sqrt(2) * float(special.stdtrit(df, 1 - alpha / (2 * pairs)))
    return _critical(lambda q: studentized_range_sf(q, k, df), alpha, lo, hi)


# --- Dunnett ----------------------------------------------------------------------------


def dunnett_lambdas(n: FloatArray, n_control: float) -> tuple[FloatArray, FloatArray]:
    """lambda_j = sqrt(n_j / (n_j + n_control)) and sqrt(1 - lambda_j^2), each computed
    directly (no cancellation)."""
    total = n + n_control
    return np.sqrt(n / total), np.sqrt(n_control / total)


def _dunnett_log_integrand(lam: FloatArray, rest: FloatArray) -> LogIntegrand:
    log_norm = -0.5 * math.log(2 * math.pi)

    def log_f(z: FloatArray, w: FloatArray) -> FloatArray:
        # Comparisons on a last axis: P(|lambda z + rest E| >= w) from both upper tails.
        zz = z[..., None] * (lam / rest)
        ww = w[..., None] / rest
        outside = special.ndtr(zz - ww) + special.ndtr(-zz - ww)
        log_none = np.log1p(-np.minimum(outside, 1.0)).sum(axis=-1)
        result: FloatArray = log_norm - z * z / 2 + np.log(-np.expm1(log_none))
        return result

    return log_f


def dunnett_sf(c: float, n: FloatArray, n_control: float, df: float) -> float:
    """P(max_j |T_j| >= c) for Dunnett's comparisons of groups of sizes ``n`` with a
    control group of size ``n_control``, on ``df`` degrees of freedom."""
    if len(n) < 1 or not df > 0:
        raise ValueError("Dunnett's test needs at least one comparison and df > 0")
    if math.isnan(c):
        return math.nan
    lam, rest = dunnett_lambdas(np.asarray(n, dtype=np.float64), float(n_control))
    # P(|lambda z + rest E| >= w) changes over a z width of rest / lambda.
    z_scale = float(np.min(rest / lam))
    return _upper_tail(_dunnett_log_integrand(lam, rest), abs(c), df, z_scale)


def dunnett_critical(alpha: float, n: FloatArray, n_control: float, df: float) -> float:
    """The c with P(max_j |T_j| >= c) = ``alpha``."""
    # Bracket: one comparison, and Bonferroni over the len(n) comparisons.
    lo = float(special.stdtrit(df, 1 - alpha / 2))
    hi = float(special.stdtrit(df, 1 - alpha / (2 * len(n))))
    return _critical(lambda c: dunnett_sf(c, n, n_control, df), alpha, lo, hi)


def _critical(sf: Callable[[float], float], alpha: float, lo: float, hi: float) -> float:
    """The x with sf(x) = alpha, for a decreasing sf with sf(lo) >= alpha >= sf(hi)."""
    if hi <= lo:  # one comparison: the bounds agree
        return lo
    # Slightly wider than the bounds, so rounding at either end cannot exclude the root.
    lo, hi = lo * (1 - 1e-9), hi * (1 + 1e-9)
    root: float = optimize.brentq(lambda x: sf(x) - alpha, lo, hi, xtol=1e-12, rtol=1e-12)
    return root
