"""The export (Matplotlib) matches the Graph panel (pyqtgraph) for every graph type, with
and without a legend, in the light and the dark app theme. See tests/ui/IMAGE_DIFF.md."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from curvis.analysis.result import FittedCurve, Result
from curvis.charts.builder import default_spec, with_grouping, with_palette, with_variant
from curvis.charts.colors import ColorScheme
from curvis.charts.data import ChartData, compute, gather
from curvis.charts.fonts import FONTS
from curvis.charts.spec import (
    BinMode,
    ChartFamily,
    ChartSpec,
    ChartVariant,
    ErrorBars,
    FitOverlay,
    Grouping,
    HistogramBins,
    LabelAngle,
    LegendPosition,
    LineStyle,
    Scale,
    Symbol,
)
from curvis.data import Column, ColumnRole, Table
from curvis.ui.theme import dark_palette, light_palette
from tests.ui.image_diff import as_array, compare, save_pair
from tests.unit.charts_tables import (
    DOSES,
    SPECTRA_NAMES,
    column_table,
    replicate_table,
    sigmoid_table,
    spectra_table,
    spread_table,
    survival_table,
    two_way_analysis,
    two_way_result,
    two_way_table,
)

PLOT_THRESHOLD = 0.01
"""Largest miss allowed in the plot area (measured: 0.0000 for every graph)."""
PAGE_THRESHOLD = 0.12
"""Largest miss allowed on the page (measured: at most 0.037). See IMAGE_DIFF.md."""
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


def _spread(variant: ChartVariant, **changes: object) -> tuple[ChartSpec, ChartData]:
    """Three columns of 18 to 30 values: horizontal bars, violins, columns of values,
    histograms and individual values."""
    table = spread_table()
    spec = with_variant(default_spec(table, ChartFamily.BAR), variant, table)
    spec = replace(spec, aspect_ratio="square", **changes)  # type: ignore[arg-type]
    return spec, compute(gather(table, spec, {}))


def _grouped(
    variant: ChartVariant, grouping: Grouping = Grouping.BY_SERIES
) -> tuple[ChartSpec, ChartData]:
    """The two-way table (Control, Low, High in Male and Female) grouped."""
    table = two_way_table()
    spec = with_grouping(default_spec(table, ChartFamily.BAR), grouping, table)
    spec = replace(with_variant(spec, variant, table), aspect_ratio="square")
    return spec, compute(gather(table, spec, {}))


def _interaction() -> tuple[ChartSpec, ChartData]:
    table = two_way_table()
    analysis = two_way_analysis(table)
    result = two_way_result(table, analysis)
    spec = replace(default_spec(table, ChartFamily.INTERACTION, [analysis]), aspect_ratio="square")
    return spec, compute(gather(table, spec, {analysis.id: result}))


def _wide_interaction() -> tuple[ChartSpec, ChartData]:
    """The interaction graph (turned labels) on a wide page with a legend; GRAPHS has
    it on a square page."""
    spec, data = _interaction()
    return replace(spec, aspect_ratio="wide", show_legend=True), data


def _long_title(title: str) -> tuple[ChartSpec, ChartData]:
    """A title wider than the plot of a square page with a legend: broken into lines
    as wide as the plot at most, in both renderers."""
    spec, data = _scatter()
    return replace(spec, title=title, aspect_ratio="square", show_legend=True), data


def _turned_bars(aspect_ratio: str, legend: bool) -> tuple[ChartSpec, ChartData]:
    """Category labels turned by 45°."""
    table = Table(
        "Dose groups",
        [
            Column.new(name, ColumnRole.Y, values)
            for name, values in (
                ("Control", [1.0, 2.0, 4.0]),
                ("Low", [3.0, 5.0, 6.0]),
                ("High", [7.0, 8.0, 9.5]),
            )
        ],
    )
    spec = default_spec(table, ChartFamily.BAR)
    spec = replace(
        spec,
        aspect_ratio=aspect_ratio,
        show_legend=legend,
        x_axis=replace(spec.x_axis, label_angle=LabelAngle.ANGLED),
        y_axis=replace(spec.y_axis, title="Signal"),
    )
    return spec, compute(gather(table, spec, {}))


LAYOUTS = {
    "title-dose-by-sex-and-time": lambda: _long_title("Dose by sex and time"),
    "title-six-lines": lambda: _long_title(
        "Response of HeLa cells to increasing doses of cisplatin"
    ),
    "turned-labels-square-legend": lambda: _turned_bars("square", True),
    "turned-labels-square-no-legend": lambda: _turned_bars("square", False),
    "turned-labels-wide-legend": lambda: _turned_bars("wide", True),
    "turned-labels-wide-no-legend": lambda: _turned_bars("wide", False),
    "interaction-wide-legend": lambda: _wide_interaction(),
}
"""Layouts that once differed between the renderers: a title wider than the plot, and
turned category labels (both renderers now lay out a title and turned labels as
Matplotlib does)."""


def _spectra(names: tuple[str, ...], **changes: object) -> tuple[ChartSpec, ChartData]:
    """Absorbance spectra drawn as lines, one series per sample (the bug report of a
    legend that squashed the plot)."""
    table = spectra_table(names)
    spec = replace(default_spec(table, ChartFamily.XY), variant=ChartVariant.LINE, title="Data 1")
    spec = replace(spec, series=tuple(replace(s, symbol=Symbol.NONE) for s in spec.series))
    spec = replace(spec, **changes)  # type: ignore[arg-type]
    return spec, compute(gather(table, spec, {}))


def _inside(make: Callable[[], tuple[ChartSpec, ChartData]]) -> tuple[ChartSpec, ChartData]:
    spec, data = make()
    return replace(spec, show_legend=True, legend_position=LegendPosition.INSIDE), data


SHORT_SPECTRA = ("BL21 80", "BL21 250", "BL21 800", "C41 80", "C41 800", "C43 80", "C43 800")

LEGEND_PLACES = {
    "legend-long-names-below": lambda: _spectra(SPECTRA_NAMES),
    "legend-sixty-series-below": lambda: _spectra(tuple(f"Sample {i}" for i in range(1, 61))),
    "legend-inside-spectra": lambda: _spectra(SHORT_SPECTRA, legend_position=LegendPosition.INSIDE),
    "legend-inside-scatter": lambda: _inside(_scatter),
    "legend-inside-line-with-fit": lambda: _inside(_line_with_fit),
    "legend-inside-survival": lambda: _inside(_survival),
    "legend-inside-histogram": lambda: _inside(lambda: _spread(ChartVariant.HISTOGRAM)),
}
"""Legends since the bug report: under the plot in columns, with smaller text, shortened
names and a count of the rest; and in an empty corner of the plot (or outside when every
corner has data: the line graph with a fit)."""


def _survival() -> tuple[ChartSpec, ChartData]:
    table = survival_table()
    spec = replace(default_spec(table, ChartFamily.SURVIVAL), aspect_ratio="square")
    return spec, compute(gather(table, spec, {}))


GRAPHS = {
    "scatter": _scatter,
    "line": _line_with_fit,
    "bar": lambda: _bars(ChartVariant.BAR),
    "box": lambda: _bars(ChartVariant.BOX),
    "interaction": _interaction,
    "survival": _survival,
    # Step 3.5: the other bar-family variants, grouped and not.
    "horizontal-bar": lambda: _spread(ChartVariant.HORIZONTAL_BAR),
    "bar-values": lambda: _spread(ChartVariant.BAR, show_points=True),
    "box-values": lambda: _spread(ChartVariant.BOX, show_points=True),
    "violin": lambda: _spread(ChartVariant.VIOLIN),
    "violin-untrimmed-values": lambda: _spread(
        ChartVariant.VIOLIN, violin_trim=False, show_points=True
    ),
    "column-scatter": lambda: _spread(ChartVariant.COLUMN_SCATTER),
    "histogram": lambda: _spread(ChartVariant.HISTOGRAM),
    "histogram-width-bins": lambda: _spread(
        ChartVariant.HISTOGRAM, bins=HistogramBins(BinMode.WIDTH, width=2.5, start=0.0)
    ),
    "grouped-bar": lambda: _grouped(ChartVariant.BAR),
    "grouped-bar-by-category": lambda: _grouped(ChartVariant.BAR, Grouping.BY_CATEGORY),
    "grouped-horizontal-bar": lambda: _grouped(ChartVariant.HORIZONTAL_BAR),
    "grouped-box": lambda: _grouped(ChartVariant.BOX, Grouping.BY_CATEGORY),
    "grouped-violin": lambda: _grouped(ChartVariant.VIOLIN),
    "palette": lambda: _palette(),
}


def _palette() -> tuple[ChartSpec, ChartData]:
    """A palette made in the palette editor, with its own page and ink."""
    mine = ColorScheme("custom", "Mine", "#fcfbf7", "#202a44", ("#8e3b46", "#4c8577", "#d1a03f"))
    spec, data = _grouped(ChartVariant.BAR, Grouping.BY_CATEGORY)
    spec = with_palette(spec, mine)
    return spec, data


def _font(font: str, graph: str) -> tuple[ChartSpec, ChartData]:
    """A graph in ``font``, with a title long enough to wrap: where it breaks depends on
    the font's letter widths in both renderers."""
    spec, data = {
        "line": _line_with_fit,
        "grouped-bar": lambda: _grouped(ChartVariant.BAR),
        "histogram": lambda: _spread(ChartVariant.HISTOGRAM),
    }[graph]()
    title = "Response of cells to dose after 24 hours"
    return replace(spec, font=font, title=title), data


