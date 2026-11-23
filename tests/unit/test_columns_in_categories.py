"""Columns put in a category directly: full names, the naming rules, and formulas.

See SPEC.md, "Columns in a category": a Y column in a category has the full name
"category: name", full names are unique in the table, and formulas follow renames.
"""

import copy
from pathlib import Path

import pytest

from curvis.core import (
    AddColumn,
    AddTable,
    BulkRename,
    Calculation,
    CommandError,
    Document,
    GroupColumns,
    ImportColumns,
    RemoveColumn,
    RenameColumn,
    SetCategory,
    UngroupColumns,
    add_calculation,
    fix_name_clashes,
)
from curvis.data import (
    AmbiguousColumnError,
    CategoryGroup,
    Column,
    ColumnRole,
    Pairing,
    Table,
    full_name,
    full_names,
    name_clashes,
    name_error,
    resolve_reference,
)
from curvis.formula import FormulaError, check
from curvis.io.project import load, save


@pytest.fixture
def doc() -> Document:
    """X, then Control and Treated in category Male, then a, b outside categories."""
    d = Document()
    male = CategoryGroup("Male", id="male")
    columns = [
        Column.new("X", ColumnRole.X, [1, 2], id="x"),
        Column.new("Control", ColumnRole.Y, [1, 2], id="mc", category_id="male"),
        Column.new("Treated", ColumnRole.Y, [3, 4], id="mt", category_id="male"),
        Column.new("a", ColumnRole.Y, [5, 6], id="a"),
        Column.new("b", ColumnRole.Y, [7, 8], id="b"),
    ]
    d.execute(AddTable(Table("T", columns, category_groups=[male], id="t")))
    d.undo_stack.clear()
    return d


def table(doc: Document) -> Table:
    return doc.tables[0]


def names(doc: Document) -> list[str]:
    return full_names(table(doc))


def calc(doc: Document, formula: str) -> None:
    doc.execute(add_calculation(doc, Calculation("t", formula, Pairing(("a",)))))


def formula(doc: Document) -> str:
    return doc.calculations[-1].formula


def check_undo_redo(doc: Document, command: object) -> None:
    """Run ``command``; undo must restore the document exactly, redo the result."""
    before = copy.deepcopy((doc.tables, doc.calculations))
    doc.execute(command)  # type: ignore[arg-type]
    after = copy.deepcopy((doc.tables, doc.calculations))
    table(doc).check()
    for _ in range(2):
        doc.undo()
        assert (doc.tables, doc.calculations) == before
        doc.redo()
        assert (doc.tables, doc.calculations) == after


# --- full names ----------------------------------------------------------------------


def test_full_names(doc: Document) -> None:
    assert names(doc) == ["X", "Male: Control", "Male: Treated", "a", "b"]
    t = table(doc)
    assert t.column_by_name("male: control") is t.column("mc")
    assert t.column_by_name("Control") is None
    assert full_name(t, t.column("mc"), category_names={"male": "M"}) == "M: Control"
    assert full_name(t, t.column("mc"), {"mc": "C"}) == "Male: C"


def test_replicate_columns_keep_their_plain_name(doc: Document) -> None:
    doc.execute(GroupColumns("t", ["a", "b"], "G"))
    doc.execute(SetCategory("t", [table(doc).replicate_groups[0].id], "Male"))
    assert names(doc)[-2:] == ["a", "b"]


def test_the_same_name_in_two_categories(doc: Document) -> None:
    doc.execute(RenameColumn("t", "a", "Control"))
    doc.execute(SetCategory("t", [], "Female", column_ids=["a"]))
    assert names(doc)[-2:] == ["Female: Control", "b"]
    table(doc).check()


