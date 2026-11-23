"""Tests for Prism import: Max's files in tests/data/prism, and small .pzfx tables built
here for layouts no real file has (they are written to a temporary folder, never kept)."""

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from curvis.data import ColumnRole, Table, series
from curvis.data.survival import survival_pairs
from curvis.io.prism import (
    X_DATA_SET,
    ExcludedValue,
    ImportedTable,
    PrismImportError,
    is_prism,
    read_prism,
    read_tables,
)

DATA = Path(__file__).parents[1] / "data" / "prism"
PAIRS = ["Column data_Rep", "Column data_SEM", "Survival data", "XY data_Rep"]
ALL = sorted(p.name for p in DATA.iterdir() if is_prism(p))


def cells(table: Table) -> list[tuple[str, str, list[Any]]]:
    """(full name, role, values) per column, NaN as None."""
    result = []
    for column in table.columns:
        values = [None if isinstance(v, float) and math.isnan(v) else v for v in column.values]
        result.append((table.full_name(column), column.role.value, values))
    return result


def only(path: Path) -> ImportedTable:
    data = read_prism(path)
    assert len(data.tables) == 1 and not data.skipped
    return data.tables[0]


# --- the files we were given -----------------------------------------------------------


@pytest.mark.parametrize("name", ALL)
def test_every_table_of_every_file_imports(name: str) -> None:
    data = read_prism(DATA / name)
    assert data.tables and not data.skipped
    for imported in data.tables:
        imported.table.check()
        assert imported.table.n_rows > 0


@pytest.mark.parametrize("stem", PAIRS)
def test_pzfx_and_prism_give_the_same_tables(stem: str) -> None:
    a, b = only(DATA / f"{stem}.pzfx"), only(DATA / f"{stem}.prism")
    assert a.title == b.title and a.kind == b.kind and a.notes == b.notes
    assert cells(a.table) == cells(b.table)
    assert [g.name for g in a.table.replicate_groups] == [g.name for g in b.table.replicate_groups]


def test_column_table_of_replicates() -> None:
    imported = only(DATA / "Column data_Rep.prism")
    assert imported.kind == "column"
    assert imported.summary == "Column, 3 series, 5 rows"
    assert cells(imported.table) == [
        ("Untreated", "Y", [3.4, 4.3, 3.0, 3.9, 4.1]),
        ("Placebo", "Y", [2.3, 5.2, 4.5, 3.1, 5.0]),
        ("Treated", "Y", [4.2, 7.8, 5.9, 6.4, 7.6]),
    ]


def test_means_with_sem_and_n_become_y_error_and_text() -> None:
    imported = only(DATA / "Column data_SEM.pzfx")
    assert imported.kind == "grouped"
    assert cells(imported.table)[:3] == [
        ("Group 1", "Y", [3.74]),
        ("Group 1 SEM", "Error", [0.532]),
        ("Group 1 N", "Text", ["5"]),
    ]
    assert [s.name for s in series(imported.table)] == ["Group 1", "Group 2", "Group 3"]
    assert imported.notes == ("N values are kept as text columns.",)


def test_xy_replicates_become_replicate_groups() -> None:
    imported = only(DATA / "XY data_Rep.pzfx")
    table = imported.table
    assert table.columns[0].role is ColumnRole.X
    assert table.columns[0].name == "Minutes"
    assert [g.name for g in table.replicate_groups] == ["Control", "Treated"]
    assert [c.name for c in table.group_columns(table.replicate_groups[0].id)] == [
        "Control 1",
        "Control 2",
        "Control 3",
    ]
    assert np.isnan(table.columns[2].values[2])  # an empty cell stays empty


def test_survival_is_time_and_event_pairs() -> None:
    imported = only(DATA / "Survival data.prism")
    table = imported.table
    assert [p.name for p in survival_pairs(table)] == ["Control", "Treatment A", "Treatment B"]
    assert table.columns[0].role is ColumnRole.TEXT  # the row titles
    assert table.columns[1].name == "Days"
    assert imported.summary == "Survival, 3 curves, 27 rows"


