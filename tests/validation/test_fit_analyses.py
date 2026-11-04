"""The curve-fit analyses, run as the app runs them, against the same R fixtures as the
fit functions (test_fixtures.py).

The fixture's X and Y columns become a table's X column and one series; the numbers are
read back from the "Best-fit values" table the Results panel shows. A series that does
not fit shows empty cells, which compare as NaN. The confidence bands are checked in
test_fixtures.py (the analysis evaluates them on a grid for graphs).
"""

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest

from curvis.analysis import base
from curvis.analysis.base import AnalysisInputs, SeriesData
from curvis.analysis.fit.analyses import Hill3Params, Hill4Params, LinearParams, PolynomialParams
from curvis.analysis.result import Result
from tests.validation.harness import Fixture, compare, fixture_paths, format_report, load_fixture

LINEAR = [p for p in fixture_paths() if p.parent.name == "linear_fit"]
DOSE = [p for p in fixture_paths() if p.parent.name == "dose_response"]


def inputs_of(fixture: Fixture) -> AnalysisInputs:
    x = np.asarray(fixture.inputs["x"], dtype=np.float64)
    columns = tuple(np.asarray(c, dtype=np.float64) for c in fixture.inputs["y"])
    return AnalysisInputs("T", len(x), (SeriesData("s", "A", columns),), x, "X")


def range_text(inputs: Mapping[str, Any]) -> str:
    low, high = float(inputs["x_min"]), float(inputs["x_max"])
    if math.isinf(low) and math.isinf(high):
        return ""
    return f"{'' if math.isinf(low) else repr(low)} to {'' if math.isinf(high) else repr(high)}"


def rows(result: Result) -> dict[str, Any]:
    (table,) = result.tables
    assert table.title == "Best-fit values"
    return {row[0]: row[1] for row in table.rows}


def number(cell: Any) -> float:
    if cell is None or (isinstance(cell, str) and not cell.strip("-").isdigit()):
        return math.nan
    return float(cell)


def ids(path: Path) -> str:
    return path.stem


@pytest.mark.parametrize("path", LINEAR, ids=ids)
def test_linear_analyses_match_r(path: Path) -> None:
    fixture = load_fixture(path)
    degree = int(fixture.inputs["degree"])
    x_range = range_text(fixture.inputs)
    if degree == 1:
        kind, params = base.get("linear_regression"), LinearParams(x_range=x_range)
        labels = ["Y-intercept", "Slope"]
    else:
        kind = base.get("polynomial_regression")
        params = PolynomialParams(x_range=x_range, degree=degree)
        labels = ["B0 (intercept)"] + [f"B{i}" for i in range(1, degree + 1)]
    r = rows(kind.run(inputs_of(fixture), params))
    ok = r["Fit"] == "OK"
    got: dict[str, npt.ArrayLike] = {
        "fit_ok": [ok],
        "n": [number(r["Number of points"])],
        "df": [number(r["Degrees of freedom"])],
        "coefficients": [number(r[label]) for label in labels],
        "se": [number(r[f"SE of {label}"]) for label in labels],
        "ci_lower": [number(r[f"Lower 95% CI of {label}"]) for label in labels],
        "ci_upper": [number(r[f"Upper 95% CI of {label}"]) for label in labels],
        "r_squared": [number(r["R²"])],
        "sy_x": [number(r["Sy.x"])],
    }
    expected = {k: v for k, v in fixture.expected.items() if not k.startswith("band_")}
    mismatches = compare(_only(fixture, expected), got)
    if mismatches:
        pytest.fail(format_report(fixture, mismatches), pytrace=False)


@pytest.mark.parametrize("path", DOSE, ids=ids)
def test_dose_response_analyses_match_r(path: Path) -> None:
    fixture = load_fixture(path)
    three = fixture.inputs["model"] == "3pl"
    common = {"x_range": range_text(fixture.inputs), "x_is_log": fixture.inputs["x_is_log"]}
    if three:
        kind, params = base.get("hill_3pl"), Hill3Params(**common)
        names = {"top": "Top", "log_ec50": "LogEC50", "hill_slope": "Hill slope"}
    else:
        kind, params = base.get("hill_4pl"), Hill4Params(**common)
        names = {
            "bottom": "Bottom",
            "top": "Top",
            "log_ec50": "LogEC50",
            "hill_slope": "Hill slope",
        }
    result = kind.run(inputs_of(fixture), params)
    r = rows(result)
    ok = r["Fit"] == "OK"
    got: dict[str, npt.ArrayLike] = {
        "fit_ok": [ok],
        "n": [number(r["Number of points"])],
        "df": [number(r["Degrees of freedom"]) if ok else math.nan],
        "ec50": [number(r["EC50"])],
        "ec50_ci_lower": [number(r["Lower 95% CI of EC50"])],
        "ec50_ci_upper": [number(r["Upper 95% CI of EC50"])],
        "r_squared": [number(r["R²"])],
        "sy_x": [number(r["Sy.x"])],
    }
    for key, label in names.items():
        got[key] = [number(r[label])]
        got[f"{key}_se"] = [number(r[f"SE of {label}"])]
        got[f"{key}_ci_lower"] = [number(r[f"Lower 95% CI of {label}"])]
        got[f"{key}_ci_upper"] = [number(r[f"Upper 95% CI of {label}"])]
    expected = {k: v for k, v in fixture.expected.items() if not k.startswith("band_")}
    mismatches = compare(_only(fixture, expected), got)
    if mismatches:
        pytest.fail(format_report(fixture, mismatches), pytrace=False)
    if ok:
        assert result.curves and result.curves[0].series_id == "s"
    else:
        assert not result.curves and result.warnings


def _only(fixture: Fixture, expected: dict[str, Any]) -> Fixture:
    from dataclasses import replace

    return replace(fixture, expected=expected, kinds={k: fixture.kinds[k] for k in expected})
