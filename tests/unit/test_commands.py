"""Tests for the concrete commands: do, undo, redo and validation."""

import copy
import math

import numpy as np
import pytest

from curvis.core import (
    AddColumn,
    AddTable,
    Command,
    CommandError,
    Document,
    MoveColumn,
    RemoveColumn,
    RemoveTable,
    RenameColumn,
    RenameTable,
    SetCellBlock,
    SetCellValue,
    SetColumnRole,
)
from curvis.data import Column, ColumnRole, Table


@pytest.fixture
def doc() -> Document:
    d = Document()
    d.execute(
        AddTable(
            Table(
                "Data",
                [
                    Column.new("Dose", ColumnRole.X, [1, 2, 3], unit="µM"),
                    Column.new("Resp", ColumnRole.Y, [10, 20, 30]),
                    Column.new("Label", ColumnRole.TEXT, ["a", "b", None]),
                ],
            )
        )
    )
    d.execute(AddTable(Table("Other")))
    d.undo_stack.clear()
    return d


def table(doc: Document) -> Table:
    return doc.tables[0]


def col(doc: Document, name: str) -> Column:
    c = table(doc).column_by_name(name)
    assert c is not None
    return c


def check_round_trip(doc: Document, command: Command) -> None:
    """do → undo restores the start; redo restores the result of do."""
    before = copy.deepcopy(doc.tables)
    doc.execute(command)
    after = copy.deepcopy(doc.tables)
    for t in doc.tables:
        t.check()
    doc.undo()
    assert doc.tables == before
    doc.redo()
    assert doc.tables == after


def check_rejected(doc: Document, command: Command, match: str) -> None:
    before = copy.deepcopy(doc.tables)
    with pytest.raises(CommandError, match=match):
        doc.execute(command)
    assert doc.tables == before
    assert doc.undo_stack.count == 0


# --- tables


def test_add_table(doc: Document) -> None:
    new = Table("New")
    check_round_trip(doc, AddTable(new, index=1))
    assert [t.name for t in doc.tables] == ["Data", "New", "Other"]
    assert doc.table(new.id) is new


def test_add_table_rejects_bad_input(doc: Document) -> None:
    check_rejected(doc, AddTable(Table("Data")), "already a table called 'Data'")
    check_rejected(doc, AddTable(Table(" ")), "cannot be blank")
    check_rejected(doc, AddTable(Table("X"), index=5), "out of range")
    check_rejected(doc, AddTable(table(doc)), "already a table")
    bad = Table("Bad", [Column.new("A", ColumnRole.Y, [1]), Column.new("B", ColumnRole.Y, [1, 2])])
    check_rejected(doc, AddTable(bad), "different lengths")
    check_rejected(doc, AddTable(Table("Dup", id=table(doc).id)), "already in the document")


def test_remove_table(doc: Document) -> None:
    removed = table(doc)
    command = RemoveTable(removed.id)
    check_round_trip(doc, command)
    assert [t.name for t in doc.tables] == ["Other"]
    assert command.summary == "Remove table 'Data'"
    doc.undo()
    assert doc.tables[0] is removed


def test_remove_table_rejects_unknown_id(doc: Document) -> None:
    check_rejected(doc, RemoveTable("nope"), "no table")


def test_rename_table(doc: Document) -> None:
    command = RenameTable(table(doc).id, "Dose response")
    check_round_trip(doc, command)
    assert table(doc).name == "Dose response"
    assert command.summary == "Rename table 'Data' to 'Dose response'"


def test_rename_table_to_same_name_is_allowed(doc: Document) -> None:
    doc.execute(RenameTable(table(doc).id, "Data"))
    assert table(doc).name == "Data"


def test_rename_table_rejects_taken_name(doc: Document) -> None:
    check_rejected(doc, RenameTable(table(doc).id, "Other"), "already a table")
    check_rejected(doc, RenameTable(table(doc).id, ""), "blank")


# --- columns


def test_add_column_at_index(doc: Document) -> None:
    new = Column.new("Err", ColumnRole.ERROR, [1, 1, 1])
    check_round_trip(doc, AddColumn(table(doc).id, new, index=2))
    assert [c.name for c in table(doc).columns] == ["Dose", "Resp", "Err", "Label"]


