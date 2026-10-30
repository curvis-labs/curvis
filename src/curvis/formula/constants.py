"""Named constants for formulas: built-in physical constants plus user constants.

Built-in values are CODATA 2018. Since the 2019 SI redefinition N_A, k_B, h, c and the
elementary charge are exact, and so are R = N_A·k_B and F = N_A·e; the values below
are those exact products rounded to the nearest double.

User constants come from two places: the project (``Document.constants``) and the
computer (``Settings.global_constants``). A project constant overrides a global one
with the same name. Built-in names cannot be redefined, so ``R`` always means the gas
constant; a user constant with a built-in name (from a hand-edited file) is ignored.
"""

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from curvis.core.constants import ProjectConstant
from curvis.formula.ast import Function


@dataclass(frozen=True)
class BuiltinConstant:
    name: str
    value: float
    unit: str
    description: str


BUILTIN_CONSTANTS: tuple[BuiltinConstant, ...] = (
    BuiltinConstant("pi", math.pi, "", "π, the ratio of a circle's circumference to its diameter"),
    BuiltinConstant("π", math.pi, "", "π (same as pi)"),
    BuiltinConstant("e", math.e, "", "Euler's number, the base of the natural logarithm"),
    BuiltinConstant("N_A", 6.02214076e23, "mol⁻¹", "Avogadro constant"),
    BuiltinConstant("R", 8.31446261815324, "J mol⁻¹ K⁻¹", "Molar gas constant"),
    BuiltinConstant("k_B", 1.380649e-23, "J K⁻¹", "Boltzmann constant"),
    BuiltinConstant("h", 6.62607015e-34, "J s", "Planck constant"),
    BuiltinConstant("c", 299792458.0, "m s⁻¹", "Speed of light in vacuum"),
    BuiltinConstant("q_e", 1.602176634e-19, "C", "Elementary charge"),
    BuiltinConstant("F", 96485.33212331001, "C mol⁻¹", "Faraday constant"),
)

BUILTIN_VALUES: Mapping[str, float] = {c.name: c.value for c in BUILTIN_CONSTANTS}

_NAME = re.compile(r"[^\W\d]\w*")
_FUNCTION_KEYS = {f.value.casefold() for f in Function}


def constant_name_problem(name: str) -> str | None:
    """Why ``name`` cannot be used for a user constant, or ``None`` if it can."""
    if not name:
        return "Enter a name."
    if not _NAME.fullmatch(name):
        return "Use letters, digits and _ only, starting with a letter."
    if name in BUILTIN_VALUES:
        return f"'{name}' is a built-in constant."
    if name.casefold() in _FUNCTION_KEYS:
        return f"'{name}' is the name of a function."
    return None


def is_valid_constant_name(name: str) -> bool:
    return constant_name_problem(name) is None


def resolve_constants(
    global_constants: Iterable[ProjectConstant] = (),
    project_constants: Iterable[ProjectConstant] = (),
) -> dict[str, float]:
    """Every constant a formula can use, by name: built-in, then global, then project."""
    values: dict[str, float] = {}
    for constant in (*global_constants, *project_constants):
        if constant.name not in BUILTIN_VALUES:
            values[constant.name] = constant.value
    return {**values, **BUILTIN_VALUES}
