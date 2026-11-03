"""Commands that add, edit and remove analyses: undoable, logged, validated."""

import dataclasses

import pytest

from curvis.analysis.base import Analysis
from curvis.analysis.descriptive import DescriptiveParams
from curvis.core import (
    AddTable,
    CommandError,
    Document,
    add_analysis,
    edit_analysis,
    remove_analysis,
)
from curvis.data import Column, Table


def document() -> Document:
    doc = Document()
    doc.execute(AddTable(Table("Data 1", [Column.new("a", "Y", [1.0], id="a")], id="T")))
    return doc


def analysis(**changes: object) -> Analysis:
    a = Analysis("descriptive_statistics", "T", ("a",), DescriptiveParams(), id="A1")
    return dataclasses.replace(a, **changes)  # type: ignore[arg-type]


def test_add_edit_remove_with_undo_and_change_log() -> None:
    doc = document()
    doc.execute(add_analysis(doc, analysis()))
    assert doc.analyses_of("T") == [analysis()]
    assert doc.change_log[-1].summary == "Analyze 'Data 1': descriptive statistics"
    assert doc.change_log[-1].command_type == "SetAnalysis"

    edited = analysis(params=DescriptiveParams(confidence_interval=False))
    doc.execute(edit_analysis(doc, edited))
    assert doc.analysis("A1") == edited
    doc.undo()
    assert doc.analysis("A1") == analysis()
    doc.redo()
    assert doc.analysis("A1") == edited

    doc.execute(remove_analysis(doc, "A1"))
    assert doc.analyses == []
    assert doc.change_log[-1].summary == "Delete analysis (descriptive statistics)"
    doc.undo()
    assert doc.analyses == [edited]


def test_touched_nodes_name_the_analysis() -> None:
    doc = document()
    command = add_analysis(doc, analysis())
    doc.execute(command)
    assert command.touched_nodes == ("A1",)


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (analysis(table_id="gone"), "table of this analysis is gone"),
        (analysis(type="nothing"), "no analysis of type"),
        (analysis(inputs=()), "Tick at least one series."),
        (analysis(params=object()), "parameters do not belong"),
    ],
)
def test_invalid_analyses_are_refused(bad: Analysis, message: str) -> None:
    doc = document()
    with pytest.raises(CommandError, match=message):
        doc.execute(add_analysis(doc, bad))
    assert doc.analyses == []


def test_editing_or_removing_a_missing_analysis_is_refused() -> None:
    doc = document()
    with pytest.raises(CommandError, match="no such analysis"):
        edit_analysis(doc, analysis())
    with pytest.raises(CommandError, match="no such analysis"):
        remove_analysis(doc, "A1")
