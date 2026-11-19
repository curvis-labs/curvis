"""The interaction chart: drawn from a two-way ANOVA's cell means, the levels of the first
factor on X and one line per category group (the second factor)."""

from dataclasses import replace

import numpy as np
import pytest

from curvis.analysis.result import Result, format_number
from curvis.charts.axes import axes_layout
from curvis.charts.builder import (
    INTERACTION_ONLY,
    NEEDS_TWO_WAY,
    default_series_of,
    default_spec,
    family_problem,
    graph_series_names,
    variant_problem,
    with_variant,
)
from curvis.charts.data import NO_TWO_WAY_RESULT, compute, gather
from curvis.charts.spec import (
    ChartFamily,
    ChartSpec,
    ChartVariant,
    ErrorBars,
    LineStyle,
    Scale,
    spec_from_json,
    spec_to_json,
)
from curvis.charts.tooltips import describe_cell
from curvis.compute.graph_engine import graph_reads
from curvis.core import Document, remove_analysis
from curvis.data import Table
from tests.unit.charts_tables import TWO_WAY, two_way_analysis, two_way_result, two_way_table


def setup() -> tuple[Table, ChartSpec, Result]:
    table = two_way_table()
    analysis = two_way_analysis(table)
    spec = default_spec(table, ChartFamily.INTERACTION, [analysis])
    return table, spec, two_way_result(table, analysis)


def test_interaction_needs_a_two_way_anova_of_the_table() -> None:
    table = two_way_table()
    assert family_problem(table, ChartFamily.INTERACTION) == NEEDS_TWO_WAY
    other = replace(two_way_analysis(table), table_id="elsewhere")
    assert family_problem(table, ChartFamily.INTERACTION, [other]) == NEEDS_TWO_WAY
    assert family_problem(table, ChartFamily.INTERACTION, [two_way_analysis(table)]) is None


def test_default_interaction_spec() -> None:
    table = two_way_table()
    analysis = two_way_analysis(table)
    spec = default_spec(table, ChartFamily.INTERACTION, [analysis])
    assert spec.variant is ChartVariant.INTERACTION
    assert spec.analysis_id == analysis.id
    assert spec.x_axis.title == "Dose"  # the first factor's name
    assert spec.y_axis.title == "mM"
    assert spec.show_legend
    names = graph_series_names(table, spec)
    assert [names[s.source] for s in spec.series] == ["Male", "Female"]
    assert all(s.line_style is LineStyle.SOLID for s in spec.series)
    assert all(s.error_bars is ErrorBars.SD for s in spec.series)
    assert [s.source for s in default_series_of(table, spec)] == [s.source for s in spec.series]


def test_the_newest_two_way_anova_is_drawn() -> None:
    table = two_way_table()
    first = two_way_analysis(table)
    second = replace(first, id="newer")
    assert default_spec(table, ChartFamily.INTERACTION, [first, second]).analysis_id == "newer"


def test_spec_rules_and_json() -> None:
    _table, spec, _result = setup()
    assert spec_from_json(spec_to_json(spec)) == spec
    with pytest.raises(ValueError, match="names the two-way ANOVA"):
        replace(spec, analysis_id=None)
    xy = default_spec(two_way_table(), ChartFamily.BAR)
    with pytest.raises(ValueError, match="names the two-way ANOVA"):
        replace(xy, analysis_id="anova")


def test_an_interaction_graph_keeps_its_type() -> None:
    table, spec, _result = setup()
    assert variant_problem(table, ChartVariant.BAR, spec) == INTERACTION_ONLY
    with pytest.raises(ValueError, match="keeps its type"):
        with_variant(spec, ChartVariant.SCATTER, table)


def cell_mean(name: str, category: str) -> float:
    return float(np.mean(np.concatenate(TWO_WAY[(name, category)])))


def cell_sd(name: str, category: str) -> float:
    return float(np.std(np.concatenate(TWO_WAY[(name, category)]), ddof=1))


def test_lines_are_the_cell_means_with_sd() -> None:
    table, spec, result = setup()
    data = compute(gather(table, spec, {"anova": result}))
    assert data.levels == ("Control", "Low", "High")
    male, female = data.xy
    assert (male.name, female.name) == ("Male", "Female")
    assert male.px.tolist() == [0.0, 1.0, 2.0]
    expected = [cell_mean(level, "Male") for level in data.levels]
    np.testing.assert_allclose(male.py, expected, rtol=1e-12)
    np.testing.assert_allclose(male.err_high, [cell_sd(lv, "Male") for lv in data.levels])
    assert male.n.tolist() == [6, 6, 6]


