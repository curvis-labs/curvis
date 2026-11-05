"""The export renderer: draws a :class:`~curvis.charts.spec.ChartSpec` with its
:class:`~curvis.charts.data.ChartData` as a Matplotlib figure.

It mirrors :mod:`curvis.charts.render_qt.chart_view`: the same page size, view ranges
and ticks (:mod:`curvis.charts.axes`), the same font (DejaVu Sans), colours, symbol
shapes, line widths, error-bar caps, bands, bars and boxes, and the legend to the right
of the plot. The image-diff tests compare the two.

One difference is on purpose: a series hidden with a legend click is left out of an
exported legend (the screen lists it dimmed, so it can be clicked back on).

Matplotlib is slow to import, so it is imported inside the functions (CLAUDE.md rule 9).
No pyplot: figures are made directly, so this runs in worker processes and threads.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from curvis.charts.axes import (
    AXIS_WIDTH_PT,
    BAND_ALPHA,
    BAR_WIDTH,
    BOX_FILL_ALPHA,
    BOX_LINE_PT,
    BOX_WIDTH,
    CAP_SHARE,
    CM_PER_INCH,
    ERROR_BAR_WIDTH_PT,
    FONT_FAMILY,
    LEGEND_GAP_EM,
    LEGEND_ROW_EM,
    LEGEND_SAMPLE_EM,
    MINOR_TICK_LENGTH_PT,
    PAGE_PAD_PT,
    TICK_LENGTH_PT,
    TITLE_SCALE,
    AxesLayout,
    AxisLayout,
    axes_layout,
)
from curvis.charts.colors import ColorScheme, scheme
from curvis.charts.data import ChartData, XYSeriesData
from curvis.charts.spec import ChartSpec, ChartVariant, LineStyle, SeriesSpec, Symbol

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure

UNSUPPORTED = "This chart type cannot be exported yet."

RC_PARAMS: dict[str, Any] = {
    "font.family": "sans-serif",
    "font.sans-serif": [FONT_FAMILY],
    "pdf.fonttype": 42,  # embed TrueType fonts in PDFs
    "ps.fonttype": 42,
    "svg.fonttype": "none",  # keep text as text in SVGs
    "svg.hashsalt": "curvis",  # the same graph gives the same SVG
    "axes.unicode_minus": True,
    "path.simplify": False,
}
"""Matplotlib settings for every graph (use with ``matplotlib.rc_context``)."""

# Symbols as pyqtgraph draws them (y up here, down there); a custom marker of Matplotlib
# is scaled so its largest coordinate is 0.5, like pyqtgraph's, so sizes match.
_MARKERS: dict[Symbol, Any] = {
    Symbol.CIRCLE: "o",
    Symbol.SQUARE: "s",
    Symbol.TRIANGLE: [(-0.5, -0.5), (0.0, 0.5), (0.5, -0.5), (-0.5, -0.5)],
    Symbol.DIAMOND: [(0.0, -0.5), (-0.4, 0.0), (0.0, 0.5), (0.4, 0.0), (0.0, -0.5)],
    Symbol.NONE: "None",
}


def _linestyle(style: LineStyle) -> Literal["-", "None"]:
    """Matplotlib's line style; dashes and dots get their pattern from :func:`_dashes`."""
    return "None" if style is LineStyle.NONE else "-"


def make_figure(spec: ChartSpec, data: ChartData | None) -> Figure:
    """A Matplotlib figure of ``spec`` drawing ``data``, sized to the graph's page.

    Call it inside ``matplotlib.rc_context(RC_PARAMS)`` and keep the figure in that
    context while drawing or saving it, so the fonts are the chart's.
    """
    from matplotlib.figure import Figure

    width, height = spec.page_size_cm
    colors = scheme(spec.color_scheme)
    fig = Figure(figsize=(width / CM_PER_INCH, height / CM_PER_INCH), facecolor=colors.page)
    pad = PAGE_PAD_PT / 72
    fig.set_layout_engine("constrained", w_pad=pad, h_pad=pad, wspace=0, hspace=0)
    ax = fig.add_subplot()
    layout = axes_layout(spec, data)
    _style(ax, spec, colors, layout)
    if data is not None:
        if spec.variant in (ChartVariant.SCATTER, ChartVariant.LINE):
            _draw_xy(ax, spec, data, layout)
        elif spec.variant in (ChartVariant.BAR, ChartVariant.BOX):
            _draw_categories(ax, spec, data, colors, layout)
        else:
            raise ValueError(UNSUPPORTED)
    _legend(ax, spec, data, colors, layout)
    return fig


# --- frame -------------------------------------------------------------------------------


