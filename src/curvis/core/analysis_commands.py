"""Commands for analyses: add, change and remove an analysis definition.

The commands store only the definition (type, source table, inputs, parameters). The
results are derived: the analysis engine (:mod:`curvis.compute.analysis_engine`)
computes them after the command and again whenever the inputs change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from curvis.analysis.base import Analysis
from curvis.core.commands import Command, CommandError

if TYPE_CHECKING:
    from curvis.charts.spec import ChartSpec
    from curvis.core.document import Document


class SetAnalysis(Command):
    """Store ``analysis`` (adding it, or replacing the one with the same id).

    With ``remove=True`` the analysis with that id is removed instead.
    """

    def __init__(self, analysis: Analysis, summary: str, remove: bool = False) -> None:
        self.analysis = analysis
        self.remove = remove
        self._summary = summary
        self._old: Analysis | None = None
        self._at = 0

    def do(self, doc: Document) -> None:
        new = self.analysis
        index = next((i for i, a in enumerate(doc.analyses) if a.id == new.id), None)
        if self.remove:
            if index is None:
                raise CommandError("There is no such analysis.")
        else:
            _check_analysis(doc, new)
        self._old = doc.analyses[index] if index is not None else None
        analyses = list(doc.analyses)
        if self.remove:
            assert index is not None
            del analyses[index]
        elif index is None:
            analyses.append(new)
        else:
            analyses[index] = new
        self._at = index if index is not None else len(analyses) - 1
        doc.analyses = analyses

    def undo(self, doc: Document) -> None:
        analyses = list(doc.analyses)
        if self._old is None:
            del analyses[self._at]
        elif self.remove:
            analyses.insert(self._at, self._old)
        else:
            analyses[self._at] = self._old
        doc.analyses = analyses

    @property
    def summary(self) -> str:
        return self._summary

    @property
    def touched_nodes(self) -> tuple[str, ...]:
        return (self.analysis.id,)


def _check_analysis(doc: Document, analysis: Analysis) -> None:
    try:
        table = doc.table(analysis.table_id)
    except KeyError:
        raise CommandError("The table of this analysis is gone.") from None
    try:
        kind = analysis.analysis_type
    except KeyError:
        raise CommandError(f"There is no analysis of type {analysis.type!r}.") from None
    if not isinstance(analysis.params, kind.params_type):
        raise CommandError(f"The parameters do not belong to {kind.name}.")
    problem = kind.check(table, analysis.inputs, analysis.params)
    if problem is not None:
        raise CommandError(problem)


def add_analysis(doc: Document, analysis: Analysis) -> Command:
    """A command that adds ``analysis`` to the document."""
    try:
        name = analysis.analysis_type.name
        table = doc.table(analysis.table_id).name
    except KeyError:
        name, table = "analysis", "?"
    return SetAnalysis(analysis, f"Analyze '{table}': {name.lower()}")


def edit_analysis(doc: Document, analysis: Analysis) -> Command:
    """A command that replaces the analysis with ``analysis.id`` by ``analysis``."""
    try:
        doc.analysis(analysis.id)
    except KeyError:
        raise CommandError("There is no such analysis.") from None
    try:
        name = analysis.analysis_type.name
    except KeyError:
        name = "analysis"
    return SetAnalysis(analysis, f"Edit analysis ({name.lower()})")


def remove_analysis(doc: Document, analysis_id: str) -> Command:
    """A command that removes the analysis ``analysis_id``, and its fitted curves from
    the graphs that show them, as one undo step."""
    from dataclasses import replace

    from curvis.core.commands import CommandGroup
    from curvis.core.graph_commands import SetGraph

    try:
        analysis = doc.analysis(analysis_id)
    except KeyError:
        raise CommandError("There is no such analysis.") from None
    try:
        name = analysis.analysis_type.name
    except KeyError:
        name = "analysis"
    summary = f"Delete analysis ({name.lower()})"
    command: Command = SetAnalysis(analysis, summary, remove=True)
    overlaid = overlaid_on(doc, analysis_id)
    if not overlaid:
        return command
    graphs = [
        SetGraph(
            replace(g, overlays=tuple(o for o in g.overlays if o.analysis_id != analysis_id)),
            "Remove fit",
        )
        for g in overlaid
    ]
    return CommandGroup(summary, [*graphs, command])


def overlaid_on(doc: Document, analysis_id: str) -> list[ChartSpec]:
    """The graphs that draw the fitted curves of ``analysis_id``."""
    return [g for g in doc.graphs if any(o.analysis_id == analysis_id for o in g.overlays)]
