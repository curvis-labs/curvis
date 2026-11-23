"""The curve-fit analyses of the Analyze dialog: linear regression, polynomial
regression, the Hill 4PL and the 3PL with the bottom fixed at 0, and the nonlinear
curves of docs/models.md (biphasic and bell-shaped dose-response, one-phase exponential
association and decay, Michaelis-Menten, Gaussian).

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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

import numpy as np

from curvis.analysis.base import (
    About,
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
from curvis.analysis.fit.sketch import (
    FloatArray,
    Marker,
    Sketch,
    SketchData,
    compute,
    half_time,
)
from curvis.analysis.result import Cell, FittedCurve, Kind, Result, ResultColumn, ResultTable
from curvis.data import Table

if TYPE_CHECKING:
    from curvis.analysis.fit.dose_response import DoseResponseFit
    from curvis.analysis.fit.engine import NonlinearFit
    from curvis.analysis.fit.linear import PolynomialFit
    from curvis.analysis.fit.nonlinear import CurveSpec, DerivedValue
    from curvis.analysis.fit.points import Points, XRange

CURVE_POINTS = 200
"""Points along each fitted curve for graphs."""
FIT_IMPORTS = ("scipy.stats", "lmfit")
CONFIDENCE = 0.95

LINEAR_METHOD = "Linear regression (least squares)"
HILL4_METHOD = "Hill four-parameter logistic fit (4PL, least squares)"
HILL3_METHOD = "Three-parameter logistic fit (3PL, bottom fixed at 0, least squares)"
BIPHASIC_METHOD = "Biphasic dose-response fit (least squares)"
BELL_METHOD = "Bell-shaped dose-response fit (least squares)"
ASSOCIATION_METHOD = "One-phase exponential association fit (least squares)"
DECAY_METHOD = "One-phase exponential decay fit (least squares)"
MICHAELIS_MENTEN_METHOD = "Michaelis-Menten fit (least squares)"
GAUSSIAN_METHOD = "Gaussian fit (least squares)"


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
    x_range: str = param("", "Fit only &X from", editor="x_range")
    """Written "low to high"; the dialog edits it as two number fields."""


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
    fixed: str = param("", "&Fix parameters", advanced=True, editor="fixed")
    """Written "name = value, …"; the dialog edits it as a table of the parameters."""


@dataclass(frozen=True)
class Hill4Params(Hill3Params):
    pass


# --- shared --------------------------------------------------------------------------------


def _int(value: int) -> str:
    return str(value)


def _zero(_values: Mapping[str, float]) -> float:
    return 0.0


def polynomial_sketch(degree: int) -> Sketch:
    """Example coefficients for a polynomial of ``degree`` on 0 to 10: 1 − X + 0.2 X² for
    degree 2; for a higher degree, roots spread evenly over 1 to 9, scaled so that the
    largest |Y| is 10."""
    if degree == 2:
        values = {"b0": 1.0, "b1": -1.0, "b2": 0.2}
    else:
        coefficients = np.poly(np.linspace(1.0, 9.0, degree))  # highest power first
        x = np.linspace(0.0, 10.0, 101)
        scale = 10.0 / float(np.max(np.abs(np.polyval(coefficients, x))))
        values = {f"b{degree - i}": float(c * scale) for i, c in enumerate(coefficients)}
    return Sketch(values, (0.0, 10.0))


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

    # --- About this analysis: the equation, the sketch and the points used -------------

    equation: ClassVar[str] = ""
    """The model in the formula language ("Y = …", parameters as names); the Analyze
    dialog renders it (:mod:`curvis.formula.display`), and a test checks that it is the
    model :meth:`curve` evaluates."""
    equation_note: ClassVar[str] = ""
    """A line under the equation (e.g. what X is)."""
    sketch: ClassVar[Sketch | None] = None

    def equation_for(self, params: Params) -> str:
        return self.equation

    def sketch_for(self, params: Params) -> Sketch | None:
        return self.sketch

    def parameters(self, params: Params) -> list[tuple[str, str]]:
        """The parameters that can be fixed, as (name, display name), in model order."""
        return []

    def curve(self, x: FloatArray, values: Mapping[str, float], params: Params) -> FloatArray:
        """The model's own function at ``x`` for parameter ``values`` (by name)."""
        raise NotImplementedError

    def sketch_data(self, params: Params) -> SketchData | None:
        """The sketch for ``params``: fixed values replace their example values."""
        sketch = self.sketch_for(params)
        if sketch is None:
            return None
        try:
            fixed = self.fixed_values(params)
        except AnalysisError:
            fixed = {}
        labels = dict(self.parameters(params))
        return compute(sketch, lambda x, v: self.curve(x, v, params), fixed, labels)

    def points(
        self, x: FloatArray, columns: Sequence[FloatArray], params: Params, xr: XRange
    ) -> Points:
        """The points the fit uses (as :meth:`run` makes them)."""
        return fit_points(x, list(columns), xr)

    def point_count(
        self, table: Table, inputs: Sequence[str], params: Params
    ) -> tuple[int, int] | None:
        """(points used, points with values) of the ticked series, or None when the X
        range cannot be read."""
        from curvis.analysis.base import x_column_id
        from curvis.analysis.fit.points import XRange

        x_id = x_column_id(table)
        assert isinstance(params, FitParams)
        try:
            xr = x_range(params)
        except AnalysisError:
            return None
        if x_id is None:
            return (0, 0)
        x = np.asarray(table.column(x_id).values, dtype=np.float64)
        columns = [
            np.asarray(table.column(c).values, dtype=np.float64)
            for choice in self.selected(table, inputs)
            for c in choice.column_ids
        ]
        if not columns:
            return (0, 0)
        return self.points(x, columns, params, xr).n, self.points(x, columns, params, XRange()).n

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
    section = "Linear"
    about = About(
        "slope, intercept, R²", "Use when Y changes in proportion to X, along a straight line."
    )
    equation = "Y = Slope*X + Intercept"
    sketch = Sketch(
        {"slope": 1.0, "intercept": 1.0}, (0.0, 10.0), (Marker("point", "Intercept", "", _zero),)
    )

    def curve(self, x: FloatArray, values: Mapping[str, float], params: Params) -> FloatArray:
        return values["slope"] * x + values["intercept"]

    @staticmethod
    def run(inputs: AnalysisInputs, params: LinearParams) -> Result:
        return run_polynomial(inputs, params, 1)


