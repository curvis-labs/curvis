"""The Kruskal-Wallis analysis: three or more ticked series, followed by Dunn's test of
every pair or of each group against a control, Holm-adjusted."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from curvis.analysis.base import (
    AnalysisError,
    AnalysisInputs,
    Params,
    input_param,
    param,
    register,
)
from curvis.analysis.compare.common import (
    STAT_IMPORTS,
    GroupComparison,
    few_values_warnings,
    group_values,
    name_list,
    number,
    p_text,
    significant,
)
from curvis.analysis.compare.kruskal import DUNN, METHOD, DunnComparison, KruskalStats, dunn
from curvis.analysis.compare.kruskal import kruskal_wallis as kruskal_test
from curvis.analysis.result import Cell, Kind, Result, ResultColumn, ResultTable, Value
from curvis.data import Table

ALL_PAIRS = "all_pairs"
CONTROL = "control"
POSTHOC_CHOICES = (
    (ALL_PAIRS, "Dunn: compare every pair"),
    (CONTROL, "Dunn: compare each group with a control"),
)


@dataclass(frozen=True)
class KruskalParams(Params):
    posthoc: str = param(ALL_PAIRS, "&Post-hoc test", choices=POSTHOC_CHOICES)
    control: str = param("", "&Control group", from_inputs=True, enabled_when=("posthoc", CONTROL))


class KruskalWallis(GroupComparison):
    id = "kruskal_wallis"
    label = "&Kruskal-Wallis test"
    method = METHOD
    params_type = KruskalParams
    min_groups = 3
    imports: ClassVar[tuple[str, ...]] = (*STAT_IMPORTS, "scikit_posthocs")

    def describe(self, table: Table, inputs: Sequence[str], params: Params) -> str:
        assert isinstance(params, KruskalParams)
        chosen = self.selected(table, inputs)
        start = f"{METHOD} of {name_list([c.name for c in chosen])}, followed by {DUNN}"
        if params.posthoc == CONTROL:
            control = input_param(params, "control", [c.id for c in chosen])
            control_name = next((c.name for c in chosen if c.id == control), "")
            return f"{start} of each group against {control_name}, Holm-adjusted."
        return f"{start} of every pair, Holm-adjusted."

    @staticmethod
    def run(inputs: AnalysisInputs, params: KruskalParams) -> Result:
        if len(inputs.series) < 3:
            raise AnalysisError("Tick at least 3 series, then run the analysis again.")
        groups = [group_values(s) for s in inputs.series]
        names = [s.name for s in inputs.series]
        for name, group in zip(names, groups, strict=True):
            if not (group == group).any():  # all missing
                raise AnalysisError(f"{name} has no values. Enter data or untick it.")
        kw = kruskal_test(groups)
        if not kw.defined:
            raise AnalysisError(
                "All values are the same, so the Kruskal-Wallis test cannot be computed."
            )
        control: int | None = None
        if params.posthoc == CONTROL:
            ids = [s.id for s in inputs.series]
            control_id = input_param(params, "control", ids)
            control = ids.index(control_id) if control_id in ids else 0
        comparisons = dunn(kw, control)
        hits = sum(significant(c.p_value) for c in comparisons)
        verdict = (
            "differ significantly" if significant(kw.p_value) else "do not differ significantly"
        )
        test = f"H({kw.df}) = {number(kw.h)}, {p_text(kw.p_value)}"
        verb = "is" if hits == 1 else "are"
        summary = (
            f"The distributions of the groups {verdict} ({test}); {hits} of "
            f"{len(comparisons)} comparisons {verb} significant in {DUNN}."
        )
        family = "every pair" if control is None else f"each group against {names[control]}"
        notes = [
            "The Kruskal-Wallis P value uses the chi-square approximation.",
            f"Dunn's P values are adjusted with Holm's method for the {len(comparisons)} "
            f"comparisons of {family}.",
        ]
        if kw.ties:
            notes.append("H and Dunn's z are corrected for tied values.")
        values = (
            Value("P value", kw.p_value, Kind.P_VALUE),
            Value("Kruskal-Wallis H", kw.h),
            Value("DF", kw.df, Kind.INTEGER),
            Value("Total number of values", kw.n_total, Kind.INTEGER),
        )
        tables = (_groups_table(kw, names), _dunn_table(comparisons, names))
        warnings = few_values_warnings(kw.samples, names)
        return Result(METHOD, summary, tuple(warnings), values, tables, notes=tuple(notes))


def _groups_table(kw: KruskalStats, names: Sequence[str]) -> ResultTable:
    columns = (
        ResultColumn("Group", Kind.TEXT),
        ResultColumn("n", Kind.INTEGER),
        ResultColumn("Median"),
        ResultColumn("Mean rank"),
    )
    rows = tuple(
        (name, s.n, s.median, float(r))
        for name, s, r in zip(names, kw.samples, kw.mean_ranks, strict=True)
    )
    return ResultTable("Groups", columns, rows)


def _dunn_table(comparisons: Sequence[DunnComparison], names: Sequence[str]) -> ResultTable:
    columns = (
        ResultColumn("Comparison", Kind.TEXT),
        ResultColumn("Mean rank difference"),
        ResultColumn("z"),
        ResultColumn("Unadjusted P value"),
        ResultColumn("Adjusted P value", Kind.P_VALUE),
    )
    rows: tuple[tuple[Cell, ...], ...] = tuple(
        (f"{names[c.j]} − {names[c.i]}", c.mean_rank_diff, c.z, c.p_unadjusted, c.p_value)
        for c in comparisons
    )
    return ResultTable(DUNN, columns, rows)


register(KruskalWallis())
