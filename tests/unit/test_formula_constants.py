"""Tests for built-in and user constants."""

from fractions import Fraction

import pytest

from curvis.core import ProjectConstant
from curvis.formula import (
    BUILTIN_CONSTANTS,
    BUILTIN_VALUES,
    constant_name_problem,
    is_valid_constant_name,
    parse,
    resolve_constants,
)
from curvis.formula.ast import ConstantRef

# Exact SI values (2019 redefinition, CODATA 2018).
N_A = Fraction("6.02214076e23")
K_B = Fraction("1.380649e-23")
Q_E = Fraction("1.602176634e-19")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("N_A", N_A),
        ("k_B", K_B),
        ("h", Fraction("6.62607015e-34")),
        ("c", Fraction(299792458)),
        ("q_e", Q_E),
        ("R", N_A * K_B),  # 8.31446261815324 exactly
        ("F", N_A * Q_E),  # 96485.33212331001840... exactly
    ],
)
def test_codata_2018_values_are_the_nearest_double(name: str, value: Fraction) -> None:
    assert BUILTIN_VALUES[name] == float(value)


def test_math_constants() -> None:
    assert BUILTIN_VALUES["pi"] == BUILTIN_VALUES["π"] == 3.141592653589793
    assert BUILTIN_VALUES["e"] == 2.718281828459045


def test_every_builtin_has_a_unit_or_is_dimensionless_and_a_description() -> None:
    units = {c.name: c.unit for c in BUILTIN_CONSTANTS}
    assert units == {
        "pi": "", "π": "", "e": "",
        "N_A": "mol⁻¹", "R": "J mol⁻¹ K⁻¹", "k_B": "J K⁻¹", "h": "J s",
        "c": "m s⁻¹", "q_e": "C", "F": "C mol⁻¹",
    }  # fmt: skip
    assert all(c.description for c in BUILTIN_CONSTANTS)


@pytest.mark.parametrize("constant", BUILTIN_CONSTANTS, ids=lambda c: c.name)
def test_every_builtin_name_parses_as_a_constant(constant: object) -> None:
    name = constant.name  # type: ignore[attr-defined]
    assert parse(name).root == ConstantRef(name, parse(name).root.span)  # type: ignore[union-attr]


def test_project_overrides_global() -> None:
    global_ = [ProjectConstant("k", 1.0), ProjectConstant("only_global", 3.0)]
    project = [ProjectConstant("k", 2.0), ProjectConstant("only_project", 4.0)]
    values = resolve_constants(global_, project)
    assert values["k"] == 2.0
    assert values["only_global"] == 3.0
    assert values["only_project"] == 4.0
    assert values["pi"] == BUILTIN_VALUES["pi"]


def test_names_are_case_sensitive() -> None:
    values = resolve_constants([ProjectConstant("k", 1.0)], [ProjectConstant("K", 2.0)])
    assert (values["k"], values["K"]) == (1.0, 2.0)


def test_builtins_cannot_be_overridden() -> None:
    values = resolve_constants([ProjectConstant("R", 1.0)], [ProjectConstant("pi", 3.0)])
    assert values["R"] == BUILTIN_VALUES["R"]
    assert values["pi"] == BUILTIN_VALUES["pi"]


@pytest.mark.parametrize("name", ["k", "k_on", "Kd2", "ε", "_x", "r", "H", "PI"])
def test_valid_names(name: str) -> None:
    assert is_valid_constant_name(name)
    assert parse(name).constants == (name,)


@pytest.mark.parametrize(
    ("name", "problem"),
    [
        ("", "Enter a name."),
        ("2k", "Use letters, digits and _ only, starting with a letter."),
        ("k on", "Use letters, digits and _ only, starting with a letter."),
        ("k-on", "Use letters, digits and _ only, starting with a letter."),
        ("[k]", "Use letters, digits and _ only, starting with a letter."),
        ("R", "'R' is a built-in constant."),
        ("π", "'π' is a built-in constant."),
        ("mean", "'mean' is the name of a function."),
        ("ROW", "'ROW' is the name of a function."),
    ],
)
def test_invalid_names(name: str, problem: str) -> None:
    assert constant_name_problem(name) == problem
    assert not is_valid_constant_name(name)
