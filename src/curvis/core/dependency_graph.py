"""Dependency graph between document nodes (columns, calculated columns, analyses, graphs).

Each node declares the nodes it reads. After a change, :meth:`DependencyGraph.downstream`
returns the nodes that must be recomputed, in an order where every node comes after
everything it reads.
"""

import heapq
from collections import deque
from collections.abc import Iterable


class CycleError(ValueError):
    """Declaring these reads would create a circular dependency."""

    def __init__(self, path: list[str]) -> None:
        self.path = path
        super().__init__("Circular dependency: " + " → ".join(path))


class DependencyGraph:
    """Directed graph of "reads" relations between string node ids.

    Ids that are read but never declared (e.g. plain data columns) count as sources.
    Ties in the recompute order are broken by the order in which nodes were declared,
    so results are deterministic.
    """

    def __init__(self) -> None:
        self._reads: dict[str, tuple[str, ...]] = {}
        self._readers: dict[str, dict[str, None]] = {}  # node -> ordered set of readers
        self._order: dict[str, int] = {}
        self._next_order = 0

    def __contains__(self, node: object) -> bool:
        return node in self._reads

    @property
    def nodes(self) -> tuple[str, ...]:
        """Declared nodes, in declaration order."""
        return tuple(self._reads)

    def reads(self, node: str) -> tuple[str, ...]:
        """What ``node`` reads (empty for an undeclared node)."""
        return self._reads.get(node, ())

    def readers(self, node: str) -> tuple[str, ...]:
        """Nodes that read ``node`` directly."""
        return tuple(self._readers.get(node, ()))

    def set_reads(self, node: str, reads: Iterable[str]) -> None:
        """Declare (or replace) what ``node`` reads.

        Raises :class:`CycleError`, leaving the graph unchanged, if this would make a
        node depend on itself.
        """
        new_reads = tuple(dict.fromkeys(reads))
        for source in new_reads:
            path = self._path(node, source)
            if path is not None:
                raise CycleError([*path, node])
        self._unlink(node)
        self._reads[node] = new_reads
        if node not in self._order:
            self._order[node] = self._next_order
            self._next_order += 1
        for source in new_reads:
            self._readers.setdefault(source, {})[node] = None

    def remove_node(self, node: str) -> None:
        """Forget what ``node`` reads. Nodes that read it keep doing so (it becomes a source)."""
        self._unlink(node)
        self._reads.pop(node, None)
        self._order.pop(node, None)

    def downstream(self, changed: str | Iterable[str]) -> list[str]:
        """Nodes affected by a change to ``changed``, in recompute order.

        Every returned node comes after all the returned nodes it reads. A changed node
        is only included if it also reads (directly or not) another changed node.
        """
        starts = [changed] if isinstance(changed, str) else list(dict.fromkeys(changed))
        affected: set[str] = set()
        queue = deque(starts)
        while queue:
            for reader in self._readers.get(queue.popleft(), ()):
                if reader not in affected:
                    affected.add(reader)
                    queue.append(reader)

        indegree = {n: sum(1 for r in self._reads[n] if r in affected) for n in affected}
        ready = [(self._order[n], n) for n, d in indegree.items() if d == 0]
        heapq.heapify(ready)
        result: list[str] = []
        while ready:
            _, node = heapq.heappop(ready)
            result.append(node)
            for reader in self._readers.get(node, ()):
                if reader in affected:
                    indegree[reader] -= 1
                    if indegree[reader] == 0:
                        heapq.heappush(ready, (self._order[reader], reader))
        return result

    def _path(self, start: str, goal: str) -> list[str] | None:
        """A data-flow path start → … → goal (following readers), or None."""
        if start == goal:
            return [start]
        parent: dict[str, str] = {}
        queue = deque([start])
        seen = {start}
        while queue:
            current = queue.popleft()
            for reader in self._readers.get(current, ()):
                if reader in seen:
                    continue
                parent[reader] = current
                if reader == goal:
                    path = [goal]
                    while path[-1] != start:
                        path.append(parent[path[-1]])
                    return path[::-1]
                seen.add(reader)
                queue.append(reader)
        return None

    def _unlink(self, node: str) -> None:
        for source in self._reads.get(node, ()):
            readers = self._readers.get(source)
            if readers is not None:
                readers.pop(node, None)
                if not readers:
                    del self._readers[source]