def _style(ax: Axes, spec: ChartSpec, colors: ColorScheme, layout: AxesLayout) -> None:
    ink = colors.ink
    ax.set_facecolor(colors.page)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(ink)
        ax.spines[side].set_linewidth(AXIS_WIDTH_PT)
        ax.spines[side].set_capstyle("projecting")
    if spec.title:
        ax.set_title(spec.title, fontsize=spec.text_size * TITLE_SCALE, color=ink)
    ax.set_xlabel(spec.x_axis.title, fontsize=spec.text_size, color=ink)
    ax.set_ylabel(spec.y_axis.title, fontsize=spec.text_size, color=ink)
    _axis_ticks(ax, "x", layout.x, spec.x_axis.show_labels)
    _axis_ticks(ax, "y", layout.y, spec.y_axis.show_labels)
    ax.tick_params(
        which="both",
        direction="out",
        width=AXIS_WIDTH_PT,
        colors=ink,
        labelsize=spec.text_size,
        pad=TICK_LENGTH_PT * 0.8,
    )
    ax.tick_params(which="major", length=TICK_LENGTH_PT)
    ax.tick_params(which="minor", length=MINOR_TICK_LENGTH_PT)
    ax.set_xlim(layout.x.low, layout.x.high)
    ax.set_ylim(layout.y.low, layout.y.high)


def _axis_ticks(ax: Axes, which: str, axis: AxisLayout, show_labels: bool) -> None:
    values = [t.value for t in axis.major]
    labels = [t.label for t in axis.major] if show_labels else [""] * len(values)
    if which == "x":
        ax.set_xticks(values, labels)
        ax.set_xticks(list(axis.minor), minor=True)
    else:
        ax.set_yticks(values, labels)
        ax.set_yticks(list(axis.minor), minor=True)


# --- XY ----------------------------------------------------------------------------------


def _draw_xy(ax: Axes, spec: ChartSpec, data: ChartData, layout: AxesLayout) -> None:
    from matplotlib.collections import LineCollection

    styles = {s.source: s for s in spec.series}
    for curve in data.curves:
        style = styles.get(curve.series_source)
        if style is None or not style.visible or len(curve.px) == 0:
            continue
        if curve.show_band:
            ax.fill_between(
                curve.px,
                np.where(np.isfinite(curve.low), curve.low, curve.py),
                np.where(np.isfinite(curve.high), curve.high, curve.py),
                color=style.color,
                alpha=BAND_ALPHA,
                linewidth=0,
                zorder=1,
            )
        ax.plot(curve.px, curve.py, color=style.color, linewidth=style.line_width, zorder=2)
    cap = CAP_SHARE * (layout.x.high - layout.x.low)
    for z, series in enumerate(data.xy):
        style = styles[series.source]
        if not style.visible:
            continue
        line = style.line_style
        if spec.variant is ChartVariant.LINE and line is LineStyle.NONE:
            line = LineStyle.SOLID
        ax.plot(
            series.px,
            series.py,
            linestyle=_linestyle(line),
            linewidth=style.line_width,
            color=style.color,
            zorder=3 + 2 * z,
            **_marker(style),
        )
        _dashes(ax.lines[-1], line, style.line_width)
        segments = _error_segments(series, cap)
        if segments:
            ax.add_collection(
                LineCollection(
                    segments,
                    colors=style.color,
                    linewidths=ERROR_BAR_WIDTH_PT,
                    capstyle="butt",
                    zorder=4 + 2 * z,
                ),
                autolim=False,
            )


def _marker(style: SeriesSpec) -> dict[str, Any]:
    return {
        "marker": _MARKERS[style.symbol],
        "markersize": style.size,
        "markerfacecolor": style.color,
        "markeredgecolor": style.color,
        "markeredgewidth": AXIS_WIDTH_PT,
    }


def _dashes(line: Any, style: LineStyle, width: float) -> None:
    if style in (LineStyle.DASH, LineStyle.DOT):
        pattern = (4.0, 2.0) if style is LineStyle.DASH else (1.0, 2.0)
        line.set_dashes([p * max(width, 0.1) for p in pattern])
        line.set_dash_capstyle("butt")


def _error_segments(series: XYSeriesData, cap: float) -> list[list[tuple[float, float]]]:
    """The error bars of ``series`` as line segments: a vertical line and its caps."""
    segments: list[list[tuple[float, float]]] = []
    half = cap / 2
    for x, y, low, high in zip(series.px, series.py, series.err_low, series.err_high, strict=True):
        if not (math.isfinite(low) or math.isfinite(high)):
            continue
        top = y + (high if math.isfinite(high) else 0.0)
        bottom = y - (low if math.isfinite(low) else 0.0)
        segments.append([(x, bottom), (x, top)])
        segments.append([(x - half, top), (x + half, top)])
        segments.append([(x - half, bottom), (x + half, bottom)])
    return segments


# --- bars and boxes --------------------------------------------------------------------


