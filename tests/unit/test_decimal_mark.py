"""Tests for decimal mark detection and reading numbers with a decimal mark."""

import math

import polars as pl
import pytest
from hypothesis import given
from hypothesis import strategies as st

from curvis.io.decimal_mark import DecimalGuess, classify, detect, parse_numbers


@pytest.mark.parametrize(
    ("cell", "kind"),
    [
        ("1,5", "comma"),
        ("0,123", "comma"),  # a thousands group never follows a lone 0
        ("12,3456", "comma"),
        (",5", "comma"),
        ("-2,25", "comma"),
        ("1,5e3", "comma"),
        ("1.234,5", "comma"),
        ("1.234.567", "comma"),
        ("1.5", "point"),
        ("1,234.5", "point"),
        ("1,234,567", "point"),
        ("1234,567", "comma"),  # four digits before the mark: no thousands group
        ("1,234", "ambiguous"),
        ("999.999", "ambiguous"),
        ("-1,234", "ambiguous"),
        ("12", None),
        ("abc", None),
        ("1,2,3", None),
        ("1.234,5.6", None),
        ("", None),
    ],
)
def test_classify(cell: str, kind: str | None) -> None:
    assert classify(cell) == kind


def test_detect_needs_cells_that_settle_it() -> None:
    assert detect(["1,5", "2", None], False) == DecimalGuess(True, False)
    assert detect(["1.5", "1,234.5"], True) == DecimalGuess(False, False)
    # Only ambiguous cells: the default, and unsure.
    assert detect(["1,234", "5,678"], False) == DecimalGuess(False, True)
    assert detect(["1,234"], True) == DecimalGuess(True, True)
    # Whole numbers and text say nothing; the mark does not matter, so it is not unsure.
    assert detect(["12", "x"], True) == DecimalGuess(True, False)
    # Conflicting cells: the default, and unsure.
    assert detect(["1,5", "2.5"], False) == DecimalGuess(False, True)


def values(series: pl.Series) -> list[float | None]:
    return series.to_list()


def test_parse_with_a_comma() -> None:
    cells = pl.Series(["1,5", "1.234,5", "2.500", "1.5", "-0,25", "x", None, "1,234"])
    assert values(parse_numbers(cells, True)) == [
        1.5,
        1234.5,
        2500.0,
        1.5,
        -0.25,
        None,
        None,
        1.234,
    ]


def test_parse_with_a_point() -> None:
    cells = pl.Series(["1.5", "1,234.5", "1,234", "1,5", "1e-3", None])
    assert values(parse_numbers(cells, False)) == [1.5, 1234.5, 1234.0, None, 0.001, None]


@given(st.floats(allow_nan=False, allow_infinity=False, min_value=-1e12, max_value=1e12))
def test_a_number_written_with_a_comma_reads_back(value: float) -> None:
    text = repr(value)
    if "e" in text:
        return
    comma = text.replace(".", ",")
    (read,) = values(parse_numbers(pl.Series([comma]), True))
    assert read is not None and math.isclose(read, value, rel_tol=0, abs_tol=0)
