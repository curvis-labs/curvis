"""Tests for the formula parser: syntax, precedence and error positions."""

import pytest

from curvis.formula import Formula, FormulaError, Function, parse, to_text
from curvis.formula.ast import (
    Binary,
    BinaryOp,
    Call,
    ColumnRef,
    ConstantRef,
    Number,
    Span,
    Unary,
    UnaryOp,
)

S = Span(0, 0)  # spans are ignored by ==


def n(value: float) -> Number:
    return Number(value, S)


def col(name: str) -> ColumnRef:
    return ColumnRef(name, S)


def b(op: BinaryOp, left: object, right: object) -> Binary:
    return Binary(op, left, right, S)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("text", "value"),
    [("0", 0.0), ("42", 42.0), ("1.5", 1.5), (".5", 0.5), ("2.", 2.0), ("2e-3", 2e-3),
     ("1E3", 1000.0), ("6.02214076e+23", 6.02214076e23)],
)  # fmt: skip
def test_numbers(text: str, value: float) -> None:
    assert parse(text).root == n(value)


@pytest.mark.parametrize(
    ("text", "name"),
    [("[A]", "A"), ("[Dose (µM)]", "Dose (µM)"), ("[  spaced  name ]", "  spaced  name "),
     ("[a]]b]", "a]b"), ("[x[1]]]", "x[1]"), ("[1 + 2]", "1 + 2")],
)  # fmt: skip
def test_column_references(text: str, name: str) -> None:
    assert parse(text).root == col(name)


@pytest.mark.parametrize(
    ("text", "op"),
    [("1 + 2", BinaryOp.ADD), ("1 - 2", BinaryOp.SUB), ("1 − 2", BinaryOp.SUB),
     ("1 * 2", BinaryOp.MUL), ("1 × 2", BinaryOp.MUL), ("1 / 2", BinaryOp.DIV),
     ("1 ÷ 2", BinaryOp.DIV), ("1 % 2", BinaryOp.MOD), ("1 ^ 2", BinaryOp.POW)],
)  # fmt: skip
def test_operator_spellings(text: str, op: BinaryOp) -> None:
    assert parse(text).root == b(op, n(1), n(2))


def test_unary_minus_both_spellings_and_unary_plus() -> None:
    assert parse("-[A]").root == Unary(UnaryOp.NEG, col("A"), S)
    assert parse("−[A]").root == Unary(UnaryOp.NEG, col("A"), S)
    assert parse("+[A]").root == Unary(UnaryOp.POS, col("A"), S)
    assert parse("--1").root == Unary(UnaryOp.NEG, Unary(UnaryOp.NEG, n(1), S), S)


def test_precedence_multiplication_before_addition() -> None:
    assert parse("1 + 2 * 3").root == b(BinaryOp.ADD, n(1), b(BinaryOp.MUL, n(2), n(3)))
    assert parse("1 - 6 % 4").root == b(BinaryOp.SUB, n(1), b(BinaryOp.MOD, n(6), n(4)))


def test_left_associative() -> None:
    assert parse("8 - 4 - 2").root == b(BinaryOp.SUB, b(BinaryOp.SUB, n(8), n(4)), n(2))
    assert parse("8 / 4 / 2").root == b(BinaryOp.DIV, b(BinaryOp.DIV, n(8), n(4)), n(2))
    assert parse("8 % 5 * 2").root == b(BinaryOp.MUL, b(BinaryOp.MOD, n(8), n(5)), n(2))


def test_power_is_right_associative_and_binds_tighter_than_unary_minus() -> None:
    assert parse("2^3^2").root == b(BinaryOp.POW, n(2), b(BinaryOp.POW, n(3), n(2)))
    assert parse("-2^2").root == Unary(UnaryOp.NEG, b(BinaryOp.POW, n(2), n(2)), S)
    assert parse("2^-1").root == b(BinaryOp.POW, n(2), Unary(UnaryOp.NEG, n(1), S))
    assert parse("2 * 3^2").root == b(BinaryOp.MUL, n(2), b(BinaryOp.POW, n(3), n(2)))


def test_parentheses() -> None:
    assert parse("(1 + 2) * 3").root == b(BinaryOp.MUL, b(BinaryOp.ADD, n(1), n(2)), n(3))
    assert parse("((([A])))").root == col("A")


@pytest.mark.parametrize("function", list(Function))
def test_functions_ignore_case(function: Function) -> None:
    args = "[A], 2" if function is Function.ROW else "[A]"
    expected_args = (col("A"), n(2)) if function is Function.ROW else (col("A"),)
    for name in (function.value, function.value.lower(), function.value.title()):
        assert parse(f"{name}({args})").root == Call(function, expected_args, S)


