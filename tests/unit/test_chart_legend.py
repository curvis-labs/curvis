"""Where the legend goes (curvis.charts.legend): it never takes over the page, and inside
the plot it keeps clear of the data."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
import pytest

from curvis.charts.axes import CM_PER_INCH, LEGEND_ROW_EM, legend_entry_width
from curvis.charts.builder import default_spec
from curvis.charts.data import ChartData, compute, gather
from curvis.charts.legend import (
    LEGEND_BELOW_SHARE,
    LEGEND_MIN_CHARS,
    Corner,
    LegendPlace,
    _segments_cross,
    graph_layout,
    legend_names,
    occupancy,
    outside_legend,
    smallest_text,
)
from curvis.charts.render_mpl.figure import chart_rc, make_figure
from curvis.charts.spec import (
    ChartFamily,
    ChartSpec,
    ChartVariant,
    LegendPosition,
    Symbol,
    spec_from_json,
    spec_to_json,
)
from curvis.data import Column, ColumnRole, Table
from tests.unit.charts_tables import (
    SPECTRA_NAMES,
    column_table,
    replicate_table,
    spectra_table,
    survival_table,
)

SHORT = ("BL21 80", "BL21 250", "BL21 800", "C41 80", "C41 800", "C43 80", "C43 800")


def spectra(names: tuple[str, ...] = SPECTRA_NAMES, **changes: Any) -> tuple[ChartSpec, ChartData]:
    """The graph of the bug report: absorbance spectra as lines, one per sample."""
    table = spectra_table(names)
    spec = replace(default_spec(table, ChartFamily.XY), variant=ChartVariant.LINE, title="Data 1")
    spec = replace(spec, series=tuple(replace(s, symbol=Symbol.NONE) for s in spec.series))
    spec = replace(spec, **changes)
    return spec, compute(gather(table, spec, {}))


def drawn(spec: ChartSpec, data: ChartData) -> tuple[Any, Any]:
    """The exported figure of ``spec`` and its canvas, drawn."""
    import matplotlib as mpl
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    with mpl.rc_context(chart_rc(spec)):
        figure = make_figure(spec, data)
        canvas = FigureCanvasAgg(figure)
        canvas.draw()
    return figure, canvas


def page_height(spec: ChartSpec) -> float:
    return spec.page_size_cm[1] / CM_PER_INCH * 72.0


# --- outside ---------------------------------------------------------------------------


def test_a_short_legend_stays_right_of_the_plot() -> None:
    table = replicate_table()
    spec = default_spec(table, ChartFamily.XY)
    data = compute(gather(table, spec, {}))
    layout, legend = graph_layout(spec, data, legend_names(spec, data))
    assert legend is not None
    assert legend.place is LegendPlace.RIGHT
    assert legend.names == ("Control", "Drug")
    assert legend.text_size == spec.text_size
    assert layout.legend_room[1] == 0.0


def test_seven_long_names_keep_the_plot_most_of_the_page() -> None:
    """The bug: seven long names under the plot squashed it to a strip."""
    spec, data = spectra()
    names = legend_names(spec, data)
    layout, legend = graph_layout(spec, data, names)
    assert legend is not None and legend.place is LegendPlace.BELOW
    tall = legend.rows * LEGEND_ROW_EM * legend.text_size
    assert tall <= LEGEND_BELOW_SHARE * page_height(spec)
    assert legend.columns >= 2
    assert legend.more == 0
    assert len(set(legend.names)) == len(names), "shortened names stay apart"
    # The two names that differ only near the end keep their ends.
    assert legend.names[0].endswith("500 ml_bl") and legend.names[1].endswith("250 ml_bl")
    assert layout.legend_room[1] == pytest.approx(tall / page_height(spec))


def test_the_text_gets_smaller_before_names_are_shortened() -> None:
    names = [f"Condition number {i}" for i in range(1, 7)]
    spec, _data = spectra(tuple(names))
    legend = outside_legend(spec, names)
    assert legend.place is LegendPlace.BELOW
    assert smallest_text(spec.text_size) <= legend.text_size < spec.text_size
    assert legend.names == tuple(names)


def test_too_many_entries_are_counted_in_a_last_one() -> None:
    names = [f"Sample {i}" for i in range(1, 61)]
    spec, data = spectra(tuple(names))
    _layout, legend = graph_layout(spec, data, names)
    assert legend is not None and legend.place is LegendPlace.BELOW
    assert legend.more > 0
    assert legend.shown + legend.more == 60
    assert legend.texts[-1] == f"+{legend.more} more"
    assert legend.rows * LEGEND_ROW_EM * legend.text_size <= (
        LEGEND_BELOW_SHARE * page_height(spec)
    )
    assert legend.text_size == smallest_text(spec.text_size)
    assert all(len(n) >= LEGEND_MIN_CHARS or n == names[i] for i, n in enumerate(legend.names))


def test_a_name_wider_than_the_page_is_shortened() -> None:
    names = ["Cells treated with a very long compound name for a very long time"] * 2
    spec = replace(spectra(SHORT)[0], aspect_ratio="tall")
    legend = outside_legend(spec, names)
    assert legend.place is LegendPlace.BELOW
    assert legend.text_size == smallest_text(spec.text_size)
    assert all("…" in n and n.endswith("long time") for n in legend.names)
    width = spec.page_size_cm[0] / CM_PER_INCH * 72.0
    assert legend_entry_width(legend.names[0], legend.text_size) <= width


@pytest.mark.parametrize("count", [1, 3, 7, 12, 25, 60, 200])
@pytest.mark.parametrize("aspect", ["tall", "wide", "square"])
def test_a_legend_never_takes_more_than_its_share(count: int, aspect: str) -> None:
    names = [f"A rather long sample name number {i}" for i in range(count)]
    spec = replace(spectra(SHORT)[0], aspect_ratio=aspect)
    legend = outside_legend(spec, names)
    if legend.place is LegendPlace.BELOW:
        tall = legend.rows * LEGEND_ROW_EM * legend.text_size
        assert tall <= LEGEND_BELOW_SHARE * page_height(spec) + 1e-9
    else:
        assert count * LEGEND_ROW_EM * legend.text_size <= page_height(spec)
    assert legend.shown + legend.more == count


def test_the_exported_plot_keeps_its_height() -> None:
    """In the export of the bug's graph the plot is still most of the page."""
    spec, data = spectra()
    figure, _canvas = drawn(spec, data)
    plot = figure.axes[0].get_window_extent()
    assert plot.height / (figure.get_figheight() * figure.dpi) > 0.4


