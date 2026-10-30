"""Compile a parsed formula into vectorised NumPy operations.

The syntax tree becomes a tree of small Python closures, each calling one NumPy
function on whole columns at once. Nothing is passed to ``eval`` or ``exec``.

Rules for values:

- A missing value (NaN) in a row gives NaN in that row.
- Division by zero, ``x % 0``, a result too large to store (±inf) and an undefined
  power such as ``(-8)^(1/3)`` all give NaN, not an error. A formula therefore only
  produces finite numbers or NaN.
- ``%`` is modulo; the result takes the sign of the divisor (as in Excel): ``-7 % 3 = 2``.
- Column functions skip missing values. With no values left, FIRST, LAST, MIN, MAX and
  MEAN give NaN, SUM and COUNT give 0. FIRST and LAST give the first and last value
  that is not missing. The argument may be any expression; a plain number counts once
  per row, so ``COUNT(1)`` is the number of rows.
- ``ROW(column, n)`` is the value of ``column`` in row ``n`` (1 is the first row);
  ``n`` may itself be a column. An ``n`` outside the table or not a whole number gives NaN.
- A column function gives one number, which is repeated on every row.
- ``{A}`` and ``{A:mean}`` read arrays the caller passes in ``roles``, by
  :attr:`~curvis.formula.ast.RoleRef.key`; they behave like columns.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np
import numpy.typing as npt

from curvis.data.names import name_key
from curvis.formula.ast import (
    Binary,
    BinaryOp,
    Call,
    ColumnRef,
    ConstantRef,
    Function,
    Node,
    Number,
    RoleRef,
    Span,
    Unary,
    UnaryOp,
)
from curvis.formula.errors import FormulaError
from curvis.formula.parser import Formula, parse

type FloatArray = npt.NDArray[np.float64]
type Value = float | FloatArray
"""A scalar (from numbers, constants and column functions) or one value per row."""


@dataclass(slots=True)
class _Env:
    columns: tuple[FloatArray, ...]
    roles: tuple[FloatArray, ...]
    constants: tuple[float, ...]
    n_rows: int
    cache: list[Value | None]


type _Eval = Callable[[_Env], Value]


class CompiledFormula:
    """A formula ready to run on columns. Compile once, call for every recalculation."""

    def __init__(self, formula: Formula) -> None:
        self.formula = formula
        self._column_keys = [name_key(c) for c in formula.columns]
        self._calls: dict[Node, int] = {}  # identical column functions are computed once
        self._run = self._compile(formula.root)

    @property
    def columns(self) -> tuple[str, ...]:
        """Column names the formula reads."""
        return self.formula.columns

    @property
    def constants(self) -> tuple[str, ...]:
        """Constant names the formula uses."""
        return self.formula.constants

    def __call__(
        self,
        columns: Mapping[str, npt.ArrayLike],
        constants: Mapping[str, float],
        n_rows: int,
        roles: Mapping[str, npt.ArrayLike] | None = None,
    ) -> FloatArray:
        """Evaluate on ``columns`` (by name, ignoring case), each ``n_rows`` long.

        ``roles`` holds the values of role references by key (``"a"``, ``"a:mean"``).
        Returns a new ``float64`` array of ``n_rows`` values. Raises
        :class:`FormulaError` for a missing column, constant or role, or a text column.
        """
        roles = roles if roles is not None else {}
        by_key = {name_key(name): values for name, values in columns.items()}
        check(self.formula, by_key, constants, roles)
        arrays: list[FloatArray] = []
        for name, key in zip(self.formula.columns, self._column_keys, strict=True):
            values = np.asarray(by_key[key])
            if values.dtype.kind not in "fiub":
                ref = _first_ref(self.formula.root, key)
                raise FormulaError(f"Column '{name}' holds text, not numbers", ref.start, ref.end)
            if values.shape != (n_rows,):
                raise ValueError(f"column '{name}' has {len(values)} rows, expected {n_rows}")
            arrays.append(values.astype(np.float64, copy=False))
        role_arrays: list[FloatArray] = []
        for key in self.formula.roles:
            values = np.asarray(roles[key], dtype=np.float64)
            if values.shape != (n_rows,):
                raise ValueError(f"role '{key}' has {len(values)} rows, expected {n_rows}")
            role_arrays.append(values)
        env = _Env(
            tuple(arrays),
            tuple(role_arrays),
            tuple(float(constants[name]) for name in self.formula.constants),
            n_rows,
            [None] * len(self._calls),
        )
        with np.errstate(all="ignore"):
            result = self._run(env)
            out = np.empty(n_rows, dtype=np.float64)
            out[...] = result
            np.copyto(out, np.nan, where=np.isinf(out))
        return out

    # --- compilation -------------------------------------------------------------

    def _compile(self, node: Node) -> _Eval:
        match node:
            case Number(value):
                return lambda env: value
            case ColumnRef(name):
                index = self._column_keys.index(name_key(name))
                return lambda env: env.columns[index]
            case ConstantRef(name):
                index = self.formula.constants.index(name)
                return lambda env: env.constants[index]
            case RoleRef():
                role_index = self.formula.roles.index(node.key)
                return lambda env: env.roles[role_index]
            case Unary(op, operand):
                inner = self._compile(operand)
                if op is UnaryOp.POS:
                    return inner
                return lambda env: _scalar(np.negative(inner(env)))
            case Binary(op, left, right):
                return _binary(_BINARY[op], self._compile(left), self._compile(right))
            case Call(function, args):
                compiled = [self._compile(a) for a in args]
                slot = self._calls.setdefault(node, len(self._calls))
                impl = _FUNCTIONS[function]

                def call(env: _Env) -> Value:
                    cached = env.cache[slot]
                    if cached is None:
                        cached = env.cache[slot] = impl(env, *(c(env) for c in compiled))
                    return cached

                return call
        raise AssertionError(node)  # pragma: no cover


# --- checking names --------------------------------------------------------------


def check(
    formula: Formula | str,
    columns: Iterable[str],
    constants: Iterable[str],
    roles: Iterable[str] = (),
) -> None:
    """Raise :class:`FormulaError` at the first column, constant or role that does not exist.

    ``roles`` are role keys (:attr:`~curvis.formula.ast.RoleRef.key`).
    """
    if isinstance(formula, str):
        formula = parse(formula)
    column_keys = {name_key(c) for c in columns}
    constant_names = set(constants)
    role_keys = set(roles)
    for node in _walk(formula.root):
        if isinstance(node, ColumnRef) and name_key(node.name) not in column_keys:
            raise FormulaError(f"Unknown column '{node.name}'", node.span.start, node.span.end)
        if isinstance(node, RoleRef) and node.key not in role_keys:
            text = "{" + node.role + ("" if node.stat is None else f":{node.stat.value}") + "}"
            raise FormulaError(f"Unknown role '{text}'", node.span.start, node.span.end)
        if isinstance(node, ConstantRef) and node.name not in constant_names:
            message = _unknown_constant(node.name, column_keys, constant_names)
            raise FormulaError(message, node.span.start, node.span.end)


def _unknown_constant(name: str, column_keys: set[str], constants: set[str]) -> str:
    if name.upper() in Function.__members__:
        return f"{name.upper()} is a function: write {Function(name.upper()).signature}"
    if name_key(name) in column_keys:
        return f"Unknown constant '{name}'. Put column names in square brackets: [{name}]"
    similar = sorted(c for c in constants if c.casefold() == name.casefold())
    if similar:
        return f"Unknown constant '{name}'. Did you mean '{similar[0]}'?"
    return f"Unknown constant '{name}'"


def _walk(node: Node) -> Iterable[Node]:
    """Nodes in the order they appear in the text."""
    match node:
        case Unary(_, operand):
            yield node
            yield from _walk(operand)
        case Binary(_, left, right):
            yield from _walk(left)
            yield node
            yield from _walk(right)
        case Call(_, args):
            yield node
            for arg in args:
                yield from _walk(arg)
        case _:
            yield node


def _first_ref(root: Node, key: str) -> Span:
    return next(n.span for n in _walk(root) if isinstance(n, ColumnRef) and name_key(n.name) == key)


# --- operators -------------------------------------------------------------------


def _scalar(result: Any) -> Value:
    """Plain ``float`` for a 0-d result; ±inf becomes NaN."""
    if isinstance(result, np.ndarray) and result.ndim:
        np.copyto(result, np.nan, where=np.isinf(result))
        return result
    value = float(result)
    return np.nan if value in (np.inf, -np.inf) else value


def _divide(a: Value, b: Value) -> Any:
    result = np.divide(a, b)
    if isinstance(b, np.ndarray):
        np.copyto(result, np.nan, where=b == 0)
    elif b == 0:
        result = np.full_like(result, np.nan) if isinstance(result, np.ndarray) else np.nan
    return result


_BINARY: dict[BinaryOp, Callable[[Value, Value], Any]] = {
    BinaryOp.ADD: np.add,
    BinaryOp.SUB: np.subtract,
    BinaryOp.MUL: np.multiply,
    BinaryOp.DIV: _divide,
    BinaryOp.MOD: np.mod,
    BinaryOp.POW: np.power,
}


def _binary(op: Callable[[Value, Value], Any], left: _Eval, right: _Eval) -> _Eval:
    return lambda env: _scalar(op(left(env), right(env)))


# --- column functions ------------------------------------------------------------


def _per_row(env: _Env, value: Value) -> FloatArray:
    if isinstance(value, np.ndarray):
        return value
    return np.full(env.n_rows, value, dtype=np.float64)


def _present(env: _Env, value: Value) -> FloatArray:
    """The values that are not missing."""
    values = _per_row(env, value)
    return values[~np.isnan(values)]


def _first(env: _Env, value: Value) -> Value:
    values = _present(env, value)
    return float(values[0]) if values.size else np.nan


def _last(env: _Env, value: Value) -> Value:
    values = _present(env, value)
    return float(values[-1]) if values.size else np.nan


def _min(env: _Env, value: Value) -> Value:
    values = _present(env, value)
    return float(values.min()) if values.size else np.nan


def _max(env: _Env, value: Value) -> Value:
    values = _present(env, value)
    return float(values.max()) if values.size else np.nan


def _mean(env: _Env, value: Value) -> Value:
    values = _present(env, value)
    return _scalar(values.mean()) if values.size else np.nan


def _sum(env: _Env, value: Value) -> Value:
    return _scalar(_present(env, value).sum())


def _count(env: _Env, value: Value) -> Value:
    return float(_present(env, value).size)


def _row(env: _Env, value: Value, n: Value) -> Value:
    values = _per_row(env, value)
    index = np.asarray(n, dtype=np.float64)
    valid = (index >= 1) & (index <= env.n_rows) & (index == np.floor(index))
    if index.ndim == 0:
        return float(values[int(index) - 1]) if valid else np.nan
    result = np.full(env.n_rows, np.nan)
    result[valid] = values[index[valid].astype(np.intp) - 1]
    return result


_FUNCTIONS: dict[Function, Callable[..., Value]] = {
    Function.FIRST: _first,
    Function.LAST: _last,
    Function.ROW: _row,
    Function.MIN: _min,
    Function.MAX: _max,
    Function.MEAN: _mean,
    Function.SUM: _sum,
    Function.COUNT: _count,
}


# --- convenience -----------------------------------------------------------------


@lru_cache(maxsize=256)
def compile_formula(formula: Formula | str) -> CompiledFormula:
    """Parse (if needed) and compile ``formula``. Results are cached by formula."""
    return CompiledFormula(parse(formula) if isinstance(formula, str) else formula)


def evaluate(
    formula: Formula | str,
    columns: Mapping[str, npt.ArrayLike],
    constants: Mapping[str, float],
    n_rows: int,
    roles: Mapping[str, npt.ArrayLike] | None = None,
) -> FloatArray:
    """Compile (cached) and evaluate ``formula``; see :meth:`CompiledFormula.__call__`."""
    return compile_formula(formula)(columns, constants, n_rows, roles)
