"""Repeated-measures one-way ANOVA and two-way ANOVA: what the Results panel shows, the
checks before Run, and the two-way layout from category groups."""

from dataclasses import replace

import numpy as np
import pytest

from curvis.analysis import base
from curvis.analysis.base import (
    Analysis,
    AnalysisError,
    AnalysisInputs,
    SeriesData,
    params_from_json,
    params_to_json,
)
from curvis.analysis.compare.anova_analysis import AnovaParams
from curvis.analysis.compare.two_way_analysis import TwoWayParams, layout
from curvis.analysis.result import Result, Value
from curvis.data import CategoryGroup, Column, ColumnRole, ReplicateGroup, Table

NAN = float("nan")


def series_inputs(*groups: tuple[str, list[float]]) -> AnalysisInputs:
    data = tuple(
        SeriesData(name.lower(), name, (np.array(v, dtype=np.float64),)) for name, v in groups
    )
    return AnalysisInputs("T", len(groups[0][1]), data)


def value(result: Result, label: str) -> object:
    found: Value = next(v for v in result.values if v.label == label)
    return found.value


def run(type_id: str, data: AnalysisInputs, **params: object) -> Result:
    kind = base.get(type_id)
    return kind.run(data, replace(kind.default_params(), **params))


BEFORE = ("Before", [5.1, 6.0, 4.8, 5.9, 5.5, 6.2])
DURING = ("During", [6.0, 7.1, 5.2, 7.0, 6.1, 7.4])
AFTER = ("After", [5.4, 6.6, 5.0, 6.2, 5.9, 6.5])


# --- repeated measures ---------------------------------------------------------------


def test_ordinary_is_the_default_and_old_projects_keep_it() -> None:
    assert AnovaParams().design == "ordinary"
    # A one-way ANOVA saved before step 3.1 has no design.
    old = params_from_json(AnovaParams, {"posthoc": "tukey", "control": ""})
    assert old == AnovaParams()
    stored = replace(AnovaParams(), design="repeated_sphericity")
    assert params_from_json(AnovaParams, params_to_json(stored)) == stored


def test_repeated_measures_applies_greenhouse_geisser_by_default() -> None:
    data = series_inputs(BEFORE, DURING, AFTER)
    result = run("one_way_anova", data, design="repeated")
    assert result.method == "Repeated-measures one-way ANOVA (Greenhouse-Geisser correction)"
    eps = value(result, "Greenhouse-Geisser epsilon")
    assert isinstance(eps, float) and 1 / 2 <= eps <= 1
    assert value(result, "DF numerator") == pytest.approx(2 * eps)
    assert value(result, "DF denominator") == pytest.approx(10 * eps)
    assert value(result, "Number of subjects (rows)") == 6
    assert result.summary.startswith("The means differ significantly across the 3 matched series")
    assert "Tukey's multiple comparisons test" in result.summary
    assert result.tables[0].title == "ANOVA table (sphericity assumed)"
    assert [r[0] for r in result.tables[0].rows] == [
        "Treatment (between series)",
        "Subjects (between rows)",
        "Residual",
        "Total",
    ]

    assumed = run("one_way_anova", data, design="repeated_sphericity")
    assert assumed.method == "Repeated-measures one-way ANOVA"
    assert value(assumed, "DF numerator") == 2
    assert value(assumed, "P value") == value(result, "P value, sphericity assumed")
    assert value(result, "P value") == value(assumed, "P value, Greenhouse-Geisser")


def test_repeated_measures_matches_by_row_not_ordinary_anova() -> None:
    data = series_inputs(BEFORE, DURING, AFTER)
    ordinary = run("one_way_anova", data)
    repeated = run("one_way_anova", data, design="repeated_sphericity")
    # Removing the subject effect leaves a much smaller error: a much smaller P.
    p_ordinary, p_repeated = value(ordinary, "P value"), value(repeated, "P value")
    assert isinstance(p_ordinary, float) and isinstance(p_repeated, float)
    assert p_repeated < p_ordinary / 100


def test_repeated_measures_drops_incomplete_rows_with_a_warning() -> None:
    data = series_inputs(
        ("A", [1.0, 2.0, NAN, 4.0, 5.2, NAN]),
        ("B", [1.5, 2.9, 3.1, 4.4, 5.9, NAN]),
        ("C", [2.2, 3.1, 3.3, NAN, 6.4, NAN]),
    )
    result = run("one_way_anova", data, design="repeated")
    assert value(result, "Number of subjects (rows)") == 3
    assert any(
        w.startswith("2 rows were left out because of missing values") for w in result.warnings
    )


