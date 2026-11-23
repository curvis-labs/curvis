"""The nonlinear curves of docs/models.md: analytic Jacobians, reported forms, derived
values, automatic initial values and the engine rules the R fixtures rely on (a
parameter at rounding level, constant data). The fits themselves are validated
against R in tests/validation."""

import math

import numpy as np
import numpy.typing as npt
import pytest

from curvis.analysis import base
from curvis.analysis.base import AnalysisInputs, SeriesData
from curvis.analysis.fit.engine import (
    Constraints,
    FitModel,
    FitStatus,
    NonlinearFit,
    _jacobian,
    fit_model,
)
from curvis.analysis.fit.kinetics import EXP_ASSOCIATION, EXP_DECAY, MICHAELIS_MENTEN
from curvis.analysis.fit.multiphase import BELL_SHAPED, BIPHASIC
from curvis.analysis.fit.nonlinear import (
    CurveSpec,
    Derived,
    best_linear,
    derived_value,
    fit_curve,
    name_key,
)
from curvis.analysis.fit.points import Points
from curvis.analysis.fit.shape import FWHM_FACTOR, GAUSSIAN
from curvis.analysis.result import Result
from curvis.data import Column, Table

type FloatArray = npt.NDArray[np.float64]

LOG_CONC = np.log10(10.0 ** np.arange(-10, -2.9, 0.5))

# (spec, X values, true parameters, noise SD): realistic designs, as in the R scripts.
CASES: dict[str, tuple[CurveSpec, FloatArray, FloatArray, float]] = {
    "biphasic": (BIPHASIC, np.repeat(LOG_CONC, 3), np.array([0, 100, 0.4, -8, -5, 1, 1.0]), 5),
    "bell_shaped": (
        BELL_SHAPED,
        np.repeat(LOG_CONC, 3),
        np.array([0, 100, 10, -8, -5, 1, 1.0]),
        5,
    ),
    "exp_association": (
        EXP_ASSOCIATION,
        np.repeat([0, 0.5, 1, 2, 3, 5, 7, 10, 15, 20, 30.0], 2),
        np.array([5, 80, 0.2]),
        7.5,
    ),
    "exp_decay": (
        EXP_DECAY,
        np.repeat([0, 1, 2, 3, 4, 6, 8, 10, 15, 20, 30.0], 2),
        np.array([100, 10, 0.15]),
        9,
    ),
    "michaelis_menten": (
        MICHAELIS_MENTEN,
        np.repeat([0.25, 0.5, 1, 2, 4, 8, 16, 32, 64.0], 2),
        np.array([120, 6.0]),
        12,
    ),
    "gaussian": (GAUSSIAN, np.repeat(np.arange(0, 21.0), 2), np.array([5, 50, 10, 2.5]), 5),
}
KEYS = list(CASES)


def simulate(key: str, seed: int) -> Points:
    spec, x, truth, sd = CASES[key]
    rng = np.random.default_rng(seed)
    return Points(x, spec.model.f(x, truth) + rng.normal(0, sd, len(x)))


# --- models -----------------------------------------------------------------------------


@pytest.mark.parametrize("key", KEYS)
def test_the_analytic_jacobian_matches_central_differences(key: str) -> None:
    spec, x, truth, _sd = CASES[key]
    p = truth * 1.07 + 0.03  # away from round values
    analytic = spec.model.jacobian(x, p)

    class Numeric(FitModel):
        parameters = spec.model.parameters

        def f(self, x: FloatArray, p: FloatArray) -> FloatArray:
            return spec.model.f(x, p)

    numeric = _jacobian(Numeric(), x, p)
    assert analytic is not None
    assert np.allclose(analytic, numeric, rtol=1e-6, atol=1e-7 * np.max(np.abs(numeric)))


@pytest.mark.parametrize("spec", [BIPHASIC, BELL_SHAPED])
def test_concentration_zero_is_the_limit_of_the_curve(spec: CurveSpec) -> None:
    p = CASES[spec.key][2]
    at_zero = spec.model.f(np.array([-np.inf]), p)
    far_left = spec.model.f(np.array([-30.0]), p)
    assert np.allclose(at_zero, far_left)
    jac = spec.model.jacobian(np.array([-np.inf]), p)
    assert jac is not None and np.all(np.isfinite(jac))


def test_association_and_decay_are_the_same_family() -> None:
    x = np.linspace(0, 30, 31)
    p = np.array([5.0, 80.0, 0.2])
    assert np.allclose(EXP_ASSOCIATION.model.f(x, p), EXP_DECAY.model.f(x, p))


