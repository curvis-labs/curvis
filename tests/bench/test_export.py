"""Benchmarks for graph export. Target: export a 300 dpi PDF/PNG < 1 s (CLAUDE.md).

Each round writes one file with :func:`curvis.charts.render_mpl.export.export_graph`, the
function the scheduler runs in a worker process. Matplotlib is imported before the
rounds start, as it is in a worker after its first export; the plot data is computed
once, as the Graph panel already has it.

- XY: 4 replicate groups × 3 columns × 1,000 rows (4,000 points with SD error bars) and a
  fitted curve with its confidence band, on a log X axis.
- Bar: the same 4 groups as bars with SD error bars.
"""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from curvis.analysis.result import FittedCurve, Result
from curvis.charts.builder import default_spec
from curvis.charts.data import ChartData, compute, gather
from curvis.charts.render_mpl.export import ExportFormat, export_graph
from curvis.charts.spec import ChartFamily, ChartSpec, FitOverlay, Scale
from tests.bench.test_graph import big_table

EXPORT_TARGET_S = 1.0
DPI = 300


def xy_graph() -> tuple[ChartSpec, ChartData]:
    table = big_table()
    spec = default_spec(table, ChartFamily.XY)
    grid = np.logspace(-3, 2, 200)
    fit = 80 / (1 + 0.5 / grid)
    curve = FittedCurve(
        table.replicate_groups[0].id,
        "Group 1",
        tuple(grid),
        tuple(fit),
        tuple(fit - 2),
        tuple(fit + 2),
        log_x=True,
    )
    spec = replace(
        spec,
        overlays=(FitOverlay("fit", show_band=True),),
        x_axis=replace(spec.x_axis, scale=Scale.LOG),
    )
    return spec, compute(gather(table, spec, {"fit": Result("m", "s", curves=(curve,))}))


def bar_graph() -> tuple[ChartSpec, ChartData]:
    table = big_table()
    spec = default_spec(table, ChartFamily.BAR)
    return spec, compute(gather(table, spec, {}))


GRAPHS: dict[str, Callable[[], tuple[ChartSpec, ChartData]]] = {
    "xy": xy_graph,
    "bar": bar_graph,
}


@pytest.mark.parametrize("fmt", [ExportFormat.PNG, ExportFormat.PDF])
@pytest.mark.parametrize("graph", GRAPHS)
def test_export_300_dpi(benchmark: Any, tmp_path: Path, graph: str, fmt: ExportFormat) -> None:
    spec, data = GRAPHS[graph]()
    path = str(tmp_path / f"graph.{fmt.value}")
    export_graph(spec, data, path, fmt, DPI)  # imports Matplotlib, as a worker's first export
    benchmark.pedantic(export_graph, args=(spec, data, path, fmt, DPI), rounds=10, warmup_rounds=1)
    assert Path(path).stat().st_size > 0
    assert benchmark.stats.stats.min < EXPORT_TARGET_S