# --- inside ----------------------------------------------------------------------------


def test_inside_goes_to_the_empty_corner() -> None:
    spec, data = spectra(SHORT, legend_position=LegendPosition.INSIDE)
    layout, legend = graph_layout(spec, data, legend_names(spec, data))
    assert legend is not None
    assert legend.place is LegendPlace.INSIDE
    assert legend.corner is Corner.UPPER_RIGHT  # the peaks are on the left
    assert layout.legend_room == (0.0, 0.0)


def test_inside_picks_another_corner_when_the_first_has_data() -> None:
    """Rising data fill the upper right; the upper left is empty."""
    x = np.linspace(0, 10, 30)
    table = Table(
        "Rise",
        [Column.new("X", ColumnRole.X, x)]
        + [Column.new(f"S{i}", ColumnRole.Y, x**2 * (1 + 0.1 * i)) for i in range(3)],
    )
    spec = replace(default_spec(table, ChartFamily.XY), legend_position=LegendPosition.INSIDE)
    data = compute(gather(table, spec, {}))
    _layout, legend = graph_layout(spec, data, legend_names(spec, data))
    assert legend is not None and legend.corner is Corner.UPPER_LEFT


def test_inside_stays_outside_when_every_corner_has_data() -> None:
    table = column_table()
    spec = replace(
        default_spec(table, ChartFamily.BAR),
        show_legend=True,
        legend_position=LegendPosition.INSIDE,
    )
    data = compute(gather(table, spec, {}))
    _layout, legend = graph_layout(spec, data, legend_names(spec, data))
    assert legend is not None and legend.place is LegendPlace.RIGHT


