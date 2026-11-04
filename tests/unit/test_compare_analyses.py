"""The t test, one-way ANOVA and Mann-Whitney analyses: what the Results panel shows."""

from dataclasses import replace

import numpy as np
import pytest

from curvis.analysis import base
from curvis.analysis.base import (
    AnalysisError,
    AnalysisInputs,
    SeriesData,
    params_from_json,
    params_to_json,
)
from curvis.analysis.compare.anova_analysis import AnovaParams
from curvis.analysis.compare.ttest_analysis import TTestParams
from curvis.analysis.result import Result, Value, grids
from curvis.data import Column, Table


def inputs(
    *groups: tuple[str, list[float]] | tuple[str, list[float], list[float]],
) -> AnalysisInputs:
    """One series per group, named by its first item; extra lists are replicate columns."""
    series = []
    for name, *columns in groups:
        arrays = tuple(np.array(c, dtype=np.float64) for c in columns)
        series.append(SeriesData(name.lower(), name, arrays))
    return AnalysisInputs("T", len(series[0].columns[0]), tuple(series))


def value(result: Result, label: str) -> object:
    found: Value = next(v for v in result.values if v.label == label)
    return found.value


A = ("Control", [4.1, 5.2, 4.8, 5.0, 4.6, 5.3])
B = ("Drug", [5.9, 6.4, 5.7, 6.8, 6.1, 6.6])
C = ("Other", [4.9, 5.1, 5.6, 5.4, 5.0, 5.2])


def run(type_id: str, data: AnalysisInputs, **params: object) -> Result:
    kind = base.get(type_id)
    return kind.run(data, replace(kind.default_params(), **params))


# --- t test ----------------------------------------------------------------------------


def test_t_test_defaults_to_welch_and_names_the_method_exactly() -> None:
    assert TTestParams().test == "welch"
    result = run("t_test", inputs(A, B))
    assert result.method == "Unpaired t test with Welch's correction"
    assert run("t_test", inputs(A, B), test="student").method == "Unpaired t test"
    assert run("t_test", inputs(A, B), test="paired").method == "Paired t test"


def test_t_test_reports_the_difference_b_minus_a_with_a_plain_reading() -> None:
    result = run("t_test", inputs(A, B))
    difference = value(result, "Difference (Drug − Control)")
    assert difference == pytest.approx(np.mean(B[1]) - np.mean(A[1]))
    assert result.summary.startswith(
        "The mean of Drug is 1.417 higher than the mean of Control (95% CI 0.8605 to 1.973)"
    )
    assert "statistically significant (P < 0.001)" in result.summary
    assert result.warnings == ()


def test_t_test_warns_about_few_values_and_no_variation() -> None:
    result = run("t_test", inputs(("Low", [2.0, 2.0]), ("High", [3.0, 4.0, 5.5])))
    assert any("Low has only 2 values" in w for w in result.warnings)
    assert any("All values in Low are the same (SD = 0)" in w for w in result.warnings)


def test_t_test_that_is_not_defined_says_what_to_change() -> None:
    with pytest.raises(AnalysisError, match="do not vary within either group"):
        run("t_test", inputs(("A", [2.0, 2.0, 2.0]), ("B", [3.0, 3.0, 3.0])))
    with pytest.raises(AnalysisError, match="at least 2 values in each group"):
        run("t_test", inputs(("A", [2.0, np.nan]), ("B", [3.0, 4.0])))
    with pytest.raises(AnalysisError, match="differences between the pairs are all the same"):
        run("t_test", inputs(("A", [1.0, 2.0, 3.0]), ("B", [2.0, 3.0, 4.0])), test="paired")


def test_paired_t_test_pairs_replicate_columns_value_by_value() -> None:
    data = inputs(("Before", [1.0, 2.0], [3.0, 4.5]), ("After", [1.5, 2.2], [3.9, 4.6]))
    result = run("t_test", data, test="paired")
    assert value(result, "Number of pairs") == 4
    assert value(result, "Difference (After − Before)") == pytest.approx(0.425)


# --- one-way ANOVA ---------------------------------------------------------------------


