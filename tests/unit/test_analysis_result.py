"""The result model: significance stars, number and P-value text, copy text."""

import math
import pickle

import pytest

from curvis.analysis.result import (
    STARS_FOOTNOTE,
    Kind,
    Result,
    ResultColumn,
    ResultTable,
    Value,
    copied,
    format_number,
    format_p,
    grids,
    stars,
)


@pytest.mark.parametrize(
    ("p", "expected"),
    [
        (0.0, "***"),
        (0.000999, "***"),
        (0.001, "**"),
        (0.00999, "**"),
        (0.01, "*"),
        (0.0499999, "*"),
        (0.05, ""),
        (0.5, ""),
        (1.0, ""),
        (math.nan, ""),
        (None, ""),
    ],
)
def test_stars_use_the_documented_thresholds(p: float | None, expected: str) -> None:
    assert stars(p) == expected
    assert STARS_FOOTNOTE == "* P < 0.05, ** P < 0.01, *** P < 0.001."


@pytest.mark.parametrize(
    ("p", "text"),
    [
        (0.0, "< 0.001"),
        (0.000999, "< 0.001"),
        (0.001, "0.00100"),
        (0.0312, "0.0312"),
        (0.03124, "0.0312"),
        (0.452, "0.452"),
        (0.99996, "1.00"),
        (1.0, "1.00"),
        (math.nan, ""),
    ],
)
def test_p_values_are_shown_like_the_mockup(p: float, text: str) -> None:
    assert format_p(p) == text


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0.0, "0"),
        (10.8, "10.80"),
        (0.23094, "0.2309"),
        (-4.8531, "-4.853"),
        (9.99996, "10.00"),
        (12345.6, "12346"),
        (1.2345e-5, "1.234e-05"),
        (2.5e7, "2.500e+07"),
        (math.inf, "∞"),
        (-math.inf, "−∞"),
        (math.nan, ""),
    ],
)
def test_numbers_are_shown_with_four_significant_digits(value: float, text: str) -> None:
    assert format_number(value) == text


def test_copied_numbers_keep_full_precision() -> None:
    assert copied(0.1 + 0.2, Kind.NUMBER) == "0.30000000000000004"
    assert copied(0.000123456789, Kind.P_VALUE) == "0.000123456789"
    assert copied(3.0, Kind.NUMBER) == "3"
    assert copied(7, Kind.INTEGER) == "7"
    assert copied(math.nan, Kind.NUMBER) == ""
    assert copied(None, Kind.TEXT) == ""
    assert copied(math.inf, Kind.NUMBER) == "Infinity"


def test_a_p_value_column_gets_a_stars_column() -> None:
    table = ResultTable(
        "Dunnett's multiple comparisons test",
        (
            ResultColumn("Comparison", Kind.TEXT),
            ResultColumn("Mean diff."),
            ResultColumn("Adj. P", Kind.P_VALUE),
        ),
        (("Drug A vs. Control", 2.63, 0.0004), ("Drug B vs. Control", 0.4, 0.2)),
    )
    result = Result("Ordinary one-way ANOVA", "The group means differ.", tables=(table,))
    (grid,) = grids(result)
    assert grid.header == ("Comparison", "Mean diff.", "Adj. P", "")
    assert grid.shown[0] == ("Drug A vs. Control", "2.630", "< 0.001", "***")
    assert grid.copied[0] == ("Drug A vs. Control", "2.63", "0.0004", "***")
    assert grid.shown[1][-1] == ""
    assert grid.numeric == (False, True, True, False)
    assert result.has_p_values


def test_named_values_come_first_with_stars_for_p_values() -> None:
    result = Result(
        "Unpaired t test",
        "The means differ.",
        values=(
            Value("t", 3.2),
            Value("P value", 0.004, Kind.P_VALUE),
            Value("Mean", 2.0, unit="mM"),
        ),
    )
    grid = grids(result)[0]
    assert grid.title == "Unpaired t test"
    assert grid.shown == (
        ("t", "3.200", ""),
        ("P value", "0.00400", "**"),
        ("Mean (mM)", "2.000", ""),
    )


def test_rows_must_match_the_columns() -> None:
    with pytest.raises(ValueError, match="2 cells for 1 columns"):
        ResultTable("T", (ResultColumn("a"),), ((1.0, 2.0),))


def test_results_pickle_for_worker_processes() -> None:
    result = Result(
        "M",
        "S",
        ("w",),
        (Value("x", 1.0),),
        (ResultTable("T", (ResultColumn("a"),), ((1.0,),)),),
    )
    assert pickle.loads(pickle.dumps(result)) == result
