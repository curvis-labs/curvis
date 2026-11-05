"""The page and axes shared by the screen and export renderers."""

import math
from dataclasses import replace

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from curvis.charts.axes import (
    PADDING,
    axes_layout,
    chart_font_path,
    linear_ticks,
    log_ticks,
    nice_step,
    page_px,
)
from curvis.charts.builder import default_spec
from curvis.charts.data import compute, gather
from curvis.charts.spec import AxisSpec, ChartFamily, ChartVariant, Scale
from tests.unit.charts_tables import DOSES, column_table, replicate_table


def xy(scale: Scale = Scale.LINEAR, **axis: float):  # type: ignore[no-untyped-def]
    table = replicate_table()
    spec = default_spec(table, ChartFamily.XY)
    spec = replace(spec, x_axis=replace(spec.x_axis, scale=scale, **axis))
    return spec, compute(gather(table, spec, {}))


def test_page_px_follows_the_page_size() -> None:
    spec = default_spec(replicate_table(), ChartFamily.XY)  # Wide, 9 x 6 cm
    assert page_px(spec) == (340, 227)
    assert page_px(spec, 300) == (1063, 709)
    assert page_px(replace(spec, aspect_ratio="tall"), 300) == (472, 709)


def test_xy_ranges_are_the_data_padded_by_five_percent() -> None:
    spec, data = xy()
    layout = axes_layout(spec, data)
    low, high = min(DOSES), max(DOSES)
    assert layout.x.low == pytest.approx(low - PADDING * (high - low))
    assert layout.x.high == pytest.approx(high + PADDING * (high - low))
    tops = [s.py + s.err_high for s in data.xy]
    assert layout.y.high > max(float(t.max()) for t in tops)


def test_typed_limits_replace_the_automatic_ends() -> None:
    spec, data = xy(minimum=-5.0)
    layout = axes_layout(spec, data)
    assert layout.x.low == -5.0
    spec, data = xy(Scale.LOG, minimum=0.01, maximum=1000.0)
    layout = axes_layout(spec, data)
    assert (layout.x.low, layout.x.high) == pytest.approx((-2.0, 3.0))
    assert [t.label for t in layout.x.major] == ["0.01", "0.1", "1", "10", "100", "1000"]


def test_hidden_series_do_not_count() -> None:
    spec, data = xy()
    hidden = spec.with_series_visible(spec.series[1].source, False)
    assert axes_layout(hidden, data).y.high < axes_layout(spec, data).y.high


def test_bars_start_at_zero_and_hidden_bars_leave_no_gap() -> None:
    table = column_table()
    spec = default_spec(table, ChartFamily.BAR)
    data = compute(gather(table, spec, {}))
    layout = axes_layout(spec, data)
    assert layout.y.low == 0.0 and layout.y.high > 0
    assert (layout.x.low, layout.x.high) == (-0.6, 1.6)
    assert [t.label for t in layout.x.major] == ["Control", "Treated"]
    hidden = spec.with_series_visible(spec.series[0].source, False)
    layout = axes_layout(hidden, data)
    assert layout.positions == {spec.series[1].source: 0}
    assert (layout.x.low, layout.x.high) == (-0.6, 0.6)


def test_box_range_is_the_whiskers() -> None:
    table = column_table()
    spec = replace(default_spec(table, ChartFamily.BAR), variant=ChartVariant.BOX)
    data = compute(gather(table, spec, {}))
    layout = axes_layout(spec, data)
    assert layout.y.low == pytest.approx(1 - PADDING * 7)
    assert layout.y.high == pytest.approx(8 + PADDING * 7)


def test_empty_graph_has_a_unit_range() -> None:
    spec = default_spec(replicate_table(), ChartFamily.XY)
    layout = axes_layout(spec, None)
    assert (layout.x.low, layout.x.high) == (0.0, 1.0)


@pytest.mark.parametrize(
    ("span", "step"), [(10, 2), (1, 0.2), (100, 20), (7, 1), (3, 0.5), (0.04, 0.01)]
)
def test_nice_steps(span: float, step: float) -> None:
    assert nice_step(span) == pytest.approx(step)


def test_linear_tick_labels() -> None:
    labels = [t.label for t in linear_ticks(-0.3, 1.05)]
    assert labels == ["-0.2", "0.0", "0.2", "0.4", "0.6", "0.8", "1.0"]
    assert [t.label for t in linear_ticks(0, 100)] == ["0", "20", "40", "60", "80", "100"]
    assert [t.label for t in linear_ticks(0, 1e9)][1] == "2e+08"


def test_log_ticks() -> None:
    major, minor = log_ticks(-0.5, 2.2)
    assert [t.label for t in major] == ["1", "10", "100"]
    assert math.log10(2) in minor and math.log10(0.4) in minor
    major, minor = log_ticks(0.1, 0.6)  # less than a decade: linear values
    assert minor == () and [t.label for t in major] == ["1.5", "2", "2.5", "3", "3.5"]
    major, _ = log_ticks(-10, 10)
    assert len(major) <= 11


@given(st.floats(-1e6, 1e6), st.floats(1e-6, 1e6))
def test_linear_ticks_lie_in_the_range(low: float, span: float) -> None:
    assume(span > abs(low) * 1e-9)  # a span below float precision has no ticks to show
    ticks = linear_ticks(low, low + span)
    assert 2 <= len(ticks) <= 12
    assert all(low - 1e-9 * span <= t.value <= low + span * (1 + 1e-9) for t in ticks)


def test_chart_font_ships_with_matplotlib() -> None:
    path = chart_font_path()
    assert path is not None and path.name == "DejaVuSans.ttf"


def test_unused_axis_spec_fields_do_not_matter() -> None:
    spec, data = xy()
    titled = replace(spec, x_axis=replace(spec.x_axis, title="Other"), y_axis=AxisSpec("Y"))
    assert axes_layout(titled, data).x == axes_layout(spec, data).x