def _inside_cases() -> dict[str, tuple[ChartSpec, ChartData]]:
    cases = {"spectra": spectra(SHORT, legend_position=LegendPosition.INSIDE)}
    table = replicate_table()
    spec = replace(default_spec(table, ChartFamily.XY), legend_position=LegendPosition.INSIDE)
    cases["scatter"] = (spec, compute(gather(table, spec, {})))
    table = survival_table()
    spec = replace(default_spec(table, ChartFamily.SURVIVAL), legend_position=LegendPosition.INSIDE)
    cases["survival"] = (spec, compute(gather(table, spec, {})))
    table = Table(
        "Falling",
        [
            Column.new(name, ColumnRole.Y, values)
            for name, values in (
                ("High", [9.0, 9.5, 10.0]),
                ("Middle", [5.0, 6.0, 5.5]),
                ("Low", [1.0, 1.5, 2.0]),
            )
        ],
    )
    spec = replace(
        default_spec(table, ChartFamily.BAR),
        show_legend=True,
        legend_position=LegendPosition.INSIDE,
    )
    cases["bar"] = (spec, compute(gather(table, spec, {})))
    return cases


INSIDE = _inside_cases()


@pytest.mark.parametrize("case", INSIDE)
def test_an_exported_inside_legend_covers_no_data(case: str) -> None:
    """Drawn by Matplotlib without its legend, the page under the legend's box is empty:
    no line, point, error bar, band or bar reaches into it."""
    spec, data = INSIDE[case]
    _layout, legend = graph_layout(spec, data, legend_names(spec, data, hidden=False))
    assert legend is not None and legend.place is LegendPlace.INSIDE, case
    import matplotlib as mpl

    figure, canvas = drawn(spec, data)
    ax = figure.axes[0]
    box = ax.get_legend().get_window_extent()
    plot = ax.get_window_extent()
    assert plot.x0 <= box.x0 < box.x1 <= plot.x1 and plot.y0 <= box.y0 < box.y1 <= plot.y1
    ax.get_legend().remove()
    with mpl.rc_context(chart_rc(spec)):
        canvas.draw()
    pixels = np.asarray(canvas.buffer_rgba())[:, :, :3]
    height = pixels.shape[0]
    under = pixels[
        height - int(np.ceil(box.y1)) : height - int(np.floor(box.y0)),
        int(np.floor(box.x0)) : int(np.ceil(box.x1)),
    ]
    page = np.array([int(spec.palette.page[i : i + 2], 16) for i in (1, 3, 5)])
    assert np.all(under == page), f"{case}: data under the legend"


def test_occupancy_sees_lines_that_only_cross_a_corner() -> None:
    """A line between two points far apart still covers what it crosses."""
    x0, y0, x1, y1 = (np.array([v]) for v in (0.0, 1.0, 1.0, 0.0))
    assert _segments_cross(x0, y0, x1, y1, (0.4, 0.4, 0.6, 0.6))
    assert not _segments_cross(x0, y0, x1, y1, (0.8, 0.8, 1.0, 1.0))
    vertical = (np.array([0.5]), np.array([0.0]), np.array([0.5]), np.array([1.0]))
    assert _segments_cross(*vertical, (0.4, 0.9, 0.6, 1.0))
    assert not _segments_cross(*vertical, (0.6, 0.0, 0.9, 1.0))


def test_occupancy_counts_error_bars() -> None:
    """A tall error bar reaching into a corner keeps the legend out of it."""
    spec, data = INSIDE["scatter"]
    layout, _legend = graph_layout(spec, data, legend_names(spec, data))
    found = occupancy(spec, data, layout)
    low, high = found.segments[1], found.segments[3]
    assert np.any(high > low)  # the vertical error bars are there


# --- spec ------------------------------------------------------------------------------


def test_legend_position_is_saved_with_the_spec() -> None:
    spec, _data = spectra(SHORT, legend_position=LegendPosition.INSIDE)
    assert spec_from_json(spec_to_json(spec)).legend_position is LegendPosition.INSIDE
    data = spec_to_json(spec)
    del data["legend_position"]
    assert spec_from_json(data).legend_position is LegendPosition.OUTSIDE
    data["legend_position"] = "nowhere"
    with pytest.raises(ValueError, match="legend_position"):
        spec_from_json(data)
