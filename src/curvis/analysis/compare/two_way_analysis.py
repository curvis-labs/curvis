"""The two-way ANOVA analysis: two named factors from the table's category groups,
with interaction, and Tukey's test for each significant main effect.

Layout: the levels of the second factor are the category groups (named by the table's
``category_factor``); the levels of the first factor are the series inside them,
matched across categories by name (case-insensitive). Each cell is one series in one
category, with all values of its replicate columns. Series outside a category are not
used.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from curvis.analysis.base import (
    About,
    Analysis,
    AnalysisError,
    AnalysisInputs,
    Hypothesis,
    InputChoice,
    Params,
    SeriesData,
    param,
    register,
)
from curvis.analysis.compare.anova import Comparison
from curvis.analysis.compare.common import (
    FEW_VALUES,
    GroupComparison,
    group_values,
    name_list,
    number,
    p_text,
    significant,
)
from curvis.analysis.compare.samples import Sample
from curvis.analysis.compare.two_way_anova import (
    Effect,
    TwoWayStats,
    tukey_marginal,
    two_way_anova,
)
from curvis.analysis.result import Cell, CellMeans, Kind, Result, ResultColumn, ResultTable, Value
from curvis.data import Table, name_key, series

METHOD = "Two-way ANOVA"
TUKEY = "Tukey's multiple comparisons test"
SS_TYPE = "Type III sums of squares"
DEFAULT_FACTOR = "Group"


@dataclass(frozen=True)
class TwoWayParams(Params):
    factor_name: str = param(DEFAULT_FACTOR, "Name of the first &factor")


@dataclass(frozen=True)
class Layout:
    """How ticked series form the cells of a two-way design."""

    levels_a: tuple[str, ...]
    """Series names, in order of first appearance."""
    levels_b: tuple[str, ...]
    """Category names, in table order."""
    cells: tuple[tuple[int, ...], ...]
    """cells[i][j]: index of the series of level i in category j."""


def layout(entries: Sequence[tuple[str, str]]) -> Layout | str:
    """The two-way layout of (series name, category name) pairs, or why there is none
    (one short sentence)."""
    levels_a: list[str] = []
    levels_b: list[str] = []
    keys_a: dict[str, int] = {}
    keys_b: dict[str, int] = {}
    found: dict[tuple[int, int], int] = {}
    for index, (name, category) in enumerate(entries):
        ka, kb = name_key(name), name_key(category)
        if ka not in keys_a:
            keys_a[ka] = len(levels_a)
            levels_a.append(name)
        if kb not in keys_b:
            keys_b[kb] = len(levels_b)
            levels_b.append(category)
        cell = (keys_a[ka], keys_b[kb])
        if cell in found:
            return f"{category} has two series named {name}. Rename one of them."
        found[cell] = index
    if len(levels_b) < 2:
        return "Tick series in at least 2 category groups."
    if len(levels_a) < 2:
        return "Tick at least 2 series in each category group."
    for i, a in enumerate(levels_a):
        for j, b in enumerate(levels_b):
            if (i, j) not in found:
                return f"{b} has no series named {a}. Each category needs the same series."
    cells = tuple(tuple(found[(i, j)] for j in range(len(levels_b))) for i in range(len(levels_a)))
    return Layout(tuple(levels_a), tuple(levels_b), cells)


class TwoWayAnova(GroupComparison):
    id = "two_way_anova"
    label = "T&wo-way ANOVA"
    method = METHOD
    section = "Three or more groups"
    about = About(
        "two factors, interaction",
        "Use when the groups differ in two ways at once, such as treatment and sex, and you "
        "want to know if the factors interact.",
        Hypothesis(
            "Three hypotheses: neither factor changes the mean, and the effect of one factor "
            "does not depend on the other (no interaction).",
            "Values are independent and roughly normal in every combination of the factors, "
            "with similar SDs.",
        ),
    )
    params_type = TwoWayParams
    min_groups = 4

    def candidates(self, table: Table) -> list[InputChoice]:
        names = {c.id: c.name for c in table.category_groups}
        out: list[InputChoice] = []
        for s in series(table):
            if s.category_id is None:
                continue
            n = sum(int(np.count_nonzero(~np.isnan(table.column(c).values))) for c in s.column_ids)
            label = f"{s.name} ({names[s.category_id]})"
            out.append(InputChoice(s.group_id or s.column_ids[0], label, n, s.column_ids))
        return out

    def _entries(self, table: Table, inputs: Sequence[str]) -> list[tuple[str, str]]:
        names = {c.id: c.name for c in table.category_groups}
        wanted = set(inputs)
        return [
            (s.name, names[s.category_id])
            for s in series(table)
            if s.category_id is not None and (s.group_id or s.column_ids[0]) in wanted
        ]

    def problem(self, table: Table) -> str | None:
        found = layout(self._entries(table, [c.id for c in self.candidates(table)]))
        if isinstance(found, str):
            return (
                "Needs at least 2 category groups that each hold the same 2 or more series "
                "(use Set category)."
            )
        return None

    def check(self, table: Table, inputs: Sequence[str], params: Params) -> str | None:
        found = layout(self._entries(table, inputs))
        return found if isinstance(found, str) else None

    def describe(self, table: Table, inputs: Sequence[str], params: Params) -> str:
        assert isinstance(params, TwoWayParams)
        found = layout(self._entries(table, inputs))
        factor_a = _factor_name(params)
        if isinstance(found, str):
            return f"{METHOD} of {factor_a} and {table.category_factor}."
        return (
            f"{METHOD} ({SS_TYPE}) of {factor_a} ({name_list(found.levels_a)}) and "
            f"{table.category_factor} ({name_list(found.levels_b)}) with their interaction, "
            f"followed by {TUKEY} for each significant factor."
        )

    def gather(self, table: Table, analysis: Analysis) -> AnalysisInputs:
        data = super().gather(table, analysis)
        names = {c.id: c.name for c in table.category_groups}
        # The series' own name (the dialog lists it with its category) and its category.
        own = {
            s.group_id or s.column_ids[0]: (s.name, names.get(s.category_id or "", ""))
            for s in series(table)
        }
        tagged = []
        for s in data.series:
            name, category = own[s.id]
            tagged.append(SeriesData(s.id, name, s.columns, category))
        return AnalysisInputs(
            data.table_name, data.n_rows, tuple(tagged), data.x, data.x_name, table.category_factor
        )

    @staticmethod
    def run(inputs: AnalysisInputs, params: TwoWayParams) -> Result:
        entries = [(s.name, s.category) for s in inputs.series]
        if any(not c for _n, c in entries):
            raise AnalysisError("Every ticked series must be in a category group.")
        found = layout(entries)
        if isinstance(found, str):
            raise AnalysisError(found)
        factor_a = _factor_name(params)
        factor_b = inputs.category_factor or "Category"
        cells = [[group_values(inputs.series[index]) for index in row] for row in found.cells]
        for i, row in enumerate(cells):
            for j, cell in enumerate(row):
                if not (cell == cell).any():
                    raise AnalysisError(
                        f"{found.levels_a[i]} in {found.levels_b[j]} has no values. "
                        "Enter data or untick it."
                    )
        n_cells = len(found.levels_a) * len(found.levels_b)
        if sum(int(np.count_nonzero(v == v)) for row in cells for v in row) <= n_cells:
            raise AnalysisError(
                "Each cell has only one value, so there is no variation within cells to "
                "test against. Add replicates."
            )
        stats = two_way_anova(cells)
        if not stats.defined:
            raise AnalysisError(
                "The values do not vary within any cell, so the ANOVA cannot be computed."
            )
        interaction = f"{factor_a} × {factor_b}"
        effects = ((factor_a, stats.a), (factor_b, stats.b), (interaction, stats.interaction))
        posthoc: list[ResultTable] = []
        for factor, effect, key, levels in (
            (factor_a, stats.a, "a", found.levels_a),
            (factor_b, stats.b, "b", found.levels_b),
        ):
            if significant(effect.p_value):
                posthoc.append(_tukey_table(factor, tukey_marginal(stats, key), levels))
        summary = _summary(effects, stats.df_residual, len(posthoc))
        warnings = _warnings(stats, found, factor_a, factor_b, interaction)
        values = (
            Value(f"P value, {factor_a}", stats.a.p_value, Kind.P_VALUE),
            Value(f"P value, {factor_b}", stats.b.p_value, Kind.P_VALUE),
            Value(f"P value, {interaction}", stats.interaction.p_value, Kind.P_VALUE),
            Value("DF residual", stats.df_residual, Kind.INTEGER),
        )
        tables = (
            _anova_table(effects, stats),
            _cells_table(stats, found, factor_a, factor_b),
            *posthoc,
        )
        return Result(
            f"{METHOD} ({SS_TYPE})",
            summary,
            tuple(warnings),
            values,
            tables,
            cells=_cell_means(stats, found, factor_a, factor_b),
        )


def _factor_name(params: TwoWayParams) -> str:
    return params.factor_name.strip() or DEFAULT_FACTOR


def _effect_text(effect: Effect, df_residual: int) -> str:
    return f"F({effect.df}, {df_residual}) = {number(effect.f)}, {p_text(effect.p_value)}"


def _verdict(effect: Effect) -> str:
    return "is significant" if significant(effect.p_value) else "is not significant"


def _summary(effects: Sequence[tuple[str, Effect]], df_residual: int, posthoc: int) -> str:
    """ "Group is significant (…); Sex is not significant (…); the interaction is not
    significant (…)." plus a note on the Tukey tests shown."""
    (a_name, a), (b_name, b), (_ab_name, ab) = effects
    text = (
        f"{a_name} {_verdict(a)} ({_effect_text(a, df_residual)}); "
        f"{b_name} {_verdict(b)} ({_effect_text(b, df_residual)}); "
        f"the interaction {_verdict(ab)} ({_effect_text(ab, df_residual)})."
    )
    if posthoc:
        text += f" {TUKEY} compares the levels of each significant factor."
    return text