def test_add_shorter_column_is_padded(doc: Document) -> None:
    check_round_trip(doc, AddColumn(table(doc).id, Column.new("Short", ColumnRole.Y, [5])))
    np.testing.assert_array_equal(col(doc, "Short").values, [5, np.nan, np.nan])


def test_add_longer_column_pads_table_and_undo_shrinks(doc: Document) -> None:
    check_round_trip(doc, AddColumn(table(doc).id, Column.new("Long", ColumnRole.Y, range(5))))
    assert table(doc).n_rows == 5
    assert col(doc, "Label").values.tolist() == ["a", "b", None, None, None]
    doc.undo()
    assert table(doc).n_rows == 3


def test_add_first_column_to_empty_table(doc: Document) -> None:
    other = doc.tables[1]
    check_round_trip(doc, AddColumn(other.id, Column.new("A", ColumnRole.Y, [1, 2])))
    assert other.n_rows == 2


def test_add_column_rejects_bad_input(doc: Document) -> None:
    tid = table(doc).id
    check_rejected(doc, AddColumn(tid, Column.new("Dose", ColumnRole.Y)), "already a column")
    check_rejected(doc, AddColumn(tid, Column.new("", ColumnRole.Y)), "blank")
    check_rejected(doc, AddColumn(tid, Column.new("Z", ColumnRole.Y), index=9), "out of range")
    check_rejected(doc, AddColumn("nope", Column.new("Z", ColumnRole.Y)), "no table")
    same_id = Column.new("Z", ColumnRole.Y, [1, 2, 3], id=col(doc, "Dose").id)
    check_rejected(doc, AddColumn(tid, same_id), "already in the table")
    broken = Column.new("Z", ColumnRole.Y, [1.0])
    broken.role = ColumnRole.TEXT  # array no longer matches the role
    check_rejected(doc, AddColumn(tid, broken), "expected object")


def test_remove_column(doc: Document) -> None:
    command = RemoveColumn(table(doc).id, col(doc, "Resp").id)
    check_round_trip(doc, command)
    assert [c.name for c in table(doc).columns] == ["Dose", "Label"]
    assert command.summary == "Remove column 'Resp'"


def test_remove_last_column_and_undo_restores_rows(doc: Document) -> None:
    t = table(doc)
    for c in list(t.columns):
        doc.execute(RemoveColumn(t.id, c.id))
    assert t.n_rows == 0
    for _ in range(3):
        doc.undo()
    assert t.n_rows == 3


def test_remove_column_rejects_unknown_id(doc: Document) -> None:
    check_rejected(doc, RemoveColumn(table(doc).id, "nope"), "no column")


@pytest.mark.parametrize("new_index", [0, 1, 2])
def test_move_column(doc: Document, new_index: int) -> None:
    check_round_trip(doc, MoveColumn(table(doc).id, col(doc, "Dose").id, new_index))
    assert table(doc).columns[new_index].name == "Dose"


def test_move_column_rejects_bad_index(doc: Document) -> None:
    check_rejected(doc, MoveColumn(table(doc).id, col(doc, "Dose").id, 3), "out of range")
    check_rejected(doc, MoveColumn(table(doc).id, col(doc, "Dose").id, -1), "out of range")


def test_rename_column(doc: Document) -> None:
    command = RenameColumn(table(doc).id, col(doc, "Resp").id, "Response")
    check_round_trip(doc, command)
    assert col(doc, "Response").role is ColumnRole.Y
    assert command.summary == "Rename column 'Resp' to 'Response'"


def test_rename_column_rejects_taken_or_blank_name(doc: Document) -> None:
    check_rejected(doc, RenameColumn(table(doc).id, col(doc, "Resp").id, "Dose"), "already")
    check_rejected(doc, RenameColumn(table(doc).id, col(doc, "Resp").id, "\t"), "blank")


def test_same_column_name_allowed_in_different_tables(doc: Document) -> None:
    doc.execute(AddColumn(doc.tables[1].id, Column.new("Dose", ColumnRole.X)))


