"""Command palette (Ctrl+K): search and run any registered action from the keyboard."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from curvis.ui.actions import ActionRegistry, RegisteredAction


def matches(entry: RegisteredAction, query: str) -> bool:
    """True when every word of ``query`` appears in the action's text or category."""
    haystack = f"{entry.text} {entry.category}".lower()
    return all(word in haystack for word in query.lower().split())


class CommandPalette(QDialog):
    """A search box over all registered actions. Enter runs the selected action."""

    def __init__(self, registry: ActionRegistry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Command palette")
        self.setModal(True)
        self.resize(480, 360)
        self._registry = registry

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Type a command")
        self.search.setClearButtonEnabled(True)
        self.list = QListWidget(self)
        self.list.setUniformItemSizes(True)

        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.list)

        self.search.textChanged.connect(self._refresh)
        self.search.returnPressed.connect(self.run_selected)
        self.search.installEventFilter(self)
        self.list.itemActivated.connect(lambda _item: self.run_selected())
        self._refresh()

    def visible_entries(self) -> list[RegisteredAction]:
        """The actions currently listed, top to bottom."""
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]

    def run_selected(self) -> None:
        """Close the palette and trigger the selected action, if it is enabled."""
        item = self.list.currentItem()
        if item is None:
            return
        entry: RegisteredAction = item.data(Qt.ItemDataRole.UserRole)
        if not entry.action.isEnabled():
            return
        self.accept()
        entry.action.trigger()

    def _refresh(self) -> None:
        query = self.search.text()
        self.list.clear()
        for entry in self._registry.entries():
            if not entry.action.isVisible() or not matches(entry, query):
                continue
            label = f"{entry.category}: {entry.text.rstrip('.…')}"
            if entry.shortcut:
                label += f"    {entry.shortcut}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            if not entry.action.isEnabled():
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list.addItem(item)
        first_enabled = next(
            (
                i
                for i in range(self.list.count())
                if self.list.item(i).flags() & Qt.ItemFlag.ItemIsEnabled
            ),
            0,
        )
        self.list.setCurrentRow(first_enabled)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt API)
        # Up and Down in the search box move the selection in the list.
        if watched is self.search and event.type() == QEvent.Type.KeyPress:
            assert isinstance(event, QKeyEvent)
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                step = -1 if event.key() == Qt.Key.Key_Up else 1
                row = self.list.currentRow() + step
                if 0 <= row < self.list.count():
                    self.list.setCurrentRow(row)
                return True
        return super().eventFilter(watched, event)