class PolynomialRegression(CurveFit):
    id = "polynomial_regression"
    label = "&Polynomial regression"
    method = "Polynomial regression (least squares)"
    params_type = PolynomialParams
    reports = "the coefficients and R²"
    section = "Linear"
    about = About(
        "degree 2–6",
        "Use when the points follow a smooth curve with no known model, such as a calibration "
        "curve. The coefficients rarely have a physical meaning.",
    )

    def equation_for(self, params: Params) -> str:
        assert isinstance(params, PolynomialParams)
        terms = ["B0", "B1*X"] + [f"B{i}*X^{i}" for i in range(2, params.degree + 1)]
        return "Y = " + " + ".join(terms)

    def sketch_for(self, params: Params) -> Sketch:
        assert isinstance(params, PolynomialParams)
        return polynomial_sketch(params.degree)

    def curve(self, x: FloatArray, values: Mapping[str, float], params: Params) -> FloatArray:
        assert isinstance(params, PolynomialParams)
        y = np.zeros_like(x)
        for i in range(params.degree, -1, -1):
            y = y * x + values[f"b{i}"]
        return y

    def describe(self, table: Table, inputs: Sequence[str], params: Params) -> str:
        assert isinstance(params, PolynomialParams)
        text = super().describe(table, inputs, params)
        return text.replace(self.what, f"a polynomial of degree {params.degree}", 1)

    @staticmethod
    def run(inputs: AnalysisInputs, params: PolynomialParams) -> Result:
        return run_polynomial(inputs, params, params.degree)


# --- dose-response ---------------------------------------------------------------------------


def run_logistic(inputs: AnalysisInputs, params: Hill3Params, three: bool) -> Result:
    from curvis.analysis.compare.common import number
    from curvis.analysis.fit.dose_response import LABELS as LOGISTIC_LABELS
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


LOG_X_NOTE = "where X is log10(concentration)"


def _log_ec50(values: Mapping[str, float]) -> float:
    return values["log_ec50"]


def _center(values: Mapping[str, float]) -> float:
    return values["center"]


def _logistic(x: FloatArray, values: Mapping[str, float]) -> FloatArray:
    from curvis.analysis.fit.dose_response import LOGISTIC, PARAMETERS

    return LOGISTIC.f(x, np.array([values[p] for p in PARAMETERS]))