def test_biphasic_is_reported_with_top_above_bottom_and_phases_in_order() -> None:
    model = BIPHASIC.model
    p = np.array([100.0, 0.0, 0.3, -5.0, -8.0, -1.0, -1.5])
    q = model.canonical(p, (True,) * 7)
    assert q[1] >= q[0] and q[3] <= q[4]
    assert q[2] == pytest.approx(0.7)
    assert np.allclose(model.f(LOG_CONC, p), model.f(LOG_CONC, q))


def test_biphasic_phases_are_not_swapped_when_only_one_slope_is_fixed() -> None:
    p = np.array([0.0, 100.0, 0.3, -5.0, -8.0, 1.0, 2.0])
    free = (True, True, True, True, True, False, True)
    assert np.array_equal(BIPHASIC.model.canonical(p, free), p)
    # Both fixed to the same value: the swap is the same curve, so it is made.
    both = (True, True, True, True, True, False, False)
    same = p.copy()
    same[6] = 1.0
    assert BIPHASIC.model.canonical(same, both)[3] == -8.0


def test_a_negative_gaussian_sd_is_reported_positive() -> None:
    q = GAUSSIAN.model.canonical(np.array([1.0, 2.0, 3.0, -4.0]), (True,) * 4)
    assert q[3] == 4.0


# --- automatic initial values ----------------------------------------------------------------


@pytest.mark.parametrize("key", KEYS)
def test_the_first_start_is_close_to_the_true_curve(key: str) -> None:
    spec, x, truth, _sd = CASES[key]
    points = simulate(key, 1)
    start = spec.model.starts(points.x, points.y)[0]
    rss_start = float(np.sum((points.y - spec.model.f(x, start)) ** 2))
    rss_truth = float(np.sum((points.y - spec.model.f(x, truth)) ** 2))
    # The grid is coarse, but the start is already a fair description of the data.
    assert rss_start < 6 * rss_truth


@pytest.mark.parametrize("key", KEYS)
def test_automatic_starts_reach_the_best_minimum_on_noisy_data(key: str) -> None:
    """A small seeded Monte-Carlo (the full one, 200 datasets per model and noise
    level, is reported for step 3.3): wherever a fit from the true parameters (or a
    perturbation of them) is OK, the fit from the automatic starts is OK too and at least
    as good. Data on which no fit is OK must fail cleanly (not tested here)."""
    spec, _x, truth, _sd = CASES[key]
    rng = np.random.default_rng(7)
    determined = 0
    for seed in range(20):
        points = simulate(key, 100 + seed)
        refs = []
        for i in range(4):
            noise = 0 if i == 0 else rng.normal(0, 0.1, len(truth)) * np.maximum(np.abs(truth), 1)
            refs.append(fit_model(spec.model, points, Constraints(), truth + noise))
        ok = [r.rss for r in refs if r.ok]
        if not ok:
            continue
        determined += 1
        f = fit_curve(spec, points)
        assert f.ok, f"seed {seed}: {f.message}"
        assert f.rss <= min(ok) * (1 + 1e-9), f"seed {seed}: a local minimum"
    assert determined >= 18


def test_starts_on_degenerate_x_are_finite() -> None:
    for spec in (BIPHASIC, BELL_SHAPED, EXP_DECAY, MICHAELIS_MENTEN, GAUSSIAN):
        x = np.full(4, 2.0)
        for start in spec.model.starts(x, np.array([1.0, 2.0, 3.0, 4.0])):
            assert np.all(np.isfinite(start))


def test_best_linear_solves_every_grid_point() -> None:
    rng = np.random.default_rng(3)
    basis = rng.normal(size=(5, 20, 2))
    y = rng.normal(size=20)
    coef, rss = best_linear(basis, y)
    for g in range(5):
        expected, res, *_ = np.linalg.lstsq(basis[g], y, rcond=None)
        assert np.allclose(coef[g], expected)
        assert rss[g] == pytest.approx(float(res[0]))
    basis[2, 0, 0] = np.nan
    assert best_linear(basis, y)[1][2] == np.inf


# --- engine rules ---------------------------------------------------------------------------


def test_constant_data_is_ambiguous() -> None:
    points = Points(np.arange(1.0, 10.0), np.full(9, 42.0))
    f = fit_curve(MICHAELIS_MENTEN, points)
    assert f.status is FitStatus.AMBIGUOUS
    assert "every Y value is the same" in f.message


def test_a_phase_with_no_span_makes_a_biphasic_fit_ambiguous() -> None:
    # Exact one-phase data: one phase of the best fit has a span of ~1e-16, so its
    # LogEC50 and slope change the curve only at rounding level.
    mono = np.array([0, 100, 1, -6.5, -6.5, 1, 1.0])
    points = Points(LOG_CONC, BIPHASIC.model.f(LOG_CONC, mono))
    f = fit_curve(BIPHASIC, points)
    assert f.status is FitStatus.AMBIGUOUS
    assert "not change the curve" in f.message
    # The warning names the parameters as the results show them.
    assert "LogEC50 " in f.message and "Hill slope " in f.message
    assert "_" not in f.message


