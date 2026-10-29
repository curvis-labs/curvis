"""Tests for table cell text, column names and Excel-style clipboard text."""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from curvis.data import Table
from curvis.data.names import column_letters, next_column_names
from curvis.ui.table.clipboard import from_tsv, to_tsv
from curvis.ui.table.format import cell_text, fitted_number_text, number_text


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (3.0, "3"),
        (-0.0, "0"),
        (0.1, "0.1"),
        (1 / 3, "0.3333333333333333"),
        (1e-5, "1e-05"),
        (1e20, "1e+20"),
        (123456789012.0, "123456789012"),
        (math.nan, ""),
        (math.inf, "inf"),
    ],
)
def test_number_text(value: float, text: str) -> None:
    assert number_text(value) == text


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_number_text_reads_back_exactly(value: float) -> None:
    assert float(number_text(value)) == value


def test_cell_text_rounds_only_when_decimals_are_given() -> None:
    assert cell_text(1 / 3, 4) == "0.3333"
    assert cell_text(2.0, 2) == "2.00"
    assert cell_text(math.nan, 2) == ""
    assert cell_text("abc", 2) == "abc"
    assert cell_text(None) == ""


def test_column_letters() -> None:
    assert [column_letters(i) for i in (0, 1, 25, 26, 27, 51, 52, 701, 702)] == [
        "A", "B", "Z", "AA", "AB", "AZ", "BA", "ZZ", "AAA"
    ]  # fmt: skip


def test_next_column_names_skip_taken_names_ignoring_case() -> None:
    assert next_column_names(["X", "a", "C"], 3) == ["B", "D", "E"]


def test_blank_table() -> None:
    table = Table.blank()
    assert table.name == "Data 1"
    assert [(c.name, c.role.value) for c in table.columns] == [("X", "X"), ("A", "Y")]
    assert table.n_rows == 0


# --- clipboard text ----------------------------------------------------------------


def test_to_tsv_matches_excel() -> None:
    assert to_tsv([["1", "2"], ["3", ""]]) == "1\t2\r\n3\t\r\n"


def test_from_tsv_reads_excel_text() -> None:
    assert from_tsv("Dose\tResp\r\n1\t10\r\n2\t20\r\n") == [
        ["Dose", "Resp"],
        ["1", "10"],
        ["2", "20"],
    ]


def test_from_tsv_accepts_other_line_breaks_and_no_final_break() -> None:
    assert from_tsv("1\t2\n3\t4") == [["1", "2"], ["3", "4"]]
    assert from_tsv("1\r2\r") == [["1"], ["2"]]


def test_from_tsv_pads_short_rows_and_keeps_empty_cells() -> None:
    assert from_tsv("1\t\t3\r\n4\r\n\r\n") == [["1", "", "3"], ["4", "", ""], ["", "", ""]]


def test_from_tsv_reads_quoted_cells() -> None:
    text = '"two\nlines"\t"a ""quote"""\r\n"x\ty"\tplain\r\n'
    assert from_tsv(text) == [["two\nlines", 'a "quote"'], ["x\ty", "plain"]]


def test_from_tsv_takes_a_stray_quote_literally() -> None:
    assert from_tsv('5" screen\t"open\r\n') == [['5" screen', '"open']]


def test_from_tsv_empty() -> None:
    assert from_tsv("") == []


cells = st.text(alphabet=st.characters(codec="utf-8", exclude_categories=["Cs"]), max_size=8)


@given(
    st.integers(1, 4).flatmap(
        lambda w: st.lists(st.lists(cells, min_size=w, max_size=w), min_size=1, max_size=5)
    )
)
def test_tsv_round_trip(rows: list[list[str]]) -> None:
    assert from_tsv(to_tsv(rows)) == rows


@pytest.mark.parametrize(
    ("value", "width", "text"),
    [
        (107.64052345967664, 12, "107.64052346"),
        (107.64052345967664, 5, "107.6"),
        (-107.64052345967664, 5, "-108"),
        (123456.0, 6, "123456"),
        (1234567.0, 6, "1e+06"),
        (1234567.0, 3, "###"),
        (1 / 3, 6, "0.3333"),
        (2.5e-12, 7, "2.5e-12"),
        (math.nan, 3, ""),
        (math.inf, 3, "inf"),
    ],
)
def test_fitted_number_text(value: float, width: int, text: str) -> None:
    assert fitted_number_text(value, width) == text


def test_fitted_number_text_applies_decimal_places_when_they_fit() -> None:
    assert fitted_number_text(1 / 3, 10, 4) == "0.3333"
    assert fitted_number_text(12345.6789, 6, 4) == "12346"


@given(st.floats(allow_nan=False, allow_infinity=False), st.integers(1, 20))
def test_fitted_number_text_always_fits(value: float, width: int) -> None:
    assert len(fitted_number_text(value, width)) <= width
