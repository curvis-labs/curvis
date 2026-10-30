"""The main window: the launcher, one tab per open document, menus and the command palette."""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStackedWidget,
    QTabWidget,
    QWidget,
)

from curvis.core import Document, UndoEvent
from curvis.data import ColumnRole, Table
from curvis.settings import Settings
from curvis.ui.actions import ActionRegistry
from curvis.ui.document_tab import DocumentTab
from curvis.ui.launcher import LauncherWidget
from curvis.ui.table import TableView

if TYPE_CHECKING:
    from curvis.compute import JobError, Scheduler
    from curvis.core import Command
    from curvis.io.importers import CsvOptions

PROJECT_SUFFIX = ".cvp"
PROJECT_FILTER = "Curvis projects (*.cvp)"
IMPORT_FILTER = "Text data (*.csv *.tsv *.txt);;All files (*)"

STATUS_MESSAGE_MS = 6000
"""How long a note such as a refused edit stays in the status bar."""

TABLE_ACTIONS = (
    "edit.copy",
    "edit.paste",
    "edit.clear",
    "table.insert_column_left",
    "table.insert_column_right",
    "table.rename_column",
    "table.delete_columns",
    *(f"table.role.{role.name.lower()}" for role in ColumnRole),
    "table.group",
    "table.ungroup",
    "table.set_category",
    "table.remove_category",
    "table.rename_category_factor",
    "table.bulk_rename",
    "table.move_left",
    "table.move_right",
    "table.calculate",
    "table.edit_calculation",
)
"""Actions that need a table view in the current tab."""


FORMULA_WARM_UP = "formula:warm-up"
"""Scheduler node that builds the formula parser after start-up. Its result is ignored;
if it fails, the first formula builds the parser instead and reports the problem."""

FORMULA_WARM_UP_DELAY_MS = 1000
"""Delay after the window is shown, so the warm-up does not compete with start-up."""


class SaveChoice(Enum):
    """Answer to "Save changes to 'X'?"."""

    SAVE = auto()
    DISCARD = auto()
    CANCEL = auto()


def _file_key(path: Path) -> str:
    """Key that treats two spellings of the same file as equal."""
    return os.path.normcase(str(path.resolve(strict=False)))


