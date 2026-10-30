"""Formula grammar, parser, compiler to NumPy, and constants.

    >>> evaluate("[Signal] - FIRST([Signal])", {"Signal": signal}, constants, len(signal))

See :mod:`curvis.formula.parser` for the grammar and :mod:`curvis.formula.compiler`
for how values, missing values and column functions behave.
"""

from curvis.formula.ast import Function, Node, to_text
from curvis.formula.compiler import CompiledFormula, check, compile_formula, evaluate
from curvis.formula.constants import (
    BUILTIN_CONSTANTS,
    BUILTIN_VALUES,
    BuiltinConstant,
    constant_name_problem,
    is_valid_constant_name,
    resolve_constants,
)
from curvis.formula.errors import FormulaError
from curvis.formula.parser import Formula, parse

__all__ = [
    "BUILTIN_CONSTANTS",
    "BUILTIN_VALUES",
    "BuiltinConstant",
    "CompiledFormula",
    "Formula",
    "FormulaError",
    "Function",
    "Node",
    "check",
    "compile_formula",
    "constant_name_problem",
    "evaluate",
    "is_valid_constant_name",
    "parse",
    "resolve_constants",
    "to_text",
]
