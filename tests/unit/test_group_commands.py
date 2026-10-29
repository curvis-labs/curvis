"""Tests for the commands that group, categorize, reorder and bulk-rename columns."""

import copy

import pytest

from curvis.core import (
    AddColumn,
    AddTable,
    BulkRename,
    Command,
    CommandError,
    Document,
    GroupColumns,
    MoveColumn,
    RemoveColumn,
    RenameCategoryFactor,
    ReorderColumns,
    SetCategory,
    SetColumnRole,
    UngroupColumns,
)
from curvis.data import Column, ColumnRole, Table


@pytest.fixture
def doc() -> Document:
    d = Document()
    columns = [Column.new("X", ColumnRole.X, [1, 2], id="x")]
    columns += [Column.new(n, ColumnRole.Y, [1, 2], id=n.lower()) for n in "ABCDEF"]
    d.execute(AddTable(Table("T", columns, id="t")))
    d.undo_stack.clear()
    return d


def table(doc: Document) -> Table:
    return doc.tables[0]


def order(doc: Document) -> str:
    return " ".join(c.id for c in table(doc).columns)


def groups(doc: Document) -> dict[str, list[str]]:
    t = table(doc)
    return {g.name: [c.id for c in t.group_columns(g.id)] for g in t.replicate_groups}


def last_summary(doc: Document) -> str:
    """The latest change-log summary, without the "Redo: " that a redo adds."""
    return doc.change_log[-1].summary.removeprefix("Redo: ")


def run_with_undo(doc: Document, command: Command) -> None:
    """Execute, check undo restores the document exactly and redo replays the change."""
    before = copy.deepcopy(doc.tables)
    doc.execute(command)
    table(doc).check()
    after = copy.deepcopy(doc.tables)
    doc.undo()
    assert doc.tables == before
    doc.redo()
    assert doc.tables == after


def test_group_columns(doc: Document) -> None:
    run_with_undo(doc, GroupColumns("t", ["b", "c"], "Control"))
    assert groups(doc) == {"Control": ["b", "c"]}
    assert table(doc).replicate_groups[0].category_id is None
    assert last_summary(doc) == "Group 2 columns as 'Control'"


def test_grouping_gathers_columns_at_the_first(doc: Document) -> None:
    run_with_undo(doc, GroupColumns("t", ["e", "b"], "G"))
    assert order(doc) == "x a b e c d f"
    assert groups(doc) == {"G": ["b", "e"]}


def test_grouping_takes_columns_from_old_groups(doc: Document) -> None:
    doc.execute(GroupColumns("t", ["a", "b"], "Old"))
    run_with_undo(doc, GroupColumns("t", ["a", "b"], "New"))
    assert groups(doc) == {"New": ["a", "b"]}  # "Old" was left empty and removed
    doc.execute(GroupColumns("t", ["c", "d", "e"], "Three"))
    doc.execute(GroupColumns("t", ["d"], "One"))
    assert groups(doc) == {"New": ["a", "b"], "Three": ["c", "e"], "One": ["d"]}
    assert order(doc) == "x a b c e d f"


def test_only_y_columns_can_be_grouped(doc: Document) -> None:
    with pytest.raises(CommandError, match=r"'X' has the role X\. Only Y columns"):
        doc.execute(GroupColumns("t", ["x", "a"], "G"))
    assert table(doc).replicate_groups == []
    assert table(doc).columns[1].group_id is None


def test_group_name_rules(doc: Document) -> None:
    with pytest.raises(CommandError, match="cannot be blank"):
        doc.execute(GroupColumns("t", ["a"], " "))
    doc.execute(GroupColumns("t", ["a"], "G"))
    with pytest.raises(CommandError, match="already a replicate group called 'G'"):
        doc.execute(GroupColumns("t", ["b"], "g"))
    with pytest.raises(CommandError, match="No columns"):
        doc.execute(GroupColumns("t", [], "H"))
    with pytest.raises(CommandError, match="no column"):
        doc.execute(GroupColumns("t", ["zzz"], "H"))
    assert groups(doc) == {"G": ["a"]}


def test_ungroup(doc: Document) -> None:
    doc.execute(GroupColumns("t", ["a", "b", "c"], "G"))
    run_with_undo(doc, UngroupColumns("t", ["b"]))
    assert groups(doc) == {"G": ["a", "c"]}
    assert order(doc) == "x a c b d e f"  # taken from the middle: placed after the group
    run_with_undo(doc, UngroupColumns("t", ["a", "c"]))
    assert groups(doc) == {}
    with pytest.raises(CommandError, match="None of these columns"):
        doc.execute(UngroupColumns("t", ["a"]))