class Hill4(CurveFit):
    id = "hill_4pl"
    label = "Hill &4PL (variable slope)"
    method = HILL4_METHOD
    params_type = Hill4Params
    what = "a 4PL (Hill) curve"
    reports = "bottom, top, EC50 and Hill slope"
    section = "Dose-response"
    about = About(
        "EC50, Hill slope, top, bottom",
        "Use when the response rises or falls between two plateaus as log dose increases.",
    )
    equation = "Y = Bottom + (Top - Bottom) / (1 + 10^((LogEC50 - X)*HillSlope))"
    equation_note = LOG_X_NOTE
    sketch = Sketch(
        {"bottom": 0.0, "top": 100.0, "log_ec50": -7.0, "hill_slope": 1.0},
        (-10.0, -4.0),
        (
            Marker("h", "Top", "top"),
            Marker("h", "Bottom", "bottom"),
            Marker("v", "EC50", "log_ec50"),
            Marker("label", "Hill slope: steepness at EC50", "hill_slope", _log_ec50),
        ),
        follow=("log_ec50",),
    )

    def parameters(self, params: Params) -> list[tuple[str, str]]:
        from curvis.analysis.fit.dose_response import LABELS, PARAMETERS

        return [(p, LABELS[p]) for p in PARAMETERS]

    def curve(self, x: FloatArray, values: Mapping[str, float], params: Params) -> FloatArray:
        return _logistic(x, values)

    def points(
        self, x: FloatArray, columns: Sequence[FloatArray], params: Params, xr: XRange
    ) -> Points:
        from curvis.analysis.fit.dose_response import dose_response_points

        assert isinstance(params, Hill3Params)
        return dose_response_points(x, list(columns), params.x_is_log, xr)

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
    section = "Dose-response"
    about = About(
        "EC50, Hill slope, top",
        "Use when the response starts at 0, for example after subtracting the baseline. If "
        "the low plateau is not 0, use Hill 4PL.",
    )
    equation = "Y = Top / (1 + 10^((LogEC50 - X)*HillSlope))"
    equation_note = LOG_X_NOTE
    sketch = Sketch(
        {"top": 100.0, "log_ec50": -7.0, "hill_slope": 1.0},
        (-10.0, -4.0),
        (
            Marker("h", "Top", "top"),
            Marker("v", "EC50", "log_ec50"),
            Marker("label", "Hill slope: steepness at EC50", "hill_slope", _log_ec50),
        ),
        follow=("log_ec50",),
    )

    def parameters(self, params: Params) -> list[tuple[str, str]]:
        from curvis.analysis.fit.dose_response import LABELS

        return [(p, LABELS[p]) for p in ("top", "log_ec50", "hill_slope")]

    def curve(self, x: FloatArray, values: Mapping[str, float], params: Params) -> FloatArray:
        return _logistic(x, {**values, "bottom": 0.0})

    def points(
        self, x: FloatArray, columns: Sequence[FloatArray], params: Params, xr: XRange
    ) -> Points:
        from curvis.analysis.fit.dose_response import dose_response_points

        assert isinstance(params, Hill3Params)
        return dose_response_points(x, list(columns), params.x_is_log, xr)

    def fixed_values(self, params: Params) -> dict[str, float]:
        assert isinstance(params, Hill3Params)
        return _logistic_fixed(params)

    @staticmethod
    def run(inputs: AnalysisInputs, params: Hill3Params) -> Result:
        return run_logistic(inputs, params, three=True)


# --- nonlinear curves (docs/models.md) ---------------------------------------------------


@dataclass(frozen=True)
class TwoPhaseParams(FitParams):
    x_is_log: bool = param(False, "X values are lo&g10(concentration)")
    fixed: str = param("", "&Fix parameters", advanced=True, editor="fixed")


@dataclass(frozen=True)
class AssociationParams(FitParams):
    fixed: str = param("", "&Fix parameters", advanced=True, editor="fixed")


@dataclass(frozen=True)
class DecayParams(FitParams):
    fixed: str = param("", "&Fix parameters", advanced=True, editor="fixed")


@dataclass(frozen=True)
class MichaelisMentenParams(FitParams):
    fixed: str = param("", "&Fix parameters", advanced=True, editor="fixed")


@dataclass(frozen=True)
class GaussianParams(FitParams):
    fixed: str = param("", "&Fix parameters", advanced=True, editor="fixed")


def curve_fixed(spec: CurveSpec, text: str, example: str) -> dict[str, float]:
    """The values of a "Fix parameters" text: "name = value" pairs separated by commas
    or semicolons, with names as the results show them in any case and spacing
    ("hill slope 1", "HillSlope1"), or a few short forms (nH1). Fixing every
    parameter is refused."""
    from curvis.analysis.fit.nonlinear import name_key

    names = spec.names()
    labels = [spec.labels[p] for p in spec.model.parameters]
    allowed = ", ".join(labels[:-1]) + " or " + labels[-1]
    fixed: dict[str, float] = {}
    for part in text.replace(";", ",").split(","):
        if not part.strip():
            continue
        name, equals, value = part.partition("=")
        key = names.get(name_key(name))
        if not equals or key is None:
            raise AnalysisError(
                f'Fix parameters as "name = value", where the name is {allowed}; '
                f'for example "{example}".'
            )
        found = parse_number(value, f"The value of {name.strip()}")
        if found is None:
            raise AnalysisError(f"Give a value for {name.strip()}, for example {example}.")
        fixed[key] = found
    if len(fixed) == len(spec.model.parameters):
        raise AnalysisError("Leave at least one parameter free to fit.")
    return fixed


