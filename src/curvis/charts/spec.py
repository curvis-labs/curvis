"""The chart spec: what a graph shows and how it looks, as plain data.

A :class:`ChartSpec` is what the document stores for a graph (rule 2 of CLAUDE.md): its
source table, chart family and variant, the series it draws with their style, both
axes, title, aspect ratio, colour scheme, text size and the fitted curves laid over it.
It holds no pixels and no computed values. The on-screen renderer
(:mod:`curvis.charts.render_qt`) and the export renderer (step 2.8) both draw from it,
and the Format dialog edits only the spec.

Specs are frozen; a change makes a new spec (``dataclasses.replace``), so a command can
undo it by putting the old one back. :func:`spec_to_json` and :func:`spec_from_json`
store a spec in a project file.

No Qt here: the document model imports this module.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from curvis.data.ids import new_id


class ChartFamily(StrEnum):
    """The chart families of the Plot menu, in its order."""

    XY = "xy"
    BAR = "bar"
    SURVIVAL = "survival"
    INTERACTION = "interaction"

    @property
    def label(self) -> str:
        return _FAMILY_LABELS[self]


_FAMILY_LABELS = {
    ChartFamily.XY: "XY",
    ChartFamily.BAR: "Bar",
    ChartFamily.SURVIVAL: "Survival",
    ChartFamily.INTERACTION: "Interaction",
}


class ChartVariant(StrEnum):
    """The variants of each family (SPEC.md, Graphs)."""

    SCATTER = "scatter"
    LINE = "line"
    BAR = "bar"
    HORIZONTAL_BAR = "horizontal_bar"
    BOX = "box"
    VIOLIN = "violin"
    HISTOGRAM = "histogram"
    KAPLAN_MEIER = "kaplan_meier"
    INTERACTION = "interaction"

    @property
    def family(self) -> ChartFamily:
        return _VARIANT_FAMILY[self]

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").capitalize()


_VARIANT_FAMILY = {
    ChartVariant.SCATTER: ChartFamily.XY,
    ChartVariant.LINE: ChartFamily.XY,
    ChartVariant.BAR: ChartFamily.BAR,
    ChartVariant.HORIZONTAL_BAR: ChartFamily.BAR,
    ChartVariant.BOX: ChartFamily.BAR,
    ChartVariant.VIOLIN: ChartFamily.BAR,
    ChartVariant.HISTOGRAM: ChartFamily.BAR,
    ChartVariant.KAPLAN_MEIER: ChartFamily.SURVIVAL,
    ChartVariant.INTERACTION: ChartFamily.INTERACTION,
}


class ErrorBars(StrEnum):
    """What a series' error bars show."""

    NONE = "none"
    SD = "sd"
    SEM = "sem"
    ERROR_COLUMN = "error_column"
    """Pre-computed values from the series' Error column."""

    @property
    def label(self) -> str:
        return _ERROR_LABELS[self]


_ERROR_LABELS = {
    ErrorBars.NONE: "None",
    ErrorBars.SD: "SD",
    ErrorBars.SEM: "SEM",
    ErrorBars.ERROR_COLUMN: "Error",
}


class Symbol(StrEnum):
    CIRCLE = "circle"
    SQUARE = "square"
    TRIANGLE = "triangle"
    DIAMOND = "diamond"
    NONE = "none"


SYMBOL_CYCLE = (Symbol.CIRCLE, Symbol.SQUARE, Symbol.TRIANGLE, Symbol.DIAMOND)
"""Symbols given to new series, in turn."""


class LineStyle(StrEnum):
    SOLID = "solid"
    DASH = "dash"
    DOT = "dot"
    NONE = "none"


class Scale(StrEnum):
    LINEAR = "linear"
    LOG = "log"


