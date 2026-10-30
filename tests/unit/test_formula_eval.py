"""Tests for compiling and evaluating formulas on columns."""

import math
import re
import warnings
from pathlib import Path

import numpy as np
import pytest

from curvis.formula import (
    BUILTIN_VALUES,
    FormulaError,
    check,
    compile_formula,
    evaluate,
    parse,
    resolve_constants,
)

NAN = math.nan
COLUMNS = {
    "A": np.array([1.0, 2.0, NAN, 4.0, -3.0]),
    "B": np.array([2.0, 0.0, 5.0, -2.0, 3.0]),
    "Dose (µM)": np.array([0.1, 1.0, 10.0, 100.0, 1000.0]),
    "Empty": np.full(5, NAN),
    "Int": np.array([1, 2, 3, 4, 5]),
}
CONSTANTS = resolve_constants()


def run(text: str, columns: dict[str, np.ndarray] | None = None, n_rows: int = 5) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # no NumPy warnings may leak out
        return evaluate(text, COLUMNS if columns is None else columns, CONSTANTS, n_rows)


def same(actual: np.ndarray, expected: list[float]) -> None:
    np.testing.assert_array_equal(actual, np.array(expected, dtype=np.float64))


# --- operators -------------------------------------------------------------------


def test_add() -> None:
    same(run("[A] + [B]"), [3, 2, NAN, 2, 0])
    same(run("[A] + 1"), [2, 3, NAN, 5, -2])


def test_subtract_both_spellings() -> None:
    same(run("[A] - [B]"), [-1, 2, NAN, 6, -6])
    same(run("[A] − [B]"), [-1, 2, NAN, 6, -6])


def test_multiply_both_spellings() -> None:
    same(run("[A] * [B]"), [2, 0, NAN, -8, -9])
    same(run("[A] × [B]"), [2, 0, NAN, -8, -9])


def test_divide_both_spellings_and_division_by_zero_gives_nan() -> None:
    same(run("[A] / [B]"), [0.5, NAN, NAN, -2, -1])
    same(run("[A] ÷ [B]"), [0.5, NAN, NAN, -2, -1])
    same(run("[A] / 0"), [NAN] * 5)
    same(run("0 / 0"), [NAN] * 5)
    same(run("1 / (1 / 0)"), [NAN] * 5)  # not 1/inf = 0
    same(run("1 / -0"), [NAN] * 5)


def test_modulo_takes_the_sign_of_the_divisor() -> None:
    same(run("7 % 3"), [1] * 5)
    same(run("-7 % 3"), [2] * 5)  # -(7 % 3) would be -1; unary minus binds tighter
    same(run("(-7) % 3"), [2] * 5)
    same(run("7 % -3"), [-2] * 5)
    same(run("5.5 % 2"), [1.5] * 5)
    same(run("[A] % 0"), [NAN] * 5)
    same(run("[A] % [B]"), [1, NAN, NAN, 0, 0])


def test_power() -> None:
    same(run("[A] ^ 2"), [1, 4, NAN, 16, 9])
    same(run("2 ^ [B]"), [4, 1, 32, 0.25, 8])
    same(run("2^3^2"), [512] * 5)
    same(run("-2^2"), [-4] * 5)
    same(run("(-2)^2"), [4] * 5)
    same(run("4^0.5"), [2] * 5)
    same(run("0^0"), [1] * 5)


def test_undefined_and_overflowing_powers_give_nan() -> None:
    same(run("(-8)^(1/3)"), [NAN] * 5)
    same(run("0^-1"), [NAN] * 5)
    same(run("10^400"), [NAN] * 5)
    same(run("-(10^400)"), [NAN] * 5)
    same(run("10^400 - 10^400"), [NAN] * 5)


def test_overflow_of_multiplication_gives_nan() -> None:
    same(run("1e300 * 1e300"), [NAN] * 5)
    same(run("1 / (1e300 * 1e300)"), [NAN] * 5)


def test_unary_minus_and_plus() -> None:
    same(run("-[A]"), [-1, -2, NAN, -4, 3])
    same(run("−[A]"), [-1, -2, NAN, -4, 3])
    same(run("+[A]"), [1, 2, NAN, 4, -3])
    same(run("--[A]"), [1, 2, NAN, 4, -3])
    same(run("2 - -1"), [3] * 5)


def test_precedence() -> None:
    same(run("1 + 2 * 3 ^ 2"), [19] * 5)
    same(run("(1 + 2) * 3"), [9] * 5)
    same(run("10 - 4 - 3"), [3] * 5)
    same(run("12 / 3 / 2"), [2] * 5)
    same(run("2 * 3 % 4"), [2] * 5)  # (2*3) % 4


