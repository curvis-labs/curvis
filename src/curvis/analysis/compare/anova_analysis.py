"""The ordinary one-way ANOVA analysis, with Tukey's, Dunnett's or Holm's post-hoc test."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from curvis.analysis.base import (
    AnalysisError,
    AnalysisInputs,
    Params,
    input_param,
    param,
    register,
)
from curvis.analysis.compare.anova import (
    AnovaStats,
    Comparison,
    dunnett,
    holm_pairwise,
    one_way_anova,
    tukey,
)
from curvis.analysis.compare.common import (
    GroupComparison,
    few_values_warnings,
    group_values,
    name_list,
    number,
    p_text,
    significant,
    zero_variance_warnings,
)
from curvis.analysis.result import Cell, Kind, Result, ResultColumn, ResultTable, Value
from curvis.data import Table

METHOD = "Ordinary one-way ANOVA"
TUKEY = "tukey"
DUNNETT = "dunnett"
HOLM = "holm"
POSTHOC_METHODS = {
    TUKEY: "Tukey's multiple comparisons test",
    DUNNETT: "Dunnett's multiple comparisons test",
    HOLM: "Pairwise t tests with Holm's correction",
}
POSTHOC_CHOICES = (
    (TUKEY, "Tukey: compare every pair"),
    (DUNNETT, "Dunnett: compare each group with a control"),
    (HOLM, "Holm: t tests of every pair, Holm-corrected"),
)


@dataclass(frozen=True)
class AnovaParams(Params):
    posthoc: str = param(TUKEY, "&Post-hoc test", choices=POSTHOC_CHOICES)
    control: str = param("", "&Control group", from_inputs=True, enabled_when=("posthoc", DUNNETT))


class OneWayAnova(GroupComparison):
    id = "one_way_anova"
    label = "&One-way ANOVA"
    method = METHOD
    params_type = AnovaParams
    min_groups = 2

    def describe(self, table: Table, inputs: Sequence[str], params: Params) -> str:
        assert isinstance(params, AnovaParams)
        chosen = self.selected(table, inputs)
        names = [c.name for c in chosen]
        start = f"{METHOD} of {name_list(names)}, followed by {POSTHOC_METHODS[params.posthoc]}"
        if params.posthoc == DUNNETT:
            control = input_param(params, "control", [c.id for c in chosen])
            control_name = next((c.name for c in chosen if c.id == control), "")
            return f"{start} of each group against {control_name}."
        return f"{start} of every pair."

    @staticmethod
    def run(inputs: AnalysisInputs, params: AnovaParams) -> Result:
        if len(inputs.series) < 2:
            raise AnalysisError("Tick at least 2 series, then run the analysis again.")
        groups = [group_values(s) for s in inputs.series]
        names = [s.name for s in inputs.series]
        for name, group in zip(names, groups, strict=True):
            if not (group == group).any():  # all missing
                raise AnalysisError(f"{name} has no values. Enter data or untick it.")
        a = one_way_anova(groups)
        if a.df_within == 0:
            raise AnalysisError(
                "Each group has only one value, so there is no variation within groups to "
                "test against. Add replicates."
            )
        if not a.defined:
            raise AnalysisError(
                "The values do not vary within any group, so the ANOVA cannot be computed."
            )
        ids = [s.id for s in inputs.series]
        if params.posthoc == DUNNETT:
            control_id = input_param(params, "control", ids)
            control = ids.index(control_id) if control_id in ids else 0
            comparisons = dunnett(a, control)
        elif params.posthoc == HOLM:
            comparisons = holm_pairwise(a)
        else:
            comparisons = tukey(a)
        posthoc = POSTHOC_METHODS[params.posthoc]
        hits = sum(significant(c.p_value) for c in comparisons)
        verdict = (
            "differ significantly" if significant(a.p_value) else "do not differ significantly"
        )
        test = f"F({a.df_between}, {a.df_within}) = {number(a.f)}, {p_text(a.p_value)}"
        verb = "is" if hits == 1 else "are"
        summary = (
            f"The group means {verdict} ({test}); {hits} of {len(comparisons)} comparisons "
            f"{verb} significant in {posthoc}."
        )
        warnings = few_values_warnings(a.samples, names) + zero_variance_warnings(a.samples, names)
        values = (
            Value("P value", a.p_value, Kind.P_VALUE),
            Value("F", a.f),
            Value("DF between groups", a.df_between, Kind.INTEGER),
            Value("DF within groups", a.df_within, Kind.INTEGER),
        )
        tables = (
            _anova_table(a),
            _groups_table(a, names),
            _posthoc_table(posthoc, params.posthoc, comparisons, names),
        )
        return Result(METHOD, summary, tuple(warnings), values, tables)


def _anova_table(a: AnovaStats) -> ResultTable:
    columns = (
        ResultColumn("Source", Kind.TEXT),
        ResultColumn("SS"),
        ResultColumn("DF", Kind.INTEGER),
        ResultColumn("MS"),
        ResultColumn("F"),
        ResultColumn("P value", Kind.P_VALUE),
    )
    n_total = sum(s.n for s in a.samples)
    rows: tuple[tuple[Cell, ...], ...] = (
        ("Between groups", a.ss_between, a.df_between, a.ms_between, a.f, a.p_value),
        ("Within groups (residual)", a.ss_within, a.df_within, a.ms_within, None, None),
        ("Total", a.ss_between + a.ss_within, n_total - 1, None, None, None),
    )
    return ResultTable("ANOVA table", columns, rows)


def _groups_table(a: AnovaStats, names: Sequence[str]) -> ResultTable:
    columns = (
        ResultColumn("Group", Kind.TEXT),
        ResultColumn("n", Kind.INTEGER),
        ResultColumn("Mean"),
        ResultColumn("SD"),
    )
    rows = tuple((name, s.n, s.mean, s.sd) for name, s in zip(names, a.samples, strict=True))
    return ResultTable("Groups", columns, rows)


def _posthoc_table(
    title: str, kind: str, comparisons: Sequence[Comparison], names: Sequence[str]
) -> ResultTable:
    def label(c: Comparison) -> str:
        return f"{names[c.j]} − {names[c.i]}"

    columns: tuple[ResultColumn, ...]
    if kind == HOLM:
        columns = (
            ResultColumn("Comparison", Kind.TEXT),
            ResultColumn("Mean difference"),
            ResultColumn("t"),
            ResultColumn("Unadjusted P value"),
            ResultColumn("Adjusted P value", Kind.P_VALUE),
        )
        rows: tuple[tuple[Cell, ...], ...] = tuple(
            (label(c), c.difference, c.statistic, c.p_unadjusted, c.p_value) for c in comparisons
        )
        return ResultTable(title, columns, rows)
    statistic = "q" if kind == TUKEY else "t"
    columns = (
        ResultColumn("Comparison", Kind.TEXT),
        ResultColumn("Mean difference"),
        ResultColumn("Lower 95% CI"),
        ResultColumn("Upper 95% CI"),
        ResultColumn(statistic),
        ResultColumn("Adjusted P value", Kind.P_VALUE),
    )
    rows = tuple(
        (label(c), c.difference, c.ci_lower, c.ci_upper, c.statistic, c.p_value)
        for c in comparisons
    )
    return ResultTable(title, columns, rows)


register(OneWayAnova())
