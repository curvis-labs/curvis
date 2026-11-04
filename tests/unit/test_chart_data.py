"""Plot data: what graphs draw, and the box-plot statistics."""

import math
from dataclasses import replace

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from curvis.analysis.result import FittedCurve, Result
from curvis.charts.builder import default_spec
from curvis.charts.data import box_stats, compute, gather, pooled_stats
from curvis.charts.spec import AxisSpec, ChartFamily, ChartVariant, ErrorBars, Scale
from curvis.charts.tooltips import describe_category, describe_point
from curvis.data import row_stats
from tests.unit.charts_tables import (
    DOSES,
    column_table,
    error_table,
    hill_fit,
    replicate_table,
)

NAN = math.nan


def test_xy_means_and_sd_come_from_row_stats() -> None:
    table = replicate_table()
    data = compute(gather(table, default_spec(table, ChartFamily.XY), {}))
    control = data.xy[0]
    stats = row_stats([c.values for c in table.group_columns(table.replicate_groups[0].id)])
    np.testing.assert_array_equal(control.y, stats.mean)
    np.testing.assert_array_equal(control.err_high, stats.sd)
    np.testing.assert_array_equal(control.px, DOSES)
    assert control.replicates.shape == (6, 3)
    assert list(control.rows) == list(range(6))


def test_sem_and_no_error_bars() -> None:
    table = replicate_table()
    spec = default_spec(table, ChartFamily.XY, error_bars=ErrorBars.SEM)
    data = compute(gather(table, spec, {}))
    stats = row_stats([c.values for c in table.group_columns(table.replicate_groups[0].id)])
    np.testing.assert_array_equal(data.xy[0].err_low, stats.sem)
    spec = default_spec(table, ChartFamily.XY, error_bars=ErrorBars.NONE)
    assert np.all(np.isnan(compute(gather(table, spec, {})).xy[0].err_high))


def test_error_column_values_are_used_as_typed_and_made_positive() -> None:
    table = error_table()
    data = compute(gather(table, default_spec(table, ChartFamily.XY), {}))
    a, b = data.xy
    np.testing.assert_array_equal(a.err_high, [0.1, 0.2, NAN])
    assert np.all(np.isnan(b.err_high))


def test_missing_values_leave_points_out_and_keep_their_rows() -> None:
    table = replicate_table()
    first = table.columns[0]
    first.values = np.array([1.0, NAN, 3.0, 4.0, 5.0, 6.0])  # X missing in row 2
    for column in table.group_columns(table.replicate_groups[1].id):
        column.values = np.array([NAN, *column.values[1:]])  # Drug has no row 1
    data = compute(gather(table, default_spec(table, ChartFamily.XY), {}))
    assert list(data.xy[0].rows) == [0, 2, 3, 4, 5]
    assert list(data.xy[1].rows) == [2, 3, 4, 5]


def test_log_axes_drop_non_positive_points_with_a_note() -> None:
    table = replicate_table()
    table.columns[0].values = np.array([0.0, -1.0, 0.1, 1.0, 10.0, 100.0])
    spec = default_spec(table, ChartFamily.XY)
    spec = replace(spec, x_axis=AxisSpec(scale=Scale.LOG))
    data = compute(gather(table, spec, {}))
    np.testing.assert_allclose(data.xy[0].px, [-1, 0, 1, 2])
    np.testing.assert_array_equal(data.xy[0].x, [0.1, 1.0, 10.0, 100.0])
    assert "4 points at or below 0 cannot be shown on a log axis." in data.notes


def test_log_y_error_bars_are_asymmetric() -> None:
    table = error_table()
    spec = replace(default_spec(table, ChartFamily.XY), y_axis=AxisSpec(scale=Scale.LOG))
    data = compute(gather(table, spec, {}))
    a = data.xy[0]
    assert a.err_high[0] == pytest.approx(math.log10(1.1))
    assert a.err_low[0] == pytest.approx(-math.log10(0.9))


def test_fit_curves_are_overlaid() -> None:
    table = replicate_table()
    fit = hill_fit(table)
    spec = default_spec(table, ChartFamily.XY, [fit])
    curve = FittedCurve(
        "g", "Control", (0.01, 1.0, 100.0), (1.0, 2.0, 3.0), (0.5,) * 3, (4.0,) * 3, log_x=True
    )
    result = Result("m", "s", curves=(curve,))
    data = compute(gather(table, spec, {fit.id: result}))
    (drawn,) = data.curves
    np.testing.assert_allclose(drawn.px, [-2, 0, 2])  # the X axis is log
    np.testing.assert_array_equal(drawn.py, [1, 2, 3])
    assert compute(gather(table, spec, {})).curves == ()  # the fit has not run yet