@dataclass(frozen=True)
class _SeriesFit:
    name: str
    fit: NonlinearFit
    derived: dict[str, DerivedValue | None]
    points: Points


def run_curve(inputs: AnalysisInputs, params: FitParams, kind: NonlinearCurve) -> Result:
    """Fit ``kind``'s curve to every series and lay out the results."""
    from curvis.analysis.fit.dose_response import log_x
    from curvis.analysis.fit.nonlinear import curve_points, derived_value, fit_curve, uses_fixed

    spec = kind.spec()
    x = _no_x(inputs)
    xr = x_range(params)
    fixed = kind.fixed_values(params)
    x_is_log = bool(getattr(params, "x_is_log", False))
    on_log = spec.log_x and not x_is_log
    fits: list[_SeriesFit] = []
    curves: list[FittedCurve] = []
    warnings: list[str] = []
    for s in inputs.series:
        points = curve_points(spec, x, s.columns, x_is_log, xr)
        f = fit_curve(spec, points, fixed, CONFIDENCE)
        derived = {d.name: derived_value(f, d) for d in spec.derived} if f.ok else {}
        fits.append(_SeriesFit(s.name, f, derived, points))
        if not f.ok:
            warnings.append(f"{s.name}: {f.message}")
            continue
        grid = _grid(fit_points(x, s.columns, xr), log_spaced=on_log)
        band = f.band(log_x(grid, False) if on_log else grid)
        curves.append(_curve(s.id, s.name, grid, band, log_x=on_log))
        warnings += [f"{s.name}: {w}" for w in kind.warnings(fits[-1], x_is_log)]
        if f.df == 0:
            warnings.append(
                f"{s.name}: the curve goes through every point (no degrees of freedom "
                "left), so there are no standard errors or confidence intervals."
            )

    names = [s.name for s in inputs.series]
    parameters = spec.model.parameters
    shown_derived = [d for d in spec.derived if not uses_fixed(d, fixed)]

    def cells(get: Any) -> list[Cell]:
        return [get(sf) if sf.fit.ok else None for sf in fits]

    def value(name: str) -> Any:
        return lambda sf: sf.fit.value(name)

    def stat(name: str, attr: str) -> Any:
        def get(sf: _SeriesFit) -> Cell:
            if name in fixed:
                return "(fixed)"
            return float(getattr(sf.fit, attr)[sf.fit.names.index(name)])

        return get

    def derived_cell(name: str, attr: str) -> Any:
        def get(sf: _SeriesFit) -> Cell:
            dv = sf.derived.get(name)
            return None if dv is None else float(getattr(dv, attr))

        return get

    labels = spec.labels
    rows: list[tuple[str, Sequence[Cell]]] = [
        ("Fit", [_status_text(sf.fit.ok, sf.fit.status.value) for sf in fits])
    ]
    rows += [(labels[n], cells(value(n))) for n in parameters]
    rows += [(d.label, cells(derived_cell(d.name, "value"))) for d in shown_derived]
    rows += [(f"SE of {labels[n]}", cells(stat(n, "se"))) for n in parameters]
    rows += [
        (f"SE of {d.label}", cells(derived_cell(d.name, "se")))
        for d in shown_derived
        if d.linear is not None
    ]
    for n in parameters:
        rows.append((f"Lower 95% CI of {labels[n]}", cells(stat(n, "ci_lower"))))
        rows.append((f"Upper 95% CI of {labels[n]}", cells(stat(n, "ci_upper"))))
    for d in shown_derived:
        rows.append((f"Lower 95% CI of {d.label}", cells(derived_cell(d.name, "ci_lower"))))
        rows.append((f"Upper 95% CI of {d.label}", cells(derived_cell(d.name, "ci_upper"))))
    rows += [
        ("R²", cells(lambda sf: sf.fit.r_squared)),
        ("Sy.x", cells(lambda sf: sf.fit.sy_x)),
        ("Degrees of freedom", cells(lambda sf: _int(sf.fit.df))),
        ("Number of points", [_int(sf.fit.n) for sf in fits]),
    ]
    one = kind.reading(fits[0]) if fits[0].fit.ok else ""
    notes = list(kind.notes(x_is_log))
    if fixed:
        from curvis.analysis.compare.common import number

        notes.append(
            "Fixed: " + ", ".join(f"{labels[n]} = {number(v)}" for n, v in fixed.items()) + "."
        )
    return Result(
        kind.method,
        _summary(names, [sf.fit.ok for sf in fits], one, kind.what),
        tuple(warnings),
        tables=(_table("Best-fit values", names, rows),),
        notes=tuple(notes),
        curves=tuple(curves),
    )


