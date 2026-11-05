"""Tests for the .cvp project format: round trips, errors, migrations and crash safety."""

import json
import math
import os
import subprocess
import sys
import textwrap
import zipfile
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from curvis.analysis.base import Analysis
from curvis.charts.spec import LabelAngle
from curvis.core import ChangeLogEntry, Document, JsonValue, ProjectConstant, RenameTable
from curvis.data import CategoryGroup, Column, ColumnRole, ReplicateGroup, Table, normalized_order
from curvis.io import project
from curvis.io.project import (
    PROJECT_JSON,
    SCHEMA_VERSION,
    CorruptProjectError,
    NewerVersionError,
    ProjectFileError,
    load,
    save,
)

from .test_model_hypothesis import draw_command, try_execute

# --- strategies --------------------------------------------------------------------------

TEXT = st.text(max_size=8)
IDS = st.text(alphabet="0123456789abcdef", min_size=1, max_size=8)
FLOATS = st.floats(allow_nan=True, allow_infinity=True) | st.sampled_from(
    [0.0, -0.0, math.nan, -math.nan, math.inf, -math.inf, 5e-324]
)
JSON_VALUES: st.SearchStrategy[JsonValue] = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | TEXT,
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(TEXT, inner, max_size=3),
    max_leaves=8,
)
# Real UTC offsets are whole minutes.
TIMESTAMPS = st.datetimes(
    timezones=st.builds(
        lambda minutes: timezone(timedelta(minutes=minutes)), st.integers(-23 * 60, 23 * 60)
    )
)


@st.composite
def project_columns(draw: st.DrawFn, n_rows: int, group_ids: list[str]) -> Column:
    role = draw(st.sampled_from(list(ColumnRole)))
    values: Any
    if role.is_numeric:
        values = np.array(draw(st.lists(FLOATS, min_size=n_rows, max_size=n_rows)))
    else:
        values = draw(st.lists(st.none() | TEXT, min_size=n_rows, max_size=n_rows))
    group_id = draw(st.none() | st.sampled_from(group_ids)) if group_ids else None
    return Column.new(draw(TEXT), role, values, unit=draw(TEXT), group_id=group_id)


@st.composite
def project_tables(draw: st.DrawFn) -> Table:
    categories = [CategoryGroup(draw(TEXT)) for _ in range(draw(st.integers(0, 2)))]
    category_ids = [c.id for c in categories]
    groups = [
        ReplicateGroup(draw(TEXT), draw(st.none() | st.sampled_from(category_ids)))
        if category_ids
        else ReplicateGroup(draw(TEXT))
        for _ in range(draw(st.integers(0, 2)))
    ]
    n_rows = draw(st.integers(0, 6))
    n_cols = draw(st.integers(0, 4))
    columns = [draw(project_columns(n_rows, [g.id for g in groups])) for _ in range(n_cols)]
    # Names are unique ignoring case (as commands guarantee); ids come from new_id().
    for i, column in enumerate(columns):
        column.name = f"{column.name}#{i}"
        if column.role is not ColumnRole.Y:
            column.group_id = None
        elif column.group_id is None and categories:
            column.category_id = draw(st.none() | st.sampled_from([c.id for c in categories]))
    for i, group in enumerate(groups):
        group.name = f"{group.name}#{i}"
    for i, category in enumerate(categories):
        category.name = f"{category.name}#{i}"
    # Groups hold only Y columns, none is empty, and each is contiguous (as commands
    # guarantee).
    used = {c.group_id for c in columns}
    groups = [g for g in groups if g.id in used]
    used_categories = {g.category_id for g in groups}
    used_categories |= {c.category_id for c in columns if c.group_id is None}
    categories = [c for c in categories if c.id in used_categories]
    table = Table(draw(TEXT), columns, groups, categories, category_factor=draw(TEXT) + "F")
    table.columns = normalized_order(table)
    return table


@st.composite
def project_documents(draw: st.DrawFn) -> Document:
    doc = Document(
        name=draw(TEXT),
        tables=draw(st.lists(project_tables(), min_size=1, max_size=3)),
        constants=[
            ProjectConstant(draw(TEXT), draw(st.floats(allow_nan=False, allow_infinity=False)))
            for _ in range(draw(st.integers(0, 3)))
        ],
        settings=draw(st.dictionaries(TEXT, JSON_VALUES, max_size=4)),
    )
    for _ in range(draw(st.integers(0, 4))):
        doc.change_log.append(ChangeLogEntry(draw(TIMESTAMPS), draw(TEXT), draw(TEXT)))
    return doc


