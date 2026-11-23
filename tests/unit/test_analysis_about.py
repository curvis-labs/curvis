"""The text, equations and sketches of *About this analysis* (step 3.8), as data on the
analysis types."""

from __future__ import annotations

import dataclasses
import math
import time

import numpy as np
import pytest

from curvis.analysis import base
from curvis.analysis.base import AnalysisError, Family
from curvis.analysis.fit.analyses import CurveFit, PolynomialParams
from curvis.analysis.fit.nonlinear import name_key
from curvis.analysis.fit.sketch import SKETCH_POINTS
from curvis.formula import compile_formula
from curvis.formula.constants import BUILTIN_VALUES
from curvis.formula.display import render_equation, split_equation

SECTIONS = {
    Family.FIT: ["Linear", "Dose-response", "Kinetics", "Shape-based"],
    Family.COMPARE: ["Describe", "Two groups", "Three or more groups"],
    Family.SURVIVAL: ["Survival"],
}
NO_HYPOTHESIS = {"descriptive_statistics", "kaplan_meier"}


def fits() -> list[CurveFit]:
    return [t for t in base.types() if isinstance(t, CurveFit)]


def fit_params(kind: CurveFit) -> list[base.Params]:
    """The default parameters, and for the polynomial every degree."""
    if kind.id == "polynomial_regression":
        return [PolynomialParams(degree=d) for d in range(2, 7)]
    return [kind.default_params()]


def test_every_analysis_has_a_section_of_its_family_and_text() -> None:
    for kind in base.types():
        assert kind.section in SECTIONS[kind.family], kind.id
        assert kind.about.reports, kind.id
        assert kind.about.use_when.startswith("Use when "), kind.id
        assert kind.about.use_when.endswith("."), kind.id


def test_every_section_has_an_analysis() -> None:
    for family, sections in SECTIONS.items():
        assert sorted({t.section for t in base.in_family(family)}) == sorted(sections)


def test_every_test_states_its_null_hypothesis_and_assumptions_for_every_option() -> None:
    for kind in base.in_family(Family.COMPARE) + base.in_family(Family.SURVIVAL):
        about = kind.about
        values: list[str] = [""]
        if about.option:
            info = dict((f.name, i) for f, i in base.param_fields(kind.params_type))
            values = [v for v, _label in info[about.option].choices]
            assert sorted(about.by_option) == sorted(values), kind.id
        for value in values:
            params = kind.default_params()
            if about.option:
                params = dataclasses.replace(params, **{about.option: value})  # type: ignore[arg-type]
            hypothesis = about.hypothesis_for(params)
            if kind.id == "descriptive_statistics":
                assert hypothesis is None
                continue
            assert hypothesis is not None and hypothesis.assumes.endswith("."), kind.id
            if kind.id in NO_HYPOTHESIS:
                assert hypothesis.null == ""
            else:
                assert hypothesis.null.endswith("."), (kind.id, value)


def test_the_t_test_hypothesis_follows_the_test_type() -> None:
    kind = base.get("t_test")
    paired = kind.about.hypothesis_for(dataclasses.replace(kind.default_params(), test="paired"))  # type: ignore[call-arg]
    assert paired is not None and paired.null == "The mean of the differences is 0."
    welch = kind.about.hypothesis_for(kind.default_params())
    assert welch is not None and "the SDs may differ" in welch.assumes


# --- equations --------------------------------------------------------------------------


def evaluate_equation(text: str, values: dict[str, float], x: float) -> float:
    """``text`` evaluated by the formula compiler, parameters matched to the model's by
    name (LogEC50_1 is log_ec50_1)."""
    left, formula = split_equation(text)
    assert left == "Y"
    by_key = {name_key(k): v for k, v in values.items()}
    constants: dict[str, float] = {}
    for name in formula.constants:
        if name == "X":
            constants[name] = x
        elif name in BUILTIN_VALUES:
            constants[name] = BUILTIN_VALUES[name]
        else:
            constants[name] = by_key[name_key(name)]
    return float(compile_formula(formula)({}, constants, 1)[0])


@pytest.mark.parametrize("kind", fits(), ids=lambda k: k.id)
def test_the_equation_shown_is_the_model_that_is_fitted(kind: CurveFit) -> None:
    rng = np.random.default_rng(3)
    for params in fit_params(kind):
        sketch = kind.sketch_for(params)
        assert sketch is not None
        text = kind.equation_for(params)
        low, high = sketch.x_range
        for _ in range(3):
            # Example values moved a little, so that no parameter is 0 or 1 by chance.
            values = {k: v + float(rng.uniform(0.1, 0.4)) for k, v in sketch.values.items()}
            x = np.linspace(low, high, 7)
            expected = kind.curve(x, values, params)
            got = np.array([evaluate_equation(text, values, float(xi)) for xi in x])
            np.testing.assert_allclose(got, expected, rtol=1e-12, atol=1e-12)


