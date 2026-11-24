"""Recovery copies: autosaved copies of open projects, offered again after a crash.

Every running Curvis session holds a lock file in the recovery folder
(``<app data>/recovery``, :func:`default_directory`) and keeps its copies in a folder of
its own next to it::

    recovery/
      <session>.lock        held while the session runs (QLockFile)
      <session>/
        <copy>.cvp          an ordinary project file, written like any save
        <copy>.json         the sidecar: original path, name, time of the copy, ...

A copy is written to a temporary file first and renamed over the old copy, so a crash
during a write leaves the previous copy intact. Copies are only ever written inside the
recovery folder, so a recovery copy never overwrites a project file.

At start-up :meth:`RecoveryStore.scan` lists the copies of sessions that are no longer
running (their lock is stale or gone). Copies of a session that is still running (a
second Curvis window) are not listed. A listed copy is kept until the user opens it
(:meth:`RecoveryStore.claim` moves it into the running session) or discards it; nothing
removes copies automatically. A clean exit (:meth:`RecoveryStore.close`) removes the
session's copies, its folder and its lock.

Writes run on a worker thread (:func:`write_copy`), deletes on the main thread. A lock
and a ticket per copy keep them in order: a write only renames its file into place if
no newer write of the same copy has started and the copy has not been deleted since.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from curvis.core.calculations import Calculation
from curvis.core.change_log import ChangeLog
from curvis.core.constants import ProjectConstant
from curvis.core.document import Document
from curvis.data import CategoryGroup, Column, ReplicateGroup, Table

SIDECAR_FORMAT = "curvis-recovery"
SIDECAR_VERSION = 1
COPY_SUFFIX = ".cvp"
SIDECAR_SUFFIX = ".json"
LOCK_SUFFIX = ".lock"
UNKNOWN_NAME = "Unknown project"
"""The name shown for a copy whose sidecar cannot be read."""


def default_directory() -> Path:
    """``<app data>/recovery``, e.g. ``~/.local/share/Curvis/Curvis/recovery`` on Linux."""
    from PySide6.QtCore import QStandardPaths

    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    return Path(base) / "recovery"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class CopyInfo:
    """What the sidecar of a copy records."""

    name: str
    """The project's name as its tab shows it (file name without extension, or the
    document name of a project that was never saved)."""
    original_path: Path | None
    """The project file; ``None`` for a project that was never saved."""
    copied_at: datetime
    """When the copy was written (UTC)."""
    original_mtime_ns: int | None
    """The project file's modification time when the document was last opened or saved;
    ``None`` when it has no file."""

    def to_json(self, session: str) -> dict[str, Any]:
        return {
            "format": SIDECAR_FORMAT,
            "version": SIDECAR_VERSION,
            "session": session,
            "name": self.name,
            "original_path": None if self.original_path is None else str(self.original_path),
            "copied_at": self.copied_at.astimezone(UTC).isoformat(),
            "original_mtime_ns": self.original_mtime_ns,
        }

    @classmethod
    def from_json(cls, data: Any) -> CopyInfo:
        """Raises ``ValueError`` if ``data`` is not a sidecar this build understands."""
        if not isinstance(data, dict) or data.get("format") != SIDECAR_FORMAT:
            raise ValueError("not a recovery sidecar")
        version = data.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError("bad sidecar version")
        if version > SIDECAR_VERSION:
            raise ValueError("sidecar written by a newer version of Curvis")
        name = data.get("name")
        original = data.get("original_path")
        copied = data.get("copied_at")
        mtime = data.get("original_mtime_ns")
        if not isinstance(name, str) or not isinstance(copied, str):
            raise ValueError("bad sidecar")
        if original is not None and not isinstance(original, str):
            raise ValueError("bad sidecar path")
        if mtime is not None and (not isinstance(mtime, int) or isinstance(mtime, bool)):
            raise ValueError("bad sidecar modification time")
        copied_at = datetime.fromisoformat(copied)
        if copied_at.utcoffset() is None:
            copied_at = copied_at.replace(tzinfo=UTC)
        return cls(name, None if original is None else Path(original), copied_at, mtime)


@dataclass(frozen=True)
class RecoveredCopy:
    """A copy left by a session that did not close normally.

    A copy whose sidecar or project file is missing or damaged cannot be recovered
    (``recoverable`` is False) and can only be discarded; ``info`` is still there when
    only the project file is damaged.
    """

    folder: Path
    copy_id: str
    info: CopyInfo | None
    readable: bool = True
    """False when the project file is missing, damaged, or failed to open."""

    @property
    def project_path(self) -> Path:
        return self.folder / f"{self.copy_id}{COPY_SUFFIX}"

    @property
    def sidecar_path(self) -> Path:
        return self.folder / f"{self.copy_id}{SIDECAR_SUFFIX}"

    @property
    def recoverable(self) -> bool:
        return self.info is not None and self.readable

    @property
    def name(self) -> str:
        return self.info.name if self.info is not None else UNKNOWN_NAME

    def damaged(self) -> RecoveredCopy:
        """This copy, marked as one that cannot be read."""
        return RecoveredCopy(self.folder, self.copy_id, self.info, readable=False)


@dataclass
class _CopyState:
    ticket: int = 0
    """The newest write started for the copy; older writes do not rename into place."""
    exists: bool = False


@dataclass
class RecoveryStore:
    """The recovery folder, seen from one running session."""

    directory: Path
    session: str = field(default_factory=lambda: secrets.token_hex(8))
    clock: Callable[[], datetime] = _utc_now

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._copies: dict[str, _CopyState] = {}
        self._session_lock: Any = None  # QLockFile, made in start()
        self._started = False

    # --- the session ---------------------------------------------------------------

    @property
    def session_folder(self) -> Path:
        return self.directory / self.session

    @property
    def lock_path(self) -> Path:
        return self.directory / f"{self.session}{LOCK_SUFFIX}"

    @property
    def started(self) -> bool:
        return self._started

    def start(self) -> None:
        """Take the session's lock. Raises ``OSError`` if the folder cannot be used."""
        if self._started:
            return
        from PySide6.QtCore import QLockFile

        self.directory.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(self.lock_path))
        lock.setStaleLockTime(0)  # stale only when its process is gone, never by age
        if not lock.tryLock(0):
            raise OSError(f"cannot lock the recovery folder ({lock.error().name})")
        self._session_lock = lock
        self._started = True

    def close(self) -> None:
        """End the session cleanly: remove its copies, its folder and its lock."""
        if not self._started:
            return
        with self._lock:
            for copy_id in list(self._copies):
                self._delete_locked(copy_id)
            self._copies.clear()
            _remove_leftovers(self.session_folder)
        with contextlib.suppress(OSError):
            self.session_folder.rmdir()
        self._session_lock.unlock()
        self._session_lock = None
        self._started = False

    # --- copies of this session -------------------------------------------------------

    def new_copy_id(self) -> str:
        return secrets.token_hex(8)

    def copy_path(self, copy_id: str) -> Path:
        return self.session_folder / f"{copy_id}{COPY_SUFFIX}"

    def sidecar_path(self, copy_id: str) -> Path:
        return self.session_folder / f"{copy_id}{SIDECAR_SUFFIX}"

    def has_copy(self, copy_id: str) -> bool:
        with self._lock:
            state = self._copies.get(copy_id)
            return state is not None and state.exists

    def begin_write(self, copy_id: str) -> int:
        """Start a write of ``copy_id`` (main thread); older writes of it will not land.
        Returns the ticket to pass to :func:`write_copy`."""
        with self._lock:
            state = self._copies.setdefault(copy_id, _CopyState())
            state.ticket += 1
            return state.ticket

    def write(self, copy_id: str, ticket: int, document: Document, info: CopyInfo) -> bool:
        """Write ``document`` as the copy ``copy_id`` (any thread). True if it was written;
        False if a newer write or a delete came first (nothing is changed then)."""
        if not self._started:
            raise RuntimeError("The recovery session has not been started.")
        from curvis.io import project

        if not self._current(copy_id, ticket):
            return False
        target = self.copy_path(copy_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = project.write_temp(document, target)
        sidecar = json.dumps(info.to_json(self.session), ensure_ascii=False).encode("utf-8")
        try:
            with self._lock:
                if not self._current(copy_id, ticket, locked=True):
                    return False
                os.replace(tmp, target)
                _write_atomic(self.sidecar_path(copy_id), sidecar)
                self._copies[copy_id].exists = True
        finally:
            tmp.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            project.fsync_dir(target.parent)
        return True

    def delete(self, copy_id: str) -> None:
        """Delete the copy (the document was saved, closed or its changes discarded).
        A write of it still running is dropped."""
        with self._lock:
            self._delete_locked(copy_id)

    def _delete_locked(self, copy_id: str) -> None:
        state = self._copies.setdefault(copy_id, _CopyState())
        state.ticket += 1
        state.exists = False
        for path in (self.copy_path(copy_id), self.sidecar_path(copy_id)):
            with contextlib.suppress(FileNotFoundError):
                path.unlink()

    def forget(self, copy_id: str) -> None:
        """Delete the copy and stop tracking it (its document is closed)."""
        with self._lock:
            self._delete_locked(copy_id)
            del self._copies[copy_id]

    def _current(self, copy_id: str, ticket: int, *, locked: bool = False) -> bool:
        if locked:
            state = self._copies.get(copy_id)
            return state is not None and state.ticket == ticket
        with self._lock:
            return self._current(copy_id, ticket, locked=True)

    # --- copies of other sessions ------------------------------------------------------

    def scan(self) -> list[RecoveredCopy]:
        """The copies left by sessions that are no longer running, newest first.

        Never raises for a damaged copy or folder: what cannot be read is listed as not
        ``recoverable``, and a folder that cannot be listed is skipped.
        """
        found: list[RecoveredCopy] = []
        try:
            entries = list(self.directory.iterdir())
        except OSError:
            return found
        folders = {e.name: e for e in entries if e.is_dir()}
        locks = {e.name.removesuffix(LOCK_SUFFIX) for e in entries if e.name.endswith(LOCK_SUFFIX)}
        for session in sorted(locks - {self.session}):
            if not _session_running(self.directory / f"{session}{LOCK_SUFFIX}"):
                locks.discard(session)
        for name, folder in folders.items():
            if name == self.session or name in locks:
                continue  # this session, or one that is still running
            copies = _copies_in(folder)
            if not copies:
                with contextlib.suppress(OSError):
                    _remove_leftovers(folder)
                    folder.rmdir()
                continue
            found.extend(copies)
        oldest = datetime.min.replace(tzinfo=UTC)
        found.sort(key=lambda c: c.info.copied_at if c.info else oldest, reverse=True)
        return found

    def claim(self, copy: RecoveredCopy) -> str:
        """Move a recovered copy into this session, so it stays safe while its document
        is open; returns its new copy id. Raises ``OSError`` if it cannot be moved."""
        if not self._started:
            raise RuntimeError("The recovery session has not been started.")
        copy_id = self.new_copy_id()
        self.session_folder.mkdir(parents=True, exist_ok=True)
        os.replace(copy.project_path, self.copy_path(copy_id))
        with self._lock:
            self._copies[copy_id] = _CopyState(exists=True)
            if copy.info is not None:
                data = json.dumps(copy.info.to_json(self.session), ensure_ascii=False)
                _write_atomic(self.sidecar_path(copy_id), data.encode("utf-8"))
        with contextlib.suppress(FileNotFoundError):
            copy.sidecar_path.unlink()
        _remove_if_empty(copy.folder)
        return copy_id

    def discard(self, copy: RecoveredCopy) -> None:
        """Delete a recovered copy for good."""
        for path in (copy.project_path, copy.sidecar_path):
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
        _remove_if_empty(copy.folder)


def _session_running(lock_path: Path) -> bool:
    """True while the session that made ``lock_path`` still runs. A stale lock (its
    process is gone) is removed."""
    from PySide6.QtCore import QLockFile

    lock = QLockFile(str(lock_path))
    lock.setStaleLockTime(0)
    if lock.tryLock(0):
        lock.unlock()  # removes the stale lock file
        return False
    return lock.error() == QLockFile.LockError.LockFailedError


def _copies_in(folder: Path) -> list[RecoveredCopy]:
    try:
        names = [p.name for p in folder.iterdir() if p.is_file()]
    except OSError:
        return []
    ids = {
        n.removesuffix(COPY_SUFFIX)
        for n in names
        if n.endswith(COPY_SUFFIX) and not n.startswith(".")
    }
    ids |= {n.removesuffix(SIDECAR_SUFFIX) for n in names if n.endswith(SIDECAR_SUFFIX)}
    return [_read_copy(folder, copy_id) for copy_id in sorted(ids)]


def _read_copy(folder: Path, copy_id: str) -> RecoveredCopy:
    copy = RecoveredCopy(folder, copy_id, None, readable=False)
    try:
        info = CopyInfo.from_json(json.loads(copy.sidecar_path.read_bytes().decode("utf-8")))
    except (OSError, ValueError, UnicodeDecodeError):
        return copy
    return RecoveredCopy(folder, copy_id, info, readable=_looks_like_project(copy.project_path))


def _looks_like_project(path: Path) -> bool:
    """A cheap check that ``path`` is a readable project zip (the full read is on Open)."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as zf:
            return "project.json" in zf.namelist()
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, ValueError):
        return False


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _remove_leftovers(folder: Path) -> None:
    """Remove temporary files a write left behind (it was interrupted)."""
    try:
        paths = list(folder.iterdir())
    except OSError:
        return
    for path in paths:
        if path.name.startswith(".") and path.name.endswith(".tmp"):
            with contextlib.suppress(OSError):
                path.unlink()


def _remove_if_empty(folder: Path) -> None:
    _remove_leftovers(folder)
    with contextlib.suppress(OSError):
        folder.rmdir()


# --- snapshots ---------------------------------------------------------------------------


def snapshot(document: Document) -> Document:
    """A copy of ``document`` that later commands cannot change, for writing on a worker
    thread. Cheap: column arrays are copied as blocks (commands change them in place),
    frozen definitions are shared. Its undo stack is new and empty."""
    tables = [
        Table(
            name=t.name,
            columns=[_copy_column(c) for c in t.columns],
            replicate_groups=[
                ReplicateGroup(g.name, g.category_id, g.id) for g in t.replicate_groups
            ],
            category_groups=[CategoryGroup(g.name, g.id) for g in t.category_groups],
            id=t.id,
            category_factor=t.category_factor,
        )
        for t in document.tables
    ]
    change_log = ChangeLog()
    for entry in document.change_log.entries:
        change_log.append(entry)
    return Document(
        name=document.name,
        tables=tables,
        constants=[ProjectConstant(c.name, c.value, c.unit, c.id) for c in document.constants],
        calculations=[_copy_calculation(c) for c in document.calculations],
        analyses=list(document.analyses),
        graphs=list(document.graphs),
        settings=json.loads(json.dumps(document.settings)),
        change_log=change_log,
        id=document.id,
    )


def _copy_column(column: Column) -> Column:
    values = np.array(column.values, copy=True)
    values.flags.writeable = False
    copy = Column.__new__(Column)  # skip coerce_values: the array is right already
    copy.name = column.name
    copy.role = column.role
    copy.values = values
    copy.unit = column.unit
    copy.group_id = column.group_id
    copy.id = column.id
    copy.category_id = column.category_id
    return copy


def _copy_calculation(calculation: Calculation) -> Calculation:
    from dataclasses import replace

    return replace(calculation, params=json.loads(json.dumps(calculation.params)))
