"""The curve-fit framework: engine behaviour that the R fixtures do not cover (fixed
parameters, bounds, ambiguity, the canonical 4PL form, robustness) and the curve-fit
analyses' results."""

import math

import numpy as np
import numpy.typing as npt
import pytest

from curvis.analysis import base
from curvis.analysis.base import AnalysisError, AnalysisInputs, SeriesData
from curvis.analysis.fit.analyses import (
    Hill3Params,
    Hill4Params,
    LinearParams,
    PolynomialParams,
    _logistic_fixed,
    x_range,
)
from curvis.analysis.fit.dose_response import (
    LOGISTIC,
    dose_response_points,
    fit_dose_response,
)
from curvis.analysis.fit.engine import Constraints, FitModel, FitStatus, fit_model
from curvis.analysis.fit.linear import fit_polynomial
from curvis.analysis.fit.points import Points, XRange, fit_points
from curvis.data import Column, Table

type FloatArray = npt.NDArray[np.float64]

CONC = 10.0 ** np.arange(-9, -3.5, 0.5)


def logistic(conc: FloatArray, bottom: float, top: float, log_ec50: float, h: float) -> FloatArray:
    return LOGISTIC.f(np.log10(conc), np.array([bottom, top, log_ec50, h]))


def noisy(seed: int, h: float = 1.0, sd: float = 3.0) -> list[FloatArray]:
    rng = np.random.default_rng(seed)
    clean = logistic(CONC, 5, 95, -6.5, h)
    return [clean + rng.normal(0, sd, len(CONC)) for _ in range(3)]


# --- points ---------------------------------------------------------------------------------


def test_points_stack_replicates_and_drop_missing_and_out_of_range() -> None:
    p = fit_points([1, 2, np.nan, 4], [[10, np.nan, 30, 40], [11, 21, 31, np.inf]], XRange(1, 3))
    assert p.x.tolist() == [1, 1, 2]
    assert p.y.tolist() == [10, 11, 21]


def test_negative_concentrations_are_left_out_and_zero_is_minus_infinity() -> None:
    p = dose_response_points([-1, 0, 10], [[1, 2, 3]], x_is_log=False)
    assert p.x.tolist() == [-math.inf, 1.0]


# --- linear ----------------------------------------------------------------------------------


def test_the_band_is_symmetric_and_narrowest_near_the_mean_x() -> None:
    x = np.arange(10.0)
    f = fit_polynomial(Points(x, 1 + 2 * x + np.sin(x)), 1)
    fit, low, high = f.band([0, 4.5, 9])
    assert np.allclose(fit - low, high - fit)
    widths = high - low
    assert widths[1] < widths[0] and widths[1] < widths[2]
    assert np.allclose(f.predict([4.5]), f.coefficients[0] + 4.5 * f.coefficients[1])


def test_the_degree_must_be_one_to_six() -> None:
    with pytest.raises(ValueError):
        fit_polynomial(Points(np.arange(10.0), np.arange(10.0)), 7)


# --- engine ----------------------------------------------------------------------------------


def test_a_fixed_parameter_keeps_its_value_and_has_no_se() -> None:
    d = fit_dose_response(dose_response_points(CONC, noisy(1), False), fixed={"hill_slope": 1.0})
    f = d.fit
    assert f.ok and f.value("hill_slope") == 1.0
    assert math.isnan(f.se[3]) and f.df == len(CONC) * 3 - 3


def test_a_parameter_that_ends_on_its_bound_makes_the_fit_ambiguous() -> None:
    points = dose_response_points(CONC, noisy(2), False)
    d = fit_dose_response(points, bounds={"hill_slope": (0.0, 0.5)})  # the slope is near 1
    assert d.fit.status is FitStatus.AMBIGUOUS
    assert d.fit.value("hill_slope") == pytest.approx(0.5)
    assert "hill_slope ended on its limit" in d.fit.message


def test_bounds_that_hold_leave_the_fit_ok() -> None:
    points = dose_response_points(CONC, noisy(2), False)
    free = fit_dose_response(points)
    bounded = fit_dose_response(points, bounds={"hill_slope": (0.0, 10.0)})
    assert bounded.fit.ok
    assert bounded.fit.values == pytest.approx(free.fit.values, rel=1e-9)


def test_4pl_reports_top_above_bottom_for_falling_data() -> None:
    y = [logistic(CONC, 10, 100, -7, -0.8) + e for e in (0.5, -0.5)]
    d = fit_dose_response(dose_response_points(CONC, y, False))
    assert d.fit.ok
    assert d.fit.value("top") > d.fit.value("bottom")
    assert d.fit.value("hill_slope") < 0