def test_sem_and_no_error_bars() -> None:
    table, spec, result = setup()
    sem = replace(spec, series=tuple(replace(s, error_bars=ErrorBars.SEM) for s in spec.series))
    data = compute(gather(table, sem, {"anova": result}))
    np.testing.assert_allclose(data.xy[0].error[0], cell_sd("Control", "Male") / np.sqrt(6))
    none = replace(spec, series=tuple(replace(s, error_bars=ErrorBars.NONE) for s in spec.series))
    data = compute(gather(table, none, {"anova": result}))
    assert np.isnan(data.xy[0].err_high).all()


def test_without_a_result_the_graph_says_so() -> None:
    table, spec, _result = setup()
    data = compute(gather(table, spec, {}))
    assert data.xy == ()
    assert data.notes == (NO_TWO_WAY_RESULT,)


def test_a_category_gone_from_the_table_is_counted() -> None:
    table, spec, result = setup()
    renamed = replace(spec, series=(*spec.series, replace(spec.series[0], source="gone")))
    data = compute(gather(table, renamed, {"anova": result}))
    assert len(data.xy) == 2
    assert data.notes == ("1 series is no longer in the table.",)


def test_x_axis_shows_the_levels_and_y_fits_the_error_bars() -> None:
    table, spec, result = setup()
    data = compute(gather(table, spec, {"anova": result}))
    layout = axes_layout(spec, data)
    assert (layout.x.low, layout.x.high) == (-0.5, 2.5)
    assert [t.label for t in layout.x.major] == ["Control", "Low", "High"]
    top = max(float(np.max(s.py + s.err_high)) for s in data.xy)
    bottom = min(float(np.min(s.py - s.err_low)) for s in data.xy)
    assert layout.y.low < bottom and layout.y.high > top


def test_log_y_axis() -> None:
    table, spec, result = setup()
    log = replace(spec, y_axis=replace(spec.y_axis, scale=Scale.LOG))
    data = compute(gather(table, log, {"anova": result}))
    np.testing.assert_allclose(data.xy[0].py[0], np.log10(cell_mean("Control", "Male")))


def test_tooltip_names_the_cell() -> None:
    table, spec, result = setup()
    data = compute(gather(table, spec, {"anova": result}))
    text = describe_cell(data.xy[1], 2, "High", "Dose")
    assert text.splitlines() == [
        "Female · Dose: High",
        f"Mean: {format_number(cell_mean('High', 'Female'))}",
        f"SD: {format_number(cell_sd('High', 'Female'))}",
        "n: 6",
    ]


def test_graph_reads_its_analysis() -> None:
    table, spec, _result = setup()
    assert "anova" in graph_reads(table, spec)


def test_deleting_the_anova_deletes_its_interaction_graphs() -> None:
    table = two_way_table()
    analysis = two_way_analysis(table)
    graph = default_spec(table, ChartFamily.INTERACTION, [analysis])
    bar = default_spec(table, ChartFamily.BAR)
    doc = Document(tables=[table], analyses=[analysis], graphs=[graph, bar])
    doc.execute(remove_analysis(doc, analysis.id))
    assert doc.analyses == [] and doc.graphs == [bar]
    doc.undo_stack.undo()
    assert doc.analyses == [analysis] and doc.graphs == [graph, bar]


def test_export_draws_lines_with_markers() -> None:
    import matplotlib

    from curvis.charts.render_mpl.figure import RC_PARAMS
    from curvis.charts.render_mpl.figure import make_figure

    table, spec, result = setup()
    data = compute(gather(table, spec, {"anova": result}))
    with matplotlib.rc_context(RC_PARAMS):
        fig = make_figure(spec, data)
        ax = fig.axes[0]
        lines = [line for line in ax.get_lines() if len(line.get_xdata()) == 3]
        assert len(lines) >= 2
        assert all(line.get_linestyle() == "-" for line in lines[:2])
        assert [t.get_text() for t in ax.get_xticklabels()] == ["Control", "Low", "High"]
        legend = fig.legends[0] if fig.legends else ax.get_legend()
        assert [t.get_text() for t in legend.get_texts()] == ["Male", "Female"]