def _warnings(
    stats: TwoWayStats, found: Layout, factor_a: str, factor_b: str, interaction: str
) -> list[str]:
    out: list[str] = []
    if significant(stats.interaction.p_value) and (
        significant(stats.a.p_value) or significant(stats.b.p_value)
    ):
        out.append(
            f"The interaction is significant: the effect of {factor_a} depends on "
            f"{factor_b}. Read the main effects and their comparisons with care, and look "
            "at the interaction plot."
        )
    for i, row in enumerate(stats.cells):
        for j, s in enumerate(row):
            name = f"{found.levels_a[i]} in {found.levels_b[j]}"
            if s.n < FEW_VALUES:
                values = "value" if s.n == 1 else "values"
                out.append(
                    f"{name} has only {s.n} {values}. With fewer than {FEW_VALUES} values per "
                    "cell the test has little power and its assumptions cannot be checked."
                )
            elif s.ss == 0:
                out.append(
                    f"All values in {name} are the same (SD = 0). Check that the data are "
                    "entered correctly; the test assumes the values vary."
                )
    return out


def _anova_table(effects: Sequence[tuple[str, Effect]], stats: TwoWayStats) -> ResultTable:
    columns = (
        ResultColumn("Source", Kind.TEXT),
        ResultColumn("SS"),
        ResultColumn("DF", Kind.INTEGER),
        ResultColumn("MS"),
        ResultColumn("F"),
        ResultColumn("P value", Kind.P_VALUE),
    )
    rows: list[tuple[Cell, ...]] = [(name, e.ss, e.df, e.ms, e.f, e.p_value) for name, e in effects]
    rows.append(("Residual", stats.ss_residual, stats.df_residual, stats.ms_residual, None, None))
    return ResultTable(f"ANOVA table ({SS_TYPE})", columns, tuple(rows))


