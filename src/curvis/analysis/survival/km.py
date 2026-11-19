"""The Kaplan-Meier estimate: survival at each time, its 95% confidence interval, the
median survival with its interval, and numbers at risk.

Validated against R's ``survival::survfit(conf.type = "log-log")`` and
``quantile.survfit`` in ``validation/r/scripts/kaplan_meier.R``. lifelines
(``KaplanMeierFitter``) computes the estimate and its interval; the rest follows R:

- Missing times or events (NaN) are left out. Times that differ only by rounding are
  one time, as with survfit's ``timefix = TRUE`` (:func:`merge_near_ties`, R's
  ``aeqSurv``).
- At each distinct time t (events and censorings), n at risk is the number with time
  ≥ t; a censoring at the time of an event counts as at risk for it.
- The standard error of S is S·σ, with Greenwood's σ² = Σ d / (n (n − d)).
- The interval is log-log: S^exp(∓z σ / log S). Where S is 1 it is [1, 1]; where S is
  0 (every subject left had an event) there is no interval and no SE (NaN), as in R.
- The median is the first time S ≤ 0.5, or the midpoint of a stretch where S is 0.5
  (:func:`quantile_time`, R's ``findq``); its interval is the same on the lower and
  upper limits. NaN means "not reached".
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

type FloatArray = npt.NDArray[np.float64]
type IntArray = npt.NDArray[np.int64]

METHOD = "Kaplan-Meier survival estimate"
CI_METHOD = "log-log"
CONFIDENCE = 0.95

TOLERANCE = math.sqrt(np.finfo(np.float64).eps)
"""R's ``sqrt(.Machine$double.eps)``: the tolerance of ``aeqSurv`` and ``quantile.survfit``."""


@dataclass(frozen=True)
class KaplanMeier:
    """A Kaplan-Meier estimate. The arrays have one value per distinct time."""

    n: int
    """Subjects used (time and event both present)."""
    n_events: int
    time: FloatArray
    n_risk: IntArray
    n_event: IntArray
    n_censor: IntArray
    survival: FloatArray
    std_err: FloatArray
    """Standard error of S (NaN where S is 0)."""
    lower: FloatArray
    upper: FloatArray
    median: float
    median_lower: float
    median_upper: float
    subject_times: FloatArray
    """Every subject's time (after merging near ties), sorted, for numbers at risk."""

    @property
    def n_censored(self) -> int:
        return self.n - self.n_events

    def at_risk(self, times: npt.ArrayLike) -> IntArray:
        """Number at risk (time ≥ t) at each of ``times``."""
        t = np.asarray(times, dtype=np.float64)
        below = np.searchsorted(self.subject_times, t, side="left")
        return (self.n - below).astype(np.int64)

    def survival_at(self, times: npt.ArrayLike) -> FloatArray:
        """S just after each of ``times`` (1 before the first time)."""
        t = np.asarray(times, dtype=np.float64)
        index = np.searchsorted(self.time, t, side="right") - 1
        return np.where(index >= 0, self.survival[np.maximum(index, 0)], 1.0)


def check_survival_data(time: FloatArray, event: FloatArray) -> str | None:
    """Why the values cannot be survival data, or None. NaN (missing) is allowed."""
    if time.shape != event.shape:
        return "The Time and Event columns have different lengths."
    bad_time = ~np.isnan(time) & ~(time >= 0)
    if bad_time.any():
        return f"Times must be 0 or more (row {int(np.argmax(bad_time)) + 1})."
    bad_event = ~np.isnan(event) & (event != 0) & (event != 1)
    if bad_event.any():
        return f"Events must be 1 (event) or 0 (censored) (row {int(np.argmax(bad_event)) + 1})."
    return None


def merge_near_ties(time: FloatArray, tolerance: float = TOLERANCE) -> FloatArray:
    """Times that differ by at most ``tolerance`` (absolutely, or relative to the mean of
    the distinct times) become the smaller one: R's ``aeqSurv``."""
    distinct = np.unique(time[np.isfinite(time)])
    if len(distinct) < 2:
        return time
    gaps = np.diff(distinct)
    scale = float(np.mean(np.abs(distinct)))
    with np.errstate(divide="ignore", invalid="ignore"):
        tied = (gaps <= tolerance) | (gaps / scale <= tolerance)
    if not tied.any():
        return time
    cuts = distinct[np.concatenate(([True], ~tied))]
    index = np.searchsorted(cuts, time, side="right") - 1
    merged = np.array(time, dtype=np.float64)
    finite = np.isfinite(time)
    merged[finite] = cuts[index[finite]]
    return merged


