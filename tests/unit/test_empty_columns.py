"""Empty columns are real: a new table has X, A, B and C, and columns without values
are not ticked by analyses or drawn by new graphs (SPEC.md, "Empty columns")."""

from curvis.analysis.compare.ttest_analysis import TTest
from curvis.analysis.descriptive import DescriptiveStatistics
from curvis.charts.builder import default_spec
from curvis.charts.spec import ChartFamily
from curvis.data import Column, ColumnRole, Table


def table() -> Table:
    """X, two filled Y columns and the empty B and C of a new table."""
    return Table(
        "T",
        [
            Column.new("X", ColumnRole.X, [1, 2, 3], id="x"),
            Column.new("A", ColumnRole.Y, [1, 2, 3], id="a"),
            Column.new("B", ColumnRole.Y, [None, None, None], id="b"),
            Column.new("D", ColumnRole.Y, [4, 5, 6], id="d"),
            Column.new("C", ColumnRole.Y, [None, None, None], id="c"),
        ],
    )


def test_a_new_table_has_three_empty_y_columns() -> None:
    blank = Table.blank()
    assert [(c.name, c.role) for c in blank.columns] == [
        ("X", ColumnRole.X),
        ("A", ColumnRole.Y),
        ("B", ColumnRole.Y),
        ("C", ColumnRole.Y),
    ]
    assert blank.n_rows == 0
    blank.check()


def test_analyses_leave_empty_columns_unticked() -> None:
    t = table()
    assert DescriptiveStatistics().default_inputs(t) == ("a", "d")
    assert TTest().default_inputs(t) == ("a", "d")
    # They can still be ticked by hand.
    assert [c.id for c in DescriptiveStatistics().candidates(t)] == ["a", "b", "d", "c"]


def test_a_calculated_column_counts_as_filled_before_its_values_arrive() -> None:
    t = table()
    assert DescriptiveStatistics().default_inputs(t, calculated={"c"}) == ("a", "d", "c")


def test_new_graphs_leave_empty_columns_out() -> None:
    t = table()
    for family in (ChartFamily.XY, ChartFamily.BAR):
        spec = default_spec(t, family)
        assert [s.source for s in spec.series] == ["a", "d"]
    spec = default_spec(t, ChartFamily.XY, calculated={"b"})
    assert [s.source for s in spec.series] == ["a", "b", "d"]


def test_a_graph_of_only_empty_columns_keeps_them() -> None:
    blank = Table(
        "T",
        [
            Column.new("X", ColumnRole.X, [1.0], id="x"),
            Column.new("A", ColumnRole.Y, [None], id="a"),
        ],
    )
    assert [s.source for s in default_spec(blank, ChartFamily.BAR).series] == ["a"]
