"""Tests for replicate statistics (mean, SD, SEM, n per row) and series."""

import math
import statistics
from fractions import Fraction

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from curvis.data import (
    CategoryGroup,
    Column,
    ColumnRole,
    ReplicateGroup,
    Table,
    replicate_stats,
    row_stats,
    series,
)
from curvis.data.replicates import series_stats

NAN = math.nan


def exact_stats(values: list[float]) -> tuple[float, float, float, int]:
    """Reference mean, SD and SEM from exact rational arithmetic (rounded once at the end)."""
    present = [Fraction(v) for v in values if not math.isnan(v)]
    n = len(present)
    if n == 0:
        return NAN, NAN, NAN, 0
    mean = sum(present, Fraction(0)) / n
    if n < 2:
        return float(mean), NAN, NAN, n
    variance = sum(((v - mean) ** 2 for v in present), Fraction(0)) / (n - 1)
    sd = math.sqrt(variance)  # correctly rounded sqrt of the (rounded) exact variance
    return float(mean), sd, sd / math.sqrt(n), n


def assert_close(actual: float, expected: float, rel: float = 1e-12) -> None:
    if math.isnan(expected):
        assert math.isnan(actual)
    else:
        assert actual == pytest.approx(expected, rel=rel, abs=1e-300)


def test_known_values() -> None:
    stats = row_stats([[1.0, 10.0], [2.0, 20.0], [3.0, 60.0]])
    assert stats.mean.tolist() == [2.0, 30.0]
    assert stats.sd.tolist() == [1.0, pytest.approx(26.457513110645905, rel=1e-15)]
    assert stats.sem[0] == pytest.approx(1 / math.sqrt(3), rel=1e-15)
    assert stats.n.tolist() == [3, 3]
    assert stats.n.dtype == np.int64


def test_missing_values_are_left_out() -> None:
    stats = row_stats([[1.0, NAN, NAN, 4.0], [3.0, 5.0, NAN, NAN], [NAN, NAN, NAN, 8.0]])
    assert stats.n.tolist() == [2, 1, 0, 2]
    assert stats.mean[0] == 2.0
    assert stats.sd[0] == pytest.approx(math.sqrt(2), rel=1e-15)
    # n = 1: the mean is the value; SD and SEM are not defined.
    assert stats.mean[1] == 5.0
    assert math.isnan(stats.sd[1]) and math.isnan(stats.sem[1])
    # n = 0: nothing is defined.
    assert math.isnan(stats.mean[2]) and math.isnan(stats.sd[2])
    assert stats.mean[3] == 6.0


def test_n_equals_two() -> None:
    stats = row_stats([[5.0], [7.0]])
    assert stats.mean[0] == 6.0
    assert stats.sd[0] == pytest.approx(math.sqrt(2), rel=1e-15)
    assert stats.sem[0] == pytest.approx(1.0, rel=1e-15)


@pytest.mark.parametrize("value", [0.1, 1e-300, 3.3, 1e300, -7.77, 123456.789])
def test_constant_data_has_zero_sd(value: float) -> None:
    stats = row_stats([[value]] * 5)
    assert stats.mean[0] == value
    assert stats.sd[0] == 0.0
    assert stats.sem[0] == 0.0


def test_ties() -> None:
    stats = row_stats([[2.0], [2.0], [5.0], [5.0]])
    assert stats.mean[0] == 3.5
    assert stats.sd[0] == pytest.approx(math.sqrt(3.0), rel=1e-15)


def test_infinite_values() -> None:
    stats = row_stats([[1.0, math.inf], [2.0, 1.0]])
    assert stats.mean[1] == math.inf
    assert math.isnan(stats.sd[1])


def test_large_offset_is_accurate() -> None:
    """A naive one-pass formula loses all digits here; the two-pass one does not."""
    values = [1e9 + 4, 1e9 + 7, 1e9 + 13, 1e9 + 16]
    stats = row_stats([[v] for v in values])
    assert stats.sd[0] == pytest.approx(statistics.stdev([4, 7, 13, 16]), rel=1e-12)


def test_needs_an_array() -> None:
    with pytest.raises(ValueError):
        row_stats([])


def test_empty_rows() -> None:
    stats = row_stats([np.empty(0), np.empty(0)])
    assert stats.mean.shape == (0,)


FINITE = st.floats(min_value=-1e12, max_value=1e12, allow_nan=False, allow_infinity=False)


@given(
    st.integers(1, 6).flatmap(
        lambda k: st.lists(
            st.lists(FINITE | st.just(NAN), min_size=k, max_size=k), min_size=1, max_size=8
        )
    )
)
def test_matches_exact_arithmetic(rows: list[list[float]]) -> None:
    replicates = [list(col) for col in zip(*rows, strict=True)]
    stats = row_stats(replicates)
    for i, row in enumerate(rows):
        mean, sd, sem, n = exact_stats(row)
        assert stats.n[i] == n
        # A mean that cancels to nearly 0 is compared on the scale of the values.
        scale = max((abs(v) for v in row if not math.isnan(v)), default=0.0)
        if not math.isnan(mean):
            assert abs(stats.mean[i] - mean) <= 1e-12 * scale + 1e-300
        assert_close(stats.sd[i], sd, rel=1e-10)
        assert_close(stats.sem[i], sem, rel=1e-10)


# --- tables ------------------------------------------------------------------------


def grouped_table() -> Table:
    female, male = CategoryGroup("Female"), CategoryGroup("Male")
    control = ReplicateGroup("Control", female.id)
    treated = ReplicateGroup("Treated", male.id)
    return Table(
        "T",
        [
            Column.new("Dose", ColumnRole.X, [1, 2]),
            Column.new("C1", ColumnRole.Y, [1, 2], group_id=control.id),
            Column.new("C2", ColumnRole.Y, [3, NAN], group_id=control.id),
            Column.new("T1", ColumnRole.Y, [10, 20], group_id=treated.id),
            Column.new("Alone", ColumnRole.Y, [5, 6]),
            Column.new("Note", ColumnRole.TEXT, ["a", "b"]),
        ],
        [control, treated],
        [female, male],
    )


def test_replicate_stats_of_a_group() -> None:
    table = grouped_table()
    table.check()
    control = table.replicate_groups[0]
    stats = replicate_stats(table, control.id)
    assert stats.mean.tolist() == [2.0, 2.0]
    assert stats.n.tolist() == [2, 1]
    with pytest.raises(KeyError):
        replicate_stats(table, "missing")


def test_series_lists_groups_and_ungrouped_y_columns() -> None:
    table = grouped_table()
    found = series(table)
    assert [s.name for s in found] == ["Control", "Treated", "Alone"]
    control, treated, alone = found
    assert [table.column(c).name for c in control.column_ids] == ["C1", "C2"]
    assert control.category_id == table.category_groups[0].id
    assert treated.category_id == table.category_groups[1].id
    assert alone.group_id is None and alone.category_id is None
    assert series_stats(table, alone).n.tolist() == [1, 1]
    assert series_stats(table, control).mean.tolist() == [2.0, 2.0]


def test_100k_rows_is_fast_enough_to_not_matter() -> None:
    rng = np.random.default_rng(1)
    columns = [rng.normal(10, 2, 100_000) for _ in range(3)]
    stats = row_stats(columns)
    stacked = np.column_stack(columns)
    np.testing.assert_allclose(stats.mean, stacked.mean(axis=1), rtol=1e-13)
    np.testing.assert_allclose(stats.sd, stacked.std(axis=1, ddof=1), rtol=1e-11)
