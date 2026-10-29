"""Per-document undo stack with a step limit, a clean state and change callbacks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from curvis.core.change_log import ChangeLogEntry

if TYPE_CHECKING:
    from curvis.core.commands import Command
    from curvis.core.document import Document

UNDO_LIMIT = 50
"""Maximum number of undo steps per document."""

type UndoEventKind = Literal["do", "undo", "redo", "clear"]


@dataclass(frozen=True)
class UndoEvent:
    """Sent to subscribers after the stack changes. ``command`` is None for "clear"."""

    kind: UndoEventKind
    command: Command | None


type UndoListener = Callable[[UndoEvent], None]


def utc_now() -> datetime:
    """The current time in UTC."""
    return datetime.now(UTC)


class UndoStack:
    """Runs commands on a document and lets them be undone and redone.

    Every do, undo and redo is appended to the document's change log.
    """

    def __init__(
        self,
        document: Document,
        limit: int = UNDO_LIMIT,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if limit < 1:
            raise ValueError("undo limit must be at least 1")
        self._document = document
        self._limit = limit
        self.clock = clock
        self._commands: list[Command] = []
        self._index = 0  # number of commands currently applied
        self._clean_index: int | None = 0
        self._listeners: list[UndoListener] = []

    @property
    def limit(self) -> int:
        """Maximum number of undo steps."""
        return self._limit

    @property
    def index(self) -> int:
        """Number of commands currently applied (the position in the stack)."""
        return self._index

    @property
    def count(self) -> int:
        """Number of commands on the stack, applied or undone."""
        return len(self._commands)

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return self._index < len(self._commands)

    @property
    def undo_text(self) -> str:
        """Summary of the command that undo would revert, or ""."""
        return self._commands[self._index - 1].summary if self.can_undo else ""

    @property
    def redo_text(self) -> str:
        """Summary of the command that redo would repeat, or ""."""
        return self._commands[self._index].summary if self.can_redo else ""

    @property
    def is_clean(self) -> bool:
        """True when the document matches the state marked by :meth:`set_clean`."""
        return self._clean_index == self._index

    def set_clean(self) -> None:
        """Mark the current state as clean (e.g. after saving)."""
        self._clean_index = self._index

    def push(self, command: Command) -> None:
        """Run ``command`` and put it on the stack.

        If ``do()`` raises, nothing is pushed or logged and the error propagates.
        """
        command.do(self._document)
        del self._commands[self._index :]
        if self._clean_index is not None and self._clean_index > self._index:
            self._clean_index = None  # the clean state was in the discarded redo tail
        self._commands.append(command)
        self._index += 1
        if len(self._commands) > self._limit:
            del self._commands[0]
            self._index -= 1
            if self._clean_index is not None:
                self._clean_index = self._clean_index - 1 if self._clean_index > 0 else None
        self._log(command, command.summary)
        self._notify(UndoEvent("do", command))

    def undo(self) -> None:
        """Revert the last applied command; does nothing if there is none."""
        if not self.can_undo:
            return
        command = self._commands[self._index - 1]
        command.undo(self._document)
        self._index -= 1
        self._log(command, f"Undo: {command.summary}")
        self._notify(UndoEvent("undo", command))

    def redo(self) -> None:
        """Repeat the last undone command; does nothing if there is none."""
        if not self.can_redo:
            return
        command = self._commands[self._index]
        command.do(self._document)
        self._index += 1
        self._log(command, f"Redo: {command.summary}")
        self._notify(UndoEvent("redo", command))

    def clear(self) -> None:
        """Forget all undo and redo steps (the document is left as it is)."""
        self._commands.clear()
        self._clean_index = 0 if self.is_clean else None
        self._index = 0
        self._notify(UndoEvent("clear", None))

    def subscribe(self, listener: UndoListener) -> Callable[[], None]:
        """Call ``listener`` after every change; returns a function that unsubscribes."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def _log(self, command: Command, summary: str) -> None:
        self._document.change_log.append(
            ChangeLogEntry(self.clock(), command.command_type, summary)
        )

    def _notify(self, event: UndoEvent) -> None:
        for listener in list(self._listeners):
            listener(event)
