"""Parse formula text into a syntax tree with Lark.

The grammar, from loosest to tightest binding::

    a + b, a − b          left to right
    a × b, a ÷ b, a % b   left to right (% is modulo)
    −a, +a                so −2^2 = −(2^2) = −4
    a ^ b                 right to left: 2^3^2 = 2^9; the exponent may be negative: 2^−1
    number, [column], {role}, {role:mean}, CONSTANT, FUNCTION(…), (…)

``*``, ``/`` and ``-`` are accepted as well as ``×``, ``÷`` and ``−``. A ``]`` inside
a column name is written ``]]``. Function names, role names and ``mean`` ignore case;
constant names do not. Role references are used by the Calculate dialog to apply one
formula to many columns (see :mod:`curvis.data.pairing`).

The parser only builds a tree of the dataclasses in :mod:`curvis.formula.ast`; nothing in
a formula is ever run as Python.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, cast

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
    RoleStat,
    Span,
    Unary,
    UnaryOp,
)
from curvis.formula.errors import FormulaError

if TYPE_CHECKING:
    from lark import Lark, Token

GRAMMAR = r"""
?start: sum

?sum: product
    | sum ADD product        -> binary
    | sum SUB product        -> binary

?product: unary
    | product MUL unary      -> binary
    | product DIV unary      -> binary
    | product MOD unary      -> binary

?unary: power
    | SUB unary              -> unary
    | ADD unary              -> unary

?power: atom
    | atom POW unary         -> binary

?atom: NUMBER                -> number
    | COLUMN                 -> column
    | ROLE                   -> role
    | NAME LPAR [arguments] RPAR -> call
    | NAME                   -> constant
    | LPAR sum RPAR          -> group

arguments: sum (COMMA sum)*

ADD: "+"
SUB: "-" | "−"
MUL: "*" | "×"
DIV: "/" | "÷"
MOD: "%"
POW: "^"
LPAR: "("
RPAR: ")"
COMMA: ","
NUMBER: /([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?/
COLUMN: /\[([^\]]|\]\])*\]/
ROLE: /\{[^{}]*\}/
NAME: /[^\W\d]\w*/

