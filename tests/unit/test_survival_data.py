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


def test_column_check_refuses_bad_values() -> None:
    column = Column.new("A", Y, [1, 2])
    column.role = E
    with pytest.raises(ValueError, match="row 2"):
        column.check()


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
