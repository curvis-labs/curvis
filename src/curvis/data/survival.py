"""Time + Event pairs: the survival data of a table.

Each Event column is paired with the nearest Time column to its left, and each pair is
one survival curve, named after its Event column. So a table can hold pairs side by side
(Time | Event | Time | Event), or one Time column shared by several Event columns (the
layout of a Prism survival table, where a subject's row is empty in the Event columns
of the other groups). An Event column with no Time column to its left is not paired.

Time values are 0 or more and Event values 1 (event) or 0 (censored); the column
checks (:func:`curvis.data.column.value_problem`) refuse anything else.
"""

from __future__ import annotations

from dataclasses import dataclass

from curvis.data.roles import ColumnRole
from curvis.data.table import Table


@dataclass(frozen=True)
class SurvivalPair:
    """One survival curve: a Time column and an Event column."""

    name: str
    """The Event column's name."""
    time_id: str
    event_id: str

    @property
    def source(self) -> str:
        """The id analyses and graphs use for this pair: its Event column's id."""
        return self.event_id


def survival_pairs(table: Table) -> list[SurvivalPair]:
    """The table's Time + Event pairs, left to right by Event column."""
    pairs: list[SurvivalPair] = []
    time_id: str | None = None
    for column in table.columns:
        if column.role is ColumnRole.TIME:
            time_id = column.id
        elif column.role is ColumnRole.EVENT and time_id is not None:
            pairs.append(SurvivalPair(column.name, time_id, column.id))
    return pairs


def has_survival_pair(table: Table) -> bool:
    return bool(survival_pairs(table))
