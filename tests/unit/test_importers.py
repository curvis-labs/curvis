"""Tests for CSV/TSV import: the fixture files in tests/data/csv and delimiter detection."""

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from curvis.data import ColumnRole
from curvis.io.importers import (
    PREVIEW_ROWS,
    CsvData,
    CsvImportError,
    CsvOptions,
    detect_delimiter,
    read_csv,
    read_preview,
)

DATA = Path(__file__).parents[1] / "data" / "csv"

nan = math.nan


def summary(data: CsvData) -> list[tuple[str, str, list[Any]]]:
    """(name, role, values) per column, with NaN as None so lists compare equal."""
    result = []
    for column in data.columns:
        values = [None if isinstance(v, float) and math.isnan(v) else v for v in column.values]
        result.append((column.name, column.role.value, values))
    return result


def read(name: str, **options: Any) -> CsvData:
    return read_csv(DATA / name, CsvOptions(**options))


# --- fixtures ----------------------------------------------------------------------


def test_basic_numbers_are_y_and_text_is_kept() -> None:
    data = read("basic.csv")
    assert data.delimiter == ","
    assert not data.decimal_comma
    assert data.encoding == "UTF-8"
    assert summary(data) == [
        ("Dose", "Y", [0.1, 1.0, 10.0]),
        ("Response", "Y", [12.5, 30.25, 88.0]),
        ("Label", "Text", ["low", "mid", "high"]),
    ]
    for column in data.columns:
        column.check()


def test_first_column_is_x() -> None:
    roles = [c.role for c in read("basic.csv", first_column_is_x=True).columns]
    assert roles == [ColumnRole.X, ColumnRole.Y, ColumnRole.TEXT]


def test_first_number_column_becomes_x_when_text_comes_first() -> None:
    roles = [c.role for c in read("header_row_3.csv", header_row=3, first_column_is_x=True).columns]
    assert roles == [ColumnRole.TEXT, ColumnRole.X]


def test_utf8_bom_is_not_part_of_the_first_name() -> None:
    data = read("bom.csv")
    assert data.columns[0].name == "Time"
    assert summary(data)[1] == ("Signal", "Y", [1.5, 2.5, 3.5])


def test_quoted_fields_keep_delimiters_quotes_and_line_breaks() -> None:
    assert summary(read("quoted.csv")) == [
        ("Sample", "Text", ["A", "B", "C"]),
        ("Value, mean", "Y", [1.5, 2.5, 3.5]),
        ("Note", "Text", ['says "hi"', "two\nlines", None]),
    ]


def test_empty_cells_are_nan_or_none_and_trailing_empty_rows_are_dropped() -> None:
    assert summary(read("empty_cells.csv")) == [
        ("X", "Y", [1.0, 2.0, 3.0]),
        ("Y1", "Y", [10.0, None, 30.0]),
        ("Y2", "Y", [None, 20.0, None]),
        ("Label", "Text", ["a", None, "c"]),
    ]


def test_semicolon_files_read_decimal_commas() -> None:
    data = read("semicolon_decimal_comma.csv")
    assert data.delimiter == ";"
    assert data.decimal_comma
    assert summary(data) == [
        ("Konzentration", "Y", [0.5, 1.0, 2.0, 0.001]),
        ("Wert", "Y", [1.25, 2.5, -3.75, 150.0]),
        ("Gruppe", "Text", ["A", "B", "A", "B"]),
    ]


def test_decimal_comma_can_be_switched_off() -> None:
    data = read("semicolon_decimal_comma.csv", decimal_comma=False)
    assert not data.decimal_comma
    assert [c.role for c in data.columns] == [ColumnRole.TEXT] * 3


def test_decimal_comma_never_applies_to_a_comma_delimiter() -> None:
    assert not read("basic.csv", delimiter=",", decimal_comma=True).decimal_comma


def test_tab_separated() -> None:
    data = read("tab.tsv")
    assert data.delimiter == "\t"
    assert summary(data) == [
        ("Dose", "Y", [1.0, 2.0]),
        ("Resp A", "Y", [2.5, 5.0]),
        ("Resp B", "Y", [3.0, 6.5]),
    ]


