"""The analysis framework: registry, families, can_run, parameters and the first analysis."""

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest

from curvis.analysis import base
from curvis.analysis.base import (
    Analysis,
    AnalysisError,
    AnalysisInputs,
    AnalysisType,
    Family,
    Params,
    SeriesData,
    available_families,
    opening_family,
    param,
    params_from_json,
    params_to_json,
    preferred_family,
    runnable,
)
from curvis.analysis.descriptive import DescriptiveParams, DescriptiveStatistics
from curvis.analysis.result import Result
from curvis.data import Column, ColumnRole, Table

NAN = math.nan


@dataclass(frozen=True)
class FakeParams(Params):
    level: float = param(0.95, "&Level", minimum=0.5, maximum=0.999)
    method: str = param("a", "&Method", choices=[("a", "A"), ("b", "B")])
    count: int = param(3, "&Count", advanced=True, minimum=1)


class FakeFit(AnalysisType):
    """Needs an X and a Y column."""

    id = "test_fit"
    label = "&Fake fit"
    method = "Fake fit"
    family = Family.FIT
    params_type = FakeParams

    def problem(self, table: Table) -> str | None:
        roles = {c.role for c in table.columns}
        return None if {ColumnRole.X, ColumnRole.Y} <= roles else "Needs X and Y columns."

    def describe(self, table: Table, inputs: Sequence[str], params: Params) -> str:
        return "Fake fit."

    @staticmethod
    def run(inputs: AnalysisInputs, params: Any) -> Result:
        return Result("Fake fit", "Fitted.")


class FakeSurvival(FakeFit):
    id = "test_survival"
    label = "&Fake survival"
    family = Family.SURVIVAL

    def problem(self, table: Table) -> str | None:
        roles = {c.role for c in table.columns}
        return None if {ColumnRole.TIME, ColumnRole.EVENT} <= roles else "Needs time and event."


@pytest.fixture
def fakes() -> Iterator[None]:
    base.register(FakeFit())
    base.register(FakeSurvival())
    yield
    base.unregister(FakeFit.id)
    base.unregister(FakeSurvival.id)


def table(*roles: str) -> Table:
    return Table("T", [Column.new(f"c{i}", role, [1.0, 2.0]) for i, role in enumerate(roles)])


def test_the_first_analysis_is_registered() -> None:
    kind = base.get("descriptive_statistics")
    assert isinstance(kind, DescriptiveStatistics)
    assert kind.family is Family.COMPARE
    assert kind.name == "Descriptive statistics"
    assert kind in base.in_family(Family.COMPARE)
    with pytest.raises(KeyError):
        base.get("nothing")


def test_a_type_cannot_be_registered_twice() -> None:
    with pytest.raises(ValueError, match="already registered"):
        base.register(DescriptiveStatistics())


def test_every_parameter_needs_a_label() -> None:
    @dataclass(frozen=True)
    class Bare(Params):
        x: int = 1

    class Broken(FakeFit):
        id = "broken"
        params_type = Bare

    with pytest.raises(TypeError, match="param"):
        base.register(Broken())


def test_can_run_needs_a_y_column() -> None:
    kind = base.get("descriptive_statistics")
    assert kind.can_run(table("Y"))
    assert not kind.can_run(table("X", "Text"))
    assert kind.problem(table("X")) == "Needs at least one Y column."


@pytest.mark.usefixtures("fakes")
@pytest.mark.parametrize(
    ("roles", "preferred", "available", "opening"),
    [
        (("Y",), Family.COMPARE, [Family.COMPARE], Family.COMPARE),
        (("X", "Y"), Family.FIT, [Family.COMPARE, Family.FIT], Family.FIT),
        (
            ("Time", "Event", "X", "Y"),
            Family.SURVIVAL,
            [Family.COMPARE, Family.FIT, Family.SURVIVAL],
            Family.SURVIVAL,
        ),
        (("Time", "Event"), Family.SURVIVAL, [Family.SURVIVAL], Family.SURVIVAL),
        (("X",), Family.COMPARE, [], None),
    ],
)
def test_families_and_the_one_the_dialog_opens_on(
    roles: tuple[str, ...], preferred: Family, available: list[Family], opening: Family | None
) -> None:
    t = table(*roles)
    assert preferred_family(t) is preferred
    assert available_families(t) == available
    assert opening_family(t) is opening


def test_xy_data_opens_on_the_curve_fits() -> None:
    t = table("X", "Y")
    assert preferred_family(t) is Family.FIT
    assert opening_family(t) is Family.FIT


def test_the_dialog_falls_back_to_a_family_that_has_analyses() -> None:
    # An X column without values: no curve fit can run, so it opens on Compare groups.
    t = Table("T", [Column.new("x", "X", [math.nan, math.nan]), Column.new("y", "Y", [1, 2])])
    assert preferred_family(t) is Family.FIT
    assert opening_family(t) is Family.COMPARE


@pytest.mark.usefixtures("fakes")
def test_runnable_filters_by_family_and_table() -> None:
    t = table("X", "Y")
    assert [k.id for k in runnable(t, Family.FIT)] == [
        "linear_regression", "polynomial_regression", "hill_4pl", "hill_3pl", "test_fit"
    ]  # fmt: skip
    assert runnable(t, Family.SURVIVAL) == []


