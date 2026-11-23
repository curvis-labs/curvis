"""The sketch of a curve-fit model in the Analyze dialog's *About this analysis*.

A sketch is computed, never drawn by hand: the model's own function evaluated at example
parameter values over a nominal X range (docs/models.md, "Sketches"). A fixed parameter's
value replaces its example value. Markers point out parameters on the curve: a horizontal
line at a plateau, a vertical line at an EC50, a label. It is an illustration of the
model, never a fit of the data.

No Qt here; :mod:`curvis.ui.sketch_view` draws it.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import numpy.typing as npt

type FloatArray = npt.NDArray[np.float64]
type Values = Mapping[str, float]
type MarkerKind = Literal["h", "v", "point", "label"]

SKETCH_POINTS = 100
FOLLOW_MARGIN = 3.0
"""For a sketch that follows its LogEC50s: X runs from 3 below the lowest to 3 above the
highest, so the plateaus show."""


@dataclass(frozen=True)
class Marker:
    """One mark on the sketch.

    - ``h``: a horizontal line at Y = position (a plateau);
    - ``v``: a vertical line from the X axis up to the curve at X = position (an EC50);
    - ``point``: a dot on the curve at X = position, with its label;
    - ``label``: a label beside the curve at X = position.

    The position is the value of ``param``, or ``where(values)`` for a value derived
    from the parameters (a half-life). ``param`` is also the parameter the mark stands
    for: when it is fixed, the mark shows its value in the accent colour.
    """

    kind: MarkerKind
    label: str
    param: str = ""
    where: Callable[[Values], float] | None = None

    def position(self, values: Values) -> float:
        if self.where is not None:
            return float(self.where(values))
        return float(values[self.param])


@dataclass(frozen=True)
class Sketch:
    """How to sketch a model: example values (by parameter name), the nominal X range
    and the markers. ``follow`` names parameters (LogEC50s) that the X range is centred
    on, so that the curve stays in view when one of them is fixed."""

    values: Mapping[str, float]
    x_range: tuple[float, float]
    markers: tuple[Marker, ...] = ()
    follow: tuple[str, ...] = ()

    def span(self, values: Values) -> tuple[float, float]:
        if not self.follow:
            return self.x_range
        at = [values[p] for p in self.follow]
        return min(at) - FOLLOW_MARGIN, max(at) + FOLLOW_MARGIN


@dataclass(frozen=True)
class PlacedMarker:
    kind: MarkerKind
    position: float
    """X for v, point and label markers; Y for h markers."""
    y: float
    """The curve's Y at the marker's X (NaN for h markers)."""
    label: str
    fixed: bool


@dataclass(frozen=True)
class SketchData:
    """A computed sketch: the curve and its markers, in the model's units."""

    x: FloatArray
    y: FloatArray
    markers: tuple[PlacedMarker, ...] = field(default=())


def number_text(value: float) -> str:
    """A parameter value as the sketch labels show it (−7, 100, 0.25, 1e-09)."""
    return f"{value:.4g}".replace("-", "−")


def compute(
    sketch: Sketch,
    f: Callable[[FloatArray, Values], FloatArray],
    fixed: Values,
    labels: Mapping[str, str],
) -> SketchData:
    """The sketch of ``f`` at the example values with ``fixed`` ones put in."""
    values = {**sketch.values, **{k: v for k, v in fixed.items() if k in sketch.values}}
    low, high = sketch.span(values)
    x = np.linspace(low, high, SKETCH_POINTS)
    with np.errstate(all="ignore"):
        y = np.asarray(f(x, values), dtype=np.float64)
    placed: list[PlacedMarker] = []
    for marker in sketch.markers:
        with np.errstate(all="ignore"):
            at = marker.position(values)
        if not math.isfinite(at):
            continue
        is_fixed = marker.param in fixed
        label = marker.label
        if is_fixed:
            name = labels.get(marker.param, marker.label)
            label = f"{name} = {number_text(fixed[marker.param])} (fixed)"
        if marker.kind == "h":
            placed.append(PlacedMarker("h", at, math.nan, label, is_fixed))
            continue
        if not low <= at <= high:
            continue
        with np.errstate(all="ignore"):
            y_at = float(np.asarray(f(np.array([at]), values), dtype=np.float64)[0])
        placed.append(PlacedMarker(marker.kind, at, y_at, label, is_fixed))
    return SketchData(x, y, tuple(placed))


LN2 = math.log(2)


def half_time(values: Values) -> float:
    """ln 2 / K, where the curve is halfway to its plateau."""
    k = values["k"]
    return LN2 / k if k > 0 else math.nan