def test_full_names_are_unique_with_a_plain_message(doc: Document) -> None:
    with pytest.raises(CommandError, match=r"^There is already a column called 'Male: Control'\.$"):
        doc.execute(RenameColumn("t", "a", "Male: Control"))
    with pytest.raises(CommandError, match="already a column called 'Male: Control'"):
        doc.execute(AddColumn("t", Column.new("male: control", ColumnRole.Y)))
    with pytest.raises(CommandError, match="already a column called 'Male: Treated'"):
        doc.execute(RenameColumn("t", "mc", "treated"))
    with pytest.raises(CommandError, match="already a column called 'M: Control'"):
        doc.execute(BulkRename("t", columns={"a": "M: Control"}, categories={"male": "M"}))
    assert names(doc) == ["X", "Male: Control", "Male: Treated", "a", "b"]
    assert not doc.undo_stack.can_undo
    assert name_error(table(doc), column_names={"a": "Male: Treated"}) is not None
    assert name_error(table(doc), column_names={"a": "Treated"}) is None


# --- formula references ----------------------------------------------------------------


def test_references_resolve_full_names_then_unique_short_names(doc: Document) -> None:
    t = table(doc)
    assert resolve_reference(t, "Male: Control").id == "mc"
    assert resolve_reference(t, "control").id == "mc"
    assert resolve_reference(t, "a").id == "a"
    with pytest.raises(LookupError, match="Unknown column 'Nope'"):
        resolve_reference(t, "Nope")


def test_a_short_name_of_two_columns_is_ambiguous(doc: Document) -> None:
    doc.execute(RenameColumn("t", "a", "Control"))
    doc.execute(SetCategory("t", [], "Female", column_ids=["a"]))
    with pytest.raises(AmbiguousColumnError) as info:
        resolve_reference(table(doc), "Control")
    assert str(info.value) == (
        "More than one column is called 'Control': write [Male: Control] or [Female: Control]"
    )
    assert resolve_reference(table(doc), "Female: Control").id == "a"


def test_a_full_name_wins_over_a_short_name(doc: Document) -> None:
    doc.execute(RenameColumn("t", "b", "Control"))  # outside: its full name is "Control"
    assert resolve_reference(table(doc), "Control").id == "b"
    assert resolve_reference(table(doc), "Male: Control").id == "mc"


# --- SetCategory naming ------------------------------------------------------------------


def test_leaving_a_category_takes_the_full_name(doc: Document) -> None:
    check_undo_redo(doc, SetCategory("t", [], None, column_ids=["mc"]))
    assert table(doc).column("mc").name == "Male: Control"
    assert table(doc).column("mc").category_id is None


def test_entering_a_category_drops_its_prefix(doc: Document) -> None:
    doc.execute(RenameColumn("t", "a", " male :Low"))
    doc.undo_stack.clear()
    check_undo_redo(doc, SetCategory("t", [], "Male", column_ids=["a"]))
    assert table(doc).column("a").name == "Low"
    assert names(doc)[1:4] == ["Male: Control", "Male: Treated", "Male: Low"]


def test_a_prefix_of_another_category_stays(doc: Document) -> None:
    doc.execute(RenameColumn("t", "a", "Female: Low"))
    doc.execute(SetCategory("t", [], "Male", column_ids=["a"]))
    assert table(doc).column("a").name == "Female: Low"


def test_a_name_that_is_only_the_prefix_stays(doc: Document) -> None:
    doc.execute(RenameColumn("t", "a", "Male:"))
    doc.execute(SetCategory("t", [], "Male", column_ids=["a"]))
    assert table(doc).column("a").name == "Male:"


def test_out_and_back_in_restores_the_short_name(doc: Document) -> None:
    doc.execute(SetCategory("t", [], None, column_ids=["mc"]))
    doc.execute(SetCategory("t", [], "Male", column_ids=["mc"]))
    assert table(doc).column("mc").name == "Control"


def test_moving_between_categories_keeps_the_short_name(doc: Document) -> None:
    check_undo_redo(doc, SetCategory("t", [], "Female", column_ids=["mt"]))
    assert table(doc).column("mt").name == "Treated"
    assert names(doc)[1:3] == ["Male: Control", "Female: Treated"]


