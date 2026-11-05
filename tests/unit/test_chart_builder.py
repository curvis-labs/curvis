"""The default chart spec of a table (the Plot menu)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from curvis.charts.builder import (
    NEEDS_SURVIVAL,
    NEEDS_TWO_WAY,
    NEEDS_X,
    NEEDS_Y,
    SURVIVAL_LATER,
    default_spec,
    error_column_of,
    family_problem,
    with_fit,
)
from curvis.charts.colors import CURVIS
from curvis.charts.spec import (
    AxisSpec,
    ChartFamily,
    ChartVariant,
    ErrorBars,
    FitOverlay,
    LineStyle,
    Scale,
)
from curvis.data import Column, ColumnRole, Table
from tests.unit.charts_tables import (
    column_table,
    error_table,
    hill_fit,
    linear_fit,
    replicate_table,
)


def test_replicate_groups_become_one_series_each_with_sd() -> None:
    table = replicate_table()
    spec = default_spec(table, ChartFamily.XY)
    assert spec.variant is ChartVariant.SCATTER
    assert [s.source for s in spec.series] == [g.id for g in table.replicate_groups]
    assert all(s.error_bars is ErrorBars.SD for s in spec.series)
    assert [s.color for s in spec.series] == list(CURVIS.series[:2])
    assert len({s.symbol for s in spec.series}) == 2
    assert all(s.line_style is LineStyle.NONE for s in spec.series)
    assert spec.x_axis.title == "Dose (µM)" and spec.title == "Assay"
    assert spec.overlays == ()


def test_sem_can_be_asked_for() -> None:
    spec = default_spec(replicate_table(), ChartFamily.XY, error_bars=ErrorBars.SEM)
    assert {s.error_bars for s in spec.series} == {ErrorBars.SEM}


def test_y_column_uses_the_error_column_to_its_right() -> None:
    table = error_table()
    a, err, b = (table.column_by_name(n) for n in ("A", "A err", "B"))
    assert a and err and b
    spec = default_spec(table, ChartFamily.XY)
    by = {s.source: s for s in spec.series}
    assert by[a.id].error_bars is ErrorBars.ERROR_COLUMN and by[a.id].error_column == err.id
    assert by[b.id].error_bars is ErrorBars.NONE and by[b.id].error_column is None
    assert error_column_of(table, b.id) is None  # a text column, then "Stray", follow B


def test_grouped_column_has_no_error_column() -> None:
    table = replicate_table()
    first_y = table.columns[1]
    assert error_column_of(table, first_y.id) is None


def test_fits_are_overlaid_and_concentrations_get_a_log_axis() -> None:
    table = replicate_table()
    other = replicate_table()
    fits = [hill_fit(table), linear_fit(table), hill_fit(other)]
    spec = default_spec(table, ChartFamily.XY, fits)
    assert spec.overlays == (FitOverlay(fits[0].id), FitOverlay(fits[1].id))
    assert spec.x_axis.scale is Scale.LOG
    log_entered = default_spec(table, ChartFamily.XY, [hill_fit(table, x_is_log=True)])
    assert log_entered.x_axis.scale is Scale.LINEAR


def test_with_fit_switches_an_untouched_axis_to_log() -> None:
    table = replicate_table()
    spec = default_spec(table, ChartFamily.XY)
    fit = hill_fit(table)
    assert with_fit(spec, fit).x_axis.scale is Scale.LOG
    assert with_fit(with_fit(spec, fit), fit).overlays == (FitOverlay(fit.id),)
    limited = spec.__class__(**{**spec.__dict__, "x_axis": AxisSpec(minimum=1.0, maximum=5.0)})
    assert with_fit(limited, fit).x_axis.scale is Scale.LINEAR
    assert with_fit(spec, linear_fit(table)).x_axis.scale is Scale.LINEAR


def test_bar_has_one_bar_per_series_with_sd() -> None:
    table = column_table()
    spec = default_spec(table, ChartFamily.BAR)
    assert spec.variant is ChartVariant.BAR
    assert [s.source for s in spec.series] == [c.id for c in table.columns]
    assert {s.error_bars for s in spec.series} == {ErrorBars.SD}
    assert spec.overlays == ()


def test_family_problems() -> None:
    columns = column_table()
    assert family_problem(columns, ChartFamily.XY) == NEEDS_X
    assert family_problem(columns, ChartFamily.BAR) is None
    assert family_problem(columns, ChartFamily.SURVIVAL) == NEEDS_SURVIVAL
    assert family_problem(columns, ChartFamily.INTERACTION) == NEEDS_TWO_WAY
    empty_x = Table(
        "T", [Column.new("X", ColumnRole.X, [float("nan")]), Column.new("A", ColumnRole.Y, [1])]
    )
    assert family_problem(empty_x, ChartFamily.XY) == NEEDS_X
    only_x = Table("T", [Column.new("X", ColumnRole.X, [1.0])])
    assert family_problem(only_x, ChartFamily.XY) == NEEDS_Y
    assert family_problem(only_x, ChartFamily.BAR) == NEEDS_Y
    survival = Table(
        "T", [Column.new("T", ColumnRole.TIME, [1]), Column.new("E", ColumnRole.EVENT, [1])]
    )
    assert family_problem(survival, ChartFamily.SURVIVAL) == SURVIVAL_LATER
    with pytest.raises(ValueError, match=NEEDS_X):
        default_spec(columns, ChartFamily.XY)


ROLES = st.sampled_from(list(ColumnRole))


@given(
    st.lists(
        st.tuples(ROLES, st.lists(st.floats(allow_nan=True), min_size=3, max_size=3)), max_size=6
    )
)
def test_every_table_gives_a_spec_or_a_reason(
    columns: list[tuple[ColumnRole, list[float]]],
) -> None:
    table = Table(
        "T",
        [
            Column.new(f"C{i}", role, values if role.is_numeric else ["x"] * 3)
            for i, (role, values) in enumerate(columns)
        ],
    )
    for family in ChartFamily:
        problem = family_problem(table, family)
        if problem is None:
            spec = default_spec(table, family)
            assert spec.family is family and spec.table_id == table.id
        else:
            assert problem and not problem.endswith(".")


# --- legends and graph types ---------------------------------------------------------------


def test_new_graphs_show_a_legend_on_xy_and_on_bars_with_categories() -> None:
    from dataclasses import replace

    from curvis.data import CategoryGroup

    table = replicate_table()
    assert default_spec(table, ChartFamily.XY).show_legend is True
    assert default_spec(table, ChartFamily.BAR).show_legend is False
    categorised = replace(table, category_groups=[CategoryGroup("Day 1")])
    assert default_spec(categorised, ChartFamily.BAR).show_legend is True


def test_graph_type_within_a_family_keeps_everything_else() -> None:
    from dataclasses import replace

    from curvis.charts.builder import with_variant

    spec = replace(default_spec(replicate_table(), ChartFamily.BAR), title="Kept")
    box = with_variant(spec, ChartVariant.BOX, replicate_table())
    assert box == replace(spec, variant=ChartVariant.BOX)


def test_graph_type_across_families_keeps_series_and_style() -> None:
    from dataclasses import replace

    from curvis.charts.builder import with_variant

    table = error_table()
    xy = default_spec(table, ChartFamily.XY)
    xy = replace(xy, series=tuple(replace(s, size=11.0) for s in xy.series), title="T")
    bar = with_variant(xy, ChartVariant.BOX, table)
    assert bar.family is ChartFamily.BAR and bar.variant is ChartVariant.BOX
    assert [s.source for s in bar.series] == [s.source for s in xy.series]
    assert {s.size for s in bar.series} == {11.0} and bar.title == "T"
    assert {s.error_bars for s in bar.series} <= {ErrorBars.SD, ErrorBars.NONE}
    assert bar.x_axis == AxisSpec()
    back = with_variant(bar, ChartVariant.LINE, table)
    assert back.variant is ChartVariant.LINE
    first = back.series[0]
    assert first.error_bars is ErrorBars.ERROR_COLUMN and first.error_column == table.columns[2].id
    assert back.x_axis.title == "X"


def test_graph_type_needs_what_the_family_needs() -> None:
    from curvis.charts.builder import variant_problem, with_variant

    table = column_table()
    bar = default_spec(table, ChartFamily.BAR)
    assert variant_problem(table, ChartVariant.SCATTER) == NEEDS_X
    assert variant_problem(table, ChartVariant.BOX) is None
    assert variant_problem(table, ChartVariant.VIOLIN) == "This chart type cannot be drawn yet"
    with pytest.raises(ValueError, match=NEEDS_X):
        with_variant(bar, ChartVariant.SCATTER, table)