def test_an_exact_line_in_log_x_has_no_4pl_fit() -> None:
    y = [50 + 8 * (np.log10(CONC) + 6.5)]
    d = fit_dose_response(dose_response_points(CONC, y, False))
    assert not d.fit.ok
    assert d.fit.message.startswith("The fit")


class Redundant(FitModel):
    """y = a * b * x: only the product is determined."""

    name = "redundant"
    parameters = ("a", "b")

    def f(self, x: FloatArray, p: FloatArray) -> FloatArray:
        return np.asarray(p[0] * p[1] * x, dtype=np.float64)


def test_parameters_that_depend_on_each_other_make_the_fit_ambiguous() -> None:
    x = np.arange(1.0, 8.0)
    f = fit_model(Redundant(), Points(x, 2 * x + np.sin(x)), start=[1.0, 1.0])
    assert f.status is FitStatus.AMBIGUOUS
    assert "a" in f.message and "b" in f.message


class Root(FitModel):
    """y = sqrt(x - c): NaN for x < c."""

    name = "root"
    parameters = ("c",)

    def f(self, x: FloatArray, p: FloatArray) -> FloatArray:
        with np.errstate(invalid="ignore"):
            return np.asarray(np.sqrt(x - p[0]), dtype=np.float64)


def test_steps_where_the_curve_is_not_defined_do_not_stop_the_fit() -> None:
    x = np.arange(5.0, 15.0)
    f = fit_model(Root(), Points(x, np.sqrt(x - 3.0)), start=[-50.0])
    assert f.converged
    assert f.value("c") == pytest.approx(3.0, abs=1e-6)


def test_too_few_points() -> None:
    f = fit_model(LOGISTIC, Points(np.array([1.0, 2.0]), np.array([1.0, 2.0])))
    assert f.status is FitStatus.TOO_FEW_POINTS and "at least 4 points" in f.message


def test_unknown_constraint_names_are_an_error() -> None:
    with pytest.raises(ValueError):
        fit_model(LOGISTIC, Points(CONC, CONC), Constraints(fixed={"slope": 1.0}))


@pytest.mark.parametrize("h", [0.6, 1.0, 2.0, -1.0, -2.0])
def test_the_automatic_initial_values_converge_on_noisy_data(h: float) -> None:
    """50 noisy data sets per slope: every 4PL fit converges near the true EC50. (With
    half-log spacing and this much noise, a steeper curve can be fitted best by a step
    between two concentrations; its slope is then rightly reported as ambiguous.)"""
    for seed in range(50):
        d = fit_dose_response(dose_response_points(CONC, noisy(seed, h, 4.0), False))
        assert d.fit.ok, (seed, d.fit.message)
        assert d.fit.value("log_ec50") == pytest.approx(-6.5, abs=0.3)


# --- analysis parameters ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "low", "high"),
    [
        ("", -math.inf, math.inf),
        ("1e-9 to 1e-5", 1e-9, 1e-5),
        ("to 5", -math.inf, 5.0),
        ("3 to", 3.0, math.inf),
        ("−2 … 4", -2.0, 4.0),
        ("0.5; 2", 0.5, 2.0),
    ],
)
def test_the_x_range_is_read_from_text(text: str, low: float, high: float) -> None:
    assert x_range(LinearParams(x_range=text)) == XRange(low, high)


@pytest.mark.parametrize("text", ["5", "a to b", "5 to 2", "1 to inf"])
def test_a_bad_x_range_says_how_to_write_it(text: str) -> None:
    with pytest.raises(AnalysisError):
        x_range(LinearParams(x_range=text))


def test_fixed_parameters_are_read_from_text() -> None:
    assert _logistic_fixed(Hill4Params(fixed="Bottom = 0, Hill slope=1; top = 1e2")) == {
        "bottom": 0.0,
        "hill_slope": 1.0,
        "top": 100.0,
    }
    with pytest.raises(AnalysisError, match="top, hill slope or logEC50"):
        _logistic_fixed(Hill3Params(fixed="bottom = 0"))
    with pytest.raises(AnalysisError):
        _logistic_fixed(Hill4Params(fixed="span = 3"))


# --- analyses ----------------------------------------------------------------------------------


def series_inputs(x: FloatArray, *series: list[FloatArray]) -> AnalysisInputs:
    data = tuple(SeriesData(f"s{i}", f"S{i}", tuple(cols)) for i, cols in enumerate(series))
    return AnalysisInputs("T", len(x), data, x, "X")