ASPECT_PRESETS: dict[str, tuple[float, float]] = {
    "tall": (4.0, 6.0),
    "wide": (9.0, 6.0),
    "square": (6.0, 6.0),
}
"""Page size presets: (width, height) in cm. "custom" uses :attr:`ChartSpec.custom_size`."""
CUSTOM_ASPECT = "custom"
ASPECT_LABELS = {"tall": "Tall", "wide": "Wide", "square": "Square", CUSTOM_ASPECT: "Custom"}
DEFAULT_ASPECT = "wide"
OLD_ASPECTS = {"4:3": "wide", "16:9": "wide", "3:2": "wide", "3:4": "tall", "1:1": "square"}
"""The ratios of project format 5 and the preset each became (format 6)."""
DEFAULT_TEXT_SIZE = 10.0
"""Chart text size in points."""
MIN_TEXT_SIZE, MAX_TEXT_SIZE = 4.0, 72.0

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class SeriesSpec:
    """One series of a graph and its style.

    ``source`` is the replicate group id, or the id of a Y column without a group (the
    same ids as an analysis' inputs). ``error_column`` is the Error column of an
    ungrouped Y column, used when ``error_bars`` is :attr:`ErrorBars.ERROR_COLUMN`.
    """

    source: str
    color: str
    symbol: Symbol = Symbol.CIRCLE
    size: float = 7.0
    """Symbol size in points."""
    line_style: LineStyle = LineStyle.SOLID
    line_width: float = 1.5
    """Line width in points."""
    error_bars: ErrorBars = ErrorBars.SD
    error_column: str | None = None
    visible: bool = True
    """False when the series is toggled off (legend click)."""
    in_legend: bool = True
    """Listed in the legend (when the graph shows one)."""


@dataclass(frozen=True)
class AxisSpec:
    """One axis. ``minimum`` / ``maximum`` None means automatic."""

    title: str = ""
    minimum: float | None = None
    maximum: float | None = None
    show_labels: bool = True
    """Show the tick labels (numbers or category names)."""
    scale: Scale = Scale.LINEAR


@dataclass(frozen=True)
class FitOverlay:
    """The fitted curves of a curve-fit analysis, drawn over an XY graph."""

    analysis_id: str
    show_band: bool = False
    """Also draw the confidence band."""


@dataclass(frozen=True)
class ChartSpec:
    """A graph as the document stores it."""

    table_id: str
    family: ChartFamily
    variant: ChartVariant
    series: tuple[SeriesSpec, ...] = ()
    x_axis: AxisSpec = AxisSpec()
    y_axis: AxisSpec = AxisSpec()
    title: str = ""
    aspect_ratio: str = DEFAULT_ASPECT
    custom_size: tuple[float, float] | None = None
    """(width, height) in cm, for ``aspect_ratio == "custom"``."""
    color_scheme: str = "curvis"
    text_size: float = DEFAULT_TEXT_SIZE
    overlays: tuple[FitOverlay, ...] = ()
    show_legend: bool = True
    id: str = field(default_factory=new_id)

    def __post_init__(self) -> None:
        problem = spec_problem(self)
        if problem is not None:
            raise ValueError(problem)

    @property
    def page_size_cm(self) -> tuple[float, float]:
        """(width, height) of the graph's page in cm: what an export measures."""
        if self.aspect_ratio == CUSTOM_ASPECT and self.custom_size is not None:
            return self.custom_size
        return ASPECT_PRESETS[self.aspect_ratio]

    @property
    def aspect(self) -> float:
        """Width divided by height."""
        width, height = self.page_size_cm
        return width / height

    def series_for(self, source: str) -> SeriesSpec | None:
        return next((s for s in self.series if s.source == source), None)

    def with_series_visible(self, source: str, visible: bool) -> ChartSpec:
        """A copy with the series ``source`` shown or hidden; KeyError if it has none."""
        if self.series_for(source) is None:
            raise KeyError(source)
        series = tuple(
            replace(s, visible=visible) if s.source == source else s for s in self.series
        )
        return replace(self, series=series)

    def with_overlay(self, overlay: FitOverlay) -> ChartSpec:
        """A copy that also draws ``overlay`` (unchanged if it does already)."""
        if any(o.analysis_id == overlay.analysis_id for o in self.overlays):
            return self
        return replace(self, overlays=(*self.overlays, overlay))


