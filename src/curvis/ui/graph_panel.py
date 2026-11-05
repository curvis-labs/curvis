"""The Graph panel, next to the table (SPEC.md: the user toggles between the Results and
the Graph panel).

The combo box switches between the graphs of the shown table. The graph redraws when
the graph engine has new plot data for it (after a data edit or a fit); while it is
being recomputed the old drawing stays with a "Recalculating…" badge. Notes such as
"1 series is no longer in the table." show under the graph.

The chart itself (:class:`~curvis.charts.render_qt.chart_view.ChartView`, which needs
pyqtgraph) is made when the first graph is shown, so opening a project stays quick.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from curvis.core import Document
from curvis.ui.dialogs import SPACING, secondary_label
from curvis.ui.toolbar_icons import ICON_SIZE

if TYPE_CHECKING:
    from curvis.charts.render_qt.chart_view import ChartView
    from curvis.compute.graph_engine import GraphEngine

EMPTY_TEXT = "No graphs yet.\nClick Plot to draw this table."
RECALCULATING = "Recalculating…"


class GraphPanel(QWidget):
    """The graphs of one table, one at a time."""

    toggle_requested = Signal(str, str, bool)
    """(graph id, series source, visible): a legend entry was clicked."""
    format_requested = Signal()
    """The graph was double-clicked."""
    current_changed = Signal()

    def __init__(
        self,
        document: Document,
        engine: GraphEngine | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("graphPanel")
        self.document = document
        self.engine = engine
        self.table_id: str | None = None
        self.chart: ChartView | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING, SPACING, SPACING, SPACING)
        layout.setSpacing(SPACING)
        header = QHBoxLayout()
        title = QLabel("Graph", self)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        self.combo = QComboBox(self)
        self.combo.setAccessibleName("Graph shown")
        self.combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.combo.setMinimumContentsLength(12)
        self.badge = secondary_label(RECALCULATING, self)
        self.badge.setWordWrap(False)
        self.badge.hide()
        self.toolbar = QToolBar("Graph toolbar", self)
        self.toolbar.setObjectName("graphToolbar")
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        self.toolbar.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.toolbar.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.toolbar.setContentsMargins(0, 0, 0, 0)
        self.toolbar.hide()  # until the window gives it its actions
        header.addWidget(title)
        header.addWidget(self.combo, 1)
        header.addWidget(self.badge)
        header.addWidget(self.toolbar)
        layout.addLayout(header)

        self.stack = QStackedWidget(self)
        self.empty = secondary_label(EMPTY_TEXT, self.stack)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.empty)
        layout.addWidget(self.stack, 1)
        self.notes = secondary_label("", self)
        self.notes.hide()
        layout.addWidget(self.notes)

        self.combo.currentIndexChanged.connect(lambda _i: self._on_current_changed())
        if engine is not None:
            engine.graphs_changed.connect(self.refresh_list)
            engine.state_changed.connect(self._on_state_changed)
        self.refresh_list()

    # --- public ------------------------------------------------------------------------

    def set_toolbar_actions(self, actions: Sequence[QAction]) -> None:
        """Show ``actions`` (registered actions with icons) as the panel's toolbar
        (docs/DESIGN.md §7, docs/mockups/graph-panel.html)."""
        self.toolbar.clear()
        for action in actions:
            self.toolbar.addAction(action)
            if action.menu() is not None:
                button = self.toolbar.widgetForAction(action)
                if isinstance(button, QToolButton):
                    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.toolbar.setVisible(bool(actions))

    @property
    def current_id(self) -> str | None:
        data = self.combo.currentData()
        return str(data) if data else None

    def set_table(self, table_id: str | None) -> None:
        self.table_id = table_id
        self.refresh_list()

    def name_of(self, graph_id: str) -> str:
        """The graph's name as the combo box lists it ("XY graph 2 · Title")."""
        index = self.combo.findData(graph_id)
        return self.combo.itemText(index) if index >= 0 else "graph"

    def set_current(self, graph_id: str) -> None:
        index = self.combo.findData(graph_id)
        if index >= 0:
            self.combo.setCurrentIndex(index)

    def refresh_list(self) -> None:
        """Fill the combo box with the graphs of the shown table, keeping the current one
        (or showing a newly added one)."""
        previous = {self.combo.itemData(i) for i in range(self.combo.count())}
        current = self.current_id
        graphs = self.document.graphs_of(self.table_id) if self.table_id else []
        self.combo.blockSignals(True)
        self.combo.clear()
        counts: dict[str, int] = {}
        for graph in graphs:
            family = graph.family.label
            counts[family] = counts.get(family, 0) + 1
            number = f" {counts[family]}" if counts[family] > 1 else ""
            title = f" · {graph.title}" if graph.title else ""
            self.combo.addItem(f"{family} graph{number}{title}", graph.id)
        added = [g.id for g in graphs if g.id not in previous]
        target = added[-1] if added and previous else current
        index = self.combo.findData(target) if target else -1
        self.combo.setCurrentIndex(index if index >= 0 else (0 if graphs else -1))
        self.combo.blockSignals(False)
        self._on_current_changed()

    # --- showing -----------------------------------------------------------------------

    def _on_current_changed(self) -> None:
        self._show()
        self.current_changed.emit()

    def _on_state_changed(self, graph_id: str) -> None:
        if graph_id == self.current_id:
            self._show()

    def _ensure_chart(self) -> ChartView:
        if self.chart is None:
            from curvis.charts.render_qt.chart_view import ChartView

            self.chart = ChartView(self.stack)
            self.chart.series_toggled.connect(self._on_series_toggled)
            self.chart.double_clicked.connect(self.format_requested)
            self.stack.addWidget(self.chart)
        return self.chart

    def _show(self) -> None:
        graph_id = self.current_id
        self.combo.setEnabled(graph_id is not None)
        if graph_id is None:
            self.badge.hide()
            self.notes.hide()
            self.stack.setCurrentWidget(self.empty)
            return
        try:
            spec = self.document.graph(graph_id)
        except KeyError:
            return
        engine = self.engine
        self.badge.setVisible(engine is not None and engine.is_stale(graph_id))
        chart = self._ensure_chart()
        self.stack.setCurrentWidget(chart)
        data = engine.data.get(graph_id) if engine is not None else None
        error = engine.errors.get(graph_id) if engine is not None else None
        stale = engine is not None and engine.is_stale(graph_id)
        if data is None or not stale or chart.spec is None or chart.spec.id != graph_id:
            chart.render_chart(spec, data)
        notes = [error] if error else list(data.notes if data is not None else ())
        if chart.message:
            notes.append(chart.message)
        self.notes.setText(" ".join(notes))
        self.notes.setVisible(bool(notes))

    def _on_series_toggled(self, source: str, visible: bool) -> None:
        if self.current_id is not None:
            self.toggle_requested.emit(self.current_id, source, visible)