def _ci(dv: DerivedValue | None, df: int) -> str:
    from curvis.analysis.compare.common import number

    if dv is None or df <= 0 or math.isnan(dv.ci_lower):
        return ""
    return f" (95% CI {number(dv.ci_lower)} to {number(dv.ci_upper)})"


def _param_ci(f: NonlinearFit, name: str) -> str:
    from curvis.analysis.compare.common import number

    i = f.names.index(name)
    if f.df <= 0 or not f.free[i] or math.isnan(float(f.ci_lower[i])):
        return ""
    return f" (95% CI {number(float(f.ci_lower[i]))} to {number(float(f.ci_upper[i]))})"


def _outside(value: float, points: Points) -> bool:
    finite = points.x[np.isfinite(points.x)]
    return bool(len(finite)) and not float(finite.min()) <= value <= float(finite.max())


class NonlinearCurve(CurveFit):
    """A curve of docs/models.md. Subclasses name their :meth:`spec` and may add a
    one-line reading, warnings and notes."""

    example: ClassVar[str] = ""
    """A "Fix parameters" example, as in the field's label."""

    @staticmethod
    def spec() -> CurveSpec:
        raise NotImplementedError

    def fixed_values(self, params: Params) -> dict[str, float]:
        return curve_fixed(self.spec(), str(getattr(params, "fixed", "")), self.example)

    def parameters(self, params: Params) -> list[tuple[str, str]]:
        spec = self.spec()
        return [(p, spec.labels[p]) for p in spec.model.parameters]

    def curve(self, x: FloatArray, values: Mapping[str, float], params: Params) -> FloatArray:
        model = self.spec().model
        return model.f(x, np.array([values[p] for p in model.parameters]))

    def points(
        self, x: FloatArray, columns: Sequence[FloatArray], params: Params, xr: XRange
    ) -> Points:
        from curvis.analysis.fit.nonlinear import curve_points

        x_is_log = bool(getattr(params, "x_is_log", False))
        return curve_points(self.spec(), x, list(columns), x_is_log, xr)

    def reading(self, sf: _SeriesFit) -> str:
        from curvis.analysis.compare.common import number

        return f"Fitted {self.what} to {sf.name}; R² = {number(sf.fit.r_squared)}."

    def warnings(self, sf: _SeriesFit, x_is_log: bool) -> list[str]:
        return []

    def notes(self, x_is_log: bool) -> list[str]:
        return []


def _ec50_warnings(sf: _SeriesFit) -> list[str]:
    out = []
    for i in (1, 2):
        name = f"log_ec50_{i}"
        if sf.fit.free[sf.fit.names.index(name)] and _outside(sf.fit.value(name), sf.points):
            out.append(
                f"EC50 {i} is outside the range of the X values, so it is an extrapolation. "
                "Measure concentrations on both sides of it."
            )
    return out


def _log_notes(x_is_log: bool) -> list[str]:
    if x_is_log:
        return ["X values are log10(concentration); EC50 = 10^LogEC50."]
    return [
        "X values are concentrations; the fit uses log10 of them (LogEC50 = log10 EC50), "
        "and the CI of each EC50 is 10^(CI of its LogEC50)."
    ]


