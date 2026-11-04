"""Adapters: run the Curvis function that a fixture's ``analysis`` names.

An adapter takes the fixture's decoded inputs and returns a mapping with the same
output names as the fixture's ``expected``. Register one per R fixture script.
"""

from collections.abc import Callable, Mapping
from typing import Any

import numpy.typing as npt

from curvis.data import mean_ci, row_stats

type Adapter = Callable[[Mapping[str, Any]], Mapping[str, npt.ArrayLike]]

ADAPTERS: dict[str, Adapter] = {}


def adapter(analysis: str) -> Callable[[Adapter], Adapter]:
    def register(function: Adapter) -> Adapter:
        if analysis in ADAPTERS:
            raise ValueError(f"two adapters for {analysis!r}")
        ADAPTERS[analysis] = function
        return function

    return register


@adapter("descriptive_stats")
def descriptive_stats(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/descriptive_stats.R: per-row stats of replicate columns."""
    stats = row_stats(inputs["replicates"])
    low, high = mean_ci(stats)
    return {
        "n": stats.n,
        "mean": stats.mean,
        "sd": stats.sd,
        "sem": stats.sem,
        "ci_lower": low,
        "ci_upper": high,
    }


def run_descriptive_analysis(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """The Descriptive statistics analysis (curvis.analysis.descriptive) on a
    descriptive_stats fixture: one series whose replicates are the fixture's columns,
    every row kept. Reads the numbers back from the result table."""
    import numpy as np

    from curvis.analysis.base import AnalysisInputs, SeriesData
    from curvis.analysis.descriptive import DescriptiveParams, DescriptiveStatistics

    columns = tuple(np.asarray(c, dtype=np.float64) for c in inputs["replicates"])
    data = AnalysisInputs("T", len(columns[0]), (SeriesData("s", "S", columns),))
    params = DescriptiveParams(confidence_interval=True, skip_empty_rows=False)
    try:
        result = DescriptiveStatistics.run(data, params)
    except Exception as exc:  # e.g. no values at all: compare like row_stats does
        from curvis.analysis.base import AnalysisError

        if not isinstance(exc, AnalysisError):
            raise
        return descriptive_stats(inputs)
    (table,) = result.tables
    by_name = {c.label: i for i, c in enumerate(table.columns)}

    def column(label: str) -> npt.NDArray[np.float64]:
        return np.array([row[by_name[label]] for row in table.rows], dtype=np.float64)

    return {
        "n": column("n"),
        "mean": column("Mean"),
        "sd": column("SD"),
        "sem": column("SEM"),
        "ci_lower": column("Lower 95% CI"),
        "ci_upper": column("Upper 95% CI"),
    }


@adapter("t_test")
def t_test(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/t_test.R: unpaired (Student, Welch) and paired t tests."""
    from curvis.analysis.compare.ttest import TTestKind
    from curvis.analysis.compare.ttest import t_test as run

    if inputs["paired"]:
        kind = TTestKind.PAIRED
    else:
        kind = TTestKind.WELCH if inputs["welch"] else TTestKind.STUDENT
    r = run(inputs["a"], inputs["b"], kind)
    counts = {"n_pairs": r.n_pairs} if inputs["paired"] else {"n_a": r.a.n, "n_b": r.b.n}
    values = {
        **counts,
        "mean_a": r.a.mean,
        "mean_b": r.b.mean,
        "difference": r.difference,
        "se": r.se,
        "t": r.t,
        "df": r.df,
        "p_value": r.p_value,
        "ci_lower": r.ci_lower,
        "ci_upper": r.ci_upper,
    }
    # t_test.R writes every output as a one-element array.
    return {name: [value] for name, value in values.items()}


def _anova(inputs: Mapping[str, Any]) -> Any:
    from curvis.analysis.compare.anova import one_way_anova

    return one_way_anova(inputs["groups"])


@adapter("one_way_anova")
def one_way_anova(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/one_way_anova.R: the ANOVA table."""
    a = _anova(inputs)
    return {
        "n": [s.n for s in a.samples],
        "mean": a.means,
        "df_between": a.df_between,
        "df_within": a.df_within,
        "ss_between": a.ss_between,
        "ss_within": a.ss_within,
        "ms_between": a.ms_between,
        "ms_within": a.ms_within,
        "f": a.f,
        "p_value": a.p_value,
    }


@adapter("tukey_hsd")
def tukey_hsd(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/one_way_anova.R: Tukey's HSD."""
    from curvis.analysis.compare.anova import tukey

    rows = tukey(_anova(inputs))
    return {
        "group_i": [c.i for c in rows],
        "group_j": [c.j for c in rows],
        "difference": [c.difference for c in rows],
        "q": [c.statistic for c in rows],
        "p_value": [c.p_value for c in rows],
        "ci_lower": [c.ci_lower for c in rows],
        "ci_upper": [c.ci_upper for c in rows],
    }


@adapter("dunnett")
def dunnett(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/one_way_anova.R: Dunnett's test against the control."""
    from curvis.analysis.compare.anova import dunnett as run

    rows = run(_anova(inputs), int(inputs["control"]))
    return {
        "group": [c.j for c in rows],
        "difference": [c.difference for c in rows],
        "se": [c.se for c in rows],
        "t": [c.statistic for c in rows],
        "p_value": [c.p_value for c in rows],
        "ci_lower": [c.ci_lower for c in rows],
        "ci_upper": [c.ci_upper for c in rows],
    }


@adapter("holm_pairwise")
def holm_pairwise(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/one_way_anova.R: pairwise t tests, Holm-corrected."""
    from curvis.analysis.compare.anova import holm_pairwise as run

    a = _anova(inputs)
    rows = run(a)
    return {
        "group_i": [c.i for c in rows],
        "group_j": [c.j for c in rows],
        "difference": [c.difference for c in rows],
        "se": [c.se for c in rows],
        "t": [c.statistic for c in rows],
        "df": a.df_within,
        "p_unadjusted": [c.p_unadjusted for c in rows],
        "p_value": [c.p_value for c in rows],
    }


@adapter("mann_whitney")
def mann_whitney(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/mann_whitney.R: Mann-Whitney U test."""
    from curvis.analysis.compare.mann_whitney import mann_whitney as run

    r = run(inputs["a"], inputs["b"])
    return {
        "n_a": r.a.n,
        "n_b": r.b.n,
        "u": r.u,
        "p_value": r.p_value,
        "exact": int(r.exact),
        "median_a": r.a.median,
        "median_b": r.b.median,
    }
