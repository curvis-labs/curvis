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

AWAITING_FIXTURES: frozenset[str] = frozenset()
"""Adapters whose R script is written and reviewed but whose fixtures have not been
generated with R 4.6.1 yet. Remove a name once its fixtures are committed."""


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


def _rm(inputs: Mapping[str, Any]) -> Any:
    from curvis.analysis.compare.rm_anova import rm_anova

    return rm_anova(inputs["treatments"])


@adapter("rm_anova")
def rm_anova(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/rm_anova.R: the ANOVA table and Greenhouse-Geisser."""
    r = _rm(inputs)
    return {
        "n_subjects": r.n_subjects,
        "n_incomplete": r.n_incomplete,
        "mean": r.means,
        "df_treatment": r.df_treatment,
        "df_subjects": r.df_subjects,
        "df_error": r.df_error,
        "ss_treatment": r.ss_treatment,
        "ss_subjects": r.ss_subjects,
        "ss_error": r.ss_error,
        "ms_treatment": r.ms_treatment,
        "ms_subjects": r.ms_subjects,
        "ms_error": r.ms_error,
        "f": r.f,
        "p_value": r.p_value,
        "gg_epsilon": r.gg_epsilon,
        "gg_df_treatment": r.gg_df_treatment,
        "gg_df_error": r.gg_df_error,
        "gg_p_value": r.gg_p_value,
    }


def _comparisons(rows: Any, *, pair: bool = True) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    if pair:
        out["group_i"] = [c.i for c in rows]
        out["group_j"] = [c.j for c in rows]
    else:
        out["group"] = [c.j for c in rows]
    out["difference"] = [c.difference for c in rows]
    out["se"] = [c.se for c in rows]
    return out


@adapter("rm_tukey")
def rm_tukey(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/rm_anova.R: Tukey on the pooled RM error."""
    from curvis.analysis.compare.anova import tukey

    rows = tukey(_rm(inputs).pooled)
    return {
        **_comparisons(rows),
        "q": [c.statistic for c in rows],
        "p_value": [c.p_value for c in rows],
        "ci_lower": [c.ci_lower for c in rows],
        "ci_upper": [c.ci_upper for c in rows],
    }


@adapter("rm_dunnett")
def rm_dunnett(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/rm_anova.R: Dunnett against the control, pooled RM error."""
    from curvis.analysis.compare.anova import dunnett as run

    rows = run(_rm(inputs).pooled, int(inputs["control"]))
    return {
        **_comparisons(rows, pair=False),
        "t": [c.statistic for c in rows],
        "p_value": [c.p_value for c in rows],
        "ci_lower": [c.ci_lower for c in rows],
        "ci_upper": [c.ci_upper for c in rows],
    }


@adapter("rm_holm")
def rm_holm(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/rm_anova.R: pairwise t tests on the RM error, Holm-corrected."""
    from curvis.analysis.compare.anova import holm_pairwise as run

    r = _rm(inputs)
    rows = run(r.pooled)
    return {
        **_comparisons(rows),
        "t": [c.statistic for c in rows],
        "df": r.df_error,
        "p_unadjusted": [c.p_unadjusted for c in rows],
        "p_value": [c.p_value for c in rows],
    }


def _two_way(inputs: Mapping[str, Any]) -> Any:
    from curvis.analysis.compare.two_way_anova import two_way_anova

    return two_way_anova(inputs["cells"])


@adapter("two_way_anova")
def two_way_anova(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/two_way_anova.R: the Type III ANOVA table."""
    r = _two_way(inputs)
    out: dict[str, npt.ArrayLike] = {
        "n": r.n.ravel(),
        "cell_mean": r.cell_means.ravel(),
        "marginal_mean_a": r.marginal_means_a,
        "marginal_mean_b": r.marginal_means_b,
        "df_residual": r.df_residual,
        "ss_residual": r.ss_residual,
        "ms_residual": r.ms_residual,
    }
    for name, effect in (("a", r.a), ("b", r.b), ("interaction", r.interaction)):
        out[f"df_{name}"] = effect.df
        out[f"ss_{name}"] = effect.ss
        out[f"ms_{name}"] = effect.ms
        out[f"f_{name}"] = effect.f
        out[f"p_{name}"] = effect.p_value
    return out


def _two_way_tukey(inputs: Mapping[str, Any], factor: str) -> Mapping[str, npt.ArrayLike]:
    from curvis.analysis.compare.two_way_anova import tukey_marginal

    rows = tukey_marginal(_two_way(inputs), factor)
    return {
        **_comparisons(rows),
        "q": [c.statistic for c in rows],
        "p_value": [c.p_value for c in rows],
        "ci_lower": [c.ci_lower for c in rows],
        "ci_upper": [c.ci_upper for c in rows],
    }


@adapter("two_way_tukey_a")
def two_way_tukey_a(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/two_way_anova.R: Tukey on the marginal means of factor A."""
    return _two_way_tukey(inputs, "a")


@adapter("two_way_tukey_b")
def two_way_tukey_b(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/two_way_anova.R: Tukey on the marginal means of factor B."""
    return _two_way_tukey(inputs, "b")


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


def _kruskal(inputs: Mapping[str, Any]) -> Any:
    from curvis.analysis.compare.kruskal import kruskal_wallis

    return kruskal_wallis(inputs["groups"])


@adapter("kruskal_wallis")
def kruskal_wallis(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/kruskal_wallis.R: the Kruskal-Wallis test."""
    r = _kruskal(inputs)
    return {
        "n": [s.n for s in r.samples],
        "median": [s.median for s in r.samples],
        "mean_rank": r.mean_ranks,
        "h": r.h,
        "df": r.df,
        "p_value": r.p_value,
    }


@adapter("dunn_all_pairs")
def dunn_all_pairs(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/kruskal_wallis.R: Dunn's test of all pairs, Holm-adjusted."""
    from curvis.analysis.compare.kruskal import dunn

    rows = dunn(_kruskal(inputs))
    return {
        "group_i": [c.i for c in rows],
        "group_j": [c.j for c in rows],
        "mean_rank_diff": [c.mean_rank_diff for c in rows],
        "z": [c.z for c in rows],
        "p_unadjusted": [c.p_unadjusted for c in rows],
        "p_value": [c.p_value for c in rows],
    }


@adapter("dunn_control")
def dunn_control(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/kruskal_wallis.R: Dunn's test against the control, Holm over
    the k - 1 comparisons."""
    from curvis.analysis.compare.kruskal import dunn

    rows = dunn(_kruskal(inputs), int(inputs["control"]))
    return {
        "group": [c.j for c in rows],
        "mean_rank_diff": [c.mean_rank_diff for c in rows],
        "z": [c.z for c in rows],
        "p_unadjusted": [c.p_unadjusted for c in rows],
        "p_value": [c.p_value for c in rows],
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


def _curve_spec(key: str) -> Any:
    from curvis.analysis.fit.kinetics import EXP_ASSOCIATION, EXP_DECAY, MICHAELIS_MENTEN
    from curvis.analysis.fit.multiphase import BELL_SHAPED, BIPHASIC
    from curvis.analysis.fit.shape import GAUSSIAN

    specs = (BIPHASIC, BELL_SHAPED, EXP_ASSOCIATION, EXP_DECAY, MICHAELIS_MENTEN, GAUSSIAN)
    return next(s for s in specs if s.key == key)


def _nonlinear_curve(key: str, inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/<key>.R with validation/r/R/nonlinear.R: the free
    parameters with SE and CI, the derived values that use only free parameters, R²,
    Sy.x and the band. A fit that is not OK reports no numbers, as R does."""
    import math

    import numpy as np

    from curvis.analysis.fit.nonlinear import curve_points, derived_value, fit_curve, uses_fixed

    spec = _curve_spec(key)
    fixed = {k: float(v) for k, v in inputs["fixed"].items()}
    x_is_log = bool(inputs.get("x_is_log", False))
    points = curve_points(spec, inputs["x"], inputs["y"], x_is_log, _x_range(inputs))
    f = fit_curve(spec, points, fixed)
    nan = math.nan
    ok = f.ok
    out: dict[str, npt.ArrayLike] = {"fit_ok": [ok], "n": [f.n], "df": [f.df if ok else nan]}
    for i, name in enumerate(f.names):
        if name in fixed:
            continue
        out[name] = [float(f.values[i]) if ok else nan]
        out[f"{name}_se"] = [float(f.se[i]) if ok else nan]
        out[f"{name}_ci_lower"] = [float(f.ci_lower[i]) if ok else nan]
        out[f"{name}_ci_upper"] = [float(f.ci_upper[i]) if ok else nan]
    for d in spec.derived:
        if uses_fixed(d, fixed):
            continue
        v = derived_value(f, d) if ok else None
        out[d.name] = [v.value if v else nan]
        if d.linear is not None:
            out[f"{d.name}_se"] = [v.se if v else nan]
        out[f"{d.name}_ci_lower"] = [v.ci_lower if v else nan]
        out[f"{d.name}_ci_upper"] = [v.ci_upper if v else nan]
    out["r_squared"] = [f.r_squared if ok else nan]
    out["sy_x"] = [f.sy_x if ok else nan]
    at = np.asarray(inputs["at"], dtype=np.float64)
    if spec.log_x and not x_is_log:
        from curvis.analysis.fit.dose_response import log_x

        at = log_x(at, False)
    fit, lower, upper = f.band(at)
    if not ok:
        fit = lower = upper = np.full(len(at), nan)
    out["band_fit"], out["band_lower"], out["band_upper"] = fit, lower, upper
    return out


for _key in ("biphasic", "bell_shaped", "exp_association", "exp_decay", "michaelis_menten",
             "gaussian"):  # fmt: skip
    adapter(_key)(lambda inputs, key=_key: _nonlinear_curve(key, inputs))


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


@adapter("box_stats")
def box_stats(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/box_stats.R: five-number summary of each group (box plots)."""
    from curvis.charts.data import box_stats as five_numbers

    found = [five_numbers(group) for group in inputs["groups"]]
    return {
        "n": [b.n for b in found],
        "min": [b.min for b in found],
        "q1": [b.q1 for b in found],
        "median": [b.median for b in found],
        "q3": [b.q3 for b in found],
        "max": [b.max for b in found],
    }


@adapter("kaplan_meier")
def kaplan_meier(inputs: Mapping[str, Any]) -> Mapping[str, npt.ArrayLike]:
    """validation/r/scripts/kaplan_meier.R: the Kaplan-Meier estimate (log-log CI)."""
    from curvis.analysis.survival.km import kaplan_meier as km

    r = km(inputs["time"], inputs["event"])
    return {
        "n": r.n,
        "n_events": r.n_events,
        "time": r.time,
        "n_risk": r.n_risk,
        "n_event": r.n_event,
        "n_censor": r.n_censor,
        "survival": r.survival,
        "std_err": r.std_err,
        "lower": r.lower,
        "upper": r.upper,
        "median": r.median,
        "median_lower": r.median_lower,
        "median_upper": r.median_upper,
        "risk_n": r.at_risk(inputs["risk_times"]),
    }