def test_every_parameter_is_in_the_equation() -> None:
    for kind in fits():
        for params in fit_params(kind):
            _left, formula = split_equation(kind.equation_for(params))
            sketch = kind.sketch_for(params)
            assert sketch is not None
            used = {name_key(n) for n in formula.constants} - {"x", "e"}
            assert used == {name_key(n) for n in sketch.values}, kind.id


def test_equations_render_as_a_textbook_writes_them() -> None:
    hill = base.get("hill_4pl")
    assert isinstance(hill, CurveFit)
    assert render_equation(hill.equation) == (
        "Y = Bottom + (Top − Bottom)&nbsp;/&nbsp;(1 + 10<sup>(LogEC50&nbsp;−&nbsp;X)"
        "&nbsp;·&nbsp;HillSlope</sup>)"
    )
    assert render_equation("Y = a - (b - c) + (d + e) - f*(g/h)") == (
        "Y = a − (b − c) + d + e − f&nbsp;·&nbsp;g&nbsp;/&nbsp;h"
    )
    assert render_equation("Y = (a/b)/c + a/(b/c) + -x^2 + (-x)^2") == (
        "Y = a&nbsp;/&nbsp;b&nbsp;/&nbsp;c + a&nbsp;/&nbsp;(b&nbsp;/&nbsp;c) + −x<sup>2</sup>"
        " + (−x)<sup>2</sup>"
    )
    assert render_equation("Y = Plateau_1 + K_on") == "Y = Plateau<sub>1</sub> + K<sub>on</sub>"


def test_an_equation_is_escaped_and_never_run() -> None:
    with pytest.raises(ValueError, match="written 'Y = …'"):
        render_equation("__import__('os')")
    from curvis.formula import FormulaError

    with pytest.raises(FormulaError):
        render_equation("Y = __import__('os').system('true')")


# --- sketches -----------------------------------------------------------------------------


@pytest.mark.parametrize("kind", fits(), ids=lambda k: k.id)
def test_a_sketch_is_computed_quickly_from_the_model(kind: CurveFit) -> None:
    for params in fit_params(kind):
        kind.sketch_data(params)  # imports the model's module once
        start = time.perf_counter()
        data = kind.sketch_data(params)
        elapsed = time.perf_counter() - start
        assert data is not None and len(data.x) == SKETCH_POINTS
        assert np.all(np.isfinite(data.y)), kind.id
        np.testing.assert_allclose(
            data.y, kind.curve(data.x, kind.sketch_for(params).values, params)
        )  # type: ignore[union-attr]
        assert elapsed < 0.005, f"{kind.id}: {elapsed * 1000:.1f} ms"


def test_a_fixed_value_replaces_its_example_value_in_the_sketch() -> None:
    kind = base.get("hill_4pl")
    assert isinstance(kind, CurveFit)
    params = dataclasses.replace(kind.default_params(), fixed="Top = 50, LogEC50 = -3")  # type: ignore[call-arg]
    data = kind.sketch_data(params)
    assert data is not None
    top = next(m for m in data.markers if m.label.startswith("Top"))
    assert top.position == 50 and top.fixed and top.label == "Top = 50 (fixed)"
    assert math.isclose(float(np.max(data.y)), 50, rel_tol=1e-3)
    ec50 = next(m for m in data.markers if m.kind == "v")
    assert ec50.position == -3 and ec50.fixed and ec50.label == "LogEC50 = −3 (fixed)"
    assert data.x[0] == -6 and data.x[-1] == 0  # the X range follows the LogEC50
    bottom = next(m for m in data.markers if m.label == "Bottom")
    assert not bottom.fixed


def test_a_fixed_text_that_cannot_be_read_draws_the_example_curve() -> None:
    kind = base.get("michaelis_menten")
    assert isinstance(kind, CurveFit)
    params = dataclasses.replace(kind.default_params(), fixed="top = 100")  # type: ignore[call-arg]
    with pytest.raises(AnalysisError):
        kind.fixed_values(params)
    data = kind.sketch_data(params)
    assert data is not None and not any(m.fixed for m in data.markers)


def test_a_half_life_marker_is_left_out_when_k_is_not_positive() -> None:
    kind = base.get("exp_decay")
    assert isinstance(kind, CurveFit)
    params = dataclasses.replace(kind.default_params(), fixed="K = -0.1")  # type: ignore[call-arg]
    data = kind.sketch_data(params)
    assert data is not None
    assert [m.label for m in data.markers] == ["Plateau"]


# --- the Fix table writes what the parsers read ----------------------------------------------


@pytest.mark.parametrize(
    "kind", [k for k in fits() if k.parameters(k.default_params())], ids=lambda k: k.id
)
def test_every_parameter_name_the_table_writes_can_be_read(kind: CurveFit) -> None:
    params = kind.default_params()
    for name, label in kind.parameters(params):
        text = f"{label} = 1.5"
        assert kind.fixed_values(dataclasses.replace(params, fixed=text)) == {name: 1.5}  # type: ignore[call-arg]
