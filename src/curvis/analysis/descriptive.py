"""Descriptive statistics: n, mean, SD, SEM and the 95% CI of the mean, row by row.

For each selected series (a replicate group, or one Y column) and each row, the
statistics of the series' replicates in that row, leaving out missing values. The
numbers come from :func:`curvis.data.row_stats` and :func:`curvis.data.mean_ci`,
which are validated against R (``tests/validation/fixtures/descriptive_stats``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from curvis.analysis.base import (
    AnalysisError,
    AnalysisInputs,
    AnalysisType,
    Family,
    Params,
    param,
    register,
)
from curvis.analysis.result import Cell, Kind, Result, ResultColumn, ResultTable
from curvis.data import Table, mean_ci, row_stats

METHOD = "Descriptive statistics"


@dataclass(frozen=True)
class DescriptiveParams(Params):
    confidence_interval: bool = param(True, "Show the 95% confidence &interval")
    skip_empty_rows: bool = param(True, "Leave out &rows without values", advanced=True)


def name_list(names: Sequence[str]) -> str:
    """ "A", "A and B", "A, B and C"."""
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


class DescriptiveStatistics(AnalysisType):
    id = "descriptive_statistics"
    label = "D&escriptive statistics"
    method = METHOD
    family = Family.COMPARE
    params_type = DescriptiveParams
    uses_x = True
    imports = ("scipy.special",)

    def describe(self, table: Table, inputs: Sequence[str], params: Params) -> str:
        assert isinstance(params, DescriptiveParams)
        names = [c.name for c in self.selected(table, inputs)]
        stats = (
            "n, mean, SD, SEM and 95% CI" if params.confidence_interval else ("n, mean, SD and SEM")
        )
        return f"{METHOD} ({stats}) for each row of {name_list(names)}."

    @staticmethod
    def run(inputs: AnalysisInputs, params: DescriptiveParams) -> Result:
        if not inputs.series:
            raise AnalysisError("Tick at least one series, then run the analysis again.")
        labels = _row_labels(inputs)
        tables: list[ResultTable] = []
        warnings: list[str] = []
        filled = np.zeros(inputs.n_rows, dtype=bool)
        for s in inputs.series:
            stats = row_stats(s.columns)
            rows = np.arange(inputs.n_rows)
            if params.skip_empty_rows:
                rows = rows[stats.n > 0]
            filled |= stats.n > 0
            columns = [labels[0], ResultColumn("n", Kind.INTEGER)]
            columns += [ResultColumn("Mean"), ResultColumn("SD"), ResultColumn("SEM")]
            arrays: list[np.ndarray] = [stats.mean, stats.sd, stats.sem]
            if params.confidence_interval:
                low, high = mean_ci(stats)
                columns += [ResultColumn("Lower 95% CI"), ResultColumn("Upper 95% CI")]
                arrays += [low, high]
            body: list[tuple[Cell, ...]] = []
            for i in rows:
                row: list[Cell] = [labels[1][i], int(stats.n[i])]
                row += [float(a[i]) for a in arrays]
                body.append(tuple(row))
            tables.append(ResultTable(s.name, tuple(columns), tuple(body)))
            warnings += _warnings(s.name, len(s.columns), stats.n)
        if not filled.any():
            raise AnalysisError("The ticked series have no values. Enter data or tick others.")
        names = [s.name for s in inputs.series]
        what = (
            "mean, SD, SEM and 95% confidence interval"
            if params.confidence_interval
            else ("mean, SD and SEM")
        )
        summary = (
            f"The {what} of {name_list(names)}, row by row "
            f"({plural(int(filled.sum()), 'row')} with values)."
        )
        return Result(METHOD, summary, tuple(warnings), (), tuple(tables))


def _row_labels(inputs: AnalysisInputs) -> tuple[ResultColumn, list[Cell]]:
    """The first column: the X values when the table has an X column, else row numbers."""
    if inputs.x is not None:
        return ResultColumn(inputs.x_name or "X", Kind.LABEL), [float(v) for v in inputs.x]
    return ResultColumn("Row", Kind.INTEGER), list(range(1, inputs.n_rows + 1))


def _warnings(name: str, n_columns: int, n: np.ndarray) -> list[str]:
    if n_columns == 1:
        return [
            f"{name} is a single column, so it has no SD, SEM or confidence interval. "
            "Group replicate columns to get them."
        ]
    single = int(np.count_nonzero(n == 1))
    if single:
        return [
            f"{name} has only one value in {plural(single, 'row')}; the SD, SEM and "
            "confidence interval of those rows are missing."
        ]
    return []


register(DescriptiveStatistics())