def assert_identical(a: Document, b: Document) -> None:
    """Equal, and numeric arrays bit-identical (so -0.0 and NaN payloads survive)."""
    assert a == b
    for ta, tb in zip(a.tables, b.tables, strict=True):
        for ca, cb in zip(ta.columns, tb.columns, strict=True):
            assert not cb.values.flags.writeable
            if ca.role.is_numeric:
                assert np.array_equal(ca.values.view(np.uint64), cb.values.view(np.uint64))
    for la, lb in zip(a.change_log, b.change_log, strict=True):
        assert la.timestamp.utcoffset() == lb.timestamp.utcoffset()


TMP_SETTINGS = settings(suppress_health_check=[HealthCheck.function_scoped_fixture])


# --- round trips ---------------------------------------------------------------------------


@TMP_SETTINGS
@given(doc=project_documents())
def test_round_trip(tmp_path: Path, doc: Document) -> None:
    path = tmp_path / "p.cvp"
    save(doc, path)
    assert_identical(doc, load(path))


@TMP_SETTINGS
@given(doc=project_documents(), data=st.data())
def test_round_trip_after_random_commands(
    tmp_path: Path, doc: Document, data: st.DataObject
) -> None:
    for _ in range(data.draw(st.integers(1, 10))):
        try_execute(doc, draw_command(data, doc))
    path = tmp_path / "p.cvp"
    save(doc, path)
    assert_identical(doc, load(path))


@TMP_SETTINGS
@given(doc=project_documents())
def test_save_is_deterministic(tmp_path: Path, doc: Document) -> None:
    save(doc, tmp_path / "a.cvp")
    save(doc, tmp_path / "b.cvp")
    assert (tmp_path / "a.cvp").read_bytes() == (tmp_path / "b.cvp").read_bytes()


def test_special_values_are_kept_exactly(tmp_path: Path) -> None:
    values = np.array([0.0, -0.0, np.nan, np.inf, -np.inf, 5e-324, 1.7976931348623157e308])
    doc = Document(
        tables=[
            Table(
                "T",
                [
                    Column.new("x", "X", values),
                    Column.new("label", "Text", ["", None, "µM", "a\nb", "日本", "", None]),
                ],
            )
        ]
    )
    save(doc, tmp_path / "p.cvp")
    loaded = load(tmp_path / "p.cvp")
    assert_identical(doc, loaded)
    assert loaded.tables[0].columns[1].values.tolist() == ["", None, "µM", "a\nb", "日本", "", None]
    assert math.copysign(1.0, loaded.tables[0].columns[0].values[1]) == -1.0


def test_empty_document_and_table_without_columns(tmp_path: Path) -> None:
    doc = Document(tables=[Table("Empty")])
    save(doc, tmp_path / "p.cvp")
    assert_identical(doc, load(tmp_path / "p.cvp"))


def test_undo_stack_is_not_saved_but_change_log_is(tmp_path: Path) -> None:
    doc = Document(tables=[Table("A")])
    doc.execute(RenameTable(doc.tables[0].id, "B"))
    save(doc, tmp_path / "p.cvp")
    loaded = load(tmp_path / "p.cvp")
    assert not loaded.undo_stack.can_undo
    assert [e.summary for e in loaded.change_log] == [e.summary for e in doc.change_log]


def test_save_overwrites_and_keeps_file_mode(tmp_path: Path) -> None:
    path = tmp_path / "p.cvp"
    save(Document(name="one"), path)
    if os.name != "nt":
        path.chmod(0o640)
    save(Document(name="two"), path)
    assert load(path).name == "two"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o640
    assert [p.name for p in tmp_path.iterdir()] == ["p.cvp"]


def test_project_json_layout(tmp_path: Path) -> None:
    table = Table("T", [Column.new("x", "X", [1, 2])])
    save(Document(name="D", tables=[table]), tmp_path / "p.cvp")
    with zipfile.ZipFile(tmp_path / "p.cvp") as zf:
        assert zf.namelist() == ["project.json", "tables/0.arrow"]
        data = json.loads(zf.read("project.json"))
    assert data["format"] == "curvis-project"
    assert data["schema_version"] == SCHEMA_VERSION == 7
    assert set(data["document"]) == {
        "id",
        "name",
        "tables",
        "constants",
        "calculations",
        "analyses",
        "graphs",
        "settings",
    }
    assert data["change_log"] == []