def test_space_separated_with_aligned_columns() -> None:
    data = read("space.txt")
    assert data.delimiter == " "
    assert summary(data) == [
        ("x", "Y", [1.0, 2.0]),
        ("y", "Y", [2.5, 5.0]),
        ("z", "Text", ["3", "six six"]),
    ]


def test_rows_above_the_header_are_skipped() -> None:
    assert summary(read("header_row_3.csv", header_row=3)) == [
        ("Well", "Text", ["A1", "A2"]),
        ("OD600", "Y", [0.123, 0.456]),
    ]


def test_no_header_names_columns_by_letter() -> None:
    assert summary(read("no_header.csv", header_row=None)) == [
        ("A", "Y", [1.0, 4.0]),
        ("B", "Y", [2.0, 5.0]),
        ("C", "Y", [3.0, 6.0]),
    ]


def test_a_cell_that_is_not_a_number_makes_the_column_text() -> None:
    data = read("mixed_text.csv")
    assert [c.role for c in data.columns] == [ColumnRole.Y, ColumnRole.TEXT, ColumnRole.TEXT]
    assert list(data.columns[1].values) == ["1.5", "n.d.", "2.5"]


def test_blank_and_duplicate_header_names_are_made_unique() -> None:
    names = [c.name for c in read("duplicate_headers.csv").columns]
    assert names == ["Rep", "Rep (2)", "A", "rep (3)"]


def test_windows_1252_text_is_decoded() -> None:
    data = read("windows_1252.csv")
    assert data.encoding == "Windows-1252"
    assert [c.name for c in data.columns] == ["Temp °C", "Résultat"]


def test_utf16_with_bom_is_decoded() -> None:
    data = read("utf16.tsv")
    assert data.encoding == "UTF-16"
    assert summary(data) == [("Dose", "Y", [1.0]), ("Response", "Y", [2.0])]


def test_crlf_line_endings() -> None:
    assert summary(read("crlf.csv")) == [("a", "Y", [1.0, 3.0]), ("b", "Y", [2.0, 4.0])]


# --- clear messages ----------------------------------------------------------------


@pytest.mark.parametrize("reader", [read_csv, read_preview])
@pytest.mark.parametrize(
    ("name", "options", "message"),
    [
        ("ragged.csv", {}, "Row 3 has 3 values, but there are only 2 columns."),
        ("empty.csv", {}, "The file is empty."),
        ("basic.csv", {"header_row": 9}, "The file has only 4 rows, so row 9 cannot be"),
    ],
)
def test_odd_files_give_a_clear_message(
    reader: Any, name: str, options: dict[str, Any], message: str
) -> None:
    with pytest.raises(CsvImportError, match=message):
        reader(DATA / name, CsvOptions(**options))


def test_empty_header_row_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "blank.csv"
    path.write_bytes(b"\na,b\n1,2\n")
    for reader in (read_csv, read_preview):
        with pytest.raises(CsvImportError, match="Row 1 is empty"):
            reader(path)
    assert read_csv(path, CsvOptions(header_row=2)).n_rows == 1


def test_binary_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "book.xlsx"
    path.write_bytes(b"PK\x03\x04\x00\x00\x00binary")
    with pytest.raises(CsvImportError, match="not a text file"):
        read_csv(path)


def test_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CsvImportError, match="could not be opened"):
        read_csv(tmp_path / "missing.csv")


def test_bad_options_are_refused() -> None:
    with pytest.raises(ValueError):
        CsvOptions(header_row=0)
    with pytest.raises(ValueError):
        CsvOptions(delimiter="|")  # type: ignore[arg-type]


# --- preview -----------------------------------------------------------------------


def test_preview_shows_skipped_rows_header_and_roles() -> None:
    preview = read_preview(DATA / "header_row_3.csv", CsvOptions(header_row=3))
    assert preview.rows[0] == ("Plate reader export",)
    assert preview.header_index == 2
    assert preview.data_start == 3
    assert preview.names == ("Well", "OD600")
    assert preview.roles == (ColumnRole.TEXT, ColumnRole.Y)
    assert not preview.more_rows


