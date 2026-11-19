"""The export renderer: draws a :class:`~curvis.charts.spec.ChartSpec` with its
:class:`~curvis.charts.data.ChartData` as a Matplotlib figure.

It mirrors :mod:`curvis.charts.render_qt.chart_view`: the same page size, view ranges
and ticks (:mod:`curvis.charts.axes`), the same font (the graph's, from the bundled
files of :mod:`curvis.charts.fonts`), colours, symbol shapes, line widths, error-bar
caps, bands, and the legend to the right of the plot. Bar-family graphs are drawn from
the shapes of :mod:`curvis.charts.marks`, as on screen. The image-diff tests compare the
two.

Draw and save a figure inside ``matplotlib.rc_context(chart_rc(spec))``, which sets
the graph's font.

One difference is on purpose: a series hidden with a legend click is left out of an
exported legend (the screen lists it dimmed, so it can be clicked back on).

Matplotlib is slow to import, so it is imported inside the functions (CLAUDE.md rule 9).
No pyplot: figures are made directly, so this runs in worker processes and threads.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from curvis.charts.axes import (
    AXIS_WIDTH_PT,
    BAND_ALPHA,
    CAP_SHARE,
    CM_PER_INCH,
    ERROR_BAR_WIDTH_PT,
    FONT_FAMILY,
    LEGEND_BORDER_EM,
    LEGEND_COLUMN_GAP_EM,
    LEGEND_GAP_EM,
    LEGEND_ROW_EM,
    LEGEND_SAMPLE_EM,
    MINOR_TICK_LENGTH_PT,
    PAGE_PAD_PT,
    TICK_LENGTH_PT,
    TITLE_PAD_PT,
    TITLE_SCALE,
    AxesLayout,
    AxisLayout,
    axes_layout,
    column_major,
    legend_columns,
    wrap_to_fit,
)
from curvis.charts.colors import ColorScheme
from curvis.charts.data import ChartData, XYSeriesData
from curvis.charts.fonts import FONTS, chart_font_info
from curvis.charts.marks import LegendEntry, Marks, Polygon, Rect, bar_marks
from curvis.charts.spec import (
    LINE_VARIANTS,
    XY_VARIANTS,
    ChartFamily,
    ChartSpec,
    ChartVariant,
    LineStyle,
    SeriesSpec,
    Symbol,
)

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
"""Matplotlib settings for every graph in DejaVu Sans; :func:`chart_rc` gives a graph's
own font."""


@functools.cache
def _register_fonts() -> None:
    """Make the bundled chart fonts known to Matplotlib (once per process)."""
    from matplotlib import font_manager

    for font in FONTS.values():
        path = font.path()
        if font.file and path is not None and path.is_file():
            font_manager.fontManager.addfont(str(path))


def chart_rc(spec: ChartSpec) -> dict[str, Any]:
    """The Matplotlib settings to draw and save ``spec`` with (``matplotlib.rc_context``):
    :data:`RC_PARAMS` with the graph's font."""
    _register_fonts()
    return {**RC_PARAMS, "font.sans-serif": [chart_font_info(spec.font).family]}


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

    _register_fonts()
    width, height = spec.page_size_cm
    colors = spec.palette
    fig = Figure(figsize=(width / CM_PER_INCH, height / CM_PER_INCH), facecolor=colors.page)
    pad = PAGE_PAD_PT / 72
    fig.set_layout_engine(_title_layout()(w_pad=pad, h_pad=pad, wspace=0, hspace=0))
    ax = fig.add_subplot(axes_class=_chart_axes())
    layout = axes_layout(spec, data)
    _style(ax, spec, colors, layout)
    marks: Marks | None = None
    if data is not None:
        if spec.variant in XY_VARIANTS:
            _draw_xy(ax, spec, data, layout)
        elif spec.family is ChartFamily.BAR:
            marks = bar_marks(spec, data, layout)
            _draw_marks(ax, marks)
        elif spec.variant is ChartVariant.KAPLAN_MEIER:
            _draw_survival(ax, spec, data)
        else:
            raise ValueError(UNSUPPORTED)
    _legend(ax, spec, data, colors, layout, marks)
    return fig