# --- errors ------------------------------------------------------------------------------


def write_zip(path: Path, members: dict[str, bytes | str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)


def saved_members(tmp_path: Path) -> dict[str, bytes | str]:
    table = Table("T", [Column.new("x", "X", [1, 2, 3]), Column.new("t", "Text", ["a", None, "c"])])
    save(Document(tables=[table]), tmp_path / "good.cvp")
    with zipfile.ZipFile(tmp_path / "good.cvp") as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def edit_json(members: dict[str, bytes | str], change: Any) -> dict[str, bytes | str]:
    data = json.loads(members["project.json"])
    change(data)
    return {**members, "project.json": json.dumps(data)}


def test_missing_file_raises_os_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "nope.cvp")


@pytest.mark.parametrize(
    "content",
    [b"", b"not a zip at all", b"PK\x03\x04garbage"],
    ids=["empty", "text", "bad-zip-header"],
)
def test_not_a_zip(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "p.cvp"
    path.write_bytes(content)
    with pytest.raises(CorruptProjectError, match=r"p\.cvp"):
        load(path)


def test_truncated_file(tmp_path: Path) -> None:
    table = Table("T", [Column.new("x", "X", np.arange(10_000.0))])
    path = tmp_path / "p.cvp"
    save(Document(tables=[table]), path)
    data = path.read_bytes()
    for cut in (len(data) // 3, len(data) // 2, len(data) - 10):
        path.write_bytes(data[:cut])
        with pytest.raises(CorruptProjectError):
            load(path)


def test_flipped_bytes_in_table_data(tmp_path: Path) -> None:
    table = Table("T", [Column.new("x", "X", np.arange(1000.0))])
    path = tmp_path / "p.cvp"
    save(Document(tables=[table]), path)
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    path.write_bytes(bytes(data))
    with pytest.raises(CorruptProjectError):  # zip CRC check catches it
        load(path)


def _set(path: tuple[str | int, ...], value: Any) -> Any:
    def change(data: Any) -> None:
        for key in path[:-1]:
            data = data[key]
        data[path[-1]] = value

    return change


def _delete(path: tuple[str | int, ...]) -> Any:
    def change(data: Any) -> None:
        for key in path[:-1]:
            data = data[key]
        del data[path[-1]]

    return change


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (_set(("format",), "something-else"), "not a Curvis project"),
        (_delete(("format",)), "not a Curvis project"),
        (_set(("schema_version",), "1"), "schema_version"),
        (_set(("schema_version",), True), "schema_version"),
        (_set(("schema_version",), 0), "schema_version"),
        (_delete(("document",)), "document"),
        (_set(("document", "tables"), {}), "tables"),
        (_set(("document", "tables", 0, "n_rows"), 4), "should have 4 rows"),
        (_set(("document", "tables", 0, "columns", 0, "role"), "Z"), "unknown role 'Z'"),
        (_set(("document", "tables", 0, "columns", 0, "role"), "Text"), "expected string"),
        (_set(("document", "tables", 0, "columns", 0, "id"), "zzz"), "do not match"),
        (_set(("document", "tables", 0, "columns", 1, "name"), "X"), "duplicate column names"),
        (_set(("document", "tables", 0, "file"), "tables/9.arrow"), "is missing"),
        (_set(("document", "constants"), [{"id": "a", "name": "k", "value": "1", "unit": ""}]),
         "not a number"),
        (_set(("document", "settings"), []), "settings"),
        (_set(("change_log",), [{"timestamp": "2024-01-01T00:00:00",
                                 "command_type": "X", "summary": ""}]), "timezone"),
        (_set(("change_log",), [{"timestamp": "yesterday", "command_type": "X",
                                 "summary": ""}]), "yesterday"),
    ],
)  # fmt: skip
def test_invalid_project_json(tmp_path: Path, change: Any, message: str) -> None:
    path = tmp_path / "p.cvp"
    write_zip(path, edit_json(saved_members(tmp_path), change))
    with pytest.raises(CorruptProjectError, match=message):
        load(path)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("{not json", "not valid JSON"),
        ("[]", "not an object"),
        ('{"format": "curvis-project", "schema_version": NaN}', "NaN"),
        (b"\xff\xfe", "damaged"),
    ],
)
def test_unreadable_project_json(tmp_path: Path, text: str | bytes, message: str) -> None:
    path = tmp_path / "p.cvp"
    write_zip(path, {**saved_members(tmp_path), "project.json": text})
    with pytest.raises(CorruptProjectError, match=message):
        load(path)


