"""What the group-comparison analyses share: reading groups, checks, warnings and wording.

Each ticked series is one group. A group's values are all values of its replicate
columns, column by column (so two series with the same layout pair value by value),
leaving out missing values.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

import numpy as np
import numpy.typing as npt

from curvis.analysis.base import AnalysisType, Family, Params, SeriesData, series_choices
from curvis.analysis.compare.samples import Sample
from curvis.analysis.result import format_number, format_p
from curvis.data import Table

type FloatArray = npt.NDArray[np.float64]

ALPHA = 0.05
"""P values below this are called significant in the plain-language reading."""

FEW_VALUES = 3
"""Fewer values than this in a group get a warning."""

STAT_IMPORTS = ("scipy.special", "scipy.stats", "scipy.optimize")


def group_values(series: SeriesData) -> FloatArray:
    """All values of a series, column by column, missing values included (as NaN)."""
    if not series.columns:
        return np.zeros(0)
    return np.concatenate([np.asarray(c, dtype=np.float64) for c in series.columns])


def name_list(names: Sequence[str]) -> str:
    """ "A", "A and B", "A, B and C"."""
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def p_text(p: float) -> str:
    """ "P = 0.0312" or "P < 0.001"."""
    shown = format_p(p)
    return f"P {shown}" if shown.startswith("<") else f"P = {shown}"


def number(value: float) -> str:
    """A number for a sentence: as shown in the results, with a real minus sign."""
    return format_number(value).replace("-", "−")


def significant(p: float) -> bool:
    return p < ALPHA


def few_values_warnings(samples: Sequence[Sample], names: Sequence[str]) -> list[str]:
    """A warning for each group with fewer than 3 values."""
    out: list[str] = []
    for s, name in zip(samples, names, strict=True):
        if s.n < FEW_VALUES:
            values = "value" if s.n == 1 else "values"
            out.append(
                f"{name} has only {s.n} {values}. With fewer than {FEW_VALUES} values per "
                "group the test has little power and its assumptions cannot be checked."
            )
    return out


def zero_variance_warnings(samples: Sequence[Sample], names: Sequence[str]) -> list[str]:
    """A warning for each group (with 2 or more values) whose values are all the same."""
    return [
        f"All values in {name} are the same (SD = 0). Check that the data are entered "
        "correctly; the test assumes the values vary."
        for s, name in zip(samples, names, strict=True)
        if s.n >= 2 and s.ss == 0
    ]


class GroupComparison(AnalysisType):
    """Base class of the analyses that compare ticked series as groups."""

    family = Family.COMPARE
    imports = STAT_IMPORTS
    min_groups: ClassVar[int] = 2
    max_groups: ClassVar[int | None] = None

    def problem(self, table: Table) -> str | None:
        if len(series_choices(table)) < self.min_groups:
            return f"Needs at least {self.min_groups} Y columns or replicate groups."
        return None

    def default_inputs(self, table: Table) -> tuple[str, ...]:
        ids = [c.id for c in self.candidates(table)]
        return tuple(ids[: self.max_groups] if self.max_groups else ids)

    def check(self, table: Table, inputs: Sequence[str], params: Params) -> str | None:
        count = len(self.selected(table, inputs))
        if self.max_groups == self.min_groups and count != self.min_groups:
            return f"Tick exactly {self.min_groups} series to compare."
        if count < self.min_groups:
            return f"Tick at least {self.min_groups} series to compare."
        return None