def _draw_categories(
    ax: Axes, spec: ChartSpec, data: ChartData, colors: ColorScheme, layout: AxesLayout
) -> None:
    from matplotlib.collections import LineCollection
    from matplotlib.patches import Rectangle

    styles = {s.source: s for s in spec.series}
    positions = layout.positions
    shown = [c for c in data.categories if c.source in positions]
    if spec.variant is ChartVariant.BOX:
        half, cap = BOX_WIDTH / 2, BOX_WIDTH / 4
        for c in shown:
            b, color, at = c.plot_box, styles[c.source].color, float(positions[c.source])
            if b.n == 0:
                continue
            ax.add_patch(
                Rectangle(
                    (at - half, b.q1),
                    BOX_WIDTH,
                    b.q3 - b.q1,
                    facecolor=_with_alpha(color, BOX_FILL_ALPHA),
                    edgecolor=color,
                    linewidth=BOX_LINE_PT,
                    zorder=2,
                )
            )
            lines = [
                [(at - half, b.median), (at + half, b.median)],
                [(at, b.q3), (at, b.max)],
                [(at, b.q1), (at, b.min)],
                [(at - cap, b.max), (at + cap, b.max)],
                [(at - cap, b.min), (at + cap, b.min)],
            ]
            ax.add_collection(
                LineCollection(
                    lines, colors=color, linewidths=BOX_LINE_PT, capstyle="butt", zorder=3
                ),
                autolim=False,
            )
        return
    x = np.array([positions[c.source] for c in shown], dtype=np.float64)
    heights = np.nan_to_num(np.array([c.mean for c in shown], dtype=np.float64))
    if len(shown):
        ax.bar(
            x,
            heights,
            width=BAR_WIDTH,
            color=[styles[c.source].color for c in shown],
            edgecolor=colors.ink,
            linewidth=AXIS_WIDTH_PT,
            zorder=2,
        )
    half = BAR_WIDTH / 6
    segments: list[list[tuple[float, float]]] = []
    for c, position, height in zip(shown, x.tolist(), heights.tolist(), strict=True):
        if not math.isfinite(c.mean) or (c.err_high <= 0 and c.err_low <= 0):
            continue
        top, bottom = height + c.err_high, height - c.err_low
        segments += [
            [(position, bottom), (position, top)],
            [(position - half, top), (position + half, top)],
            [(position - half, bottom), (position + half, bottom)],
        ]
    if segments:
        ax.add_collection(
            LineCollection(
                segments, colors=colors.ink, linewidths=BOX_LINE_PT, capstyle="butt", zorder=3
            ),
            autolim=False,
        )


def _with_alpha(color: str, alpha: float) -> tuple[float, float, float, float]:
    from matplotlib.colors import to_rgba

    return to_rgba(color, alpha)


# --- legend ------------------------------------------------------------------------------


def _legend(
    ax: Axes, spec: ChartSpec, data: ChartData | None, colors: ColorScheme, layout: AxesLayout
) -> None:
    if not spec.show_legend or data is None:
        return
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    styles = {s.source: s for s in spec.series}
    handles: list[Any] = []
    names: list[str] = []
    if spec.variant in (ChartVariant.SCATTER, ChartVariant.LINE):
        for series in data.xy:
            style = styles[series.source]
            if not (style.visible and style.in_legend):
                continue
            line = style.line_style
            if spec.variant is ChartVariant.LINE and line is LineStyle.NONE:
                line = LineStyle.SOLID
            handle = Line2D(
                [],
                [],
                color=style.color,
                linewidth=style.line_width,
                linestyle=_linestyle(line),
                **_marker(style),
            )
            _dashes(handle, line, style.line_width)
            handles.append(handle)
            names.append(series.name)
    else:
        for c in data.categories:
            if c.source in layout.positions and styles[c.source].in_legend:
                handles.append(
                    Patch(
                        facecolor=styles[c.source].color,
                        edgecolor=colors.ink,
                        linewidth=AXIS_WIDTH_PT,
                    )
                )
                names.append(c.name)
    if handles:
        figure = ax.get_figure()
        assert figure is not None
        # Right of the plot, at the top of the page, as in the Graph panel.
        figure.legend(
            handles,
            names,
            loc="outside right upper",
            frameon=False,
            fontsize=spec.text_size,
            labelcolor=colors.ink,
            # The sizes of the Graph panel's legend (curvis.charts.axes), in ems.
            borderaxespad=0.0,
            borderpad=(LEGEND_ROW_EM - 1.0) / 2,  # the half row above the first name
            handlelength=LEGEND_SAMPLE_EM,
            handleheight=0.7,
            handletextpad=LEGEND_GAP_EM,
            labelspacing=LEGEND_ROW_EM - 1.0,
        )


__all__ = ("RC_PARAMS", "make_figure")