def test_missing_project_json(tmp_path: Path) -> None:
    members = saved_members(tmp_path)
    del members["project.json"]
    path = tmp_path / "p.cvp"
    write_zip(path, members)
    with pytest.raises(CorruptProjectError, match=r"project\.json is missing"):
        load(path)


def test_garbage_arrow_file(tmp_path: Path) -> None:
    path = tmp_path / "p.cvp"
    write_zip(path, {**saved_members(tmp_path), "tables/0.arrow": b"not arrow"})
    with pytest.raises(CorruptProjectError, match=r"p\.cvp"):
        load(path)


def test_newer_version(tmp_path: Path) -> None:
    path = tmp_path / "p.cvp"
    write_zip(path, edit_json(saved_members(tmp_path), _set(("schema_version",), 99)))
    with pytest.raises(NewerVersionError, match="newer version of Curvis") as info:
        load(path)
    assert info.value.version == 99
    assert isinstance(info.value, ProjectFileError)


# --- migrations ----------------------------------------------------------------------------


def test_migrations_cover_every_older_version() -> None:
    assert SCHEMA_VERSION == 7
    assert sorted(project.MIGRATIONS) == list(range(1, SCHEMA_VERSION))


def test_adding_a_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """How a format change is shipped: bump SCHEMA_VERSION and register a migration.

    Here format 2 renames the document key "name" to "title" and the loader for format 2
    reads "title". A version 1 file must still open.
    """
    path = tmp_path / "old.cvp"
    monkeypatch.setattr(project, "SCHEMA_VERSION", 1)
    save(Document(name="Kinetics"), path)  # written as version 1

    monkeypatch.setattr(project, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(project, "MIGRATIONS", {})

    @project.register_migration(1)
    def rename_name_to_title(data: dict[str, Any]) -> dict[str, Any]:
        data["document"]["title"] = data["document"].pop("name")
        return data

    seen: list[dict[str, Any]] = []
    build = project._build_document

    def build_v2(zf: zipfile.ZipFile, data: dict[str, Any]) -> Document:
        seen.append(data)
        data["document"]["name"] = data["document"].pop("title")  # the "new" loader
        return build(zf, data)

    monkeypatch.setattr(project, "_build_document", build_v2)

    assert load(path).name == "Kinetics"
    assert seen[0]["schema_version"] == 2
    with pytest.raises(ValueError, match="already registered"):
        project.register_migration(1)(rename_name_to_title)


def test_version_1_files_get_the_default_category_factor(tmp_path: Path) -> None:
    """Format 2 added the name of each table's second factor."""
    path = tmp_path / "v1.cvp"
    table = Table("T", [Column.new("A", ColumnRole.Y, [1.0])], category_factor="Sex")
    save(Document(tables=[table]), path)
    with zipfile.ZipFile(path) as zf:
        files = {name: zf.read(name) for name in zf.namelist()}
    data = json.loads(files[PROJECT_JSON])
    data["schema_version"] = 1
    del data["document"]["tables"][0]["category_factor"]
    files[PROJECT_JSON] = json.dumps(data).encode()
    with zipfile.ZipFile(path, "w") as zf:
        for name, raw in files.items():
            zf.writestr(name, raw)
    assert load(path).tables[0].category_factor == "Category"


def test_missing_migration_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "old.cvp"
    save(Document(), path)
    monkeypatch.setattr(project, "SCHEMA_VERSION", 8)
    monkeypatch.setattr(project, "MIGRATIONS", {v: lambda data: data for v in range(1, 7)})
    with pytest.raises(ProjectFileError, match="from format 7"):
        load(path)


# --- crash safety --------------------------------------------------------------------------


def big_document(name: str) -> Document:
    columns = [Column.new(f"C{i}", "Y", np.random.default_rng(i).random(50_000)) for i in range(8)]
    return Document(name=name, tables=[Table("T", columns), Table("U", [columns[0]])])


def test_failure_during_save_keeps_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "p.cvp"
    original = big_document("original")
    save(original, path)
    before = path.read_bytes()

    calls = 0
    write_table = project._write_table

    def failing_write_table(zf: zipfile.ZipFile, name: str, table: Table) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        write_table(zf, name, table)

    monkeypatch.setattr(project, "_write_table", failing_write_table)
    with pytest.raises(OSError, match="disk full"):
        save(big_document("changed"), path)

    assert path.read_bytes() == before
    assert_identical(load(path), original)
    assert [p.name for p in tmp_path.iterdir()] == ["p.cvp"]


def test_failure_during_rename_keeps_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "p.cvp"
    save(Document(name="original"), path)

    def failing_replace(src: Any, dst: Any) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(project.os, "replace", failing_replace)
    with pytest.raises(PermissionError):
        save(Document(name="changed"), path)
    assert load(path).name == "original"
    assert [p.name for p in tmp_path.iterdir()] == ["p.cvp"]


CRASH_SCRIPT = textwrap.dedent(
    """
    import os, sys
    import numpy as np
    from curvis.core import Document
    from curvis.data import Column, Table
    from curvis.io import project

    write_table = project._write_table

    def crash_mid_table(zf, name, table):
        # Write part of the table data, then die without any cleanup (like a power cut
        # or a killed process).
        with zf.open(name + ".partial", "w") as out:
            out.write(b"\\0" * 1_000_000)
        os._exit(3)

    project._write_table = crash_mid_table
    columns = [Column.new(f"C{i}", "Y", np.arange(100_000.0)) for i in range(4)]
    project.save(Document(name="changed", tables=[Table("T", columns)]), sys.argv[1])
    """
)


def test_process_killed_mid_save_keeps_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "p.cvp"
    original = big_document("original")
    save(original, path)
    before = path.read_bytes()

    result = subprocess.run(
        [sys.executable, "-c", CRASH_SCRIPT, str(path)], capture_output=True, timeout=120
    )
    assert result.returncode == 3, result.stderr.decode()

    assert path.read_bytes() == before
    assert_identical(load(path), original)
    # The crashed save leaves only its hidden temp file behind, never a damaged project.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "p.cvp"]
    assert all(name.startswith(".p.cvp.") and name.endswith(".tmp") for name in leftovers)


def test_constants_must_be_finite() -> None:
    with pytest.raises(ValueError, match="finite"):
        ProjectConstant("k", math.inf)


def test_timestamps_keep_their_offset(tmp_path: Path) -> None:
    doc = Document(tables=[Table("T")])
    stamp = datetime(2024, 5, 6, 7, 8, 9, 123456, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    doc.change_log.append(ChangeLogEntry(stamp, "AddTable", "Add table 'A'"))
    doc.change_log.append(ChangeLogEntry(datetime(2024, 1, 1, tzinfo=UTC), "X", "y"))
    save(doc, tmp_path / "p.cvp")
    assert_identical(doc, load(tmp_path / "p.cvp"))


def test_settings_that_are_not_json_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="JSON"):
        save(Document(settings={"limit": math.inf}), tmp_path / "p.cvp")
    assert list(tmp_path.iterdir()) == []


# --- analyses ------------------------------------------------------------------------------


def _analysis_document() -> tuple[Document, Analysis]:
    from curvis.analysis.descriptive import DescriptiveParams

    table = Table("T", [Column.new("a", "Y", [1.0, 2.0], id="a")], id="T")
    analysis = Analysis(
        "descriptive_statistics", "T", ("a",), DescriptiveParams(confidence_interval=False)
    )
    return Document(tables=[table], analyses=[analysis]), analysis


def test_analyses_are_saved_with_their_inputs_and_parameters(tmp_path: Path) -> None:
    document, analysis = _analysis_document()
    save(document, tmp_path / "a.cvp")
    loaded = load(tmp_path / "a.cvp")
    assert loaded.analyses == [analysis]
    with zipfile.ZipFile(tmp_path / "a.cvp") as zf:
        stored = json.loads(zf.read(PROJECT_JSON))["document"]["analyses"][0]
    assert stored["params"] == {"confidence_interval": False, "skip_empty_rows": True}
    assert "result" not in stored  # results are derived, not saved


def _rewrite(path: Path, change: Any) -> None:
    with zipfile.ZipFile(path) as zf:
        files = {name: zf.read(name) for name in zf.namelist()}
    data = json.loads(files[PROJECT_JSON])
    change(data)
    files[PROJECT_JSON] = json.dumps(data).encode()
    with zipfile.ZipFile(path, "w") as zf:
        for name, raw in files.items():
            zf.writestr(name, raw)


def test_version_3_files_have_no_analyses(tmp_path: Path) -> None:
    """Format 4 added analyses."""
    path = tmp_path / "v3.cvp"
    save(Document(tables=[Table("T", [Column.new("a", "Y", [1.0])])]), path)

    def to_v3(data: dict[str, Any]) -> None:
        data["schema_version"] = 3
        del data["document"]["analyses"]

    _rewrite(path, to_v3)
    assert load(path).analyses == []


def test_version_4_files_have_no_graphs(tmp_path: Path) -> None:
    """Format 5 added graphs."""
    path = tmp_path / "v4.cvp"
    save(Document(tables=[Table("T", [Column.new("a", "Y", [1.0])])]), path)

    def to_v4(data: dict[str, Any]) -> None:
        data["schema_version"] = 4
        del data["document"]["graphs"]

    _rewrite(path, to_v4)
    assert load(path).graphs == []


def _graph_document() -> Document:
    from curvis.charts.builder import default_spec
    from curvis.charts.spec import ChartFamily

    table = Table("T", [Column.new("x", "X", [1.0, 2.0]), Column.new("a", "Y", [3.0, 4.0])])
    hidden = default_spec(table, ChartFamily.XY).with_series_visible(table.columns[1].id, False)
    return Document(tables=[table], graphs=[hidden, default_spec(table, ChartFamily.BAR)])


def test_graphs_round_trip(tmp_path: Path) -> None:
    document = _graph_document()
    save(document, tmp_path / "g.cvp")
    assert load(tmp_path / "g.cvp").graphs == document.graphs


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda g: g.update(table_id="gone"), "table that does not exist"),
        (lambda g: g.update(family="pie"), "family cannot be 'pie'"),
        (lambda g: g.update(series="a"), "series must be a list"),
    ],
)
def test_bad_graphs_are_rejected(tmp_path: Path, change: Any, message: str) -> None:
    path = tmp_path / "g.cvp"
    save(_graph_document(), path)
    _rewrite(path, lambda data: change(data["document"]["graphs"][0]))
    with pytest.raises(ProjectFileError, match=message):
        load(path)