@pytest.mark.parametrize("path", sorted(DATA.glob("*")), ids=lambda p: p.name)
def test_preview_agrees_with_the_import(path: Path) -> None:
    for options in (CsvOptions(), CsvOptions(first_column_is_x=True), CsvOptions(header_row=None)):
        try:
            data = read_csv(path, options)
        except CsvImportError as exc:
            with pytest.raises(CsvImportError) as info:
                read_preview(path, options)
            assert str(info.value) == str(exc)
            continue
        preview = read_preview(path, options)
        assert preview.names == tuple(c.name for c in data.columns)
        assert preview.roles == tuple(c.role for c in data.columns)
        assert preview.delimiter == data.delimiter
        assert preview.decimal_comma == data.decimal_comma
        assert preview.encoding == data.encoding


def test_preview_stops_after_the_preview_rows(tmp_path: Path) -> None:
    path = tmp_path / "long.csv"
    path.write_text("n\n" + "".join(f"{i}\n" for i in range(PREVIEW_ROWS * 3)))
    preview = read_preview(path)
    assert len(preview.rows) == 1 + PREVIEW_ROWS
    assert preview.more_rows
    assert read_preview(path, rows=5).rows[-1] == ("4",)


def test_preview_of_a_file_larger_than_its_read_limit(tmp_path: Path) -> None:
    path = tmp_path / "big.csv"
    row = "1.5," + "x" * 1000 + "\n"
    path.write_text("a,b\n" + row * 5000)  # about 5 MB, beyond the 2 MB preview read
    preview = read_preview(path)
    assert preview.more_rows
    assert preview.roles == (ColumnRole.Y, ColumnRole.TEXT)


# --- delimiter detection -----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a,b,c\n1,2,3\n", ","),
        ("a;b\n1,5;2,5\n", ";"),  # decimal commas do not fool it
        ("a\tb\n1,5\t2,5\n", "\t"),
        ("a b c\n1 2 3\n", " "),
        ("Sample name,Value\nfirst one,1\n", ","),  # spaces inside cells
        ('"x;y",b\n"1;2",3\n', ","),  # delimiters inside quotes do not count
        ("one\ncolumn\n", ","),
    ],
)
def test_detect_delimiter(text: str, expected: str) -> None:
    assert detect_delimiter(text) == expected


def test_detect_delimiter_ignores_rows_above_the_header() -> None:
    text = "Export, version 2; final\nTitle\nA;B;C\n1;2;3\n4;5;6\n"
    assert detect_delimiter(text, skip_rows=2) == ";"


# --- round trips -------------------------------------------------------------------

finite = st.floats(allow_nan=False, allow_infinity=False, width=64)
cell = st.one_of(finite, st.none())


@given(st.lists(st.lists(cell, min_size=3, max_size=3), min_size=1, max_size=30))
def test_numbers_round_trip_through_csv(
    tmp_path_factory: Any, rows: list[list[float | None]]
) -> None:
    path = tmp_path_factory.mktemp("round") / "data.csv"
    lines = ["a,b,c"]
    lines += [",".join("" if v is None else repr(v) for v in row) for row in rows]
    path.write_text("\n".join(lines) + "\n")
    data = read_csv(path)
    expected = np.array([[nan if v is None else v for v in row] for row in rows])
    while len(expected) and np.isnan(expected[-1]).all():
        expected = expected[:-1]  # trailing empty rows are dropped
    got = np.column_stack([c.values for c in data.columns]) if data.n_rows else np.empty((0, 3))
    np.testing.assert_array_equal(got, expected)
    assert all(c.role is ColumnRole.Y for c in data.columns)


# --- row and column ranges -----------------------------------------------------------

RANGES = DATA / "ranges.csv"  # title, header in row 2, data in rows 3-6, a long total row 7


