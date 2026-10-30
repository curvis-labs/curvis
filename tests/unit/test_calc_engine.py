"""Tests for the CalcEngine: calculated columns recomputed through the scheduler."""

import math
import time
from collections.abc import Iterator

import numpy as np
import pytest
from pytestqt.qtbot import QtBot

from curvis.compute import Scheduler
from curvis.compute.calc_engine import CalcEngine
from curvis.core import (
    AddColumn,
    AddConstant,
    AddTable,
    Calculation,
    Document,
    EditConstant,
    RemoveColumn,
    RenameColumn,
    SetCellBlock,
    SetCellValue,
    add_calculation,
    edit_calculation,
)
from curvis.core.constants import ProjectConstant
from curvis.data import Column, Pairing, Table

NAN = math.nan


class Lab:
    """A table x, a1, a2, b1, b2 and an engine; ``settle`` waits for the results."""

    def __init__(self, scheduler: Scheduler) -> None:
        self.doc = Document()
        self.table = Table(
            "T",
            [
                Column.new("x", "X", [0, 1, 2], id="x"),
                Column.new("a1", "Y", [1, 2, 3], id="a1"),
                Column.new("a2", "Y", [3, NAN, 5], id="a2"),
                Column.new("b1", "Y", [10, 20, 30], id="b1"),
                Column.new("b2", "Y", [40, 50, 60], id="b2"),
            ],
            id="T",
        )
        self.doc.execute(AddTable(self.table))
        self.global_constants: list[ProjectConstant] = []
        self.scheduler = scheduler
        self.engine = CalcEngine(self.doc, scheduler, lambda: self.global_constants)
        self.changed: list[str] = []
        self.engine.values_changed.connect(self.changed.append)

    def add(self, formula: str, pairing: Pairing) -> Calculation:
        self.doc.execute(add_calculation(self.doc, Calculation("T", formula, pairing)))
        self.settle()
        return self.doc.calculations[-1]

    def values(self, calc: Calculation, i: int = 0) -> list[float]:
        return list(self.table.column(calc.columns[i].column_id).values)

    def settle(self, timeout: float = 10) -> None:
        deadline = time.monotonic() + timeout
        while self.engine.busy or self.scheduler.busy:
            assert time.monotonic() < deadline, "recompute did not settle"
            self.scheduler.wait_idle(50)
        self.scheduler.wait_idle()


@pytest.fixture
def scheduler(qtbot: QtBot) -> Iterator[Scheduler]:
    s = Scheduler(debounce_ms=20, max_processes=1)
    yield s
    s.shutdown()


@pytest.fixture
def lab(scheduler: Scheduler) -> Iterator[Lab]:
    harness = Lab(scheduler)
    yield harness
    harness.engine.close()


def nan_equal(a: list[float], b: list[float]) -> bool:
    return bool(np.array_equal(np.array(a), np.array(b), equal_nan=True))


def test_new_calculation_fills_its_columns(lab: Lab) -> None:
    calc = lab.add("{A} * 2 + [x]", Pairing(("a1", "a2")))
    assert lab.values(calc, 0) == [2, 5, 8]
    assert nan_equal(lab.values(calc, 1), [6, NAN, 12])
    assert lab.changed and set(lab.changed) == {"T"}


def test_editing_a_source_cell_updates_the_calculated_column(lab: Lab) -> None:
    calc = lab.add("{A} - FIRST({A})", Pairing(("a1",)))
    assert lab.values(calc) == [0, 1, 2]
    lab.doc.execute(SetCellValue("T", "a1", 0, 0.5))
    lab.settle()
    assert lab.values(calc) == [0, 1.5, 2.5]
    lab.doc.undo()
    lab.settle()
    assert lab.values(calc) == [0, 1, 2]
    lab.doc.redo()
    lab.settle()
    assert lab.values(calc) == [0, 1.5, 2.5]


def test_paired_index_matched_and_mean(lab: Lab) -> None:
    pairing = Pairing(("a1", "a2"), ("b1", "b2"), paired=True)
    matched = lab.add("{B} - {A}", pairing)
    assert lab.values(matched, 0) == [9, 18, 27]
    assert nan_equal(lab.values(matched, 1), [37, NAN, 55])
    mean = lab.add("{B} - {A:mean}", pairing)
    assert lab.values(mean, 0) == [8, 18, 26]  # mean of a1, a2 ignores the missing value
    lab.doc.execute(SetCellValue("T", "a2", 1, 4.0))
    lab.settle()
    assert lab.values(mean, 0) == [8, 17, 26]


def test_chained_calculations_update_in_order(lab: Lab) -> None:
    first = lab.add("{A} + 1", Pairing(("a1",)))
    lab.add("[a1_calc] * 10", Pairing(("b1",)))
    second = lab.doc.calculations[-1]
    assert lab.values(second) == [20, 30, 40]
    lab.doc.execute(SetCellValue("T", "a1", 2, 9.0))
    lab.settle()
    assert lab.values(first) == [2, 3, 10]
    assert lab.values(second) == [20, 30, 100]


