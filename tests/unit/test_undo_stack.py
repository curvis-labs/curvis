"""Tests for the undo stack."""

import pytest

from curvis.core import (
    UNDO_LIMIT,
    AddTable,
    CommandError,
    Document,
    RenameTable,
    UndoEvent,
)
from curvis.data import Table


def make_doc() -> tuple[Document, Table]:
    doc = Document()
    table = Table("T0")
    doc.execute(AddTable(table))
    doc.undo_stack.clear()
    doc.undo_stack.set_clean()
    return doc, table


def test_limit_is_fifty() -> None:
    assert UNDO_LIMIT == 50
    doc, table = make_doc()
    for i in range(1, 61):
        doc.execute(RenameTable(table.id, f"T{i}"))
    assert doc.undo_stack.count == 50
    undone = 0
    while doc.undo_stack.can_undo:
        doc.undo()
        undone += 1
    assert undone == 50
    assert table.name == "T10"
    doc.undo()  # nothing left: no-op
    assert table.name == "T10"


def test_new_command_discards_redo_tail() -> None:
    doc, table = make_doc()
    doc.execute(RenameTable(table.id, "A"))
    doc.execute(RenameTable(table.id, "B"))
    doc.undo()
    assert doc.undo_stack.can_redo
    doc.execute(RenameTable(table.id, "C"))
    assert not doc.undo_stack.can_redo
    assert doc.undo_stack.count == 2
    doc.redo()  # no-op
    assert table.name == "C"


def test_undo_and_redo_text() -> None:
    doc, table = make_doc()
    assert doc.undo_stack.undo_text == ""
    doc.execute(RenameTable(table.id, "A"))
    assert doc.undo_stack.undo_text == "Rename table 'T0' to 'A'"
    doc.undo()
    assert doc.undo_stack.redo_text == "Rename table 'T0' to 'A'"
    assert doc.undo_stack.undo_text == ""


def test_listeners_are_called_and_can_unsubscribe() -> None:
    doc, table = make_doc()
    events: list[UndoEvent] = []
    unsubscribe = doc.undo_stack.subscribe(events.append)
    command = RenameTable(table.id, "A")
    doc.execute(command)
    doc.undo()
    doc.redo()
    doc.undo_stack.clear()
    assert [(e.kind, e.command) for e in events] == [
        ("do", command),
        ("undo", command),
        ("redo", command),
        ("clear", None),
    ]
    unsubscribe()
    unsubscribe()  # harmless twice
    doc.execute(RenameTable(table.id, "B"))
    assert len(events) == 4


def test_failed_command_is_not_pushed_or_notified() -> None:
    doc, table = make_doc()
    events: list[UndoEvent] = []
    doc.undo_stack.subscribe(events.append)
    with pytest.raises(CommandError):
        doc.execute(RenameTable(table.id, "  "))
    assert doc.undo_stack.count == 0
    assert events == []
    assert table.name == "T0"


def test_clean_state() -> None:
    doc, table = make_doc()
    stack = doc.undo_stack
    assert stack.is_clean
    doc.execute(RenameTable(table.id, "A"))
    assert not stack.is_clean
    stack.set_clean()
    doc.execute(RenameTable(table.id, "B"))
    assert not stack.is_clean
    doc.undo()
    assert stack.is_clean
    doc.undo()
    assert not stack.is_clean
    doc.redo()
    assert stack.is_clean


def test_clean_state_lost_when_redo_tail_is_discarded() -> None:
    doc, table = make_doc()
    doc.execute(RenameTable(table.id, "A"))
    doc.undo_stack.set_clean()
    doc.undo()
    doc.execute(RenameTable(table.id, "B"))
    doc.undo()
    assert not doc.undo_stack.is_clean
    doc.redo()
    assert not doc.undo_stack.is_clean


def test_clean_state_lost_when_dropped_past_limit() -> None:
    doc, table = make_doc()
    doc.undo_stack.set_clean()  # clean at the start
    for i in range(UNDO_LIMIT + 1):
        doc.execute(RenameTable(table.id, f"N{i}"))
    while doc.undo_stack.can_undo:
        doc.undo()
        assert not doc.undo_stack.is_clean


def test_clean_state_kept_when_still_reachable_past_limit() -> None:
    doc, table = make_doc()
    doc.execute(RenameTable(table.id, "A"))
    doc.execute(RenameTable(table.id, "B"))
    doc.undo_stack.set_clean()
    for i in range(UNDO_LIMIT - 1):
        doc.execute(RenameTable(table.id, f"N{i}"))
    for _ in range(UNDO_LIMIT - 1):
        doc.undo()
    assert table.name == "B"
    assert doc.undo_stack.is_clean


def test_clear_keeps_clean_flag() -> None:
    doc, table = make_doc()
    doc.execute(RenameTable(table.id, "A"))
    doc.undo_stack.clear()
    assert not doc.undo_stack.is_clean
    doc.undo_stack.set_clean()
    doc.undo_stack.clear()
    assert doc.undo_stack.is_clean
    assert not doc.undo_stack.can_undo


def test_limit_must_be_positive() -> None:
    from curvis.core import UndoStack

    with pytest.raises(ValueError):
        UndoStack(Document(), limit=0)
