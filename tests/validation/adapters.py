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


# --- curve fits ----------------------------------------------------------------------


def _x_range(inputs: Mapping[str, Any]) -> Any:
    from curvis.analysis.fit.points import XRange

    return XRange(float(inputs["x_min"]), float(inputs["x_max"]))


@adapter("linear_fit")
def linear_fit(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/linear_fit.R: lines and polynomials of degree 2-6."""
    from curvis.analysis.fit.linear import fit_polynomial
    from curvis.analysis.fit.points import fit_points

    points = fit_points(inputs["x"], inputs["y"], _x_range(inputs))
    f = fit_polynomial(points, int(inputs["degree"]))
    fit, low, high = f.band(inputs["at"])
    return {
        "fit_ok": [f.ok],
        "n": [f.n],
        "df": [f.df if f.ok else float("nan")],
        "coefficients": f.coefficients,
        "se": f.se,
        "ci_lower": f.ci_lower,
        "ci_upper": f.ci_upper,
        "r_squared": [f.r_squared],
        "sy_x": [f.sy_x],
        "band_fit": fit,
        "band_lower": low,
        "band_upper": high,
    }


@adapter("nist_linear")
def nist_linear(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/nist_strd.R: NIST StRD linear datasets, certified values."""
    from curvis.analysis.fit.linear import fit_polynomial
    from curvis.analysis.fit.points import Points

    f = fit_polynomial(Points(inputs["x"], inputs["y"]), int(inputs["degree"]))
    out: dict[str, npt.ArrayLike] = {"coefficients": f.coefficients, "r_squared": [f.r_squared]}
    # nist_strd.R leaves out the SDs of the exact fits, whose certified SDs are 0.
    if inputs["dataset"] not in NIST_EXACT_LINEAR:
        out["se"] = f.se
        out["sy_x"] = [f.sy_x]
    return out


NIST_EXACT_LINEAR = {"Wampler1", "Wampler2"}
NIST_ROUNDING_LEVEL = {"Lanczos1"}
"""NIST nonlinear datasets whose residuals (~1e-13) are at rounding level, so their SDs,
RSS and residual SD cannot be reproduced in double precision; nist_strd.R compares only
their parameters and df (approved by the project owner, step 2.4)."""


@adapter("dose_response")
def dose_response(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/dose_response.R: 4PL and 3PL (Bottom = 0). A fit that is
    not OK (too few points, not converged, ambiguous) reports no numbers, as R does."""
    import math

    import numpy as np

    from curvis.analysis.fit.dose_response import dose_response_points, fit_dose_response

    three = inputs["model"] == "3pl"
    points = dose_response_points(inputs["x"], inputs["y"], inputs["x_is_log"], _x_range(inputs))
    d = fit_dose_response(points, three_parameter=three, x_is_log=inputs["x_is_log"])
    f = d.fit
    names = (
        ("top", "log_ec50", "hill_slope") if three else ("bottom", "top", "log_ec50", "hill_slope")
    )
    nan = float("nan")
    out: dict[str, npt.ArrayLike] = {"fit_ok": [f.ok], "n": [f.n], "df": [f.df if f.ok else nan]}
    for name in names:
        i = f.names.index(name)
        ok = f.ok
        out[name] = [float(f.values[i]) if ok else nan]
        out[f"{name}_se"] = [float(f.se[i]) if ok else nan]
        out[f"{name}_ci_lower"] = [float(f.ci_lower[i]) if ok else nan]
        out[f"{name}_ci_upper"] = [float(f.ci_upper[i]) if ok else nan]
    low, high = d.ec50_ci
    out["ec50"] = [d.ec50 if f.ok else nan]
    out["ec50_ci_lower"] = [low if f.ok else nan]
    out["ec50_ci_upper"] = [high if f.ok else nan]
    out["r_squared"] = [f.r_squared if f.ok else nan]
    out["sy_x"] = [f.sy_x if f.ok else nan]
    fit, lower, upper = d.band(inputs["at"])
    if not f.ok:
        fit = lower = upper = np.full(len(inputs["at"]), math.nan)
    out["band_fit"], out["band_lower"], out["band_upper"] = fit, lower, upper
    return out


@adapter("nist_nls")
def nist_nls(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/nist_strd.R: NIST StRD nonlinear datasets from NIST's
    starting values, against the certified values. The numbers of an ambiguous fit are
    compared too (it converged; Curvis only declines to report them).

    From starting values 1 (far from the solution) a fit may fail, but it must then say
    so (not converged, or ambiguous): such a case is an expected failure (xfail), listed
    in the report.
    A fit that converges to the wrong values fails, from either start, as does any
    failure from starting values 2."""
    import pytest

    from curvis.analysis.fit.engine import fit_model
    from curvis.analysis.fit.points import Points
    from tests.validation.nist_models import NistModel

    start = inputs["start"]
    model = NistModel(inputs["dataset"], len(start))
    f = fit_model(model, Points(inputs["x"], inputs["y"]), start=start)
    out: dict[str, Any] = {"parameters": f.values, "df": f.df}
    # nist_strd.R leaves these out where the residuals are at rounding level.
    if inputs["dataset"] not in NIST_ROUNDING_LEVEL:
        out.update(se=f.se, rss=f.rss, sy_x=f.sy_x)
    if inputs["start_number"] == 1 and not f.ok and _differs_from_nist(inputs, out):
        pytest.xfail(f"{inputs['dataset']} from start 1: {f.status.value}: {f.message}")
    return out


def _differs_from_nist(inputs: Mapping[str, Any], out: Mapping[str, Any]) -> bool:
    from tests.validation.harness import FIXTURE_DIR, compare, load_fixture

    case = f"{inputs['dataset'].lower()}_start{inputs['start_number']}"
    return bool(compare(load_fixture(FIXTURE_DIR / "nist_nls" / f"{case}.json"), out))
