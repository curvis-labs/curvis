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
