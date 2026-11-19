"""Time + Event pairs, the checks on their values, and the Kaplan-Meier core."""

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from curvis.analysis.survival.km import kaplan_meier, merge_near_ties, quantile_time
from curvis.core import AddTable, CommandError, Document, SetCellBlock, SetCellValue, SetColumnRole
from curvis.data import Column, ColumnRole, Table, survival_pairs
from curvis.data.column import coerce_cell, role_problem

T, E, Y = ColumnRole.TIME, ColumnRole.EVENT, ColumnRole.Y


def make(*columns: tuple[str, ColumnRole, list[float]]) -> Table:
    return Table("T", [Column.new(n, r, v) for n, r, v in columns])


# --- pairing ---------------------------------------------------------------------------


def test_adjacent_pairs() -> None:
    table = make(("t1", T, [1]), ("A", E, [1]), ("t2", T, [2]), ("B", E, [0]))
    pairs = survival_pairs(table)
    assert [p.name for p in pairs] == ["A", "B"]
    assert pairs[0].time_id == table.columns[0].id
    assert pairs[1].time_id == table.columns[2].id
    assert pairs[1].source == table.columns[3].id


def test_one_time_column_shared_by_several_event_columns() -> None:
    table = make(("t", T, [1, 2]), ("A", E, [1, math.nan]), ("B", E, [math.nan, 0]))
    pairs = survival_pairs(table)
    assert [p.name for p in pairs] == ["A", "B"]
    assert {p.time_id for p in pairs} == {table.columns[0].id}


def test_event_without_time_on_its_left_is_not_paired() -> None:
    table = make(("A", E, [1]), ("y", Y, [3]), ("t", T, [1]), ("B", E, [1]))
    assert [p.name for p in survival_pairs(table)] == ["B"]


def test_other_columns_between_time_and_event_keep_the_pair() -> None:
    table = make(("t", T, [1]), ("note", Y, [3]), ("A", E, [1]))
    assert [p.name for p in survival_pairs(table)] == ["A"]


# --- checks on entry ----------------------------------------------------------------------


@pytest.mark.parametrize("value", [0, 1, "1", "0", "", None, 1.0])
def test_event_accepts_0_1_and_missing(value: object) -> None:
    coerce_cell(value, E)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [2, -1, 0.5, "2", math.inf])
def test_event_refuses_other_numbers(value: object) -> None:
    with pytest.raises(ValueError, match=r"1 \(event\) or 0 \(censored\)"):
        coerce_cell(value, E)  # type: ignore[arg-type]


def test_time_refuses_negative_values() -> None:
    assert coerce_cell(0, T) == 0
    assert coerce_cell("12.5", T) == 12.5
    with pytest.raises(ValueError, match="cannot be negative"):
        coerce_cell(-0.1, T)


def test_role_problem_names_the_first_bad_row() -> None:
    assert role_problem(np.array([1.0, np.nan, 0.0]), E) is None
    bad = role_problem(np.array([1.0, 0.0, 2.0, 3.0]), E)
    assert bad is not None and bad[0] == 2
    assert role_problem(np.array([1.0, -2.0]), T) == (1, "A time cannot be negative (-2)")
    assert role_problem(np.array([-1.0]), Y) is None


@pytest.fixture
def doc() -> Document:
    d = Document()
    table = make(("t", T, [1, 2, 3]), ("A", E, [1, 0, 1]), ("y", Y, [0, 1, 1]), ("z", Y, [5, 1, 1]))
    d.execute(AddTable(table))
    d.undo_stack.clear()
    return d


def test_typing_a_bad_event_is_refused(doc: Document) -> None:
    table = doc.tables[0]
    with pytest.raises(CommandError, match="An event is 1"):
        doc.execute(SetCellValue(table.id, table.columns[1].id, 0, 2))
    with pytest.raises(CommandError, match="cannot be negative"):
        doc.execute(SetCellBlock(table.id, 0, 0, [[-1, 1]]))
    assert table.columns[1].values.tolist() == [1, 0, 1]
    doc.execute(SetCellValue(table.id, table.columns[1].id, 0, "0"))
    assert table.columns[1].values[0] == 0


def test_role_change_to_event_is_refused_with_bad_values(doc: Document) -> None:
    table = doc.tables[0]
    z = table.columns[3]
    with pytest.raises(CommandError, match=r"cannot have the role Event. Row 1: An event is 1"):
        doc.execute(SetColumnRole(table.id, z.id, E))
    assert z.role is Y
    assert doc.undo_stack.count == 0
    y = table.columns[2]
    doc.execute(SetColumnRole(table.id, y.id, E))
    assert y.role is E
    assert [p.name for p in survival_pairs(table)] == ["A", "y"]