def test_repeated_measures_warns_about_sphericity_when_assumed() -> None:
    rng = np.random.default_rng(1)
    subject = rng.normal(0, 1, 12)
    groups = [
        (name, list(subject + rng.normal(0, sd, 12)))
        for name, sd in (("A", 0.1), ("B", 0.1), ("C", 1.0), ("D", 5.0))
    ]
    data = series_inputs(*groups)
    assumed = run("one_way_anova", data, design="repeated_sphericity")
    assert any("sphericity is violated" in w for w in assumed.warnings)
    corrected = run("one_way_anova", data, design="repeated")
    assert not any("sphericity" in w for w in corrected.warnings)


def test_repeated_measures_refusals_say_what_to_change() -> None:
    with pytest.raises(AnalysisError, match="at least 2 rows with a value in every series"):
        run(
            "one_way_anova",
            series_inputs(("A", [1.0, NAN]), ("B", [2.0, 3.0])),
            design="repeated",
        )
    additive = series_inputs(("A", [1.0, 2.0, 4.0]), ("B", [2.0, 3.0, 5.0]))
    with pytest.raises(AnalysisError, match="nothing is left to test against"):
        run("one_way_anova", additive, design="repeated")


def test_repeated_measures_needs_series_of_the_same_width() -> None:
    group = ReplicateGroup("Pair")
    table = Table(
        "T",
        [
            Column.new("A", ColumnRole.Y, [1, 2, 3], id="a"),
            Column.new("P1", ColumnRole.Y, [1, 2, 3], group_id=group.id),
            Column.new("P2", ColumnRole.Y, [1, 2, 3], group_id=group.id),
        ],
        [group],
    )
    kind = base.get("one_way_anova")
    params = replace(AnovaParams(), design="repeated")
    assert kind.check(table, ["a", group.id], params) == (
        "For repeated measures, tick series with the same number of columns."
    )
    assert kind.check(table, ["a", group.id], AnovaParams()) is None
    assert kind.describe(table, ["a", group.id], params).startswith(
        "Repeated-measures one-way ANOVA with the Greenhouse-Geisser correction of A and Pair"
    )


def test_repeated_measures_dunnett_uses_the_chosen_control() -> None:
    data = series_inputs(BEFORE, DURING, AFTER)
    result = run("one_way_anova", data, design="repeated", posthoc="dunnett", control="during")
    table = result.tables[-1]
    assert table.title == "Dunnett's multiple comparisons test"
    assert [r[0] for r in table.rows] == ["Before − During", "After − During"]


# --- two-way layout --------------------------------------------------------------------


def test_layout_matches_series_across_categories_by_name() -> None:
    found = layout(
        [("Control", "Male"), ("Drug", "Male"), ("drug", "Female"), ("Control", "Female")]
    )
    assert not isinstance(found, str)
    assert found.levels_a == ("Control", "Drug")
    assert found.levels_b == ("Male", "Female")
    assert found.cells == ((0, 3), (1, 2))


def test_layout_problems() -> None:
    assert layout([("A", "M"), ("B", "M")]) == "Tick series in at least 2 category groups."
    assert layout([("A", "M"), ("A", "F")]) == "Tick at least 2 series in each category group."
    assert layout([("A", "M"), ("B", "M"), ("A", "F")]) == (
        "F has no series named B. Each category needs the same series."
    )
    assert layout([("A", "M"), ("a", "M"), ("A", "F"), ("B", "F")]) == (
        "M has two series named a. Rename one of them."
    )


def two_way_table() -> Table:
    """Category = Sex, replicate group = treatment (the same name in each category),
    two replicate columns per group."""
    male, female = CategoryGroup("Male"), CategoryGroup("Female")
    cells = [
        ("Control", male, [5.1, 5.4], [4.9, 5.3]),
        ("Drug", male, [6.8, 7.1], [6.5, 7.3]),
        ("Control", female, [5.0, 5.6], [5.2, 4.8]),
        ("Drug", female, [5.6, 5.9], [5.3, 6.1]),
    ]
    groups: list[ReplicateGroup] = []
    columns: list[Column] = []
    for name, cat, first, second in cells:
        group = ReplicateGroup(name, cat.id, id=f"{name}_{cat.name}")
        groups.append(group)
        for i, values in enumerate((first, second)):
            column_name = f"{name} {cat.name} {i + 1}"
            columns.append(Column.new(column_name, ColumnRole.Y, values, group_id=group.id))
    table = Table("T", columns, groups, [male, female])
    table.category_factor = "Sex"
    table.check()
    return table