# --- roles


def test_set_role_between_numeric_roles_keeps_values(doc: Document) -> None:
    resp = col(doc, "Resp")
    values = resp.values
    check_round_trip(doc, SetColumnRole(table(doc).id, resp.id, ColumnRole.ERROR))
    assert resp.role is ColumnRole.ERROR
    assert resp.values is values


def test_set_role_numeric_to_text_and_back_is_exact_on_undo(doc: Document) -> None:
    t = table(doc)
    with_fraction = Column.new("F", ColumnRole.Y, [0.1, 1e-300, math.nan])
    doc.execute(AddColumn(t.id, with_fraction))
    check_round_trip(doc, SetColumnRole(t.id, with_fraction.id, ColumnRole.TEXT))
    assert with_fraction.values.tolist() == ["0.1", "1e-300", None]
    doc.execute(SetColumnRole(t.id, with_fraction.id, ColumnRole.Y))
    np.testing.assert_array_equal(with_fraction.values, [0.1, 1e-300, np.nan])


def test_set_role_text_to_numeric_makes_non_numbers_missing(doc: Document) -> None:
    label = col(doc, "Label")
    check_round_trip(doc, SetColumnRole(table(doc).id, label.id, ColumnRole.EVENT))
    np.testing.assert_array_equal(label.values, [np.nan, np.nan, np.nan])
    doc.undo()
    assert label.values.tolist() == ["a", "b", None]


def test_set_role_summary(doc: Document) -> None:
    command = SetColumnRole(table(doc).id, col(doc, "Resp").id, ColumnRole.TIME)
    doc.execute(command)
    assert command.summary == "Set role of column 'Resp' to Time"


# --- cells


def test_set_cell_value(doc: Document) -> None:
    command = SetCellValue(table(doc).id, col(doc, "Resp").id, 1, 99)
    check_round_trip(doc, command)
    np.testing.assert_array_equal(col(doc, "Resp").values, [10, 99, 30])
    assert command.summary == "Set cell in column 'Resp', row 2"


@pytest.mark.parametrize("value", [None, ""])
def test_clear_cell_sets_nan(doc: Document, value: str | None) -> None:
    doc.execute(SetCellValue(table(doc).id, col(doc, "Resp").id, 0, value))
    assert math.isnan(col(doc, "Resp").values[0])


def test_set_text_cell(doc: Document) -> None:
    check_round_trip(doc, SetCellValue(table(doc).id, col(doc, "Label").id, 2, "c"))
    assert col(doc, "Label").values.tolist() == ["a", "b", "c"]


def test_set_cell_past_end_grows_table(doc: Document) -> None:
    check_round_trip(doc, SetCellValue(table(doc).id, col(doc, "Dose").id, 5, 4))
    assert table(doc).n_rows == 6
    np.testing.assert_array_equal(col(doc, "Dose").values, [1, 2, 3, np.nan, np.nan, 4])
    doc.undo()
    assert table(doc).n_rows == 3


def test_set_cell_rejects_text_in_numeric_column(doc: Document) -> None:
    check_rejected(doc, SetCellValue(table(doc).id, col(doc, "Resp").id, 0, "abc"), "not a number")


def test_set_cell_rejects_bad_position(doc: Document) -> None:
    check_rejected(doc, SetCellValue(table(doc).id, col(doc, "Resp").id, -1, 1), "negative")
    check_rejected(doc, SetCellValue(table(doc).id, "nope", 0, 1), "no column")


def test_set_cell_block_across_columns(doc: Document) -> None:
    t = table(doc)
    command = SetCellBlock(t.id, 1, 1, [[21, "x"], ["22.5", None]])
    check_round_trip(doc, command)
    np.testing.assert_array_equal(col(doc, "Resp").values, [10, 21, 22.5])
    assert col(doc, "Label").values.tolist() == ["a", "x", None]
    assert command.summary == "Set 4 cells from row 2"


