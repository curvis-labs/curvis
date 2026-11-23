"""Recovery copies on disk: writing, deleting, sessions and what a scan offers."""

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from curvis.core import Document
from curvis.core.commands import RenameTable, SetCellValue
from curvis.data import Table
from curvis.io import project
from curvis.io.recovery import CopyInfo, RecoveryStore, snapshot

WHEN = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


def store_in(folder: Path, session: str | None = None) -> RecoveryStore:
    store = RecoveryStore(folder) if session is None else RecoveryStore(folder, session)
    store.start()
    return store


def info(name: str = "Assay", path: Path | None = None, mtime: int | None = None) -> CopyInfo:
    return CopyInfo(name, path, WHEN, mtime)


def edited_document() -> Document:
    doc = Document(name="Assay", tables=[Table.blank()])
    table = doc.tables[0]
    doc.execute(SetCellValue(table.id, table.columns[1].id, 0, 4.5))
    return doc


def write(store: RecoveryStore, copy_id: str, doc: Document, meta: CopyInfo | None = None) -> bool:
    return store.write(copy_id, store.begin_write(copy_id), snapshot(doc), meta or info())


def files(folder: Path) -> list[str]:
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*"))


def test_a_copy_is_an_ordinary_project_with_a_sidecar(tmp_path: Path) -> None:
    store = store_in(tmp_path)
    doc = edited_document()
    copy_id = store.new_copy_id()
    original = tmp_path / "work" / "assay.cvp"
    assert write(store, copy_id, doc, info(path=original, mtime=123))
    loaded = project.load(store.copy_path(copy_id))
    assert loaded.tables == doc.tables
    assert [e.summary for e in loaded.change_log] == [e.summary for e in doc.change_log]
    side = json.loads(store.sidecar_path(copy_id).read_text("utf-8"))
    assert side["original_path"] == str(original)
    assert side["name"] == "Assay"
    assert side["original_mtime_ns"] == 123
    assert datetime.fromisoformat(side["copied_at"]) == WHEN
    assert store.copy_path(copy_id).is_relative_to(tmp_path)
    store.close()


def test_a_clean_close_leaves_the_folder_empty(tmp_path: Path) -> None:
    store = store_in(tmp_path)
    assert files(tmp_path) == [f"{store.session}.lock"]
    write(store, store.new_copy_id(), edited_document())
    write(store, store.new_copy_id(), edited_document())
    store.close()
    assert files(tmp_path) == []


def test_delete_removes_the_copy_and_drops_a_write_that_started_before(tmp_path: Path) -> None:
    store = store_in(tmp_path)
    copy_id = store.new_copy_id()
    write(store, copy_id, edited_document())
    ticket = store.begin_write(copy_id)  # a write that is still running
    store.delete(copy_id)
    assert not store.copy_path(copy_id).exists()
    assert not store.sidecar_path(copy_id).exists()
    assert not store.write(copy_id, ticket, snapshot(edited_document()), info())
    assert not store.copy_path(copy_id).exists()
    assert files(store.session_folder) == []  # no temporary file left either
    store.close()


def test_an_older_write_never_replaces_a_newer_copy(tmp_path: Path) -> None:
    store = store_in(tmp_path)
    copy_id = store.new_copy_id()
    old_doc, new_doc = edited_document(), edited_document()
    new_doc.execute(RenameTable(new_doc.tables[0].id, "Newer"))
    old_ticket = store.begin_write(copy_id)
    new_ticket = store.begin_write(copy_id)
    assert store.write(copy_id, new_ticket, snapshot(new_doc), info())
    assert not store.write(copy_id, old_ticket, snapshot(old_doc), info())
    assert project.load(store.copy_path(copy_id)).tables[0].name == "Newer"
    store.close()


