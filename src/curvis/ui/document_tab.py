"""One open document: its model, file path and the widget shown in its tab (the table,
with the Results and Graph panels to its right, one at a time)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QLabel, QSplitter, QTabWidget, QVBoxLayout, QWidget

from curvis.charts.spec import ChartFamily
from curvis.core import Command, CommandError, Document, UndoEvent
from curvis.core.constants import ProjectConstant
from curvis.settings.store import DECIMAL_PLACES_DEFAULT
from curvis.ui.graph_panel import GraphPanel
from curvis.ui.results_panel import ResultsPanel
from curvis.ui.table import TableModel, TableView

if TYPE_CHECKING:
    from curvis.analysis.base import Analysis
    from curvis.compute import Scheduler
    from curvis.compute.analysis_engine import AnalysisEngine
    from curvis.compute.calc_engine import CalcEngine
    from curvis.compute.graph_engine import GraphEngine
    from curvis.ui.analyze_dialog import AnalyzeDialog
    from curvis.ui.calculate_dialog import CalculateDialog

RESULTS_PANEL_WIDTH = 520
"""Starting width of the Results and Graph panels."""
RESULTS_TAB, GRAPH_TAB = 0, 1


class DocumentTab(QWidget):
    """The tab of one document: the view of its first table, or a placeholder.

    ``message`` sends short notes for the status bar, such as why an edit was refused.
    With a ``scheduler``, the tab keeps the document's calculated columns and analyses up
    to date. ``recent_analyses`` gives the recently used analysis types (newest first);
    ``analysis_used`` is sent with the type of each analysis that is run.
    """

    message = Signal(str)
    manage_constants_requested = Signal()
    analysis_used = Signal(str)
    state_changed = Signal()
    """What the tab's actions can act on changed (e.g. the analysis shown)."""

    def __init__(
        self,
        document: Document,
        path: Path | None,
        parent: QWidget | None = None,
        *,
        decimal_places: int = DECIMAL_PLACES_DEFAULT,
        scheduler: Scheduler | None = None,
        global_constants: Callable[[], Sequence[ProjectConstant]] = list,
        recent_analyses: Callable[[], Sequence[str]] = list,
    ) -> None:
        super().__init__(parent)
        self.document = document
        self.path = path
        self._decimal_places = decimal_places
        self._global_constants = global_constants
        self._recent_analyses = recent_analyses
        self.engine: CalcEngine | None = None
        self.analysis_engine: AnalysisEngine | None = None
        self.graph_engine: GraphEngine | None = None
        if scheduler is not None:
            from curvis.compute.analysis_engine import AnalysisEngine
            from curvis.compute.calc_engine import CalcEngine
            from curvis.compute.graph_engine import GraphEngine

            self.engine = CalcEngine(document, scheduler, global_constants, self)
            self.engine.values_changed.connect(self._on_values_changed)
            self.analysis_engine = AnalysisEngine(document, self.engine, self)
            self.graph_engine = GraphEngine(document, self.engine, self.analysis_engine, self)
        self.table_view: TableView | None = None
        self.placeholder = QLabel("This project has no tables yet.", self)
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setEnabled(False)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setChildrenCollapsible(False)
        self.side_tabs = QTabWidget(self.splitter)
        self.side_tabs.setObjectName("sidePanels")
        self.side_tabs.setDocumentMode(True)
        self.side_tabs.setMinimumWidth(260)
        self.results_panel = ResultsPanel(document, self.analysis_engine, self.side_tabs)
        self.results_panel.edit_requested.connect(self.edit_analysis)
        self.results_panel.current_changed.connect(self.state_changed)
        self.graph_panel = GraphPanel(document, self.graph_engine, self.side_tabs)
        self.graph_panel.current_changed.connect(self.state_changed)
        self.graph_panel.toggle_requested.connect(self._toggle_series)
        self.side_tabs.addTab(self.results_panel, "Results")
        self.side_tabs.addTab(self.graph_panel, "Graph")
        self.side_tabs.currentChanged.connect(lambda _i: self.state_changed.emit())
        self.splitter.addWidget(self.side_tabs)
        self.splitter.setStretchFactor(0, 1)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(self.placeholder)
        self._layout.addWidget(self.splitter)
        self._show_first_table()
        self._unsubscribe = document.undo_stack.subscribe(self._on_undo_event)
        self.destroyed.connect(lambda *_: self._unsubscribe())

    @property
    def title(self) -> str:
        """The file name without extension, or the document name for a new project."""
        return self.path.stem if self.path is not None else self.document.name

    @property
    def is_modified(self) -> bool:
        """True when there are changes since the last save (or since it was created)."""
        return not self.document.undo_stack.is_clean

    def set_decimal_places(self, places: int) -> None:
        self._decimal_places = places
        if self.table_view is not None:
            self.table_view.model().set_decimal_places(places)

    def close_engine(self) -> None:
        """Stop recomputing (the tab is closing)."""
        if self.graph_engine is not None:
            self.graph_engine.close()
            self.graph_engine = None
        if self.analysis_engine is not None:
            self.analysis_engine.close()
            self.analysis_engine = None
        if self.engine is not None:
            self.engine.close()
            self.engine = None

    # --- calculations ----------------------------------------------------------------------

    def calculate(self) -> bool:
        """Open the Calculate dialog for the shown table; True if columns were added."""
        if self.table_view is None:
            return False
        from curvis.ui.calculate_dialog import CalculateDialog, selected_sources

        model = self.table_view.model()
        selected = [c.id for c in model.columns_at(self.table_view.selected_columns())]
        dialog = CalculateDialog(
            self.document,
            model.table_id,
            selected=selected_sources(model.table, selected),
            global_constants=self._global_constants(),
            manage_constants=self.manage_constants_requested.emit,
            parent=self,
        )
        return self._run_dialog(dialog, editing=False)

    def edit_calculation(self, column_id: str | None = None) -> bool:
        """Reopen the Calculate dialog for the calculation of ``column_id`` (default: current)."""
        if self.table_view is None:
            return False
        column_id = column_id or self.table_view.current_calculated_column()
        calculation = self.document.calculation_of(column_id) if column_id else None
        if calculation is None:
            self.message.emit("This column is not calculated.")
            return False
        from curvis.ui.calculate_dialog import CalculateDialog

        dialog = CalculateDialog(
            self.document,
            calculation.table_id,
            calculation=calculation,
            global_constants=self._global_constants(),
            manage_constants=self.manage_constants_requested.emit,
            parent=self,
        )
        return self._run_dialog(dialog, editing=True)

    def _run_dialog(self, dialog: CalculateDialog, *, editing: bool) -> bool:
        from curvis.core import add_calculation, edit_calculation

        try:
            if self._exec_dialog(dialog) != QDialog.DialogCode.Accepted:
                return False
            calculation = dialog.calculation()
            try:
                build = edit_calculation if editing else add_calculation
                self.document.execute(build(self.document, calculation))
            except CommandError as exc:
                self.message.emit(str(exc))
                return False
            return True
        finally:
            dialog.deleteLater()

    # --- analyses --------------------------------------------------------------------------

    @property
    def current_analysis_id(self) -> str | None:
        """The analysis shown in the Results panel."""
        return self.results_panel.current_id

    def analyze(self) -> bool:
        """Open the Analyze dialog for the shown table; True if an analysis was added."""
        if self.table_view is None:
            return False
        from curvis.ui.analyze_dialog import AnalyzeDialog

        dialog = AnalyzeDialog(
            self.document,
            self.table_view.model().table_id,
            recent=self._recent_analyses(),
            parent=self,
        )
        return self._run_analyze_dialog(dialog, editing=False)

    def edit_analysis(self, analysis_id: str | None = None) -> bool:
        """Reopen the Analyze dialog with the stored parameters of ``analysis_id`` (default:
        the analysis shown in the Results panel)."""
        analysis_id = analysis_id or self.current_analysis_id
        if analysis_id is None:
            self.message.emit("There is no analysis to edit.")
            return False
        from curvis.ui.analyze_dialog import AnalyzeDialog

        analysis = self.document.analysis(analysis_id)
        dialog = AnalyzeDialog(
            self.document,
            analysis.table_id,
            analysis=analysis,
            recent=self._recent_analyses(),
            parent=self,
        )
        return self._run_analyze_dialog(dialog, editing=True)

    def delete_analysis(self, analysis_id: str | None = None) -> bool:
        """Delete ``analysis_id`` (default: the one shown), as an undoable step."""
        from curvis.core import remove_analysis

        analysis_id = analysis_id or self.current_analysis_id
        if analysis_id is None:
            return False
        try:
            self.document.execute(remove_analysis(self.document, analysis_id))
        except CommandError as exc:
            self.message.emit(str(exc))
            return False
        return True

    def copy(self) -> None:
        """Copy the selection of the Results panel when it has the focus, else of the table."""
        if self.results_panel.has_focus_inside():
            self.results_panel.copy_selection()
        elif self.table_view is not None:
            self.table_view.copy_selection()

    def _run_analyze_dialog(self, dialog: AnalyzeDialog, *, editing: bool) -> bool:
        from curvis.core import add_analysis, edit_analysis

        try:
            if self._exec_dialog(dialog) != QDialog.DialogCode.Accepted:
                return False
            analysis = dialog.analysis()
            try:
                if editing:
                    command = edit_analysis(self.document, analysis)
                else:
                    command = self._with_overlays(add_analysis(self.document, analysis), analysis)
                self.document.execute(command)
            except CommandError as exc:
                self.message.emit(str(exc))
                return False
            self.results_panel.set_current(analysis.id)
            self.show_results()
            self.analysis_used.emit(analysis.type)
            return True
        finally:
            dialog.deleteLater()

    def _with_overlays(self, command: Command, analysis: Analysis) -> Command:
        """``command`` (adding ``analysis``) together with laying a new fit's curves over
        the XY graphs of its table, as one undo step."""
        from curvis.charts.builder import is_fit, with_fit
        from curvis.core import CommandGroup, edit_graph

        if not is_fit(analysis):
            return command
        graphs = [
            g for g in self.document.graphs_of(analysis.table_id) if g.family is ChartFamily.XY
        ]
        if not graphs:
            return command
        overlays = [edit_graph(self.document, with_fit(g, analysis), "Overlay fit") for g in graphs]
        return CommandGroup(command.summary, [command, *overlays])

    # --- graphs ----------------------------------------------------------------------------

    @property
    def current_graph_id(self) -> str | None:
        """The graph shown in the Graph panel."""
        return self.graph_panel.current_id

    @property
    def showing_graph(self) -> bool:
        return self.side_tabs.currentIndex() == GRAPH_TAB

    def plot(self, family: ChartFamily) -> bool:
        """Make a new graph of the shown table in ``family`` and show it."""
        if self.table_view is None:
            return False
        from curvis.charts.builder import default_spec, family_problem
        from curvis.core import add_graph

        table = self.document.table(self.table_view.model().table_id)
        problem = family_problem(table, family)
        if problem is not None:
            self.message.emit(f"{problem}.")
            return False
        spec = default_spec(table, family, self.document.analyses)
        try:
            self.document.execute(add_graph(self.document, spec))
        except CommandError as exc:
            self.message.emit(str(exc))
            return False
        self.show_graph()
        self.graph_panel.set_current(spec.id)
        return True

    def delete_graph(self, graph_id: str | None = None) -> bool:
        """Delete ``graph_id`` (default: the one shown), as an undoable step."""
        from curvis.core import remove_graph

        graph_id = graph_id or self.current_graph_id
        if graph_id is None:
            return False
        try:
            self.document.execute(remove_graph(self.document, graph_id))
        except CommandError as exc:
            self.message.emit(str(exc))
            return False
        return True

    def show_results(self) -> None:
        self.side_tabs.setCurrentIndex(RESULTS_TAB)

    def show_graph(self) -> None:
        self.side_tabs.setCurrentIndex(GRAPH_TAB)

    def _toggle_series(self, graph_id: str, source: str, visible: bool) -> None:
        from curvis.core import set_series_visible

        try:
            self.document.execute(set_series_visible(self.document, graph_id, source, visible))
        except CommandError as exc:
            self.message.emit(str(exc))

    def _exec_dialog(self, dialog: QDialog) -> int:
        """Show ``dialog`` modally (tests replace this)."""
        return dialog.exec()

    def _on_values_changed(self, table_id: str) -> None:
        if self.table_view is not None and self.table_view.model().table_id == table_id:
            self.table_view.model().refresh_values()

    # --- table view ------------------------------------------------------------------------

    def _shown_table_id(self) -> str | None:
        return self.table_view.model().table_id if self.table_view is not None else None

    def _on_undo_event(self, _event: UndoEvent) -> None:
        first = self.document.tables[0].id if self.document.tables else None
        if first != self._shown_table_id():
            self._show_first_table()

    def _show_first_table(self) -> None:
        if self.table_view is not None:
            self.table_view.model().detach()
            self.table_view.setParent(None)
            self.table_view.deleteLater()
            self.table_view = None
        if self.document.tables:
            model = TableModel(
                self.document, self.document.tables[0].id, decimal_places=self._decimal_places
            )
            if self.engine is not None:
                model.set_calculation_errors(self.engine.errors.get)
            self.table_view = TableView(model, self)
            model.setParent(self.table_view)
            self.table_view.error.connect(self.message)
            self.table_view.calculate_requested.connect(self.calculate)
            self.table_view.edit_calculation_requested.connect(self.edit_calculation)
            self.table_view.selection_changed.connect(self.state_changed)
            self.splitter.insertWidget(0, self.table_view)
            self.splitter.setStretchFactor(0, 1)
            self.splitter.setStretchFactor(1, 0)
            total = max(self.width(), 2 * RESULTS_PANEL_WIDTH)
            self.splitter.setSizes([total - RESULTS_PANEL_WIDTH, RESULTS_PANEL_WIDTH])
        self.placeholder.setVisible(self.table_view is None)
        self.splitter.setVisible(self.table_view is not None)
        self.results_panel.set_table(self._shown_table_id())
        self.graph_panel.set_table(self._shown_table_id())
