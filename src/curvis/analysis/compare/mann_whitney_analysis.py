"""The Mann-Whitney U test analysis: two ticked series, two-sided. The result states
whether the P value is exact or from the normal approximation."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from curvis.analysis.base import AnalysisError, AnalysisInputs, Params, register
from curvis.analysis.compare.common import (
    GroupComparison,
    few_values_warnings,
    group_values,
    p_text,
    significant,
)
from curvis.analysis.compare.mann_whitney import EXACT_LIMIT, METHOD, mann_whitney
from curvis.analysis.result import Kind, Result, Value
from curvis.data import Table


@dataclass(frozen=True)
class MannWhitneyParams(Params):
    pass


class MannWhitney(GroupComparison):
    id = "mann_whitney"
    label = "&Mann-Whitney test"
    method = METHOD
    params_type = MannWhitneyParams
    min_groups = 2
    max_groups = 2

    def describe(self, table: Table, inputs: Sequence[str], params: Params) -> str:
        a, b = (c.name for c in self.selected(table, inputs))
        return (
            f"{METHOD} of {b} against {a}: an exact P value when both groups have fewer "
            f"than {EXACT_LIMIT} values, otherwise the normal approximation."
        )

    @staticmethod
    def run(inputs: AnalysisInputs, params: MannWhitneyParams) -> Result:
        if len(inputs.series) != 2:
            raise AnalysisError("Tick exactly 2 series, then run the analysis again.")
        first, second = inputs.series
        a, b = group_values(first), group_values(second)
        names = (first.name, second.name)
        for name, group in zip(names, (a, b), strict=True):
            if not (group == group).any():  # all missing
                raise AnalysisError(f"{name} has no values. Enter data or untick it.")
        r = mann_whitney(a, b)
        if math.isnan(r.p_value):
            raise AnalysisError(
                "All values are the same, so the Mann-Whitney test cannot be computed."
            )
        method = "Exact" if r.exact else "Normal approximation with continuity correction"
        u_text = f"U = {r.u:g}"  # a count of pairs (ties count 1/2)
        if significant(r.p_value):
            # U counts the pairs with a > b: below half of all pairs, B tends to be higher.
            direction = "higher" if r.u < r.a.n * r.b.n / 2 else "lower"
            summary = (
                f"The values of {names[1]} tend to be {direction} than those of {names[0]}; "
                f"the difference is statistically significant ({p_text(r.p_value)}, {u_text})."
            )
        else:
            summary = (
                f"The values of {names[0]} and {names[1]} do not differ significantly "
                f"({p_text(r.p_value)}, {u_text})."
            )
        if r.exact:
            note = "The P value is exact" + (", computed for the tied values." if r.ties else ".")
        else:
            note = (
                "The P value uses the normal approximation with continuity correction, "
                f"because a group has {EXACT_LIMIT} or more values."
            )
        values = (
            Value("P value", r.p_value, Kind.P_VALUE),
            Value("P value method", method, Kind.TEXT),
            Value("Mann-Whitney U", r.u, Kind.LABEL),  # shown as is: 8, 10.5
            Value(f"Median of {names[0]}", r.a.median),
            Value(f"Median of {names[1]}", r.b.median),
            Value(f"n ({names[0]})", r.a.n, Kind.INTEGER),
            Value(f"n ({names[1]})", r.b.n, Kind.INTEGER),
        )
        warnings = few_values_warnings((r.a, r.b), names)
        return Result(METHOD, summary, tuple(warnings), values, notes=(note,))


register(MannWhitney())
