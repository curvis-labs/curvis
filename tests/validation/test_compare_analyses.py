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


# --- repeated-measures and two-way ANOVA (step 3.1) ---------------------------------


def run_rm(fixture: Fixture, design: str = "repeated", **params: object) -> Result | None:
    kind = base.get("one_way_anova")
    data = series(*fixture.inputs["treatments"])
    try:
        return kind.run(data, replace(kind.default_params(), design=design, **params))
    except AnalysisError:
        return None


@pytest.mark.parametrize("path", paths("rm_anova"), ids=ids)
@pytest.mark.parametrize("design", ["repeated", "repeated_sphericity"])
def test_rm_anova_analysis(path: Path, design: str) -> None:
    fixture = load_fixture(path)
    result = run_rm(fixture, design)
    sphericity = "gg" if design == "repeated" else "assume"
    p_key = "gg_p_value" if sphericity == "gg" else "p_value"
    if np.isnan(fixture.expected["p_value"]).all():
        assert result is None, f"{fixture.id}: R gives no P value, so the analysis should refuse"
        return
    assert result is not None, f"{fixture.id}: the analysis refused, R gives a P value"
    v = values_of(result)
    table = result.tables[0]
    got: dict[str, npt.ArrayLike] = {
        p_key: v["P value"],
        "f": v["F"],
        "gg_epsilon": v["Greenhouse-Geisser epsilon"],
        "ss_treatment": column(table, "SS")[0],
        "ss_subjects": column(table, "SS")[1],
        "ss_error": column(table, "SS")[2],
        "n_subjects": v["Number of subjects (rows)"],
    }
    if sphericity == "gg":
        got["gg_df_treatment"] = v["DF numerator"]
        got["gg_df_error"] = v["DF denominator"]
        got["p_value"] = v["P value, sphericity assumed"]
    else:
        got["df_treatment"] = v["DF numerator"]
        got["df_error"] = v["DF denominator"]
        got["gg_p_value"] = v["P value, Greenhouse-Geisser"]
    compare_subset(fixture, got)
    dropped = int(fixture.expected["n_incomplete"])
    assert any("left out" in w for w in result.warnings) == (dropped > 0)


def compare_subset(fixture: Fixture, got: Mapping[str, npt.ArrayLike]) -> None:
    wanted = {k: v for k, v in fixture.expected.items() if k in got}
    assert set(wanted) == set(got), set(got) - set(wanted)
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


RM_POSTHOC = {"rm_tukey": "tukey", "rm_dunnett": "dunnett", "rm_holm": "holm"}


@pytest.mark.parametrize(
    "path", [p for a in RM_POSTHOC for p in paths(a)], ids=lambda p: f"{p.parent.name}/{p.stem}"
)
def test_rm_posthoc_tables(path: Path) -> None:
    fixture = load_fixture(path)
    params: dict[str, object] = {"posthoc": RM_POSTHOC[fixture.analysis]}
    if fixture.analysis == "rm_dunnett":
        params["control"] = f"s{int(fixture.inputs['control'])}"
    result = run_rm(fixture, **params)
    got: dict[str, npt.ArrayLike] = {}
    if result is not None:
        table = result.tables[-1]
        got = {
            "difference": column(table, "Mean difference"),
            "p_value": column(table, "Adjusted P value"),
        }
        if fixture.analysis != "rm_holm":
            got["ci_lower"] = column(table, "Lower 95% CI")
            got["ci_upper"] = column(table, "Upper 95% CI")
        else:
            got["p_unadjusted"] = column(table, "Unadjusted P value")
    check(fixture, result, got)


LEVELS_B = ("Male", "Female", "Other", "Unknown")


def two_way_inputs(cells: list[list[Any]]) -> AnalysisInputs:
    """Each cell becomes a one-column series named by its level of A, in a category
    named by its level of B; the series are listed category by category, as in a table."""
    longest = max(len(np.atleast_1d(v)) for row in cells for v in row)
    out = []
    for j in range(len(cells[0])):
        for i, row in enumerate(cells):
            values = np.full(longest, np.nan)
            values[: len(np.atleast_1d(row[j]))] = np.atleast_1d(row[j])
            out.append(SeriesData(f"s{i}_{j}", NAMES[i], (values,), LEVELS_B[j]))
    return AnalysisInputs("T", longest, tuple(out), category_factor="Sex")


def run_two_way(fixture: Fixture) -> Result | None:
    kind = base.get("two_way_anova")
    try:
        return kind.run(two_way_inputs(fixture.inputs["cells"]), kind.default_params())
    except AnalysisError:
        return None


@pytest.mark.parametrize("path", paths("two_way_anova"), ids=ids)
def test_two_way_anova_analysis(path: Path) -> None:
    fixture = load_fixture(path)
    result = run_two_way(fixture)
    if np.isnan(fixture.expected["p_a"]).all():
        assert result is None, f"{fixture.id}: R gives no P value, so the analysis should refuse"
        return
    assert result is not None, f"{fixture.id}: the analysis refused, R gives a P value"
    table = result.tables[0]
    ss, f, p = column(table, "SS"), column(table, "F"), column(table, "P value")
    got: dict[str, npt.ArrayLike] = {
        "ss_a": ss[0],
        "ss_b": ss[1],
        "ss_interaction": ss[2],
        "ss_residual": ss[3],
        "f_a": f[0],
        "f_b": f[1],
        "f_interaction": f[2],
        "p_a": p[0],
        "p_b": p[1],
        "p_interaction": p[2],
        "df_residual": column(table, "DF")[3],
    }
    compare_subset(fixture, got)
    assert result.cells is not None
    compare_subset(fixture, {"cell_mean": np.ravel(result.cells.mean)})


@pytest.mark.parametrize(
    "path",
    [p for a in ("two_way_tukey_a", "two_way_tukey_b") for p in paths(a)],
    ids=lambda p: f"{p.parent.name}/{p.stem}",
)
def test_two_way_tukey_tables(path: Path) -> None:
    fixture = load_fixture(path)
    result = run_two_way(fixture)
    anova = load_fixture(path.parent.parent / "two_way_anova" / path.name)
    factor = "Group" if fixture.analysis.endswith("_a") else "Sex"
    p_factor = float(anova.expected["p_a" if factor == "Group" else "p_b"])
    if math.isnan(p_factor):
        assert result is None
        return
    assert result is not None
    found = [t for t in result.tables if t.title == f"Tukey's multiple comparisons test: {factor}"]
    if p_factor >= 0.05:
        assert not found, f"{fixture.id}: Tukey shown for a factor that is not significant"
        return
    (table,) = found
    compare_subset(
        fixture,
        {
            "difference": column(table, "Mean difference"),
            "q": column(table, "q"),
            "p_value": column(table, "Adjusted P value"),
            "ci_lower": column(table, "Lower 95% CI"),
            "ci_upper": column(table, "Upper 95% CI"),
        },
    )
