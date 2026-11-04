"""The t test, one-way ANOVA and Mann-Whitney analyses, run as the app runs them, against
the same R fixtures as the statistics functions (test_fixtures.py).

The fixture's groups become series of one column each; the numbers are read back from
the Result the Results panel shows. Where R's P value is undefined (NaN), the analysis
must refuse to run with a message (AnalysisError) instead.
"""

import math
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest

from curvis.analysis import base
from curvis.analysis.base import AnalysisError, AnalysisInputs, SeriesData
from curvis.analysis.result import Result, ResultTable
from tests.validation.harness import Fixture, compare, fixture_paths, format_report, load_fixture

NAMES = "ABCDEFGHIJ"


def series(*groups: npt.ArrayLike) -> AnalysisInputs:
    longest = max(len(np.atleast_1d(g)) for g in groups)
    out = []
    for i, g in enumerate(groups):
        values = np.full(longest, np.nan)
        values[: len(np.atleast_1d(g))] = np.atleast_1d(g)
        out.append(SeriesData(f"s{i}", NAMES[i], (values,)))
    return AnalysisInputs("T", longest, tuple(out))


def values_of(result: Result) -> dict[str, Any]:
    return {v.label: v.value for v in result.values}


def column(table: ResultTable, label: str) -> list[Any]:
    index = [c.label for c in table.columns].index(label)
    return [row[index] for row in table.rows]


def run(fixture: Fixture, type_id: str, **params: object) -> Result | None:
    kind = base.get(type_id)
    inputs = fixture.inputs
    groups = inputs["groups"] if "groups" in inputs else [inputs["a"], inputs["b"]]
    data = series(*groups)
    try:
        return kind.run(data, replace(kind.default_params(), **params))
    except AnalysisError:
        return None


def check(fixture: Fixture, result: Result | None, got: Mapping[str, npt.ArrayLike]) -> None:
    expected_p = fixture.expected["p_value"]
    if np.isnan(expected_p).all():
        assert result is None, f"{fixture.id}: R gives no P value, so the analysis should refuse"
        return
    assert result is not None, f"{fixture.id}: the analysis refused, R gives a P value"
    wanted = {k: v for k, v in fixture.expected.items() if k in got}
    trimmed = Fixture(
        fixture.path,
        fixture.analysis,
        fixture.case,
        fixture.description,
        fixture.generated_by,
        fixture.inputs,
        wanted,
        {k: fixture.kinds[k] for k in wanted},
    )
    mismatches = compare(trimmed, got)
    if mismatches:
        pytest.fail(format_report(trimmed, mismatches), pytrace=False)


def paths(analysis: str) -> list[Path]:
    return [p for p in fixture_paths() if p.parent.name == analysis]


def ids(path: Path) -> str:
    return path.stem


@pytest.mark.parametrize("path", paths("t_test"), ids=ids)
def test_t_test_analysis(path: Path) -> None:
    fixture = load_fixture(path)
    inputs = fixture.inputs
    test = "paired" if inputs["paired"] else ("welch" if inputs["welch"] else "student")
    result = run(fixture, "t_test", test=test)
    got: dict[str, npt.ArrayLike] = {}
    if result is not None:
        v = values_of(result)
        got = {
            "p_value": [v["P value"]],
            "t": [v["t"]],
            "difference": [v["Difference (B − A)"]],
            "ci_lower": [v["Lower 95% CI of difference"]],
            "ci_upper": [v["Upper 95% CI of difference"]],
        }
    check(fixture, result, got)


@pytest.mark.parametrize("path", paths("one_way_anova"), ids=ids)
def test_one_way_anova_analysis(path: Path) -> None:
    fixture = load_fixture(path)
    result = run(fixture, "one_way_anova")
    got: dict[str, npt.ArrayLike] = {}
    if result is not None:
        v = values_of(result)
        table = result.tables[0]
        got = {
            "p_value": v["P value"],
            "f": v["F"],
            "ss_between": column(table, "SS")[0],
            "ss_within": column(table, "SS")[1],
        }
    check(fixture, result, got)


POSTHOC = {"tukey_hsd": "tukey", "dunnett": "dunnett", "holm_pairwise": "holm"}


@pytest.mark.parametrize(
    "path", [p for a in POSTHOC for p in paths(a)], ids=lambda p: f"{p.parent.name}/{p.stem}"
)
def test_posthoc_tables(path: Path) -> None:
    fixture = load_fixture(path)
    params: dict[str, object] = {"posthoc": POSTHOC[fixture.analysis]}
    if fixture.analysis == "dunnett":
        params["control"] = f"s{int(fixture.inputs['control'])}"
    result = run(fixture, "one_way_anova", **params)
    got: dict[str, npt.ArrayLike] = {}
    if result is not None:
        table = result.tables[-1]
        got = {
            "difference": column(table, "Mean difference"),
            "p_value": column(table, "Adjusted P value"),
        }
        if fixture.analysis != "holm_pairwise":
            got["ci_lower"] = column(table, "Lower 95% CI")
            got["ci_upper"] = column(table, "Upper 95% CI")
        else:
            got["p_unadjusted"] = column(table, "Unadjusted P value")
    check(fixture, result, got)


@pytest.mark.parametrize("path", paths("mann_whitney"), ids=ids)
def test_mann_whitney_analysis(path: Path) -> None:
    fixture = load_fixture(path)
    result = run(fixture, "mann_whitney")
    got: dict[str, npt.ArrayLike] = {}
    if result is not None:
        v = values_of(result)
        exact = v["P value method"] == "Exact"
        got = {"p_value": v["P value"], "u": v["Mann-Whitney U"], "exact": int(exact)}
        assert math.isfinite(v["P value"])
    check(fixture, result, got)
