"""Adding, changing and removing graphs; legend clicks are undo steps."""

import dataclasses

import pytest

from curvis.charts.builder import default_spec
from curvis.charts.spec import ChartFamily
from curvis.core import (
    MAX_GRAPH_NAME,
    CommandError,
    Document,
    add_graph,
    edit_graph,
    graph_name,
    graph_names,
    remove_graph,
    rename_graph,
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


def two_bar_graphs() -> tuple[Document, str, str]:
    doc = document()
    table = doc.tables[0]
    first = default_spec(table, ChartFamily.BAR)
    second = default_spec(table, ChartFamily.BAR)
    doc.execute(add_graph(doc, first))
    doc.execute(add_graph(doc, second))
    return doc, first.id, second.id


def test_graphs_are_named_by_family_and_number_until_renamed() -> None:
    doc, first, second = two_bar_graphs()
    assert graph_names(doc, doc.tables[0].id) == {first: "Bar graph", second: "Bar graph 2"}
    doc.execute(rename_graph(doc, first, "  Tumour   volume "))
    assert doc.graph(first).name == "Tumour volume"
    # a renamed graph still counts: the other keeps its name
    assert graph_names(doc, doc.tables[0].id) == {first: "Tumour volume", second: "Bar graph 2"}
    assert graph_name(doc, first) == "Tumour volume"
    assert doc.change_log.entries[-1].summary == "Rename graph 'Bar graph' to 'Tumour volume'"


def test_rename_is_an_undo_step_and_a_blank_name_restores_the_default() -> None:
    doc, first, _ = two_bar_graphs()
    doc.execute(rename_graph(doc, first, "Day 7"))
    doc.undo()
    assert doc.graph(first).name == ""
    doc.redo()
    assert doc.graph(first).name == "Day 7"
    doc.execute(rename_graph(doc, first, "   "))
    assert doc.graph(first).name == "" and graph_name(doc, first) == "Bar graph"
    doc.execute(rename_graph(doc, first, "Day 7"))
    doc.execute(rename_graph(doc, first, "Bar graph"))  # the default name: stored as ""
    assert doc.graph(first).name == ""


def test_a_name_another_graph_of_the_table_has_is_refused() -> None:
    doc, first, second = two_bar_graphs()
    with pytest.raises(CommandError, match="already a graph called 'Bar graph 2'"):
        rename_graph(doc, first, "bar GRAPH 2")
    doc.execute(rename_graph(doc, second, "Day 7"))
    with pytest.raises(CommandError, match="already a graph called 'Day 7'"):
        rename_graph(doc, first, "day 7")
    with pytest.raises(CommandError, match="at most"):
        rename_graph(doc, first, "x" * (MAX_GRAPH_NAME + 1))
    with pytest.raises(CommandError, match="already has this name"):
        rename_graph(doc, second, "Day 7")
    with pytest.raises(CommandError, match="no such graph"):
        rename_graph(doc, "nope", "Day 8")
    # graphs of another table may share a name
    other = dataclasses.replace(replicate_table(), name="Other", id="other-table")
    doc.tables.append(other)
    spec = default_spec(other, ChartFamily.BAR)
    doc.execute(add_graph(doc, spec))
    doc.execute(rename_graph(doc, spec.id, "Day 7"))
    assert graph_name(doc, spec.id) == "Day 7"