def test_a_category_with_a_series_of_that_name_refuses_the_column(doc: Document) -> None:
    doc.execute(RenameColumn("t", "a", "Control"))
    doc.undo_stack.clear()
    before = copy.deepcopy(doc.tables)
    message = r"^There is already a series called 'Control' in category 'Male'\.$"
    with pytest.raises(CommandError, match=message):
        doc.execute(SetCategory("t", [], "male", column_ids=["a"]))
    doc.execute(GroupColumns("t", ["b"], "Treated"))
    group = table(doc).replicate_groups[0].id
    with pytest.raises(CommandError, match="series called 'Treated' in category 'Male'"):
        doc.execute(SetCategory("t", [group], "Male"))
    doc.undo()
    assert doc.tables == before


def test_two_columns_of_one_name_cannot_enter_one_category(doc: Document) -> None:
    doc.execute(BulkRename("t", columns={"a": "Low", "b": "Male: Low"}))
    with pytest.raises(CommandError, match="series called 'Low' in category 'Male'"):
        doc.execute(SetCategory("t", [], "Male", column_ids=["a", "b"]))


def test_leaving_keeps_the_full_name_so_it_cannot_clash(doc: Document) -> None:
    before = names(doc)
    doc.execute(SetCategory("t", [], None, column_ids=["mc", "mt"]))
    assert names(doc) == before


# --- formulas follow ----------------------------------------------------------------------


def test_renaming_a_category_rewrites_full_references(doc: Document) -> None:
    calc(doc, "[Male: Control] + [Treated] + {A}")
    check_undo_redo(doc, BulkRename("t", categories={"male": "Men"}))
    # A short reference that still names one column is left as written.
    assert formula(doc) == "[Men: Control] + [Treated] + {A}"


def test_renaming_a_column_in_a_category(doc: Document) -> None:
    calc(doc, "[Male: Control] + [Treated]")
    check_undo_redo(doc, RenameColumn("t", "mc", "Ctrl"))
    assert formula(doc) == "[Male: Ctrl] + [Treated]"
    doc.execute(RenameColumn("t", "mt", "Drug"))
    assert formula(doc) == "[Male: Ctrl] + [Drug]"


def test_moving_a_column_rewrites_references(doc: Document) -> None:
    calc(doc, "[Male: Control] * 2")
    check_undo_redo(doc, SetCategory("t", [], None, column_ids=["mc"]))
    assert formula(doc) == "[Male: Control] * 2"  # its full name did not change
    # Into Female from outside: "Male: Control" keeps its name, which still names it.
    doc.execute(SetCategory("t", [], "Female", column_ids=["mc"]))
    assert names(doc)[1] == "Female: Male: Control"
    assert formula(doc) == "[Male: Control] * 2"
    doc.undo()
    doc.undo()
    check_undo_redo(doc, SetCategory("t", [], "Female", column_ids=["mc"]))
    assert formula(doc) == "[Female: Control] * 2"


def test_a_new_column_of_the_same_short_name_keeps_formulas_on_theirs(doc: Document) -> None:
    calc(doc, "[Control] - [a]")
    # Outside every category "Control" is a full name, and [Control] would read it.
    check_undo_redo(doc, RenameColumn("t", "b", "Control"))
    assert formula(doc) == "[Male: Control] - [a]"
    doc.undo()
    assert formula(doc) == "[Control] - [a]"
    doc.execute(SetCategory("t", [], "Female", column_ids=["b"]))
    assert formula(doc) == "[Control] - [a]"
    # A second category's "Control" makes [Control] name two columns.
    check_undo_redo(doc, RenameColumn("t", "b", "Control"))
    assert formula(doc) == "[Male: Control] - [a]"
    assert resolve_reference(table(doc), "Female: Control").id == "b"
    new = Column.new("Control", ColumnRole.Y, category_id=table(doc).column("b").category_id)
    doc.undo()
    check_undo_redo(doc, AddColumn("t", new))
    assert formula(doc) == "[Male: Control] - [a]"


