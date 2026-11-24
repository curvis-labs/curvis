"""Comparison brackets on bar-family graphs: which pairs the analyses report, which the
graph draws and with what text, where the brackets go, and that both renderers draw
them."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from curvis.analysis.base import Analysis
from curvis.analysis.compare.anova_analysis import AnovaParams
from curvis.analysis.compare.kruskal_analysis import KruskalParams
from curvis.analysis.compare.mann_whitney_analysis import MannWhitneyParams
from curvis.analysis.compare.ttest_analysis import TTestParams
from curvis.analysis.result import PDisplay, Result
from curvis.charts.axes import axes_layout
from curvis.charts.brackets import LEG_INSET, category_top, place_brackets, settle
from curvis.charts.builder import comparison_problem, default_spec, is_comparison
from curvis.charts.data import (
    COMPARISONS_NOT_DRAWN,
    NO_COMPARISON_RESULT,
    ChartData,
    PairMark,
    compute,
    gather,
    pair_text,
)
from curvis.charts.marks import bar_marks
from curvis.charts.spec import (
    AxisSpec,
    ChartFamily,
    ChartSpec,
    ChartVariant,
    Comparisons,
    Grouping,
    PLabel,
    spec_from_json,
    spec_to_json,
)
from curvis.core import Document, add_analysis, add_graph, compared_on, remove_analysis
from curvis.data import Column, ColumnRole, Table


def groups_table() -> Table:
    return Table(
        "Groups",
        [
            Column.new("Control", ColumnRole.Y, [4.1, 5.0, 4.6, 5.3, 4.8], id="c"),
            Column.new("Drug A", ColumnRole.Y, [6.2, 7.1, 6.8, 7.4, 6.5], id="a"),
            Column.new("Drug B", ColumnRole.Y, [9.0, 8.4, 9.6, 8.8, 9.9], id="b"),
            Column.new("Drug C", ColumnRole.Y, [5.0, 5.6, 4.9, 5.8, 5.2], id="d"),
        ],
        id="t",
    )


def run(table: Table, analysis: Analysis) -> Result:
    kind = analysis.analysis_type
    return kind.run(kind.gather(table, analysis), analysis.params)


def anova(table: Table, **params: str) -> Analysis:
    ids = tuple(c.id for c in table.columns)
    return Analysis("one_way_anova", table.id, ids, AnovaParams(**params), id="anova")


def bar_graph(table: Table, variant: ChartVariant = ChartVariant.BAR, **kw: object) -> ChartSpec:
    spec = default_spec(table, ChartFamily.BAR)
    return replace(spec, variant=variant, comparisons=Comparisons("anova", **kw))  # type: ignore[arg-type]


def data_of(table: Table, spec: ChartSpec, result: Result | None) -> ChartData:
    results = {"anova": result} if result is not None else {}
    return compute(gather(table, spec, results))


# --- the pairs the analyses report ----------------------------------------------------------


def test_tukey_reports_every_pair_with_its_adjusted_p_value() -> None:
    table = groups_table()
    result = run(table, anova(table))
    assert len(result.pairs) == 6
    posthoc = result.tables[-1]
    for pair, row in zip(result.pairs, posthoc.rows, strict=True):
        assert pair.p_value == row[-1]
    names = {"c": "Control", "a": "Drug A", "b": "Drug B", "d": "Drug C"}
    labels = [f"{names[p.second]} − {names[p.first]}" for p in result.pairs]
    assert labels == [row[0] for row in posthoc.rows]


def test_dunnett_reports_each_group_against_the_control() -> None:
    table = groups_table()
    result = run(table, anova(table, posthoc="dunnett", control="a"))
    assert {p.first for p in result.pairs} == {"a"}
    assert {p.second for p in result.pairs} == {"c", "b", "d"}


@pytest.mark.parametrize(
    ("kind", "params"),
    [("t_test", TTestParams()), ("mann_whitney", MannWhitneyParams())],
)
def test_two_group_tests_report_their_one_pair(kind: str, params: object) -> None:
    table = groups_table()
    analysis = Analysis(kind, table.id, ("c", "b"), params)  # type: ignore[arg-type]
    result = run(table, analysis)
    (pair,) = result.pairs
    assert (pair.first, pair.second) == ("c", "b")
    assert pair.p_value == next(v.value for v in result.values if v.label == "P value")


def test_dunn_reports_its_pairs() -> None:
    table = groups_table()
    ids = tuple(c.id for c in table.columns)
    result = run(table, Analysis("kruskal_wallis", table.id, ids, KruskalParams()))
    assert len(result.pairs) == 6
    assert [p.p_value for p in result.pairs] == [row[-1] for row in result.tables[-1].rows]


def test_which_analyses_graphs_can_compare() -> None:
    table = groups_table()
    assert is_comparison(anova(table))
    assert not is_comparison(Analysis("descriptive_statistics", table.id, ("c",), AnovaParams()))


# --- what the graph draws -----------------------------------------------------------------


def test_stars_and_ns() -> None:
    assert pair_text(0.04, PLabel.STARS) == "*"
    assert pair_text(0.009, PLabel.STARS) == "**"
    assert pair_text(0.0009, PLabel.STARS) == "***"
    assert pair_text(0.05, PLabel.STARS) == "ns"
    assert pair_text(0.0312, PLabel.P_VALUE) == "P = 0.0312"
    assert pair_text(0.00002, PLabel.P_VALUE) == "P < 0.001"
    assert pair_text(0.00002, PLabel.P_VALUE, PDisplay.BELOW_00001) == "P < 0.0001"
    assert pair_text(2.3e-7, PLabel.P_VALUE, PDisplay.EXACT) == "P = 2.3 × 10⁻⁷"


def test_only_significant_pairs_unless_ns_is_shown() -> None:
    table = groups_table()
    result = run(table, anova(table))
    significant = [p for p in result.pairs if p.p_value < 0.05]
    assert 0 < len(significant) < len(result.pairs)
    data = data_of(table, bar_graph(table), result)
    assert [(p.first, p.second) for p in data.pairs] == [(p.first, p.second) for p in significant]
    shown = data_of(table, bar_graph(table, show_ns=True), result)
    assert len(shown.pairs) == len(result.pairs)
    assert "ns" in [p.text for p in shown.pairs]
    as_p = data_of(table, bar_graph(table, label=PLabel.P_VALUE), result)
    assert all(p.text.startswith("P ") for p in as_p.pairs)
    assert data.notes == ()


def test_notes_without_a_result_or_on_a_graph_that_cannot_draw_them() -> None:
    table = groups_table()
    data = data_of(table, bar_graph(table), None)
    assert data.pairs == () and data.notes == (NO_COMPARISON_RESULT,)
    result = run(table, anova(table))
    for variant in (ChartVariant.HORIZONTAL_BAR, ChartVariant.HISTOGRAM):
        data = data_of(table, bar_graph(table, variant), result)
        assert data.pairs == () and COMPARISONS_NOT_DRAWN in data.notes
        assert comparison_problem(bar_graph(table, variant)) is not None
    assert comparison_problem(bar_graph(table)) is None
    assert comparison_problem(replace(bar_graph(table), grouping=Grouping.BY_SERIES)) is not None


def test_only_bar_family_graphs_store_comparisons() -> None:
    table = groups_table()
    with pytest.raises(ValueError, match="only bar-family"):
        ChartSpec(table.id, ChartFamily.XY, ChartVariant.SCATTER, comparisons=Comparisons("a"))


def test_comparisons_round_trip_through_json() -> None:
    table = groups_table()
    spec = bar_graph(table, label=PLabel.P_VALUE, show_ns=True)
    assert spec_from_json(spec_to_json(spec)) == spec
    plain = default_spec(table, ChartFamily.BAR)
    assert spec_from_json(spec_to_json(plain)).comparisons is None


# --- where the brackets go -----------------------------------------------------------------


def mark(first: str, second: str) -> PairMark:
    return PairMark(first, second, "*", 0.01)


def test_brackets_stack_shortest_first_and_clear_what_they_span() -> None:
    positions = {"a": 0.0, "b": 1.0, "c": 2.0}
    tops = {"a": 1.0, "b": 5.0, "c": 2.0}
    em = 0.5
    ab, bc, ac = place_brackets(
        [mark("a", "c"), mark("a", "b"), mark("b", "c")], positions, tops, em
    )
    # the short ones first, from the left; the long one goes over both
    assert (ab.first, bc.first, ac.first) == ("a", "b", "a")
    assert ab.y_leg > tops["b"] and bc.y_leg > tops["b"]
    assert bc.y_leg > ab.y_text  # they share Drug B: the second goes over the first's text
    assert ac.y_leg > max(ab.y_text, bc.y_text)
    assert ab.x0 == pytest.approx(LEG_INSET) and ab.x1 == pytest.approx(1 - LEG_INSET)
    assert ac.x_text == pytest.approx(1.0)


def test_pairs_of_hidden_groups_are_left_out() -> None:
    placed = place_brackets([mark("a", "z")], {"a": 0.0, "b": 1.0}, {"a": 1.0, "b": 1.0}, 0.1)
    assert placed == []


def test_the_range_settles_over_the_brackets() -> None:
    positions = {"a": 0.0, "b": 1.0, "c": 2.0}
    tops = {"a": 4.0, "b": 9.0, "c": 5.0}
    pairs = [mark("a", "b"), mark("b", "c"), mark("a", "c")]
    brackets, top = settle(pairs, positions, tops, 0.0, 9.0, 120.0, 9.0, 0.05)
    assert len(brackets) == 3
    units = top / 120.0  # plot units per point of the final range
    reach = max(b.y_text for b in brackets) + 9.0 * units
    assert top == pytest.approx(reach * 1.05, rel=1e-6)  # the text fits, padded by 5 %
    # the brackets were placed with the scale of the range they make
    again = place_brackets(pairs, positions, tops, 9.0 * units)
    assert [b.y for b in again] == pytest.approx([b.y for b in brackets], rel=1e-6)


def test_many_brackets_do_not_squeeze_the_data_to_nothing() -> None:
    positions = {s: float(i) for i, s in enumerate("abcdefgh")}
    tops = dict.fromkeys(positions, 1.0)
    pairs = [mark(x, y) for i, x in enumerate("abcdefgh") for y in "abcdefgh"[i + 1 :]]
    brackets, top = settle(pairs, positions, tops, 0.0, 1.0, 100.0, 10.0, 0.05)
    assert len(brackets) == 28 and math.isfinite(top)
    assert (top - 1.0) / top <= 0.75  # the data keeps a quarter of the axis


def test_layout_and_marks_of_a_bar_graph() -> None:
    table = groups_table()
    result = run(table, anova(table))
    spec = bar_graph(table)
    data = data_of(table, spec, result)
    layout = axes_layout(spec, data)
    assert len(layout.brackets) == len(data.pairs)
    highest = max(b.y_text for b in layout.brackets)
    assert layout.y.high > highest
    plain = axes_layout(
        replace(spec, comparisons=None), data_of(table, replace(spec, comparisons=None), None)
    )
    assert plain.brackets == () and plain.y.high < layout.y.high
    marks = bar_marks(spec, data, layout)
    assert [label.text for label in marks.labels] == [b.text for b in layout.brackets]
    bracket_lines = marks.over[-1]
    assert len(bracket_lines.segments) == 3 * len(layout.brackets)


def test_a_typed_top_keeps_the_axis_and_sizes_the_brackets_for_it() -> None:
    table = groups_table()
    result = run(table, anova(table))
    spec = replace(bar_graph(table), y_axis=AxisSpec(maximum=100.0))
    layout = axes_layout(spec, data_of(table, spec, result))
    assert layout.y.high == 100.0 and layout.brackets


@pytest.mark.parametrize(
    "variant",
    [ChartVariant.BAR, ChartVariant.BOX, ChartVariant.VIOLIN, ChartVariant.COLUMN_SCATTER],
)
def test_every_upright_variant_draws_brackets_over_its_marks(variant: ChartVariant) -> None:
    table = groups_table()
    result = run(table, anova(table))
    spec = bar_graph(table, variant)
    data = data_of(table, spec, result)
    layout = axes_layout(spec, data)
    assert layout.brackets
    tops = {c.source: category_top(c, variant) for c in data.categories}
    for b in layout.brackets:
        spanned = [s for s, x in layout.positions.items() if b.x0 - 1 < x < b.x1 + 1]
        assert b.y_leg > max(tops[s] for s in spanned)


def test_the_export_draws_the_bracket_texts(tmp_path: object) -> None:
    import matplotlib

    from curvis.charts.render_mpl.figure import chart_rc, make_figure

    table = groups_table()
    result = run(table, anova(table))
    spec = bar_graph(table)
    data = data_of(table, spec, result)
    with matplotlib.rc_context(chart_rc(spec)):  # type: ignore[arg-type]
        fig = make_figure(spec, data)
        texts = [t.get_text() for t in fig.axes[0].texts]
    assert texts == [p.text for p in sorted_pairs(spec, data)]


def sorted_pairs(spec: ChartSpec, data: ChartData) -> list[PairMark]:
    layout = axes_layout(spec, data)
    order = [(b.first, b.second) for b in layout.brackets]
    by = {(p.first, p.second): p for p in data.pairs}
    return [by[k] for k in order]


# --- deleting the analysis --------------------------------------------------------------


def test_deleting_the_analysis_removes_the_brackets_in_one_undo_step() -> None:
    table = groups_table()
    doc = Document(tables=[table])
    doc.execute(add_analysis(doc, anova(table)))
    spec = bar_graph(table)
    doc.execute(add_graph(doc, spec))
    assert compared_on(doc, "anova") == [spec]
    doc.execute(remove_analysis(doc, "anova"))
    assert doc.graph(spec.id).comparisons is None
    doc.undo()
    assert doc.graph(spec.id).comparisons == spec.comparisons
