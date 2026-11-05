"""One open document: its model, file path and the widget shown in its tab (the selected
table, with the Results and Graph panels to its right, one at a time, and the table
tabs along the bottom)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QLabel, QSplitter, QTabWidget, QVBoxLayout, QWidget

from curvis.charts.spec import ChartFamily, ChartSpec, ChartVariant
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
    from curvis.charts.render_mpl.export import ExportFormat
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
    that is added (or brought back by undo) is selected. Undoing an added table goes back
    to the table that was selected before it.

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
        self.scheduler = scheduler
        self._pending_exports: dict[str, Path] = {}
        self._export_settings: tuple[str, int] = ("png", 300)
        self._export_folder: Path | None = path.parent if path is not None else None
        if scheduler is not None:
            scheduler.finished.connect(self._on_job_finished)
            scheduler.failed.connect(self._on_job_failed)
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
        self.results_panel.delete_requested.connect(self.delete_analysis)
        self.results_panel.current_changed.connect(self.state_changed)
        self.graph_panel = GraphPanel(document, self.graph_engine, self.side_tabs)
        self.graph_panel.current_changed.connect(self.state_changed)
        self.graph_panel.toggle_requested.connect(self._toggle_series)
        self.graph_panel.format_requested.connect(self.format_graph)
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
        # command that added a table -> the table selected before it (for its undo)
        self._selected_before: WeakKeyDictionary[Command, str | None] = WeakKeyDictionary()
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
        if self.scheduler is not None:
            self.scheduler.finished.disconnect(self._on_job_finished)
            self.scheduler.failed.disconnect(self._on_job_failed)
            self.scheduler = None
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
        """Delete ``analysis_id`` (default: the one shown) after a warning, as an
        undoable step. Its fitted curves leave the graphs that show them."""
        from curvis.core import overlaid_on, remove_analysis
        from curvis.ui.close_table_dialog import DeleteDialog

        analysis_id = analysis_id or self.current_analysis_id
        if analysis_id is None:
            return False
        try:
            analysis = self.document.analysis(analysis_id)
        except KeyError:
            return False
        try:
            name = analysis.analysis_type.name
        except KeyError:
            name = analysis.type
        graphs = len(overlaid_on(self.document, analysis_id))
        items = (
            [f"Its fitted curves on {graphs} graph{'s' if graphs > 1 else ''}"] if graphs else []
        )
        dialog = DeleteDialog(
            "Delete analysis",
            f"Deleting “{name}” removes the analysis and its results from the project.",
            "Delete analysis",
            items,
            self,
        )
        if not self._confirm(dialog):
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
        """Delete ``graph_id`` (default: the one shown) after a warning, as an undoable
        step."""
        from curvis.core import remove_graph
        from curvis.ui.close_table_dialog import DeleteDialog

        graph_id = graph_id or self.current_graph_id
        if graph_id is None:
            return False
        name = self.graph_panel.name_of(graph_id)
        dialog = DeleteDialog(
            "Delete graph",
            f"Deleting “{name}” removes the graph from the project.",
            "Delete graph",
            parent=self,
        )
        if not self._confirm(dialog):
            return False
        try:
            self.document.execute(remove_graph(self.document, graph_id))
        except CommandError as exc:
            self.message.emit(str(exc))
            return False
        return True

    def change_graph_type(self, variant: ChartVariant, graph_id: str | None = None) -> bool:
        """Draw the graph ``graph_id`` (default: the one shown) as ``variant``, keeping its
        series and style; one undo step."""
        from curvis.charts.builder import with_variant
        from curvis.core import edit_graph

        graph_id = graph_id or self.current_graph_id
        if graph_id is None:
            return False
        spec = self.document.graph(graph_id)
        if spec.variant is variant:
            return False
        try:
            changed = with_variant(spec, variant, self.document.table(spec.table_id))
            summary = f"Change graph type to {variant.label.lower()}"
            self.document.execute(edit_graph(self.document, changed, summary))
        except (ValueError, CommandError) as exc:
            self.message.emit(str(exc))
            return False
        return True

    def format_graph(self, graph_id: str | None = None) -> bool:
        """Open the Format dialog for ``graph_id`` (default: the one shown); Apply stores
        the changes as one undo step."""
        from curvis.core import edit_graph
        from curvis.ui.format_dialog import FormatDialog

        graph_id = graph_id or self.current_graph_id
        if graph_id is None:
            return False
        engine = self.graph_engine
        dialog = FormatDialog(
            self.document,
            graph_id,
            scheduler=self.scheduler,
            results=self.analysis_engine.results if self.analysis_engine is not None else {},
            data=engine.data.get(graph_id) if engine is not None else None,
            parent=self,
        )
        try:
            if not self._exec_dialog(dialog) or not dialog.changed:
                return False
            try:
                self.document.execute(edit_graph(self.document, dialog.spec, "Format graph"))
            except CommandError as exc:
                self.message.emit(str(exc))
                return False
            return True
        finally:
            dialog.deleteLater()

    def export_graph(self, graph_id: str | None = None) -> bool:
        """Ask for a format and a file, then export ``graph_id`` (default: the one shown)
        in a worker process."""
        from curvis.charts.render_mpl.export import ExportFormat
        from curvis.ui.export_dialog import ExportDialog

        graph_id = graph_id or self.current_graph_id
        if graph_id is None:
            return False
        spec = self.document.graph(graph_id)
        fmt, dpi = self._export_settings
        dialog = ExportDialog(spec, fmt=ExportFormat(fmt), dpi=dpi, parent=self)
        try:
            if not self._exec_dialog(dialog):
                return False
            chosen, dpi = dialog.export_format, dialog.dpi.value()
        finally:
            dialog.deleteLater()
        self._export_settings = (chosen.value, dpi)
        path = self._ask_export_path(spec, chosen)
        if path is None:
            return False
        self._export_folder = path.parent
        return self.start_export(graph_id, path, chosen, dpi)

    def start_export(self, graph_id: str, path: Path, fmt: ExportFormat, dpi: int) -> bool:
        """Write the graph ``graph_id`` to ``path``, off the UI thread when there is a
        scheduler. The status bar says when it is done."""
        from curvis.charts.data import compute, gather
        from curvis.charts.render_mpl.export import export_graph

        spec = self.document.graph(graph_id)
        engine = self.graph_engine
        data = engine.data.get(graph_id) if engine is not None else None
        if data is None or (engine is not None and engine.is_stale(graph_id)):
            results = self.analysis_engine.results if self.analysis_engine is not None else {}
            data = compute(gather(self.document.table(spec.table_id), spec, results))
        if self.scheduler is None:
            try:
                export_graph(spec, data, str(path), fmt, dpi)
            except (OSError, ValueError) as exc:
                self.message.emit(f"Could not export {path.name}: {exc}")
                return False
            self.message.emit(f"Exported {path.name}.")
            return True
        from curvis.compute import Job

        node = f"export:{path}"
        self._pending_exports[node] = path
        self.message.emit(f"Exporting {path.name}…")
        job = Job(export_graph, (spec, data, str(path), fmt.value, dpi))
        self.scheduler.submit(node, job, weight="heavy", delay_ms=0)
        return True

    def _ask_export_path(self, spec: ChartSpec, fmt: ExportFormat) -> Path | None:
        """Ask where to save the export (tests replace this)."""
        from PySide6.QtWidgets import QFileDialog

        name = _file_name(spec.title or self.document.table(spec.table_id).name)
        folder = self._export_folder or Path.home()
        start = str(folder / f"{name}.{fmt.value}")
        filters = f"{fmt.label} (*.{fmt.value})"
        chosen, _filter = QFileDialog.getSaveFileName(self, "Export graph", start, filters)
        if not chosen:
            return None
        path = Path(chosen)
        return (
            path
            if path.suffix.lower() == f".{fmt.value}"
            else path.with_name(f"{path.name}.{fmt.value}")
        )

    def _on_job_finished(self, node: str, _result: object) -> None:
        path = self._pending_exports.pop(node, None)
        if path is not None:
            self.message.emit(f"Exported {path.name}.")

    def _on_job_failed(self, node: str, error: object) -> None:
        path = self._pending_exports.pop(node, None)
        if path is not None:
            reason = getattr(error, "message", str(error))
            self.message.emit(f"Could not export {path.name}: {reason}")

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

    def _confirm(self, dialog: QDialog) -> bool:
        """Show the warning ``dialog``; True if the user chose to go on."""
        try:
            return bool(self._exec_dialog(dialog) == QDialog.DialogCode.Accepted)
        finally:
            dialog.deleteLater()

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

    def move_selected_table(self, step: int = 1) -> bool:
        """Move the selected table one place right (``step`` 1) or left (-1)."""
        if self._table_id is None:
            return False
        index = self.document.table_index(self._table_id) + step
        if not 0 <= index < len(self.document.tables):
            return False
        return self.move_table(self._table_id, index)

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
        command = event.command
        wanted = _table_to_select(event)
        if command is not None and event.kind in ("do", "redo") and wanted is not None:
            # a table was added: undo goes back to the table selected before it
            self._selected_before[command] = self._table_id
        elif command is not None and event.kind == "undo":
            wanted = wanted or self._selected_before.get(command)
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


def _file_name(name: str) -> str:
    """``name`` without the characters a file name cannot hold."""
    cleaned = "".join("_" if c in '<>:"/\\|?*' or ord(c) < 32 else c for c in name).strip(" .")
    return cleaned or "Graph"
