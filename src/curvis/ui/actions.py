"""Registry of the app's actions. Every action must be created here so that the command
palette (Ctrl+K) can list it."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QWidget


@dataclass(frozen=True)
class RegisteredAction:
    """An action with its stable id and the category shown in the palette."""

    id: str
    category: str
    action: QAction

    @property
    def text(self) -> str:
        """Action text without the ``&`` mnemonic markers and trailing dots."""
        return self.action.text().replace("&&", "\0").replace("&", "").replace("\0", "&")

    @property
    def shortcut(self) -> str:
        """The first shortcut in the platform's native notation, or ""."""
        return self.action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)


class ActionRegistry:
    """Creates and owns the app's ``QAction`` objects, keyed by a stable id."""

    def __init__(self, parent: QWidget) -> None:
        self._parent = parent
        self._actions: dict[str, RegisteredAction] = {}

    def register(
        self,
        action_id: str,
        text: str,
        callback: Callable[[], object],
        *,
        category: str,
        shortcut: QKeySequence | QKeySequence.StandardKey | str | None = None,
        menu_role: QAction.MenuRole = QAction.MenuRole.NoRole,
    ) -> QAction:
        """Create an action, add it to the parent window (so its shortcut works) and return it."""
        if action_id in self._actions:
            raise ValueError(f"An action with id {action_id!r} is already registered.")
        action = QAction(text, self._parent)
        action.setObjectName(action_id)
        if isinstance(shortcut, QKeySequence.StandardKey):
            action.setShortcuts(shortcut)
        elif shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.setMenuRole(menu_role)
        action.triggered.connect(lambda _checked=False: callback())
        self._parent.addAction(action)
        self._actions[action_id] = RegisteredAction(action_id, category, action)
        return action

    def action(self, action_id: str) -> QAction:
        """The action with ``action_id``; raises ``KeyError`` if there is none."""
        return self._actions[action_id].action

    def entries(self) -> list[RegisteredAction]:
        """All registered actions in registration order."""
        return list(self._actions.values())

    def __contains__(self, action: object) -> bool:
        return any(entry.action is action for entry in self._actions.values())