def test_role_change_from_text_checks_converted_values() -> None:
    d = Document()
    table = Table("T", [Column.new("t", T, [1, 2]), Column.new("txt", ColumnRole.TEXT, ["1", "5"])])
    d.execute(AddTable(table))
    with pytest.raises(CommandError, match="Row 2"):
        d.execute(SetColumnRole(table.id, table.columns[1].id, E))


def test_bad_values_from_an_older_project_load_and_the_analysis_says_why() -> None:
    """Entry is checked, but loading is not: a project saved before step 3.4 may hold an
    Event column with other values. It opens, and Kaplan-Meier refuses with the row."""
    from curvis.analysis import base
    from curvis.analysis.base import AnalysisError

    table = make(("t", T, [1, 2]), ("A", Y, [1, 2]))
    table.columns[1].role = E  # as a loaded file can hold it
    table.check()
    kind = base.get("kaplan_meier")
    analysis = base.Analysis(kind.id, table.id, kind.default_inputs(table), kind.default_params())
    with pytest.raises(AnalysisError, match=r"A: Events must be 1 \(event\) or 0 .* \(row 2\)"):
        kind.run(kind.gather(table, analysis), analysis.params)


# --- Kaplan-Meier core --------------------------------------------------------------------


def test_kaplan_meier_by_hand() -> None:
    r = kaplan_meier([1, 2, 2, 3, 4], [1, 1, 0, 1, 0])
    assert r.time.tolist() == [1, 2, 3, 4]
    assert r.n_risk.tolist() == [5, 4, 2, 1]
    assert r.n_event.tolist() == [1, 1, 1, 0]
    assert r.n_censor.tolist() == [0, 1, 0, 1]
    np.testing.assert_allclose(r.survival, [0.8, 0.6, 0.3, 0.3])
    assert r.median == 3
    assert r.n_censored == 2
    assert r.at_risk([0, 2, 2.5, 5]).tolist() == [5, 4, 2, 0]
    np.testing.assert_allclose(r.survival_at([0, 1, 1.5, 9]), [1, 0.8, 0.8, 0.3])


def test_kaplan_meier_refuses_bad_or_empty_input() -> None:
    with pytest.raises(ValueError, match="Events must be"):
        kaplan_meier([1, 2], [1, 2])
    with pytest.raises(ValueError, match="0 or more"):
        kaplan_meier([-1, 2], [1, 1])
    with pytest.raises(ValueError, match="No subject"):
        kaplan_meier([math.nan, 2], [1, math.nan])


def test_merge_near_ties_keeps_the_smaller_time() -> None:
    merged = merge_near_ties(np.array([0.1 + 0.2, 0.3, 1.0, 1 + 1e-12, 2.0]))
    assert merged.tolist() == [0.3, 0.3, 1.0, 1.0, 2.0]
    distinct = np.array([1.0, 1.001, 3.0])
    assert merge_near_ties(distinct) is distinct


def test_quantile_midpoint_on_a_flat_stretch_and_at_the_end() -> None:
    x = np.array([0.0, 1, 2, 3])
    assert quantile_time(x, np.array([0.0, 0.25, 0.5, 1.0]), 0.5) == 2.5
    assert quantile_time(x, np.array([0.0, 0.25, 0.5, 0.5]), 0.5) == 2.5  # flat to the end
    assert math.isnan(quantile_time(x, np.array([0.0, 0.1, 0.2, 0.3]), 0.5))
    assert quantile_time(x, np.array([0.0, np.nan, 0.6, np.nan]), 0.5) == 2


@settings(max_examples=60, deadline=None)
@given(
    st.lists(
        st.tuples(st.integers(0, 30), st.booleans()),
        min_size=1,
        max_size=40,
    )
)
def test_kaplan_meier_properties(subjects: list[tuple[int, bool]]) -> None:
    time = [float(t) for t, _ in subjects]
    event = [float(e) for _, e in subjects]
    r = kaplan_meier(time, event)
    assert r.n == len(subjects)
    assert int(r.n_event.sum() + r.n_censor.sum()) == r.n
    assert np.all(np.diff(r.survival) <= 1e-15)
    assert np.all((r.survival >= 0) & (r.survival <= 1))
    ok = ~np.isnan(r.lower)
    assert np.all(r.lower[ok] <= r.survival[ok] + 1e-12)
    assert np.all(r.upper[ok] >= r.survival[ok] - 1e-12)
    assert np.all(np.isnan(r.lower) == (r.survival == 0))


# --- chart data ------------------------------------------------------------------------------


