"""Tests for the change log."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from curvis.core import AddTable, ChangeLog, ChangeLogEntry, Document, RenameTable
from curvis.data import Table


def test_entries_are_appended_for_do_undo_and_redo() -> None:
    doc = Document()
    table = Table("T")
    doc.execute(AddTable(table))
    doc.execute(RenameTable(table.id, "Data"))
    doc.undo()
    doc.redo()
    assert [(e.command_type, e.summary) for e in doc.change_log] == [
        ("AddTable", "Add table 'T'"),
        ("RenameTable", "Rename table 'T' to 'Data'"),
        ("RenameTable", "Undo: Rename table 'T' to 'Data'"),
        ("RenameTable", "Redo: Rename table 'T' to 'Data'"),
    ]


def test_timestamps_are_utc() -> None:
    doc = Document()
    before = datetime.now(UTC)
    doc.execute(AddTable(Table("T")))
    entry = doc.change_log[0]
    assert entry.timestamp.utcoffset() == timedelta(0)
    assert before <= entry.timestamp <= datetime.now(UTC)


def test_clock_is_injectable() -> None:
    doc = Document()
    fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    doc.undo_stack.clock = lambda: fixed
    doc.execute(AddTable(Table("T")))
    assert doc.change_log[0].timestamp == fixed


def test_rejected_command_is_not_logged() -> None:
    doc = Document()
    doc.execute(AddTable(Table("T")))
    with pytest.raises(ValueError):
        doc.execute(AddTable(Table("T")))
    assert len(doc.change_log) == 1


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ChangeLogEntry(datetime(2026, 1, 1), "X", "x")


def test_log_is_append_only() -> None:
    log = ChangeLog()
    entry = ChangeLogEntry(datetime.now(timezone.utc), "X", "x")  # noqa: UP017
    log.append(entry)
    assert log.entries == (entry,)
    assert list(log) == [entry]
    assert not hasattr(log, "remove") and not hasattr(log, "clear")
    with pytest.raises(TypeError):
        del log[0]  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        entry.summary = "changed"  # type: ignore[misc]
