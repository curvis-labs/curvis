"""Tests for columns, roles and tables."""

import math

import numpy as np
import pytest

from curvis.data import Column, ColumnRole, Table, name_key
from curvis.data.column import coerce_cell, convert_values, resized, writable


def test_numeric_column_stores_float64_with_nan_for_missing() -> None:
    col = Column.new("A", ColumnRole.Y, [1, "2.5", None, ""])
    assert col.values.dtype == np.float64
    np.testing.assert_array_equal(col.values, [1.0, 2.5, np.nan, np.nan])


def test_text_column_stores_objects_with_none_for_missing() -> None:
    col = Column.new("Label", ColumnRole.TEXT, ["a", None, 3, 1.5])
    assert col.values.dtype == object
    assert col.values.tolist() == ["a", None, "3", "1.5"]


def test_numeric_column_rejects_text() -> None:
    with pytest.raises(ValueError, match="not a number"):
        Column.new("A", ColumnRole.Y, ["abc"])


def test_values_are_copied_and_read_only() -> None:
    source = np.array([1.0, 2.0])
    col = Column.new("A", ColumnRole.X, source)
    source[0] = 99.0
    assert col.values[0] == 1.0
    with pytest.raises(ValueError, match="read-only"):
        col.values[0] = 5.0


def test_writable_unlocks_temporarily() -> None:
    col = Column.new("A", ColumnRole.Y, [1.0])
    with writable(col.values) as arr:
        arr[0] = 2.0
    assert col.values[0] == 2.0
    assert not col.values.flags.writeable


def test_role_accepts_string_value() -> None:
    assert Column.new("A", "Error").role is ColumnRole.ERROR


def test_is_numeric() -> None:
    assert all(r.is_numeric for r in ColumnRole if r is not ColumnRole.TEXT)
    assert not ColumnRole.TEXT.is_numeric


def test_equality_treats_nan_as_equal() -> None:
    a = Column.new("A", ColumnRole.Y, [1.0, math.nan], id="x")
    b = Column.new("A", ColumnRole.Y, [1.0, math.nan], id="x")
    assert a == b
    assert a != Column.new("A", ColumnRole.Y, [1.0, 2.0], id="x")
    assert a != Column.new("B", ColumnRole.Y, [1.0, math.nan], id="x")
    assert a != Column.new("A", ColumnRole.Y, [1.0, math.nan], id="y")
    assert a != Column.new("A", ColumnRole.Y, [1.0, math.nan], unit="s", id="x")


def test_equality_of_text_columns() -> None:
    a = Column.new("A", ColumnRole.TEXT, ["a", None], id="x")
    assert a == Column.new("A", ColumnRole.TEXT, ["a", None], id="x")
    assert a != Column.new("A", ColumnRole.TEXT, ["a", "b"], id="x")


def test_coerce_cell() -> None:
    assert coerce_cell(" 3 ", ColumnRole.Y) == 3.0
    assert math.isnan(coerce_cell(None, ColumnRole.Y))  # type: ignore[arg-type]
    assert coerce_cell(2, ColumnRole.TEXT) == "2"
    assert coerce_cell(None, ColumnRole.TEXT) is None
    with pytest.raises(ValueError):
        coerce_cell([1], ColumnRole.Y)  # type: ignore[arg-type]


def test_convert_values_between_numeric_and_text() -> None:
    num = Column.new("A", ColumnRole.Y, [1.0, 2.5, math.nan]).values
    text = convert_values(num, ColumnRole.TEXT)
    assert text.tolist() == ["1", "2.5", None]
    back = convert_values(Column.new("T", ColumnRole.TEXT, ["1", "x", None]).values, ColumnRole.Y)
    np.testing.assert_array_equal(back, [1.0, np.nan, np.nan])
    assert convert_values(num, ColumnRole.X) is num


def test_resized_pads_with_missing_and_truncates() -> None:
    num = Column.new("A", ColumnRole.Y, [1.0]).values
    np.testing.assert_array_equal(resized(num, 3), [1.0, np.nan, np.nan])
    assert resized(Column.new("T", ColumnRole.TEXT, ["a"]).values, 2).tolist() == ["a", None]
    assert resized(num, 0).size == 0


def test_table_lookups_and_rows() -> None:
    a = Column.new("A", ColumnRole.X, [1, 2])
    b = Column.new("B", ColumnRole.Y, [3, 4])
    table = Table("T", [a, b])
    assert table.n_rows == 2
    assert table.column(b.id) is b
    assert table.column_index(b.id) == 1
    assert table.column_by_name("A") is a
    assert table.column_by_name("Z") is None
    with pytest.raises(KeyError):
        table.column("missing")
    assert Table("Empty").n_rows == 0


def test_table_resize() -> None:
    table = Table(
        "T", [Column.new("A", ColumnRole.Y, [1]), Column.new("B", ColumnRole.TEXT, ["x"])]
    )
    table.resize(3)
    assert table.n_rows == 3
    assert table.columns[1].values.tolist() == ["x", None, None]
    table.check()


def test_table_check_detects_problems() -> None:
    with pytest.raises(ValueError, match="different lengths"):
        Table(
            "T", [Column.new("A", ColumnRole.Y, [1]), Column.new("B", ColumnRole.Y, [1, 2])]
        ).check()
    with pytest.raises(ValueError, match="duplicate column names"):
        Table("T", [Column.new("A", ColumnRole.Y), Column.new("A", ColumnRole.Y)]).check()
    with pytest.raises(ValueError, match="duplicate column ids"):
        Table(
            "T", [Column.new("A", ColumnRole.Y, id="x"), Column.new("B", ColumnRole.Y, id="x")]
        ).check()


def test_table_check_detects_names_differing_only_in_case() -> None:
    with pytest.raises(ValueError, match="duplicate column names"):
        Table("T", [Column.new("Dose", ColumnRole.Y), Column.new("dose", ColumnRole.Y)]).check()


def test_name_key_ignores_case() -> None:
    assert name_key("Straße") == name_key("STRASSE")
    assert name_key("A") != name_key("A ")