@functools.cache
def _chart_axes() -> type[Axes]:
    """Matplotlib's Axes, with the title broken into lines no wider than the plot.

    The Graph panel wraps a title wider than the plot the same way (``ChartTitle``,
    :func:`~curvis.charts.axes.wrap_to_fit`). The constrained layout leaves a title's
    width out (only its height counts), so a long title would otherwise run past the
    page and over the legend.

    The wrapped title's height must be known to the layout, and the plot's width only
    after it: the layout asks for the axes' extent (``get_tightbbox``) in each of its
    passes, so the title is wrapped there to the plot's width of the pass before. The
    plot's width does not depend on the title's height, so the last pass wraps it as
    drawn; ``draw`` wraps it once more to the final width all the same."""
    from matplotlib.axes import Axes

    class ChartAxes(Axes):
        full_title = ""
        renderer: Any = None

        def _wrap_title(self, renderer: Any) -> bool:
            """Wrap the title to the plot's width now; True if its lines changed."""
            if renderer is not None:
                self.renderer = renderer
            renderer = self.renderer
            if not self.full_title or renderer is None:
                return False
            font = self.title.get_fontproperties()

            def measure(text: str) -> float:
                width, _h, _d = renderer.get_text_width_height_descent(text, font, ismath=False)
                return float(width)

            text = "\n".join(wrap_to_fit(self.full_title, self.bbox.width, measure))
            if text == self.title.get_text():
                return False
            self.title.set_text(text)
            return True

        def get_tightbbox(self, renderer: Any = None, *args: Any, **kwargs: Any) -> Any:
            self._wrap_title(renderer)
            return super().get_tightbbox(renderer, *args, **kwargs)

        def draw(self, renderer: Any) -> None:
            self._wrap_title(renderer)
            super().draw(renderer)

    return ChartAxes


@functools.cache
def _title_layout() -> type[Any]:
    """Matplotlib's constrained layout, run again (at most twice) while a wrapped title's
    lines do not fit the plot's final width: the layout stops after two passes, and the
    plot's width can still move by a few pixels in the second, enough to change where
    a long title breaks and so its height."""
    from matplotlib.layout_engine import ConstrainedLayoutEngine

    chart_axes: Any = _chart_axes()  # ChartAxes, with _wrap_title

    class TitleLayout(ConstrainedLayoutEngine):
        def execute(self, fig: Any) -> Any:
            result = super().execute(fig)
            for _ in range(2):
                axes: list[Any] = [ax for ax in fig.axes if isinstance(ax, chart_axes)]
                changed = [ax._wrap_title(None) for ax in axes]
                if not any(changed):
                    break
                result = super().execute(fig)
            return result

    return TitleLayout


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
        ax.set_title(
            spec.title,
            fontsize=spec.text_size * TITLE_SCALE,
            color=ink,
            pad=TITLE_PAD_PT,
            multialignment="center",
        )
        ax.full_title = spec.title  # type: ignore[attr-defined]  # ChartAxes
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
        if axis.label_angle:
            # Turned about the end of the label, which sits under its tick.
            for text in ax.get_xticklabels():
                text.set_rotation(axis.label_angle)
                text.set_rotation_mode("anchor")
                text.set_horizontalalignment("right")
                text.set_verticalalignment("center")
    else:
        ax.set_yticks(values, labels)
        ax.set_yticks(list(axis.minor), minor=True)


# --- XY ----------------------------------------------------------------------------------


