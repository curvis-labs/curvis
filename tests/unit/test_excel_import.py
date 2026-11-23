"""Tests for Excel import (.xlsx with fastexcel, .xls with xlrd); fixtures in tests/data/excel."""

import math
from pathlib import Path
from typing import Any

import pytest

from curvis.data import ColumnRole
from curvis.io.excel import sheet_names
from curvis.io.importers import CsvData, CsvImportError, CsvOptions, read_data, read_preview

DATA = Path(__file__).parents[1] / "data" / "excel"


def summary(data: CsvData) -> list[tuple[str, str, list[Any]]]:
    result = []
    for column in data.columns:
        cells = [None if isinstance(v, float) and math.isnan(v) else v for v in column.values]
        result.append((column.name, column.role.value, cells))
    return result


@pytest.mark.parametrize("name", ["basic.xlsx", "basic.xls"])
def test_first_sheet_by_default(name: str) -> None:
    data = read_data(DATA / name)
    assert data.delimiter is None
    assert data.encoding == "Excel"
    assert summary(data) == [
        ("Dose", "Y", [0.1, 1.0, 10.0]),
        ("Response", "Y", [12.5, 30.25, 88.0]),
        ("Label", "Text", ["low", "mid", "high"]),
    ]


@pytest.mark.parametrize("name", ["basic.xlsx", "basic.xls"])
def test_sheets_are_listed_and_chosen(name: str) -> None:
    assert sheet_names(DATA / name) == ("Results", "Plate 2", "Empty")
    options = CsvOptions(sheet="Plate 2", header_row=3, first_column_is_x=True)
    data = read_data(DATA / name, options)
    assert summary(data) == [
        ("Well", "Text", ["A1", "A2", "A3"]),
        ("OD", "X", [0.254, 1e-9, None]),
        ("Time", "Y", [0.0, 5.0, 10.0]),
    ]


@pytest.mark.parametrize("name", ["basic.xlsx", "basic.xls"])
def test_preview_matches_the_import(name: str) -> None:
    options = CsvOptions(sheet="Plate 2", header_row=3)
    preview = read_preview(DATA / name, options)
    assert preview.sheets == ("Results", "Plate 2", "Empty")
    assert preview.sheet == "Plate 2"
    assert preview.header_index == 2
    assert preview.row_numbers == (1, 2, 3, 4, 5, 6)
    assert preview.rows[4] == ("A2", "1e-09", "5")  # a number cell in its shortest form
    data = read_data(DATA / name, options)
    assert preview.names == tuple(c.name for c in data.columns)
    assert preview.roles == tuple(c.role for c in data.columns)


@pytest.mark.parametrize("name", ["basic.xlsx", "basic.xls"])
def test_ranges_apply_to_sheets(name: str) -> None:
    options = CsvOptions(sheet="Plate 2", header_row=3, first_row=5, first_column=2)
    assert summary(read_data(DATA / name, options)) == [
        ("OD", "Y", [1e-9, None]),
        ("Time", "Y", [5.0, 10.0]),
    ]


@pytest.mark.parametrize("name", ["basic.xlsx", "basic.xls"])
def test_empty_and_missing_sheets_say_so(name: str) -> None:
    with pytest.raises(CsvImportError, match=r"The sheet 'Empty' is empty\."):
        read_data(DATA / name, CsvOptions(sheet="Empty"))
    with pytest.raises(CsvImportError, match="no sheet called 'Nope'"):
        read_data(DATA / name, CsvOptions(sheet="Nope"))
    with pytest.raises(CsvImportError, match="cannot be the header"):
        read_data(DATA / name, CsvOptions(header_row=50))


def test_text_cells_follow_the_decimal_mark_and_number_cells_do_not() -> None:
    data = read_data(DATA / "text_cells.xlsx", CsvOptions(sheet="Comma"))
    assert data.decimal_comma and not data.decimal_unsure
    assert summary(data) == [
        ("Conc", "Y", [0.5, 1.5, 2.25]),
        ("Signal", "Y", [1234.5, 2500.0, 12.0]),  # "2.500" is a text cell: 2500
        ("Number cells", "Y", [0.5, 1.25, 2.0]),
    ]
    point = read_data(DATA / "text_cells.xlsx", CsvOptions(sheet="Comma", decimal_comma=False))
    assert summary(point)[0][1] == "Text"
    assert summary(point)[2] == ("Number cells", "Y", [0.5, 1.25, 2.0])


def test_ambiguous_text_cells_use_the_default() -> None:
    for default, value in ((False, 1234.0), (True, 1.234)):
        options = CsvOptions(sheet="Ambiguous", default_decimal_comma=default)
        data = read_data(DATA / "text_cells.xlsx", options)
        assert data.decimal_unsure
        assert data.decimal_comma is default
        assert data.columns[0].values[0] == value


def test_dates_and_booleans_are_text() -> None:
    data = read_data(DATA / "text_cells.xlsx", CsvOptions(sheet="Mixed"))
    roles = {c.name: c.role for c in data.columns}
    assert roles["Date"] is ColumnRole.TEXT
    assert roles["Flag"] is ColumnRole.TEXT
    assert summary(data)[3] == ("Value", "Y", [1.5, None])  # "NA" is missing


def test_a_damaged_workbook_says_so(tmp_path: Path) -> None:
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"not a workbook")
    with pytest.raises(CsvImportError, match="could not be read"):
        read_data(path)
    old = tmp_path / "broken.xls"
    old.write_bytes(b"not a workbook")
    with pytest.raises(CsvImportError, match="could not be read"):
        read_data(old)
