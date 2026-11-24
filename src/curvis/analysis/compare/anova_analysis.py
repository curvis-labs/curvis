"""The one-way ANOVA analysis, ordinary or repeated measures, with Tukey's, Dunnett's or
Holm's post-hoc test.

Repeated measures: each row is a subject, matched across the ticked series value by
value (series with several replicate columns are matched column by column, as the
paired t test does). Rows with a missing value are left out. The Greenhouse-Geisser
correction is applied by default; the post-hoc tests use the pooled error.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from curvis.analysis.base import (
    About,
    AnalysisError,
    AnalysisInputs,
    Hypothesis,
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
    FEW_VALUES,
    FloatArray,
    GroupComparison,
    few_values_warnings,
    group_values,
    name_list,
    number,
    p_text,
    pair_ps,
    significant,
    zero_variance_warnings,
)
from curvis.analysis.compare.rm_anova import RMAnovaStats, complete_rows, rm_anova
from curvis.analysis.result import Cell, Kind, Result, ResultColumn, ResultTable, Value
from curvis.data import Table

METHOD = "Ordinary one-way ANOVA"
RM_METHOD = "Repeated-measures one-way ANOVA"
GG = "Greenhouse-Geisser correction"
ORDINARY = "ordinary"
REPEATED = "repeated"
"""Repeated measures with the Greenhouse-Geisser correction (the default for RM)."""
REPEATED_SPHERICAL = "repeated_sphericity"
"""Repeated measures assuming sphericity (no correction)."""
DESIGN_CHOICES = (
    (ORDINARY, "Ordinary: independent groups"),
    (REPEATED, "Repeated measures, matched by row (Greenhouse-Geisser correction)"),
    (REPEATED_SPHERICAL, "Repeated measures, matched by row, assume sphericity"),
)
LOW_EPSILON = 0.75
"""Below this Greenhouse-Geisser epsilon, sphericity looks violated."""
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
    design: str = param(ORDINARY, "De&sign", choices=DESIGN_CHOICES)
    posthoc: str = param(TUKEY, "&Post-hoc test", choices=POSTHOC_CHOICES)
    control: str = param("", "&Control group", from_inputs=True, enabled_when=("posthoc", DUNNETT))


class OneWayAnova(GroupComparison):
    id = "one_way_anova"
    label = "&One-way ANOVA"
    method = METHOD
    section = "Three or more groups"
    about = About(
        "means, post-hoc tests",
        "Use when comparing the means of three or more groups. For skewed data or outliers, "
        "use the Kruskal-Wallis test.",
        option="design",
        by_option={
            ORDINARY: Hypothesis(
                "All group means are equal.",
                "Values are independent and roughly normal in each group, with similar SDs.",
            ),
            REPEATED: Hypothesis(
                "All group means are equal.",
                "Each row is one subject measured in every group; the values are roughly "
                "normal. The correction allows for unequal variances of the differences (no "
                "sphericity needed).",
            ),
            REPEATED_SPHERICAL: Hypothesis(
                "All group means are equal.",
                "Each row is one subject measured in every group; the values are roughly "
                "normal, and the differences between every pair of groups have the same "
                "variance (sphericity).",
            ),
        },
    )
    params_type = AnovaParams
    min_groups = 2

    def check(self, table: Table, inputs: Sequence[str], params: Params) -> str | None:
        problem = super().check(table, inputs, params)
        if problem is not None:
            return problem
        assert isinstance(params, AnovaParams)
        chosen = self.selected(table, inputs)
        if _repeated(params) and len({c.n_replicates for c in chosen}) > 1:
            return "For repeated measures, tick series with the same number of columns."
        return None

    def describe(self, table: Table, inputs: Sequence[str], params: Params) -> str:
        assert isinstance(params, AnovaParams)
        chosen = self.selected(table, inputs)
        names = [c.name for c in chosen]
        method = METHOD
        if _repeated(params):
            method = RM_METHOD + (f" with the {GG}" if params.design == REPEATED else "")
        start = f"{method} of {name_list(names)}, followed by {POSTHOC_METHODS[params.posthoc]}"
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
        if _repeated(params):
            return _run_repeated(inputs, params, groups, names)
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
        comparisons = _posthoc(a, inputs, params)
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
        pairs = pair_ps(inputs.series, [(c.i, c.j, c.p_value) for c in comparisons])
        return Result(METHOD, summary, tuple(warnings), values, tables, pairs=pairs)


def _repeated(params: AnovaParams) -> bool:
    return params.design in (REPEATED, REPEATED_SPHERICAL)


def _posthoc(a: AnovaStats, inputs: AnalysisInputs, params: AnovaParams) -> list[Comparison]:
    ids = [s.id for s in inputs.series]
    if params.posthoc == DUNNETT:
        control_id = input_param(params, "control", ids)
        control = ids.index(control_id) if control_id in ids else 0
        return dunnett(a, control)
    if params.posthoc == HOLM:
        return holm_pairwise(a)
    return tukey(a)


def _run_repeated(
    inputs: AnalysisInputs, params: AnovaParams, groups: Sequence[FloatArray], names: list[str]
) -> Result:
    if len({len(g) for g in groups}) > 1:
        raise AnalysisError("For repeated measures every series needs the same number of columns.")
    _complete, incomplete = complete_rows(groups)
    if len(_complete) < 2:
        raise AnalysisError(
            "Repeated measures needs at least 2 rows with a value in every series "
            f"(there {'is' if len(_complete) == 1 else 'are'} {len(_complete)})."
        )
    r = rm_anova(groups)
    if not r.defined:
        raise AnalysisError(
            "Each row differs from the others by the same amount in every series, so "
            "nothing is left to test against."
        )
    corrected = params.design == REPEATED
    p = r.gg_p_value if corrected else r.p_value
    df_t = r.gg_df_treatment if corrected else r.df_treatment
    df_e = r.gg_df_error if corrected else r.df_error
    method = f"{RM_METHOD} ({GG})" if corrected else RM_METHOD
    a = r.pooled
    comparisons = _posthoc(a, inputs, params)
    posthoc = POSTHOC_METHODS[params.posthoc]
    hits = sum(significant(c.p_value) for c in comparisons)
    verdict = "differ significantly" if significant(p) else "do not differ significantly"
    test = f"F({_df(df_t)}, {_df(df_e)}) = {number(r.f)}, {p_text(p)}"
    verb = "is" if hits == 1 else "are"
    summary = (
        f"The means {verdict} across the {r.k} matched series ({test}); {hits} of "
        f"{len(comparisons)} comparisons {verb} significant in {posthoc}."
    )
    warnings: list[str] = []
    if incomplete:
        rows = "row was" if incomplete == 1 else "rows were"
        warnings.append(
            f"{incomplete} {rows} left out because of missing values: repeated measures "
            f"needs a value in every series. The analysis uses the {r.n_subjects} "
            "complete rows."
        )
    if r.n_subjects < FEW_VALUES:
        warnings.append(
            f"Only {r.n_subjects} complete rows. With fewer than {FEW_VALUES} subjects the "
            "test has little power and its assumptions cannot be checked."
        )
    if not corrected and r.gg_epsilon < LOW_EPSILON:
        warnings.append(
            f"Greenhouse-Geisser epsilon is {number(r.gg_epsilon)}, so the variances of "
            "the differences look unequal (sphericity is violated) and the P value may be "
            "too small. Choose the Greenhouse-Geisser correction."
        )
    values = (
        Value("P value", p, Kind.P_VALUE),
        Value("F", r.f),
        Value("DF numerator", df_t, Kind.NUMBER if corrected else Kind.INTEGER),
        Value("DF denominator", df_e, Kind.NUMBER if corrected else Kind.INTEGER),
        Value("Greenhouse-Geisser epsilon", r.gg_epsilon),
        Value(
            "P value, sphericity assumed" if corrected else "P value, Greenhouse-Geisser",
            r.p_value if corrected else r.gg_p_value,
            Kind.P_VALUE,
        ),
        Value("Number of subjects (rows)", r.n_subjects, Kind.INTEGER),
    )
    tables = (
        _rm_table(r),
        _groups_table(a, names),
        _posthoc_table(posthoc, params.posthoc, comparisons, names),
    )
    pairs = pair_ps(inputs.series, [(c.i, c.j, c.p_value) for c in comparisons])
    return Result(method, summary, tuple(warnings), values, tables, pairs=pairs)


def _df(df: float) -> str:
    return str(int(df)) if float(df).is_integer() else number(df)


def _rm_table(r: RMAnovaStats) -> ResultTable:
    columns = (
        ResultColumn("Source", Kind.TEXT),
        ResultColumn("SS"),
        ResultColumn("DF", Kind.INTEGER),
        ResultColumn("MS"),
        ResultColumn("F"),
        ResultColumn("P value", Kind.P_VALUE),
    )
    rows: tuple[tuple[Cell, ...], ...] = (
        (
            "Treatment (between series)",
            r.ss_treatment,
            r.df_treatment,
            r.ms_treatment,
            r.f,
            r.p_value,
        ),
        ("Subjects (between rows)", r.ss_subjects, r.df_subjects, r.ms_subjects, None, None),
        ("Residual", r.ss_error, r.df_error, r.ms_error, None, None),
        (
            "Total",
            r.ss_treatment + r.ss_subjects + r.ss_error,
            r.n_subjects * r.k - 1,
            None,
            None,
            None,
        ),
    )
    return ResultTable("ANOVA table (sphericity assumed)", columns, rows)


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
