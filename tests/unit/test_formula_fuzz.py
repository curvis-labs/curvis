"""Fuzzing the formula parser and compiler with hypothesis.

- Any text either parses or raises FormulaError, never another exception or a warning,
  and nothing is evaluated, executed or imported along the way.
- Random syntax trees survive printing and re-parsing unchanged.
- Random formulas give exactly the same numbers as a slow, independent pure-Python
  evaluator that follows the documented rules row by row.
"""

import builtins
import math
import sys
import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from curvis.data.names import name_key
from curvis.formula import FormulaError, Function, evaluate, parse, to_text
from curvis.formula.ast import (
    Binary,
    BinaryOp,
    Call,
    ColumnRef,
    ConstantRef,
    Node,
    Number,
    Span,
    Unary,
    UnaryOp,
)

S = Span(0, 0)
COLUMN_NAMES = ["A", "b", "Dose (µM)", "x]y"]
CONSTANTS = {"pi": math.pi, "k": 2.5, "π": math.pi}
LITERALS = [0.0, 1.0, 2.0, 3.0, 0.5, 10.0, 1e-3, 1e300]
CELLS = [math.nan, 0.0, -0.0, 1.0, -1.0, 2.0, 0.5, 3.0, -2.5, 10.0, 1e300, -7.0]


# --- strategies ------------------------------------------------------------------

leaves = st.one_of(
    st.sampled_from(LITERALS).map(lambda v: Number(v, S)),
    st.sampled_from(COLUMN_NAMES).map(lambda n: ColumnRef(n, S)),
    st.sampled_from(sorted(CONSTANTS)).map(lambda n: ConstantRef(n, S)),
)


def extend(children: st.SearchStrategy[Node]) -> st.SearchStrategy[Node]:
    return st.one_of(
        st.builds(lambda op, a: Unary(op, a, S), st.sampled_from(UnaryOp), children),
        st.builds(
            lambda op, a, b: Binary(op, a, b, S), st.sampled_from(BinaryOp), children, children
        ),
        st.builds(
            lambda f, args: Call(f, tuple(args[: f.arity]), S),
            st.sampled_from(Function),
            st.lists(children, min_size=2, max_size=2),
        ),
    )


trees = st.recursive(leaves, extend, max_leaves=12)

FORMULA_ALPHABET = "0123456789.eE+-−*×/÷%^()[], \tABabkpiπ_MEANSUMROWCOUNTFIRSTLAX$=\"'\\"


@st.composite
def tables(draw: st.DrawFn) -> tuple[dict[str, np.ndarray], int]:
    n_rows = draw(st.integers(0, 6))
    columns = {
        name: np.array(draw(st.lists(st.sampled_from(CELLS), min_size=n_rows, max_size=n_rows)))
        for name in COLUMN_NAMES
    }
    return columns, n_rows


# --- no code runs ----------------------------------------------------------------


@contextmanager
def no_code_execution() -> Iterator[list[str]]:
    """Record any call to eval, exec or compile and any import of a new module."""
    calls: list[str] = []
    real_import = builtins.__import__

    def forbid(name: str) -> Callable[..., Any]:
        def guard(*args: Any, **kwargs: Any) -> Any:
            calls.append(name)
            raise AssertionError(f"{name} was called")

        return guard

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name not in sys.modules:
            calls.append(f"import {name}")
        return real_import(name, *args, **kwargs)

    saved = {n: getattr(builtins, n) for n in ("eval", "exec", "compile", "__import__")}
    builtins.eval = forbid("eval")
    builtins.exec = forbid("exec")
    builtins.compile = forbid("compile")
    builtins.__import__ = guarded_import
    try:
        yield calls
    finally:
        for name, value in saved.items():
            setattr(builtins, name, value)


def try_formula(text: str, columns: dict[str, np.ndarray], n_rows: int) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        try:
            result = evaluate(text, columns, CONSTANTS, n_rows)
        except FormulaError as e:
            assert 0 <= e.start < e.end <= max(len(text), 1) + 1
            assert e.reason
            return
    assert result.shape == (n_rows,)
    assert not np.isinf(result).any()


parse("1")  # build the parser before the guards go up


@given(st.text(alphabet=FORMULA_ALPHABET, max_size=40), tables())
def test_formula_like_text_never_crashes(text: str, table: Any) -> None:
    with no_code_execution() as calls:
        try_formula(text, *table)
    assert calls == []