FONT_CASES = [(f, g) for f in FONTS for g in ("line", "grouped-bar", "histogram")]


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
    _check(spec, data, f"{graph}-{'legend' if legend else 'no-legend'}-{theme}")


@pytest.mark.parametrize(("font", "graph"), FONT_CASES, ids=[f"{f}-{g}" for f, g in FONT_CASES])
def test_every_chart_font_matches(font: str, graph: str, qapp: QApplication) -> None:
    """Each bundled font is drawn alike by both renderers (step 3.5)."""
    spec, data = _font(font, graph)
    _check(spec, data, f"font-{font}-{graph}")


@pytest.mark.parametrize("layout", LAYOUTS)
def test_title_and_turned_labels_match(layout: str, qapp: QApplication) -> None:
    spec, data = LAYOUTS[layout]()
    _check(spec, data, layout)


@pytest.mark.parametrize("layout", LEGEND_PLACES)
def test_legend_places_match(layout: str, qapp: QApplication) -> None:
    spec, data = LEGEND_PLACES[layout]()
    # An inside legend is left out of the plot measure (the page measure compares it):
    # its text would be the only ink in the plot. That no data is under it is checked
    # in tests/unit/test_chart_legend.py.
    _check(spec, data, layout, mask_legend=spec.legend_position is LegendPosition.INSIDE)


