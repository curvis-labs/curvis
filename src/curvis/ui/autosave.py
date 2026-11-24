"""Autosave: a timer per open document that writes a recovery copy of unsaved changes.

At each tick (every few minutes, as set in Settings) a document gets a copy only when it
has unsaved changes and has changed since its last copy. The document is copied on the
main thread (:func:`curvis.io.recovery.snapshot`: it shares the column arrays, which never
change once made, so it costs well under a millisecond whatever the size of the data) and
written on a worker thread through the scheduler, so the window never waits for the disk. A newer
copy of the same document cancels an older one still being written.

Saving, closing or discarding a document deletes its copy (:meth:`Autosave.saved`,
:meth:`Autosave.remove`). Autosave cannot be turned off.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal

from curvis.core import UndoEvent

if TYPE_CHECKING:
    from curvis.compute import JobError, Scheduler
    from curvis.io.recovery import RecoveryStore
    from curvis.ui.document_tab import DocumentTab

NODE_PREFIX = "recovery:"
"""Scheduler nodes of recovery copies: ``recovery:<copy id>``."""
MINUTE_MS = 60_000


@dataclass
class _Entry:
    copy_id: str
    timer: QTimer
    unsubscribe: Callable[[], None]
    changes: int = 0
    """Undo events since the document was opened."""
    copied: int = 0
    """``changes`` when the last copy was taken (or the document was saved)."""
    pending: set[str] = field(default_factory=set)


class Autosave(QObject):
    """Writes recovery copies of the open documents into ``store``."""

    failed = Signal(str)
    """A copy could not be written: a short message for the status bar."""

    def __init__(
        self,
        store: RecoveryStore,
        scheduler: Scheduler,
        interval_ms: int,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.scheduler = scheduler
        self._interval_ms = interval_ms
        self._entries: dict[DocumentTab, _Entry] = {}
        self._tabs_by_node: dict[str, DocumentTab] = {}
        self._reported_failure = False
        scheduler.finished.connect(self._on_finished)
        scheduler.failed.connect(self._on_failed)

    @property
    def interval_ms(self) -> int:
        return self._interval_ms

    def set_interval(self, interval_ms: int) -> None:
        """Use a new interval for every open document, from now on."""
        self._interval_ms = interval_ms
        for entry in self._entries.values():
            entry.timer.start(interval_ms)

    # --- documents -------------------------------------------------------------------

    def add(self, tab: DocumentTab, copy_id: str | None = None) -> None:
        """Start autosaving ``tab``. ``copy_id`` is the copy it was recovered from."""
        timer = QTimer(self)
        timer.timeout.connect(functools.partial(self.write_if_needed, tab))
        entry = _Entry(
            copy_id or self.store.new_copy_id(),
            timer,
            tab.document.undo_stack.subscribe(functools.partial(self._on_change, tab)),
        )
        self._entries[tab] = entry
        self._tabs_by_node[NODE_PREFIX + entry.copy_id] = tab
        timer.start(self._interval_ms)

    def remove(self, tab: DocumentTab) -> None:
        """Stop autosaving ``tab`` and delete its copy (it was closed)."""
        entry = self._entries.pop(tab, None)
        if entry is None:
            return
        entry.timer.stop()
        entry.timer.deleteLater()
        entry.unsubscribe()
        node = NODE_PREFIX + entry.copy_id
        self.scheduler.cancel(node)
        self._tabs_by_node.pop(node, None)
        self.store.forget(entry.copy_id)

    def remove_all(self) -> None:
        for tab in list(self._entries):
            self.remove(tab)

    def saved(self, tab: DocumentTab) -> None:
        """``tab`` was saved: its copy is no longer needed."""
        entry = self._entries.get(tab)
        if entry is None:
            return
        entry.copied = entry.changes
        self._drop_copy(entry)

    def copy_id(self, tab: DocumentTab) -> str | None:
        entry = self._entries.get(tab)
        return entry.copy_id if entry is not None else None

    def needs_copy(self, tab: DocumentTab) -> bool:
        """True when ``tab`` has unsaved changes that its copy does not hold yet."""
        entry = self._entries.get(tab)
        return entry is not None and tab.is_modified and entry.changes != entry.copied

    def write_if_needed(self, tab: DocumentTab) -> bool:
        """Take a copy of ``tab`` now if it needs one (the timer calls this). True if a
        copy is being written."""
        entry = self._entries.get(tab)
        if entry is None:
            return False
        if not tab.is_modified:
            # Back to the saved state (e.g. by undo): a copy would only offer old changes.
            entry.copied = entry.changes
            if self.store.has_copy(entry.copy_id):
                self._drop_copy(entry)
            return False
        if entry.changes == entry.copied:
            return False
        self._submit(tab, entry)
        return True

    def write_all(self) -> None:
        """Take a copy of every document that needs one (for tests)."""
        for tab in list(self._entries):
            self.write_if_needed(tab)

    # --- internals ---------------------------------------------------------------------

    def _submit(self, tab: DocumentTab, entry: _Entry) -> None:
        from curvis.compute import Job
        from curvis.io.recovery import CopyInfo, snapshot

        document = snapshot(tab.document)
        info = CopyInfo(
            name=tab.title,
            original_path=tab.path,
            copied_at=self.store.clock(),
            original_mtime_ns=tab.file_mtime_ns if tab.path is not None else None,
        )
        ticket = self.store.begin_write(entry.copy_id)
        entry.copied = entry.changes
        job = Job(
            self.store.write,
            (entry.copy_id, ticket, document, info),
            imports=("pyarrow", "curvis.io.project"),
        )
        self.scheduler.submit(NODE_PREFIX + entry.copy_id, job, delay_ms=0)

    def _drop_copy(self, entry: _Entry) -> None:
        self.scheduler.cancel(NODE_PREFIX + entry.copy_id)
        self.store.delete(entry.copy_id)

    def _on_change(self, tab: DocumentTab, _event: UndoEvent) -> None:
        entry = self._entries.get(tab)
        if entry is not None:
            entry.changes += 1

    def _on_finished(self, node: str, _result: object) -> None:
        if node in self._tabs_by_node:
            self._reported_failure = False

    def _on_failed(self, node: str, error: JobError) -> None:
        tab = self._tabs_by_node.get(node)
        if tab is None:
            return
        entry = self._entries.get(tab)
        if entry is not None:
            entry.copied = -1  # try again at the next tick
        if not self._reported_failure:  # once, not every few minutes
            self._reported_failure = True
            self.failed.emit(f"Could not save a recovery copy of '{tab.title}': {error.message}")