class Biphasic(NonlinearCurve):
    id = "biphasic"
    label = "&Biphasic dose-response"
    method = BIPHASIC_METHOD
    params_type = TwoPhaseParams
    what = "a biphasic dose-response curve"
    reports = "bottom, top, the fraction in phase 1, both EC50s and Hill slopes"
    example = "hill slope 1 = 1"
    section = "Dose-response"
    about = About(
        "two EC50s, fraction",
        "Use when the curve rises or falls in two steps, such as a drug acting on two binding "
        "sites with different potencies.",
    )
    equation = (
        "Y = Bottom + (Top - Bottom)*(Frac / (1 + 10^((LogEC50_1 - X)*HillSlope_1))"
        " + (1 - Frac) / (1 + 10^((LogEC50_2 - X)*HillSlope_2)))"
    )
    equation_note = LOG_X_NOTE
    sketch = Sketch(
        {
            "bottom": 0.0,
            "top": 100.0,
            "frac": 0.4,
            "log_ec50_1": -8.0,
            "log_ec50_2": -5.0,
            "hill_slope_1": 1.0,
            "hill_slope_2": 1.0,
        },
        (-11.0, -2.0),
        (
            Marker("h", "Top", "top"),
            Marker("h", "Bottom", "bottom"),
            Marker("v", "EC50 1", "log_ec50_1"),
            Marker("v", "EC50 2", "log_ec50_2"),
        ),
        follow=("log_ec50_1", "log_ec50_2"),
    )

    @staticmethod
    def spec() -> CurveSpec:
        from curvis.analysis.fit.multiphase import BIPHASIC

        return BIPHASIC

    @staticmethod
    def run(inputs: AnalysisInputs, params: TwoPhaseParams) -> Result:
        return run_curve(inputs, params, BIPHASIC_TYPE)

    def reading(self, sf: _SeriesFit) -> str:
        from curvis.analysis.compare.common import number

        f = sf.fit
        e1, e2 = sf.derived.get("ec50_1"), sf.derived.get("ec50_2")
        first = f"{number(e1.value)}{_ci(e1, f.df)}" if e1 else number(10 ** f.value("log_ec50_1"))
        second = f"{number(e2.value)}{_ci(e2, f.df)}" if e2 else number(10 ** f.value("log_ec50_2"))
        return (
            f"The curve of {sf.name} has two phases, with EC50s of {first} and {second}; "
            f"{number(100 * f.value('frac'))}% of the span is in the first phase; "
            f"R² = {number(f.r_squared)}."
        )

    def warnings(self, sf: _SeriesFit, x_is_log: bool) -> list[str]:
        out = _ec50_warnings(sf)
        frac = sf.fit.value("frac")
        if not 0 <= frac <= 1:
            out.append(
                "Frac is outside 0 to 1, so the two phases go in opposite directions. "
                "A bell-shaped curve may describe the data better."
            )
        return out

    def notes(self, x_is_log: bool) -> list[str]:
        return [*_log_notes(x_is_log), "Frac is the fraction of the span in phase 1."]


class BellShaped(NonlinearCurve):
    id = "bell_shaped"
    label = "B&ell-shaped dose-response"
    method = BELL_METHOD
    params_type = TwoPhaseParams
    what = "a bell-shaped dose-response curve"
    reports = "both plateaus, the dip, both EC50s and Hill slopes, and the peak center, width and amplitude"  # noqa: E501
    example = "hill slope 1 = 1"
    section = "Shape-based"  # SPEC.md lists it with the Gaussian
    about = About(
        "two EC50s, peak",
        "Use when the response first rises and then falls as dose increases, or falls and "
        "then rises.",
    )
    equation = (
        "Y = Dip + (Plateau_1 - Dip) / (1 + 10^((X - LogEC50_1)*HillSlope_1))"
        " + (Plateau_2 - Dip) / (1 + 10^((LogEC50_2 - X)*HillSlope_2))"
    )
    equation_note = LOG_X_NOTE
    sketch = Sketch(
        {
            "plateau_1": 0.0,
            "dip": 100.0,
            "plateau_2": 10.0,
            "log_ec50_1": -8.0,
            "log_ec50_2": -5.0,
            "hill_slope_1": 1.5,
            "hill_slope_2": 1.5,
        },
        (-11.0, -2.0),
        (
            Marker("h", "Dip", "dip"),
            Marker("v", "EC50 1", "log_ec50_1"),
            Marker("v", "EC50 2", "log_ec50_2"),
        ),
        follow=("log_ec50_1", "log_ec50_2"),
    )

    @staticmethod
    def spec() -> CurveSpec:
        from curvis.analysis.fit.multiphase import BELL_SHAPED

        return BELL_SHAPED

    @staticmethod
    def run(inputs: AnalysisInputs, params: TwoPhaseParams) -> Result:
        return run_curve(inputs, params, BELL_TYPE)

    def reading(self, sf: _SeriesFit) -> str:
        from curvis.analysis.compare.common import number

        f = sf.fit
        amplitude = sf.derived.get("amplitude")
        center = sf.derived.get("center")
        shape = (
            "peaks"
            if f.value("dip") >= (f.value("plateau_1") + f.value("plateau_2")) / 2
            else "dips"
        )
        where = (
            f" at a log concentration of {number(center.value)}{_ci(center, f.df)}"
            if center
            else ""
        )
        size = f" with an amplitude of {number(amplitude.value)}" if amplitude else ""
        return f"The curve of {sf.name} {shape}{where}{size}; R² = {number(f.r_squared)}."

    def warnings(self, sf: _SeriesFit, x_is_log: bool) -> list[str]:
        out = _ec50_warnings(sf)
        f = sf.fit
        if f.value("log_ec50_1") > f.value("log_ec50_2"):
            out.append(
                "LogEC50 1 is above LogEC50 2, so the curve does not have the usual bell "
                "shape; read the peak center, width and amplitude with care."
            )
        return out

    def notes(self, x_is_log: bool) -> list[str]:
        unit = "X" if x_is_log else "log10(concentration)"
        return [
            *_log_notes(x_is_log),
            f"Peak center = (LogEC50 1 + LogEC50 2) / 2 and peak width = LogEC50 2 − LogEC50 1, "
            f"in units of {unit}; peak amplitude = Dip − (Plateau 1 + Plateau 2) / 2.",
        ]


