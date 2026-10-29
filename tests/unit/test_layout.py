"""Tests for the group layout rules, drag-and-drop targets and bulk-rename helpers."""

import copy

import pytest

from curvis.data import (
    CategoryGroup,
    Column,
    ColumnRole,
    NodeKind,
    ReplicateGroup,
    Table,
    layout_error,
    name_error,
    normalized_order,
)
from curvis.data.layout import layout_tree, moved_order, snap_boundary
from curvis.data.rename import find_replace, numbered_name, numbered_names, renamed

C, G, K = NodeKind.COLUMN, NodeKind.GROUP, NodeKind.CATEGORY


def make_table(spec: str) -> Table:
    """A table from a compact spec: "x a1 a2 | b1" style tokens.

    Each token is a column; ``name.group.category`` puts it in a group (and category);
    ``x`` columns are X columns.
    """
    groups: dict[str, ReplicateGroup] = {}
    categories: dict[str, CategoryGroup] = {}
    columns = []
    for token in spec.split():
        name, *rest = token.split(".")
        group_id = None
        if rest:
            category_id = None
            if len(rest) > 1:
                category_id = categories.setdefault(rest[1], CategoryGroup(rest[1])).id
            group_id = groups.setdefault(rest[0], ReplicateGroup(rest[0], category_id)).id
        role = ColumnRole.X if name.startswith("x") else ColumnRole.Y
        columns.append(Column.new(name, role, [1.0], group_id=group_id, id=name))
    return Table("T", columns, list(groups.values()), list(categories.values()))


def names(table: Table, ids: list[str] | None = None) -> str:
    if ids is None:
        ids = [c.id for c in table.columns]
    return " ".join(ids)


# --- rules -------------------------------------------------------------------------


def test_valid_layout() -> None:
    assert layout_error(make_table("x a.A b.A c.B.K d.C.K e")) is None


def test_groups_must_be_contiguous() -> None:
    error = layout_error(make_table("a.A b c.A"))
    assert error is not None and "next to each other" in error


def test_categories_must_be_contiguous() -> None:
    error = layout_error(make_table("a.A.K b c.B.K"))
    assert error is not None and "category 'K'" in error


def test_only_y_columns_in_groups() -> None:
    table = make_table("a.A b.A")
    table.columns[0].role = ColumnRole.X
    error = layout_error(table)
    assert error is not None and "only Y columns" in error


def test_no_empty_groups_or_missing_references() -> None:
    table = make_table("a.A")
    table.replicate_groups.append(ReplicateGroup("Empty"))
    assert "has no columns" in str(layout_error(table))
    table = make_table("a.A.K")
    table.category_groups.append(CategoryGroup("Empty"))
    assert "has no replicate groups" in str(layout_error(table))
    table = make_table("a.A")
    table.columns[0].group_id = "missing"
    assert "missing replicate group" in str(layout_error(table))
    table = make_table("a.A")
    table.replicate_groups[0].category_id = "missing"
    assert "missing category" in str(layout_error(table))


def test_check_reports_layout_errors() -> None:
    with pytest.raises(ValueError, match="next to each other"):
        make_table("a.A b c.A").check()


def test_group_names_are_unique_per_category() -> None:
    table = make_table("a.A.K b.B.L")
    assert name_error(table) is None
    group_b = table.replicate_groups[1]
    # The same series name may appear in two categories ...
    assert name_error(table, group_names={group_b.id: "a"}) is None
    # ... but not twice in one.
    table = make_table("a.A.K b.B.K")
    error = name_error(table, group_names={table.replicate_groups[1].id: "a"})
    assert error == "There is already a replicate group called 'A' in category 'K'."
    table = make_table("a.A b.B")
    error = name_error(table, group_names={table.replicate_groups[1].id: "a"})
    assert error == "There is already a replicate group called 'A'."


def test_name_rules() -> None:
    table = make_table("a.A.K b.B.L c")
    assert name_error(table, column_names={"a": "C"}) == "There is already a column called 'C'."
    assert name_error(table, column_names={"a": "c", "c": "a"}) is None  # a swap is fine
    assert name_error(table, column_names={"a": " "}) == "A column name cannot be blank."
    k = table.category_groups[0]
    assert name_error(table, category_names={k.id: "l"}) == (
        "There is already a category called 'l'."
    )
    table.category_factor = ""
    assert name_error(table) == "The category factor name cannot be blank."


# --- normalizing and the tree --------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("x a.A b.A c", "x a b c"),  # already valid: unchanged
        ("a.A b c.A", "a c b"),  # a group gathers at its first column
        ("b.A a c.A", "b c a"),
        ("a.A.K b c.B.K d.A.K", "a d c b"),  # groups gather, then categories
        ("c.B.K a.A.L d.B.K", "c d a"),
    ],
)
def test_normalized_order(spec: str, expected: str) -> None:
    table = make_table(spec)
    order = normalized_order(table)
    assert " ".join(c.id for c in order) == expected
    table.columns = order
    assert layout_error(table) is None