def test_deleting_a_column_marks_its_references(doc: Document) -> None:
    calc(doc, "[a] * 2 + MEAN([b])")
    check_undo_redo(doc, RemoveColumn("t", "a"))
    assert formula(doc) == "#DELETED[a] * 2 + MEAN([b])"
    # A new column called "a" does not bring the formula back to life unnoticed.
    doc.execute(RenameColumn("t", "b", "a"))
    assert formula(doc) == "#DELETED[a] * 2 + MEAN([a])"


def test_deleting_control_does_not_move_control_to_another_column(doc: Document) -> None:
    # "Control" outside every category next to "Male: Control": [Control] reads it.
    doc.execute(RenameColumn("t", "b", "Control"))
    calc(doc, "[Control] - [Male: Control] - [control]")
    assert resolve_reference(table(doc), "Control").id == "b"
    # Once it is gone, [Control] would read "Male: Control": it is marked instead.
    check_undo_redo(doc, RemoveColumn("t", "b"))
    assert formula(doc) == "#DELETED[Control] - [Male: Control] - #DELETED[control]"
    assert resolve_reference(table(doc), "Control").id == "mc"
    with pytest.raises(FormulaError, match="Column 'Control' was deleted"):
        check(formula(doc), names(doc), [])
    doc.undo()
    assert formula(doc) == "[Control] - [Male: Control] - [control]"


def test_deleting_an_unused_column_leaves_formulas_as_written(doc: Document) -> None:
    calc(doc, "[Control] - [a]")
    check_undo_redo(doc, RemoveColumn("t", "b"))
    assert formula(doc) == "[Control] - [a]"


def test_a_deleted_reference_survives_save_and_load(doc: Document, tmp_path: Path) -> None:
    calc(doc, "[Control] - [a]")
    doc.execute(RemoveColumn("t", "a"))
    path = tmp_path / "p.cvp"
    save(doc, path)
    assert load(path).calculations[-1].formula == "[Control] - #DELETED[a]"


def test_importing_a_column_of_the_same_name(doc: Document) -> None:
    calc(doc, "[Control] - [a]")
    imported = Column.new("Control", ColumnRole.Y, [9, 9])
    check_undo_redo(doc, ImportColumns("t", [imported], "data.csv"))
    assert formula(doc) == "[Male: Control] - [a]"
    assert resolve_reference(table(doc), "Control").id == imported.id


def test_grouping_keeps_references(doc: Document) -> None:
    calc(doc, "[Male: Control] + [Male: Treated]")
    check_undo_redo(doc, GroupColumns("t", ["mc", "mt"], "G"))
    # In a replicate group a column's full name is its plain name.
    assert formula(doc) == "[Control] + [Treated]"
    doc.execute(UngroupColumns("t", ["mc", "mt"]))
    assert formula(doc) == "[Control] + [Treated]"


def test_calculated_columns_from_a_category_are_named_by_full_name(doc: Document) -> None:
    doc.execute(add_calculation(doc, Calculation("t", "{A} * 2", Pairing(("mc", "mt")))))
    # They go right after the category, outside it.
    assert names(doc) == [
        "X",
        "Male: Control",
        "Male: Treated",
        "Male: Control_calc",
        "Male: Treated_calc",
        "a",
        "b",
    ]
    table(doc).check()


# --- projects saved before full names -------------------------------------------------------


def test_name_clashes_of_older_projects(doc: Document) -> None:
    t = table(doc)
    t.column("a").name = "Male: Control"  # possible before full names existed
    assert name_clashes(t) == {"a": "Male: Control (2)"}
    t.column("a").name = "a"
    assert name_clashes(t) == {}


def test_fix_name_clashes_renames_in_one_undo_step(doc: Document) -> None:
    calc(doc, "[Male: Control] + [Control]")
    t = table(doc)
    t.column("a").name = "Male: Control"
    t.check(check_names=False)
    notes = fix_name_clashes(doc)
    assert notes == [
        "Renamed 'Male: Control' to 'Male: Control (2)' in table 'T', "
        "so every column name is unique."
    ]
    assert names(doc)[1] == "Male: Control"
    assert t.column("a").name == "Male: Control (2)"
    t.check()
    assert doc.undo_stack.count == 2