class _Exponential(NonlinearCurve):
    half: ClassVar[str] = "half_life"
    half_label: ClassVar[str] = "half-life"
    moves: ClassVar[str] = "decays"

    def reading(self, sf: _SeriesFit) -> str:
        from curvis.analysis.compare.common import number

        f = sf.fit
        k = f.value("k")
        half = sf.derived.get(self.half)
        if k > 0 and half is not None:
            return (
                f"{sf.name} {self.moves} to a plateau of {number(f.value('plateau'))} with a "
                f"{self.half_label} of {number(half.value)}{_ci(half, f.df)} "
                f"(K = {number(k)}); R² = {number(f.r_squared)}."
            )
        return f"Fitted {self.what} to {sf.name} with K = {number(k)}; R² = {number(f.r_squared)}."

    def warnings(self, sf: _SeriesFit, x_is_log: bool) -> list[str]:
        f = sf.fit
        k = f.value("k")
        finite = sf.points.x[np.isfinite(sf.points.x)]
        out = []
        if k < 0:
            out.append(
                f"K is negative, so the curve grows away from the plateau instead of "
                f"approaching it, and the {self.half_label} is not defined. Check the "
                "data, or choose another model."
            )
        elif len(finite) and k > 0 and math.log(2) / k > float(np.ptp(finite)):
            out.append(
                f"The {self.half_label} is longer than the X range of the data, so the "
                "plateau is an extrapolation. Measure for longer."
            )
        return out

    def notes(self, x_is_log: bool) -> list[str]:
        return [
            f"The {self.half_label} is ln 2 / K and tau is 1 / K (X units); their CIs "
            "come from the CI of K."
        ]


class ExpAssociation(_Exponential):
    id = "exp_association"
    label = "Exponential ass&ociation"
    method = ASSOCIATION_METHOD
    params_type = AssociationParams
    what = "a one-phase exponential association"
    reports = "Y0, plateau, the rate constant K and the half-time"
    example = "Y0 = 0"
    section = "Kinetics"
    about = About(
        "plateau, K, half-time",
        "Use when Y rises towards a plateau over time, at a rate proportional to the distance "
        "left, such as ligand binding. For a fall towards a plateau, use exponential decay.",
    )
    equation = "Y = Y0 + (Plateau - Y0)*(1 - e^(-K*X))"
    sketch = Sketch(
        {"y0": 0.0, "plateau": 100.0, "k": 0.3},
        (0.0, 20.0),
        (Marker("h", "Plateau", "plateau"), Marker("v", "Half-time", "k", half_time)),
    )
    half = "half_time"
    half_label = "half-time"
    moves = "rises"

    @staticmethod
    def spec() -> CurveSpec:
        from curvis.analysis.fit.kinetics import EXP_ASSOCIATION

        return EXP_ASSOCIATION

    @staticmethod
    def run(inputs: AnalysisInputs, params: AssociationParams) -> Result:
        return run_curve(inputs, params, ASSOCIATION_TYPE)


class ExpDecay(_Exponential):
    id = "exp_decay"
    label = "Exponential deca&y"
    method = DECAY_METHOD
    params_type = DecayParams
    what = "a one-phase exponential decay"
    reports = "Y0, plateau, the rate constant K and the half-life"
    example = "plateau = 0"
    section = "Kinetics"
    about = About(
        "plateau, K, half-life",
        "Use when Y falls towards a plateau over time, such as radioactive decay, drug "
        "elimination or dissociation.",
    )
    equation = "Y = Plateau + (Y0 - Plateau)*e^(-K*X)"
    sketch = Sketch(
        {"y0": 100.0, "plateau": 10.0, "k": 0.3},
        (0.0, 20.0),
        (Marker("h", "Plateau", "plateau"), Marker("v", "Half-life", "k", half_time)),
    )

    @staticmethod
    def spec() -> CurveSpec:
        from curvis.analysis.fit.kinetics import EXP_DECAY

        return EXP_DECAY

    @staticmethod
    def run(inputs: AnalysisInputs, params: DecayParams) -> Result:
        return run_curve(inputs, params, DECAY_TYPE)