def test_a_failed_write_keeps_the_previous_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = store_in(tmp_path)
    copy_id = store.new_copy_id()
    write(store, copy_id, edited_document())
    before = store.copy_path(copy_id).read_bytes()

    def broken(document: Document, f: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(project, "_write_project", broken)
    with pytest.raises(OSError):
        write(store, copy_id, edited_document())
    assert store.copy_path(copy_id).read_bytes() == before
    assert files(store.session_folder) == sorted(
        [store.copy_path(copy_id).name, store.sidecar_path(copy_id).name]
    )
    store.close()


def test_copies_of_a_running_session_are_not_offered(tmp_path: Path) -> None:
    first = store_in(tmp_path, "aaaa")
    write(first, first.new_copy_id(), edited_document())
    second = store_in(tmp_path, "bbbb")  # a second Curvis window
    assert second.scan() == []
    first.close()
    second.close()


def test_copies_of_a_session_whose_lock_is_gone_are_offered(tmp_path: Path) -> None:
    crashed = store_in(tmp_path, "aaaa")
    copy_id = crashed.new_copy_id()
    original = tmp_path / "assay.cvp"
    write(crashed, copy_id, edited_document(), info(path=original, mtime=7))
    crashed._session_lock.unlock()  # what a killed process leaves: copies, no live lock

    store = store_in(tmp_path, "bbbb")
    [found] = store.scan()
    assert found.recoverable
    assert found.copy_id == copy_id
    assert found.info == info(path=original, mtime=7)
    assert found.name == "Assay"
    assert project.load(found.project_path).tables[0].columns[1].values[0] == 4.5
    store.close()
    assert (tmp_path / "aaaa").is_dir()  # kept until opened or discarded
    later = store_in(tmp_path, "cccc")
    assert [c.copy_id for c in later.scan()] == [copy_id]
    later.close()


def crashed_copy(tmp_path: Path, meta: CopyInfo | None = None) -> tuple[str, Document]:
    crashed = store_in(tmp_path, "dead")
    copy_id = crashed.new_copy_id()
    doc = edited_document()
    write(crashed, copy_id, doc, meta)
    crashed._session_lock.unlock()
    return copy_id, doc


def test_claim_moves_the_copy_into_the_running_session(tmp_path: Path) -> None:
    _, doc = crashed_copy(tmp_path)
    store = store_in(tmp_path, "live")
    [found] = store.scan()
    new_id = store.claim(found)
    assert not (tmp_path / "dead").exists()
    assert project.load(store.copy_path(new_id)).tables == doc.tables
    assert store.has_copy(new_id)
    assert store.scan() == []
    store.forget(new_id)  # its document is closed
    assert files(tmp_path) == ["live", "live.lock"]
    store.close()
    assert files(tmp_path) == []


def test_discard_deletes_a_recovered_copy(tmp_path: Path) -> None:
    crashed_copy(tmp_path)
    store = store_in(tmp_path, "live")
    [found] = store.scan()
    store.discard(found)
    assert store.scan() == []
    store.close()
    assert files(tmp_path) == []


@pytest.mark.parametrize("damage", ["project", "sidecar", "no project", "no sidecar"])
def test_a_damaged_copy_is_listed_as_not_recoverable(tmp_path: Path, damage: str) -> None:
    copy_id, _doc = crashed_copy(tmp_path)
    folder = tmp_path / "dead"
    cvp, side = folder / f"{copy_id}.cvp", folder / f"{copy_id}.json"
    if damage == "project":
        cvp.write_bytes(b"not a zip at all")
    elif damage == "sidecar":
        side.write_text("{ broken", "utf-8")
    elif damage == "no project":
        cvp.unlink()
    else:
        side.unlink()
    store = store_in(tmp_path, "live")
    [found] = store.scan()
    assert not found.recoverable
    # The name is kept when only the project file is damaged.
    assert found.name == ("Unknown project" if "sidecar" in damage else "Assay")
    store.discard(found)
    assert store.scan() == []
    assert not folder.exists()
    store.close()


def test_scan_never_raises_on_odd_contents(tmp_path: Path) -> None:
    (tmp_path / "stray.txt").write_text("hello", "utf-8")
    (tmp_path / "empty-folder").mkdir()
    (tmp_path / "old.lock").write_text("garbage", "utf-8")
    store = store_in(tmp_path, "live")
    assert store.scan() == []
    assert not (tmp_path / "empty-folder").exists()
    store.close()


def test_a_missing_recovery_folder_scans_empty(tmp_path: Path) -> None:
    store = RecoveryStore(tmp_path / "nowhere")
    assert store.scan() == []


def test_newest_copies_are_listed_first(tmp_path: Path) -> None:
    crashed = store_in(tmp_path, "dead")
    for hour in (9, 11, 10):
        when = datetime(2026, 11, 23, hour, tzinfo=UTC)
        write(crashed, f"c{hour}", edited_document(), CopyInfo(f"P{hour}", None, when, None))
    crashed._session_lock.unlock()
    store = store_in(tmp_path, "live")
    assert [c.name for c in store.scan()] == ["P11", "P10", "P9"]
    store.close()


def test_snapshot_does_not_change_when_the_document_does() -> None:
    doc = edited_document()
    table = doc.tables[0]
    copy = snapshot(doc)
    doc.execute(SetCellValue(table.id, table.columns[1].id, 0, 99.0))
    doc.execute(RenameTable(table.id, "Changed"))
    assert copy.tables[0].columns[1].values[0] == 4.5
    assert copy.tables[0].name == "Data 1"
    assert len(copy.change_log) == 1
    assert not copy.tables[0].columns[1].values.flags.writeable
    assert copy.undo_stack.count == 0


def test_snapshot_writes_the_same_project_as_the_document(tmp_path: Path) -> None:
    from curvis.samples import sample

    for key in ("group_comparison", "dose_response", "xy_replicates"):
        doc = sample(key).build()
        project.save(doc, tmp_path / "a.cvp")
        project.save(snapshot(doc), tmp_path / "b.cvp")
        assert (tmp_path / "a.cvp").read_bytes() == (tmp_path / "b.cvp").read_bytes()


def test_writes_and_deletes_from_two_threads_leave_a_consistent_folder(tmp_path: Path) -> None:
    store = store_in(tmp_path)
    copy_id = store.new_copy_id()
    doc = snapshot(edited_document())
    stop = threading.Event()

    def writer() -> None:
        while not stop.is_set():
            store.write(copy_id, store.begin_write(copy_id), doc, info())

    thread = threading.Thread(target=writer)
    thread.start()
    for _ in range(50):
        store.delete(copy_id)
    stop.set()
    thread.join()
    store.delete(copy_id)
    assert files(store.session_folder) == []
    store.close()
    assert not np.isnan(doc.tables[0].columns[1].values[0])