def test_fit_warnings_name_the_parameters_as_the_results_show_them() -> None:
    # With Frac fixed at 1 the second phase has no effect on the curve.
    x = np.linspace(-9.0, -4.0, 12)
    y = 10 + 90 / (1 + 10 ** (-6.5 - x)) + np.sin(x)
    f = fit_curve(BIPHASIC, Points(x, y), {"frac": 1.0})
    assert f.status is FitStatus.AMBIGUOUS
    assert f.message == ("The fit is ambiguous: LogEC50 2, Hill slope 2 do not change the curve.")


def test_a_fixed_parameter_is_kept_and_the_others_fitted() -> None:
    points = simulate("gaussian", 2)
    f = fit_curve(GAUSSIAN, points, {"baseline": 5.0})
    assert f.ok and f.value("baseline") == 5.0 and math.isnan(f.se[0])
    assert f.value("center") == pytest.approx(10, abs=0.5)


# --- derived values ---------------------------------------------------------------------------


def fitted(key: str, seed: int = 4) -> NonlinearFit:
    spec = CASES[key][0]
    f = fit_curve(spec, simulate(key, seed))
    assert f.ok
    return f


def test_a_linear_derived_value_has_the_se_of_the_combination() -> None:
    f = fitted("gaussian")
    fwhm = derived_value(f, GAUSSIAN.derived[0])
    assert fwhm is not None
    assert fwhm.value == pytest.approx(FWHM_FACTOR * f.value("sd"))
    assert fwhm.se == pytest.approx(FWHM_FACTOR * float(f.se[3]))
    assert fwhm.ci_lower == pytest.approx(FWHM_FACTOR * float(f.ci_lower[3]))


def test_the_bell_amplitude_uses_the_covariances() -> None:
    f = fitted("bell_shaped")
    amplitude = derived_value(f, BELL_SHAPED.derived[4])
    assert amplitude is not None
    c = np.array([-0.5, 1.0, -0.5, 0, 0, 0, 0])
    assert amplitude.value == pytest.approx(float(c @ f.values))
    assert amplitude.se == pytest.approx(math.sqrt(float(c @ f.cov @ c)))


def test_half_life_ci_comes_from_the_ci_of_k() -> None:
    f = fitted("exp_decay")
    half = derived_value(f, EXP_DECAY.derived[1])
    assert half is not None and math.isnan(half.se)
    assert half.value == pytest.approx(math.log(2) / f.value("k"))
    assert half.ci_lower == pytest.approx(math.log(2) / float(f.ci_upper[2]))
    assert half.ci_upper == pytest.approx(math.log(2) / float(f.ci_lower[2]))


def test_half_life_is_open_ended_when_k_may_be_zero() -> None:
    f = fitted("exp_decay")
    i = 2
    ci_lower = f.ci_lower.copy()
    ci_lower[i] = -0.01
    wide = NonlinearFit(**{**f.__dict__, "ci_lower": ci_lower})
    half = derived_value(wide, EXP_DECAY.derived[1])
    assert half is not None and half.ci_upper == math.inf
    negative = NonlinearFit(**{**f.__dict__, "values": np.array([100.0, 10.0, -0.1])})
    none = derived_value(negative, EXP_DECAY.derived[1])
    assert none is not None and math.isnan(none.value)


def test_a_derived_value_of_a_fixed_parameter_is_not_reported() -> None:
    f = fit_curve(GAUSSIAN, simulate("gaussian", 2), {"sd": 2.5})
    assert derived_value(f, GAUSSIAN.derived[0]) is None
    f = fit_curve(EXP_DECAY, simulate("exp_decay", 2), {"k": 0.15})
    assert derived_value(f, Derived("t", "T", of="k", transform=abs)) is None


def test_parameter_names_are_matched_without_case_or_spacing() -> None:
    names = BIPHASIC.names()
    for written in ("Hill slope 1", "hill_slope_1", "HillSlope1", "nH1"):
        assert names[name_key(written)] == "hill_slope_1"
    assert GAUSSIAN.names()[name_key("centre")] == "center"


# --- analyses ------------------------------------------------------------------------------


def run(type_id: str, x: npt.ArrayLike, *columns: npt.ArrayLike, **params: object) -> Result:
    kind = base.get(type_id)
    x = np.asarray(x, dtype=np.float64)
    data = tuple(np.asarray(c, dtype=np.float64) for c in columns)
    inputs = AnalysisInputs("T", len(x), (SeriesData("s", "A", data),), x, "X")
    return kind.run(inputs, kind.params_type(**params))


def table_rows(result: Result) -> dict[str, object]:
    return {str(row[0]): row[1] for row in result.tables[0].rows}