class MichaelisMenten(NonlinearCurve):
    id = "michaelis_menten"
    label = "&Michaelis-Menten"
    method = MICHAELIS_MENTEN_METHOD
    params_type = MichaelisMentenParams
    what = "a Michaelis-Menten curve"
    reports = "Vmax and Km"
    example = "Vmax = 100"
    section = "Kinetics"
    about = About(
        "Vmax, Km",
        "Use when enzyme velocity (Y) levels off as substrate concentration (X) increases.",
    )
    equation = "Y = Vmax*X / (Km + X)"
    sketch = Sketch(
        {"vmax": 100.0, "km": 2.0},
        (0.0, 20.0),
        (Marker("h", "Vmax", "vmax"), Marker("v", "Km", "km")),
    )

    @staticmethod
    def spec() -> CurveSpec:
        from curvis.analysis.fit.kinetics import MICHAELIS_MENTEN

        return MICHAELIS_MENTEN

    @staticmethod
    def run(inputs: AnalysisInputs, params: MichaelisMentenParams) -> Result:
        return run_curve(inputs, params, MICHAELIS_MENTEN_TYPE)

    def reading(self, sf: _SeriesFit) -> str:
        from curvis.analysis.compare.common import number

        f = sf.fit
        return (
            f"{sf.name} has Vmax = {number(f.value('vmax'))}{_param_ci(f, 'vmax')} and "
            f"Km = {number(f.value('km'))}{_param_ci(f, 'km')}; R² = {number(f.r_squared)}."
        )

    def warnings(self, sf: _SeriesFit, x_is_log: bool) -> list[str]:
        km = sf.fit.value("km")
        finite = sf.points.x[np.isfinite(sf.points.x)]
        if km < 0:
            return ["Km is negative, which has no meaning for an enzyme. Check the data."]
        if len(finite) and km > float(finite.max()):
            return [
                "Km is above the highest substrate concentration, so Vmax and Km are "
                "extrapolations. Measure at higher concentrations."
            ]
        return []


class Gaussian(NonlinearCurve):
    id = "gaussian"
    label = "Ga&ussian"
    method = GAUSSIAN_METHOD
    params_type = GaussianParams
    what = "a Gaussian peak"
    reports = "the peak center, width (SD and FWHM), amplitude and baseline"
    example = "baseline = 0"
    section = "Shape-based"
    about = About(
        "center, SD, amplitude",
        "Use when the data form one symmetric peak or dip, such as a spectral band.",
    )
    equation = "Y = Baseline + Amplitude*e^(-0.5*((X - Center) / SD)^2)"
    sketch = Sketch(
        {"baseline": 5.0, "amplitude": 100.0, "center": 0.0, "sd": 1.0},
        (-4.0, 4.0),
        (
            Marker("h", "Baseline", "baseline"),
            Marker("v", "Center", "center"),
            Marker("label", "Amplitude", "amplitude", _center),
        ),
    )

    @staticmethod
    def spec() -> CurveSpec:
        from curvis.analysis.fit.shape import GAUSSIAN

        return GAUSSIAN

    @staticmethod
    def run(inputs: AnalysisInputs, params: GaussianParams) -> Result:
        return run_curve(inputs, params, GAUSSIAN_TYPE)

    def reading(self, sf: _SeriesFit) -> str:
        from curvis.analysis.compare.common import number

        f = sf.fit
        return (
            f"The peak of {sf.name} is at {number(f.value('center'))}{_param_ci(f, 'center')}, "
            f"with SD {number(f.value('sd'))} and amplitude {number(f.value('amplitude'))}; "
            f"R² = {number(f.r_squared)}."
        )

    def warnings(self, sf: _SeriesFit, x_is_log: bool) -> list[str]:
        if _outside(sf.fit.value("center"), sf.points):
            return [
                "The center of the peak is outside the range of the X values, so it is "
                "an extrapolation."
            ]
        return []

    def notes(self, x_is_log: bool) -> list[str]:
        return ["FWHM (full width at half maximum) = 2.3548 × SD."]


BIPHASIC_TYPE = Biphasic()
BELL_TYPE = BellShaped()
ASSOCIATION_TYPE = ExpAssociation()
DECAY_TYPE = ExpDecay()
MICHAELIS_MENTEN_TYPE = MichaelisMenten()
GAUSSIAN_TYPE = Gaussian()

register(LinearRegression())
register(PolynomialRegression())
register(Hill4())
register(Hill3())
register(BIPHASIC_TYPE)
register(ASSOCIATION_TYPE)
register(DECAY_TYPE)
register(MICHAELIS_MENTEN_TYPE)
register(BELL_TYPE)
register(GAUSSIAN_TYPE)