def test_missing_values_propagate() -> None:
    assert np.isnan(run("[A] * 0 + 1")[2])
    same(run("[Empty] + 1"), [NAN] * 5)


# --- columns ---------------------------------------------------------------------


def test_column_names_ignore_case_and_may_contain_spaces() -> None:
    same(run("[a] + [DOSE (µm)]"), [1.1, 3, NAN, 104, 997])


def test_integer_columns_are_read_as_numbers() -> None:
    same(run("[Int] / 2"), [0.5, 1, 1.5, 2, 2.5])


def test_result_is_a_new_float_array() -> None:
    result = run("[A]")
    assert result.dtype == np.float64
    assert result is not COLUMNS["A"]
    assert not np.shares_memory(result, COLUMNS["A"])
    result[0] = 99  # writable, and the column is untouched
    assert COLUMNS["A"][0] == 1


def test_input_columns_are_not_changed() -> None:
    before = {k: v.copy() for k, v in COLUMNS.items()}
    run("-[A] / [B] % 2 ^ [Dose (µM)] + ROW([A], [Int]) + +[B]")
    for name, values in before.items():
        np.testing.assert_array_equal(COLUMNS[name], values)


def test_infinite_input_values_give_nan() -> None:
    same(run("[X]", {"X": np.array([np.inf, -np.inf, 1.0])}, 3), [NAN, NAN, 1])


def test_numbers_only_formula_fills_every_row() -> None:
    same(run("2 * pi", n_rows=3), [2 * math.pi] * 3)
    assert run("1", {}, 0).shape == (0,)


# --- column functions ------------------------------------------------------------


def test_first_and_last_skip_missing_values() -> None:
    cols = {"X": np.array([NAN, 3.0, 5.0, 7.0, NAN])}
    same(run("FIRST([X])", cols), [3] * 5)
    same(run("LAST([X])", cols), [7] * 5)
    same(run("[X] - FIRST([X])", cols), [NAN, 0, 2, 4, NAN])


def test_min_max() -> None:
    same(run("MIN([A])"), [-3] * 5)
    same(run("MAX([A])"), [4] * 5)


def test_mean_sum_count_skip_missing_values() -> None:
    same(run("MEAN([A])"), [1] * 5)  # (1 + 2 + 4 - 3) / 4
    same(run("SUM([A])"), [4] * 5)
    same(run("COUNT([A])"), [4] * 5)


def test_all_missing() -> None:
    for text in ("FIRST", "LAST", "MIN", "MAX", "MEAN"):
        same(run(f"{text}([Empty])"), [NAN] * 5)
    same(run("SUM([Empty])"), [0] * 5)
    same(run("COUNT([Empty])"), [0] * 5)


def test_one_row() -> None:
    cols = {"X": np.array([4.0])}
    for text in ("FIRST", "LAST", "MIN", "MAX", "MEAN", "SUM"):
        same(run(f"{text}([X])", cols, 1), [4])
    same(run("COUNT([X])", cols, 1), [1])


def test_zero_rows() -> None:
    cols = {"X": np.array([], dtype=np.float64)}
    for text in ("FIRST", "LAST", "MIN", "MAX", "MEAN", "SUM", "COUNT"):
        assert run(f"{text}([X])", cols, 0).shape == (0,)
    assert run("ROW([X], 1)", cols, 0).shape == (0,)


def test_functions_of_expressions() -> None:
    same(run("SUM([A] * 2)"), [8] * 5)
    same(run("MEAN([A] / [B])"), [(0.5 - 2 - 1) / 3] * 5)  # x/0 and missing rows skipped
    same(run("MAX(-[A])"), [3] * 5)
    same(run("MEAN(MEAN([A]))"), [1] * 5)


def test_functions_of_a_number_count_it_once_per_row() -> None:
    same(run("COUNT(1)"), [5] * 5)
    same(run("SUM(2)"), [10] * 5)
    same(run("MEAN(7)"), [7] * 5)
    same(run("FIRST(7)"), [7] * 5)
    same(run("COUNT(1 / 0)"), [0] * 5)


def test_mean_is_accurate() -> None:
    values = np.full(100_000, 0.1)
    result = run("MEAN([X])", {"X": values}, len(values))
    assert result[0] == pytest.approx(0.1, rel=1e-14)


def test_sum_overflow_gives_nan() -> None:
    same(run("SUM([X])", {"X": np.array([1e308, 1e308])}, 2), [NAN, NAN])


