"""Calculations: calculated columns made by the Calculate dialog.

A :class:`Calculation` is one formula applied to the columns selected in the dialog. It
makes one calculated column per selected column (per role B column when paired); the
formula reads that column, and its partner, through role references such as ``{A}``,
``{B}`` and ``{A:mean}`` (see :mod:`curvis.data.pairing`). Plain ``[column]``
references work too.

A calculation stores its *definition*: formula text, pairing, the calculated columns
and, for the ready-made kinds, the options chosen in the dialog, so the dialog can
reopen as it was. The values of calculated columns are derived from it and kept up to
date by :class:`curvis.compute.calc_engine.CalcEngine`.

Calculations are replaced as a whole when they change, never edited in place, so a
command can undo a change by putting the old object back.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from curvis.core.constants import JsonValue
from curvis.data import new_id
from curvis.data.pairing import Pairing


class CalculationKind(StrEnum):
    """Which option of the Calculate dialog made the formula."""

    CUSTOM = "custom"
    BASELINE = "baseline"
    NORMALIZE = "normalize"
    FOLD_CHANGE = "fold_change"

    @property
    def label(self) -> str:
        return _LABELS[self]


_LABELS = {
    CalculationKind.CUSTOM: "Custom formula",
    CalculationKind.BASELINE: "Baseline subtraction",
    CalculationKind.NORMALIZE: "Normalize to 0–100%",
    CalculationKind.FOLD_CHANGE: "Fold change vs. reference",
}


@dataclass(frozen=True)
class CalculatedColumn:
    """The column ``column_id`` holds the formula's results for the column ``source_id``."""

    column_id: str
    source_id: str


@dataclass(frozen=True)
class Calculation:
    """A formula applied to the selected columns of one table."""

    table_id: str
    formula: str
    pairing: Pairing
    kind: CalculationKind = CalculationKind.CUSTOM
    params: dict[str, JsonValue] = field(default_factory=dict)
    """Options of the ready-made kinds, e.g. ``{"reference": "first"}``."""
    suffix: str = "_calc"
    """Added to the name of a source column to name its calculated column."""
    append_at_end: bool = False
    """Put the calculated columns after the last column instead of next to their sources."""
    columns: tuple[CalculatedColumn, ...] = ()
    id: str = field(default_factory=new_id)

    def output_for(self, column_id: str) -> CalculatedColumn | None:
        """The calculated column with id ``column_id``, or ``None``."""
        return next((c for c in self.columns if c.column_id == column_id), None)

    def target_index(self, output: CalculatedColumn) -> int:
        """Position of the output's source among the pairing's targets (-1 if gone)."""
        targets = self.pairing.targets
        return targets.index(output.source_id) if output.source_id in targets else -1


def constant_node(name: str) -> str:
    """Dependency-graph node of the constant called ``name`` (project or global)."""
    return f"constant:{name}"
