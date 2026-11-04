"""Commands for graphs: add, change and remove a chart spec.

Like analyses, the commands store only the definition (the spec). What a graph draws
is derived: the graph engine (:mod:`curvis.compute.graph_engine`) computes it after the
command and whenever the data or the fits it shows change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from curvis.charts.spec import ChartFamily, ChartSpec
from curvis.core.commands import Command, CommandError

if TYPE_CHECKING:
    from curvis.core.document import Document


class SetGraph(Command):
    """Store ``graph`` (adding it, or replacing the one with the same id).

    With ``remove=True`` the graph with that id is removed instead.
    """

    def __init__(self, graph: ChartSpec, summary: str, remove: bool = False) -> None:
        self.graph = graph
        self.remove = remove
        self._summary = summary
        self._old: ChartSpec | None = None
        self._at = 0

    def do(self, doc: Document) -> None:
        new = self.graph
        index = next((i for i, g in enumerate(doc.graphs) if g.id == new.id), None)
        if self.remove:
            if index is None:
                raise CommandError("There is no such graph.")
        else:
            try:
                doc.table(new.table_id)
            except KeyError:
                raise CommandError("The table of this graph is gone.") from None
        self._old = doc.graphs[index] if index is not None else None
        graphs = list(doc.graphs)
        if self.remove:
            assert index is not None
            del graphs[index]
        elif index is None:
            graphs.append(new)
        else:
            graphs[index] = new
        self._at = index if index is not None else len(graphs) - 1
        doc.graphs = graphs

    def undo(self, doc: Document) -> None:
        graphs = list(doc.graphs)
        if self._old is None:
            del graphs[self._at]
        elif self.remove:
            graphs.insert(self._at, self._old)
        else:
            graphs[self._at] = self._old
        doc.graphs = graphs

    @property
    def summary(self) -> str:
        return self._summary

    @property
    def touched_nodes(self) -> tuple[str, ...]:
        return (self.graph.id,)


def _family(graph: ChartSpec) -> str:
    return "XY" if graph.family is ChartFamily.XY else graph.family.label.lower()


def add_graph(doc: Document, graph: ChartSpec) -> Command:
    """A command that adds ``graph`` to the document."""
    try:
        table = doc.table(graph.table_id).name
    except KeyError:
        table = "?"
    return SetGraph(graph, f"Plot '{table}': {_family(graph)} graph")


def edit_graph(doc: Document, graph: ChartSpec, summary: str = "Format graph") -> Command:
    """A command that replaces the graph with ``graph.id`` by ``graph``."""
    try:
        doc.graph(graph.id)
    except KeyError:
        raise CommandError("There is no such graph.") from None
    return SetGraph(graph, summary)


def remove_graph(doc: Document, graph_id: str) -> Command:
    """A command that removes the graph ``graph_id``."""
    try:
        graph = doc.graph(graph_id)
    except KeyError:
        raise CommandError("There is no such graph.") from None
    return SetGraph(graph, f"Delete {_family(graph)} graph", remove=True)


def set_series_visible(doc: Document, graph_id: str, source: str, visible: bool) -> Command:
    """A command that shows or hides one series of a graph (a legend click)."""
    try:
        graph = doc.graph(graph_id)
        changed = graph.with_series_visible(source, visible)
    except KeyError:
        raise CommandError("There is no such series.") from None
    name = _series_name(doc, graph, source)
    verb = "Show" if visible else "Hide"
    return SetGraph(changed, f"{verb} series '{name}'")


def _series_name(doc: Document, graph: ChartSpec, source: str) -> str:
    try:
        table = doc.table(graph.table_id)
    except KeyError:
        return source
    for group in table.replicate_groups:
        if group.id == source:
            return group.name
    for column in table.columns:
        if column.id == source:
            return column.name
    return source
