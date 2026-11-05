"""The tables of a project as tabs along the bottom of its tab (docs/DESIGN.md section 8).

One tab per table, then a *+* button (New table). The bar only shows and asks: the
owner (:class:`~curvis.ui.document_tab.DocumentTab`) runs the commands.

- Clicking a tab selects the table (``current_changed``).
- Double-clicking a tab, or *Rename* in its context menu, asks to rename it in place
  (``rename_requested``; the owner calls :meth:`TableTabs.edit_name`).
- The close button and *Close table…* ask to close it (``close_requested``). There is
  no close button while the project has only one table.
- Dragging a tab asks to move the table (``move_requested``) when the drag ends.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QEnterEvent, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QHBoxLayout,
    QMenu,
    QStyle,
    QStyleOptionTab,
    QTabBar,
    QWidget,
)

from curvis.ui.inline_edit import InlineEditor

NEW_TABLE_TIP = "New table (Ctrl+Shift+N)"


class TableTabBar(QTabBar):
    """The tab bar; reports a finished drag as one move."""

    drag_finished = Signal(str, int)
    """A tab was dragged: (table id, its new position)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("tableTabs")
        self.setShape(QTabBar.Shape.RoundedSouth)
        self.setDocumentMode(True)
        self.setMovable(True)
        self.setExpanding(False)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.TextElideMode.ElideNone)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._pressed: tuple[str, int] | None = None

    def table_id(self, index: int) -> str:
        data = self.tabData(index)
        return data if isinstance(data, str) else ""

    def ids(self) -> list[str]:
        return [self.table_id(i) for i in range(self.count())]

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        index = self.tabAt(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and index >= 0:
            self._pressed = (self.table_id(index), index)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        super().mouseReleaseEvent(event)
        pressed, self._pressed = self._pressed, None
        if pressed is None or event.button() != Qt.MouseButton.LeftButton:
            return
        table_id, before = pressed
        ids = self.ids()
        if table_id in ids and ids.index(table_id) != before:
            self.drag_finished.emit(table_id, ids.index(table_id))


class NewTableTab(QAbstractButton):
    """The *+* after the last tab, drawn as an unselected tab of ``bar`` with the same
    height, so it reads as "one more table"."""

    def __init__(self, bar: QTabBar, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bar = bar
        self._hovered = False
        self.setObjectName("newTableButton")
        self.setText("+")
        self.setToolTip(NEW_TABLE_TIP)
        self.setAccessibleName("New table")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        height = self._tab_height()
        return QSize(max(height + height // 3, 2 * self.fontMetrics().height()), height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        return self.sizeHint()

    def _tab_height(self) -> int:
        if self._bar.count():
            return self._bar.tabRect(0).height() or self._bar.sizeHint().height()
        return self.fontMetrics().height() + 12

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802 (Qt API)
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt API)
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802 (Qt API)
        option = QStyleOptionTab()
        option.initFrom(self)
        option.rect = self.rect()
        option.shape = self._bar.shape()
        option.documentMode = self._bar.documentMode()
        option.position = QStyleOptionTab.TabPosition.End
        option.selectedPosition = QStyleOptionTab.SelectedPosition.NotAdjacent
        option.text = ""
        option.state &= ~QStyle.StateFlag.State_Selected
        if self._hovered:
            option.state |= QStyle.StateFlag.State_MouseOver
        if self.isDown():
            option.state |= QStyle.StateFlag.State_Sunken
        painter = QPainter(self)
        style = self._bar.style()
        style.drawControl(QStyle.ControlElement.CE_TabBarTabShape, option, painter, self._bar)
        font = painter.font()
        font.setPointSizeF(font.pointSizeF() * 1.3)
        painter.setFont(font)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "+")
        painter.end()


class TableTabs(QWidget):
    """The tab bar with the *+* button after the last tab."""

    current_changed = Signal(str)
    """The user selected the table with this id."""
    new_requested = Signal()
    rename_requested = Signal(str)
    close_requested = Signal(str)
    move_requested = Signal(str, int)
    """Move the table (id) to this position."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bar = TableTabBar(self)
        self.plus = NewTableTab(self.bar, self)
        self.plus.clicked.connect(self.new_requested)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)  # the + sits right after the last tab, like another tab
        layout.addWidget(self.bar, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.plus, 0, Qt.AlignmentFlag.AlignTop)
        layout.addStretch(1)
        self.editor: InlineEditor | None = None
        self._syncing = False
        self.bar.currentChanged.connect(self._on_current_changed)
        self.bar.tabBarDoubleClicked.connect(self._on_double_click)
        self.bar.tabCloseRequested.connect(
            lambda i: self.close_requested.emit(self.bar.table_id(i))
        )
        self.bar.drag_finished.connect(self.move_requested)
        self.bar.customContextMenuRequested.connect(self._show_menu)

    @property
    def current_id(self) -> str | None:
        index = self.bar.currentIndex()
        return self.bar.table_id(index) if index >= 0 else None

    def set_tables(self, tables: Sequence[tuple[str, str]], current: str | None) -> None:
        """Show ``tables`` as (id, name) pairs and select ``current``."""
        self._syncing = True
        try:
            bar = self.bar
            closable = len(tables) > 1
            shown = [(bar.table_id(i), bar.tabText(i)) for i in range(bar.count())]
            if shown != list(tables) or bar.tabsClosable() != closable:
                while bar.count():
                    bar.removeTab(bar.count() - 1)
                for table_id, name in tables:
                    index = bar.addTab(name)
                    bar.setTabData(index, table_id)
                    bar.setTabToolTip(index, name)
                bar.setTabsClosable(closable)
                self.plus.updateGeometry()
                # Give the bar its new width now, so selecting a tab below does not
                # scroll a bar that is still as narrow as before.
                bar.updateGeometry()
                layout = self.layout()
                if layout is not None:
                    layout.activate()
            ids = bar.ids()
            if current in ids:
                bar.setCurrentIndex(ids.index(current))
        finally:
            self._syncing = False

    def edit_name(
        self, table_id: str, commit: Callable[[str], bool], restore_focus: QWidget | None
    ) -> InlineEditor | None:
        """Edit the name of the tab of ``table_id`` in place; ``commit`` gets the text and
        returns False to keep the field open."""
        ids = self.bar.ids()
        if table_id not in ids:
            return None
        index = ids.index(table_id)
        self.bar.setCurrentIndex(index)
        rect = self.bar.tabRect(index).adjusted(2, 2, -2, -2)
        rect.setWidth(max(rect.width(), 120))
        self.editor = InlineEditor(
            self.bar,
            rect,
            self.bar.tabText(index),
            lambda text, _step: commit(text),
            restore_focus=restore_focus,
        )
        return self.editor

    def context_menu(self, index: int) -> QMenu:
        """The menu of the tab at ``index``: Rename, Close table…, New table."""
        table_id = self.bar.table_id(index)
        menu = QMenu(self)
        menu.addAction("&Rename", lambda: self.rename_requested.emit(table_id))
        close = menu.addAction("&Close table…", lambda: self.close_requested.emit(table_id))
        close.setEnabled(self.bar.count() > 1)
        menu.addSeparator()
        menu.addAction("&New table", self.new_requested.emit)
        return menu

    def _show_menu(self, pos: QPoint) -> None:
        index = self.bar.tabAt(pos)
        if index < 0:
            return
        self.bar.setCurrentIndex(index)
        self.context_menu(index).exec(self.bar.mapToGlobal(pos))

    def _on_current_changed(self, index: int) -> None:
        if not self._syncing and index >= 0:
            self.current_changed.emit(self.bar.table_id(index))

    def _on_double_click(self, index: int) -> None:
        if index >= 0:
            self.rename_requested.emit(self.bar.table_id(index))