def _draw_xy(ax: Axes, spec: ChartSpec, data: ChartData, layout: AxesLayout) -> None:

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
        if spec.variant in LINE_VARIANTS and line is LineStyle.NONE:
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
        xs, ys = _error_lines(series, cap)
        if len(xs):
            # One line with gaps, not a collection of segments: a PDF writes it in one
            # go, where 12,000 separate segments take seconds.
            ax.plot(
                xs,
                ys,
                color=style.color,
                linewidth=ERROR_BAR_WIDTH_PT,
                solid_capstyle="butt",
                zorder=4 + 2 * z,
                scalex=False,
                scaley=False,
            )


# --- survival ----------------------------------------------------------------------------


def _survival_line(style: SeriesSpec) -> LineStyle:
    """A survival curve is always drawn, so "no line" means a solid one."""
    return LineStyle.SOLID if style.line_style is LineStyle.NONE else style.line_style


def _draw_survival(ax: Axes, spec: ChartSpec, data: ChartData) -> None:
    styles = {s.source: s for s in spec.series}
    for z, curve in enumerate(data.survival):
        style = styles.get(curve.source)
        if style is None or not style.visible or len(curve.px) == 0:
            continue
        if spec.show_bands:
            ax.fill_between(
                curve.px,
                np.where(np.isfinite(curve.low), curve.low, curve.py),
                np.where(np.isfinite(curve.high), curve.high, curve.py),
                color=style.color,
                alpha=BAND_ALPHA,
                linewidth=0,
                zorder=1,
            )
        line = _survival_line(style)
        ax.plot(
            curve.px,
            curve.py,
            color=style.color,
            linewidth=style.line_width,
            linestyle=_linestyle(line),
            zorder=3 + 2 * z,
        )
        _dashes(ax.lines[-1], line, style.line_width)
        if len(curve.censor_px):
            ax.plot(
                curve.censor_px,
                curve.censor_py,
                linestyle="None",
                marker="|",
                markersize=style.size,
                markeredgewidth=style.line_width,
                color=style.color,
                zorder=4 + 2 * z,
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


def _error_lines(series: XYSeriesData, cap: float) -> tuple[np.ndarray, np.ndarray]:
    """The error bars of ``series`` as one line broken by NaN: per point, the vertical
    bar and its two caps."""
    low = np.where(np.isfinite(series.err_low), series.err_low, 0.0)
    high = np.where(np.isfinite(series.err_high), series.err_high, 0.0)
    keep = np.isfinite(series.err_low) | np.isfinite(series.err_high)
    x, y = series.px[keep], series.py[keep]
    top, bottom = y + high[keep], y - low[keep]
    half = cap / 2
    gap = np.full(len(x), np.nan)
    # Per point: bar (2 vertices), gap, top cap (2), gap, bottom cap (2), gap.
    xs = np.column_stack([x, x, gap, x - half, x + half, gap, x - half, x + half, gap])
    ys = np.column_stack([bottom, top, gap, top, top, gap, bottom, bottom, gap])
    return xs.ravel(), ys.ravel()


# --- bar-family marks -----------------------------------------------------------------


_DASHES = {LineStyle.DASH: (4.0, 2.0), LineStyle.DOT: (1.0, 2.0)}
"""Dash patterns in line widths, as Qt's dash and dot lines."""


def _draw_marks(ax: Axes, marks: Marks) -> None:
    """The shapes, lines and symbols of a bar-family graph (curvis.charts.marks)."""
    from matplotlib.collections import LineCollection
    from matplotlib.patches import Polygon as PolygonPatch
    from matplotlib.patches import Rectangle

    for shape in marks.shapes:
        common: dict[str, Any] = {
            "facecolor": _with_alpha(shape.fill, shape.fill_alpha),
            "edgecolor": shape.edge,
            "linewidth": shape.edge_pt,
            "zorder": 2,
        }
        if isinstance(shape, Rect):
            ax.add_patch(
                Rectangle((shape.x0, shape.y0), shape.x1 - shape.x0, shape.y1 - shape.y0, **common)
            )
        elif isinstance(shape, Polygon):
            xy = np.column_stack([shape.xs, shape.ys])
            ax.add_patch(PolygonPatch(xy, closed=True, joinstyle="miter", **common))
    for lines in marks.lines:
        style: Any = "solid"
        if lines.style in _DASHES:
            style = (0, tuple(p * lines.width_pt for p in _DASHES[lines.style]))
        ax.add_collection(
            LineCollection(
                [list(seg) for seg in lines.segments],
                colors=lines.color,
                linewidths=lines.width_pt,
                linestyles=style,
                capstyle="butt",
                zorder=3,
            ),
            autolim=False,
        )
    for points in marks.points:
        ax.plot(
            points.xs,
            points.ys,
            linestyle="None",
            marker=_MARKERS[points.symbol],
            markersize=points.size_pt,
            markerfacecolor=points.color,
            markeredgecolor=points.color,
            markeredgewidth=AXIS_WIDTH_PT,
            zorder=4,
            scalex=False,
            scaley=False,
        )


def _with_alpha(color: str, alpha: float) -> tuple[float, float, float, float]:
    from matplotlib.colors import to_rgba

    return to_rgba(color, alpha)


def _legend_handle(entry: LegendEntry) -> Any:
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    if entry.symbol is not None:
        return Line2D(
            [],
            [],
            linestyle="None",
            marker=_MARKERS[entry.symbol],
            markersize=entry.size_pt,
            markerfacecolor=entry.color,
            markeredgecolor=entry.color,
            markeredgewidth=AXIS_WIDTH_PT,
        )
    return Patch(
        facecolor=_with_alpha(entry.color, entry.fill_alpha),
        edgecolor=entry.edge or entry.color,
        linewidth=AXIS_WIDTH_PT,
    )


# --- legend ------------------------------------------------------------------------------


def _legend(
    ax: Axes,
    spec: ChartSpec,
    data: ChartData | None,
    colors: ColorScheme,
    layout: AxesLayout,
    marks: Marks | None = None,
) -> None:
    if not spec.show_legend or data is None:
        return
    from matplotlib.lines import Line2D

    styles = {s.source: s for s in spec.series}
    handles: list[Any] = []
    names: list[str] = []
    if spec.variant in XY_VARIANTS:
        for series in data.xy:
            style = styles[series.source]
            if not (style.visible and style.in_legend):
                continue
            line = style.line_style
            if spec.variant in LINE_VARIANTS and line is LineStyle.NONE:
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
    elif spec.variant is ChartVariant.KAPLAN_MEIER:
        for curve in data.survival:
            style = styles[curve.source]
            if not (style.visible and style.in_legend):
                continue
            line = _survival_line(style)
            handle = Line2D(
                [], [], color=style.color, linewidth=style.line_width, linestyle=_linestyle(line)
            )
            _dashes(handle, line, style.line_width)
            handles.append(handle)
            names.append(curve.name)
    elif marks is not None:
        for entry in marks.legend:
            handles.append(_legend_handle(entry))
            names.append(entry.name)
    if handles:
        figure = ax.get_figure()
        assert figure is not None
        # Right of the plot, at the top of the page, as in the Graph panel; under the
        # plot, row by row, when the page is too narrow for that.
        columns = legend_columns(spec, names)
        if columns:
            order = column_major(len(names), columns)
            handles, names = [handles[i] for i in order], [names[i] for i in order]
        figure.legend(
            handles,
            names,
            loc="outside lower center" if columns else "outside right upper",
            ncols=max(columns, 1),
            columnspacing=LEGEND_COLUMN_GAP_EM,
            frameon=False,
            fontsize=spec.text_size,
            labelcolor=colors.ink,
            # The sizes of the Graph panel's legend (curvis.charts.axes), in ems.
            borderaxespad=0.0,
            borderpad=LEGEND_BORDER_EM,  # the half row above the first name
            handlelength=LEGEND_SAMPLE_EM,
            handleheight=0.7,
            handletextpad=LEGEND_GAP_EM,
            labelspacing=LEGEND_ROW_EM - 1.0,
        )


__all__ = ("RC_PARAMS", "chart_rc", "make_figure")
