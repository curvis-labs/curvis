"""The ``.cvp`` project file: a zip with ``project.json`` and one Arrow IPC file per table.

Layout::

    project.json          definitions, constants, settings, change log, schema_version
    tables/<n>.arrow      the column arrays of one table, one field per column id

Column metadata (name, role, unit, group) lives in ``project.json``; the Arrow files hold
only the arrays. Numeric columns are stored as ``float64`` (NaN, infinities and -0.0 are
kept exactly), text columns as ``string`` with nulls for missing cells. The undo stack is
session state and is not saved; the change log is.

:func:`save` writes to a temporary file in the same folder, syncs it to disk and then
renames it over the target, so an interrupted save never damages an existing project.
:func:`load` upgrades older files with the migrations in :data:`MIGRATIONS`.
"""

import json
import os
import secrets
import stat
import zipfile
import zlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
import pyarrow as pa

from curvis import __version__
from curvis.core.change_log import ChangeLog, ChangeLogEntry
from curvis.core.constants import JsonValue, ProjectConstant
from curvis.core.document import Document
from curvis.data import (
    DEFAULT_CATEGORY_FACTOR,
    CategoryGroup,
    Column,
    ColumnRole,
    ReplicateGroup,
    Table,
)

FORMAT_NAME = "curvis-project"
SCHEMA_VERSION = 2
"""Version of the ``project.json`` layout written by this build. Bump it for every change
to the format and register a migration from the previous version."""

PROJECT_JSON = "project.json"

type Migration = Callable[[dict[str, Any]], dict[str, Any]]

MIGRATIONS: dict[int, Migration] = {}
"""Migrations keyed by the version they upgrade from: ``MIGRATIONS[n]`` turns a version
``n`` project dict into a version ``n + 1`` one (it need not set ``schema_version``)."""

# Fixed zip timestamp so the same document always gives the same bytes.
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


class ProjectFileError(Exception):
    """A project file cannot be opened."""


class CorruptProjectError(ProjectFileError):
    """The file is not a valid Curvis project (damaged, truncated or not a project)."""


class NewerVersionError(ProjectFileError):
    """The file was saved by a newer version of Curvis with a format this build cannot read."""

    def __init__(self, path: Path, version: int) -> None:
        self.path = path
        self.version = version
        super().__init__(
            f"'{path.name}' was saved by a newer version of Curvis (file format {version}; "
            f"this version reads up to {SCHEMA_VERSION}). Update Curvis to open it."
        )


