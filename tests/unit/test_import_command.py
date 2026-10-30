"""Tests for ImportColumns: fill empty columns first, then append; one undo step."""

import math

import numpy as np
import pytest

from curvis.core import CommandError, Document, GroupColumns, ImportColumns
from curvis.data import Column, ColumnRole, Table


def imported(n_rows: int = 3, count: int = 3) -> list[Column]:
    return [
        Column.new(f"In {i}", "Y", np.arange(n_rows, dtype=float) + 10 * i) for i in range(count)
    ]


def snapshot(table: Table) -> list[tuple[str, str, str, list[object]]]:
    return [
        (c.id, c.name, c.role.value, ["nan" if v != v else v for v in c.values.tolist()])
        for c in table.columns
    ]


def test_blank_table_fills_its_columns_left_to_right_then_appends() -> None:
    table = Table.blank()
    doc = Document(tables=[table])
    x_id, a_id = (c.id for c in table.columns)
    cols = imported()
    doc.execute(ImportColumns(table.id, cols, "'data.csv'"))

    assert [c.name for c in table.columns] == ["In 0", "In 1", "In 2"]
    assert [c.role for c in table.columns] == [ColumnRole.Y] * 3
    assert [c.id for c in table.columns] == [x_id, a_id, cols[2].id]
    assert table.n_rows == 3
    table.check()
    assert doc.undo_stack.undo_text == "Import 'data.csv'"
    assert doc.change_log.entries[-1].summary == "Import 'data.csv'"


def test_imported_x_fills_the_empty_x_column() -> None:
    table = Table.blank()
    doc = Document(tables=[table])
    cols = [Column.new("Dose", "X", [1, 2]), Column.new("Resp", "Y", [3, 4])]
    doc.execute(ImportColumns(table.id, cols, "'f.csv'"))
    assert [(c.name, c.role) for c in table.columns] == [
        ("Dose", ColumnRole.X),
        ("Resp", ColumnRole.Y),
    ]


def test_undo_and_redo_restore_the_table_exactly() -> None:
    table = Table(
        "T",
        [
            Column.new("X", "X", [1, 2, 3, 4]),
            Column.new("Empty", "Y", [math.nan] * 4),
            Column.new("Label", "Text", ["a", None, None, None]),
        ],
    )
    doc = Document(tables=[table])
    before = snapshot(table)
    doc.execute(ImportColumns(table.id, imported(n_rows=6, count=2), "'f.csv'"))
    after = snapshot(table)
    assert [c.name for c in table.columns] == ["X", "In 0", "Label", "In 1"]
    assert table.n_rows == 6  # the longer import pads the existing columns
    table.check()

    doc.undo()
    assert snapshot(table) == before
    table.check()
    doc.redo()
    assert snapshot(table) == after


def test_shorter_import_is_padded_to_the_table() -> None:
    table = Table("T", [Column.new("X", "X", [1, 2, 3, 4, 5])])
    doc = Document(tables=[table])
    doc.execute(ImportColumns(table.id, imported(n_rows=2, count=1), "'f.csv'"))
    assert table.n_rows == 5
    assert np.isnan(table.columns[1].values[2:]).all()


def test_table_of_empty_rows_takes_the_imported_row_count() -> None:
    table = Table(
        "T", [Column.new("A", "Y", [math.nan] * 50), Column.new("B", "Text", [None] * 50)]
    )
    doc = Document(tables=[table])
    doc.execute(ImportColumns(table.id, imported(n_rows=3, count=1), "'f.csv'"))
    assert table.n_rows == 3
    assert [c.name for c in table.columns] == ["In 0", "B"]  # B stays, unfilled


def test_grouped_empty_columns_are_not_filled() -> None:
    table = Table(
        "T",
        [Column.new("R1", "Y", [math.nan]), Column.new("R2", "Y", [math.nan])],
    )
    doc = Document(tables=[table])
    doc.execute(GroupColumns(table.id, [c.id for c in table.columns], "Reps"))
    doc.execute(ImportColumns(table.id, imported(n_rows=1, count=1), "'f.csv'"))
    assert [c.name for c in table.columns] == ["R1", "R2", "In 0"]
    table.check()


def test_names_clashing_with_kept_columns_get_a_number() -> None:
    table = Table("T", [Column.new("Dose", "X", [1]), Column.new("A", "Y", [math.nan])])
    doc = Document(tables=[table])
    cols = [Column.new("dose", "Y", [2]), Column.new("A", "Y", [3]), Column.new("A", "Y", [4])]
    doc.execute(ImportColumns(table.id, cols, "'f.csv'"))
    # "A" is freed when the empty column A is filled; the second "A" clashes with it.
    assert [c.name for c in table.columns] == ["Dose", "dose (2)", "A", "A (2)"]


def test_text_columns_are_imported() -> None:
    table = Table.blank()
    doc = Document(tables=[table])
    doc.execute(ImportColumns(table.id, [Column.new("Label", "Text", ["a", None])], "'f.csv'"))
    assert table.columns[0].role is ColumnRole.TEXT
    assert table.columns[0].values.tolist() == ["a", None]
    assert len(table.columns[1]) == 2
    assert np.isnan(table.columns[1].values).all()  # the empty A column, padded
    table.check()


def test_refused_imports_leave_the_table_alone() -> None:
    table = Table.blank()
    doc = Document(tables=[table])
    before = snapshot(table)
    with pytest.raises(CommandError, match="no columns"):
        doc.execute(ImportColumns(table.id, [], "'f.csv'"))
    with pytest.raises(CommandError, match="different lengths"):
        cols = [Column.new("a", "Y", [1]), Column.new("b", "Y", [1, 2])]
        doc.execute(ImportColumns(table.id, cols, "'f.csv'"))
    with pytest.raises(CommandError, match="no table"):
        doc.execute(ImportColumns("missing", imported(), "'f.csv'"))
    assert snapshot(table) == before
    assert not doc.undo_stack.can_undo
