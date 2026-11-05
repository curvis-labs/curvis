"""Several tables per project: removing a table with what belongs to it, moving tables,
naming new tables, and a project file with three tables (step 2.7)."""

import copy
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from curvis.analysis.base import Analysis
from curvis.analysis.fit.analyses import LinearParams
from curvis.charts.builder import default_spec
from curvis.charts.spec import ChartFamily
from curvis.compute import Scheduler
from curvis.core import (
    LAST_TABLE,
    AddTable,
    CommandError,
    Document,
    MoveTable,
    RemoveTable,
    RenameTable,
    add_analysis,
    add_calculation,
    add_graph,
    new_table_name,
)
from curvis.core.calculations import Calculation
from curvis.data import Column, Pairing, Table
from curvis.io.project import load, save
from tests.unit.test_graph_engine import Lab
from tests.unit.test_project_io import assert_identical


def assay(name: str, table_id: str) -> Table:
    return Table(
        name,
        [
            Column.new("x", "X", [1, 2, 3, 4], id=f"{table_id}x"),
            Column.new("y", "Y", [2, 4, 7, 8], id=f"{table_id}y"),
        ],
        id=table_id,
    )


def three_tables() -> Document:
    """Tables A, B and C; B has a calculated column, a linear fit and an XY graph, and C
    a graph of its own."""
    doc = Document()
    for name, table_id in (("A", "tA"), ("B", "tB"), ("C", "tC")):
        doc.execute(AddTable(assay(name, table_id)))
    doc.execute(add_calculation(doc, Calculation("tB", "{A} * 2", Pairing(("tBy",)))))
    fit = Analysis("linear_regression", "tB", ("tBy",), LinearParams(), id="fitB")
    doc.execute(add_analysis(doc, fit))
    doc.execute(add_graph(doc, default_spec(doc.table("tB"), ChartFamily.XY, doc.analyses)))
    doc.execute(add_graph(doc, default_spec(doc.table("tC"), ChartFamily.XY)))
    doc.undo_stack.clear()
    return doc


def snapshot(doc: Document) -> tuple[object, ...]:
    return copy.deepcopy((doc.tables, doc.calculations, doc.analyses, doc.graphs))


def test_remove_table_takes_its_analyses_graphs_and_calculations() -> None:
    doc = three_tables()
    others = [g for g in doc.graphs if g.table_id == "tC"]
    command = RemoveTable("tB")
    doc.execute(command)
    assert [t.name for t in doc.tables] == ["A", "C"]
    assert doc.analyses == [] and doc.calculations == []
    assert doc.graphs == others
    assert command.summary == "Delete table 'B' with 1 analysis and 1 graph"
    assert doc.change_log.entries[-1].summary == command.summary
    assert doc.change_log.entries[-1].command_type == "RemoveTable"
    assert doc.undo_stack.count == 1  # one undo step


def test_undo_restores_the_table_and_everything_with_it() -> None:
    doc = three_tables()
    before = snapshot(doc)
    doc.execute(RemoveTable("tB"))
    after = snapshot(doc)
    doc.undo()
    assert snapshot(doc) == before
    assert doc.change_log.entries[-1].summary.startswith("Undo: Delete table 'B'")
    doc.redo()
    assert snapshot(doc) == after


def test_summary_of_a_table_without_analyses_or_graphs() -> None:
    doc = three_tables()
    command = RemoveTable("tA")
    doc.execute(command)
    assert command.summary == "Delete table 'A'"
    command = RemoveTable("tC")
    doc.execute(command)
    assert command.summary == "Delete table 'C' with 1 graph"


def test_the_last_table_cannot_be_removed() -> None:
    doc = Document(tables=[Table.blank()])
    with pytest.raises(CommandError, match=LAST_TABLE):
        doc.execute(RemoveTable(doc.tables[0].id))
    assert len(doc.tables) == 1 and doc.undo_stack.count == 0
    assert LAST_TABLE == "A project needs at least one table."


def test_move_table() -> None:
    doc = three_tables()
    command = MoveTable("tA", 2)
    doc.execute(command)
    assert [t.name for t in doc.tables] == ["B", "C", "A"]
    assert command.summary == "Move table 'A'"
    doc.undo()
    assert [t.name for t in doc.tables] == ["A", "B", "C"]
    doc.redo()
    assert [t.name for t in doc.tables] == ["B", "C", "A"]
    doc.execute(MoveTable("tA", 0))
    assert [t.name for t in doc.tables] == ["A", "B", "C"]


def test_move_table_rejects_bad_input() -> None:
    doc = three_tables()
    with pytest.raises(CommandError, match="out of range"):
        doc.execute(MoveTable("tA", 3))
    with pytest.raises(CommandError, match="no table"):
        doc.execute(MoveTable("nope", 0))
    assert [t.name for t in doc.tables] == ["A", "B", "C"]


def test_new_table_names_use_the_first_free_number() -> None:
    doc = Document(tables=[Table.blank()])
    assert new_table_name(doc) == "Table 2"
    doc.execute(AddTable(Table(new_table_name(doc))))
    doc.execute(AddTable(Table(new_table_name(doc))))
    assert [t.name for t in doc.tables] == ["Data 1", "Table 2", "Table 3"]
    doc.execute(RemoveTable(doc.tables[1].id))
    assert new_table_name(doc) == "Table 2"
    doc.execute(RenameTable(doc.tables[0].id, "table 4"))
    doc.execute(AddTable(Table(new_table_name(doc))))
    assert new_table_name(doc) == "Table 5"


def test_three_tables_save_and_open_unchanged(tmp_path: Path) -> None:
    doc = three_tables()
    doc.execute(MoveTable("tC", 0))
    path = tmp_path / "three.cvp"
    save(doc, path)
    loaded = load(path)
    assert [t.name for t in loaded.tables] == ["C", "A", "B"]
    assert_identical(doc, loaded)


def test_a_project_without_tables_opens_with_one_blank_table(tmp_path: Path) -> None:
    path = tmp_path / "empty.cvp"
    save(Document(name="Empty"), path)
    loaded = load(path)
    assert len(loaded.tables) == 1
    blank = Table.blank()
    assert [(c.name, c.role) for c in loaded.tables[0].columns] == [
        (c.name, c.role) for c in blank.columns
    ]
    assert loaded.undo_stack.is_clean


# --- dependency graph ----------------------------------------------------------------------


@pytest.fixture
def lab(qtbot: QtBot) -> Iterator[Lab]:
    scheduler = Scheduler(debounce_ms=20, max_processes=1)
    harness = Lab(scheduler)
    yield harness
    harness.engine.close()
    harness.analyses.close()
    harness.calc.close()
    scheduler.shutdown()


def test_removing_a_table_drops_its_nodes_and_undo_brings_them_back(lab: Lab) -> None:
    lab.doc.execute(AddTable(assay("Other", "tO")))
    fit = Analysis("linear_regression", "T", ("b",), LinearParams())
    lab.doc.execute(add_analysis(lab.doc, fit))
    lab.settle()
    graph = lab.plot()
    assert fit.id in lab.calc.graph and graph in lab.calc.graph
    lab.doc.execute(RemoveTable("T"))
    lab.settle()
    assert fit.id not in lab.calc.graph and graph not in lab.calc.graph
    assert graph not in lab.engine.data and fit.id not in lab.analyses.results
    lab.doc.undo()
    lab.settle()
    assert fit.id in lab.calc.graph and graph in lab.calc.graph
    assert lab.data(graph).curves  # redrawn with its fit
    assert fit.id in lab.analyses.results
