"""How columns, replicate groups and category groups are laid out in a table.

A table's columns form a tree, read left to right:

- the top level holds category groups, replicate groups without a category, and
  columns that are in neither;
- a category group holds replicate groups and Y columns without a group (a series
  of one column);
- a replicate group holds Y columns.

Every node covers a contiguous run of columns, so the header can draw a group as one
box above its columns. :func:`layout_error` checks this, :func:`normalized_order`
restores it after a change of membership, and :func:`snap_boundary` and
:func:`moved_order` move one node among its siblings (drag and drop).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from curvis.data.names import name_key
from curvis.data.roles import ColumnRole

if TYPE_CHECKING:
    from curvis.data.column import Column
    from curvis.data.table import Table


class NodeKind(StrEnum):
    """What a layout node is."""

    CATEGORY = "category"
    GROUP = "group"
    COLUMN = "column"


@dataclass(frozen=True)
class Node:
    """A column, replicate group or category group covering columns ``start`` to ``end - 1``."""

    kind: NodeKind
    id: str
    start: int
    end: int
    children: tuple[Node, ...] = ()


def column_category(table: Table, column: Column) -> str | None:
    """The category a column is in: its group's, or its own if it has no group."""
    if column.group_id is None:
        return column.category_id
    return table.replicate_group(column.group_id).category_id


FULL_NAME_SEPARATOR = ": "
"""Between a category's name and a column's name in the column's full name."""


def in_category_directly(column: Column) -> bool:
    """True for a column that sits in a category itself, not through a replicate group."""
    return column.group_id is None and column.category_id is not None


def full_name(
    table: Table,
    column: Column,
    column_names: Mapping[str, str] | None = None,
    category_names: Mapping[str, str] | None = None,
) -> str:
    """The name a column is known by in the table: "category: name" for a column that
    sits in a category directly ("Male: Control"), else its name.

    ``column_names`` and ``category_names`` (by id) replace current names, to check a
    rename before it is made. A column whose category is missing has its plain name.
    """
    name = (column_names or {}).get(column.id, column.name)
    if not in_category_directly(column):
        return name
    assert column.category_id is not None
    category = (category_names or {}).get(column.category_id)
    if category is None:
        category = next((c.name for c in table.category_groups if c.id == column.category_id), None)
        if category is None:
            return name
    return f"{category}{FULL_NAME_SEPARATOR}{name}"


def full_names(table: Table) -> list[str]:
    """The full names of the table's columns, left to right."""
    return [full_name(table, c) for c in table.columns]


def column_by_full_name(table: Table, name: str) -> Column | None:
    """The column whose full name is ``name`` (ignoring case), or None."""
    key = name_key(name)
    return next((c for c in table.columns if name_key(full_name(table, c)) == key), None)


class AmbiguousColumnError(LookupError):
    """A formula's short column name belongs to more than one column."""


def resolve_reference(table: Table, name: str) -> Column:
    """The column a formula's ``[name]`` refers to.

    A full name always resolves ("Male: Control"). Otherwise the short name must belong
    to exactly one column ("Control" where only one category has it). Raises
    ``LookupError`` with a plain message for an unknown name, and its subclass
    :class:`AmbiguousColumnError` for a short name of several columns.
    """
    found = column_by_full_name(table, name)
    if found is not None:
        return found
    key = name_key(name)
    matches = [c for c in table.columns if name_key(c.name) == key]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise LookupError(f"Unknown column '{name}'")
    options = [f"[{full_name(table, c)}]" for c in matches]
    listed = ", ".join(options[:-1]) + " or " + options[-1]
    raise AmbiguousColumnError(f"More than one column is called '{name}': write {listed}")


def name_clashes(table: Table) -> dict[str, str]:
    """New names (by column id) that make the full names of ``table`` unique again.

    Projects saved before full names existed can hold, e.g., "Male: Control" outside
    every category next to "Control" in category Male. Of two clashing columns the one
    outside every category is renamed (else the later one), with " (2)", " (3)", ….
    """
    renames: dict[str, str] = {}
    by_key: dict[str, list[Column]] = {}
    for column in table.columns:
        by_key.setdefault(name_key(full_name(table, column)), []).append(column)
    taken = {name_key(n) for n in full_names(table)}
    for clashing in by_key.values():
        # Before full names, plain names were unique; a clash of equal plain names is
        # not one an older project can hold, so it is left for name_error to report.
        if len(clashing) < 2 or len({name_key(c.name) for c in clashing}) < len(clashing):
            continue
        # Keep one column: the first that sits in a category directly, else the first.
        keep = next((c for c in clashing if in_category_directly(c)), clashing[0])
        for column in clashing:
            if column is keep:
                continue
            n = 2
            while True:
                candidate = f"{column.name} ({n})"
                full = full_name(table, column, {column.id: candidate})
                if name_key(full) not in taken:
                    break
                n += 1
            taken.add(name_key(full))
            renames[column.id] = candidate
    return renames