def _check(spec: ChartSpec, data: ChartData, name: str, *, mask_legend: bool = False) -> None:
    diff = compare(spec, data, mask_legend=mask_legend)
    report = f"{name}: plot {diff.plot:.4f}, page {diff.page:.4f}"
    print(report)
    if diff.plot > PLOT_THRESHOLD or diff.page > PAGE_THRESHOLD:
        path = save_pair(diff, FAILED, name)
        pytest.fail(
            f"{report} (thresholds {PLOT_THRESHOLD}, {PAGE_THRESHOLD}); see {path}\n" + diff.details
        )


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


@pytest.mark.parametrize("font", ["liberation_serif", "liberation_sans"])
def test_the_measure_sees_a_wrong_font(font: str, qapp: QApplication) -> None:
    """Text drawn in another font on screen than on export fails the page measure."""
    from PIL import Image

    from curvis.charts.render_mpl.export import render_image
    from curvis.charts.render_qt.chart_view import render_page
    from tests.ui.image_diff import PAGE_RADIUS, SCALE, miss, qimage_to_pil

    spec, data = _font("dejavu_sans", "grouped-bar")
    screen = qimage_to_pil(render_page(spec, data, SCALE)[0])
    array, _box = render_image(replace(spec, font=font), data, 96 * SCALE)
    export = Image.fromarray(array, "RGBA").convert("RGB")
    ink = spec.palette.ink
    rgb = (int(ink[1:3], 16), int(ink[3:5], 16), int(ink[5:7], 16))
    assert miss(screen, export, [rgb], PAGE_RADIUS) > PAGE_THRESHOLD


@pytest.mark.parametrize("font", sorted(FONTS))
def test_both_renderers_use_the_graphs_font(font: str, qapp: QApplication) -> None:
    """Inter is close enough to DejaVu Sans that the page measure alone would not see a
    swap: check what each renderer resolves the font to."""
    import matplotlib
    from matplotlib import font_manager
    from PySide6.QtGui import QFontInfo

    from curvis.charts.render_mpl.figure import chart_rc
    from curvis.charts.render_qt.chart_view import chart_font, chart_font_family

    info = FONTS[font]
    family = chart_font_family(font)
    assert family == info.family
    assert QFontInfo(chart_font(family)).family() == info.family
    spec, _data = _font(font, "line")
    with matplotlib.rc_context(chart_rc(spec)):
        found = font_manager.findfont(font_manager.FontProperties(), fallback_to_default=False)
    assert found == str(info.path())