def test_layout_tree() -> None:
    table = make_table("x a.A b.A c.B.K d.C.K e")
    tree = layout_tree(table)
    assert [(n.kind, n.start, n.end) for n in tree] == [
        (C, 0, 1),
        (G, 1, 3),
        (K, 3, 5),
        (C, 5, 6),
    ]
    category = tree[2]
    assert [(n.kind, n.start, n.end) for n in category.children] == [(G, 3, 4), (G, 4, 5)]
    assert [n.id for n in tree[1].children] == ["a", "b"]


# --- drag and drop targets ----------------------------------------------------------


def group_id(table: Table, name: str) -> str:
    return next(g.id for g in table.replicate_groups if g.name == name)


def category_id(table: Table, name: str) -> str:
    return next(c.id for c in table.category_groups if c.name == name)


def test_move_ungrouped_column() -> None:
    table = make_table("x a.A b.A y")
    # Dropping inside group A snaps to its nearest edge.
    assert snap_boundary(table, C, "y", 2) == 1
    assert snap_boundary(table, C, "y", 0) == 0
    assert moved_order(table, C, "y", 0) == ["y", "x", "a", "b"]
    assert snap_boundary(table, C, "x", 3) == 3
    assert moved_order(table, C, "x", 3) == ["a", "b", "x", "y"]
    # Dropping where it already is does nothing.
    assert snap_boundary(table, C, "y", 4) is None
    assert snap_boundary(table, C, "y", 3) is None


def test_grouped_column_stays_in_its_group() -> None:
    table = make_table("x a.A b.A c.A y")
    assert snap_boundary(table, C, "a", 4) == 4
    assert moved_order(table, C, "a", 4) == ["x", "b", "c", "a", "y"]
    assert snap_boundary(table, C, "a", 5) == 4  # past the group: clamped to its end
    assert snap_boundary(table, C, "c", 0) == 1
    assert moved_order(table, C, "c", 1) == ["x", "c", "a", "b", "y"]


def test_move_group_and_category() -> None:
    table = make_table("x a.A b.A c.B.K d.C.K e")
    a, b_, c_ = group_id(table, "A"), group_id(table, "B"), group_id(table, "C")
    k = category_id(table, "K")
    # An ungrouped group moves among top-level items; it cannot enter category K.
    assert snap_boundary(table, G, a, 4) is None  # nearest edge is its own
    assert snap_boundary(table, G, a, 5) == 5
    assert moved_order(table, G, a, 5) == ["x", "c", "d", "a", "b", "e"]
    # A group in a category moves only inside it.
    assert snap_boundary(table, G, b_, 6) == 5
    assert moved_order(table, G, b_, 5) == ["x", "a", "b", "d", "c", "e"]
    assert snap_boundary(table, G, c_, 0) == 3
    # A category moves as a whole.
    assert moved_order(table, K, k, 0) == ["c", "d", "x", "a", "b", "e"]
    assert moved_order(table, K, k, 4) is None  # not a sibling boundary
    for kind, node, boundary in [(G, a, 5), (G, b_, 5), (K, k, 0)]:
        moved = copy.deepcopy(table)
        order = moved_order(moved, kind, node, boundary)
        assert order is not None
        moved.columns = [moved.column(i) for i in order]
        assert layout_error(moved) is None


def test_unknown_node() -> None:
    table = make_table("a b")
    assert snap_boundary(table, G, "nope", 0) is None
    assert moved_order(table, G, "nope", 0) is None


# --- bulk rename helpers -----------------------------------------------------------


def test_find_replace() -> None:
    assert find_replace("Rep 1", "rep", "Replicate") == "Replicate 1"
    assert find_replace("Rep 1", "rep", "X", match_case=True) == "Rep 1"
    assert find_replace("a.b.a", ".", "-") == "a-b-a"  # plain text, not a pattern
    assert find_replace("a", "", "x") == "a"
    assert find_replace("a1", "a", r"\1") == r"\11"  # replacement is literal too


def test_numbered_names() -> None:
    assert numbered_name("Dose #", 3) == "Dose 3"
    assert numbered_name("Dose ##", 3) == "Dose 03"
    assert numbered_name("D#-###", 7) == "D7-007"
    assert numbered_name("Rep", 2) == "Rep 2"
    assert numbered_name("", 2) == "2"
    assert numbered_names("R#", 3, start=10, step=5) == ["R10", "R15", "R20"]
    assert numbered_names("R#", 3, start=3, step=-1) == ["R3", "R2", "R1"]


def test_renamed() -> None:
    assert renamed(["a1", "a2"], find="a", replace="b") == ["b1", "b2"]
    assert renamed(["a1", "a2"], pattern="C#", start=0) == ["C0", "C1"]
    assert renamed(["a1"]) == ["a1"]