def kaplan_meier(time: npt.ArrayLike, event: npt.ArrayLike) -> KaplanMeier:
    """The Kaplan-Meier estimate of ``time`` (≥ 0) and ``event`` (1 event, 0 censored).

    Raises ``ValueError`` if a value is invalid or no subject has both values.
    """
    t_all = np.asarray(time, dtype=np.float64)
    e_all = np.asarray(event, dtype=np.float64)
    problem = check_survival_data(t_all, e_all)
    if problem is not None:
        raise ValueError(problem)
    keep = ~np.isnan(t_all) & ~np.isnan(e_all)
    if not keep.any():
        raise ValueError("No subject has both a time and an event.")
    t = merge_near_ties(t_all[keep])
    e = e_all[keep].astype(np.int64)

    times, inverse = np.unique(t, return_inverse=True)
    n_event = np.bincount(inverse, weights=e, minlength=len(times)).astype(np.int64)
    n_at = np.bincount(inverse, minlength=len(times)).astype(np.int64)
    n_censor = n_at - n_event
    n_risk = (len(t) - np.concatenate(([0], np.cumsum(n_at)[:-1]))).astype(np.int64)

    survival, lower, upper = _lifelines_estimate(t, e, times)
    with np.errstate(divide="ignore", invalid="ignore"):
        terms = n_event / (n_risk * (n_risk - n_event))
    terms[n_event == 0] = 0.0
    sigma = np.sqrt(np.cumsum(terms))
    std_err = survival * sigma
    zero = survival == 0
    one = survival == 1
    std_err[zero] = np.nan
    lower[zero] = np.nan
    upper[zero] = np.nan
    std_err[one] = 0.0
    lower[one] = 1.0
    upper[one] = 1.0

    x = np.concatenate(([0.0], times))
    median = quantile_time(x, np.concatenate(([0.0], 1 - survival)), 0.5)
    median_lower = quantile_time(x, np.concatenate(([0.0], 1 - lower)), 0.5)
    median_upper = quantile_time(x, np.concatenate(([0.0], 1 - upper)), 0.5)
    return KaplanMeier(
        n=len(t),
        n_events=int(e.sum()),
        time=times,
        n_risk=n_risk,
        n_event=n_event,
        n_censor=n_censor,
        survival=survival,
        std_err=std_err,
        lower=lower,
        upper=upper,
        median=median,
        median_lower=median_lower,
        median_upper=median_upper,
        subject_times=np.sort(t),
    )


def _lifelines_estimate(
    t: FloatArray, e: IntArray, times: FloatArray
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """S and its log-log interval at ``times``, from lifelines."""
    import warnings

    from lifelines import KaplanMeierFitter  # imported here: lifelines is slow to import

    fitter = KaplanMeierFitter(alpha=1 - CONFIDENCE)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # lifelines warns about S = 0 in its CI
        fitter.fit(t, e)
    timeline = np.asarray(fitter.timeline, dtype=np.float64)
    index = np.searchsorted(timeline, times)
    if not np.array_equal(timeline[index], times):  # pragma: no cover - lifelines changed
        raise RuntimeError("lifelines' timeline does not hold every time")
    survival = np.array(fitter.survival_function_.to_numpy()[index, 0], dtype=np.float64)
    ci = fitter.confidence_interval_.to_numpy()
    return (
        survival,
        np.array(ci[index, 0], dtype=np.float64),
        np.array(ci[index, 1], dtype=np.float64),
    )


# --- quantiles, as R's quantile.survfit --------------------------------------------------


def quantile_time(x: FloatArray, y: FloatArray, p: float, tolerance: float = TOLERANCE) -> float:
    """The time at which the distribution function ``y`` (1 − S, a step function with a
    jump at each ``x``, starting at x = 0 with y = 0) reaches ``p``: R's ``findq``.

    Where y equals p (within ``tolerance``) over a stretch, the midpoint of that stretch;
    NaN if y never reaches p. NaN values of y (no interval there) are skipped.
    """
    finite_max = np.nanmax(y) if np.any(~np.isnan(y)) else -math.inf
    if finite_max < p:
        return math.nan
    x_max = float(x[-1])
    keep = ~_duplicated(y)
    x, y = x[keep], y[keep]
    n = len(y)
    positions = np.arange(1, n + 1, dtype=np.float64)
    i1 = _approx_constant(y + tolerance, positions, p)
    i2 = _approx_constant(y - tolerance, positions, p)
    if p == 0:
        return float(x[0])
    if not math.isnan(y[-1]) and abs(p - y[-1]) < tolerance:
        return (_at(x, i1) + x_max) / 2
    return (_at(x, i1) + _at(x, i2)) / 2


def _at(x: FloatArray, position: float) -> float:
    """``x[position]`` with R's 1-based, truncating indexing (NaN stays NaN)."""
    if math.isnan(position):
        return math.nan
    return float(x[int(position) - 1])


def _duplicated(y: FloatArray) -> npt.NDArray[np.bool_]:
    """R's ``duplicated``: True for a value seen earlier (NaN equals NaN)."""
    seen: set[float] = set()
    seen_nan = False
    out = np.zeros(len(y), dtype=bool)
    for i, value in enumerate(y):
        v = float(value)
        if math.isnan(v):
            out[i] = seen_nan
            seen_nan = True
        else:
            out[i] = v in seen
            seen.add(v)
    return out


def _approx_constant(x: FloatArray, y: FloatArray, v: float) -> float:
    """R's ``approx(x, y, v, method = "constant", f = 1)$y`` (rule 1: NaN outside)."""
    ok = ~np.isnan(x) & ~np.isnan(y)
    x, y = x[ok], y[ok]
    if len(x) == 0:
        return math.nan
    order = np.argsort(x, kind="stable")
    x, y = x[order], y[order]
    # ties = mean
    ux, start = np.unique(x, return_index=True)
    if len(ux) != len(x):
        sums = np.add.reduceat(y, start)
        counts = np.diff(np.append(start, len(x)))
        x, y = ux, sums / counts
    if v < x[0] or v > x[-1]:
        return math.nan
    i, j = 0, len(x) - 1
    while i < j - 1:
        mid = (i + j) // 2
        if v < x[mid]:
            j = mid
        else:
            i = mid
    if v == x[j]:
        return float(y[j])
    if v == x[i]:
        return float(y[i])
    return float(y[j])
