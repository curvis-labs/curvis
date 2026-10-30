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