def test_duplicate_graph_ids_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "g.cvp"
    save(_graph_document(), path)

    def duplicate(data: dict[str, Any]) -> None:
        graphs = data["document"]["graphs"]
        graphs[1]["id"] = graphs[0]["id"]

    _rewrite(path, duplicate)
    with pytest.raises(ProjectFileError, match="graph ids"):
        load(path)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda a: a.update(type="no_such_analysis"), "unknown type"),
        (lambda a: a.update(table_id="gone"), "table that does not exist"),
        (lambda a: a["params"].update(confidence_interval="yes"), "true or false"),
        (lambda a: a.update(inputs="a"), "inputs"),
    ],
)
def test_damaged_analyses_are_reported(tmp_path: Path, change: Any, message: str) -> None:
    document, _analysis = _analysis_document()
    path = tmp_path / "bad.cvp"
    save(document, path)
    _rewrite(path, lambda data: change(data["document"]["analyses"][0]))
    with pytest.raises(CorruptProjectError, match=message):
        load(path)


def test_version_5_graphs_get_page_presets_and_legends(tmp_path: Path) -> None:
    """Format 6 replaced the aspect ratios by Tall, Wide and Square pages and stores
    whether a graph shows its legend."""
    path = tmp_path / "v5.cvp"
    save(_graph_document(), path)
    ratios = ["4:3", "3:4"]

    def to_v5(data: dict[str, Any]) -> None:
        data["schema_version"] = 5
        for graph, ratio in zip(data["document"]["graphs"], ratios, strict=True):
            graph["aspect_ratio"] = ratio
            del graph["show_legend"]
            for series in graph["series"]:
                del series["in_legend"]

    _rewrite(path, to_v5)
    xy, bar = load(path).graphs
    assert (xy.aspect_ratio, bar.aspect_ratio) == ("wide", "tall")
    assert (xy.show_legend, bar.show_legend) == (True, False)
    assert all(s.in_legend for s in xy.series)


def test_version_6_axes_get_auto_label_angles(tmp_path: Path) -> None:
    """Format 7 stores how each axis turns its tick labels; older graphs get Auto."""
    path = tmp_path / "v6.cvp"
    save(_graph_document(), path)

    def to_v6(data: dict[str, Any]) -> None:
        data["schema_version"] = 6
        for graph in data["document"]["graphs"]:
            for key in ("x_axis", "y_axis"):
                del graph[key]["label_angle"]

    _rewrite(path, to_v6)
    for graph in load(path).graphs:
        assert graph.x_axis.label_angle is LabelAngle.AUTO
        assert graph.y_axis.label_angle is LabelAngle.AUTO