def layout_error(table: Table, *, check_names: bool = True) -> str | None:
    """Why the groups of ``table`` break the layout rules, or None if they are fine."""
    groups = {g.id: g for g in table.replicate_groups}
    categories = {c.id: c for c in table.category_groups}
    if len(groups) != len(table.replicate_groups):
        return "duplicate replicate group ids"
    if len(categories) != len(table.category_groups):
        return "duplicate category group ids"
    for column in table.columns:
        if column.group_id is not None:
            if column.group_id not in groups:
                return f"column '{column.name}' refers to a missing replicate group"
            if column.category_id is not None:
                return f"column '{column.name}' is in a replicate group and set a category"
            if column.role is not ColumnRole.Y:
                return (
                    f"column '{column.name}' has the role {column.role.value}; "
                    "replicate groups hold only Y columns"
                )
        elif column.category_id is not None:
            if column.category_id not in categories:
                return f"column '{column.name}' refers to a missing category group"
            if column.role is not ColumnRole.Y:
                return (
                    f"column '{column.name}' has the role {column.role.value}; "
                    "categories hold only Y columns"
                )
    for group in table.replicate_groups:
        if group.category_id is not None and group.category_id not in categories:
            return f"replicate group '{group.name}' refers to a missing category group"

    group_cols: dict[str, list[int]] = {g: [] for g in groups}
    category_cols: dict[str, list[int]] = {c: [] for c in categories}
    for i, column in enumerate(table.columns):
        if column.group_id is not None:
            group_cols[column.group_id].append(i)
        category_id = column_category(table, column)
        if category_id is not None:
            category_cols[category_id].append(i)
    for group_id, cols in group_cols.items():
        name = groups[group_id].name
        if not cols:
            return f"replicate group '{name}' has no columns"
        if cols[-1] - cols[0] + 1 != len(cols):
            return f"the columns of replicate group '{name}' are not next to each other"
    for category_id, cols in category_cols.items():
        name = categories[category_id].name
        if not cols:
            return f"category group '{name}' has no series"
        if cols[-1] - cols[0] + 1 != len(cols):
            return f"the series of category '{name}' are not next to each other"
    return name_error(table) if check_names else None


def name_error(
    table: Table,
    column_names: Mapping[str, str] | None = None,
    group_names: Mapping[str, str] | None = None,
    category_names: Mapping[str, str] | None = None,
) -> str | None:
    """Why the names of ``table`` (with the given names replaced, by id) are not valid.

    Full names of columns (see :func:`full_name`) are unique in the table. Series names
    (replicate groups, and columns that sit in a category directly) are unique within
    one category, and replicate
    group names among the groups without a category, so the same series, e.g.
    "Control", can appear in every category. Category names are unique. Names are
    compared ignoring case and cannot be blank.
    """
    column_names = column_names or {}
    group_names = group_names or {}
    category_names = category_names or {}
    categories = {c.id: category_names.get(c.id, c.name) for c in table.category_groups}

    def check(names: list[str], kind: str, where: str = "") -> str | None:
        seen: dict[str, str] = {}
        for name in names:
            if not name.strip():
                return f"A {kind} name cannot be blank."
            key = name_key(name)
            if key in seen:
                return f"There is already a {kind} called '{seen[key]}'{where}."
            seen[key] = name
        return None

    blank = next((c for c in table.columns if not column_names.get(c.id, c.name).strip()), None)
    if blank is not None:
        return "A column name cannot be blank."
    seen: dict[str, Column] = {}
    for column in table.columns:
        key = name_key(full_name(table, column, column_names, categories))
        other = seen.setdefault(key, column)
        if other is not column:
            # Name the column that keeps its name, not the one being renamed.
            existing = column if other.id in column_names else other
            shown = full_name(table, existing, column_names, categories)
            return f"There is already a column called '{shown}'."
    by_category: dict[str | None, list[str]] = {}
    for group in table.replicate_groups:
        name = group_names.get(group.id, group.name)
        by_category.setdefault(group.category_id, []).append(name)
    for category_id, names in by_category.items():
        where = "" if category_id is None else f" in category '{categories.get(category_id, '')}'"
        error = check(names, "replicate group", where)
        if error:
            return error
    # A column in a category is a series too: its name must not repeat a group's there.
    for column in table.columns:
        if column.group_id is not None or column.category_id is None:
            continue
        name = column_names.get(column.id, column.name)
        groups_here = by_category.get(column.category_id, [])
        if name_key(name) in {name_key(n) for n in groups_here}:
            where = categories.get(column.category_id, "")
            return f"There is already a series called '{name}' in category '{where}'."
    error = check(list(categories.values()), "category")
    if error:
        return error
    if not table.category_factor.strip():
        return "The category factor name cannot be blank."
    return None


def _top_key(table: Table, column: Column) -> tuple[NodeKind, str]:
    """The top-level node a column belongs to."""
    category_id = column_category(table, column)
    if category_id is not None:
        return NodeKind.CATEGORY, category_id
    if column.group_id is not None:
        return NodeKind.GROUP, column.group_id
    return NodeKind.COLUMN, column.id


def _member_key(column: Column) -> tuple[NodeKind, str]:
    """The node a column belongs to inside a category: its group, or itself."""
    if column.group_id is not None:
        return NodeKind.GROUP, column.group_id
    return NodeKind.COLUMN, column.id