def test_several_series_in_one_run_with_one_that_cannot_be_fitted() -> None:
    flat = [np.full(len(CONC), 42.0)]
    inputs = series_inputs(CONC, noisy(3), flat, noisy(4))
    result = base.get("hill_4pl").run(inputs, Hill4Params())
    (table,) = result.tables
    assert [c.label for c in table.columns] == ["", "S0", "S1", "S2"]
    status = dict((r[0], r[1:]) for r in table.rows)["Fit"]
    assert status[0] == "OK" and status[1] != "OK" and status[2] == "OK"
    assert result.summary == "Fitted a 4PL curve to 2 of 3 series; see the warnings for the others."
    assert any(w.startswith("S1: The fit") for w in result.warnings)
    assert [c.series_id for c in result.curves] == ["s0", "s2"]
    curve = result.curves[0]
    assert curve.log_x and len(curve.x) == 200
    assert curve.x[0] == pytest.approx(CONC[0]) and curve.x[-1] == pytest.approx(CONC[-1])
    assert all(lo <= y <= hi for lo, y, hi in zip(curve.lower, curve.y, curve.upper, strict=True))


def test_one_series_gets_a_plain_language_reading() -> None:
    result = base.get("hill_4pl").run(series_inputs(CONC, noisy(5)), Hill4Params())
    assert result.summary.startswith("The curve of S0 rises with an EC50 of ")
    assert result.method == "Hill four-parameter logistic fit (4PL, least squares)"
    line = base.get("linear_regression").run(
        series_inputs(np.arange(5.0), [np.array([1.0, 3.1, 4.9, 7.2, 9.0])]), LinearParams()
    )
    assert line.summary.startswith("The best-fit line for S0 has slope 2.0")


def test_an_ec50_outside_the_data_is_flagged_as_an_extrapolation() -> None:
    x = CONC[:6]  # up to 10^-6.5 M: only the bottom half of a curve with EC50 10^-6
    y = [logistic(x, 0, 100, -6.0, 1.0) + e for e in (1.0, -1.0, 0.5)]
    result = base.get("hill_3pl").run(series_inputs(x, y), Hill3Params())
    assert any("extrapolation" in w for w in result.warnings)


def test_the_3pl_table_shows_bottom_as_fixed() -> None:
    result = base.get("hill_3pl").run(series_inputs(CONC, noisy(6)), Hill3Params())
    rows = {r[0]: r[1] for r in result.tables[0].rows}
    assert rows["Bottom"] == 0.0 and rows["SE of Bottom"] == "(fixed)"


def test_log_x_input_gives_the_same_fit_as_concentrations() -> None:
    y = noisy(7)
    conc = base.get("hill_4pl").run(series_inputs(CONC, y), Hill4Params())
    logs = base.get("hill_4pl").run(series_inputs(np.log10(CONC), y), Hill4Params(x_is_log=True))
    a = {r[0]: r[1] for r in conc.tables[0].rows}
    b = {r[0]: r[1] for r in logs.tables[0].rows}
    assert a["EC50"] == pytest.approx(b["EC50"], rel=1e-8)
    assert not logs.curves[0].log_x


def test_polynomial_reports_every_coefficient() -> None:
    x = np.linspace(0, 5, 12)
    result = base.get("polynomial_regression").run(
        series_inputs(x, [1 + x - 0.5 * x**2 + 0.01 * np.cos(7 * x)]), PolynomialParams(degree=3)
    )
    labels = [r[0] for r in result.tables[0].rows]
    assert labels[1:5] == ["B0 (intercept)", "B1", "B2", "B3"]
    assert result.method == "Polynomial regression, degree 3 (least squares)"


def xy_table(x: list[float]) -> Table:
    return Table("T", [Column.new("x", "X", x), Column.new("y", "Y", [1.0] * len(x))])


def test_curve_fits_need_x_values() -> None:
    kind = base.get("linear_regression")
    assert kind.problem(xy_table([1.0, 2.0])) is None
    assert kind.problem(xy_table([math.nan, math.nan])) == "Needs an X column with values."


def test_run_stays_disabled_while_a_parameter_cannot_be_read() -> None:
    kind = base.get("hill_4pl")
    t = xy_table([1.0, 2.0])
    inputs = (kind.candidates(t)[0].id,)
    assert kind.check(t, inputs, Hill4Params()) is None
    assert "X range" in (kind.check(t, inputs, Hill4Params(x_range="5 to 1")) or "")
    assert "name = value" in (kind.check(t, inputs, Hill4Params(fixed="top 100")) or "")
