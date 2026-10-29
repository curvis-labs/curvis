"""The launcher: shown at start-up and whenever no document is open."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from curvis.settings import RecentProject
from curvis.ui.relative_time import format_relative


def _utc_now() -> datetime:
    return datetime.now(UTC)


class LauncherWidget(QWidget):
    """New project and Open buttons, and the list of recent projects."""

    new_requested = Signal()
    open_requested = Signal()
    recent_chosen = Signal(Path)

    def __init__(
        self,
        parent: QWidget | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("launcher")
        self._clock = clock

        title = QLabel("Curvis", self)
        font = title.font()
        font.setPointSizeF(font.pointSizeF() * 2)
        font.setBold(True)
        title.setFont(font)

        self.new_button = QPushButton("New project", self)
        self.new_button.setDefault(True)
        self.open_button = QPushButton("Open…", self)
        buttons = QHBoxLayout()
        buttons.addWidget(self.new_button)
        buttons.addWidget(self.open_button)
        buttons.addStretch()

        self.recent_label = QLabel("Recent projects", self)
        self.recent_list = QListWidget(self)
        self.recent_list.setObjectName("recentProjects")
        self.empty_label = QLabel("Projects you open or save will appear here.", self)
        self.empty_label.setEnabled(False)

        column = QVBoxLayout()
        column.addWidget(title)
        column.addSpacing(12)
        column.addLayout(buttons)
        column.addSpacing(18)
        column.addWidget(self.recent_label)
        column.addWidget(self.recent_list, 1)
        column.addWidget(self.empty_label)
        outer = QHBoxLayout(self)
        outer.addStretch(1)
        outer.addLayout(column, 3)
        outer.addStretch(1)

        self.new_button.clicked.connect(self.new_requested)
        self.open_button.clicked.connect(self.open_requested)
        self.recent_list.itemActivated.connect(self._on_activated)
        self.recent_list.itemClicked.connect(self._on_activated)
        self.set_recent_projects([])

    def set_recent_projects(self, projects: Sequence[RecentProject]) -> None:
        """Show ``projects`` (newest first). Files that no longer exist are disabled."""
        now = self._clock()
        self.recent_list.clear()
        for project in projects:
            exists = project.path.is_file()
            when = format_relative(project.opened_at, now) if exists else "File not found"
            item = QListWidgetItem(f"{project.name}\n{project.path.parent}  ·  {when}")
            item.setData(Qt.ItemDataRole.UserRole, project.path)
            item.setToolTip(str(project.path))
            if not exists:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.recent_list.addItem(item)
        has_any = bool(projects)
        self.recent_list.setVisible(has_any)
        self.empty_label.setVisible(not has_any)

    def _on_activated(self, item: QListWidgetItem) -> None:
        if item.flags() & Qt.ItemFlag.ItemIsEnabled:
            self.recent_chosen.emit(item.data(Qt.ItemDataRole.UserRole))