@given(st.text(max_size=40), tables())
def test_any_text_never_crashes(text: str, table: Any) -> None:
    with no_code_execution() as calls:
        try_formula(text, *table)
    assert calls == []


def test_python_code_does_not_run() -> None:
    with no_code_execution() as calls:
        for text in ['__import__("os").system("echo hi")', "exec('x=1')", "eval('1')"]:
            try_formula(text, {}, 0)
    assert calls == []


# --- round trip ------------------------------------------------------------------


@given(trees)
def test_printed_tree_parses_back_to_the_same_tree(tree: Node) -> None:
    assert parse(to_text(tree)).root == tree


# --- reference evaluator ---------------------------------------------------------


def clean(x: float) -> float:
    return math.nan if math.isinf(x) else x


def binary(op: BinaryOp, a: float, b: float) -> float:
    if op is BinaryOp.ADD:
        return clean(a + b)
    if op is BinaryOp.SUB:
        return clean(a - b)
    if op is BinaryOp.MUL:
        return clean(a * b)
    if op is BinaryOp.DIV:
        return math.nan if math.isnan(a) or math.isnan(b) or b == 0 else clean(a / b)
    if op is BinaryOp.MOD:
        return math.nan if math.isnan(a) or math.isnan(b) or b == 0 else a % b
    # The power itself is NumPy's: its vectorised pow can differ from libm in the last bit
    # (2^0.001), see test_numpy_power_is_within_one_ulp_of_libm.
    with np.errstate(all="ignore"):
        return clean(float(np.power(a, b)))  # (-8)^(1/3) is NaN; 0^-1 and 10^400 are inf


class Reference:
    """Evaluates a tree one row at a time with plain Python floats."""

    def __init__(self, columns: dict[str, np.ndarray], n_rows: int) -> None:
        self.columns = {name_key(k): [float(x) for x in v] for k, v in columns.items()}
        self.n = n_rows

    def column(self, node: Node) -> list[float]:
        return [self.row(node, i) for i in range(self.n)]

    def row(self, node: Node, i: int) -> float:
        match node:
            case Number(value):
                return value
            case ColumnRef(name):
                return clean(self.columns[name_key(name)][i])
            case ConstantRef(name):
                return CONSTANTS[name]
            case Unary(op, a):
                return -self.row(a, i) if op is UnaryOp.NEG else self.row(a, i)
            case Binary(op, a, b):
                return binary(op, self.row(a, i), self.row(b, i))
            case Call(Function.ROW, (a, k)):
                index = self.row(k, i)
                if math.isnan(index) or index != math.floor(index) or not 1 <= index <= self.n:
                    return math.nan
                return self.row(a, int(index) - 1)
            case Call(function, (a,)):
                present = [x for x in self.column(a) if not math.isnan(x)]
                return aggregate(function, present)
        raise AssertionError(node)


def total(values: list[float]) -> float:
    """The addition itself is NumPy's (pairwise and unrolled, so the rounding differs from
    Python's left-to-right sum); its accuracy is tested in test_formula_eval."""
    with np.errstate(all="ignore"):
        return float(np.sum(np.array(values, dtype=np.float64)))


def aggregate(function: Function, values: list[float]) -> float:
    if function is Function.COUNT:
        return float(len(values))
    if function is Function.SUM:
        return clean(total(values))
    if not values:
        return math.nan
    if function is Function.FIRST:
        return values[0]
    if function is Function.LAST:
        return values[-1]
    if function is Function.MIN:
        return min(values)
    if function is Function.MAX:
        return max(values)
    return clean(total(values) / len(values))  # MEAN


@given(st.sampled_from(CELLS + LITERALS), st.sampled_from(CELLS + LITERALS + [1 / 3, -1.5]))
def test_numpy_power_is_within_one_ulp_of_libm(a: float, b: float) -> None:
    try:
        expected = math.pow(a, b)
    except (ValueError, OverflowError, ZeroDivisionError):
        return
    with np.errstate(all="ignore"):
        actual = float(np.power(np.array([a]), b)[0])
    if math.isnan(expected) or math.isinf(expected):
        assert actual == expected or (math.isnan(actual) and math.isnan(expected))
    else:
        assert abs(actual - expected) <= math.ulp(expected)


@given(trees, tables())
def test_results_match_the_reference_evaluator(tree: Node, table: Any) -> None:
    columns, n_rows = table
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        actual = evaluate(to_text(tree), columns, CONSTANTS, n_rows)
    expected = np.array(Reference(columns, n_rows).column(tree), dtype=np.float64)
    np.testing.assert_array_equal(actual, expected)
