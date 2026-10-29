"""Recompute stale nodes in dependency order after each command.

A :class:`Recalculator` watches one document's undo stack. After every do, undo or
redo it asks the :class:`~curvis.core.DependencyGraph` which nodes are downstream of
what the command touched, marks them stale and schedules them: a node starts once none
of the nodes it reads is still stale, so independent nodes run in parallel and every
node sees up-to-date inputs.

What a node computes is decided by the ``plan`` function, which turns a node id into a
:class:`NodeJob` (or ``None`` if there is nothing to compute). It runs on the main
thread when the node is about to start, so it can safely copy its inputs from the
document.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, Signal

from curvis.compute.jobs import JobError, JobLike, Weight
from curvis.compute.scheduler import Scheduler
from curvis.core import DependencyGraph, Document, UndoEvent


@dataclass(frozen=True)
class NodeJob:
    """The job that recomputes one node."""

    job: JobLike
    weight: Weight = "light"


type Planner = Callable[[str], NodeJob | None]

UPSTREAM_ERROR = "UpstreamError"
"""``JobError.error_type`` of a node that was not updated because an input failed."""


class Recalculator(QObject):
    """Keeps the derived nodes of one document up to date, through a shared scheduler."""

    stale = Signal(list)
    """Node ids that became stale, in recompute order."""
    node_finished = Signal(str, object)
    """``(node_id, result)``. Slots run before the node's readers are planned."""
    node_failed = Signal(str, object)
    """``(node_id, JobError)``, also for nodes skipped because an input failed."""
    node_cancelled = Signal(str)
    """The node's job was cancelled; it and its readers were not updated."""
    settled = Signal()
    """No node is stale any more."""

    def __init__(
        self,
        document: Document,
        graph: DependencyGraph,
        scheduler: Scheduler,
        plan: Planner,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.graph = graph
        self.scheduler = scheduler
        self.plan = plan
        self.errors: dict[str, JobError] = {}
        """Last error of each node whose latest recompute failed."""
        self._dirty: dict[str, None] = {}  # stale nodes not yet recomputed (ordered set)
        self._running: set[str] = set()  # dirty nodes submitted to the scheduler
        self._unsubscribe = document.undo_stack.subscribe(self._on_undo_event)
        scheduler.finished.connect(self._on_finished)
        scheduler.failed.connect(self._on_failed)
        scheduler.cancelled.connect(self._on_cancelled)

    @property
    def busy(self) -> bool:
        """True while any node is stale."""
        return bool(self._dirty)

    def is_stale(self, node_id: str) -> bool:
        return node_id in self._dirty

    def changed(self, node_ids: Iterable[str], delay_ms: int | None = None) -> None:
        """Mark everything downstream of ``node_ids`` stale and schedule it."""
        self._mark(self.graph.downstream(node_ids), delay_ms)

    def recompute(self, node_ids: Iterable[str], delay_ms: int | None = 0) -> None:
        """Recompute ``node_ids`` themselves and everything downstream of them."""
        starts = list(dict.fromkeys(node_ids))
        downstream = self.graph.downstream(starts)
        self._mark([n for n in starts if n not in downstream] + downstream, delay_ms)

    def close(self) -> None:
        """Stop watching the document and cancel this document's running jobs."""
        self._unsubscribe()
        for signal, slot in (
            (self.scheduler.finished, self._on_finished),
            (self.scheduler.failed, self._on_failed),
            (self.scheduler.cancelled, self._on_cancelled),
        ):
            signal.disconnect(slot)
        running, self._running = self._running, set()
        for node in running:
            self.scheduler.cancel(node)
        self._dirty.clear()

    # --- internals -----------------------------------------------------------------

    def _on_undo_event(self, event: UndoEvent) -> None:
        if event.command is not None:
            self.changed(event.command.touched_nodes)

    def _mark(self, stale: list[str], delay_ms: int | None) -> None:
        if not stale:
            return
        for node in stale:
            if node in self._running:
                # Its inputs are changing again: throw the old run away.
                self._running.discard(node)
                self.scheduler.cancel(node)
            self._dirty.pop(node, None)
            self._dirty[node] = None
        self.stale.emit(stale)
        self._launch(stale, delay_ms)

    def _ready(self, node: str) -> bool:
        return (
            node in self._dirty
            and node not in self._running
            and not any(source in self._dirty for source in self.graph.reads(node))
        )

    def _launch(self, candidates: Iterable[str], delay_ms: int | None) -> None:
        queue = list(candidates)
        while queue:
            node = queue.pop(0)
            if not self._ready(node):
                continue
            try:
                node_job = self.plan(node)
            except Exception as exc:  # a bug in a planner must not stop the others
                self._fail(node, JobError.from_exception(node, exc))
                continue
            if node_job is None:  # nothing to compute: its readers can go ahead
                del self._dirty[node]
                queue.extend(r for r in self.graph.readers(node) if r in self._dirty)
                continue
            self._running.add(node)
            self.scheduler.submit(node, node_job.job, node_job.weight, delay_ms)
        self._check_settled()

    def _on_finished(self, node: str, result: Any) -> None:
        if node not in self._running:
            return  # another document's node, or a run we no longer want
        self._running.discard(node)
        del self._dirty[node]
        self.errors.pop(node, None)
        self.node_finished.emit(node, result)
        self._launch([r for r in self.graph.readers(node) if r in self._dirty], 0)

    def _on_failed(self, node: str, error: JobError) -> None:
        if node not in self._running:
            return
        self._running.discard(node)
        self._fail(node, error)
        self._check_settled()

    def _fail(self, node: str, error: JobError) -> None:
        self._dirty.pop(node, None)
        self.errors[node] = error
        self.node_failed.emit(node, error)
        message = f"Not updated because an input failed: {error.message}"
        for reader in self.graph.downstream(node):
            if reader not in self._dirty:
                continue
            del self._dirty[reader]
            if reader in self._running:
                self._running.discard(reader)
                self.scheduler.cancel(reader)
            skipped = JobError(reader, message, UPSTREAM_ERROR)
            self.errors[reader] = skipped
            self.node_failed.emit(reader, skipped)

    def _on_cancelled(self, node: str) -> None:
        if node not in self._running:
            return
        self._running.discard(node)
        self._dirty.pop(node, None)
        for reader in self.graph.downstream(node):
            self._dirty.pop(reader, None)
        self.node_cancelled.emit(node)
        self._check_settled()

    def _check_settled(self) -> None:
        if not self._dirty:
            self.settled.emit()
