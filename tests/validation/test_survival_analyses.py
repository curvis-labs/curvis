"""The Kaplan-Meier analysis, run as the app runs it, against the kaplan_meier R fixtures:
the numbers are read back from the Result the Results panel shows.

The estimate table lists event times only, so it is compared with the fixture's rows
that have events. "Not reached" in the median table is R's NaN.
"""

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from curvis.analysis import base
from curvis.analysis.base import AnalysisInputs, SeriesData
from curvis.analysis.result import Result, ResultTable
from curvis.analysis.survival.kaplan_meier import NOT_REACHED
from tests.validation.harness import Fixture, compare, fixture_paths, format_report, load_fixture


def paths() -> list[Path]:
    return [p for p in fixture_paths() if p.parent.name == "kaplan_meier"]


def column(table: ResultTable, label: str) -> list[Any]:
    index = [c.label for c in table.columns].index(label)
    return [row[index] for row in table.rows]


def time_value(cell: Any) -> float:
    return float("nan") if cell == NOT_REACHED else float(cell)


def run(fixture: Fixture) -> Result:
    time = np.asarray(fixture.inputs["time"], dtype=np.float64)
    event = np.asarray(fixture.inputs["event"], dtype=np.float64)
    inputs = AnalysisInputs("T", len(time), (SeriesData("e", "A", (time, event)),))
    kind = base.get("kaplan_meier")
    return kind.run(inputs, kind.default_params())


@pytest.mark.parametrize("path", paths(), ids=lambda p: p.stem)
def test_kaplan_meier_analysis(path: Path) -> None:
    fixture = load_fixture(path)
    result = run(fixture)
    summary, estimate = result.tables[0], result.tables[1]
    has_event = np.asarray(fixture.expected["n_event"]) > 0
    keys = ("time", "n_risk", "n_event", "n_censor", "survival", "std_err", "lower", "upper")
    labels = ("Time", "At risk", "Events", "Censored", "Survival", "SE", "95% CI, lower")
    labels += ("95% CI, upper",)
    wanted: dict[str, Any] = {k: np.asarray(fixture.expected[k])[has_event] for k in keys}
    got: dict[str, Any] = {
        k: column(estimate, label) for k, label in zip(keys, labels, strict=True)
    }
    for key, label in (
        ("median", "Median survival"),
        ("median_lower", "95% CI of median, lower"),
        ("median_upper", "95% CI of median, upper"),
    ):
        wanted[key] = fixture.expected[key]
        got[key] = time_value(column(summary, label)[0])
    for key, label in (("n", "n"), ("n_events", "Events")):
        wanted[key] = fixture.expected[key]
        got[key] = column(summary, label)[0]
    trimmed = Fixture(
        fixture.path,
        fixture.analysis,
        fixture.case,
        fixture.description,
        fixture.generated_by,
        fixture.inputs,
        wanted,
        {k: fixture.kinds[k] for k in wanted},
    )
    mismatches = compare(trimmed, got)
    if mismatches:
        pytest.fail(format_report(trimmed, mismatches), pytrace=False)