def test_survival_chart_data_steps_bands_and_ticks() -> None:
    from dataclasses import replace

    from curvis.charts.builder import default_spec
    from curvis.charts.data import compute, gather
    from curvis.charts.spec import ChartFamily, Scale
    from curvis.compute.graph_engine import graph_reads

    table = make(
        ("Days", T, [2, 3, 3, 5, 8]),
        ("A", E, [1, 0, 1, 1, 0]),
        ("B", E, [math.nan] * 5),  # nobody in group B
    )
    spec = default_spec(table, ChartFamily.SURVIVAL)
    data = compute(gather(table, spec, {}))
    (a,) = data.survival
    assert a.px.tolist() == [0, 2, 2, 3, 3, 5, 5, 8, 8]
    np.testing.assert_allclose(a.py, [1, 1, 0.8, 0.8, 0.6, 0.6, 0.3, 0.3, 0.3])
    assert a.censor_px.tolist() == [3, 8]
    np.testing.assert_allclose(a.censor_py, [0.6, 0.3])
    assert np.all(a.low <= a.py + 1e-12) and np.all(a.high >= a.py - 1e-12)
    assert data.notes == ("B is not drawn: No subject has both a time and an event.",)
    assert graph_reads(table, spec) == [
        table.id,
        *(c.id for c in table.columns[:2]),
        table.columns[2].id,
    ]

    log_x = replace(spec, x_axis=replace(spec.x_axis, scale=Scale.LOG))
    (a_log,) = compute(gather(table, log_x, {})).survival
    assert a_log.px[0] == pytest.approx(math.log10(2))  # time 0 cannot be on a log axis


# --- cross-checks ------------------------------------------------------------------------


def test_z_is_qnorm_0_975() -> None:
    from scipy.stats import norm

    from curvis.analysis.survival.km import Z

    assert norm.ppf(0.975) == Z


@settings(max_examples=60, deadline=None)
@given(
    st.lists(st.tuples(st.integers(0, 15), st.booleans()), min_size=1, max_size=40),
)
def test_estimate_and_interval_match_lifelines(subjects: list[tuple[int, bool]]) -> None:
    """Curvis computes the estimate with NumPy (validated against R); lifelines'
    KaplanMeierFitter is the cross-check. Small integer times give many ties. Where S
    is 0, lifelines gives the interval [0, 0] and R none (NaN), which Curvis follows."""
    import warnings

    from lifelines import KaplanMeierFitter

    time = np.array([t for t, _ in subjects], dtype=np.float64)
    event = np.array([e for _, e in subjects], dtype=np.float64)
    r = kaplan_meier(time, event)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fitter = KaplanMeierFitter(alpha=0.05).fit(time, event)
    index = np.searchsorted(np.asarray(fitter.timeline), r.time)
    survival = fitter.survival_function_.to_numpy()[index, 0]
    ci = fitter.confidence_interval_.to_numpy()[index]
    np.testing.assert_allclose(r.survival, survival, rtol=1e-12, atol=1e-15)
    defined = r.survival > 0
    np.testing.assert_allclose(r.lower[defined], ci[defined, 0], rtol=1e-10, atol=1e-15)
    np.testing.assert_allclose(r.upper[defined], ci[defined, 1], rtol=1e-10, atol=1e-15)
    assert np.all(np.isnan(r.lower[~defined]) & (ci[~defined, 0] == 0))


def test_survival_tooltips() -> None:
    from curvis.charts.builder import default_spec
    from curvis.charts.data import compute, gather
    from curvis.charts.spec import ChartFamily
    from curvis.charts.tooltips import describe_censored, describe_step, step_at

    table = make(("Days", T, [2, 3, 3, 5]), ("A", E, [1, 0, 1, 1]))
    (curve,) = compute(gather(table, default_spec(table, ChartFamily.SURVIVAL), {})).survival
    assert [step_at(curve, x) for x in (0.0, 1.9, 2.0, 4.0, 5.0, 9.0)] == [-1, -1, 0, 1, 2, 2]
    assert describe_step(curve, -1, "Days") == "A\nDays: 0 to 2\nSurvival: 1\nAt risk: 4"
    assert describe_step(curve, 1, "Days").splitlines() == [
        "A",
        "Days: 3 to 5",
        "Survival: 0.5000",
        "95% CI: 0.05785 to 0.8449",  # sigma = 0.5: 0.5^exp(±1.96 · 0.5 / ln 0.5)
        "At risk: 3, events: 1, censored: 1",
    ]
    # S = 0 has no interval, so no CI line; the last step has no end
    assert describe_step(curve, 2, "Days") == (
        "A\nDays: 5\nSurvival: 0\nAt risk: 1, events: 1, censored: 0"
    )
    assert describe_censored(curve, 1, "Days") == (
        "A · 1 censored subject\nDays: 3\nSurvival: 0.5000\nAt risk: 3"
    )
