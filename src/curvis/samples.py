"""The sample projects shown as tiles in the launcher and under the File menu (New from sample).

Each sample is built in code with the same defaults the Analyze dialog and the Plot
button use, so it always matches the current document model. Every call returns a new
document with new ids and a clean undo stack: a sample opens as a new, untitled copy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from curvis.analysis.base import Analysis, get, load_builtin
from curvis.analysis.compare.anova_analysis import DUNNETT
from curvis.charts.builder import default_spec
from curvis.charts.spec import ChartFamily, ChartSpec, ErrorBars
from curvis.core.document import Document
from curvis.data import Column, ColumnRole, ReplicateGroup, Table

GROUP_COMPARISON = "group_comparison"
DOSE_RESPONSE = "dose_response"
XY_REPLICATES = "xy_replicates"


@dataclass(frozen=True)
class Sample:
    key: str
    name: str
    build: Callable[[], Document]


def _analysis(type_id: str, table: Table, **params: object) -> Analysis:
    load_builtin()
    analysis_type = get(type_id)
    defaults = analysis_type.default_params()
    return Analysis(
        type_id,
        table.id,
        analysis_type.default_inputs(table),
        replace(defaults, **params) if params else defaults,
    )


def _document(
    name: str,
    table: Table,
    analysis: Analysis,
    family: ChartFamily,
    error_bars: ErrorBars = ErrorBars.SD,
) -> Document:
    graph: ChartSpec = default_spec(table, family, [analysis], error_bars=error_bars)
    return Document(name=name, tables=[table], analyses=[analysis], graphs=[graph])


def group_comparison() -> Document:
    """Viability of cells treated with three drugs: one-way ANOVA with Dunnett against
    Control, drawn as bars with SD."""
    values = {
        "Control": [98.2, 101.5, 95.8, 103.1, 99.4, 97.0],
        "Drug A": [92.5, 88.1, 95.3, 90.8, 86.9, 93.4],
        "Drug B": [71.2, 76.8, 68.5, 74.9, 79.3, 70.1],
        "Drug C": [55.4, 61.2, 49.8, 58.7, 52.3, 63.0],
    }
    table = Table(
        "Viability",
        [Column.new(name, ColumnRole.Y, v, unit="%") for name, v in values.items()],
    )
    control = table.columns[0].id
    anova = _analysis("one_way_anova", table, posthoc=DUNNETT, control=control)
    return _document("Group comparison", table, anova, ChartFamily.BAR)


DOSES_UM = [0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0]
INHIBITION = [
    [3.2, 5.4, 8.0, 16.9, 40.1, 66.1, 86.7, 92.6, 96.8],
    [2.4, 4.1, 5.9, 12.4, 33.2, 66.9, 83.4, 90.4, 93.4],
    [5.6, 6.9, 10.5, 20.2, 48.9, 67.6, 86.3, 86.2, 90.0],
]


def dose_response() -> Document:
    """Inhibition by a compound at nine concentrations in triplicate, fitted with a
    Hill 4PL and drawn on a log X axis with the fitted curve."""
    group = ReplicateGroup("Compound")
    columns = [Column.new("Concentration", ColumnRole.X, DOSES_UM, unit="µM")]
    columns += [
        Column.new(f"Compound {i + 1}", ColumnRole.Y, v, unit="%", group_id=group.id)
        for i, v in enumerate(INHIBITION)
    ]
    table = Table("Inhibition", columns, [group])
    fit = _analysis("hill_4pl", table)
    return _document("Dose-response", table, fit, ChartFamily.XY, ErrorBars.SEM)


TIMES_MIN = [0, 5, 10, 15, 20, 25, 30]
ABSORBANCE = {
    "Control": [
        [0.254, 0.325, 0.339, 0.388, 0.461, 0.469, 0.559],
        [0.202, 0.305, 0.310, 0.397, 0.464, 0.532, 0.561],
        [0.196, 0.226, 0.353, 0.414, 0.440, 0.515, 0.542],
    ],
    "Treated": [
        [0.196, 0.385, 0.530, 0.711, 0.902, 1.062, 1.208],
        [0.232, 0.405, 0.560, 0.722, 0.894, 1.068, 1.195],
        [0.222, 0.353, 0.544, 0.708, 0.889, 1.085, 1.231],
    ],
}


def xy_replicates() -> Document:
    """Absorbance over time for two conditions in triplicate, with a straight line
    fitted to each; drawn as points with SEM."""
    columns = [Column.new("Time", ColumnRole.X, TIMES_MIN, unit="min")]
    groups = []
    for name, replicates in ABSORBANCE.items():
        group = ReplicateGroup(name)
        groups.append(group)
        columns += [
            Column.new(f"{name} {i + 1}", ColumnRole.Y, v, group_id=group.id)
            for i, v in enumerate(replicates)
        ]
    table = Table("Absorbance", columns, groups)
    linear = _analysis("linear_regression", table)
    return _document("XY with replicates", table, linear, ChartFamily.XY, ErrorBars.SEM)


SAMPLES: tuple[Sample, ...] = (
    Sample(GROUP_COMPARISON, "Group comparison", group_comparison),
    Sample(DOSE_RESPONSE, "Dose-response", dose_response),
    Sample(XY_REPLICATES, "XY with replicates", xy_replicates),
)


def sample(key: str) -> Sample:
    """The sample with ``key``; raises ``KeyError`` if there is none."""
    for s in SAMPLES:
        if s.key == key:
            return s
    raise KeyError(key)