def test_set_category(doc: Document) -> None:
    doc.execute(GroupColumns("t", ["a", "b"], "Control"))
    doc.execute(GroupColumns("t", ["c"], "Treated"))
    doc.execute(GroupColumns("t", ["e", "f"], "Control 2"))
    g = {grp.name: grp.id for grp in table(doc).replicate_groups}
    run_with_undo(doc, SetCategory("t", [g["Control"], g["Control 2"]], "Female"))
    t = table(doc)
    (female,) = t.category_groups
    assert female.name == "Female"
    assert t.replicate_group(g["Control"]).category_id == female.id
    # The groups of a category are gathered next to each other.
    assert order(doc) == "x a b e f c d"
    # An existing category is found by name, ignoring case.
    doc.execute(SetCategory("t", [g["Treated"]], "female"))
    assert len(t.category_groups) == 1
    assert order(doc) == "x a b e f c d"
    # Removing groups from their category; the empty category is removed.
    run_with_undo(doc, SetCategory("t", list(g.values()), None))
    assert t.category_groups == []
    assert last_summary(doc) == "Remove 3 series from their category"


def test_same_series_name_in_two_categories(doc: Document) -> None:
    doc.execute(GroupColumns("t", ["a", "b"], "Control"))
    doc.execute(GroupColumns("t", ["c", "d"], "Treated"))
    g = {grp.name: grp.id for grp in table(doc).replicate_groups}
    doc.execute(SetCategory("t", [g["Control"], g["Treated"]], "Female"))
    doc.execute(GroupColumns("t", ["e", "f"], "Control"))  # outside a category: fine
    g2 = next(grp.id for grp in table(doc).replicate_groups if grp.id not in g.values())
    doc.execute(SetCategory("t", [g2], "Male"))
    with pytest.raises(CommandError, match="already a replicate group called 'Control'"):
        doc.execute(SetCategory("t", [g2], "Female"))
    table(doc).check()


def test_new_group_keeps_a_shared_category(doc: Document) -> None:
    doc.execute(GroupColumns("t", ["a", "b", "c"], "G"))
    gid = table(doc).replicate_groups[0].id
    doc.execute(SetCategory("t", [gid], "K"))
    doc.execute(GroupColumns("t", ["c"], "H"))
    t = table(doc)
    k = t.category_groups[0].id
    assert [grp.category_id for grp in t.replicate_groups] == [k, k]


def test_reorder(doc: Document) -> None:
    run_with_undo(doc, ReorderColumns("t", list("xfedcba"), "Move column 'F'"))
    assert order(doc) == "x f e d c b a"
    assert last_summary(doc) == "Move column 'F'"
    with pytest.raises(CommandError, match="every column"):
        doc.execute(ReorderColumns("t", list("xfedcb")))
    doc.execute(GroupColumns("t", ["a", "b"], "G"))
    before = order(doc)
    with pytest.raises(CommandError, match="not next to each other"):
        doc.execute(ReorderColumns("t", ["x", "f", "e", "d", "b", "c", "a"]))
    assert order(doc) == before


def test_column_commands_respect_groups(doc: Document) -> None:
    doc.execute(GroupColumns("t", ["a", "b", "c"], "G"))
    gid = table(doc).replicate_groups[0].id
    # Adding an ungrouped column inside the group is refused; into the group is fine.
    with pytest.raises(CommandError, match="not next to each other"):
        doc.execute(AddColumn("t", Column.new("N", ColumnRole.Y), 2))
    run_with_undo(doc, AddColumn("t", Column.new("N", ColumnRole.Y, group_id=gid), 2))
    assert groups(doc) == {"G": ["a", table(doc).columns[2].id, "b", "c"]}
    # Moving a grouped column out of its group is refused.
    with pytest.raises(CommandError, match="not next to each other"):
        doc.execute(MoveColumn("t", "a", 6))
    # A grouped column must stay Y.
    with pytest.raises(CommandError, match="Ungroup it first"):
        doc.execute(SetColumnRole("t", "a", ColumnRole.ERROR))
    table(doc).check()


def test_removing_the_last_column_removes_the_group(doc: Document) -> None:
    doc.execute(GroupColumns("t", ["a"], "G"))
    gid = table(doc).replicate_groups[0].id
    doc.execute(SetCategory("t", [gid], "K"))
    run_with_undo(doc, RemoveColumn("t", "a"))
    assert table(doc).replicate_groups == []
    assert table(doc).category_groups == []
    doc.undo()
    assert groups(doc) == {"G": ["a"]}
    assert table(doc).category_groups[0].name == "K"


def test_rename_category_factor(doc: Document) -> None:
    assert table(doc).category_factor == "Category"
    run_with_undo(doc, RenameCategoryFactor("t", "Sex"))
    assert table(doc).category_factor == "Sex"
    assert last_summary(doc) == "Rename category factor 'Category' to 'Sex'"
    with pytest.raises(CommandError, match="cannot be blank"):
        doc.execute(RenameCategoryFactor("t", ""))


