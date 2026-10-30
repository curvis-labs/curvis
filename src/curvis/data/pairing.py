"""Pairing: which columns play role A and role B, and which A column goes with each B column.

Columns are paired by position: the first B column with the first A column, the second
with the second, and so on (*index-matched replicates*, e.g. "sample 1" with "baseline 1").
If role A has a single column, it goes with every B column.

Without pairing there is only role A, and every A column is a column of its own.

Formulas in the Calculate dialog read roles with ``{A}`` (the A column that goes with the
column being calculated), ``{B}`` (that column itself, when paired) and ``{A:mean}`` /
``{B:mean}`` (the row-wise mean of all columns of the role, ignoring missing values).
Paired analyses read the same pairs through :meth:`Pairing.pairs`.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

import numpy as np

from curvis.data.names import name_key
from curvis.data.replicates import FloatArray, row_stats
from curvis.data.table import Table

DEFAULT_ROLE_A = "A"
DEFAULT_ROLE_B = "B"
MEAN = "mean"


def role_name_problem(name: str) -> str | None:
    """Why ``name`` cannot name a role, or ``None`` if it can."""
    if not name.strip():
        return "Enter a role name."
    if any(char in name for char in "{}:"):
        return "Role names cannot contain {, } or :."
    return None


@dataclass(frozen=True)
class Pairing:
    """Column ids for role A and, when ``paired``, role B, in table order."""

    a_columns: tuple[str, ...] = ()
    b_columns: tuple[str, ...] = ()
    paired: bool = False
    a_name: str = DEFAULT_ROLE_A
    b_name: str = DEFAULT_ROLE_B

    @property
    def targets(self) -> tuple[str, ...]:
        """The columns a calculation makes one result for: role B when paired, else role A."""
        return self.b_columns if self.paired else self.a_columns

    @property
    def role_names(self) -> list[str]:
        return [self.a_name, self.b_name] if self.paired else [self.a_name]

    def problem(self) -> str | None:
        """Why this pairing cannot be used, or ``None`` if it can."""
        a, b = self.a_name.strip(), self.b_name.strip()
        for name in self.role_names:
            problem = role_name_problem(name)
            if problem is not None:
                return problem
        if not self.paired:
            return None if self.a_columns else "Select at least one column."
        if name_key(a) == name_key(b):
            return "The two roles need different names."
        if set(self.a_columns) & set(self.b_columns):
            return "A column cannot have both roles."
        if not self.a_columns:
            return f"Give at least one column the role {a}."
        if not self.b_columns:
            return f"Give at least one column the role {b}."
        n_a, n_b = len(self.a_columns), len(self.b_columns)
        if n_a not in (1, n_b):
            return (
                f"Role {a} has {n_a} columns and role {b} has {n_b}. Give both roles the "
                f"same number of columns, or role {a} just one."
            )
        return None

    def partner(self, index: int) -> str:
        """The A column that goes with B column ``index`` (paired only)."""
        return self.a_columns[0] if len(self.a_columns) == 1 else self.a_columns[index]

    def pairs(self) -> list[tuple[str, str]]:
        """(A column, B column) for every B column. Check :meth:`problem` first."""
        return [(self.partner(i), b) for i, b in enumerate(self.b_columns)]

    def role_sources(self, index: int) -> dict[str, list[str]]:
        """For target ``index``: role key (``"a"``, ``"a:mean"``, …) → columns to read.

        A key with several columns reads their row-wise mean.
        """
        a, b = name_key(self.a_name.strip()), name_key(self.b_name.strip())
        if not self.paired:
            return {a: [self.a_columns[index]], f"{a}:{MEAN}": list(self.a_columns)}
        return {
            a: [self.partner(index)],
            f"{a}:{MEAN}": list(self.a_columns),
            b: [self.b_columns[index]],
            f"{b}:{MEAN}": list(self.b_columns),
        }

    def columns(self) -> tuple[str, ...]:
        """Every column this pairing reads."""
        return tuple(dict.fromkeys([*self.a_columns, *(self.b_columns if self.paired else ())]))


def role_values(
    table: Table, pairing: Pairing, index: int, keys: Collection[str] | None = None
) -> dict[str, FloatArray]:
    """The values of the role references for target ``index``: new arrays, safe to keep.

    ``keys`` limits the result to those role keys (default: all). Raises ``KeyError``
    with the column id if a column that is needed is not in ``table``.
    """
    values: dict[str, FloatArray] = {}
    for key, column_ids in pairing.role_sources(index).items():
        if keys is not None and key not in keys:
            continue
        arrays = [table.column(c).values for c in column_ids]
        if len(arrays) == 1:
            values[key] = np.array(arrays[0], dtype=np.float64)
        else:
            values[key] = row_stats(arrays).mean
    return values
