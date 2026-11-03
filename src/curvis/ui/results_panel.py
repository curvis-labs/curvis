"""The Results panel, docked to the right of the table (mockup: docs/mockups/index.html).

The combo box switches between the analyses of the shown table, and *Edit…* reopens
the Analyze dialog with the stored parameters. A result shows, in this order: its
plain-language reading, its assumption warnings (with the warning icon), then its
numbers in tables with exact method names, and the star thresholds. An analysis that
cannot run shows the error and what to change instead. While an analysis reruns, a
"Recalculating…" badge appears and the old numbers stay visible.

Ctrl+C copies the selected cells of a result table as tab-separated text, with numbers
at full precision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import shiboken6
from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    Signal,
)
from PySide6.QtGui import QFont, QGuiApplication, QKeyEvent, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from curvis.analysis.result import STARS_FOOTNOTE, Grid, Result, grids
from curvis.core import Document
from curvis.ui.dialogs import SECTION_SPACING, SPACING, secondary_label
from curvis.ui.table.clipboard import to_tsv

if TYPE_CHECKING:
    from curvis.compute.analysis_engine import AnalysisEngine

EMPTY_TEXT = "No analyses yet.\nPress Ctrl+Shift+A to analyze this table."
RECALCULATING = "Recalculating…"
COPY_HINT = "Ctrl+C copies the selected cells."
MAX_GRID_ROWS = 20
"""A result table with more rows than this scrolls on its own."""

type _Index = QModelIndex | QPersistentModelIndex


class GridModel(QAbstractTableModel):
    """One result section as a read-only table model."""

    def __init__(self, grid: Grid, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.grid = grid

    def rowCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: N802, B008 (Qt API)
        return 0 if parent.isValid() else len(self.grid.shown)

    def columnCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: N802, B008 (Qt API)
        return 0 if parent.isValid() else len(self.grid.header)

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid():
            return None
        row, column = index.row(), index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return self.grid.shown[row][column]
        if role == Qt.ItemDataRole.ToolTipRole:
            full = self.grid.copied[row][column]
            return full if full != self.grid.shown[row][column] else None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            horizontal = (
                Qt.AlignmentFlag.AlignRight
                if self.grid.numeric[column]
                else Qt.AlignmentFlag.AlignLeft
            )
            return horizontal | Qt.AlignmentFlag.AlignVCenter
        return None

    def headerData(  # noqa: N802 (Qt API)
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.grid.header[section]
        return None


class ResultGrid(QTableView):
    """A result table; Ctrl+C copies its selection."""

    def __init__(self, grid: Grid, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModel(GridModel(grid, self))
        self.setAccessibleName(grid.title or "Results")
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        rows = self.verticalHeader()
        rows.hide()
        rows.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        rows.setDefaultSectionSize(self.fontMetrics().height() + 6)
        self.setWordWrap(False)
        self.setCornerButtonEnabled(True)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setHighlightSections(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._fit_height()

    @property
    def grid(self) -> Grid:
        model = self.model()
        assert isinstance(model, GridModel)
        return model.grid

    def _fit_height(self) -> None:
        rows = min(self.model().rowCount(), MAX_GRID_ROWS)
        height = self.horizontalHeader().sizeHint().height() + 2 * self.frameWidth()
        height += rows * self.verticalHeader().defaultSectionSize()
        height += self.horizontalScrollBar().sizeHint().height()  # room for it if needed
        self.setFixedHeight(height)

    def copy_text(self) -> str:
        """The selected cells as tab-separated text (headers too when whole columns are
        selected). Empty if nothing is selected."""
        indexes = self.selectionModel().selectedIndexes()
        if not indexes:
            return ""
        rows = sorted({i.row() for i in indexes})
        cols = sorted({i.column() for i in indexes})
        chosen = {(i.row(), i.column()) for i in indexes}
        grid = self.grid
        block: list[list[str]] = []
        if len(rows) == len(grid.shown) and len(grid.shown) > 1:
            block.append([grid.header[c] for c in cols])
        for r in rows:
            block.append([grid.copied[r][c] if (r, c) in chosen else "" for c in cols])
        return to_tsv(block)

    def copy_selection(self) -> bool:
        text = self.copy_text()
        if text:
            QGuiApplication.clipboard().setText(text)
        return bool(text)

    def event(self, event: QEvent) -> bool:
        # Take Ctrl+C before the window's Copy action (which copies table cells).
        if (
            event.type() == QEvent.Type.ShortcutOverride
            and isinstance(event, QKeyEvent)
            and event.matches(QKeySequence.StandardKey.Copy)
        ):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt API)
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selection()
            event.accept()
            return
        super().keyPressEvent(event)


def _icon_row(icon: QStyle.StandardPixmap, text: str, parent: QWidget) -> tuple[QWidget, QLabel]:
    """The standard icon in front of a word-wrapped sentence (a warning or an error)."""
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    picture = QLabel(row)
    size = row.fontMetrics().height()
    picture.setPixmap(row.style().standardIcon(icon).pixmap(size, size))
    label = QLabel(text, row)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(picture, 0, Qt.AlignmentFlag.AlignTop)
    layout.addWidget(label, 1)
    return row, label


class ResultsPanel(QWidget):
    """The results of the analyses of one table, one analysis at a time."""

    edit_requested = Signal(str)
    """The user asked to edit the analysis with this id."""
    current_changed = Signal()

    def __init__(
        self,
        document: Document,
        engine: AnalysisEngine | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("resultsPanel")
        self.document = document
        self.engine = engine
        self.table_id: str | None = None
        self.grids: list[ResultGrid] = []
        self.warning_rows: list[QWidget] = []
        self.error_row: QWidget | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING, SPACING, SPACING, SPACING)
        layout.setSpacing(SPACING)
        header = QHBoxLayout()
        title = QLabel("Results", self)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        self.combo = QComboBox(self)
        self.combo.setAccessibleName("Analysis shown")
        self.combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.combo.setMinimumContentsLength(12)
        self.badge = secondary_label(RECALCULATING, self)
        self.badge.setWordWrap(False)
        self.badge.hide()
        self.edit_button = QPushButton("Ed&it…", self)
        self.edit_button.setToolTip("Change the data or options of this analysis")
        header.addWidget(title)
        header.addWidget(self.combo, 1)
        header.addWidget(self.badge)
        header.addWidget(self.edit_button)
        layout.addLayout(header)

        self.empty = secondary_label(EMPTY_TEXT, self)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty, 1)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.scroll_area, 1)
        self.content: QWidget | None = None

        self.combo.currentIndexChanged.connect(lambda _i: self._on_current_changed())
        self.edit_button.clicked.connect(self._on_edit)
        if engine is not None:
            engine.analyses_changed.connect(self.refresh_list)
            engine.state_changed.connect(self._on_state_changed)
        self.refresh_list()

    # --- public ------------------------------------------------------------------------

    @property
    def current_id(self) -> str | None:
        data = self.combo.currentData()
        return str(data) if data else None

    def set_table(self, table_id: str | None) -> None:
        self.table_id = table_id
        self.refresh_list()

    def set_current(self, analysis_id: str) -> None:
        index = self.combo.findData(analysis_id)
        if index >= 0:
            self.combo.setCurrentIndex(index)

    def has_focus_inside(self) -> bool:
        focus = QGuiApplication.focusObject()
        return isinstance(focus, QWidget) and self.isAncestorOf(focus)

    def copy_selection(self) -> bool:
        """Copy the selection of the focused result table (else of the first that has one)."""
        focus = QGuiApplication.focusObject()
        ordered = sorted(self.grids, key=lambda g: g is not focus)
        return any(g.copy_selection() for g in ordered)

    def refresh_list(self) -> None:
        """Fill the combo box with the analyses of the shown table, keeping the current one
        (or showing a newly added one)."""
        previous = {self.combo.itemData(i) for i in range(self.combo.count())}
        current = self.current_id
        analyses = self.document.analyses_of(self.table_id) if self.table_id else []
        table = self.document.table(self.table_id).name if analyses and self.table_id else ""
        self.combo.blockSignals(True)
        self.combo.clear()
        for analysis in analyses:
            try:
                name = analysis.analysis_type.name
            except KeyError:
                name = analysis.type
            self.combo.addItem(f"{name} · {table}", analysis.id)
        added = [a.id for a in analyses if a.id not in previous]
        target = added[-1] if added and previous else current
        index = self.combo.findData(target) if target else -1
        self.combo.setCurrentIndex(index if index >= 0 else (0 if analyses else -1))
        self.combo.blockSignals(False)
        self._on_current_changed()

    # --- showing -----------------------------------------------------------------------

    def _on_current_changed(self) -> None:
        self._show()
        self.current_changed.emit()

    def _on_state_changed(self, analysis_id: str) -> None:
        if analysis_id == self.current_id:
            self._show()

    def _on_edit(self) -> None:
        if self.current_id is not None:
            self.edit_requested.emit(self.current_id)

    def _show(self) -> None:
        analysis_id = self.current_id
        has_analysis = analysis_id is not None
        self.empty.setVisible(not has_analysis)
        self.scroll_area.setVisible(has_analysis)
        self.combo.setEnabled(has_analysis)
        self.edit_button.setEnabled(has_analysis)
        if analysis_id is None:
            self.badge.hide()
            self._set_content(None, None)
            return
        self.badge.setVisible(self.engine is not None and self.engine.is_stale(analysis_id))
        engine = self.engine
        result = engine.results.get(analysis_id) if engine else None
        error = engine.errors.get(analysis_id) if engine else None
        self._set_content(result, error)

    def _set_content(self, result: Result | None, error: str | None) -> None:
        old = self.content
        self.grids = []
        self.warning_rows = []
        self.error_row = None
        content = QWidget()
        content.setObjectName("resultsContent")
        content.setBackgroundRole(QPalette.ColorRole.Window)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING)
        if error is not None:
            icon = QStyle.StandardPixmap.SP_MessageBoxCritical
            self.error_row, label = _icon_row(icon, error, content)
            label.setForegroundRole(QPalette.ColorRole.BrightText)
            layout.addWidget(self.error_row)
        elif result is not None:
            reading = QLabel(result.summary, content)
            reading.setObjectName("reading")
            reading.setWordWrap(True)
            reading.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            font = QFont(reading.font())
            font.setPointSizeF(font.pointSizeF() * 1.1)
            reading.setFont(font)
            layout.addWidget(reading)
            for warning in result.warnings:
                row, _label = _icon_row(
                    QStyle.StandardPixmap.SP_MessageBoxWarning, warning, content
                )
                self.warning_rows.append(row)
                layout.addWidget(row)
            for section in grids(result):
                layout.addSpacing(SECTION_SPACING - SPACING)
                heading = QLabel(section.title or result.method, content)
                bold = heading.font()
                bold.setBold(True)
                heading.setFont(bold)
                layout.addWidget(heading)
                grid = ResultGrid(section, content)
                self.grids.append(grid)
                layout.addWidget(grid)
            # The star thresholds are always printed (CLAUDE.md, mockup note 4).
            notes = " ".join([*result.notes, STARS_FOOTNOTE, COPY_HINT])
            layout.addSpacing(SECTION_SPACING - SPACING)
            layout.addWidget(secondary_label(notes, content))
        elif self.current_id is not None:
            layout.addWidget(secondary_label("Running the analysis…", content))
        layout.addStretch(1)
        self.scroll_area.setWidget(content)  # deletes the old content
        self.content = content
        if old is not None and shiboken6.isValid(old):
            old.deleteLater()
