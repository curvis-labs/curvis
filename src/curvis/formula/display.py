"""Render an equation written in the formula language as rich text for display.

The curve fits store their model as formula text ("Y = Bottom + (Top - Bottom) / …", see
:attr:`curvis.analysis.fit.analyses.CurveFit.equation`). :func:`render_equation` parses the
right-hand side with the formula grammar and writes it back the way a textbook would:
− for minus, · for times, powers as superscripts, a trailing ``_1`` as a subscript, and
only the parentheses the order of operations needs. The text is parsed, never evaluated.

The result is Qt rich text (a subset of HTML), with every name escaped.
"""

from __future__ import annotations

import html
import re
from functools import cache

from curvis.formula.ast import Binary, BinaryOp, ConstantRef, Node, Number, Unary, UnaryOp
from curvis.formula.parser import Formula, parse

_ADD, _MUL, _UNARY, _POW, _ATOM = 1, 2, 3, 4, 5
_PRECEDENCE = {
    BinaryOp.ADD: _ADD,
    BinaryOp.SUB: _ADD,
    BinaryOp.MUL: _MUL,
    BinaryOp.DIV: _MUL,
    BinaryOp.MOD: _MUL,
    BinaryOp.POW: _POW,
}
_SYMBOLS = {
    BinaryOp.ADD: " + ",
    BinaryOp.SUB: " − ",
    BinaryOp.MUL: " · ",
    BinaryOp.DIV: " / ",
    BinaryOp.MOD: " % ",
}
NO_BREAK = 100
"""A depth at which nothing wraps (inside a power)."""
_SUBSCRIPT = re.compile(r"^(.*?)_(\w+)$")


def split_equation(text: str) -> tuple[str, Formula]:
    """The left-hand side ("Y") and the parsed right-hand side of ``text``.

    Raises ``ValueError`` without an "=", and :class:`~curvis.formula.FormulaError` when
    the right-hand side does not parse."""
    left, equals, right = text.partition("=")
    if not equals or not left.strip():
        raise ValueError(f"an equation is written 'Y = …', not {text!r}")
    return left.strip(), parse(right)


@cache
def render_equation(text: str) -> str:
    """``text`` ("Y = …" in the formula language) as rich text."""
    left, formula = split_equation(text)
    return f"{_name(left)} = {_render(formula.root, formula.text, 0)}"


def _precedence(node: Node) -> int:
    if isinstance(node, Binary):
        return _PRECEDENCE[node.op]
    if isinstance(node, Unary):
        return _UNARY
    return _ATOM


def _render(node: Node, source: str, depth: int) -> str:
    """``node`` as rich text. ``depth`` counts the parentheses around it: a long equation
    may wrap only at a + or − outside parentheses or inside one pair, never in a power."""
    match node:
        case Number(_value, span):
            return html.escape(source[span.start : span.end])
        case ConstantRef(name):
            return _name(name)
        case Unary(op, operand):
            sign = "−" if op is UnaryOp.NEG else "+"
            return sign + _wrap(operand, source, depth, _precedence(operand) < _UNARY)
        case Binary(BinaryOp.POW, base, exponent):
            power = _render(exponent, source, NO_BREAK)
            return f"{_wrap(base, source, depth, _precedence(base) < _ATOM)}<sup>{power}</sup>"
        case Binary(op, left, right):
            mine = _PRECEDENCE[op]
            # a − (b + c) and a / (b · c) need their parentheses; a + (b − c) does not.
            keeps_order = op in (BinaryOp.ADD, BinaryOp.MUL)
            right_parens = _precedence(right) < mine or (
                _precedence(right) == mine and not keeps_order
            )
            left_text = _wrap(left, source, depth, _precedence(left) < mine)
            symbol = _SYMBOLS[op]
            if not (mine == _ADD and depth <= 1):
                symbol = symbol.replace(" ", "&nbsp;")
            return left_text + symbol + _wrap(right, source, depth, right_parens)
    raise ValueError(f"an equation cannot show {type(node).__name__}")


def _wrap(node: Node, source: str, depth: int, parens: bool) -> str:
    if not parens:
        return _render(node, source, depth)
    return f"({_render(node, source, depth + 1)})"


def _name(name: str) -> str:
    """A name with a trailing "_1" as a subscript: LogEC50_1 → LogEC50₁."""
    match = _SUBSCRIPT.match(name)
    if match and match.group(1):
        return f"{html.escape(match.group(1))}<sub>{html.escape(match.group(2))}</sub>"
    return html.escape(name)
