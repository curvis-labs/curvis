"""Columns, column roles, replicate and category groups, and tables."""

from curvis.data.column import CellValue, Column
from curvis.data.groups import CategoryGroup, ReplicateGroup
from curvis.data.ids import new_id
from curvis.data.layout import Node, NodeKind, layout_error, name_error, normalized_order
from curvis.data.names import name_key
from curvis.data.pairing import Pairing, role_name_problem, role_values
from curvis.data.replicates import ReplicateStats, Series, replicate_stats, row_stats, series
from curvis.data.roles import ColumnRole
from curvis.data.table import DEFAULT_CATEGORY_FACTOR, Table

__all__ = [
    "DEFAULT_CATEGORY_FACTOR",
    "CategoryGroup",
    "CellValue",
    "Column",
    "ColumnRole",
    "Node",
    "NodeKind",
    "Pairing",
    "ReplicateGroup",
    "ReplicateStats",
    "Series",
    "Table",
    "layout_error",
    "name_error",
    "name_key",
    "new_id",
    "normalized_order",
    "replicate_stats",
    "role_name_problem",
    "role_values",
    "row_stats",
    "series",
]
