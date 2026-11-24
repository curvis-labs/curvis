"""Where comparison brackets go on a bar-family graph, the same on screen and on export.

A bracket joins two groups: a line above both, with a short leg down at each end, and
its text (stars, "ns" or a P value) centred above the line. Brackets are stacked so
none crosses another or its text:

- They are placed from the shortest span to the longest (then from the left), so a
  bracket between neighbours sits lowest and a long one goes over them.
- A bracket clears everything drawn at the groups it spans and between them (bars with
  their error bars, boxes, violins and individual values), and the brackets and texts
  already placed there, by a small gap.

Sizes are in points of the page (the text size of the graph), turned into plot units
with the height the value axis gets on the page. That scale depends on the axis range,
which has to reach over the highest bracket, so the layout is worked out a few times
until it settles.

Pure: no Qt and no Matplotlib.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from curvis.charts.data import CategoryData, PairMark
from curvis.charts.spec import ChartVariant

GAP_EM = 0.3
"""Space between what a bracket clears and the ends of its legs, in ems of the text."""
LEG_EM = 0.3
"""Length of a bracket's legs, in ems."""
TEXT_PAD_EM = 0.1
"""Space between a bracket's line and the bottom of its text, in ems."""
TEXT_EM = 1.0
"""Height of a bracket's line of text, in ems."""
LEG_INSET = 0.06
"""How far inside the centre of each group a bracket ends, in category units, so the
brackets that meet at one group do not touch."""
MAX_SHARE = 0.7
"""The brackets take at most this share of the axis: with more of them, they are drawn
closer together (their sizes shrink), rather than squeezing the data to nothing."""
SQUEEZE = 0.85
"""Each try that does not fit shrinks the bracket sizes by this factor."""


@dataclass(frozen=True)
class Bracket:
    """One bracket, in plot coordinates: the line from ``x0`` to ``x1`` at ``y``, the
    legs down to ``y_leg``, the text centred at ``x_text`` with its bottom at
    ``y_text``."""

    x0: float
    x1: float
    y: float
    y_leg: float
    x_text: float
    y_text: float
    text: str
    first: str
    second: str


def category_top(c: CategoryData, variant: ChartVariant) -> float:
    """The highest point drawn for ``c`` (plot coordinates; NaN: nothing drawn)."""
    tops: list[float] = []
    if variant is ChartVariant.BAR:
        if math.isfinite(c.mean):
            tops += [c.mean, c.mean + c.err_high, 0.0]
    elif variant is ChartVariant.BOX:
        if c.plot_box.n:
            tops.append(c.plot_box.max)
    elif variant is ChartVariant.VIOLIN:
        if c.violin is not None and len(c.violin.grid):
            tops.append(float(c.violin.grid[-1]))
        elif c.plot_box.n:
            tops.append(c.plot_box.max)
    else:
        tops += [v for v in (c.plot_mean, c.plot_high) if math.isfinite(v)]
    if len(c.values):
        tops.append(float(np.max(c.values)))
    finite = [t for t in tops if math.isfinite(t)]
    return max(finite) if finite else math.nan


def place_brackets(
    pairs: Sequence[PairMark],
    positions: Mapping[str, float],
    tops: Mapping[str, float],
    em: float,
) -> list[Bracket]:
    """The brackets of ``pairs`` between the groups at ``positions`` (source → X), over
    ``tops`` (source → highest point drawn), with ``em`` plot units to an em of the
    text. Pairs whose groups are not both shown are left out."""
    finite_tops = [t for t in tops.values() if math.isfinite(t)]
    floor = min(finite_tops) if finite_tops else 0.0
    height = {s: (t if math.isfinite(t) else floor) for s, t in tops.items()}
    order = sorted(
        (p for p in pairs if p.first in positions and p.second in positions),
        key=lambda p: (
            abs(positions[p.first] - positions[p.second]),
            min(positions[p.first], positions[p.second]),
        ),
    )
    out: list[Bracket] = []
    for pair in order:
        a, b = sorted((positions[pair.first], positions[pair.second]))
        under = [s for s, x in positions.items() if a <= x <= b and s in height]
        base = max((height[s] for s in under), default=floor)
        y_leg = base + GAP_EM * em
        y = y_leg + LEG_EM * em
        y_text = y + TEXT_PAD_EM * em
        out.append(
            Bracket(
                a + LEG_INSET,
                b - LEG_INSET,
                y,
                y_leg,
                (a + b) / 2,
                y_text,
                pair.text,
                pair.first,
                pair.second,
            )
        )
        reach = y_text + TEXT_EM * em
        for s in under:
            height[s] = reach
    return out


def bracket_top(brackets: Sequence[Bracket], em: float) -> float:
    """The top of the highest bracket's text (NaN: no brackets)."""
    if not brackets:
        return math.nan
    return max(b.y_text for b in brackets) + TEXT_EM * em


def settle(
    pairs: Sequence[PairMark],
    positions: Mapping[str, float],
    tops: Mapping[str, float],
    low: float,
    high: float,
    axis_points: float,
    text_size: float,
    padding: float,
) -> tuple[list[Bracket], float]:
    """Brackets over data spanning ``low`` to ``high`` on an axis ``axis_points`` long,
    and the top the automatic range then needs: the top of the brackets plus
    ``padding`` of the span (``high`` itself when there are no brackets).

    The plot units per point ``u`` must be the range over the axis length, and the
    range's top depends on ``u``: the top is found where the two agree (by bisection,
    as the top grows with ``u``). When the brackets need more than :data:`MAX_SHARE` of
    the axis, their sizes shrink until they fit."""
    if not pairs or axis_points <= 0 or not (math.isfinite(low) and math.isfinite(high)):
        return [], high
    span = (high - low) or 1.0

    def top_for(units: float, scale: float) -> tuple[list[Bracket], float]:
        brackets = place_brackets(pairs, positions, tops, text_size * units * scale)
        if not brackets:
            return [], high
        reach = bracket_top(brackets, text_size * units * scale)
        return brackets, max(high, reach + padding * (reach - low))

    def excess(units: float, scale: float) -> float:
        """How far the range ``units`` gives falls short of the range it needs."""
        return top_for(units, scale)[1] - low - units * axis_points

    scale = 1.0
    start = span / axis_points
    while True:
        lo, hi = start, start
        for _ in range(60):
            if excess(hi, scale) <= 0:
                break
            lo, hi = hi, hi * 2
        fits = excess(hi, scale) <= 0
        if fits:
            for _ in range(60):
                mid = (lo + hi) / 2
                if excess(mid, scale) > 0:
                    lo = mid
                else:
                    hi = mid
            brackets, top = top_for(hi, scale)
            share = (top - high) / ((top - low) or 1.0)
            if share <= MAX_SHARE or scale < 0.05:
                return brackets, top
        elif scale < 0.05:  # pragma: no cover - a share above 1 even at this size
            return top_for(start, scale)
        scale *= SQUEEZE
