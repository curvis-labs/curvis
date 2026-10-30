"""Syntax tree of a formula. Every node keeps the span of text it came from."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class BinaryOp(StrEnum):
    ADD = "+"
    SUB = "-"
    MUL = "*"
    DIV = "/"
    MOD = "%"
    POW = "^"


class UnaryOp(StrEnum):
    NEG = "-"
    POS = "+"


class Function(StrEnum):
    """Column functions. All take one argument except ROW, which takes two."""

    FIRST = "FIRST"
    LAST = "LAST"
    ROW = "ROW"
    MIN = "MIN"
    MAX = "MAX"
    MEAN = "MEAN"
    SUM = "SUM"
    COUNT = "COUNT"

    @property
    def arity(self) -> int:
        return 2 if self is Function.ROW else 1

    @property
    def signature(self) -> str:
        """How the function is written, for error messages."""
        return "ROW(column, n)" if self is Function.ROW else f"{self.value}(column)"


class RoleStat(StrEnum):
    """A statistic over all columns of a role, row by row: ``{A:mean}``."""

    MEAN = "mean"


@dataclass(frozen=True, slots=True)
class Span:
    """0-based character offsets ``[start, end)`` in the formula text."""

    start: int
    end: int


# Spans are left out of equality so trees parsed from differently spaced text compare equal.


@dataclass(frozen=True, slots=True)
class Number:
    value: float
    span: Span = field(compare=False)


@dataclass(frozen=True, slots=True)
class ColumnRef:
    name: str
    span: Span = field(compare=False)


@dataclass(frozen=True, slots=True)
class ConstantRef:
    name: str
    span: Span = field(compare=False)


@dataclass(frozen=True, slots=True)
class RoleRef:
    """``{A}`` (the column of role A that goes with the column being calculated) or
    ``{A:mean}`` (the row-wise mean of all columns of role A). See :mod:`curvis.data.pairing`."""

    role: str
    stat: RoleStat | None
    span: Span = field(compare=False)

    @property
    def key(self) -> str:
        """Key of the values this reference reads: the role name ignoring case, plus the stat."""
        key = self.role.casefold()
        return key if self.stat is None else f"{key}:{self.stat.value}"


@dataclass(frozen=True, slots=True)
class Unary:
    op: UnaryOp
    operand: Node
    span: Span = field(compare=False)


@dataclass(frozen=True, slots=True)
class Binary:
    op: BinaryOp
    left: Node
    right: Node
    span: Span = field(compare=False)


@dataclass(frozen=True, slots=True)
class Call:
    function: Function
    args: tuple[Node, ...]
    span: Span = field(compare=False)


type Node = Number | ColumnRef | ConstantRef | RoleRef | Unary | Binary | Call


def to_text(node: Node) -> str:
    """Formula text for ``node``, fully parenthesized. Parsing it gives the same tree."""
    match node:
        case Number(value):
            return repr(value)
        case ColumnRef(name):
            return "[" + name.replace("]", "]]") + "]"
        case ConstantRef(name):
            return name
        case RoleRef(role, stat):
            return "{" + role + ("" if stat is None else f":{stat.value}") + "}"
        case Unary(op, operand):
            return f"({op.value}{to_text(operand)})"
        case Binary(op, left, right):
            return f"({to_text(left)} {op.value} {to_text(right)})"
        case Call(function, args):
            return f"{function.value}({', '.join(to_text(a) for a in args)})"
