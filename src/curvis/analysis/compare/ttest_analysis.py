"""The t test analysis: two ticked series, unpaired (Welch's correction by default, or
Student's test) or paired, with the difference between the means and its 95% CI."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from curvis.analysis.base import (
    AnalysisError,
    AnalysisInputs,
    Params,
    param,
    register,
)
from curvis.analysis.compare.common import (
    FEW_VALUES,
    GroupComparison,
    few_values_warnings,
    group_values,
    number,
    p_text,
    significant,
    zero_variance_warnings,
)
from curvis.analysis.compare.ttest import METHODS, Problem, TTestKind, TTestStats, t_test
from curvis.analysis.result import Kind, Result, Value
from curvis.data import Table

TEST_CHOICES = (
    (TTestKind.WELCH.value, "Unpaired, Welch's correction (SDs may differ)"),
    (TTestKind.STUDENT.value, "Unpaired, assume equal SDs (Student)"),
    (TTestKind.PAIRED.value, "Paired (values matched by row)"),
)


@dataclass(frozen=True)
class TTestParams(Params):
    test: str = param(TTestKind.WELCH.value, "Test t&ype", choices=TEST_CHOICES)


class TTest(GroupComparison):
    id = "t_test"
    label = "&t test"
    method = "t test"
    params_type = TTestParams
    min_groups = 2
    max_groups = 2

    def check(self, table: Table, inputs: Sequence[str], params: Params) -> str | None:
        problem = super().check(table, inputs, params)
        if problem is not None:
            return problem
        assert isinstance(params, TTestParams)
        if params.test == TTestKind.PAIRED.value:
            a, b = self.selected(table, inputs)
            if a.n_replicates != b.n_replicates:
                return "For a paired t test, tick two series with the same number of columns."
        return None

    def describe(self, table: Table, inputs: Sequence[str], params: Params) -> str:
        assert isinstance(params, TTestParams)
        kind = TTestKind(params.test)
        a, b = (c.name for c in self.selected(table, inputs))
        what = (
            f"the mean of the differences {b} − {a}"
            if kind is TTestKind.PAIRED
            else f"the difference between the means ({b} − {a})"
        )
        return f"{METHODS[kind]} of {b} against {a}: {what} with its 95% confidence interval."

    @staticmethod
    def run(inputs: AnalysisInputs, params: TTestParams) -> Result:
        if len(inputs.series) != 2:
            raise AnalysisError("Tick exactly 2 series, then run the analysis again.")
        first, second = inputs.series
        a, b = group_values(first), group_values(second)
        kind = TTestKind(params.test)
        if kind is TTestKind.PAIRED and len(a) != len(b):
            raise AnalysisError("For a paired t test both series need the same number of columns.")
        r = t_test(a, b, kind)
        names = (first.name, second.name)
        _raise_problem(r, kind)
        return Result(
            METHODS[kind],
            _summary(r, kind, names),
            tuple(_warnings(r, kind, names)),
            tuple(_values(r, kind, names)),
        )


def _raise_problem(r: TTestStats, kind: TTestKind) -> None:
    if r.problem is Problem.TOO_FEW:
        if kind is TTestKind.PAIRED:
            raise AnalysisError(
                "A paired t test needs at least 2 complete pairs (rows with a value in "
                "both series)."
            )
        if kind is TTestKind.WELCH:
            raise AnalysisError("Welch's t test needs at least 2 values in each group.")
        raise AnalysisError("The t test needs a value in each group and 3 values in total.")
    if r.problem is Problem.NO_VARIATION:
        if kind is TTestKind.PAIRED:
            raise AnalysisError(
                "The differences between the pairs are all the same, so the paired t test "
                "cannot be computed."
            )
        raise AnalysisError(
            "The values do not vary within either group, so the t test cannot be computed."
        )


def _summary(r: TTestStats, kind: TTestKind, names: tuple[str, str]) -> str:
    a, b = names
    ci = f"95% CI {number(r.ci_lower)} to {number(r.ci_upper)}"
    verdict = "statistically significant" if significant(r.p_value) else "not significant"
    if r.difference == 0:
        return f"The means of {a} and {b} are equal ({ci}, {p_text(r.p_value)})."
    direction = "higher" if r.difference > 0 else "lower"
    size = number(abs(r.difference))
    if kind is TTestKind.PAIRED:
        start = f"On average, {b} is {size} {direction} than {a} in the same row"
    else:
        start = f"The mean of {b} is {size} {direction} than the mean of {a}"
    return f"{start} ({ci}); the difference is {verdict} ({p_text(r.p_value)})."


def _warnings(r: TTestStats, kind: TTestKind, names: tuple[str, str]) -> list[str]:
    if kind is TTestKind.PAIRED:
        if r.n_pairs < FEW_VALUES:
            return [
                f"There are only {r.n_pairs} complete pairs. With fewer than {FEW_VALUES} the "
                "test has little power and its assumptions cannot be checked."
            ]
        return []
    samples = (r.a, r.b)
    return few_values_warnings(samples, names) + zero_variance_warnings(samples, names)


def _values(r: TTestStats, kind: TTestKind, names: tuple[str, str]) -> list[Value]:
    a, b = names
    df_kind = Kind.NUMBER if kind is TTestKind.WELCH else Kind.INTEGER
    values = [
        Value("P value", r.p_value, Kind.P_VALUE),
        Value("t", r.t),
        Value("df", r.df, df_kind),
        Value(f"Mean of {a}", r.a.mean),
        Value(f"Mean of {b}", r.b.mean),
        Value(f"Difference ({b} − {a})", r.difference),
        Value("SE of difference", r.se),
        Value("Lower 95% CI of difference", r.ci_lower),
        Value("Upper 95% CI of difference", r.ci_upper),
    ]
    if kind is TTestKind.PAIRED:
        values.append(Value("Number of pairs", r.n_pairs, Kind.INTEGER))
    else:
        values += [Value(f"n ({a})", r.a.n, Kind.INTEGER), Value(f"n ({b})", r.b.n, Kind.INTEGER)]
    return values


register(TTest())
