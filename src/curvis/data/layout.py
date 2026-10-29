"""How columns, replicate groups and category groups are laid out in a table.

A table's columns form a tree, read left to right:

- the top level holds category groups, replicate groups without a category, and
  columns without a group;
- a category group holds replicate groups;
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


def layout_error(table: Table) -> str | None:
    """Why the groups of ``table`` break the layout rules, or None if they are fine."""
    groups = {g.id: g for g in table.replicate_groups}
    categories = {c.id: c for c in table.category_groups}
    if len(groups) != len(table.replicate_groups):
        return "duplicate replicate group ids"
    if len(categories) != len(table.category_groups):
        return "duplicate category group ids"
    for column in table.columns:
        if column.group_id is None:
            continue
        if column.group_id not in groups:
            return f"column '{column.name}' refers to a missing replicate group"
        if column.role is not ColumnRole.Y:
            return (
                f"column '{column.name}' has the role {column.role.value}; "
                "replicate groups hold only Y columns"
            )
    for group in table.replicate_groups:
        if group.category_id is not None and group.category_id not in categories:
            return f"replicate group '{group.name}' refers to a missing category group"

    group_cols: dict[str, list[int]] = {g: [] for g in groups}
    category_cols: dict[str, list[int]] = {c: [] for c in categories}
    for i, column in enumerate(table.columns):
        if column.group_id is None:
            continue
        group_cols[column.group_id].append(i)
        category_id = groups[column.group_id].category_id
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
            return f"category group '{name}' has no replicate groups"
        if cols[-1] - cols[0] + 1 != len(cols):
            return f"the groups of category '{name}' are not next to each other"
    return name_error(table)


def name_error(
    table: Table,
    column_names: Mapping[str, str] | None = None,
    group_names: Mapping[str, str] | None = None,
    category_names: Mapping[str, str] | None = None,
) -> str | None:
    """Why the names of ``table`` (with the given names replaced, by id) are not valid.

    Column names are unique in the table. Replicate group names are unique among the
    groups of one category (and among the groups without a category), so the same
    series, e.g. "Control", can appear in every category. Category names are unique.
    Names are compared ignoring case and cannot be blank.
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

    error = check([column_names.get(c.id, c.name) for c in table.columns], "column")
    if error:
        return error
    by_category: dict[str | None, list[str]] = {}
    for group in table.replicate_groups:
        name = group_names.get(group.id, group.name)
        by_category.setdefault(group.category_id, []).append(name)
    for category_id, names in by_category.items():
        where = "" if category_id is None else f" in category '{categories.get(category_id, '')}'"
        error = check(names, "replicate group", where)
        if error:
            return error
    error = check(list(categories.values()), "category")
    if error:
        return error
    if not table.category_factor.strip():
        return "The category factor name cannot be blank."
    return None


def _top_key(table: Table, column: Column) -> tuple[NodeKind, str]:
    """The top-level node a column belongs to."""
    if column.group_id is None:
        return NodeKind.COLUMN, column.id
    category_id = table.replicate_group(column.group_id).category_id
    if category_id is None:
        return NodeKind.GROUP, column.group_id
    return NodeKind.CATEGORY, category_id


def normalized_order(table: Table) -> list[Column]:
    """The columns reordered so every group and category is contiguous.

    Each group gathers its columns where its first column is, and each category its
    groups where its first group is; everything else keeps its order. A table whose
    layout is already valid keeps its order.
    """
    groups = {g.id: g for g in table.replicate_groups}
    group_columns: dict[str, list[Column]] = {}
    category_groups: dict[str, list[str]] = {}
    for column in table.columns:
        if column.group_id is None or column.group_id not in groups:
            continue
        if column.group_id not in group_columns:
            group_columns[column.group_id] = []
            category_id = groups[column.group_id].category_id
            if category_id is not None:
                category_groups.setdefault(category_id, []).append(column.group_id)
        group_columns[column.group_id].append(column)

    order: list[Column] = []
    emitted: set[tuple[NodeKind, str]] = set()
    for column in table.columns:
        if column.group_id is not None and column.group_id not in groups:
            order.append(column)
            continue
        key = _top_key(table, column)
        if key in emitted:
            continue
        emitted.add(key)
        kind, node_id = key
        if kind is NodeKind.COLUMN:
            order.append(column)
        elif kind is NodeKind.GROUP:
            order += group_columns[node_id]
        else:
            for group_id in category_groups[node_id]:
                order += group_columns[group_id]
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
            group_ids = [str(c.group_id) for c in columns[start:end]]
            children = tuple(group_node(g, s, e) for g, s, e in _runs(group_ids, start))
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