def _sem(s: Sample) -> float:
    return s.sd / math.sqrt(s.n) if s.n >= 2 else math.nan


def _cells_table(stats: TwoWayStats, found: Layout, factor_a: str, factor_b: str) -> ResultTable:
    columns = (
        ResultColumn(factor_a, Kind.TEXT),
        ResultColumn(factor_b, Kind.TEXT),
        ResultColumn("n", Kind.INTEGER),
        ResultColumn("Mean"),
        ResultColumn("SD"),
    )
    rows = tuple(
        (found.levels_a[i], found.levels_b[j], s.n, s.mean, s.sd)
        for i, row in enumerate(stats.cells)
        for j, s in enumerate(row)
    )
    return ResultTable("Cells", columns, rows)


def _tukey_table(
    factor: str, comparisons: Sequence[Comparison], levels: Sequence[str]
) -> ResultTable:
    columns = (
        ResultColumn("Comparison", Kind.TEXT),
        ResultColumn("Mean difference"),
        ResultColumn("Lower 95% CI"),
        ResultColumn("Upper 95% CI"),
        ResultColumn("q"),
        ResultColumn("Adjusted P value", Kind.P_VALUE),
    )
    rows = tuple(
        (
            f"{levels[c.j]} − {levels[c.i]}",
            c.difference,
            c.ci_lower,
            c.ci_upper,
            c.statistic,
            c.p_value,
        )
        for c in comparisons
    )
    return ResultTable(f"{TUKEY}: {factor}", columns, rows)


def _cell_means(stats: TwoWayStats, found: Layout, factor_a: str, factor_b: str) -> CellMeans:
    return CellMeans(
        factor_a,
        factor_b,
        found.levels_a,
        found.levels_b,
        tuple(tuple(s.n for s in row) for row in stats.cells),
        tuple(tuple(s.mean for s in row) for row in stats.cells),
        tuple(tuple(s.sd for s in row) for row in stats.cells),
        tuple(tuple(_sem(s) for s in row) for row in stats.cells),
    )


register(TwoWayAnova())