def test_row_range_uses_file_row_numbers() -> None:
    data = read("ranges.csv", header_row=2, first_row=4, last_row=5)
    assert summary(data)[:2] == [("Sample", "Text", ["s2", "s3"]), ("Dose", "Y", [2.0, 3.0])]


def test_first_row_above_the_header_starts_under_it() -> None:
    data = read("ranges.csv", header_row=2, first_row=1, last_row=4)
    assert list(data.columns[0].values) == ["s1", "s2"]


def test_column_range_and_first_column_is_x_in_range() -> None:
    data = read(
        "ranges.csv",
        header_row=2,
        last_row=6,
        first_column=2,
        last_column=3,
        first_column_is_x=True,
    )
    assert summary(data) == [
        ("Dose", "X", [1.0, 2.0, 3.0, 4.0]),
        ("Resp", "Y", [10.0, 20.0, 30.0, 40.0]),
    ]


def test_long_row_outside_the_range_is_ignored() -> None:
    with pytest.raises(CsvImportError, match="Row 7 has 5 values"):
        read("ranges.csv", header_row=2)
    assert read("ranges.csv", header_row=2, last_row=6).n_rows == 4
    preview = read_preview(RANGES, CsvOptions(header_row=2, last_row=6))
    assert len(preview.rows) - preview.data_start == 4
    assert not preview.more_rows


@pytest.mark.parametrize("reader", [read_csv, read_preview])
@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"header_row": 2, "first_row": 9}, "The file has only 7 rows, so there is no row 9."),
        ({"header_row": 2, "last_row": 6, "last_column": 5}, "The file has only 4 columns."),
        ({"header_row": 2, "last_row": 6, "first_column": 5}, "The file has only 4 columns."),
        ({"header_row": 2, "first_row": 5, "last_row": 4}, "The rows run from 5 to 4."),
        ({"header_row": 2, "last_row": 2}, r"Row 2 is above the first data row \(3\)."),
        ({"header_row": 2, "first_column": 3, "last_column": 2}, "The columns run from 3 to 2."),
        ({"header_row": 2, "first_row": 4}, "Row 7 has 5 values"),  # the long row is in range
    ],
)
def test_bad_ranges_give_a_clear_message(
    reader: Any, options: dict[str, Any], message: str
) -> None:
    with pytest.raises(CsvImportError, match=message):
        reader(RANGES, CsvOptions(**options))


def test_preview_numbers_the_rows_shown_and_marks_the_column_range() -> None:
    options = CsvOptions(header_row=2, first_row=5, last_row=6, first_column=2)
    preview = read_preview(RANGES, options)
    assert preview.row_numbers == (1, 2, 5, 6)
    assert preview.rows[2] == ("s3", "3", "30", "c")
    assert preview.column_start == 1
    assert preview.names == ("Dose", "Resp", "Note")


@pytest.mark.parametrize(
    "options",
    [
        CsvOptions(header_row=2, first_row=4, last_row=6),
        CsvOptions(header_row=2, last_row=5, first_column=2, first_column_is_x=True),
        CsvOptions(header_row=None, first_row=3, last_row=6, last_column=3),
    ],
)
def test_preview_agrees_with_the_import_for_ranges(options: CsvOptions) -> None:
    data = read_csv(RANGES, options)
    preview = read_preview(RANGES, options)
    assert preview.names == tuple(c.name for c in data.columns)
    assert preview.roles == tuple(c.role for c in data.columns)
    assert len(preview.rows) - preview.data_start == data.n_rows


def test_preview_reads_rows_far_down_the_file(tmp_path: Path) -> None:
    path = tmp_path / "long.csv"
    path.write_text("n\n" + "".join(f"{i}\n" for i in range(PREVIEW_ROWS * 50)))
    preview = read_preview(path, CsvOptions(first_row=4000, last_row=4002))
    assert preview.row_numbers == (1, 4000, 4001, 4002)
    assert preview.rows[1:] == (("3998",), ("3999",), ("4000",))


def test_range_options_start_at_1() -> None:
    for name in ("first_row", "last_row", "first_column", "last_column"):
        with pytest.raises(ValueError):
            CsvOptions(**{name: 0})
