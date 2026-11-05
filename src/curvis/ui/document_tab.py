"""One open document: its model, file path and the widget shown in its tab (the selected
table, with the Results and Graph panels to its right, one at a time, and the table
tabs along the bottom)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QLabel, QSplitter, QTabWidget, QVBoxLayout, QWidget

from curvis.charts.spec import ChartFamily
from curvis.core import (
    LAST_TABLE,
    AddTable,
    Command,
    CommandError,
    CommandGroup,
    Document,
    MoveTable,
    RemoveTable,
    RenameTable,
    UndoEvent,
    new_table_name,
)
from curvis.core.constants import ProjectConstant
from curvis.data import Table
from curvis.settings.store import DECIMAL_PLACES_DEFAULT
from curvis.ui.graph_panel import GraphPanel
from curvis.ui.results_panel import ResultsPanel
from curvis.ui.table import TableModel, TableView
from curvis.ui.table_tabs import TableTabs

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
    """The tab of one document: the view of its selected table, or a placeholder.

    The table tabs along the bottom select the table. The table view, the Results and
    the Graph panel and every table action follow the selected table. Commands keep the
    selection; when the selected table is removed its neighbour is selected, and a table
    that is added (or brought back by undo) is selected.

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
        self._layout.setSpacing(0)
        self.table_tabs = TableTabs(self)
        self.table_tabs.current_changed.connect(self.select_table)
        self.table_tabs.new_requested.connect(self.new_table)
        self.table_tabs.rename_requested.connect(self.rename_table)
        self.table_tabs.close_requested.connect(self.close_table)
        self.table_tabs.move_requested.connect(self.move_table)
        self._layout.addWidget(self.placeholder, 1)
        self._layout.addWidget(self.splitter, 1)
        self._layout.addWidget(self.table_tabs, 0)
        self._table_id: str | None = None
        self._table_index = 0
        self._side_width = RESULTS_PANEL_WIDTH
        self._show_table(document.tables[0].id if document.tables else None)
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

    @property
    def current_table_id(self) -> str | None:
        """The selected table."""
        return self._table_id

    @property
    def current_table(self) -> Table | None:
        return self.document.table(self._table_id) if self._table_id is not None else None

    def _shown_table_id(self) -> str | None:
        return self.table_view.model().table_id if self.table_view is not None else None

    def select_table(self, table_id: str) -> None:
        """Show the table ``table_id``."""
        if table_id != self._table_id:
            self._show_table(table_id)

    def select_next_table(self, step: int = 1) -> None:
        """Select the next (``step`` 1) or previous (-1) table; stops at the ends."""
        tables = self.document.tables
        if self._table_id is None or not tables:
            return
        index = self.document.table_index(self._table_id) + step
        if 0 <= index < len(tables):
            self.select_table(tables[index].id)

    def new_table(self) -> bool:
        """Add a blank table after the selected one and select it (one undo step)."""
        index = (
            self.document.table_index(self._table_id) + 1
            if self._table_id is not None
            else len(self.document.tables)
        )
        table = Table.blank(new_table_name(self.document))
        return self._execute(AddTable(table, index))

    def close_table(self, table_id: str | None = None) -> bool:
        """Delete ``table_id`` (default: the selected table), with its calculated
        columns, analyses and graphs, after the warning; an empty table goes without
        asking. The last table cannot be closed."""
        table_id = table_id or self._table_id
        if table_id is None:
            return False
        if len(self.document.tables) <= 1:
            self.message.emit(LAST_TABLE)
            return False
        table = self.document.table(table_id)
        analyses = self.document.analyses_of(table_id)
        graphs = self.document.graphs_of(table_id)
        if not (is_empty_table(table) and not analyses and not graphs):
            from curvis.ui.close_table_dialog import CloseTableDialog

            dialog = CloseTableDialog(table.name, analyses, graphs, self)
            try:
                if self._exec_dialog(dialog) != QDialog.DialogCode.Accepted:
                    return False
            finally:
                dialog.deleteLater()
        return self._execute(RemoveTable(table_id))

    def rename_table(self, table_id: str | None = None) -> bool:
        """Edit the name of ``table_id`` (default: the selected table) in its tab."""
        table_id = table_id or self._table_id
        if table_id is None:
            return False

        def commit(text: str) -> bool:
            name = text.strip()
            table = self.document.table(table_id)
            if not name:
                self.message.emit("Enter a table name. The old name was kept.")
            elif name != table.name:
                other = self.document.table_by_name(name)
                if other is not None and other.id != table_id:
                    self.message.emit(
                        f"'{other.name}' is already used in this project. The old name was kept."
                    )
                else:
                    self._execute(RenameTable(table_id, name))
            return True

        return self.table_tabs.edit_name(table_id, commit, self.table_view) is not None

    def move_table(self, table_id: str, index: int) -> bool:
        """Move ``table_id`` to position ``index`` (a dragged tab)."""
        ok = self._execute(MoveTable(table_id, index))
        if not ok:
            self._sync_tabs()
        return ok

    def _execute(self, command: Command) -> bool:
        try:
            self.document.execute(command)
        except CommandError as exc:
            self.message.emit(str(exc))
            return False
        return True

    def _on_undo_event(self, event: UndoEvent) -> None:
        ids = [t.id for t in self.document.tables]
        wanted = _table_to_select(event)
        if wanted is None or wanted not in ids:
            wanted = self._table_id
        if wanted not in ids:
            # the selected table is gone: select its neighbour
            wanted = ids[min(self._table_index, len(ids) - 1)] if ids else None
        if wanted != self._shown_table_id():
            self._show_table(wanted)
        else:
            self._sync_tabs()

    def _sync_tabs(self) -> None:
        tables = self.document.tables
        if self._table_id is not None:
            self._table_index = self.document.table_index(self._table_id)
        self.table_tabs.set_tables([(t.id, t.name) for t in tables], self._table_id)

    def _show_table(self, table_id: str | None) -> None:
        if self.table_view is not None:
            sizes = self.splitter.sizes()
            if len(sizes) == 2 and sizes[1] > 0:
                self._side_width = sizes[1]  # the side panel keeps its width
            self.table_view.model().detach()
            self.table_view.setParent(None)
            self.table_view.deleteLater()
            self.table_view = None
        self._table_id = table_id
        if table_id is not None:
            model = TableModel(self.document, table_id, decimal_places=self._decimal_places)
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
            total = max(self.width(), 2 * self._side_width)
            self.splitter.setSizes([total - self._side_width, self._side_width])
        self.placeholder.setVisible(self.table_view is None)
        self.splitter.setVisible(self.table_view is not None)
        self.table_tabs.setVisible(self.table_view is not None)
        self._sync_tabs()
        self.results_panel.set_table(table_id)
        self.graph_panel.set_table(table_id)
        self.state_changed.emit()


def is_empty_table(table: Table) -> bool:
    """True for a table without values in any column (it closes without asking)."""
    import numpy as np

    for column in table.columns:
        if column.role.is_numeric:
            if not bool(np.isnan(column.values).all()):
                return False
        elif any(v not in (None, "") for v in column.values):
            return False
    return True


def _table_to_select(event: UndoEvent) -> str | None:
    """The table a command adds, or that undo brings back: it becomes the selected one."""
    command = event.command
    if command is None:
        return None
    commands = command.commands if isinstance(command, CommandGroup) else [command]
    for c in commands:
        if isinstance(c, AddTable) and event.kind in ("do", "redo"):
            return c.table.id
        if isinstance(c, RemoveTable) and event.kind == "undo":
            return c.table_id
    return None
