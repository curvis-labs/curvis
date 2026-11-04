"""Tables shared by the chart tests."""

import numpy as np

from curvis.analysis.base import Analysis
from curvis.analysis.fit.analyses import Hill4Params, LinearParams
from curvis.data import Column, ColumnRole, ReplicateGroup, Table

DOSES = [0.001, 0.01, 0.1, 1.0, 10.0, 100.0]


def replicate_table() -> Table:
    """X (dose) plus two replicate groups of three columns: Control and Drug."""
    control, drug = ReplicateGroup("Control"), ReplicateGroup("Drug")
    columns = [Column.new("Dose", ColumnRole.X, DOSES, unit="µM")]
    for group, base in ((control, 10.0), (drug, 50.0)):
        for i in range(3):
            values = [base + i + 2 * k for k in range(len(DOSES))]
            columns.append(
                Column.new(f"{group.name} {i + 1}", ColumnRole.Y, values, group_id=group.id)
            )
    return Table("Assay", columns, [control, drug])


def error_table() -> Table:
    """X, Y with an Error column to its right, a Y without one, and a stray Error column."""
    return Table(
        "Errors",
        [
            Column.new("X", ColumnRole.X, [1, 2, 3]),
            Column.new("A", ColumnRole.Y, [1.0, 2.0, 3.0]),
            Column.new("A err", ColumnRole.ERROR, [0.1, -0.2, float("nan")]),
            Column.new("B", ColumnRole.Y, [3.0, 2.0, 1.0]),
            Column.new("Note", ColumnRole.TEXT, ["a", "b", "c"]),
            Column.new("Stray", ColumnRole.ERROR, [1, 1, 1]),
        ],
    )


def column_table() -> Table:
    """Column data: two Y columns of different lengths of values, no X."""
    nan = float("nan")
    return Table(
        "Groups",
        [
            Column.new("Control", ColumnRole.Y, [1.0, 2.0, 4.0, 8.0]),
            Column.new("Treated", ColumnRole.Y, [5.0, 7.0, nan, nan]),
        ],
    )


def hill_fit(table: Table, *, x_is_log: bool = False) -> Analysis:
    inputs = tuple(g.id for g in table.replicate_groups)
    return Analysis("hill_4pl", table.id, inputs, Hill4Params(x_is_log=x_is_log))


def linear_fit(table: Table) -> Analysis:
    inputs = tuple(g.id for g in table.replicate_groups)
    return Analysis("linear_regression", table.id, inputs, LinearParams())


def sigmoid_table() -> Table:
    """Like :func:`replicate_table`, with dose-response values a 4PL fits well."""
    table = replicate_table()
    for i, column in enumerate(table.columns[1:]):
        bottom = 10.0 if i < 3 else 30.0
        noise = (-1.5, 0.5, 1.0, -0.5, 1.5, -1.0)
        column.values = np.array(
            [bottom + 80 / (1 + 0.5 / x) + noise[(k + i) % 6] for k, x in enumerate(DOSES)]
        )
        column.values.flags.writeable = False
    return table
