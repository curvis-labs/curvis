"""Tables shared by the chart tests."""

import math

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


TWO_WAY = {
    ("Control", "Male"): ([5.1, 5.4, 4.9], [5.3, 5.0, 5.2]),
    ("Low", "Male"): ([6.0, 6.3, 5.8], [6.1, 5.9, 6.4]),
    ("High", "Male"): ([6.8, 7.1, 6.5], [7.3, 6.9, 7.0]),
    ("Control", "Female"): ([5.0, 5.6, 5.2], [4.8, 5.3, 5.1]),
    ("Low", "Female"): ([5.3, 5.5, 5.1], [5.6, 5.2, 5.4]),
    ("High", "Female"): ([5.6, 5.9, 5.3], [6.1, 5.8, 5.5]),
}


def two_way_table() -> Table:
    """Categories Male and Female (factor "Sex"), each with replicate groups Control,
    Low and High of two columns (unit mM)."""
    from curvis.data import CategoryGroup

    categories = {name: CategoryGroup(name) for name in ("Male", "Female")}
    groups: list[ReplicateGroup] = []
    columns: list[Column] = []
    for (name, category), replicates in TWO_WAY.items():
        group = ReplicateGroup(name, categories[category].id, id=f"{name}_{category}")
        groups.append(group)
        for i, values in enumerate(replicates, 1):
            columns.append(
                Column.new(
                    f"{name} {category} {i}", ColumnRole.Y, values, unit="mM", group_id=group.id
                )
            )
    table = Table("Assay", columns, groups, list(categories.values()))
    table.category_factor = "Sex"
    table.check()
    return table


def two_way_analysis(table: Table) -> Analysis:
    from curvis.analysis.compare.two_way_analysis import TwoWayParams

    inputs = tuple(g.id for g in table.replicate_groups)
    return Analysis("two_way_anova", table.id, inputs, TwoWayParams("Dose"), id="anova")


def two_way_result(table: Table, analysis: Analysis):  # type: ignore[no-untyped-def]
    """The analysis' result, computed as the scheduler would."""
    kind = analysis.analysis_type
    return kind.run(kind.gather(table, analysis), analysis.params)


def survival_table() -> Table:
    """Two Kaplan-Meier curves on one Time column (Prism's layout): ties, censored
    subjects and a curve that drops to 0."""
    times = [2, 3, 3, 5, 6, 8, 8, 9, 11, 12, 14, 15, 17, 20, 22, 25]
    drug = [1, 0, 1, 0, 1, 1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 0]
    placebo = [1, 1, 1, 1, 0, 1, 1, 1, 0, 1, 1, 1, 1, math.nan, math.nan, math.nan]
    return Table(
        "Survival",
        [
            Column.new("Days", ColumnRole.TIME, times, unit="d"),
            Column.new("Drug", ColumnRole.EVENT, drug),
            Column.new("Placebo", ColumnRole.EVENT, placebo),
        ],
    )


def spread_table() -> Table:
    """Three Y columns of 30, 24 and 18 values with different spreads (seeded), for
    violins, histograms and individual values."""
    rng = np.random.default_rng(20261119)
    n = 30
    values = {
        "Control": rng.normal(10.0, 2.0, 30),
        "Low": rng.normal(14.0, 3.0, 24),
        "High": rng.gamma(4.0, 1.5, 18) + 12.0,
    }
    columns = []
    for name, v in values.items():
        padded = np.full(n, np.nan)
        padded[: len(v)] = np.round(v, 2)
        columns.append(Column.new(name, ColumnRole.Y, padded, unit="mM"))
    return Table("Spread", columns)