def register_migration(from_version: int) -> Callable[[Migration], Migration]:
    """Decorator that registers a migration from ``from_version`` to ``from_version + 1``."""

    def decorator(func: Migration) -> Migration:
        if from_version in MIGRATIONS:
            raise ValueError(f"a migration from version {from_version} is already registered")
        MIGRATIONS[from_version] = func
        return func

    return decorator


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a ``project.json`` dict to :data:`SCHEMA_VERSION`, one version at a time."""
    version: int = data["schema_version"]
    while version < SCHEMA_VERSION:
        migration = MIGRATIONS.get(version)
        if migration is None:
            raise ProjectFileError(f"Cannot upgrade project files from format {version}.")
        data = migration(data)
        version += 1
        data["schema_version"] = version
    return data


@register_migration(1)
def _add_category_factor(data: dict[str, Any]) -> dict[str, Any]:
    """Format 2 names the second factor of each table (its category groups)."""
    document = data.get("document")
    tables = document.get("tables") if isinstance(document, dict) else None
    for table in tables if isinstance(tables, list) else []:
        if isinstance(table, dict):
            table.setdefault("category_factor", DEFAULT_CATEGORY_FACTOR)
    return data


# --- save --------------------------------------------------------------------------------


def save(document: Document, path: str | os.PathLike[str]) -> None:
    """Save ``document`` to ``path`` atomically.

    The project is written to a temporary file next to ``path``, synced to disk and then
    renamed over ``path``. If anything fails, ``path`` is left exactly as it was.
    """
    path = Path(path)
    for table in document.tables:
        table.check()
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    # os.open with 0o666 applies the user's umask, unlike tempfile's 0o600.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o666)
    try:
        with os.fdopen(fd, "wb") as f:
            _write_project(document, f)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def _fsync_dir(folder: Path) -> None:
    """Make the rename durable (POSIX only; Windows has no directory handles for this)."""
    if os.name == "nt":
        return
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_project(document: Document, f: BinaryIO) -> None:
    with zipfile.ZipFile(f, "w", allowZip64=True) as zf:
        files = [f"tables/{i}.arrow" for i in range(len(document.tables))]
        info = zipfile.ZipInfo(PROJECT_JSON, _ZIP_DATE)
        info.compress_type = zipfile.ZIP_DEFLATED
        text = json.dumps(_project_dict(document, files), ensure_ascii=False, allow_nan=False)
        zf.writestr(info, text.encode("utf-8"))
        for table, name in zip(document.tables, files, strict=True):
            _write_table(zf, name, table)


def _write_table(zf: zipfile.ZipFile, name: str, table: Table) -> None:
    """Write the arrays of ``table`` as an uncompressed Arrow IPC file (fast to open)."""
    arrays = [_to_arrow(c) for c in table.columns]
    batch = pa.record_batch(arrays, names=[c.id for c in table.columns])
    sink = pa.BufferOutputStream()
    with pa.ipc.new_file(sink, batch.schema) as writer:
        writer.write_batch(batch)
    info = zipfile.ZipInfo(name, _ZIP_DATE)
    info.compress_type = zipfile.ZIP_STORED
    with zf.open(info, "w", force_zip64=True) as out:
        out.write(memoryview(sink.getvalue()))


def _to_arrow(column: Column) -> pa.Array:
    if column.role.is_numeric:
        return pa.array(column.values, type=pa.float64(), from_pandas=False)
    return pa.array(column.values, type=pa.string())


def _project_dict(document: Document, files: list[str]) -> dict[str, Any]:
    return {
        "format": FORMAT_NAME,
        "schema_version": SCHEMA_VERSION,
        "app_version": __version__,
        "document": {
            "id": document.id,
            "name": document.name,
            "tables": [_table_dict(t, f) for t, f in zip(document.tables, files, strict=True)],
            "constants": [
                {"id": c.id, "name": c.name, "value": c.value, "unit": c.unit}
                for c in document.constants
            ],
            "settings": document.settings,
        },
        "change_log": [
            {
                "timestamp": e.timestamp.isoformat(),
                "command_type": e.command_type,
                "summary": e.summary,
            }
            for e in document.change_log
        ],
    }


def _table_dict(table: Table, file: str) -> dict[str, Any]:
    return {
        "id": table.id,
        "name": table.name,
        "n_rows": table.n_rows,
        "file": file,
        "columns": [
            {
                "id": c.id,
                "name": c.name,
                "role": c.role.value,
                "unit": c.unit,
                "group_id": c.group_id,
            }
            for c in table.columns
        ],
        "replicate_groups": [
            {"id": g.id, "name": g.name, "category_id": g.category_id}
            for g in table.replicate_groups
        ],
        "category_groups": [{"id": g.id, "name": g.name} for g in table.category_groups],
        "category_factor": table.category_factor,
    }


# --- load --------------------------------------------------------------------------------


def load(path: str | os.PathLike[str]) -> Document:
    """Open the project at ``path``.

    Raises :class:`CorruptProjectError` if the file is damaged or not a project,
    :class:`NewerVersionError` if it needs a newer Curvis, and ``OSError`` (e.g.
    ``FileNotFoundError``) if it cannot be read at all.
    """
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as zf:
            data = _read_json(zf)
            version = _check_header(data, path)
            if version < SCHEMA_VERSION:
                data = migrate(data)
            return _build_document(zf, data)
    except _ContentError as e:
        raise CorruptProjectError(f"'{path.name}' is damaged and cannot be opened: {e}") from e
    except (zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, EOFError, pa.ArrowException) as e:
        raise CorruptProjectError(
            f"'{path.name}' is damaged or is not a Curvis project ({e})."
        ) from e
    except ValueError as e:  # bad values rejected by the model, bad UTF-8, zlib errors
        raise CorruptProjectError(f"'{path.name}' is damaged and cannot be opened: {e}") from e


class _ContentError(Exception):
    """Internal: the project content does not match the format."""


def _read_json(zf: zipfile.ZipFile) -> Any:
    try:
        raw = zf.read(PROJECT_JSON)
    except KeyError:
        raise _ContentError(f"{PROJECT_JSON} is missing") from None
    try:
        return json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except json.JSONDecodeError as e:
        raise _ContentError(f"{PROJECT_JSON} is not valid JSON ({e})") from None


def _reject_constant(name: str) -> None:
    raise _ContentError(f"{PROJECT_JSON} contains {name}, which is not valid JSON")


def _check_header(data: Any, path: Path) -> int:
    obj = _obj(data, PROJECT_JSON)
    if obj.get("format") != FORMAT_NAME:
        raise _ContentError(f"{PROJECT_JSON} is not a Curvis project description")
    version = _int(obj.get("schema_version"), "schema_version")
    if version < 1:
        raise _ContentError(f"schema_version {version} is not valid")
    if version > SCHEMA_VERSION:
        raise NewerVersionError(path, version)
    return version


def _build_document(zf: zipfile.ZipFile, data: dict[str, Any]) -> Document:
    doc_data = _obj(data.get("document"), "document")
    tables = [
        _read_table(zf, t, f"table {i + 1}")
        for i, t in enumerate(_list(doc_data.get("tables"), "tables"))
    ]
    _check_unique([t.id for t in tables], "table ids")
    constants = [
        _read_constant(c, f"constant {i + 1}")
        for i, c in enumerate(_list(doc_data.get("constants"), "constants"))
    ]
    _check_unique([c.id for c in constants], "constant ids")
    settings: dict[str, JsonValue] = _obj(doc_data.get("settings"), "settings")
    change_log = ChangeLog()
    for i, e in enumerate(_list(data.get("change_log"), "change_log")):
        change_log.append(_read_log_entry(e, f"change log entry {i + 1}"))
    return Document(
        name=_str(doc_data.get("name"), "document name"),
        tables=tables,
        constants=constants,
        settings=settings,
        change_log=change_log,
        id=_str(doc_data.get("id"), "document id"),
    )


def _read_table(zf: zipfile.ZipFile, value: Any, where: str) -> Table:
    obj = _obj(value, where)
    n_rows = _int(obj.get("n_rows"), f"{where} n_rows")
    file = _str(obj.get("file"), f"{where} file")
    columns = [
        _read_column_def(c, f"{where} column {i + 1}")
        for i, c in enumerate(_list(obj.get("columns"), f"{where} columns"))
    ]
    replicate_groups = [
        ReplicateGroup(
            name=_str(g.get("name"), f"{where} group name"),
            category_id=_opt_str(g.get("category_id"), f"{where} group category_id"),
            id=_str(g.get("id"), f"{where} group id"),
        )
        for g in (_obj(x, f"{where} group") for x in _list(obj.get("replicate_groups"), where))
    ]
    category_groups = [
        CategoryGroup(
            name=_str(g.get("name"), f"{where} category name"),
            id=_str(g.get("id"), f"{where} category id"),
        )
        for g in (_obj(x, f"{where} category") for x in _list(obj.get("category_groups"), where))
    ]
    _check_unique([g.id for g in replicate_groups], f"{where} group ids")
    _check_unique([g.id for g in category_groups], f"{where} category ids")
    table = Table(
        name=_str(obj.get("name"), f"{where} name"),
        columns=columns,
        replicate_groups=replicate_groups,
        category_groups=category_groups,
        id=_str(obj.get("id"), f"{where} id"),
        category_factor=_str(obj.get("category_factor"), f"{where} category_factor"),
    )
    _read_arrays(zf, file, table, n_rows)
    try:
        table.check()
    except ValueError as e:
        raise _ContentError(str(e)) from None
    return table


def _read_column_def(value: Any, where: str) -> Column:
    obj = _obj(value, where)
    role_name = _str(obj.get("role"), f"{where} role")
    try:
        role = ColumnRole(role_name)
    except ValueError:
        raise _ContentError(f"{where} has an unknown role '{role_name}'") from None
    return Column(
        name=_str(obj.get("name"), f"{where} name"),
        role=role,
        unit=_str(obj.get("unit"), f"{where} unit"),
        group_id=_opt_str(obj.get("group_id"), f"{where} group_id"),
        id=_str(obj.get("id"), f"{where} id"),
    )


def _read_arrays(zf: zipfile.ZipFile, file: str, table: Table, n_rows: int) -> None:
    """Fill the columns of ``table`` from its Arrow file, checking it matches the JSON."""
    try:
        raw = zf.read(file)
    except KeyError:
        raise _ContentError(f"the data of table '{table.name}' ({file}) is missing") from None
    arrow = pa.ipc.open_file(pa.py_buffer(raw)).read_all()
    if arrow.column_names != [c.id for c in table.columns]:
        raise _ContentError(f"the columns stored for table '{table.name}' do not match")
    if arrow.num_rows != n_rows:
        raise _ContentError(f"table '{table.name}' should have {n_rows} rows, not {arrow.num_rows}")
    for column, data in zip(table.columns, arrow.columns, strict=True):
        expected = pa.float64() if column.role.is_numeric else pa.string()
        if data.type != expected:
            raise _ContentError(f"column '{column.name}' holds {data.type}, expected {expected}")
        if column.role.is_numeric:
            if data.null_count:
                raise _ContentError(f"numeric column '{column.name}' contains nulls")
            values = np.array(data.to_numpy(), dtype=np.float64, copy=True)
        else:
            values = np.empty(len(data), dtype=object)
            values[:] = data.to_pylist()
        values.flags.writeable = False
        column.values = values


def _read_constant(value: Any, where: str) -> ProjectConstant:
    obj = _obj(value, where)
    number = obj.get("value")
    if isinstance(number, bool) or not isinstance(number, int | float):
        raise _ContentError(f"{where} value is not a number")
    return ProjectConstant(
        name=_str(obj.get("name"), f"{where} name"),
        value=float(number),
        unit=_str(obj.get("unit"), f"{where} unit"),
        id=_str(obj.get("id"), f"{where} id"),
    )


def _read_log_entry(value: Any, where: str) -> ChangeLogEntry:
    obj = _obj(value, where)
    return ChangeLogEntry(
        timestamp=datetime.fromisoformat(_str(obj.get("timestamp"), f"{where} timestamp")),
        command_type=_str(obj.get("command_type"), f"{where} command_type"),
        summary=_str(obj.get("summary"), f"{where} summary"),
    )


# --- JSON shape checks ---------------------------------------------------------------------


def _obj(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _ContentError(f"{where} is missing or not an object")
    return value


def _list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise _ContentError(f"{where} is missing or not a list")
    return value


def _str(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise _ContentError(f"{where} is missing or not text")
    return value


def _opt_str(value: Any, where: str) -> str | None:
    return None if value is None else _str(value, where)


def _int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _ContentError(f"{where} is missing or not a whole number")
    return value


def _check_unique(ids: list[str], what: str) -> None:
    if len(set(ids)) != len(ids):
        raise _ContentError(f"duplicate {what}")
