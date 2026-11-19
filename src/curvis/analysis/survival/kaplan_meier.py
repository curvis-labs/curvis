"""The Kaplan-Meier analysis: a survival curve for each ticked Time + Event pair, with
95% confidence intervals, the median survival and numbers at risk."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np

from curvis.analysis.base import (
    Analysis,
    AnalysisError,
    AnalysisInputs,
    Family,
    InputChoice,
    Params,
    register,
)
from curvis.analysis.base import AnalysisType as _AnalysisType
from curvis.analysis.result import Cell, Kind, Result, ResultColumn, ResultTable, shown
from curvis.analysis.survival.km import CI_METHOD, METHOD, KaplanMeier, kaplan_meier
from curvis.data import Table, survival_pairs

NEEDS_PAIR = "Needs a Time + Event pair."
FEW_EVENTS = 5
"""Fewer events than this get a warning: the curve and its CI rest on very few drops."""
RISK_STEPS = 5
"""About this many steps between 0 and the longest time in the numbers-at-risk table."""


@dataclass(frozen=True)
class KaplanMeierParams(Params):
    """Kaplan-Meier has no options: the 95% log-log interval is fixed (step 3.4)."""


class KaplanMeierAnalysis(_AnalysisType):
    id = "kaplan_meier"
    label = "&Kaplan-Meier survival"
    method = METHOD
    family = Family.SURVIVAL
    params_type = KaplanMeierParams

    def candidates(self, table: Table) -> list[InputChoice]:
        choices: list[InputChoice] = []
        for pair in survival_pairs(table):
            time = table.column(pair.time_id).values
            event = table.column(pair.event_id).values
            n = int(np.count_nonzero(~np.isnan(time) & ~np.isnan(event)))
            choices.append(InputChoice(pair.source, pair.name, n, (pair.time_id, pair.event_id)))
        return choices

    def problem(self, table: Table) -> str | None:
        return None if self.candidates(table) else NEEDS_PAIR

    def check(self, table: Table, inputs: Sequence[str], params: Params) -> str | None:
        if not inputs:
            return "Tick at least one Time + Event pair."
        return None

    def describe(self, table: Table, inputs: Sequence[str], params: Params) -> str:
        chosen = self.selected(table, inputs)
        names = ", ".join(c.name for c in chosen)
        curves = "a survival curve" if len(chosen) == 1 else "survival curves"
        return (
            f"{METHOD}: {curves} of {names}, with 95% confidence intervals ({CI_METHOD}) "
            "and the median survival."
        )

    def gather(self, table: Table, analysis: Analysis) -> AnalysisInputs:
        inputs = super().gather(table, analysis)
        units = [table.column(c.column_ids[0]).unit for c in self.selected(table, analysis.inputs)]
        return replace(inputs, time_unit=next((u for u in units if u), ""))

    @staticmethod
    def run(inputs: AnalysisInputs, params: KaplanMeierParams) -> Result:
        if not inputs.series:
            raise AnalysisError("Tick at least one Time + Event pair, then run it again.")
        unit = inputs.time_unit
        names = [s.name for s in inputs.series]
        estimates: list[KaplanMeier] = []
        for s in inputs.series:
            time, event = s.columns
            try:
                estimates.append(kaplan_meier(time, event))
            except ValueError as exc:
                raise AnalysisError(f"{s.name}: {exc}") from None
        tables = [_summary_table(names, estimates, unit)]
        tables += [_estimate_table(n, km, unit) for n, km in zip(names, estimates, strict=True)]
        tables.append(_risk_table(names, estimates, unit))
        warnings: list[str] = []
        for name, km in zip(names, estimates, strict=True):
            if km.n_events == 0:
                warnings.append(f"{name} has no events, so its curve stays at 1.")
            elif km.n_events < FEW_EVENTS:
                warnings.append(
                    f"{name} has only {km.n_events} event{'s' if km.n_events > 1 else ''}; "
                    "its curve and confidence interval are rough."
                )
        notes = (
            f"95% confidence intervals use the {CI_METHOD} transform of Greenwood's variance.",
            "The median survival is the first time the curve is at or below 0.5. "
            "“Not reached” means the curve (or a limit of its interval) "
            "stays above 0.5.",
            "Subjects with a missing time or event are left out.",
        )
        return Result(
            METHOD,
            _summary(names, estimates, unit),
            tuple(warnings),
            (),
            tuple(tables),
            notes=notes,
        )


# --- results ---------------------------------------------------------------------------

NOT_REACHED = "Not reached"


def _time_cell(value: float) -> Cell:
    return NOT_REACHED if math.isnan(value) else value


def _with_unit(value: float, unit: str) -> str:
    if math.isnan(value):
        return "not reached"
    text = shown(value, Kind.LABEL)
    return f"{text} {unit}" if unit else text


def _summary(names: Sequence[str], estimates: Sequence[KaplanMeier], unit: str) -> str:
    if len(estimates) == 1:
        km = estimates[0]
        ci = f"95% CI {_with_unit(km.median_lower, unit)} to {_with_unit(km.median_upper, unit)}"
        events = f"{km.n_events} of {km.n} subjects had an event"
        if math.isnan(km.median):
            return f"Median survival is not reached ({events})."
        return f"Median survival is {_with_unit(km.median, unit)} ({ci}); {events}."
    parts = [f"{n} {_with_unit(km.median, unit)}" for n, km in zip(names, estimates, strict=True)]
    return f"Median survival: {', '.join(parts)}."


def _summary_table(
    names: Sequence[str], estimates: Sequence[KaplanMeier], unit: str
) -> ResultTable:
    columns = (
        ResultColumn("Curve", Kind.TEXT),
        ResultColumn("n", Kind.INTEGER),
        ResultColumn("Events", Kind.INTEGER),
        ResultColumn("Censored", Kind.INTEGER),
        ResultColumn("Median survival", Kind.LABEL, unit),
        ResultColumn("95% CI of median, lower", Kind.LABEL, unit),
        ResultColumn("95% CI of median, upper", Kind.LABEL, unit),
    )
    rows = tuple(
        (
            name,
            km.n,
            km.n_events,
            km.n_censored,
            _time_cell(km.median),
            _time_cell(km.median_lower),
            _time_cell(km.median_upper),
        )
        for name, km in zip(names, estimates, strict=True)
    )
    return ResultTable("Median survival", columns, rows)


def _estimate_table(name: str, km: KaplanMeier, unit: str) -> ResultTable:
    """Survival at each event time."""
    columns = (
        ResultColumn("Time", Kind.LABEL, unit),
        ResultColumn("At risk", Kind.INTEGER),
        ResultColumn("Events", Kind.INTEGER),
        ResultColumn("Censored", Kind.INTEGER),
        ResultColumn("Survival"),
        ResultColumn("SE"),
        ResultColumn("95% CI, lower"),
        ResultColumn("95% CI, upper"),
    )
    rows: list[tuple[Cell, ...]] = []
    for j in np.flatnonzero(km.n_event > 0):
        rows.append(
            (
                float(km.time[j]),
                int(km.n_risk[j]),
                int(km.n_event[j]),
                int(km.n_censor[j]),
                float(km.survival[j]),
                float(km.std_err[j]),
                float(km.lower[j]),
                float(km.upper[j]),
            )
        )
    return ResultTable(f"Survival of {name} at each event time", columns, tuple(rows))


def risk_times(longest: float, steps: int = RISK_STEPS) -> list[float]:
    """0, then 1, 2 or 5 × 10^k steps up to ``longest``: the times of the numbers-at-risk
    table."""
    if not longest > 0 or not math.isfinite(longest):
        return [0.0]
    raw = longest / steps
    power = 10.0 ** math.floor(math.log10(raw))
    ratio = raw / power
    step = (1.0 if ratio < 1.5 else 2.0 if ratio < 3.0 else 5.0 if ratio < 7.0 else 10.0) * power
    count = math.floor(longest / step + 1e-9)
    return [round(i * step, 12) for i in range(count + 1)]


def _risk_table(names: Sequence[str], estimates: Sequence[KaplanMeier], unit: str) -> ResultTable:
    longest = max(float(km.time[-1]) for km in estimates)
    times = risk_times(longest)
    columns = (
        ResultColumn("Time", Kind.LABEL, unit),
        *(ResultColumn(name, Kind.INTEGER) for name in names),
    )
    at_risk = [km.at_risk(times) for km in estimates]
    rows = tuple((t, *(int(counts[i]) for counts in at_risk)) for i, t in enumerate(times))
    return ResultTable("Number at risk", columns, rows)


register(KaplanMeierAnalysis())