def test_large_xy_tables_read_in_full() -> None:
    data = read_prism(DATA / "2025-03-21 Analysis, LacY-GFP, ONPG.prism")
    first = data.tables[0]
    assert first.title == "Sensor 1, ONPG"
    assert first.table.n_rows == 3000
    assert len(first.table.replicate_groups) == 9
    names = [t.table.name for t in data.tables]
    assert len(set(names)) == len(names)


def test_grouped_replicates_make_categories() -> None:
    path = DATA / "20260610 LacY-GFP, bGal, PLE, HV50, 22-16, 26-9, stability at 20°C.prism"
    grouped = [t for t in read_prism(path).tables if t.kind == "grouped"]
    assert grouped
    table = grouped[0].table
    assert len(table.category_groups) == 12
    first = table.columns[0]
    assert table.full_name(first) == "50% 22-16 LacY - 1: without TX"
    assert first.values.tolist() == [1.282]


@pytest.mark.parametrize("name", [n for n in ALL if n.endswith(".prism")])
def test_csv_columns_follow_the_stored_row_ranges(name: str) -> None:
    """The CSV columns of a .prism table are in the order row titles, X, data sets: each
    subcolumn's first and last filled row match the ones its data set stores."""
    import json
    import zipfile

    with zipfile.ZipFile(DATA / name) as archive:
        document = json.loads(archive.read("document.json"))
        for sheet_id in document["sheets"]["data"]:
            table = json.loads(archive.read(f"data/sheets/{sheet_id}/sheet.json"))["table"]
            ids = [table[k] for k in ("rowTitlesDataSet", "xDataSet") if k in table]
            ids += table["dataSets"]
            ranges: list[tuple[int, int]] = []
            for data_set_id in ids:
                data_set = json.loads(archive.read(f"data/sets/{data_set_id}.json"))
                if "replicates" in data_set:
                    stored = data_set["replicates"]
                else:
                    stored = []
                    for entry in data_set["replicate ranges"]:
                        low, *high = entry["range"].split("~")
                        stored += [entry["replicate"]] * (
                            int(high[0] if high else low) - int(low) + 1
                        )
                ranges += [(r["firstRow"], r["lastRow"]) for r in stored]
            raw = next(
                t
                for t in read_tables(DATA / name)
                if t.title == _title(table, sheet_id, document, archive)
            )
            subcolumns = []
            if raw.row_titles is not None:
                subcolumns.append(raw.row_titles)
            if raw.x is not None:
                subcolumns += raw.x.subcolumns
            for data_set in raw.data_sets:
                subcolumns += data_set.subcolumns
            found = []
            for cells_ in subcolumns:
                filled = [i for i, c in enumerate(cells_) if c is not None]
                found.append((filled[0], filled[-1]) if filled else (-1, -1))
            assert found == ranges


def _title(table: Any, sheet_id: str, document: Any, archive: Any) -> str:
    import json

    sheet = json.loads(archive.read(f"data/sheets/{sheet_id}/sheet.json"))
    title = sheet.get("title")
    if isinstance(title, dict):
        title = title.get("string")
    return " ".join(str(title).split())


# --- layouts built here ----------------------------------------------------------------

HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<GraphPadPrismFile xmlns="http://graphpad.com/prism/Prism.htm" PrismXMLVersion="5.00">
<TableSequence>{refs}</TableSequence>
{tables}
</GraphPadPrismFile>
"""


def column(tag: str, title: str | None, *subcolumns: list[str | None], **attrs: str) -> str:
    extra = "".join(f' {k}="{v}"' for k, v in attrs.items())
    body = f"<Title>{title}</Title>" if title is not None else ""
    for sub in subcolumns:
        ds = "".join(
            "<d/>"
            if v is None
            else (f'<d Excluded="1">{v[1:]}</d>' if v.startswith("!") else f"<d>{v}</d>")
            for v in sub
        )
        body += f"<Subcolumn>{ds}</Subcolumn>"
    return f"<{tag}{extra}>{body}</{tag}>"


def pzfx(tmp_path: Path, *tables: tuple[dict[str, str], str, list[str]]) -> Path:
    refs = "".join(f'<Ref ID="T{i}"/>' for i in range(len(tables)))
    parts = []
    for i, (attrs, title, columns) in enumerate(tables):
        extra = "".join(f' {k}="{v}"' for k, v in attrs.items())
        parts.append(f'<Table ID="T{i}"{extra}><Title>{title}</Title>{"".join(columns)}</Table>')
    path = tmp_path / "made.pzfx"
    path.write_text(HEADER.format(refs=refs, tables="".join(parts)), encoding="utf-8")
    return path


def test_xy_with_sd_and_n(tmp_path: Path) -> None:
    path = pzfx(
        tmp_path,
        (
            {"TableType": "XY", "XFormat": "numbers", "YFormat": "SDN"},
            "Means",
            [
                column("XColumn", "Dose", ["1", "2"]),
                column("YColumn", "Drug", ["10", "20"], ["1.5", "2.5"], ["3", "3"]),
            ],
        ),
    )
    imported = only(path)
    assert cells(imported.table) == [
        ("Dose", "X", [1.0, 2.0]),
        ("Drug", "Y", [10.0, 20.0]),
        ("Drug SD", "Error", [1.5, 2.5]),
        ("Drug N", "Text", ["3", "3"]),
    ]


def test_cv_and_limits_are_kept_as_text(tmp_path: Path) -> None:
    path = pzfx(
        tmp_path,
        (
            {"TableType": "OneWay", "YFormat": "CV"},
            "CV",
            [column("YColumn", "A", ["10"], ["5"])],
        ),
        (
            {"TableType": "OneWay", "YFormat": "upper-lower-limits"},
            "Limits",
            [column("YColumn", "B", ["10"], ["12"], ["8"])],
        ),
    )
    cv, limits = read_prism(path).tables
    assert cells(cv.table) == [("A", "Y", [10.0]), ("A %CV", "Text", ["5"])]
    assert cv.notes == ("%CV values are kept as text columns.",)
    assert [c.role.value for c in limits.table.columns] == ["Y", "Text", "Text"]


def test_grouped_with_row_titles_and_replicates(tmp_path: Path) -> None:
    path = pzfx(
        tmp_path,
        (
            {"TableType": "TwoWay", "YFormat": "replicates", "Replicates": "2"},
            "Two-way",
            [
                column("RowTitlesColumn", None, ["Control", "Treated", None]),
                column("YColumn", "Male", ["1", "3", None], ["2", "4", None]),
                column("YColumn", "Female", ["5", "7", None], ["6", None, None]),
            ],
        ),
    )
    imported = only(path)
    table = imported.table
    assert [c.name for c in table.category_groups] == ["Male", "Female"]
    assert cells(table) == [
        ("Male: Control", "Y", [1.0, 2.0]),
        ("Male: Treated", "Y", [3.0, 4.0]),
        ("Female: Control", "Y", [5.0, 6.0]),
        ("Female: Treated", "Y", [7.0, None]),
    ]


def test_grouped_means_are_not_categories(tmp_path: Path) -> None:
    path = pzfx(
        tmp_path,
        (
            {"TableType": "TwoWay", "YFormat": "SE"},
            "Means",
            [
                column("RowTitlesColumn", None, ["Control", "Treated"]),
                column("YColumn", "Male", ["1", "3"], ["0.1", "0.3"]),
            ],
        ),
    )
    imported = only(path)
    assert not imported.table.category_groups
    assert cells(imported.table) == [
        ("Row title", "Text", ["Control", "Treated"]),
        ("Male", "Y", [1.0, 3.0]),
        ("Male SEM", "Error", [0.1, 0.3]),
    ]
    assert "not a category" in imported.notes[0]


def test_excluded_values_are_left_empty(tmp_path: Path) -> None:
    path = pzfx(
        tmp_path,
        (
            {"TableType": "OneWay"},
            "Excluded",
            [column("YColumn", "A", ["1", "!2", "3"])],
        ),
    )
    imported = only(path)
    assert cells(imported.table) == [("A", "Y", [1.0, None, 3.0])]
    assert imported.notes == ("1 excluded value left empty.",)
    assert imported.excluded == (ExcludedValue(0, 0, 1, "2"),)


def test_excluded_values_keep_their_place_in_x_and_replicates(tmp_path: Path) -> None:
    path = pzfx(
        tmp_path,
        (
            {"TableType": "XY", "XFormat": "numbers", "Replicates": "2"},
            "Places",
            [
                column("XColumn", "X", ["1", "!2"]),
                column("YColumn", "A", ["5", "6"]),
                column("YColumn", "B", ["7", "8"], ["9", "!10.5"]),
            ],
        ),
    )
    imported = only(path)
    assert imported.excluded == (
        ExcludedValue(X_DATA_SET, 0, 1, "2"),
        ExcludedValue(1, 1, 1, "10.5"),
    )
    assert imported.notes[-1] == "2 excluded values left empty."
    group_b = imported.table.group_columns(imported.table.replicate_groups[0].id)
    assert np.isnan(group_b[1].values[1])


def test_x_error_and_untitled_data_sets(tmp_path: Path) -> None:
    path = pzfx(
        tmp_path,
        (
            {"TableType": "XY", "XFormat": "error"},
            "X error",
            [
                column("XColumn", "", ["1", "2"], ["0.1", "0.1"]),
                column("YColumn", "", ["5", "6"]),
                column("YColumn", "", ["7", "8"]),
            ],
        ),
    )
    imported = only(path)
    assert [c.name for c in imported.table.columns] == ["X", "Data set A", "Data set B"]
    assert imported.notes == ("X error values are not imported.",)


def test_text_in_a_y_column_is_kept_as_text(tmp_path: Path) -> None:
    path = pzfx(
        tmp_path,
        ({"TableType": "OneWay"}, "Text", [column("YColumn", "A", ["1", "n.d."])]),
    )
    imported = only(path)
    assert cells(imported.table) == [("A", "Text", ["1", "n.d."])]
    assert "'A' hold text" in imported.notes[0]


def test_unsupported_and_invalid_tables_are_listed_with_a_reason(tmp_path: Path) -> None:
    path = pzfx(
        tmp_path,
        ({"TableType": "Contingency"}, "Counts", [column("YColumn", "A", ["4"])]),
        (
            {"TableType": "Survival", "XFormat": "numbers"},
            "Bad events",
            [column("XColumn", "Days", ["1", "2"]), column("YColumn", "A", ["1", "2"])],
        ),
        (
            {"TableType": "Survival", "XFormat": "numbers"},
            "Negative time",
            [column("XColumn", "Days", ["-1"]), column("YColumn", "A", ["1"])],
        ),
        ({"TableType": "OneWay"}, "Fine", [column("YColumn", "A", ["4"])]),
    )
    data = read_prism(path)
    assert [t.title for t in data.tables] == ["Fine"]
    reasons = {s.title: s.reason for s in data.skipped}
    assert reasons == {
        "Counts": "Contingency tables cannot be imported.",
        "Bad events": "'A' holds values other than 1 (event) and 0 (censored).",
        "Negative time": "A time is negative.",
    }


def test_duplicate_names_are_made_unique(tmp_path: Path) -> None:
    path = pzfx(
        tmp_path,
        (
            {"TableType": "OneWay"},
            "Same",
            [column("YColumn", "A", ["1"]), column("YColumn", "A", ["2"])],
        ),
        ({"TableType": "OneWay"}, "Same", [column("YColumn", "B", ["1"])]),
    )
    first, second = read_prism(path).tables
    assert [c.name for c in first.table.columns] == ["A", "A (2)"]
    assert (first.table.name, second.table.name) == ("Same", "Same (2)")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not xml at all", "not a Prism file"),
        (b"<?xml version='1.0'?><Other/>", "not a Prism file"),
        (b"PK\x03\x04broken", "not a Prism file"),
    ],
)
def test_files_that_are_not_prism_say_so(tmp_path: Path, content: bytes, message: str) -> None:
    path = tmp_path / "bad.pzfx"
    path.write_bytes(content)
    with pytest.raises(PrismImportError, match=message):
        read_prism(path)


def test_a_missing_file_says_so(tmp_path: Path) -> None:
    with pytest.raises(PrismImportError, match="could not be opened"):
        read_prism(tmp_path / "gone.prism")
