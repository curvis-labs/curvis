"""The graph title on screen: as wide as the plot at most, shortened with an ellipsis
when it is longer, and never squeezing the plot or the legend."""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtCore import QRectF, Qt
from pytestqt.qtbot import QtBot

from curvis.charts.axes import FONT_ASCENT_EM, TITLE_PAD_PT, TITLE_SCALE, page_px
from curvis.charts.builder import default_spec
from curvis.charts.data import compute, gather
from curvis.charts.render_qt.chart_view import ChartView
from curvis.charts.spec import ChartFamily
from tests.unit.charts_tables import replicate_table


def drawn(qtbot: QtBot, title: str) -> ChartView:
    """The XY graph on a square page with its legend and ``title``, at 192 dpi."""
    table = replicate_table()
    spec = default_spec(table, ChartFamily.XY)
    spec = replace(spec, title=title, aspect_ratio="square", show_legend=True)
    view = ChartView()
    qtbot.addWidget(view)
    view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    view.resize(*page_px(spec, 192))
    view.show()
    view.render_chart(spec, compute(gather(table, spec, {})))
    for _ in range(4):
        qtbot.wait(1)
    return view


def legend_box(view: ChartView) -> QRectF:
    return view.legend.mapRectToScene(view.legend.boundingRect())


@pytest.mark.parametrize("title", ["Dose by sex and time", "Response of cells to dose"])
def test_a_long_title_is_shortened_and_leaves_the_layout_alone(
    qtbot: QtBot, title: str
) -> None:
    short = drawn(qtbot, "Dose")
    long = drawn(qtbot, title)
    shown = long.title.shown_text()
    assert shown.endswith("…") and title.startswith(shown[:-1])
    # The plot and the legend are where a short title leaves them (the plot used to grow
    # to the title's width and squeeze the legend: "Fema" for "Female").
    assert long.plot_rect() == short.plot_rect()
    assert legend_box(long) == legend_box(short)
    assert long.legend_names() == ["Control", "Drug"]
    # The title is no wider than the plot, and sits over it.
    assert long.title.size().width() == pytest.approx(long.plot_rect().width(), abs=1)
    assert long.title.mapRectToScene(long.title.rect()).right() <= legend_box(long).left()


def test_a_title_that_fits_is_left_whole(qtbot: QtBot) -> None:
    assert drawn(qtbot, "Dose").title.shown_text() == "Dose"


def test_the_plot_starts_below_the_title_as_on_export(qtbot: QtBot) -> None:
    """Matplotlib: the title's baseline 0.76 em under the page padding, the plot 6 pt
    under the baseline. pyqtgraph's own title row was 30 px whatever the zoom."""
    view = drawn(qtbot, "Dose")
    assert view.spec is not None
    em = view._px(view.spec.text_size * TITLE_SCALE)
    assert view.title.size().height() == pytest.approx(
        FONT_ASCENT_EM * em + view._px(TITLE_PAD_PT), abs=0.5
    )
    no_title = drawn(qtbot, "")
    assert not no_title.title.isVisible()
    assert no_title.plot_rect().top() < view.plot_rect().top()
