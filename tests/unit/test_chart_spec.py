"""Chart specs: validation, helpers and the JSON form stored in project files."""

import json
from dataclasses import replace

import pytest

from curvis.charts.spec import (
    AxisSpec,
    ChartFamily,
    ChartSpec,
    ChartVariant,
    ErrorBars,
    FitOverlay,
    LineStyle,
    Scale,
    SeriesSpec,
    Symbol,
    spec_from_json,
    spec_to_json,
)


def full_spec() -> ChartSpec:
    return ChartSpec(
        table_id="t1",
        family=ChartFamily.XY,
        variant=ChartVariant.LINE,
        series=(
            SeriesSpec("g1", "#1d3557", Symbol.SQUARE, 9.0, LineStyle.DASH, 2.0, ErrorBars.SEM),
            SeriesSpec(
                "c2", "#00a6a6", error_bars=ErrorBars.ERROR_COLUMN, error_column="e3", visible=False
            ),
        ),
        x_axis=AxisSpec("Dose (µM)", 0.001, 100.0, True, Scale.LOG),
        y_axis=AxisSpec("Response", None, 120.0, False),
        title="Assay",
        aspect_ratio="custom",
        custom_size=(12.0, 8.5),
        text_size=12.0,
        overlays=(FitOverlay("a1", show_band=True),),
    )


def test_json_round_trip_keeps_everything() -> None:
    spec = full_spec()
    data = json.loads(json.dumps(spec_to_json(spec)))
    assert spec_from_json(data) == spec


def test_json_fills_in_missing_style_keys() -> None:
    data = {
        "id": "g",
        "table_id": "t",
        "family": "bar",
        "variant": "box",
        "series": [{"source": "s", "color": "#123456"}],
    }
    spec = spec_from_json(data)
    assert spec.series[0] == SeriesSpec("s", "#123456")
    assert spec.aspect_ratio == "4:3" and spec.x_axis == AxisSpec()


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("family", "pie", "family cannot be 'pie'"),
        ("variant", "box", "does not belong"),
        ("text_size", "big", "text_size must be a number"),
        ("text_size", 1.0, "text size must be from"),
        ("aspect_ratio", "5:4", "unknown aspect ratio"),
        ("series", [{"source": "s", "color": "red"}], "not #rrggbb"),
        ("x_axis", {"minimum": 5, "maximum": 1}, "below its maximum"),
        ("x_axis", {"minimum": 0, "maximum": 1, "scale": "log"}, "above 0"),
        ("overlays", [{"analysis_id": "a"}, {"analysis_id": "a"}], "overlaid twice"),
    ],
)
def test_json_rejects_bad_values(key: str, value: object, message: str) -> None:
    data = spec_to_json(full_spec())
    data[key] = value
    if key == "aspect_ratio":
        data["custom_size"] = None
    with pytest.raises(ValueError, match=message):
        spec_from_json(data)


def test_spec_rejects_duplicate_series_and_missing_error_column() -> None:
    s = SeriesSpec("a", "#000000")
    with pytest.raises(ValueError, match="twice"):
        ChartSpec("t", ChartFamily.BAR, ChartVariant.BAR, series=(s, s))
    with pytest.raises(ValueError, match="need the column"):
        ChartSpec(
            "t",
            ChartFamily.XY,
            ChartVariant.SCATTER,
            series=(replace(s, error_bars=ErrorBars.ERROR_COLUMN),),
        )


def test_helpers_return_changed_copies() -> None:
    spec = full_spec()
    hidden = spec.with_series_visible("g1", False)
    assert hidden.series_for("g1").visible is False  # type: ignore[union-attr]
    assert spec.series_for("g1").visible is True  # type: ignore[union-attr]
    with pytest.raises(KeyError):
        spec.with_series_visible("nope", False)
    assert spec.with_overlay(FitOverlay("a1")) is spec
    assert spec.with_overlay(FitOverlay("a2")).overlays[-1] == FitOverlay("a2")


def test_aspect() -> None:
    assert full_spec().aspect == pytest.approx(12 / 8.5)
    assert replace(full_spec(), aspect_ratio="16:9", custom_size=None).aspect == 16 / 9