def test_parameters_round_trip_through_json() -> None:
    params = FakeParams(level=0.9, method="b", count=5)
    data = params_to_json(params)
    assert data == {"level": 0.9, "method": "b", "count": 5}
    assert params_from_json(FakeParams, data) == params
    assert params_from_json(FakeParams, {"unknown": 1}) == FakeParams()
    assert params_from_json(FakeParams, {"count": 1}) == FakeParams(count=1)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"level": "high"}, "must be a number"),
        ({"level": True}, "must be a number"),
        ({"level": 0.1}, "at least 0.5"),
        ({"level": float("inf")}, "finite"),
        ({"method": "c"}, "cannot be 'c'"),
        ({"count": 2.5}, "whole number"),
        ({"count": 0}, "at least 1"),
    ],
)
def test_bad_parameters_are_refused(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        params_from_json(FakeParams, data)
    with pytest.raises(ValueError, match="true or false"):
        params_from_json(DescriptiveParams, {"confidence_interval": 1})


# --- descriptive statistics ----------------------------------------------------------------


def grouped_table() -> Table:
    t = Table(
        "Data",
        [
            Column.new("Dose", "X", [1, 2, 3], id="x"),
            Column.new("a1", "Y", [1, 2, NAN], id="a1", group_id="g"),
            Column.new("a2", "Y", [3, NAN, NAN], id="a2", group_id="g"),
            Column.new("b", "Y", [5, 6, 7], id="b"),
            Column.new("Note", "Text", ["x", None, "y"], id="t"),
        ],
    )
    from curvis.data import ReplicateGroup

    t.replicate_groups.append(ReplicateGroup("A", id="g"))
    return t


def test_descriptive_candidates_are_the_series_with_their_n() -> None:
    kind = base.get("descriptive_statistics")
    choices = kind.candidates(grouped_table())
    assert [(c.id, c.name, c.n_values, c.n_replicates) for c in choices] == [
        ("g", "A", 3, 2),
        ("b", "b", 3, 1),
    ]


@pytest.mark.parametrize(
    ("x_values", "expected"),
    [([1.0, 2.0, 3.0], True), ([NAN, 2.0, NAN], True), ([NAN, NAN, NAN], False), (None, False)],
)
def test_rows_match_x_needs_an_x_column_with_values(
    x_values: list[float] | None, expected: bool
) -> None:
    columns = [Column.new("a", "Y", [1, 2, 3]), Column.new("label", "Text", ["p", "q", "r"])]
    if x_values is not None:
        columns.append(Column.new("x", "X", x_values))
    t = Table("T", columns)
    assert base.rows_match_x(t) is expected
    (choice,) = base.series_choices(t)
    assert choice.n(base.rows_match_x(t)) == (1 if expected else 3)


def test_descriptive_statistics_reads_its_series_and_the_x_column() -> None:
    kind = base.get("descriptive_statistics")
    t = grouped_table()
    analysis = Analysis(kind.id, t.id, ("g",), DescriptiveParams())
    assert kind.reads(t, analysis) == ["a1", "a2", "x"]
    inputs = kind.gather(t, analysis)
    assert inputs.x_name == "Dose" and [s.name for s in inputs.series] == ["A"]
    inputs.series[0].columns[0][0] = 99  # a copy: the table is not changed
    assert t.column("a1").values[0] == 1


def test_descriptive_statistics_results() -> None:
    kind = base.get("descriptive_statistics")
    t = grouped_table()
    analysis = Analysis(kind.id, t.id, ("g", "b"), DescriptiveParams())
    result = kind.run(kind.gather(t, analysis), DescriptiveParams())
    assert result.method == "Descriptive statistics"
    a, _b = result.tables
    assert [c.label for c in a.columns] == [
        "Dose", "n", "Mean", "SD", "SEM", "Lower 95% CI", "Upper 95% CI"
    ]  # fmt: skip
    assert len(a.rows) == 2  # the row without values is left out
    assert a.rows[0][:5] == (1.0, 2, 2.0, math.sqrt(2), 1.0)
    assert math.isnan(a.rows[1][3])  # type: ignore[arg-type]  # n = 1: no SD
    assert "A has only one value in 1 row" in result.warnings[0]
    assert "b is a single column" in result.warnings[1]
    assert result.summary.startswith("The mean, SD, SEM and 95% confidence interval of A and b")


def test_descriptive_statistics_options() -> None:
    kind = base.get("descriptive_statistics")
    t = grouped_table()
    params = DescriptiveParams(confidence_interval=False, skip_empty_rows=False)
    result = kind.run(kind.gather(t, Analysis(kind.id, t.id, ("g",), params)), params)
    (a,) = result.tables
    assert len(a.columns) == 5 and len(a.rows) == 3


def test_descriptive_statistics_without_values_says_what_to_do() -> None:
    inputs = AnalysisInputs("T", 2, (SeriesData("s", "S", (np.array([NAN, NAN]),)),))
    with pytest.raises(AnalysisError, match="no values"):
        DescriptiveStatistics.run(inputs, DescriptiveParams())


def test_gather_reports_deleted_inputs() -> None:
    kind = base.get("descriptive_statistics")
    t = grouped_table()
    with pytest.raises(AnalysisError, match="Edit…"):
        kind.gather(t, Analysis(kind.id, t.id, ("gone",), DescriptiveParams()))


def test_check_says_why_run_is_disabled() -> None:
    kind = base.get("descriptive_statistics")
    assert kind.check(grouped_table(), (), DescriptiveParams()) == "Tick at least one series."
    assert kind.check(grouped_table(), ("b",), DescriptiveParams()) is None
