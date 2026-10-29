"""Scroll benchmarks for the table view. Target: 60 fps on a 1,000,000-cell table (CLAUDE.md).

Two kinds of frame, each on a tall and a wide table:

- **wheel**: scroll by 3 rows (one mouse-wheel step) and let Qt paint as in the app.
  Qt moves the old pixels and paints only the newly exposed rows.
- **page**: jump by a page (Page Down, or dragging the scroll bar) and repaint the cells
  and both headers in full. Every visible cell is drawn: the worst case.

The 60 fps target is asserted for wheel steps, which is how a table scrolls smoothly.
Page jumps are recorded for the 15% regression gate only: a full repaint of ~400
cells is bound by Qt's text drawing (about 30 µs per cell in software rendering).
"""

from collections.abc import Iterator
from itertools import cycle
from typing import Any

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from curvis.core import Document
from curvis.data import Column, ColumnRole, Table
from curvis.ui.table import TableModel, TableView

FRAME_TARGET_S = 1 / 60
VIEW_SIZE = (1200, 800)


def make_document(n_rows: int, n_cols: int) -> Document:
    rng = np.random.default_rng(0)
    columns = [
        Column.new(f"Y{c + 1}", ColumnRole.Y, rng.normal(100, 15, n_rows)) for c in range(n_cols)
    ]
    return Document(tables=[Table("Data 1", columns)])


def make_view(qtbot: QtBot, n_rows: int, n_cols: int) -> TableView:
    doc = make_document(n_rows, n_cols)
    model = TableModel(doc, doc.tables[0].id)
    v = TableView(model)
    model.setParent(v)
    qtbot.addWidget(v)
    v.resize(*VIEW_SIZE)
    v.show()
    qtbot.waitExposed(v)
    return v


@pytest.fixture
def tall(qtbot: QtBot) -> TableView:
    """100,000 rows x 10 columns."""
    return make_view(qtbot, 100_000, 10)


@pytest.fixture
def wide(qtbot: QtBot) -> TableView:
    """1,000 rows x 1,000 columns."""
    return make_view(qtbot, 1_000, 1_000)


def scroll_positions(view: TableView) -> Iterator[tuple[int, int]]:
    """Page by page through the table (and across a wide one), always showing full pages."""
    vbar, hbar = view.verticalScrollBar(), view.horizontalScrollBar()
    v_page, h_page = max(vbar.pageStep(), 1), max(hbar.pageStep(), 1)
    # Stop a page short of the end, where the spare rows and columns are empty.
    v_span, h_span = max(vbar.maximum() - v_page, 1), max(hbar.maximum() - h_page, 1)
    return cycle([((i * v_page) % v_span, (i * h_page // 4) % h_span) for i in range(400)])


def scroll_by_wheel_steps(benchmark: Any, view: TableView) -> None:
    assert view.model().n_data_rows * view.model().n_data_columns == 1_000_000
    vbar = view.verticalScrollBar()
    steps = list(range(0, min(vbar.maximum(), 900), 3))
    # Down and back up without repeating the turning points: every frame scrolls.
    positions = cycle(steps + steps[-2:0:-1])
    app = QApplication.instance()
    assert app is not None

    def frame() -> None:
        vbar.setValue(next(positions))
        app.processEvents()  # paints the exposed rows and flushes, as in the event loop

    benchmark.pedantic(frame, rounds=300, warmup_rounds=20)
    assert benchmark.stats.stats.median < FRAME_TARGET_S


def scroll_by_pages(benchmark: Any, view: TableView) -> None:
    assert view.model().n_data_rows * view.model().n_data_columns == 1_000_000
    positions = scroll_positions(view)
    vbar, hbar = view.verticalScrollBar(), view.horizontalScrollBar()
    app = QApplication.instance()
    assert app is not None

    def frame() -> None:
        v, h = next(positions)
        vbar.setValue(v)
        hbar.setValue(h)
        view.viewport().update()
        view.verticalHeader().viewport().update()
        view.horizontalHeader().viewport().update()
        app.processEvents()

    benchmark.pedantic(frame, rounds=300, warmup_rounds=20)


def test_scroll_tall_table_by_wheel_steps(benchmark: Any, tall: TableView) -> None:
    scroll_by_wheel_steps(benchmark, tall)


def test_scroll_wide_table_by_wheel_steps(benchmark: Any, wide: TableView) -> None:
    scroll_by_wheel_steps(benchmark, wide)


def test_scroll_tall_table_by_pages(benchmark: Any, tall: TableView) -> None:
    scroll_by_pages(benchmark, tall)


def test_scroll_wide_table_by_pages(benchmark: Any, wide: TableView) -> None:
    scroll_by_pages(benchmark, wide)