def test_series_no_longer_in_the_table_are_skipped_with_a_note() -> None:
    table = replicate_table()
    spec = default_spec(table, ChartFamily.XY)
    gone = table.replicate_groups.pop(0)
    table.columns = [c for c in table.columns if c.group_id != gone.id]
    data = compute(gather(table, spec, {}))
    assert len(data.xy) == 1
    assert "1 series is no longer in the table." in data.notes


def test_bars_pool_all_values_of_a_series() -> None:
    table = column_table()
    data = compute(gather(table, default_spec(table, ChartFamily.BAR), {}))
    control, treated = data.categories
    assert control.mean == pytest.approx(3.75)
    assert control.error == pytest.approx(float(np.std([1, 2, 4, 8], ddof=1)))
    assert (treated.n, treated.mean, treated.position) == (2, 6.0, 1)
    box = replace(default_spec(table, ChartFamily.BAR), variant=ChartVariant.BOX)
    assert compute(gather(table, box, {})).categories[0].box.q3 == 7.0


# --- box statistics (R quantile type 6) -------------------------------------------------


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([1, 2, 4, 8], (4, 1, 1.25, 3, 7, 8)),
        ([5, 7], (2, 5, 5, 6, 7, 7)),
        ([4.2], (1, 4.2, 4.2, 4.2, 4.2, 4.2)),
        ([-1, 3, 8], (3, -1, -1, 3, 8, 8)),
        ([1, NAN, 3, 4, NAN, 6], (4, 1, 1.5, 3.5, 5.5, 6)),
        ([3.3] * 6, (6, 3.3, 3.3, 3.3, 3.3, 3.3)),
    ],
)
def test_box_stats_known_values(values: list[float], expected: tuple[float, ...]) -> None:
    b = box_stats(values)
    assert (b.n, b.min, b.q1, b.median, b.q3, b.max) == pytest.approx(expected)


def test_box_stats_of_nothing_is_nan() -> None:
    b = box_stats([NAN, NAN])
    assert b.n == 0 and all(math.isnan(v) for v in (b.min, b.q1, b.median, b.q3, b.max))


@given(st.lists(st.floats(-1e6, 1e6), min_size=1, max_size=60))
def test_box_stats_match_numpy_weibull(values: list[float]) -> None:
    """NumPy's "weibull" method is the same definition as R's type 6."""
    b = box_stats(values)
    q = np.quantile(values, [0.25, 0.5, 0.75], method="weibull")
    np.testing.assert_allclose([b.q1, b.median, b.q3], q, rtol=1e-12, atol=1e-9)
    assert b.median == pytest.approx(float(np.median(values)), rel=1e-12, abs=1e-9)


def test_pooled_stats_of_nothing() -> None:
    mean, sd, sem, n = pooled_stats(np.array([NAN]))
    assert n == 0 and math.isnan(mean) and math.isnan(sd) and math.isnan(sem)


# --- tooltips --------------------------------------------------------------------------


def test_point_tooltip_shows_the_table_values() -> None:
    table = replicate_table()
    spec = replace(default_spec(table, ChartFamily.XY), x_axis=AxisSpec("Dose", scale=Scale.LOG))
    data = compute(gather(table, spec, {}))
    text = describe_point(data.xy[0], 0, "Dose")
    assert text.splitlines() == [
        "Control · row 1",
        "Dose: 0.001",  # as typed, not the log10 that is plotted
        "Mean: 11.00",
        "SD: 1.000",
        "n: 3",
        "Values: 10, 11, 12",
    ]
    err = error_table()
    data = compute(gather(err, default_spec(err, ChartFamily.XY), {}))
    assert describe_point(data.xy[0], 1).splitlines() == [
        "A · row 2",
        "X: 2",
        "Y: 2",
        "Error: -0.2",
    ]


def test_category_tooltips() -> None:
    table = column_table()
    spec = default_spec(table, ChartFamily.BAR)
    control = compute(gather(table, spec, {})).categories[0]
    assert describe_category(control, ChartVariant.BAR).splitlines() == [
        "Control",
        "Mean: 3.750",
        "SD: 3.096",
        "n: 4",
    ]
    assert describe_category(control, ChartVariant.BOX).splitlines() == [
        "Control",
        "Maximum: 8",
        "Q3: 7.000",
        "Median: 3.000",
        "Q1: 1.250",
        "Minimum: 1",
        "n: 4",
    ]