def test_set_cell_block_grows_table_and_undo_shrinks(doc: Document) -> None:
    t = table(doc)
    check_round_trip(doc, SetCellBlock(t.id, 2, 0, [[3.5], [4], [5]]))
    assert t.n_rows == 5
    assert col(doc, "Label").values.tolist() == ["a", "b", None, None, None]
    doc.undo()
    assert t.n_rows == 3


def test_set_cell_block_rejects_bad_shapes(doc: Document) -> None:
    tid = table(doc).id
    check_rejected(doc, SetCellBlock(tid, 0, 0, []), "no values")
    check_rejected(doc, SetCellBlock(tid, 0, 0, [[]]), "no values")
    check_rejected(doc, SetCellBlock(tid, 0, 0, [[1, 2], [3]]), "same length")
    check_rejected(doc, SetCellBlock(tid, 0, 2, [[1, 2]]), "past the last column")
    check_rejected(doc, SetCellBlock(tid, 0, -1, [[1]]), "negative")
    check_rejected(doc, SetCellBlock(tid, 0, 0, [[1, "x"]]), "not a number")


def test_rejected_block_changes_nothing_even_when_last_cell_is_bad(doc: Document) -> None:
    check_rejected(doc, SetCellBlock(table(doc).id, 0, 0, [[1, 2], [3, "bad"]]), "not a number")


def test_cell_edit_after_resize_undoes_correctly(doc: Document) -> None:
    t = table(doc)
    start = copy.deepcopy(doc.tables)
    doc.execute(SetCellValue(t.id, col(doc, "Dose").id, 0, 100))
    doc.execute(AddColumn(t.id, Column.new("Long", ColumnRole.Y, range(6))))
    doc.execute(SetCellValue(t.id, col(doc, "Dose").id, 5, 7))
    for _ in range(3):
        doc.undo()
    assert doc.tables == start


def test_arrays_stay_read_only_after_commands(doc: Document) -> None:
    doc.execute(SetCellValue(table(doc).id, col(doc, "Resp").id, 0, 1))
    doc.undo()
    assert all(not c.values.flags.writeable for c in table(doc).columns)


def test_command_type_is_class_name(doc: Document) -> None:
    assert RenameTable("x", "y").command_type == "RenameTable"


def test_numeric_role_change_then_cell_edits_and_resize_undo_exactly(doc: Document) -> None:
    # Regression (found by hypothesis): a role change that keeps the array must not
    # hold on to it, because later in-place cell edits would change the "old" values.
    t = table(doc)
    resp = col(doc, "Resp")
    start = copy.deepcopy(doc.tables)
    doc.execute(SetColumnRole(t.id, resp.id, ColumnRole.ERROR))
    doc.execute(SetCellValue(t.id, resp.id, 0, 0.0))
    doc.execute(SetCellValue(t.id, resp.id, 4, None))  # grows the table
    for _ in range(3):
        doc.undo()
    assert doc.tables == start


# --- case-insensitive names


def test_table_names_are_unique_ignoring_case(doc: Document) -> None:
    check_rejected(doc, AddTable(Table("DATA")), "already a table called 'Data'")
    check_rejected(doc, RenameTable(doc.tables[1].id, "data"), "already a table called 'Data'")


def test_column_names_are_unique_ignoring_case(doc: Document) -> None:
    tid = table(doc).id
    check_rejected(doc, AddColumn(tid, Column.new("dose", ColumnRole.Y)), "called 'Dose'")
    check_rejected(doc, RenameColumn(tid, col(doc, "Resp").id, "DOSE"), "called 'Dose'")


def test_changing_only_the_case_of_a_name_is_allowed(doc: Document) -> None:
    check_round_trip(doc, RenameTable(table(doc).id, "DATA"))
    assert table(doc).name == "DATA"
    check_round_trip(doc, RenameColumn(table(doc).id, col(doc, "Dose").id, "dose"))
    assert [c.name for c in table(doc).columns] == ["dose", "Resp", "Label"]


def test_lookups_by_name_ignore_case(doc: Document) -> None:
    assert doc.table_by_name("data") is table(doc)
    assert table(doc).column_by_name("RESP") is col(doc, "Resp")