def test_two_way_lists_only_series_in_categories() -> None:
    table = two_way_table()
    table.columns.append(Column.new("Alone", ColumnRole.Y, [1, 2], id="alone"))
    kind = base.get("two_way_anova")
    names = [c.name for c in kind.candidates(table)]
    assert names == ["Control (Male)", "Drug (Male)", "Control (Female)", "Drug (Female)"]
    assert kind.can_run(table)
    params = TwoWayParams()
    assert kind.describe(table, kind.default_inputs(table), params) == (
        "Two-way ANOVA (Type III sums of squares) of Group (Control and Drug) and Sex "
        "(Male and Female) with their interaction, followed by Tukey's multiple "
        "comparisons test for each significant factor."
    )
    assert kind.check(table, ["Control_Male", "Drug_Male", "Control_Female"], params) == (
        "Female has no series named Drug. Each category needs the same series."
    )


def test_two_way_is_not_offered_without_categories() -> None:
    table = Table(
        "T", [Column.new("A", ColumnRole.Y, [1, 2]), Column.new("B", ColumnRole.Y, [3, 4])]
    )
    problem = base.get("two_way_anova").problem(table)
    assert problem is not None and "category groups" in problem


def run_table(table: Table, **params: object) -> Result:
    kind = base.get("two_way_anova")
    analysis = Analysis(
        "two_way_anova", table.id, kind.default_inputs(table), replace(TwoWayParams(), **params)
    )
    return kind.run(kind.gather(table, analysis), analysis.params)


def test_two_way_result_names_both_factors_and_runs_tukey_for_significant_ones() -> None:
    result = run_table(two_way_table(), factor_name="Treatment")
    assert result.method == "Two-way ANOVA (Type III sums of squares)"
    anova = result.tables[0]
    assert [r[0] for r in anova.rows] == ["Treatment", "Sex", "Treatment × Sex", "Residual"]
    assert result.summary.startswith("Treatment is significant (F(1, 12) = ")
    assert "; the interaction is significant (" in result.summary
    titles = [t.title for t in result.tables]
    assert titles[1] == "Cells"
    assert "Tukey's multiple comparisons test: Treatment" in titles
    assert any("The interaction is significant" in w for w in result.warnings)
    assert result.cells is not None
    assert result.cells.factor_a == "Treatment" and result.cells.factor_b == "Sex"
    assert result.cells.levels_a == ("Control", "Drug")
    assert result.cells.n == ((4, 4), (4, 4))
    assert result.cells.mean[1][0] == pytest.approx(np.mean([6.8, 7.1, 6.5, 7.3]))
    assert result.cells.n == ((4, 4), (4, 4))


def test_two_way_skips_tukey_for_factors_that_are_not_significant() -> None:
    table = two_way_table()
    # Make Sex irrelevant: the Female columns get the Male values.
    by_name = {c.name: c for c in table.columns}
    for name in ("Control", "Drug"):
        for i in (1, 2):
            by_name[f"{name} Female {i}"].values = by_name[f"{name} Male {i}"].values.copy()
    result = run_table(table)
    titles = [t.title for t in result.tables]
    assert "Tukey's multiple comparisons test: Sex" not in titles


def test_two_way_refuses_one_value_per_cell() -> None:
    data = AnalysisInputs(
        "T",
        1,
        tuple(
            SeriesData(f"{a}{b}", a, (np.array([v]),), b)
            for (a, b), v in {
                ("A", "M"): 1.0,
                ("B", "M"): 2.0,
                ("A", "F"): 3.0,
                ("B", "F"): 5.0,
            }.items()
        ),
        category_factor="Sex",
    )
    with pytest.raises(AnalysisError, match="only one value"):
        base.get("two_way_anova").run(data, TwoWayParams())


def test_two_way_blank_factor_name_falls_back_to_group() -> None:
    result = run_table(two_way_table(), factor_name="  ")
    assert result.tables[0].rows[0][0] == "Group"