def test_a_growing_exponential_is_reported_with_a_warning() -> None:
    t = np.arange(0, 10.0)
    result = run("exp_decay", t, 10 + 5 * np.exp(0.2 * t) + np.sin(t) * 0.1)
    r = table_rows(result)
    assert r["Fit"] == "OK" and float(r["K"]) < 0  # type: ignore[arg-type]
    assert math.isnan(float(r["Half-life"]))  # type: ignore[arg-type]
    assert any("K is negative" in w for w in result.warnings)


def test_a_slow_decay_warns_that_the_plateau_is_extrapolated() -> None:
    t = np.arange(0, 6.0)
    y = 10 + 90 * np.exp(-0.05 * t) + np.array([0.3, -0.2, 0.1, -0.3, 0.2, -0.1])
    result = run("exp_decay", t, y)
    assert any("longer than the X range" in w for w in result.warnings)


def test_km_above_the_highest_concentration_is_an_extrapolation() -> None:
    s = np.array([1, 2, 4, 8, 16.0])
    y = 500 * s / (100 + s) * (1 + 0.01 * np.array([1, -1, 1, -1, 1]))
    result = run("michaelis_menten", s, y)
    assert table_rows(result)["Fit"] == "OK"
    assert any("Km is above the highest substrate" in w for w in result.warnings)


def test_a_peak_outside_the_data_is_an_extrapolation() -> None:
    x = np.arange(0, 10.0)
    y = 50 * np.exp(-0.5 * ((x - 11) / 3) ** 2) + 0.2 * np.sin(x)
    result = run("gaussian", x, y)
    assert table_rows(result)["Fit"] == "OK"
    assert any("center of the peak is outside" in w for w in result.warnings)


def test_the_gaussian_reading_and_fwhm() -> None:
    points = simulate("gaussian", 3)
    x, y = points.x[::2], points.y[::2]
    result = run("gaussian", x, y)
    r = table_rows(result)
    assert float(r["FWHM"]) == pytest.approx(FWHM_FACTOR * float(r["SD"]))  # type: ignore[arg-type]
    assert result.summary.startswith("The peak of A is at ")
    assert result.method == "Gaussian fit (least squares)"
    assert "FWHM (full width at half maximum) = 2.3548 × SD." in result.notes


def test_the_bell_shaped_fit_reports_its_peak() -> None:
    conc = 10.0**LOG_CONC
    y = BELL_SHAPED.model.f(LOG_CONC, CASES["bell_shaped"][2])
    rng = np.random.default_rng(9)
    result = run("bell_shaped", conc, *(y + rng.normal(0, 3, len(y)) for _ in range(3)))
    r = table_rows(result)
    assert r["Fit"] == "OK"
    assert float(r["Peak center"]) == pytest.approx(-6.5, abs=0.2)  # type: ignore[arg-type]
    assert float(r["Peak width"]) == pytest.approx(3, abs=0.3)  # type: ignore[arg-type]
    assert "SE of Peak amplitude" in r and "SE of EC50 1" not in r
    assert result.summary.startswith("The curve of A peaks at a log concentration of ")


def test_fixed_parameters_are_listed_and_their_derived_values_left_out() -> None:
    points = simulate("exp_decay", 5)
    result = run("exp_decay", points.x[::2], points.y[::2], fixed="K = 0.15")
    r = table_rows(result)
    assert r["SE of K"] == "(fixed)" and "Half-life" not in r and "Span" in r
    assert "Fixed: K = 0.1500." in result.notes


@pytest.mark.parametrize(
    ("type_id", "text", "message"),
    [
        ("michaelis_menten", "top = 1", "where the name is Vmax or Km"),
        ("michaelis_menten", "km", 'Fix parameters as "name = value"'),
        ("gaussian", "sd = wide", "The value of sd must be a number"),
        ("gaussian", "sd =", "Give a value for sd"),
        ("exp_decay", "y0 = 1, plateau = 0, k = 1", "Leave at least one parameter free"),
    ],
)
def test_fixed_text_that_cannot_be_read_says_what_to_write(
    type_id: str, text: str, message: str
) -> None:
    kind = base.get(type_id)
    table = Table("T", [Column.new("x", "X", [1.0, 2.0, 3.0]), Column.new("y", "Y", [1.0, 2, 3])])
    problem = kind.check(table, ("y",), kind.params_type(fixed=text))  # type: ignore[call-arg]
    assert problem is not None and message in problem


def test_descriptions_name_what_is_reported() -> None:
    table = Table("T", [Column.new("x", "X", [1.0, 2.0, 3.0]), Column.new("y", "Y", [1.0, 2, 3])])
    kind = base.get("biphasic")
    text = kind.describe(table, ("y",), kind.default_params())
    assert text.startswith("Fits a biphasic dose-response curve to ")
    assert "both EC50s" in text