%ignore /\s+/
"""

_OPS = {
    "ADD": BinaryOp.ADD,
    "SUB": BinaryOp.SUB,
    "MUL": BinaryOp.MUL,
    "DIV": BinaryOp.DIV,
    "MOD": BinaryOp.MOD,
    "POW": BinaryOp.POW,
}

FUNCTION_NAMES = ", ".join(f.value for f in Function)


@dataclass(frozen=True)
class Formula:
    """A parsed formula and the names it reads."""

    text: str
    root: Node
    columns: tuple[str, ...]
    """Column names referenced, in order of first use (as written; duplicates ignoring case
    are dropped)."""
    constants: tuple[str, ...]
    """Constant names referenced, in order of first use."""
    roles: tuple[str, ...] = ()
    """Keys (:attr:`RoleRef.key`) of the role references, in order of first use."""


@lru_cache(maxsize=256)
def parse(text: str) -> Formula:
    """Parse ``text``; raises :class:`FormulaError` with the position of the problem."""
    if not text.strip():
        raise FormulaError("The formula is empty", 0, max(len(text), 1))
    from lark.exceptions import UnexpectedCharacters, UnexpectedInput, UnexpectedToken

    try:
        root = cast(Node, _parser().parse(text))  # the _Build callbacks return nodes
    except UnexpectedCharacters as e:
        raise _character_error(text, e.pos_in_stream or 0) from None
    except UnexpectedToken as e:
        raise _token_error(text, e.token, set(e.expected)) from None
    except UnexpectedInput as e:  # pragma: no cover - LALR raises one of the above
        raise FormulaError("The formula is not valid", max(e.pos_in_stream or 0, 0)) from None
    columns: dict[str, str] = {}
    constants: dict[str, None] = {}
    roles: dict[str, None] = {}
    _collect(root, columns, constants, roles)
    return Formula(text, root, tuple(columns.values()), tuple(constants), tuple(roles))


def _collect(
    node: Node, columns: dict[str, str], constants: dict[str, None], roles: dict[str, None]
) -> None:
    match node:
        case ColumnRef(name):
            columns.setdefault(name_key(name), name)
        case ConstantRef(name):
            constants.setdefault(name)
        case RoleRef():
            roles.setdefault(node.key)
        case Unary(_, operand):
            _collect(operand, columns, constants, roles)
        case Binary(_, left, right):
            _collect(left, columns, constants, roles)
            _collect(right, columns, constants, roles)
        case Call(_, args):
            for arg in args:
                _collect(arg, columns, constants, roles)


def _character_error(text: str, pos: int) -> FormulaError:
    char = text[pos]
    if char == "[":
        return FormulaError("The column name has no closing ']'", pos, len(text))
    if char == "]":
        return FormulaError("']' has no matching '['", pos)
    if char == "{":
        return FormulaError("The role has no closing '}'", pos, len(text))
    if char == "}":
        return FormulaError("'}' has no matching '{'", pos)
    return FormulaError(f"'{char}' cannot be used in a formula", pos)


def _token_error(text: str, token: Token, expected: set[str]) -> FormulaError:
    wants_value = "NUMBER" in expected  # a value may start here (+ and − may also be operators)
    if token.type == "$END":
        end = len(text.rstrip())
        if wants_value:
            return FormulaError("The formula ends where a value is expected", end)
        return FormulaError("')' is missing at the end", end)
    start, end, value = token.start_pos or 0, token.end_pos or 0, str(token)
    if wants_value:
        return FormulaError(f"A value is missing before '{value}'", start, end)
    if token.type == "RPAR":
        return FormulaError("')' has no matching '('", start, end)
    if token.type == "COMMA":
        return FormulaError("',' can only separate function arguments", start, end)
    if token.type == "LPAR":
        return FormulaError("An operator is missing before '('", start, end)
    return FormulaError(f"An operator is missing before '{value}'", start, end)


def _span(*parts: Any) -> Span:
    """Span from the first to the last of ``parts`` (tokens or nodes)."""
    return Span(_start(parts[0]), _end(parts[-1]))


def _start(part: Any) -> int:
    return part.span.start if hasattr(part, "span") else int(part.start_pos)


def _end(part: Any) -> int:
    return part.span.end if hasattr(part, "span") else int(part.end_pos)


class _Build:
    """Callbacks that turn each grammar rule into a node, run by the LALR parser."""

    def number(self, children: list[Token]) -> Number:
        (token,) = children
        value = float(token)
        if not math.isfinite(value):
            raise FormulaError("The number is too large", token.start_pos or 0, token.end_pos)
        return Number(value, _span(token))

    def column(self, children: list[Token]) -> ColumnRef:
        (token,) = children
        name = str(token)[1:-1].replace("]]", "]")
        if not name.strip():
            raise FormulaError("The column name is empty", token.start_pos or 0, token.end_pos)
        return ColumnRef(name, _span(token))

    def role(self, children: list[Token]) -> RoleRef:
        (token,) = children
        start, end = token.start_pos or 0, token.end_pos or 0
        name, colon, stat_text = str(token)[1:-1].partition(":")
        name = name.strip()
        if not name:
            raise FormulaError("The role name is empty", start, end)
        stat: RoleStat | None = None
        if colon:
            try:
                stat = RoleStat(stat_text.strip().casefold())
            except ValueError:
                raise FormulaError(
                    f"Unknown statistic '{stat_text.strip()}': write {{{name}:mean}}", start, end
                ) from None
        return RoleRef(name, stat, Span(start, end))

    def constant(self, children: list[Token]) -> ConstantRef:
        (token,) = children
        return ConstantRef(str(token), _span(token))

    def call(self, children: list[Any]) -> Call:
        name, _lpar, arguments, rpar = children
        try:
            function = Function(str(name).upper())
        except ValueError:
            raise FormulaError(
                f"Unknown function '{name}'. Functions: {FUNCTION_NAMES}",
                name.start_pos or 0,
                name.end_pos,
            ) from None
        args: tuple[Node, ...] = tuple(arguments) if arguments is not None else ()
        span = _span(name, rpar)
        if len(args) != function.arity:
            count = "1 argument" if function.arity == 1 else f"{function.arity} arguments"
            raise FormulaError(
                f"{function.value} takes {count}: {function.signature}", span.start, span.end
            )
        return Call(function, args, span)

    def arguments(self, children: list[Any]) -> list[Node]:
        return [c for c in children if not hasattr(c, "type")]  # drop the COMMA tokens

    def group(self, children: list[Any]) -> Node:
        _lpar, inner, _rpar = children
        node: Node = inner
        return node

    def unary(self, children: list[Any]) -> Unary:
        op, operand = children
        kind = UnaryOp.NEG if op.type == "SUB" else UnaryOp.POS
        return Unary(kind, operand, _span(op, operand))

    def binary(self, children: list[Any]) -> Binary:
        left, op, right = children
        return Binary(_OPS[op.type], left, right, _span(left, right))


_lock = threading.Lock()
_lark: Lark | None = None


def _parser() -> Lark:
    """The LALR parser, built on first use so importing this module stays cheap."""
    global _lark
    if _lark is None:
        with _lock:  # warm_up() may be building it on a worker thread right now
            if _lark is None:
                from lark import Lark

                _lark = Lark(GRAMMAR, parser="lalr", transformer=_Build(), maybe_placeholders=True)
    return _lark


def warm_up() -> None:
    """Build the parser now, so the first formula the user types is not slowed down.

    Importing Lark and building the grammar takes ~150 ms. The app calls this on a
    worker thread shortly after start-up. Safe to call from any thread, any number of times.
    """
    _parser()
