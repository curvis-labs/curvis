"""The curve-fit analyses of the Analyze dialog: linear regression, polynomial
regression, the Hill 4PL and the 3PL with the bottom fixed at 0.

Each fits every ticked series to the table's X column in one run (every replicate is a
point) and reports, per series, the parameters with their standard errors and 95%
confidence intervals, R², Sy.x, df and n. The fitted curves with their confidence
bands go into :attr:`Result.curves` for graphs.

X limits and fixed values are text parameters: empty means no limit (not fixed), and a
number may be written like 1e-9, which concentrations need.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import numpy as np

from curvis.analysis.base import (
    AnalysisError,
    AnalysisInputs,
    AnalysisType,
    Family,
    Params,
    param,
    register,
    rows_match_x,
    series_choices,
)
from curvis.analysis.result import Cell, FittedCurve, Kind, Result, ResultColumn, ResultTable
from curvis.data import Table

if TYPE_CHECKING:
    from curvis.analysis.fit.dose_response import DoseResponseFit
    from curvis.analysis.fit.linear import PolynomialFit
    from curvis.analysis.fit.points import Points, XRange

CURVE_POINTS = 200
"""Points along each fitted curve for graphs."""
FIT_IMPORTS = ("scipy.stats", "lmfit")
CONFIDENCE = 0.95

LINEAR_METHOD = "Linear regression (least squares)"
HILL4_METHOD = "Hill four-parameter logistic fit (4PL, least squares)"
HILL3_METHOD = "Three-parameter logistic fit (3PL, bottom fixed at 0, least squares)"


def polynomial_method(degree: int) -> str:
    return f"Polynomial regression, degree {degree} (least squares)"


# --- parameters --------------------------------------------------------------------------


def parse_number(text: str, what: str) -> float | None:
    """``text`` as a number, None when empty. Raises AnalysisError for anything else."""
    stripped = text.strip().replace("−", "-")
    if not stripped:
        return None
    try:
        value = float(stripped)
    except ValueError:
        raise AnalysisError(f"{what} must be a number, such as 2.5 or 1e-9.") from None
    if not math.isfinite(value):
        raise AnalysisError(f"{what} must be a finite number.")
    return value


@dataclass(frozen=True)
class FitParams(Params):
    x_range: str = param("", "Fit only &X from … to … (empty: all)", advanced=True)


RANGE_SEPARATOR = re.compile(r"\s*(?:\bto\b|…|\.\.|;)\s*", re.IGNORECASE)


def x_range(params: FitParams) -> XRange:
    """The X range of ``params.x_range``: "low to high" (or "low … high", "low; high");
    either end may be left out ("1e-8 to" means X >= 1e-8, "to 5" means X <= 5); empty
    means every point."""
    from curvis.analysis.fit.points import XRange

    text = params.x_range.strip()
    if not text:
        return XRange()
    parts = RANGE_SEPARATOR.split(text)
    if len(parts) != 2:
        raise AnalysisError('Write the X range as "low to high", for example "1e-9 to 1e-5".')
    low = parse_number(parts[0], "The low end of the X range")
    high = parse_number(parts[1], "The high end of the X range")
    low = -math.inf if low is None else low
    high = math.inf if high is None else high
    if low > high:
        raise AnalysisError("The low end of the X range is above the high end.")
    return XRange(low, high)


@dataclass(frozen=True)
class LinearParams(FitParams):
    pass


@dataclass(frozen=True)
class PolynomialParams(FitParams):
    degree: int = param(2, "&Degree", minimum=2, maximum=6)


@dataclass(frozen=True)
class Hill3Params(FitParams):
    x_is_log: bool = param(False, "X values are lo&g10(concentration)")
    fixed: str = param("", "&Fix parameters (e.g. top = 100)", advanced=True)


@dataclass(frozen=True)
class Hill4Params(Hill3Params):
    fixed: str = param("", "&Fix parameters (e.g. bottom = 0)", advanced=True)


# --- shared --------------------------------------------------------------------------------


def _int(value: int) -> str:
    return str(value)


class CurveFit(AnalysisType):
    """Base class of the curve fits: each ticked series against the X column."""

    family = Family.FIT
    uses_x = True
    imports = FIT_IMPORTS
    what: ClassVar[str] = "a curve"

    def problem(self, table: Table) -> str | None:
        if not rows_match_x(table):
            return "Needs an X column with values."
        if not series_choices(table):
            return "Needs at least one Y column."
        return None

    def check(self, table: Table, inputs: Sequence[str], params: Params) -> str | None:
        problem = super().check(table, inputs, params)
        if problem is not None:
            return problem
        assert isinstance(params, FitParams)
        try:
            x_range(params)
            self.fixed_values(params)
        except AnalysisError as exc:
            return str(exc)
        return None

    def fixed_values(self, params: Params) -> dict[str, float]:
        return {}

    def describe(self, table: Table, inputs: Sequence[str], params: Params) -> str:
        from curvis.analysis.descriptive import name_list

        names = [c.name for c in self.selected(table, inputs)]
        assert isinstance(params, FitParams)
        limited = " within the X range" if params.x_range.strip() else ""
        return f"Fits {self.what} to {name_list(names)}{limited}: {self.reports} with 95% CIs."

    reports: ClassVar[str] = ""


def _no_x(inputs: AnalysisInputs) -> np.ndarray[Any, np.dtype[np.float64]]:
    if inputs.x is None:
        raise AnalysisError("This table has no X column. Set a column's role to X.")
    if not inputs.series:
        raise AnalysisError("Tick at least one series, then run the analysis again.")
    return inputs.x


def _grid(points: Points, log_spaced: bool) -> np.ndarray[Any, np.dtype[np.float64]]:
    x = points.x[np.isfinite(points.x)]
    if log_spaced:
        x = x[x > 0]
    if len(x) == 0:
        return np.empty(0)
    low, high = float(np.min(x)), float(np.max(x))
    if log_spaced:
        return np.asarray(np.logspace(math.log10(low), math.log10(high), CURVE_POINTS))
    return np.linspace(low, high, CURVE_POINTS)


def _curve(series_id: str, name: str, grid: Any, band: Any, log_x: bool) -> FittedCurve:
    fit, low, high = band
    return FittedCurve(
        series_id,
        name,
        tuple(float(v) for v in grid),
        tuple(float(v) for v in fit),
        tuple(float(v) for v in low),
        tuple(float(v) for v in high),
        log_x,
        CONFIDENCE,
    )


def _table(
    title: str, names: Sequence[str], rows: Sequence[tuple[str, Sequence[Cell]]]
) -> ResultTable:
    columns = (ResultColumn("", Kind.TEXT), *(ResultColumn(n) for n in names))
    return ResultTable(title, columns, tuple((label, *cells) for label, cells in rows))


def _status_text(ok: bool, status: str) -> str:
    return "OK" if ok else status[0].upper() + status[1:]


def _summary(names: Sequence[str], oks: Sequence[bool], one: str, what: str) -> str:
    fitted = sum(oks)
    if len(names) == 1:
        return one if fitted else f"{names[0]} could not be fitted: see the warning."
    if fitted == len(names):
        return f"Fitted {what} to all {len(names)} series."
    if fitted == 0:
        return "None of the series could be fitted: see the warnings."
    return f"Fitted {what} to {fitted} of {len(names)} series; see the warnings for the others."


# --- linear and polynomial -------------------------------------------------------------------


def _coefficient_names(degree: int) -> list[str]:
    if degree == 1:
        return ["Y-intercept", "Slope"]
    return ["B0 (intercept)"] + [f"B{i}" for i in range(1, degree + 1)]


def run_polynomial(inputs: AnalysisInputs, params: FitParams, degree: int) -> Result:
    from curvis.analysis.compare.common import number
    from curvis.analysis.fit.linear import fit_polynomial
    from curvis.analysis.fit.points import fit_points

    x = _no_x(inputs)
    xr = x_range(params)
    labels = _coefficient_names(degree)
    order = [1, 0] if degree == 1 else list(range(degree + 1))  # Prism: slope first
    names = [s.name for s in inputs.series]
    fits: list[PolynomialFit] = []
    curves: list[FittedCurve] = []
    warnings: list[str] = []
    for s in inputs.series:
        points = fit_points(x, s.columns, xr)
        f = fit_polynomial(points, degree, CONFIDENCE)
        fits.append(f)
        if not f.ok:
            warnings.append(f"{s.name}: {f.problem}")
            continue
        grid = _grid(points, log_spaced=False)
        curves.append(_curve(s.id, s.name, grid, f.band(grid), log_x=False))
        if f.df == 0:
            warnings.append(
                f"{s.name}: the curve goes through every point (no degrees of freedom left), "
                "so there are no standard errors or confidence intervals."
            )

    def cells(get: Any) -> list[Cell]:
        return [get(f) if f.ok else None for f in fits]

    rows: list[tuple[str, Sequence[Cell]]] = [("Fit", [_status_text(f.ok, "no fit") for f in fits])]
    rows += [(labels[i], cells(lambda f, i=i: float(f.coefficients[i]))) for i in order]
    rows += [(f"SE of {labels[i]}", cells(lambda f, i=i: float(f.se[i]))) for i in order]
    for i in order:
        rows.append((f"Lower 95% CI of {labels[i]}", cells(lambda f, i=i: float(f.ci_lower[i]))))
        rows.append((f"Upper 95% CI of {labels[i]}", cells(lambda f, i=i: float(f.ci_upper[i]))))
    rows += [
        ("R²", cells(lambda f: f.r_squared)),
        ("Sy.x", cells(lambda f: f.sy_x)),
        ("Degrees of freedom", cells(lambda f: _int(f.df))),
        ("Number of points", [_int(f.n) for f in fits]),
    ]
    method = LINEAR_METHOD if degree == 1 else polynomial_method(degree)
    one = ""
    if fits[0].ok:
        f = fits[0]
        r2 = "" if math.isnan(f.r_squared) else f", R² = {number(f.r_squared)}"
        if degree == 1:
            ci = f" (95% CI {number(f.ci_lower[1])} to {number(f.ci_upper[1])})" if f.df > 0 else ""
            one = (
                f"The best-fit line for {names[0]} has slope {number(f.coefficients[1])}{ci} "
                f"and Y-intercept {number(f.coefficients[0])}{r2}."
            )
        else:
            one = f"Fitted a polynomial of degree {degree} to {names[0]}{r2}."
    what = "a line" if degree == 1 else f"a polynomial of degree {degree}"
    return Result(
        method,
        _summary(names, [f.ok for f in fits], one, what),
        tuple(warnings),
        tables=(_table("Best-fit values", names, rows),),
        curves=tuple(curves),
    )


class LinearRegression(CurveFit):
    id = "linear_regression"
    label = "Li&near regression"
    method = LINEAR_METHOD
    params_type = LinearParams
    what = "a straight line"
    reports = "slope, Y-intercept and R²"

    @staticmethod
    def run(inputs: AnalysisInputs, params: LinearParams) -> Result:
        return run_polynomial(inputs, params, 1)


class PolynomialRegression(CurveFit):
    id = "polynomial_regression"
    label = "&Polynomial regression"
    method = "Polynomial regression (least squares)"
    params_type = PolynomialParams
    reports = "the coefficients and R²"

    def describe(self, table: Table, inputs: Sequence[str], params: Params) -> str:
        assert isinstance(params, PolynomialParams)
        text = super().describe(table, inputs, params)
        return text.replace(self.what, f"a polynomial of degree {params.degree}", 1)

    @staticmethod
    def run(inputs: AnalysisInputs, params: PolynomialParams) -> Result:
        return run_polynomial(inputs, params, params.degree)


# --- dose-response ---------------------------------------------------------------------------

LOGISTIC_LABELS = {
    "bottom": "Bottom",
    "top": "Top",
    "log_ec50": "LogEC50",
    "hill_slope": "Hill slope",
}


def run_logistic(inputs: AnalysisInputs, params: Hill3Params, three: bool) -> Result:
    from curvis.analysis.compare.common import number
    from curvis.analysis.fit.dose_response import dose_response_points, fit_dose_response

    x = _no_x(inputs)
    xr = x_range(params)
    fixed = _logistic_fixed(params)
    names = [s.name for s in inputs.series]
    fits: list[DoseResponseFit] = []
    curves: list[FittedCurve] = []
    warnings: list[str] = []
    for s in inputs.series:
        points = dose_response_points(x, s.columns, params.x_is_log, xr)
        d = fit_dose_response(
            points,
            three_parameter=three,
            x_is_log=params.x_is_log,
            fixed=fixed,
            confidence=CONFIDENCE,
        )
        fits.append(d)
        f = d.fit
        if not f.ok:
            warnings.append(f"{s.name}: {f.message}")
            continue
        grid = _grid(fit_points(x, s.columns, xr), log_spaced=not params.x_is_log)
        curves.append(_curve(s.id, s.name, grid, d.band(grid), log_x=not params.x_is_log))
        finite = points.x[np.isfinite(points.x)]
        if len(finite) and not finite.min() <= f.value("log_ec50") <= finite.max():
            warnings.append(
                f"{s.name}: the EC50 is outside the range of the X values, so it is an "
                "extrapolation. Measure concentrations on both sides of it."
            )
        if f.df == 0:
            warnings.append(
                f"{s.name}: the curve goes through every point (no degrees of freedom "
                "left), so there are no standard errors or confidence intervals."
            )

    shown = ["bottom", "top", "log_ec50", "hill_slope"]

    def cells(get: Any) -> list[Cell]:
        return [get(d) if d.fit.ok else None for d in fits]

    def is_fixed(name: str) -> bool:
        return (three and name == "bottom") or name in fixed

    def value(name: str) -> Any:
        return lambda d: d.fit.value(name)

    def stat(name: str, attr: str) -> Any:
        def get(d: DoseResponseFit) -> Cell:
            if is_fixed(name):
                return "(fixed)"
            return float(getattr(d.fit, attr)[d.fit.names.index(name)])

        return get

    rows: list[tuple[str, Sequence[Cell]]] = [
        ("Fit", [_status_text(d.fit.ok, d.fit.status.value) for d in fits])
    ]
    rows += [(LOGISTIC_LABELS[n], cells(value(n))) for n in shown]
    rows.append(("EC50", cells(lambda d: d.ec50)))
    rows += [(f"SE of {LOGISTIC_LABELS[n]}", cells(stat(n, "se"))) for n in shown]
    for n in shown:
        rows.append((f"Lower 95% CI of {LOGISTIC_LABELS[n]}", cells(stat(n, "ci_lower"))))
        rows.append((f"Upper 95% CI of {LOGISTIC_LABELS[n]}", cells(stat(n, "ci_upper"))))
    if "log_ec50" not in fixed:
        rows.append(("Lower 95% CI of EC50", cells(lambda d: d.ec50_ci[0])))
        rows.append(("Upper 95% CI of EC50", cells(lambda d: d.ec50_ci[1])))
    rows += [
        ("R²", cells(lambda d: d.fit.r_squared)),
        ("Sy.x", cells(lambda d: d.fit.sy_x)),
        ("Degrees of freedom", cells(lambda d: _int(d.fit.df))),
        ("Number of points", [_int(d.fit.n) for d in fits]),
    ]
    one = ""
    if fits[0].fit.ok:
        d = fits[0]
        low, high = d.ec50_ci
        ci = f" (95% CI {number(low)} to {number(high)})" if d.fit.df > 0 else ""
        direction = "rises" if d.fit.value("hill_slope") > 0 else "falls"
        one = (
            f"The curve of {names[0]} {direction} with an EC50 of {number(d.ec50)}{ci} and a "
            f"Hill slope of {number(d.fit.value('hill_slope'))}; "
            f"R² = {number(d.fit.r_squared)}."
        )
    notes = []
    if params.x_is_log:
        notes.append("X values are log10(concentration); EC50 = 10^LogEC50.")
    else:
        notes.append(
            "X values are concentrations; the fit uses log10 of them (LogEC50 = log10 EC50), "
            "and the CI of the EC50 is 10^(CI of LogEC50)."
        )
    if fixed:
        notes.append(
            "Fixed: "
            + ", ".join(f"{LOGISTIC_LABELS[n]} = {number(v)}" for n, v in fixed.items())
            + "."
        )
    method = HILL3_METHOD if three else HILL4_METHOD
    what = "a 3PL curve" if three else "a 4PL curve"
    return Result(
        method,
        _summary(names, [d.fit.ok for d in fits], one, what),
        tuple(warnings),
        tables=(_table("Best-fit values", names, rows),),
        notes=tuple(notes),
        curves=tuple(curves),
    )


def fit_points(x: Any, columns: Any, xr: XRange) -> Points:
    from curvis.analysis.fit.points import fit_points as points

    return points(x, columns, xr)


FIXABLE = {
    "bottom": "bottom",
    "top": "top",
    "hill slope": "hill_slope",
    "hillslope": "hill_slope",
    "slope": "hill_slope",
    "logec50": "log_ec50",
    "log ec50": "log_ec50",
}


def _logistic_fixed(params: Hill3Params) -> dict[str, float]:
    """The values of ``params.fixed``: "name = value" pairs separated by commas or
    semicolons, e.g. "bottom = 0, hill slope = 1" (names in any case)."""
    three = not isinstance(params, Hill4Params)
    allowed = "top, hill slope or logEC50" if three else "bottom, top, hill slope or logEC50"
    fixed: dict[str, float] = {}
    for part in params.fixed.replace(";", ",").split(","):
        if not part.strip():
            continue
        name, equals, value = part.partition("=")
        key = FIXABLE.get(" ".join(name.lower().split()))
        if not equals or key is None or (three and key == "bottom"):
            raise AnalysisError(
                f'Fix parameters as "name = value", where the name is {allowed}; '
                'for example "top = 100".'
            )
        number = parse_number(value, f"The value of {name.strip()}")
        if number is None:
            raise AnalysisError(f"Give a value for {name.strip()}, for example {name.strip()} = 1.")
        fixed[key] = number
    return fixed


class Hill4(CurveFit):
    id = "hill_4pl"
    label = "Hill &4PL (variable slope)"
    method = HILL4_METHOD
    params_type = Hill4Params
    what = "a 4PL (Hill) curve"
    reports = "bottom, top, EC50 and Hill slope"

    def fixed_values(self, params: Params) -> dict[str, float]:
        assert isinstance(params, Hill3Params)
        return _logistic_fixed(params)

    @staticmethod
    def run(inputs: AnalysisInputs, params: Hill4Params) -> Result:
        return run_logistic(inputs, params, three=False)


class Hill3(CurveFit):
    id = "hill_3pl"
    label = "&3PL (bottom fixed at 0)"
    method = HILL3_METHOD
    params_type = Hill3Params
    what = "a 3PL curve (bottom 0)"
    reports = "top, EC50 and Hill slope"

    def fixed_values(self, params: Params) -> dict[str, float]:
        assert isinstance(params, Hill3Params)
        return _logistic_fixed(params)

    @staticmethod
    def run(inputs: AnalysisInputs, params: Hill3Params) -> Result:
        return run_logistic(inputs, params, three=True)


register(LinearRegression())
register(PolynomialRegression())
register(Hill4())
register(Hill3())