def test_row() -> None:
    same(run("ROW([B], 1)"), [2] * 5)
    same(run("ROW([B], 5)"), [3] * 5)
    same(run("ROW([A], 3)"), [NAN] * 5)  # the cell itself is missing
    same(run("[B] / ROW([B], 1)"), [1, 0, 2.5, -1, 1.5])


@pytest.mark.parametrize("n", ["0", "6", "-1", "1.5", "0/0", "1/0", "10^400"])
def test_row_outside_the_table_or_not_whole_gives_nan(n: str) -> None:
    same(run(f"ROW([B], {n})"), [NAN] * 5)


def test_row_with_a_column_of_row_numbers() -> None:
    cols = {"X": np.array([10.0, 20.0, 30.0]), "N": np.array([3.0, NAN, 1.5])}
    same(run("ROW([X], [N])", cols, 3), [30, NAN, NAN])
    same(run("ROW([X], 4 - [Int])", {**cols, "Int": np.array([1, 2, 3])}, 3), [30, 20, 10])


def test_row_of_an_expression() -> None:
    same(run("ROW([B] * 10, 3)"), [50] * 5)


def test_normalize_to_percent() -> None:
    cols = {"Y": np.array([2.0, 4.0, NAN, 6.0])}
    same(run("([Y] - MIN([Y])) / (MAX([Y]) - MIN([Y])) * 100", cols, 4), [0, 50, NAN, 100])


def test_repeated_column_functions_are_computed_once() -> None:
    compiled = compile_formula("([A] - MIN([A])) / (MAX([A]) - MIN([A]))")
    assert len(compiled._calls) == 2


# --- constants -------------------------------------------------------------------


def test_builtin_constants() -> None:
    same(run("pi"), [math.pi] * 5)
    same(run("π", n_rows=1), [math.pi])
    same(run("e", n_rows=1), [math.e])
    same(run("R / N_A", n_rows=1), [BUILTIN_VALUES["R"] / BUILTIN_VALUES["N_A"]])


def test_user_constants() -> None:
    constants = {**CONSTANTS, "k_on": 2.0}
    same(evaluate("[A] * k_on", COLUMNS, constants, 5), [2, 4, NAN, 8, -6])


# --- errors ----------------------------------------------------------------------


def error(text: str, columns: dict[str, np.ndarray] | None = None) -> FormulaError:
    with pytest.raises(FormulaError) as info:
        run(text, columns)
    return info.value


def test_unknown_column() -> None:
    e = error("[A] + [Signal]")
    assert e.reason == "Unknown column 'Signal'"
    assert (e.start, e.end) == (6, 14)


def test_unknown_constant() -> None:
    e = error("[A] * k_off")
    assert e.reason == "Unknown constant 'k_off'"
    assert (e.start, e.end) == (6, 11)


def test_unknown_constant_hints() -> None:
    assert error("2 * Pi").reason == "Unknown constant 'Pi'. Did you mean 'pi'?"
    assert error("B + 1").reason == (
        "Unknown constant 'B'. Put column names in square brackets: [B]"
    )
    assert error("mean").reason == "MEAN is a function: write MEAN(column)"


def test_first_problem_in_the_text_is_reported() -> None:
    e = error("k + [Nope] + [Also nope]")
    assert e.reason == "Unknown constant 'k'"


def test_text_column() -> None:
    labels = np.array(["a", None, "c", "d", "e"], dtype=object)
    e = error("1 + [Label]", {**COLUMNS, "Label": labels})
    assert e.reason == "Column 'Label' holds text, not numbers"
    assert (e.start, e.end) == (4, 11)


def test_wrong_column_length_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="has 5 rows, expected 4"):
        run("[A]", n_rows=4)


def test_check_without_data() -> None:
    check("[A] * k + MEAN([b])", ["a", "B"], ["k"])
    with pytest.raises(FormulaError, match="Unknown column 'C'"):
        check(parse("[C]"), ["A"], [])


def test_parse_errors_come_through_evaluate() -> None:
    assert error("1 +").reason == "The formula ends where a value is expected"


# --- safety ----------------------------------------------------------------------


def test_formula_package_never_uses_eval_or_exec() -> None:
    import curvis.formula

    package = Path(curvis.formula.__file__).parent
    for path in package.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        # Built-in eval/exec/compile (re.compile is fine), dynamic imports and attributes.
        pattern = r"(?<![\w.])(eval|exec|compile)\s*\(|__import__|importlib|getattr"
        assert not re.search(pattern, source), f"{path.name}"