def normalized_order(table: Table) -> list[Column]:
    """The columns reordered so every group and category is contiguous.

    Each group gathers its columns where its first column is, and each category its
    series where its first series is; everything else keeps its order. A table whose
    layout is already valid keeps its order. Columns that refer to missing groups or
    categories stay where they are (:func:`layout_error` reports them).
    """
    groups = {g.id for g in table.replicate_groups}
    categories = {c.id for c in table.category_groups}

    def broken(column: Column) -> bool:
        if column.group_id is not None:
            if column.group_id not in groups:
                return True
            category_id = table.replicate_group(column.group_id).category_id
        else:
            category_id = column.category_id
        return category_id is not None and category_id not in categories

    # Columns of each node, in order: groups hold columns, categories hold members.
    members: dict[tuple[NodeKind, str], list[Column]] = {}
    for column in table.columns:
        if not broken(column):
            members.setdefault(_member_key(column), []).append(column)
    category_members: dict[str, list[tuple[NodeKind, str]]] = {}
    for column in table.columns:
        if broken(column):
            continue
        category_id = column_category(table, column)
        if category_id is not None:
            keys = category_members.setdefault(category_id, [])
            if _member_key(column) not in keys:
                keys.append(_member_key(column))

    order: list[Column] = []
    emitted: set[tuple[NodeKind, str]] = set()
    for column in table.columns:
        if broken(column):
            order.append(column)
            continue
        key = _top_key(table, column)
        if key in emitted:
            continue
        emitted.add(key)
        if key[0] is NodeKind.CATEGORY:
            for member in category_members[key[1]]:
                order += members[member]
        else:
            order += members[_member_key(column)]
    return order


def _runs[K](keys: list[K], offset: int) -> list[tuple[K, int, int]]:
    """Runs of equal keys as (key, start, end), with positions shifted by ``offset``."""
    found: list[tuple[K, int, int]] = []
    for i, key in enumerate(keys):
        if found and found[-1][0] == key:
            found[-1] = (key, found[-1][1], offset + i + 1)
        else:
            found.append((key, offset + i, offset + i + 1))
    return found


def layout_tree(table: Table) -> list[Node]:
    """The top-level nodes of a table with a valid layout, left to right."""
    columns = table.columns

    def group_node(group_id: str, start: int, end: int) -> Node:
        children = tuple(Node(NodeKind.COLUMN, columns[i].id, i, i + 1) for i in range(start, end))
        return Node(NodeKind.GROUP, group_id, start, end, children)

    top: list[Node] = []
    for (kind, node_id), start, end in _runs([_top_key(table, c) for c in columns], 0):
        if kind is NodeKind.COLUMN:
            top.append(Node(kind, node_id, start, end))
        elif kind is NodeKind.GROUP:
            top.append(group_node(node_id, start, end))
        else:
            keys = [_member_key(c) for c in columns[start:end]]
            children = tuple(
                group_node(k, s, e) if kind_ is NodeKind.GROUP else Node(kind_, k, s, e)
                for (kind_, k), s, e in _runs(keys, start)
            )
            top.append(Node(kind, node_id, start, end, children))
    return top


def find_node(table: Table, kind: NodeKind, node_id: str) -> tuple[Node, tuple[Node, ...]] | None:
    """The node and its siblings (itself included), or None if there is no such node."""

    def search(nodes: tuple[Node, ...]) -> tuple[Node, tuple[Node, ...]] | None:
        for node in nodes:
            if node.kind is kind and node.id == node_id:
                return node, nodes
            found = search(node.children)
            if found is not None:
                return found
        return None

    return search(tuple(layout_tree(table)))


def snap_boundary(table: Table, kind: NodeKind, node_id: str, gap: int) -> int | None:
    """Where a dragged node lands when dropped at column boundary ``gap``.

    Boundary ``i`` is the line left of column ``i`` (``len(columns)`` is the right
    edge). A node moves only among its siblings, so a grouped column stays in its
    group and nothing lands inside another group. Returns the nearest sibling
    boundary, or None when the node would not move.
    """
    found = find_node(table, kind, node_id)
    if found is None:
        return None
    node, siblings = found
    boundaries = [s.start for s in siblings] + [siblings[-1].end]
    best = min(boundaries, key=lambda b: (abs(b - gap), b))
    return None if best in (node.start, node.end) else best


def moved_order(table: Table, kind: NodeKind, node_id: str, boundary: int) -> list[str] | None:
    """Column ids after moving a node to ``boundary`` (see :func:`snap_boundary`).

    Returns None when ``boundary`` is not a place the node can move to.
    """
    if snap_boundary(table, kind, node_id, boundary) != boundary:
        return None
    found = find_node(table, kind, node_id)
    assert found is not None
    node = found[0]
    ids = [c.id for c in table.columns]
    block = ids[node.start : node.end]
    rest = ids[: node.start] + ids[node.end :]
    at = boundary if boundary < node.start else boundary - len(block)
    return rest[:at] + block + rest[at:]