def test_new_rows_extend_the_calculated_column(lab: Lab) -> None:
    calc = lab.add("{A} * 2", Pairing(("a1",)))
    lab.doc.execute(SetCellBlock("T", 3, 1, [[4.0]]))
    lab.settle()
    assert lab.values(calc) == [2, 4, 6, 8]


def test_editing_the_formula_recomputes(lab: Lab) -> None:
    calc = lab.add("{A} * 2", Pairing(("a1",)))
    lab.doc.execute(
        edit_calculation(lab.doc, Calculation("T", "{A} * 3", Pairing(("a1",)), id=calc.id))
    )
    lab.settle()
    assert lab.values(lab.doc.calculations[0]) == [3, 6, 9]
    lab.doc.undo()
    lab.settle()
    assert lab.values(calc) == [2, 4, 6]


def test_formula_errors_empty_the_column_and_are_reported(lab: Lab) -> None:
    calc = lab.add("{A} + [Missing]", Pairing(("a1",)))
    output = calc.columns[0].column_id
    assert nan_equal(lab.values(calc), [NAN] * 3)
    assert "Unknown column 'Missing'" in lab.engine.errors[output]
    lab.doc.execute(AddColumn("T", Column.new("Missing", "Y", [1, 1, 1])))
    lab.settle()
    assert lab.values(calc) == [2, 3, 4]
    assert output not in lab.engine.errors


def test_renaming_a_referenced_column_keeps_the_formula_working(lab: Lab) -> None:
    calc = lab.add("{A} + [x]", Pairing(("a1",)))
    lab.doc.execute(RenameColumn("T", "x", "Time"))
    lab.settle()
    assert lab.doc.calculations[0].formula == "{A} + [Time]"
    lab.doc.execute(SetCellValue("T", "x", 0, 100.0))
    lab.settle()
    assert lab.values(calc) == [101, 3, 5]


def test_deleting_a_source_column_is_an_error(lab: Lab) -> None:
    calc = lab.add("{A} * 2", Pairing(("a1",)))
    output = calc.columns[0].column_id
    lab.doc.execute(RemoveColumn("T", "a1"))
    lab.settle()
    assert "deleted" in lab.engine.errors[output]
    lab.doc.undo()
    lab.settle()
    assert lab.values(calc) == [2, 4, 6]


def test_a_formula_reading_its_own_result_is_an_error(lab: Lab) -> None:
    calc = lab.add("[a1_calc] + 1", Pairing(("a1",)))
    assert "own result" in lab.engine.errors[calc.columns[0].column_id]


def test_circular_calculations_are_an_error(lab: Lab) -> None:
    lab.add("{A} + 1", Pairing(("a1",)))  # a1_calc
    lab.add("[a1_calc] + 1", Pairing(("b1",)))  # b1_calc reads a1_calc
    first = lab.doc.calculations[0]
    lab.doc.execute(
        edit_calculation(lab.doc, Calculation("T", "[b1_calc]", Pairing(("a1",)), id=first.id))
    )
    lab.settle()
    errors = list(lab.engine.errors.values())
    assert any("depends on its own result" in e for e in errors)


def test_project_and_global_constants(lab: Lab) -> None:
    calc = lab.add("{A} * k", Pairing(("a1",)))
    output = calc.columns[0].column_id
    assert "Unknown constant 'k'" in lab.engine.errors[output]
    lab.global_constants = [ProjectConstant("k", 2.0)]
    lab.engine.constants_changed(["k"])
    lab.settle()
    assert lab.values(calc) == [2, 4, 6]
    k = ProjectConstant("k", 10.0)
    lab.doc.execute(AddConstant(k))  # the project constant wins
    lab.settle()
    assert lab.values(calc) == [10, 20, 30]
    lab.doc.execute(EditConstant(k.id, "k", 100.0))
    lab.settle()
    assert lab.values(calc) == [100, 200, 300]


def test_opening_a_document_recomputes_its_calculations(scheduler: Scheduler) -> None:
    doc = Document()
    doc.execute(AddTable(Table("T", [Column.new("a", "Y", [1, 2], id="a")], id="T")))
    doc.execute(add_calculation(doc, Calculation("T", "{A} * 5", Pairing(("a",)))))
    engine = CalcEngine(doc, scheduler)
    try:
        while engine.busy or scheduler.busy:
            scheduler.wait_idle(50)
        output = doc.tables[0].column(doc.calculations[0].columns[0].column_id)
        assert list(output.values) == [5, 10]
        assert doc.undo_stack.count == 2  # recomputing is not an undo step
    finally:
        engine.close()