def spec_problem(spec: ChartSpec) -> str | None:
    """Why ``spec`` is not a valid chart spec, or None."""
    if spec.variant.family is not spec.family:
        return f"variant {spec.variant} does not belong to family {spec.family}"
    sources = [s.source for s in spec.series]
    if len(set(sources)) != len(sources):
        return "a series is listed twice"
    for s in spec.series:
        if not _HEX_COLOR.match(s.color):
            return f"series colour {s.color!r} is not #rrggbb"
        if not (_positive(s.size) and _positive(s.line_width, zero=True)):
            return "symbol size and line width must be positive numbers"
        if s.error_bars is ErrorBars.ERROR_COLUMN and s.error_column is None:
            return "error bars from an Error column need the column"
    for axis in (spec.x_axis, spec.y_axis):
        for limit in (axis.minimum, axis.maximum):
            if limit is not None and not math.isfinite(limit):
                return "axis limits must be finite"
        if axis.minimum is not None and axis.maximum is not None:
            if axis.minimum >= axis.maximum:
                return "an axis minimum must be below its maximum"
            if axis.scale is Scale.LOG and axis.minimum <= 0:
                return "a log axis must start above 0"
    if spec.aspect_ratio == CUSTOM_ASPECT:
        size = spec.custom_size
        if size is None or not (_positive(size[0]) and _positive(size[1])):
            return "a custom size needs a positive width and height"
    elif spec.aspect_ratio not in ASPECT_PRESETS:
        return f"unknown aspect ratio {spec.aspect_ratio!r}"
    if not (MIN_TEXT_SIZE <= spec.text_size <= MAX_TEXT_SIZE):
        return f"text size must be from {MIN_TEXT_SIZE:g} to {MAX_TEXT_SIZE:g} points"
    overlays = [o.analysis_id for o in spec.overlays]
    if len(set(overlays)) != len(overlays):
        return "a fit is overlaid twice"
    return None


def _positive(value: float, zero: bool = False) -> bool:
    return math.isfinite(value) and (value >= 0 if zero else value > 0)


# --- JSON --------------------------------------------------------------------------------


def spec_to_json(spec: ChartSpec) -> dict[str, Any]:
    """The spec as a JSON object (for ``project.json``)."""
    return {
        "id": spec.id,
        "table_id": spec.table_id,
        "family": spec.family.value,
        "variant": spec.variant.value,
        "series": [_series_to_json(s) for s in spec.series],
        "x_axis": _axis_to_json(spec.x_axis),
        "y_axis": _axis_to_json(spec.y_axis),
        "title": spec.title,
        "aspect_ratio": spec.aspect_ratio,
        "custom_size": None if spec.custom_size is None else list(spec.custom_size),
        "color_scheme": spec.color_scheme,
        "text_size": spec.text_size,
        "overlays": [
            {"analysis_id": o.analysis_id, "show_band": o.show_band} for o in spec.overlays
        ],
        "show_legend": spec.show_legend,
    }


def _series_to_json(s: SeriesSpec) -> dict[str, Any]:
    return {
        "source": s.source,
        "color": s.color,
        "symbol": s.symbol.value,
        "size": s.size,
        "line_style": s.line_style.value,
        "line_width": s.line_width,
        "error_bars": s.error_bars.value,
        "error_column": s.error_column,
        "visible": s.visible,
        "in_legend": s.in_legend,
    }


def _axis_to_json(axis: AxisSpec) -> dict[str, Any]:
    return {
        "title": axis.title,
        "minimum": axis.minimum,
        "maximum": axis.maximum,
        "show_labels": axis.show_labels,
        "scale": axis.scale.value,
    }


