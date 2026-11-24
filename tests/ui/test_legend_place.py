"""The legend in the Graph panel: it never squeezes the plot to a strip, and inside the
plot it sits in an empty corner and moves when the data fill that corner."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from PySide6.QtCore import QRectF, Qt
from pytestqt.qtbot import QtBot

from curvis.charts.axes import page_px
from curvis.charts.builder import default_spec
from curvis.charts.data import ChartData, compute, gather
from curvis.charts.legend import Corner, LegendPlace
from curvis.charts.render_qt.chart_view import ChartView
from curvis.charts.spec import ChartFamily, ChartSpec, ChartVariant, LegendPosition, Symbol
from curvis.data import Table
from tests.unit.charts_tables import SPECTRA_NAMES, column_table, spectra_table

SHORT = ("BL21 80", "BL21 250", "BL21 800", "C41 80", "C41 800", "C43 80", "C43 800")


def spectra(names: tuple[str, ...] = SPECTRA_NAMES, **changes: object) -> tuple[Table, ChartSpec]:
    table = spectra_table(names)
    spec = replace(default_spec(table, ChartFamily.XY), variant=ChartVariant.LINE, title="Data 1")
    spec = replace(spec, series=tuple(replace(s, symbol=Symbol.NONE) for s in spec.series))
    return table, replace(spec, **changes)  # type: ignore[arg-type]


def view_of(qtbot: QtBot, spec: ChartSpec) -> ChartView:
    view = ChartView()
    qtbot.addWidget(view)
    view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    view.resize(*page_px(spec, 192))
    view.show()
    return view


def draw(qtbot: QtBot, view: ChartView, spec: ChartSpec, data: ChartData) -> None:
    view.render_chart(spec, data)
    for _ in range(4):
        qtbot.wait(1)


def legend_box(view: ChartView) -> QRectF:
    box = view.legend.mapRectToScene(view.legend.boundingRect())
    return box.translated(view.plot_widget.pos())


def test_seven_long_names_leave_the_plot_most_of_the_page(qtbot: QtBot) -> None:
    """The bug report: the legend under the plot squashed it to a thin strip."""
    table, spec = spectra()
    view = view_of(qtbot, spec)
    draw(qtbot, view, spec, compute(gather(table, spec, {})))
    plan = view.legend_plan
    assert plan is not None and plan.place is LegendPlace.BELOW and plan.columns >= 2
    assert view.plot_rect().height() > 0.4 * view.plot_widget.height()
    assert view.legend_names() == list(SPECTRA_NAMES)  # in full
    assert view.legend_texts()[0].endswith("500 ml_bl")
    assert legend_box(view).top() >= view.plot_rect().bottom()


def test_too_many_series_end_in_a_count(qtbot: QtBot) -> None:
    names = tuple(f"Sample {i}" for i in range(1, 61))
    table, spec = spectra(names)
    view = view_of(qtbot, spec)
    draw(qtbot, view, spec, compute(gather(table, spec, {})))
    texts = view.legend_texts()
    assert texts[-1].startswith("+") and texts[-1].endswith(" more")
    assert len(texts) - 1 + int(texts[-1].split()[0][1:]) == 60
    assert view.plot_rect().height() > 0.4 * view.plot_widget.height()


def test_inside_sits_in_the_empty_corner_of_the_plot(qtbot: QtBot) -> None:
    table, spec = spectra(SHORT, legend_position=LegendPosition.INSIDE)
    view = view_of(qtbot, spec)
    draw(qtbot, view, spec, compute(gather(table, spec, {})))
    plan = view.legend_plan
    assert plan is not None and plan.place is LegendPlace.INSIDE
    assert plan.corner is Corner.UPPER_RIGHT
    plot, box = view.plot_rect(), legend_box(view)
    assert plot.contains(box)
    assert box.right() > plot.right() - 0.1 * plot.width()
    assert box.top() < plot.top() + 0.1 * plot.height()
    assert view.legend_names() == list(SHORT)


def test_a_data_edit_moves_an_inside_legend(qtbot: QtBot) -> None:
    """The peaks move to the right: the legend moves to the upper left."""
    table, spec = spectra(SHORT, legend_position=LegendPosition.INSIDE)
    view = view_of(qtbot, spec)
    draw(qtbot, view, spec, compute(gather(table, spec, {})))
    assert view.legend_plan is not None and view.legend_plan.corner is Corner.UPPER_RIGHT
    x = table.columns[0].values
    for i, column in enumerate(table.columns[1:]):
        column.values = (0.1 + 0.1 * i) * np.exp(-(((x - 540.0) / 10.0) ** 2))
    draw(qtbot, view, spec, compute(gather(table, spec, {})))
    assert view.legend_plan is not None and view.legend_plan.corner is Corner.UPPER_LEFT
    plot, box = view.plot_rect(), legend_box(view)
    assert plot.contains(box) and box.left() < plot.center().x()


def test_inside_falls_back_outside_and_the_legend_can_go_and_come_back(qtbot: QtBot) -> None:
    table = column_table()
    spec = replace(
        default_spec(table, ChartFamily.BAR),
        show_legend=True,
        legend_position=LegendPosition.INSIDE,
    )
    data = compute(gather(table, spec, {}))
    view = view_of(qtbot, spec)
    draw(qtbot, view, spec, data)
    assert view.legend_plan is not None and view.legend_plan.place is LegendPlace.RIGHT
    assert legend_box(view).left() >= view.plot_rect().right() - 1
    # Inside, outside, hidden and inside again, on one view.
    table2, inside = spectra(SHORT, legend_position=LegendPosition.INSIDE)
    data2 = compute(gather(table2, inside, {}))
    for changed in (
        inside,
        replace(inside, legend_position=LegendPosition.OUTSIDE),
        replace(inside, show_legend=False),
        inside,
    ):
        draw(qtbot, view, changed, data2)
        if not changed.show_legend:
            assert view.legend_plan is None and view.legend.scene() is None
        elif changed.legend_position is LegendPosition.INSIDE:
            assert view.legend.parentItem() is view.plot_item.vb
            assert view.plot_rect().contains(legend_box(view))
        else:
            assert legend_box(view).left() >= view.plot_rect().right() - 1


def test_an_inside_legend_still_toggles_series(qtbot: QtBot) -> None:
    table, spec = spectra(SHORT, legend_position=LegendPosition.INSIDE)
    view = view_of(qtbot, spec)
    draw(qtbot, view, spec, compute(gather(table, spec, {})))
    toggled: list[tuple[str, bool]] = []
    view.series_toggled.connect(lambda source, on: toggled.append((source, on)))
    source, sample = view.legend_entries()[0]
    centre = sample.mapRectToScene(sample.boundingRect()).center()
    at = view.plot_widget.mapFromScene(centre)
    qtbot.mouseClick(view.plot_widget.viewport(), Qt.MouseButton.LeftButton, pos=at)
    assert toggled == [(source, False)]
