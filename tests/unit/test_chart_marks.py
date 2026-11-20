"""The layouts of the new bar-family graphs: grouped clusters, horizontal bars, violins,
columns of values and histograms (curvis.charts.axes, .grouping, .marks, .builder)."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from curvis.charts.axes import CLUSTER_WIDTH, axes_layout
from curvis.charts.builder import default_spec, with_grouping, with_palette, with_variant
from curvis.charts.colors import BRIGHT, CURVIS
from curvis.charts.data import compute, gather
from curvis.charts.grouping import NO_CATEGORY, Entry, grouped_layout
from curvis.charts.marks import Lines, Polygon, Rect, bar_marks
from curvis.charts.spec import ChartFamily, ChartVariant, Grouping, LineStyle
from tests.unit.charts_tables import column_table, spread_table, two_way_table


def _drawn(spec, table):  # type: ignore[no-untyped-def]
    data = compute(gather(table, spec, {}))
    layout = axes_layout(spec, data)
    return data, layout, bar_marks(spec, data, layout)


def test_grouping_matches_series_by_name_across_categories() -> None:
    entries = [
        Entry("a", "Control", "Male"),
        Entry("b", "Drug", "Male"),
        Entry("c", "control", "Female"),  # the same name, as two-way ANOVA matches it
        Entry("d", "Extra", None),
    ]
    by_series = grouped_layout(entries, Grouping.BY_SERIES)
    assert by_series.clusters == ("Control", "Drug", "Extra")
    assert by_series.slots == 3  # Male, Female, No category
    assert [(p.cluster, p.slot) for p in by_series.places] == [(0, 0), (1, 0), (0, 1), (2, 2)]
    assert [n for _k, n in by_series.legend()] == ["Male", "Female", NO_CATEGORY]
    by_category = grouped_layout(entries, Grouping.BY_CATEGORY)
    assert by_category.clusters == ("Male", "Female", NO_CATEGORY)
    assert [n for _k, n in by_category.legend()] == ["Control", "Drug", "Extra"]


def test_a_new_bar_graph_of_categories_is_grouped_and_colored_by_category() -> None:
    table = two_way_table()
    spec = default_spec(table, ChartFamily.BAR)
    assert spec.grouping is Grouping.BY_SERIES and spec.show_legend
    _data, layout, marks = _drawn(spec, table)
    # Clusters Control, Low, High at 0, 1, 2; Male left of Female in each.
    assert [t.label for t in layout.x.major] == ["Control", "Low", "High"]
    slot = CLUSTER_WIDTH / 2
    assert layout.slot == pytest.approx(slot)
    male = [g.id for g in table.replicate_groups if g.category_id == table.category_groups[0].id]
    for i, source in enumerate(male):
        assert layout.positions[source] == pytest.approx(i - CLUSTER_WIDTH / 2 + slot / 2)
    colors = {s.source: s.color for s in spec.series}
    assert {colors[s] for s in male} == {CURVIS.series[0]}
    assert [e.name for e in marks.legend] == ["Male", "Female"]
    bars = [s for s in marks.shapes if isinstance(s, Rect)]
    assert len(bars) == 6 and all(b.x1 - b.x0 == pytest.approx(slot) for b in bars)


def test_by_category_colors_by_series_name() -> None:
    table = two_way_table()
    spec = with_grouping(default_spec(table, ChartFamily.BAR), Grouping.BY_CATEGORY, table)
    _data, layout, marks = _drawn(spec, table)
    assert [t.label for t in layout.x.major] == ["Male", "Female"]
    assert [e.name for e in marks.legend] == ["Control", "Low", "High"]
    assert len({s.color for s in spec.series}) == 3
    back = with_grouping(spec, Grouping.NONE, table)
    assert [s.color for s in back.series] == [CURVIS.series_color(i) for i in range(6)]


def test_horizontal_bars_put_the_first_series_at_the_top_and_swap_the_axes() -> None:
    table = spread_table()
    bar = default_spec(table, ChartFamily.BAR)
    bar = replace(bar, y_axis=replace(bar.y_axis, title="mM"))
    spec = with_variant(bar, ChartVariant.HORIZONTAL_BAR, table)
    assert spec.x_axis.title == "mM" and spec.y_axis.title == ""
    _data, layout, marks = _drawn(spec, table)
    assert layout.horizontal
    names = [t.label for t in sorted(layout.y.major, key=lambda t: -t.value)]
    assert names == ["Control", "Low", "High"]
    bars = [s for s in marks.shapes if isinstance(s, Rect)]
    assert all(b.x0 == 0.0 and b.x1 > 0 for b in bars)  # bars run along X
    assert with_variant(spec, ChartVariant.BAR, table).y_axis.title == "mM"


def test_violins_fill_their_place_at_the_peak_and_show_their_quartiles() -> None:
    table = spread_table()
    spec = with_variant(default_spec(table, ChartFamily.BAR), ChartVariant.VIOLIN, table)
    data, layout, marks = _drawn(spec, table)
    outlines = [s for s in marks.shapes if isinstance(s, Polygon)]
    assert len(outlines) == 3
    for outline, c in zip(outlines, data.categories, strict=True):
        at = layout.positions[c.source]
        assert np.max(outline.xs) - at == pytest.approx(0.4)  # half of the violin width
        assert c.violin is not None
        assert np.min(outline.ys) == pytest.approx(c.box.min)  # trimmed
    dashed = [ln for ln in marks.lines if ln.style is LineStyle.DASH]
    assert len(dashed) == 3 and all(len(ln.segments) == 2 for ln in dashed)
    untrimmed = _drawn(replace(spec, violin_trim=False), table)[0]
    assert all(
        u.violin is not None and t.violin is not None and u.violin.grid[0] < t.violin.grid[0]
        for u, t in zip(untrimmed.categories, data.categories, strict=True)
    )


def test_individual_values_do_not_cover_each_other() -> None:
    table = spread_table()
    spec = replace(default_spec(table, ChartFamily.BAR), show_points=True)
    data, layout, marks = _drawn(spec, table)
    assert [len(p.xs) for p in marks.points] == [c.n for c in data.categories]
    for points, c in zip(marks.points, data.categories, strict=True):
        at = layout.positions[c.source]
        assert np.all(np.abs(points.xs - at) <= 0.6 * 0.8 / 2 + 1e-12)
        assert np.array_equal(np.sort(points.ys), np.sort(c.values))
        # Symbols at the same height sit apart.
        same = points.ys == points.ys[0]
        assert len(set(points.xs[same].tolist())) == int(same.sum())


def test_column_scatter_draws_mean_and_error_over_the_values() -> None:
    table = spread_table()
    spec = with_variant(default_spec(table, ChartFamily.BAR), ChartVariant.COLUMN_SCATTER, table)
    data, _layout, marks = _drawn(spec, table)
    assert not marks.shapes and len(marks.points) == 3
    assert len(marks.over) == 3
    for over, c in zip(marks.over, data.categories, strict=True):
        assert isinstance(over, Lines) and over.color == spec.palette.ink
        assert over.segments[0][0][1] == pytest.approx(c.mean)


def test_histogram_bars_and_legend() -> None:
    table = spread_table()
    spec = with_variant(default_spec(table, ChartFamily.BAR), ChartVariant.HISTOGRAM, table)
    assert spec.show_legend and spec.y_axis.title == "Number of values"
    data, layout, marks = _drawn(spec, table)
    assert data.histogram is not None
    breaks = data.histogram.breaks
    assert (layout.x.low, layout.x.high) == (breaks[0], breaks[-1])
    total = sum(int(s.y1) for s in marks.shapes if isinstance(s, Rect))
    assert total == sum(
        c.n for c in compute(gather(table, default_spec(table, ChartFamily.BAR), {})).categories
    )
    assert [e.name for e in marks.legend] == ["Control", "Low", "High"]
    assert all(s.fill_alpha < 1 for s in marks.shapes if isinstance(s, Rect))


def test_types_that_cannot_be_grouped_color_each_series_and_keep_the_grouping() -> None:
    table = two_way_table()
    grouped = default_spec(table, ChartFamily.BAR)
    histogram = with_variant(grouped, ChartVariant.HISTOGRAM, table)
    assert histogram.grouping is Grouping.BY_SERIES
    assert len({s.color for s in histogram.series}) == 6
    data = compute(gather(table, histogram, {}))
    assert data.histogram is not None and data.histogram.names[0] == "Control · Male"
    back = with_variant(histogram, ChartVariant.BOX, table)
    assert [s.color for s in back.series] == [s.color for s in grouped.series]


def test_a_new_palette_keeps_shared_colors_shared() -> None:
    table = two_way_table()
    grouped = default_spec(table, ChartFamily.BAR)
    bright = with_palette(grouped, BRIGHT)
    assert bright.palette == BRIGHT
    assert len({s.color for s in bright.series}) == 2
    assert {s.color for s in bright.series} == set(BRIGHT.series[:2])


def test_log_value_axis_for_boxes_leaves_out_values_at_or_below_zero() -> None:
    from curvis.charts.spec import Scale

    table = column_table()
    spec = with_variant(default_spec(table, ChartFamily.BAR), ChartVariant.COLUMN_SCATTER, table)
    spec = replace(spec, y_axis=replace(spec.y_axis, scale=Scale.LOG))
    data = compute(gather(table, spec, {}))
    assert np.allclose(data.categories[0].values, np.log10([1, 2, 4, 8]))