def spec_from_json(data: Any) -> ChartSpec:
    """A spec from a JSON object. Raises ``ValueError`` saying what is wrong.

    Missing style keys keep their defaults, so later versions can add keys; the id,
    table, family and variant are required.
    """
    obj = _obj(data, "graph")
    try:
        series = tuple(_series_from_json(s) for s in _list(obj.get("series", []), "series"))
        raw_size = obj.get("custom_size")
        custom_size: tuple[float, float] | None = None
        if raw_size is not None:
            size = _list(raw_size, "custom_size")
            if len(size) != 2:
                raise ValueError("custom_size must be [width, height]")
            custom_size = (_num(size[0], "custom_size"), _num(size[1], "custom_size"))
        family = _enum(ChartFamily, obj.get("family"), "family")
        return ChartSpec(
            id=_str(obj.get("id"), "id"),
            table_id=_str(obj.get("table_id"), "table_id"),
            family=family,
            variant=_enum(ChartVariant, obj.get("variant"), "variant"),
            series=series,
            x_axis=_axis_from_json(obj.get("x_axis", {}), "x_axis"),
            y_axis=_axis_from_json(obj.get("y_axis", {}), "y_axis"),
            title=_str(obj.get("title", ""), "title"),
            aspect_ratio=_str(obj.get("aspect_ratio", DEFAULT_ASPECT), "aspect_ratio"),
            custom_size=custom_size,
            color_scheme=_str(obj.get("color_scheme", "curvis"), "color_scheme"),
            text_size=_num(obj.get("text_size", DEFAULT_TEXT_SIZE), "text_size"),
            overlays=tuple(
                FitOverlay(
                    _str(_obj(o, "overlay").get("analysis_id"), "overlay analysis_id"),
                    _bool(_obj(o, "overlay").get("show_band", False), "overlay show_band"),
                )
                for o in _list(obj.get("overlays", []), "overlays")
            ),
            show_legend=_bool(obj.get("show_legend", family is ChartFamily.XY), "show_legend"),
        )
    except TypeError as exc:  # pragma: no cover - guarded by the checks above
        raise ValueError(str(exc)) from None


def _series_from_json(data: Any) -> SeriesSpec:
    obj = _obj(data, "series")
    default = SeriesSpec("", "#000000")
    error_column = obj.get("error_column")
    return SeriesSpec(
        source=_str(obj.get("source"), "series source"),
        color=_str(obj.get("color"), "series color"),
        symbol=_enum(Symbol, obj.get("symbol", default.symbol), "symbol"),
        size=_num(obj.get("size", default.size), "size"),
        line_style=_enum(LineStyle, obj.get("line_style", default.line_style), "line_style"),
        line_width=_num(obj.get("line_width", default.line_width), "line_width"),
        error_bars=_enum(ErrorBars, obj.get("error_bars", default.error_bars), "error_bars"),
        error_column=None if error_column is None else _str(error_column, "error_column"),
        visible=_bool(obj.get("visible", True), "visible"),
        in_legend=_bool(obj.get("in_legend", True), "in_legend"),
    )


def _axis_from_json(data: Any, where: str) -> AxisSpec:
    obj = _obj(data, where)

    def limit(key: str) -> float | None:
        value = obj.get(key)
        return None if value is None else _num(value, f"{where} {key}")

    return AxisSpec(
        title=_str(obj.get("title", ""), f"{where} title"),
        minimum=limit("minimum"),
        maximum=limit("maximum"),
        show_labels=_bool(obj.get("show_labels", True), f"{where} show_labels"),
        scale=_enum(Scale, obj.get("scale", Scale.LINEAR), f"{where} scale"),
    )


def _obj(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be an object")
    return value


def _list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{where} must be a list")
    return value


def _str(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{where} must be text")
    return value


def _bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{where} must be true or false")
    return value


def _num(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{where} must be a number")
    return float(value)


def _enum[E: StrEnum](kind: type[E], value: Any, where: str) -> E:
    try:
        return kind(value)
    except ValueError:
        raise ValueError(f"{where} cannot be {value!r}") from None