class MainWindow(QMainWindow):
    """Top-level window with one tab per open document."""

    def __init__(self, settings: Settings | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Curvis")
        self.resize(1200, 800)
        self.settings = settings if settings is not None else Settings()
        self.registry = ActionRegistry(self)
        self._scheduler: Scheduler | None = None
        self._pending_opens: dict[str, Path] = {}  # scheduler node -> file
        # scheduler node -> (file, tab to import into; None for a new project)
        self._pending_imports: dict[str, tuple[Path, DocumentTab | None]] = {}
        self._untitled_count = 0
        self._table_actions_created = False

        self.launcher = LauncherWidget(self)
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("documentTabs")
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.launcher)
        self.stack.addWidget(self.tabs)
        self.setCentralWidget(self.stack)

        self._create_actions()
        self._create_menus()

        self.launcher.new_requested.connect(self.new_document)
        self.launcher.open_requested.connect(self.open_document)
        self.launcher.recent_chosen.connect(self.open_path)
        self.tabs.currentChanged.connect(self._on_current_tab_changed)
        self.tabs.tabCloseRequested.connect(self._on_tab_close_requested)
        self.settings.changed.connect(self._on_setting_changed)

        self._refresh_recent()
        self._update_state()

    # --- actions and menus ----------------------------------------------------------

    def _create_actions(self) -> None:
        r = self.registry
        k = QKeySequence.StandardKey
        r.register("file.new", "&New project", self.new_document, category="File", shortcut=k.New)
        r.register("file.open", "&Open…", self.open_document, category="File", shortcut=k.Open)
        r.register(
            "file.import",
            "&Import data…",
            self.import_data,
            category="File",
            shortcut="Ctrl+Shift+I",
        )
        r.register("file.save", "&Save", self.save_current, category="File", shortcut=k.Save)
        r.register(
            "file.save_as",
            "Save &as…",
            self.save_current_as,
            category="File",
            shortcut="Ctrl+Shift+S",
        )
        r.register("file.close", "&Close", self.close_current, category="File", shortcut="Ctrl+W")
        r.register(
            "file.quit",
            "&Quit",
            self.close,
            category="File",
            shortcut="Ctrl+Q",
            menu_role=QAction.MenuRole.QuitRole,
        )
        r.register("edit.undo", "&Undo", self.undo, category="Edit", shortcut=k.Undo)
        r.register("edit.redo", "&Redo", self.redo, category="Edit", shortcut=k.Redo)
        r.register(
            "edit.settings",
            "Se&ttings…",
            self.show_settings,
            category="Edit",
            shortcut="Ctrl+,",
            menu_role=QAction.MenuRole.PreferencesRole,
        )
        r.register(
            "view.command_palette",
            "&Command palette…",
            self.show_command_palette,
            category="View",
            shortcut="Ctrl+K",
        )

    def _create_menus(self) -> None:
        a = self.registry.action
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addActions([a("file.new"), a("file.open"), a("file.import")])
        file_menu.addSeparator()
        file_menu.addActions([a("file.save"), a("file.save_as")])
        file_menu.addSeparator()
        file_menu.addActions([a("file.close"), a("file.quit")])
        edit_menu = self.menuBar().addMenu("&Edit")
        edit_menu.addActions([a("edit.undo"), a("edit.redo")])
        edit_menu.addSeparator()
        self._edit_menu = edit_menu
        self._settings_separator = edit_menu.addSeparator()
        edit_menu.addAction(a("edit.settings"))
        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(a("view.command_palette"))
        self._view_menu = view_menu

    def _create_table_actions(self) -> None:
        """Actions and menus for tables, made when the first document opens.

        The launcher does not need them, so the window shell stays quick to build.
        """
        if self._table_actions_created:
            return
        self._table_actions_created = True
        r = self.registry
        k = QKeySequence.StandardKey
        r.register(
            "edit.copy",
            "&Copy",
            self._table_call(TableView.copy_selection),
            category="Edit",
            shortcut=k.Copy,
        )
        r.register(
            "edit.paste",
            "&Paste",
            self._table_call(TableView.paste),
            category="Edit",
            shortcut=k.Paste,
        )
        r.register(
            "edit.clear",
            "C&lear cells",
            self._table_call(TableView.clear_selected_cells),
            category="Edit",
            shortcut=k.Delete,
        )
        r.register(
            "table.insert_column_left",
            "Insert column &left",
            self._table_call(TableView.insert_column_left),
            category="Table",
        )
        r.register(
            "table.insert_column_right",
            "Insert column &right",
            self._table_call(TableView.insert_column_right),
            category="Table",
            shortcut="Ctrl+Shift+=",
        )
        r.register(
            "table.rename_column",
            "Re&name column…",
            self._table_call(TableView.rename_current_column),
            category="Table",
            shortcut="Ctrl+Shift+R",
        )
        r.register(
            "table.delete_columns",
            "&Delete column",
            self._table_call(TableView.delete_selected_columns),
            category="Table",
            shortcut="Ctrl+-",
        )
        for role in ColumnRole:
            r.register(
                f"table.role.{role.name.lower()}",
                f"Set column role to {role.value}",
                self._table_call(TableView.set_role_of_selected, role),
                category="Table",
            )
        r.register(
            "table.group",
            "&Group as replicates…",
            self._table_call(TableView.group_selected),
            category="Table",
            shortcut="Ctrl+G",
        )
        r.register(
            "table.ungroup",
            "&Ungroup",
            self._table_call(TableView.ungroup_selected),
            category="Table",
            shortcut="Ctrl+Shift+G",
        )
        r.register(
            "table.set_category",
            "Set &category…",
            self._table_call(TableView.set_category_of_selected),
            category="Table",
        )
        r.register(
            "table.remove_category",
            "Remove from categor&y",
            self._table_call(TableView.remove_category_of_selected),
            category="Table",
        )
        r.register(
            "table.rename_category_factor",
            "Rename category &factor…",
            self._table_call(TableView.rename_category_factor),
            category="Table",
        )
        r.register(
            "table.move_left",
            "Move column or group &left",
            self._table_call(TableView.move_selected_left),
            category="Table",
            shortcut="Alt+Shift+Left",
        )
        r.register(
            "table.move_right",
            "Move column or group righ&t",
            self._table_call(TableView.move_selected_right),
            category="Table",
            shortcut="Alt+Shift+Right",
        )
        r.register(
            "table.bulk_rename",
            "&Bulk rename…",
            self._table_call(TableView.bulk_rename),
            category="Table",
        )
        r.register(
            "table.calculate",
            "Ca&lculate…",
            self._tab_call(DocumentTab.calculate),
            category="Table",
            shortcut="Ctrl+Shift+K",
        )
        r.register(
            "table.edit_calculation",
            "&Edit calculation…",
            self._tab_call(DocumentTab.edit_calculation),
            category="Table",
            shortcut="Ctrl+Shift+E",
        )
        r.register(
            "table.constants",
            "C&onstants…",
            self.show_constants,
            category="Table",
        )
        a = self.registry.action
        self._edit_menu.insertActions(
            self._settings_separator, [a("edit.copy"), a("edit.paste"), a("edit.clear")]
        )
        table_menu = QMenu("&Table", self)
        self.menuBar().insertMenu(self._view_menu.menuAction(), table_menu)
        table_menu.addActions(
            [
                a("table.insert_column_left"),
                a("table.insert_column_right"),
                a("table.rename_column"),
                a("table.delete_columns"),
            ]
        )
        role_menu = table_menu.addMenu("Column &role")
        for role in ColumnRole:
            role_menu.addAction(a(f"table.role.{role.name.lower()}"))
        table_menu.addSeparator()
        table_menu.addActions(
            [
                a("table.group"),
                a("table.ungroup"),
                a("table.set_category"),
                a("table.remove_category"),
                a("table.rename_category_factor"),
            ]
        )
        table_menu.addSeparator()
        table_menu.addActions([a("table.move_left"), a("table.move_right")])
        table_menu.addAction(a("table.bulk_rename"))
        table_menu.addSeparator()
        table_menu.addActions(
            [a("table.calculate"), a("table.edit_calculation"), a("table.constants")]
        )

    # --- documents --------------------------------------------------------------------

    @property
    def current_tab(self) -> DocumentTab | None:
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, DocumentTab) else None

    def document_tabs(self) -> list[DocumentTab]:
        tabs = (self.tabs.widget(i) for i in range(self.tabs.count()))
        return [t for t in tabs if isinstance(t, DocumentTab)]

    def new_document(self) -> DocumentTab:
        """Open a blank project in a new tab."""
        self._untitled_count += 1
        name = "Untitled" if self._untitled_count == 1 else f"Untitled {self._untitled_count}"
        return self._add_tab(Document(name=name, tables=[Table.blank()]), None)

    def open_document(self) -> None:
        """Ask for a project file and open it."""
        path = self._ask_open_path()
        if path is not None:
            self.open_path(path)

    def open_path(self, path: Path) -> None:
        """Open the project at ``path`` in a new tab, or switch to it if it is open already."""
        path = Path(path)
        for tab in self.document_tabs():
            if tab.path is not None and _file_key(tab.path) == _file_key(path):
                self.tabs.setCurrentWidget(tab)
                return
        if not path.is_file():
            self._show_error("Cannot open project", f"'{path}' does not exist.")
            self.settings.remove_recent_project(path)
            return
        from curvis.compute import Job

        node = f"open:{_file_key(path)}"
        self._pending_opens[node] = path
        self.scheduler.submit(node, Job(_load_project, (str(path),)), delay_ms=0)

    @property
    def scheduler(self) -> Scheduler:
        """The job scheduler, started on first use so it does not slow down start-up."""
        if self._scheduler is None:
            from curvis.compute import Scheduler

            self._scheduler = Scheduler(parent=self)
            self._scheduler.finished.connect(self._on_job_finished)
            self._scheduler.failed.connect(self._on_job_failed)
        return self._scheduler

    def warm_up_formulas(self) -> None:
        """Build the formula parser on a worker thread, ahead of the first formula."""
        from curvis.compute import Job
        from curvis.formula import warm_up

        self.scheduler.submit(FORMULA_WARM_UP, Job(warm_up), delay_ms=0)

    def _on_job_finished(self, node: str, result: object) -> None:
        if node in self._pending_imports:
            self._finish_import(*self._pending_imports.pop(node), result)
            return
        path = self._pending_opens.pop(node, None)
        if path is None:
            return
        assert isinstance(result, Document)
        self._add_tab(result, path)
        self.settings.add_recent_project(path)

    def _on_job_failed(self, node: str, error: JobError) -> None:
        if node in self._pending_imports:
            source, _tab = self._pending_imports.pop(node)
            self._show_error(
                "Cannot import data", f"'{source.name}' could not be imported.\n\n{error.message}"
            )
            return
        path = self._pending_opens.pop(node, None)
        if path is None:
            return
        self._show_error(
            "Cannot open project", f"'{path.name}' could not be opened.\n\n{error.message}"
        )

    def _add_tab(self, document: Document, path: Path | None) -> DocumentTab:
        self._create_table_actions()
        tab = DocumentTab(
            document,
            path,
            self.tabs,
            decimal_places=self.settings.decimal_places,
            scheduler=self.scheduler,
            global_constants=lambda: self.settings.global_constants,
        )
        tab.message.connect(self._show_status)
        tab.manage_constants_requested.connect(self.show_constants)
        index = self.tabs.addTab(tab, tab.title)
        self.tabs.setTabToolTip(index, str(path) if path else tab.title)
        document.undo_stack.subscribe(functools.partial(self._on_undo_event, tab))
        self.tabs.setCurrentIndex(index)
        self.stack.setCurrentWidget(self.tabs)
        self._update_state()
        return tab

    # --- import ---------------------------------------------------------------------

    def import_data(self) -> None:
        """Ask for a CSV or TSV file, show the Import dialog, then import the file."""
        path = self._ask_import_path()
        if path is None:
            return
        from curvis.ui.import_dialog import ImportDialog

        self.scheduler.start_process_pool()  # warm up the workers while the dialog is open
        dialog = ImportDialog(path, self.scheduler, self)
        try:
            if self._exec_import_dialog(dialog):
                self.start_import(path, dialog.options())
        finally:
            dialog.deleteLater()

    def start_import(self, path: Path, options: CsvOptions) -> None:
        """Read ``path`` in a worker process, then put it into the current table.

        The columns fill the table's empty columns first and are added at the end
        otherwise, as one undo step. Without an open project, a new one is made.
        """
        from curvis.compute import Job
        from curvis.io.importers import read_csv

        node = f"import:{_file_key(path)}"
        self._pending_imports[node] = (path, self.current_tab)
        self._show_status(f"Importing {path.name}…")
        job = Job(read_csv, (str(path), options))
        self.scheduler.submit(node, job, weight="heavy", delay_ms=0)

    def _finish_import(self, path: Path, tab: DocumentTab | None, result: object) -> None:
        from curvis.core import AddTable, CommandError, CommandGroup, ImportColumns
        from curvis.io.importers import CsvData

        assert isinstance(result, CsvData)
        if tab is None or tab not in self.document_tabs():
            tab = self.new_document()
        document = tab.document
        source = f"'{path.name}'"
        command: Command
        if tab.table_view is not None:
            command = ImportColumns(tab.table_view.model().table_id, result.columns, source)
        else:
            table = Table.blank()
            command = CommandGroup(
                f"Import {source}",
                [AddTable(table), ImportColumns(table.id, result.columns, source)],
            )
        try:
            document.execute(command)
        except CommandError as exc:
            self._show_error("Cannot import data", f"'{path.name}' could not be imported.\n\n{exc}")
            return
        self.tabs.setCurrentWidget(tab)
        rows = f"{result.n_rows:,} row{'s' if result.n_rows != 1 else ''}"
        columns = len(result.columns)
        self._show_status(
            f"Imported {rows} and {columns} column{'s' if columns != 1 else ''} from {path.name}."
        )

    def save_current(self) -> bool:
        tab = self.current_tab
        return self.save_tab(tab) if tab is not None else False

    def save_current_as(self) -> bool:
        tab = self.current_tab
        return self.save_tab_as(tab) if tab is not None else False

    def save_tab(self, tab: DocumentTab) -> bool:
        """Save ``tab`` to its file (asking for one if it has none). True if saved."""
        if tab.path is None:
            return self.save_tab_as(tab)
        return self._write(tab, tab.path)

    def save_tab_as(self, tab: DocumentTab) -> bool:
        path = self._ask_save_path(tab)
        if path is None:
            return False
        if path.suffix.lower() != PROJECT_SUFFIX:
            path = path.with_name(path.name + PROJECT_SUFFIX)
        return self._write(tab, path)

    def _write(self, tab: DocumentTab, path: Path) -> bool:
        # Saved on the main thread: the document must not change while it is written.
        from curvis.io import project

        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            project.save(tab.document, path)
        except OSError as exc:
            error = exc.strerror or str(exc)
        else:
            error = ""
        finally:
            QGuiApplication.restoreOverrideCursor()
        if error:
            self._show_error("Cannot save project", f"'{path}' could not be saved.\n\n{error}")
            return False
        tab.path = path
        tab.document.undo_stack.set_clean()
        self.settings.add_recent_project(path)
        self._update_tab(tab)
        return True

    def close_current(self) -> bool:
        tab = self.current_tab
        return self.close_tab(tab) if tab is not None else False

    def close_tab(self, tab: DocumentTab) -> bool:
        """Close ``tab``, asking to save unsaved changes first. True if it was closed."""
        if tab.is_modified:
            self.tabs.setCurrentWidget(tab)
            choice = self._ask_save(tab)
            if choice is SaveChoice.CANCEL:
                return False
            if choice is SaveChoice.SAVE and not self.save_tab(tab):
                return False
        self.tabs.removeTab(self.tabs.indexOf(tab))
        tab.close_engine()
        tab.deleteLater()
        if self.tabs.count() == 0:
            self.stack.setCurrentWidget(self.launcher)
        self._update_state()
        return True

    def close_all(self) -> bool:
        """Close every tab; stops (and returns False) if the user cancels."""
        return all(self.close_tab(tab) for tab in self.document_tabs())

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt API)
        if not self.close_all():
            event.ignore()
            return
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
        event.accept()

    # --- edit -------------------------------------------------------------------------

    def undo(self) -> None:
        if (tab := self.current_tab) is not None:
            tab.document.undo()

    def redo(self) -> None:
        if (tab := self.current_tab) is not None:
            tab.document.redo()

    def _table_call(self, method: Callable[..., object], *args: object) -> Callable[[], None]:
        """A callback that runs ``method`` on the current tab's table view, if any."""

        def call() -> None:
            tab = self.current_tab
            if tab is not None and tab.table_view is not None:
                method(tab.table_view, *args)

        return call

    def _tab_call(self, method: Callable[..., object]) -> Callable[[], None]:
        """A callback that runs ``method`` on the current document tab, if any."""

        def call() -> None:
            tab = self.current_tab
            if tab is not None:
                method(tab)

        return call

    def show_constants(self) -> None:
        """Show the Constants dialog for the current project and the global constants."""
        from curvis.ui.constants_dialog import ConstantsDialog

        tab = self.current_tab
        dialog = ConstantsDialog(tab.document if tab else None, self.settings, self)
        try:
            self._exec_constants_dialog(dialog)
        finally:
            dialog.deleteLater()

    def _exec_constants_dialog(self, dialog: QDialog) -> None:
        dialog.exec()

    def _show_status(self, message: str) -> None:
        self.statusBar().showMessage(message, STATUS_MESSAGE_MS)

    def show_settings(self) -> None:
        from curvis.ui.settings_dialog import SettingsDialog

        SettingsDialog(self.settings, self).exec()

    def show_command_palette(self) -> None:
        from curvis.ui.command_palette import CommandPalette

        CommandPalette(self.registry, self).exec()

    # --- state ------------------------------------------------------------------------

    def _on_undo_event(self, tab: DocumentTab, _event: UndoEvent) -> None:
        self._update_tab(tab)

    def _on_current_tab_changed(self, _index: int) -> None:
        self._update_state()

    def _on_tab_close_requested(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if isinstance(widget, DocumentTab):
            self.close_tab(widget)

    def _on_setting_changed(self, key: str) -> None:
        if key == Settings.RECENT:
            self._refresh_recent()
        elif key == Settings.DECIMAL_PLACES:
            for tab in self.document_tabs():
                tab.set_decimal_places(self.settings.decimal_places)
        elif key == Settings.GLOBAL_CONSTANTS:
            for tab in self.document_tabs():
                if tab.engine is not None:
                    tab.engine.global_constants_changed()

    def _refresh_recent(self) -> None:
        self.launcher.set_recent_projects(self.settings.recent_projects)

    def _update_tab(self, tab: DocumentTab) -> None:
        index = self.tabs.indexOf(tab)
        if index >= 0:
            self.tabs.setTabText(index, f"{tab.title}*" if tab.is_modified else tab.title)
            self.tabs.setTabToolTip(index, str(tab.path) if tab.path else tab.title)
        if tab is self.current_tab:
            self._update_state()

    def _update_state(self) -> None:
        """Enable actions and set the window title for the current tab."""
        a = self.registry.action
        tab = self.current_tab
        for action_id in ("file.save", "file.save_as", "file.close"):
            a(action_id).setEnabled(tab is not None)
        has_table = tab is not None and tab.table_view is not None
        if self._table_actions_created:
            for action_id in TABLE_ACTIONS:
                a(action_id).setEnabled(has_table)
        stack = tab.document.undo_stack if tab is not None else None
        undo, redo = a("edit.undo"), a("edit.redo")
        undo.setEnabled(stack is not None and stack.can_undo)
        redo.setEnabled(stack is not None and stack.can_redo)
        undo.setText(f"&Undo {stack.undo_text}" if stack and stack.can_undo else "&Undo")
        redo.setText(f"&Redo {stack.redo_text}" if stack and stack.can_redo else "&Redo")
        if tab is None:
            self.setWindowTitle("Curvis")
            self.setWindowModified(False)
            self.setWindowFilePath("")
        else:
            self.setWindowTitle(f"{tab.title}[*] — Curvis")
            self.setWindowModified(tab.is_modified)
            self.setWindowFilePath(str(tab.path) if tab.path else "")

    # --- dialogs (tests replace these) ---------------------------------------------------

    def _ask_save(self, tab: DocumentTab) -> SaveChoice:
        buttons = QMessageBox.StandardButton
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            f"Save changes to '{tab.title}' before closing?",
            buttons.Save | buttons.Discard | buttons.Cancel,
            buttons.Save,
        )
        return {buttons.Save: SaveChoice.SAVE, buttons.Discard: SaveChoice.DISCARD}.get(
            answer, SaveChoice.CANCEL
        )

    def _ask_open_path(self) -> Path | None:
        name, _ = QFileDialog.getOpenFileName(self, "Open project", "", PROJECT_FILTER)
        return Path(name) if name else None

    def _ask_save_path(self, tab: DocumentTab) -> Path | None:
        start = str(tab.path) if tab.path else f"{tab.title}{PROJECT_SUFFIX}"
        name, _ = QFileDialog.getSaveFileName(self, "Save project", start, PROJECT_FILTER)
        return Path(name) if name else None

    def _ask_import_path(self) -> Path | None:
        name, _ = QFileDialog.getOpenFileName(self, "Import data", "", IMPORT_FILTER)
        return Path(name) if name else None

    def _exec_import_dialog(self, dialog: QDialog) -> bool:
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)


def _load_project(path: str) -> Document:
    """Scheduler job: load a project file (runs on a worker thread)."""
    from curvis.io import project

    return project.load(path)