def test_function_arguments_can_be_expressions() -> None:
    root = parse("MEAN([A] * 2 + 1)").root
    assert isinstance(root, Call)
    assert root.args == (b(BinaryOp.ADD, b(BinaryOp.MUL, col("A"), n(2)), n(1)),)


def test_constants_are_case_sensitive_names() -> None:
    assert parse("pi").root == ConstantRef("pi", S)
    assert parse("π").root == ConstantRef("π", S)
    assert parse("k_B").root == ConstantRef("k_B", S)
    assert parse("K_b").root == ConstantRef("K_b", S)


def test_whitespace_is_ignored() -> None:
    assert parse(" \t1\n+\u00a02 ").root == parse("1+2").root


def test_names_read() -> None:
    f = parse("[Dose] * k + [dose] / [Signal] - MEAN([Signal]) + k + pi")
    assert isinstance(f, Formula)
    assert f.columns == ("Dose", "Signal")  # duplicates ignoring case dropped
    assert f.constants == ("k", "pi")


def test_spans() -> None:
    root = parse("1 + MEAN([Dose])").root
    assert isinstance(root, Binary)
    assert root.span == Span(0, 16)
    assert root.right.span == Span(4, 16)
    assert isinstance(root.right, Call)
    assert root.right.args[0].span == Span(9, 15)


@pytest.mark.parametrize(
    "text",
    ["1 + 2 * 3", "-2^2", "2^-1^2", "MEAN([a]] b]) % ROW([A], 3)", "-(1 - -pi)", "k_B * 1e-5"],
)
def test_to_text_round_trip(text: str) -> None:
    root = parse(text).root
    assert parse(to_text(root)).root == root


# --- errors ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "message", "start", "end"),
    [
        ("", "The formula is empty", 0, 1),
        ("   ", "The formula is empty", 0, 3),
        ("1 +", "The formula ends where a value is expected", 3, 4),
        ("(1 + 2", "')' is missing at the end", 6, 7),
        ("1 + 2)", "')' has no matching '('", 5, 6),
        ("1 * )", "A value is missing before ')'", 4, 5),
        ("2 3", "An operator is missing before '3'", 2, 3),
        ("[A] [B]", "An operator is missing before '[B]'", 4, 7),
        ("2(3)", "An operator is missing before '('", 1, 2),
        ("1, 2", "',' can only separate function arguments", 1, 2),
        ("[Dose + 1", "The column name has no closing ']'", 0, 9),
        ("1 + ]", "']' has no matching '['", 4, 5),
        ("1 + $", "'$' cannot be used in a formula", 4, 5),
        ("1 = 1", "'=' cannot be used in a formula", 2, 3),
        ("[]", "The column name is empty", 0, 2),
        ("[ ]", "The column name is empty", 0, 3),
        ("1e999", "The number is too large", 0, 5),
        ("2 * AVG([A])", "Unknown function 'AVG'. Functions: FIRST, LAST, ROW, MIN, MAX, MEAN, "
                         "SUM, COUNT", 4, 7),
        ("MEAN([A], [B])", "MEAN takes 1 argument: MEAN(column)", 0, 14),
        ("SUM()", "SUM takes 1 argument: SUM(column)", 0, 5),
        ("ROW(3)", "ROW takes 2 arguments: ROW(column, n)", 0, 6),
        ("MEAN(", "The formula ends where a value is expected", 5, 6),
    ],
)  # fmt: skip
def test_errors_with_positions(text: str, message: str, start: int, end: int) -> None:
    with pytest.raises(FormulaError) as info:
        parse(text)
    assert info.value.reason == message
    assert (info.value.start, info.value.end) == (start, end)
    assert str(info.value) == f"{message} (at character {start + 1})"


@pytest.mark.parametrize(
    "text",
    ['__import__("os").system("x")', "exec('1')", "eval('1')", "lambda: 1", "a.b", "x[0]",
     "1 if 1 else 2", "[A] and [B]", "1 ** 2", "1 // 2", "'text'", "f(x)", "open('f')"],
)  # fmt: skip
def test_python_code_is_rejected(text: str) -> None:
    with pytest.raises(FormulaError):
        parse(text)


# --- warm-up ---------------------------------------------------------------------


def test_warm_up_from_many_threads_builds_the_parser_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    import lark

    from curvis.formula import parser, warm_up

    builds: list[int] = []
    real_lark = lark.Lark

    def counting_lark(*args: object, **kwargs: object) -> lark.Lark:
        builds.append(1)
        return real_lark(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(parser, "_lark", None)
    monkeypatch.setattr(lark, "Lark", counting_lark)
    threads = [threading.Thread(target=warm_up) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert builds == [1]
    parse.cache_clear()
    assert parse("1 + 2").root == b(BinaryOp.ADD, n(1), n(2))
    assert builds == [1]