def test_anova_with_tukey_by_default() -> None:
    assert AnovaParams().posthoc == "tukey"
    result = run("one_way_anova", inputs(A, B, C))
    assert result.method == "Ordinary one-way ANOVA"
    titles = [t.title for t in result.tables]
    assert titles == ["ANOVA table", "Groups", "Tukey's multiple comparisons test"]
    posthoc = result.tables[-1]
    assert [row[0] for row in posthoc.rows] == [
        "Drug − Control",
        "Other − Control",
        "Other − Drug",
    ]
    assert result.summary.startswith("The group means differ significantly (F(2, 15) = ")
    assert "comparisons are significant in Tukey's multiple comparisons test." in result.summary


def test_dunnett_compares_with_the_chosen_control() -> None:
    result = run("one_way_anova", inputs(A, B, C), posthoc="dunnett", control="other")
    posthoc = result.tables[-1]
    assert posthoc.title == "Dunnett's multiple comparisons test"
    assert [row[0] for row in posthoc.rows] == ["Control − Other", "Drug − Other"]


def test_dunnett_without_a_control_uses_the_first_group() -> None:
    for control in ("", "unticked"):
        result = run("one_way_anova", inputs(A, B, C), posthoc="dunnett", control=control)
        assert [row[0] for row in result.tables[-1].rows] == ["Drug − Control", "Other − Control"]


def test_holm_shows_unadjusted_and_adjusted_p_values() -> None:
    result = run("one_way_anova", inputs(A, B, C), posthoc="holm")
    posthoc = result.tables[-1]
    assert posthoc.title == "Pairwise t tests with Holm's correction"
    headers = grids(result)[-1].header
    assert "Unadjusted P value" in headers and "Adjusted P value" in headers
    for row in posthoc.rows:
        assert row[4] >= row[3]  # type: ignore[operator]


def test_anova_warnings_and_errors() -> None:
    result = run("one_way_anova", inputs(("A", [1.0, 2.0]), ("B", [3.0, 3.0, 3.0]), C))
    assert any("A has only 2 values" in w for w in result.warnings)
    assert any("All values in B are the same" in w for w in result.warnings)
    with pytest.raises(AnalysisError, match="do not vary within any group"):
        run("one_way_anova", inputs(("A", [1.0, 1.0]), ("B", [2.0, 2.0])))
    with pytest.raises(AnalysisError, match="only one value"):
        run("one_way_anova", inputs(("A", [1.0]), ("B", [2.0])))
    with pytest.raises(AnalysisError, match="B has no values"):
        run("one_way_anova", inputs(("A", [1.0, 2.0]), ("B", [np.nan, np.nan])))


def test_anova_params_store_the_control_by_id() -> None:
    params = AnovaParams(posthoc="dunnett", control="col-7")
    assert params_from_json(AnovaParams, params_to_json(params)) == params


# --- Mann-Whitney ----------------------------------------------------------------------


def test_mann_whitney_states_the_method() -> None:
    result = run("mann_whitney", inputs(A, B))
    assert result.method == "Mann-Whitney test"
    assert value(result, "P value method") == "Exact"
    assert result.notes == ("The P value is exact.",)
    assert "tend to be higher than those of Control" in result.summary
    tied = run("mann_whitney", inputs(("A", [1.0, 2.0, 2.0, 3.0]), ("B", [2.0, 3.0, 4.0])))
    assert tied.notes == ("The P value is exact, computed for the tied values.",)
    rng = np.random.default_rng(1)
    big = inputs(("A", list(rng.normal(0, 1, 60))), ("B", list(rng.normal(0, 1, 60))))
    result = run("mann_whitney", big)
    assert value(result, "P value method") == "Normal approximation with continuity correction"
    assert "because a group has 50 or more values" in result.notes[0]


# --- availability ----------------------------------------------------------------------


def two_column_table() -> Table:
    return Table(
        "T",
        [Column.new("A", "Y", [1, 2, 3], id="a"), Column.new("B", "Y", [2, 3, 4], id="b")],
    )


def test_comparisons_need_two_series() -> None:
    one = Table("T", [Column.new("A", "Y", [1, 2, 3])])
    for type_id in ("t_test", "one_way_anova", "mann_whitney"):
        assert not base.get(type_id).can_run(one)
        assert base.get(type_id).can_run(two_column_table())


def test_two_group_tests_need_exactly_two_ticked() -> None:
    table = two_column_table()
    kind = base.get("t_test")
    assert kind.check(table, ["a"], kind.default_params()) == "Tick exactly 2 series to compare."
    assert kind.check(table, ["a", "b"], kind.default_params()) is None
    assert "Welch's correction of B against A" in kind.describe(
        table, ["a", "b"], kind.default_params()
    )
