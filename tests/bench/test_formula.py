"""Benchmarks for formulas. Target: recalculate a formula column of 100,000 rows in < 50 ms.

The formula mixes column functions, every operator and a constant; the column has 1%
missing values. Parsing and compiling happen once when a calculated column is created
or edited, and are timed separately (with the caches cleared).
"""

from typing import Any

import numpy as np
import pytest

from curvis.formula import compile_formula, parse, resolve_constants

RECALC_TARGET_S = 0.050
N_ROWS = 100_000
FORMULA = "([A] - MIN([A])) / (MAX([A]) - MIN([A])) * 100 + [B]^2 % 7 − MEAN([B]) × k_B ÷ 2"


@pytest.fixture(scope="module")
def columns() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(0)
    a = rng.normal(10, 2, N_ROWS)
    a[rng.random(N_ROWS) < 0.01] = np.nan
    return {"A": a, "B": rng.normal(size=N_ROWS)}


def test_recalculate_formula_100k_rows(benchmark: Any, columns: dict[str, np.ndarray]) -> None:
    compiled = compile_formula(FORMULA)
    constants = resolve_constants()
    result = benchmark(compiled, columns, constants, N_ROWS)
    assert result.shape == (N_ROWS,)
    assert np.isnan(result).sum() == np.isnan(columns["A"]).sum()
    assert benchmark.stats.stats.mean < RECALC_TARGET_S


def test_parse_and_compile_formula(benchmark: Any) -> None:
    parse("1")  # the Lark parser is built once per session, on first use

    def parse_and_compile() -> None:
        parse.cache_clear()
        compile_formula.cache_clear()
        compile_formula(FORMULA)

    benchmark(parse_and_compile)


def test_recalculate_paired_calculation_100k_rows(benchmark: Any) -> None:
    """A paired calculated column: its role values (a mean of three columns) plus the formula."""
    from curvis.data import Column, Pairing, Table, role_values

    rng = np.random.default_rng(1)
    columns = [Column.new(f"a{i}", "Y", rng.normal(size=N_ROWS), id=f"a{i}") for i in range(3)]
    columns += [Column.new(f"b{i}", "Y", rng.normal(size=N_ROWS), id=f"b{i}") for i in range(3)]
    table = Table("T", columns)
    pairing = Pairing(("a0", "a1", "a2"), ("b0", "b1", "b2"), paired=True)
    compiled = compile_formula("({B} - {A:mean}) / {A:mean} * 100")
    constants = resolve_constants()

    def recalculate() -> np.ndarray:
        roles = role_values(table, pairing, 1, compiled.formula.roles)
        return compiled({}, constants, N_ROWS, roles)

    result = benchmark(recalculate)
    assert result.shape == (N_ROWS,)
    assert benchmark.stats.stats.mean < RECALC_TARGET_S