def test_bulk_rename(doc: Document) -> None:
    doc.execute(GroupColumns("t", ["a", "b"], "G"))
    gid = table(doc).replicate_groups[0].id
    doc.execute(SetCategory("t", [gid], "K"))
    kid = table(doc).category_groups[0].id
    n_log = len(doc.change_log)
    run_with_undo(doc, BulkRename("t", {"a": "B", "b": "A", "c": "Rep 3"}, {gid: "H"}, {kid: "L"}))
    t = table(doc)
    assert [c.name for c in t.columns[:4]] == ["X", "B", "A", "Rep 3"]
    assert t.replicate_groups[0].name == "H"
    assert t.category_groups[0].name == "L"
    # One undo step for the whole rename.
    assert len(doc.change_log) == n_log + 3
    assert doc.change_log[n_log].summary == "Rename 3 columns and 1 group and 1 category"
    doc.undo()
    assert [c.name for c in t.columns[:4]] == ["X", "A", "B", "C"]
    assert t.replicate_groups[0].name == "G"


def test_bulk_rename_is_checked_as_a_whole(doc: Document) -> None:
    with pytest.raises(CommandError, match="already a column called"):
        doc.execute(BulkRename("t", {"a": "Z", "b": "z"}))
    with pytest.raises(CommandError, match="no names"):
        doc.execute(BulkRename("t"))
    with pytest.raises(CommandError, match="nothing with id"):
        doc.execute(BulkRename("t", groups={"nope": "G"}))
    assert [c.name for c in table(doc).columns] == ["X", *"ABCDEF"]


def test_bulk_rename_summary_for_one_item(doc: Document) -> None:
    doc.execute(BulkRename("t", {"a": "Dose"}))
    assert last_summary(doc) == "Rename column 'A' to 'Dose'"


def test_touched_nodes(doc: Document) -> None:
    command = GroupColumns("t", ["a", "b"], "G")
    doc.execute(command)
    assert command.touched_nodes == ("t", "a", "b")
    reorder = ReorderColumns("t", list("xbacdef"))
    doc.execute(reorder)
    assert reorder.touched_nodes == ("t", "b", "a")
    doc.undo()
    assert reorder.touched_nodes == ("t", "b", "a")


# --- columns directly in a category ----------------------------------------------------


def test_columns_in_a_category(doc: Document) -> None:
    doc.execute(GroupColumns("t", ["a", "b"], "Control"))
    gid = table(doc).replicate_groups[0].id
    run_with_undo(doc, SetCategory("t", [gid], "Female", column_ids=["e"]))
    t = table(doc)
    (female,) = t.category_groups
    assert t.column("e").category_id == female.id
    assert order(doc) == "x a b e c d f"  # the category's series are gathered
    assert last_summary(doc) == "Put 2 series in category 'Female'"
    # Grouping a column that is in the category keeps the category on the group.
    doc.execute(GroupColumns("t", ["e"], "Treated"))
    assert t.column("e").category_id is None
    assert t.replicate_groups[-1].category_id == female.id
    t.check()


def test_column_in_a_category_rules(doc: Document) -> None:
    doc.execute(GroupColumns("t", ["a", "b"], "Control"))
    with pytest.raises(CommandError, match="put the group in the category instead"):
        doc.execute(SetCategory("t", [], "K", column_ids=["a"]))
    with pytest.raises(CommandError, match="Only Y columns can be put in a category"):
        doc.execute(SetCategory("t", [], "K", column_ids=["x"]))
    doc.execute(SetCategory("t", [], "K", column_ids=["c"]))
    with pytest.raises(CommandError, match="Remove it from the category first"):
        doc.execute(SetColumnRole("t", "c", ColumnRole.TEXT))
    # A column series cannot share a name with a group series in the same category.
    gid = table(doc).replicate_groups[0].id
    doc.execute(BulkRename("t", {"c": "Control 2"}))
    doc.execute(SetCategory("t", [gid], "K"))
    with pytest.raises(CommandError, match="already a series called 'control'"):
        doc.execute(BulkRename("t", {"c": "control"}))
    table(doc).check()


def test_removing_the_last_column_of_a_category_removes_it(doc: Document) -> None:
    doc.execute(SetCategory("t", [], "K", column_ids=["c"]))
    run_with_undo(doc, RemoveColumn("t", "c"))
    assert table(doc).category_groups == []
    doc.undo()
    assert table(doc).column("c").category_id == table(doc).category_groups[0].id


def test_column_inserted_into_a_category(doc: Document) -> None:
    doc.execute(SetCategory("t", [], "K", column_ids=["b", "c"]))
    kid = table(doc).category_groups[0].id
    with pytest.raises(CommandError, match="not next to each other"):
        doc.execute(AddColumn("t", Column.new("N", ColumnRole.Y), 3))  # between b and c
    run_with_undo(doc, AddColumn("t", Column.new("N", ColumnRole.Y, category_id=kid), 3))
