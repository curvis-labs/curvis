"""Formulas made by the ready-made options of the Calculate dialog.

Each option turns its settings (``params``) into ordinary formula text, which the dialog
shows and the user can edit. Formulas use role references, so one formula serves every
selected column: ``{A}`` is the column being calculated (unpaired) or its role A partner
(paired), ``{B}`` the role B column being calculated, ``{A:mean}`` the row-wise mean of
the role A columns.

=====================  ===========================  ================================
Option                 Unpaired                     Paired
=====================  ===========================  ================================
Baseline subtraction   ``{A} - FIRST({A})``,        ``{B} - {A}``, ``{B} - {A:mean}``
                       ``{A} - 2``, ``{A} - [Blank]``,
                       ``{A} - MEAN([Blank])``
Normalize to 0–100%    ``({A} - MIN({A})) / (MAX({A}) - MIN({A})) * 100``, or with
                       typed values: ``({A} - 5) / (95 - 5) * 100``
Fold change            as baseline, with ``/``      ``{B} / {A}``, ``{B} / {A:mean}``
=====================  ===========================  ================================
"""

from collections.abc import Mapping
from typing import Final

from curvis.core.calculations import CalculationKind
from curvis.core.constants import JsonValue
from curvis.data import Pairing, Table
from curvis.data.column import format_number

# Values of params["reference"] for baseline subtraction and fold change.
FIRST: Final = "first"
"""The first value of the column itself."""
VALUE: Final = "value"
"""A number typed in the dialog (params["value"])."""
COLUMN: Final = "column"
"""Another column, row by row (params["column"] holds its id)."""
COLUMN_MEAN: Final = "column_mean"
"""The mean of another column."""
MATCHED: Final = "matched"
"""Paired: the matching role A column, row by row."""
ROLE_MEAN: Final = "role_mean"
"""Paired: the row-wise mean of the role A columns."""

UNPAIRED_REFERENCES: Final = (FIRST, VALUE, COLUMN, COLUMN_MEAN)
PAIRED_REFERENCES: Final = (MATCHED, ROLE_MEAN)

# Values of params["range"] for normalize.
OWN_RANGE: Final = "own"
"""0% and 100% are the column's own minimum and maximum."""
VALUES_RANGE: Final = "values"
"""0% and 100% are typed in the dialog (params["low"] and params["high"])."""

DEFAULT_SUFFIXES: Final = {
    CalculationKind.CUSTOM: "_calc",
    CalculationKind.BASELINE: "_bl",
    CalculationKind.NORMALIZE: "_norm",
    CalculationKind.FOLD_CHANGE: "_fc",
}

PAIRABLE: Final = frozenset(
    {CalculationKind.CUSTOM, CalculationKind.BASELINE, CalculationKind.FOLD_CHANGE}
)
"""Kinds that can use paired data (normalizing works on each column by itself)."""


def default_params(kind: CalculationKind, paired: bool = False) -> dict[str, JsonValue]:
    """The settings the dialog starts with for ``kind``."""
    if kind is CalculationKind.NORMALIZE:
        return {"range": OWN_RANGE, "low": 0.0, "high": 100.0}
    if kind in (CalculationKind.BASELINE, CalculationKind.FOLD_CHANGE):
        value = 1.0 if kind is CalculationKind.FOLD_CHANGE else 0.0
        return {"reference": ROLE_MEAN if paired else FIRST, "value": value}
    return {}


def generate(
    kind: CalculationKind, params: Mapping[str, JsonValue], pairing: Pairing, table: Table
) -> str | None:
    """Formula text for a ready-made option, or ``None`` for a custom formula or settings
    that cannot make one (e.g. a reference column that no longer exists)."""
    a = _role(pairing.a_name)
    if kind is CalculationKind.NORMALIZE:
        if params.get("range") == VALUES_RANGE:
            low, high = _number(params.get("low")), _number(params.get("high"))
            if low is None or high is None:
                return None
            return f"({a} - {low}) / ({high} - {low}) * 100"
        return f"({a} - MIN({a})) / (MAX({a}) - MIN({a})) * 100"
    if kind not in (CalculationKind.BASELINE, CalculationKind.FOLD_CHANGE):
        return None
    op = "-" if kind is CalculationKind.BASELINE else "/"
    reference = params.get("reference")
    if pairing.paired:
        b = _role(pairing.b_name)
        if reference == MATCHED:
            return f"{b} {op} {a}"
        if reference == ROLE_MEAN:
            return f"{b} {op} {_role(pairing.a_name, mean=True)}"
        return None
    if reference == FIRST:
        return f"{a} {op} FIRST({a})"
    if reference == VALUE:
        value = _number(params.get("value"))
        return None if value is None else f"{a} {op} {value}"
    if reference in (COLUMN, COLUMN_MEAN):
        column_id = params.get("column")
        try:
            column = table.column(column_id) if isinstance(column_id, str) else None
        except KeyError:
            column = None
        if column is None:
            return None
        ref = "[" + column.name.replace("]", "]]") + "]"
        return f"{a} {op} {ref}" if reference == COLUMN else f"{a} {op} MEAN({ref})"
    return None


def _role(name: str, mean: bool = False) -> str:
    return "{" + name.strip() + (":mean" if mean else "") + "}"


def _number(value: JsonValue) -> str | None:
    """Formula text for a number; negative numbers in parentheses."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    text = format_number(float(value))
    if text is None or text in ("inf", "-inf"):
        return None
    return f"({text})" if float(value) < 0 else text
