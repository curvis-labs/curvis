"""The export (Matplotlib) matches the Graph panel (pyqtgraph) for every graph type, with
and without a legend, in the light and the dark app theme. See tests/ui/IMAGE_DIFF.md."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from curvis.analysis.result import FittedCurve, Result
from curvis.charts.builder import default_spec
from curvis.charts.data import ChartData, compute, gather
from curvis.charts.spec import (
    ChartFamily,
    ChartSpec,
    ChartVariant,
    ErrorBars,
    FitOverlay,
    LineStyle,
    Scale,
    Symbol,
)
from curvis.data import Table
from curvis.ui.theme import dark_palette, light_palette
from tests.ui.image_diff import as_array, compare, save_pair
from tests.unit.charts_tables import DOSES, column_table, replicate_table, sigmoid_table

PLOT_THRESHOLD = 0.01
"""Largest miss allowed in the plot area (measured: 0.0000 for every graph)."""
PAGE_THRESHOLD = 0.12
"""Largest miss allowed on the page (measured: at most 0.092). See IMAGE_DIFF.md."""
FAILED = Path(__file__).parents[2] / "build" / "image-diff"


def _scatter() -> tuple[ChartSpec, ChartData]:
    table = replicate_table()
    spec = default_spec(table, ChartFamily.XY)
    spec = replace(spec, y_axis=replace(spec.y_axis, title="Response (%)"))
    return spec, compute(gather(table, spec, {}))


def _line_with_fit() -> tuple[ChartSpec, ChartData]:
    table = sigmoid_table()
    spec = default_spec(table, ChartFamily.XY)
    group = table.replicate_groups[1]
    grid = np.logspace(-3, 2, 120)
    fit = 30 + 80 / (1 + 0.5 / grid)
    curve = FittedCurve(
        group.id, group.name, tuple(grid), tuple(fit), tuple(fit - 4), tuple(fit + 4), log_x=True
    )
    styled = tuple(
        replace(s, line_style=LineStyle.DASH, symbol=Symbol.TRIANGLE, size=8.0, line_width=1.0)
        if i == 0
        else replace(s, symbol=Symbol.DIAMOND, error_bars=ErrorBars.SEM)
        for i, s in enumerate(spec.series)
    )
    spec = replace(
        spec,
        variant=ChartVariant.LINE,
        series=styled,
        x_axis=replace(spec.x_axis, scale=Scale.LOG),
        overlays=(FitOverlay("fit", show_band=True),),
        aspect_ratio="square",
        text_size=9.0,
    )
    result = Result("fit", "fit", curves=(curve,))
    return spec, compute(gather(table, spec, {"fit": result}))


def _bars(variant: ChartVariant) -> tuple[ChartSpec, ChartData]:
    table: Table = column_table()
    spec = replace(default_spec(table, ChartFamily.BAR), variant=variant, aspect_ratio="square")
    spec = replace(spec, y_axis=replace(spec.y_axis, title="Signal"))
    return spec, compute(gather(table, spec, {}))


GRAPHS = {
    "scatter": _scatter,
    "line": _line_with_fit,
    "bar": lambda: _bars(ChartVariant.BAR),
    "box": lambda: _bars(ChartVariant.BOX),
}


@pytest.fixture(params=["light", "dark"])
def theme(request: pytest.FixtureRequest, qapp: QApplication) -> Iterator[str]:
    original = qapp.palette()
    qapp.setPalette(light_palette() if request.param == "light" else dark_palette())
    yield request.param
    qapp.setPalette(original)


@pytest.mark.parametrize("legend", [True, False], ids=["legend", "no-legend"])
@pytest.mark.parametrize("graph", GRAPHS)
def test_export_matches_the_screen(graph: str, legend: bool, theme: str) -> None:
    spec, data = GRAPHS[graph]()
    spec = replace(spec, show_legend=legend)
    diff = compare(spec, data)
    name = f"{graph}-{'legend' if legend else 'no-legend'}-{theme}"
    report = f"{name}: plot {diff.plot:.4f}, page {diff.page:.4f}"
    print(report)
    if diff.plot > PLOT_THRESHOLD or diff.page > PAGE_THRESHOLD:
        path = save_pair(diff, FAILED, name)
        pytest.fail(f"{report} (thresholds {PLOT_THRESHOLD}, {PAGE_THRESHOLD}); see {path}")


@pytest.mark.parametrize("graph", GRAPHS)
def test_the_app_theme_does_not_change_a_graph(graph: str, qapp: QApplication) -> None:
    """Chart colours come from the chart's scheme, never from the theme (DESIGN.md §3)."""
    spec, data = GRAPHS[graph]()
    original = qapp.palette()
    try:
        qapp.setPalette(light_palette())
        light = compare(spec, data)
        qapp.setPalette(dark_palette())
        dark = compare(spec, data)
    finally:
        qapp.setPalette(original)
    assert np.array_equal(as_array(light.screen), as_array(dark.screen))
    assert np.array_equal(as_array(light.export), as_array(dark.export))


def test_the_measure_sees_a_wrong_colour_and_a_wrong_range(qapp: QApplication) -> None:
    """The thresholds are tight enough to catch real differences."""
    spec, data = _scatter()
    recoloured = replace(spec, series=tuple(replace(s, color="#d55e00") for s in spec.series))
    from tests.ui.image_diff import PLOT_RADIUS, crop, draw_both, key_colors, miss

    screen, screen_box, _export, _box = draw_both(spec, data)
    plot = crop(screen, screen_box)
    for changed in (
        recoloured,
        replace(spec, y_axis=replace(spec.y_axis, minimum=0.0, maximum=150.0)),
        spec.with_series_visible(spec.series[0].source, False),
    ):
        other, other_box, _e, _b = draw_both(changed, data)
        resized = crop(other, other_box)
        colors = key_colors(spec) + key_colors(changed)
        assert miss(plot, resized, colors, PLOT_RADIUS) > 5 * PLOT_THRESHOLD
    assert len(DOSES) == len(data.xy[0].px)
