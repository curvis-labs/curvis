"""Adding, changing and removing graphs; legend clicks are undo steps."""

import pytest

from curvis.charts.builder import default_spec
from curvis.charts.spec import ChartFamily
from curvis.core import (
    CommandError,
    Document,
    add_graph,
    edit_graph,
    remove_graph,
    set_series_visible,
)
from tests.unit.charts_tables import replicate_table


def document() -> Document:
    return Document(tables=[replicate_table()])


def test_add_undo_redo() -> None:
    doc = document()
    table = doc.tables[0]
    spec = default_spec(table, ChartFamily.XY)
    doc.execute(add_graph(doc, spec))
    assert doc.graphs == [spec] and doc.graphs_of(table.id) == [spec]
    assert doc.change_log.entries[-1].summary == "Plot 'Assay': XY graph"
    doc.undo()
    assert doc.graphs == []
    doc.redo()
    assert doc.graph(spec.id) == spec


def test_legend_toggle_is_an_undo_step() -> None:
    doc = document()
    table = doc.tables[0]
    spec = default_spec(table, ChartFamily.XY)
    doc.execute(add_graph(doc, spec))
    control = table.replicate_groups[0]
    command = set_series_visible(doc, spec.id, control.id, False)
    assert command.summary == "Hide series 'Control'"
    assert command.touched_nodes == (spec.id,)
    doc.execute(command)
    assert doc.graph(spec.id).series_for(control.id).visible is False  # type: ignore[union-attr]
    doc.undo()
    assert doc.graph(spec.id).series_for(control.id).visible is True  # type: ignore[union-attr]
    with pytest.raises(CommandError, match="no such series"):
        set_series_visible(doc, spec.id, "nope", False)


def test_edit_and_remove() -> None:
    doc = document()
    spec = default_spec(doc.tables[0], ChartFamily.BAR)
    doc.execute(add_graph(doc, spec))
    other = default_spec(doc.tables[0], ChartFamily.XY)
    doc.execute(add_graph(doc, other))
    renamed = spec.__class__(**{**spec.__dict__, "title": "New"})
    doc.execute(edit_graph(doc, renamed))
    assert doc.graphs[0].title == "New"
    doc.execute(remove_graph(doc, spec.id))
    assert doc.graphs == [other]
    assert doc.change_log.entries[-1].summary == "Delete bar graph"
    doc.undo()
    assert [g.id for g in doc.graphs] == [spec.id, other.id]  # back in its place
    with pytest.raises(CommandError):
        remove_graph(doc, "nope")
    with pytest.raises(CommandError):
        edit_graph(doc, default_spec(doc.tables[0], ChartFamily.XY))


def test_graph_of_a_missing_table_is_refused() -> None:
    doc = document()
    spec = default_spec(replicate_table(), ChartFamily.XY)
    with pytest.raises(CommandError, match="table of this graph"):
        doc.execute(add_graph(doc, spec))
    assert doc.graphs == []
