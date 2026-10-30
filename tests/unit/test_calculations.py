"""Tests for calculations: role references, pairing, generated formulas and commands."""

import json
import math
import zipfile
from pathlib import Path

import numpy as np
import pytest

from curvis.core import (
    AddConstant,
    AddTable,
    BulkRename,
    CalculatedColumn,
    Calculation,
    CalculationKind,
    CommandError,
    Document,
    EditConstant,
    GroupColumns,
    ImportColumns,
    RemoveColumn,
    RemoveConstant,
    RemoveTable,
    RenameColumn,
    SetCalculation,
    SetCellValue,
    SetColumnRole,
    add_calculation,
    constant_node,
    edit_calculation,
)
from curvis.core import calculation_formulas as cf
from curvis.core.constants import ProjectConstant
from curvis.data import Column, ColumnRole, Pairing, Table, role_values
from curvis.formula import FormulaError, evaluate, parse, rename_columns, to_text
from curvis.io.project import PROJECT_JSON, load, save

NAN = math.nan


# --- formulas: role references --------------------------------------------------------


def test_role_references_parse_with_keys() -> None:
    formula = parse("{B} - {a:MEAN} + {b}")
    assert formula.roles == ("b", "a:mean")
    assert to_text(formula.root) == "(({B} - {a:mean}) + {b})"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("{A", "no closing '}'"),
        ("A}", "'}' has no matching '{'"),
        ("{ }", "role name is empty"),
        ("{A:sd}", "Unknown statistic 'sd'"),
    ],
)
def test_bad_role_references(text: str, message: str) -> None:
    with pytest.raises(FormulaError, match=message):
        parse(text)


def test_role_references_evaluate_from_the_roles_given() -> None:
    result = evaluate("{B} - {A:mean}", {}, {}, 2, roles={"b": [10, 20], "a:mean": [1, 2]})
    np.testing.assert_array_equal(result, [9, 18])


def test_missing_role_is_an_error_at_its_position() -> None:
    with pytest.raises(FormulaError, match=r"Unknown role '\{B\}'") as info:
        evaluate("1 + {B}", {}, {}, 1, roles={"a": [1]})
    assert (info.value.start, info.value.end) == (4, 7)


def test_rename_columns_keeps_the_rest_of_the_text() -> None:
    assert rename_columns("[a] *2+ MEAN([B])", {"A": "Dose", "b": "x]y"}) == (
        "[Dose] *2+ MEAN([x]]y])"
    )
    assert rename_columns("[A] - [B]", {"A": "B", "B": "A"}) == "[B] - [A]"
    assert rename_columns("[A] +", {"A": "B"}) == "[A] +"  # does not parse: left alone


# --- pairing ----------------------------------------------------------------------------


def test_unpaired_roles_read_the_column_itself_and_the_mean_of_all() -> None:
    pairing = Pairing(("x", "y"))
    assert pairing.targets == ("x", "y")
    assert pairing.role_sources(1) == {"a": ["y"], "a:mean": ["x", "y"]}
    assert pairing.problem() is None
    assert Pairing().problem() == "Select at least one column."


def test_paired_roles_match_by_position_or_share_one_a_column() -> None:
    pairing = Pairing(("a1", "a2"), ("b1", "b2"), paired=True, a_name="Ctrl", b_name="Drug")
    assert pairing.pairs() == [("a1", "b1"), ("a2", "b2")]
    assert pairing.role_sources(1) == {
        "ctrl": ["a2"],
        "ctrl:mean": ["a1", "a2"],
        "drug": ["b2"],
        "drug:mean": ["b1", "b2"],
    }
    assert Pairing(("a",), ("b1", "b2"), paired=True).pairs() == [("a", "b1"), ("a", "b2")]


@pytest.mark.parametrize(
    ("pairing", "problem"),
    [
        (Pairing(("a1", "a2"), ("b1", "b2", "b3"), paired=True), "same number"),
        (Pairing(("a",), (), paired=True), "role B"),
        (Pairing((), ("b",), paired=True), "role A"),
        (Pairing(("a",), ("a",), paired=True), "both roles"),
        (Pairing(("a",), ("b",), paired=True, b_name="a"), "different names"),
        (Pairing(("a",), a_name="x:y"), "cannot contain"),
        (Pairing(("a",), a_name=" "), "Enter a role name"),
    ],
)
def test_pairing_problems(pairing: Pairing, problem: str) -> None:
    message = pairing.problem()
    assert message is not None and problem in message


def test_role_values_mean_ignores_missing_values() -> None:
    table = Table(
        "T",
        [
            Column.new("a1", "Y", [1, NAN, 3], id="a1"),
            Column.new("a2", "Y", [3, 4, NAN], id="a2"),
            Column.new("b1", "Y", [5, 6, 7], id="b1"),
            Column.new("b2", "Y", [8, 9, 10], id="b2"),
        ],
    )
    pairing = Pairing(("a1", "a2"), ("b1", "b2"), paired=True)
    values = role_values(table, pairing, 1)
    np.testing.assert_array_equal(values["a"], [3, 4, NAN])
    np.testing.assert_array_equal(values["a:mean"], [2, 4, 3])
    np.testing.assert_array_equal(values["b"], [8, 9, 10])
    assert set(role_values(table, pairing, 0, keys={"b"})) == {"b"}


# --- generated formulas -----------------------------------------------------------------


def blank_table() -> Table:
    return Table(
        "T",
        [Column.new("Blank", "Y", [1, 1], id="blank"), Column.new("Sig]", "Y", [2, 4], id="sig")],
    )


@pytest.mark.parametrize(
    ("kind", "params", "expected"),
    [
        (CalculationKind.BASELINE, {"reference": cf.FIRST}, "{A} - FIRST({A})"),
        (CalculationKind.BASELINE, {"reference": cf.VALUE, "value": 2}, "{A} - 2"),
        (CalculationKind.BASELINE, {"reference": cf.VALUE, "value": -0.5}, "{A} - (-0.5)"),
        (CalculationKind.BASELINE, {"reference": cf.COLUMN, "column": "blank"}, "{A} - [Blank]"),
        (
            CalculationKind.BASELINE,
            {"reference": cf.COLUMN_MEAN, "column": "sig"},
            "{A} - MEAN([Sig]]])",
        ),
        (CalculationKind.FOLD_CHANGE, {"reference": cf.FIRST}, "{A} / FIRST({A})"),
        (CalculationKind.FOLD_CHANGE, {"reference": cf.VALUE, "value": 2.5}, "{A} / 2.5"),
        (
            CalculationKind.NORMALIZE,
            {"range": cf.OWN_RANGE},
            "({A} - MIN({A})) / (MAX({A}) - MIN({A})) * 100",
        ),
        (
            CalculationKind.NORMALIZE,
            {"range": cf.VALUES_RANGE, "low": -5, "high": 95},
            "({A} - (-5)) / (95 - (-5)) * 100",
        ),
        (CalculationKind.BASELINE, {"reference": cf.COLUMN, "column": "gone"}, None),
        (CalculationKind.CUSTOM, {}, None),
    ],
)
def test_generated_formulas_unpaired(
    kind: CalculationKind, params: dict[str, object], expected: str | None
) -> None:
    formula = cf.generate(kind, params, Pairing(("sig",)), blank_table())  # type: ignore[arg-type]
    assert formula == expected
    if formula is not None:
        parse(formula)


@pytest.mark.parametrize(
    ("kind", "reference", "expected"),
    [
        (CalculationKind.BASELINE, cf.MATCHED, "{Drug} - {Ctrl}"),
        (CalculationKind.BASELINE, cf.ROLE_MEAN, "{Drug} - {Ctrl:mean}"),
        (CalculationKind.FOLD_CHANGE, cf.MATCHED, "{Drug} / {Ctrl}"),
        (CalculationKind.FOLD_CHANGE, cf.ROLE_MEAN, "{Drug} / {Ctrl:mean}"),
    ],
)
def test_generated_formulas_paired(kind: CalculationKind, reference: str, expected: str) -> None:
    pairing = Pairing(("a",), ("b",), paired=True, a_name="Ctrl", b_name="Drug")
    assert cf.generate(kind, {"reference": reference}, pairing, blank_table()) == expected


def test_default_params_make_a_formula_for_every_ready_made_kind() -> None:
    for kind in (CalculationKind.BASELINE, CalculationKind.NORMALIZE, CalculationKind.FOLD_CHANGE):
        assert cf.generate(kind, cf.default_params(kind), Pairing(("sig",)), blank_table())
    for kind in (CalculationKind.BASELINE, CalculationKind.FOLD_CHANGE):
        pairing = Pairing(("blank",), ("sig",), paired=True)
        assert cf.generate(kind, cf.default_params(kind, paired=True), pairing, blank_table())


# --- commands ---------------------------------------------------------------------------


def lab_document() -> tuple[Document, Table]:
    """Time, a replicate group "baseline" (3 columns) and a group "sample" (3 columns)."""
    columns = [Column.new("Time", "X", [0, 1, 2], id="t")]
    columns += [Column.new(f"baseline {i}", "Y", [i, i, i], id=f"a{i}") for i in (1, 2, 3)]
    columns += [Column.new(f"sample {i}", "Y", [10 * i] * 3, id=f"b{i}") for i in (1, 2, 3)]
    columns.append(Column.new("Other", "Y", [5, 6, 7], id="o"))
    table = Table("T", columns, id="T")
    doc = Document()
    doc.execute(AddTable(table))
    doc.execute(GroupColumns("T", ["a1", "a2", "a3"], "baseline"))
    doc.execute(GroupColumns("T", ["b1", "b2", "b3"], "sample"))
    return doc, table


def names(table: Table) -> list[str]:
    return [c.name for c in table.columns]


def test_add_calculation_places_columns_after_their_group_and_groups_them() -> None:
    doc, table = lab_document()
    pairing = Pairing(("a1", "a2", "a3"), ("b1", "b2", "b3"), paired=True)
    calc = Calculation("T", "{B} - {A}", pairing, CalculationKind.BASELINE, suffix="_bl")
    doc.execute(add_calculation(doc, calc))
    assert names(table) == [
        "Time",
        *(f"baseline {i}" for i in (1, 2, 3)),
        *(f"sample {i}" for i in (1, 2, 3)),
        *(f"sample {i}_bl" for i in (1, 2, 3)),
        "Other",
    ]
    (stored,) = doc.calculations
    assert [c.source_id for c in stored.columns] == ["b1", "b2", "b3"]
    outputs = [table.column(c.column_id) for c in stored.columns]
    assert all(np.isnan(c.values).all() for c in outputs)
    group = table.replicate_group(outputs[0].group_id or "")
    assert group.name == "sample_bl"
    assert doc.undo_stack.undo_text == "Calculate baseline subtraction for 3 columns"

    doc.undo()
    assert doc.calculations == [] and len(table.columns) == 8
    doc.redo()
    assert doc.calculations == [stored] and len(table.columns) == 11


def test_add_calculation_at_the_end_and_with_unique_names() -> None:
    doc, table = lab_document()
    doc.execute(RenameColumn("T", "o", "Other_calc"))
    calc = Calculation("T", "{A} * 2", Pairing(("o",)), append_at_end=True)
    doc.execute(add_calculation(doc, calc))
    assert names(table)[-2:] == ["Other_calc", "Other_calc_calc"]
    calc2 = Calculation("T", "{A} * 2", Pairing(("o",)), append_at_end=True)
    doc.execute(add_calculation(doc, calc2))
    assert names(table)[-1] == "Other_calc_calc (2)"


def test_add_calculation_rejects_bad_pairing_and_text_columns() -> None:
    doc, _table = lab_document()
    with pytest.raises(CommandError, match="same number"):
        pairing = Pairing(("a1", "a2"), ("b1", "b2", "b3"), paired=True)
        add_calculation(doc, Calculation("T", "{B}", pairing))
    doc.execute(SetColumnRole("T", "o", ColumnRole.TEXT))
    with pytest.raises(CommandError, match="holds text"):
        add_calculation(doc, Calculation("T", "{A}", Pairing(("o",))))


def one_calc(
    doc: Document, sources: tuple[str, ...] = ("o",), suffix: str = "_calc"
) -> Calculation:
    doc.execute(add_calculation(doc, Calculation("T", "{A} + 1", Pairing(sources), suffix=suffix)))
    return doc.calculations[-1]


def test_calculated_columns_cannot_be_written_or_made_text() -> None:
    doc, _table = lab_document()
    calc = one_calc(doc)
    output = calc.columns[0].column_id
    with pytest.raises(CommandError, match="is calculated"):
        doc.execute(SetCellValue("T", output, 0, 1.0))
    with pytest.raises(CommandError, match="cannot become a Text column"):
        doc.execute(SetColumnRole("T", output, ColumnRole.TEXT))
    doc.execute(SetColumnRole("T", output, ColumnRole.X))  # another numeric role is fine


def test_import_does_not_fill_calculated_columns() -> None:
    doc = Document()
    doc.execute(AddTable(Table("T", [Column.new("A", "Y", [1.0])], id="T")))
    doc.execute(
        add_calculation(doc, Calculation("T", "[nope]", Pairing((doc.tables[0].columns[0].id,))))
    )
    table = doc.tables[0]
    output = table.columns[1]
    doc.execute(SetCellValue("T", table.columns[0].id, 0, None))  # every column empty now
    doc.execute(ImportColumns("T", [Column.new("New", "Y", [7.0])], "f.csv"))
    assert table.column(output.id).name == "A_calc"
    assert doc.is_calculated(output.id)


def test_removing_a_calculated_column_updates_its_calculation() -> None:
    doc, _table = lab_document()
    calc = one_calc(doc, ("a1", "a2"))
    first, second = (c.column_id for c in calc.columns)
    doc.execute(RemoveColumn("T", first))
    assert [c.column_id for c in doc.calculations[0].columns] == [second]
    doc.execute(RemoveColumn("T", second))
    assert doc.calculations == []
    doc.undo()
    doc.undo()
    assert doc.calculations == [calc]


def test_removing_a_table_removes_its_calculations() -> None:
    doc, _table = lab_document()
    calc = one_calc(doc)
    doc.execute(RemoveTable("T"))
    assert doc.calculations == []
    doc.undo()
    assert doc.calculations == [calc]


def test_renames_rewrite_formulas_and_undo_restores_them() -> None:
    doc, _table = lab_document()
    doc.execute(add_calculation(doc, Calculation("T", "[Other] - [Time] + {A}", Pairing(("o",)))))
    doc.execute(RenameColumn("T", "o", "Signal"))
    assert doc.calculations[0].formula == "[Signal] - [Time] + {A}"
    doc.execute(BulkRename("T", columns={"o": "Time", "t": "Signal"}))
    assert doc.calculations[0].formula == "[Time] - [Signal] + {A}"
    doc.undo()
    doc.undo()
    assert doc.calculations[0].formula == "[Other] - [Time] + {A}"


def test_edit_calculation_keeps_renames_removes_and_adds_columns() -> None:
    doc, table = lab_document()
    calc = one_calc(doc, ("a1", "a2"))
    kept_id = calc.columns[0].column_id
    dropped_id = calc.columns[1].column_id
    doc.execute(RenameColumn("T", dropped_id, "custom name"))
    new = Calculation(
        "T", "{A} * 3", Pairing(("a1", "o")), CalculationKind.CUSTOM, suffix="_x", id=calc.id
    )
    doc.execute(edit_calculation(doc, new))
    (stored,) = doc.calculations
    assert stored.formula == "{A} * 3"
    assert stored.columns[0] == CalculatedColumn(kept_id, "a1")
    assert table.column(kept_id).name == "baseline 1_x"
    assert dropped_id not in {c.id for c in table.columns}
    added = table.column(stored.columns[1].column_id)
    assert added.name == "Other_x"
    assert names(table)[-2:] == ["Other", "Other_x"]
    doc.undo()
    assert doc.calculations == [calc]
    assert table.column(dropped_id).name == "custom name"


def test_set_calculation_validates() -> None:
    doc, _table = lab_document()
    with pytest.raises(CommandError, match="not in the table"):
        doc.execute(
            SetCalculation(
                Calculation("T", "1", Pairing(("o",)), columns=(CalculatedColumn("zz", "o"),))
            )
        )
    with pytest.raises(CommandError, match="own inputs"):
        doc.execute(
            SetCalculation(
                Calculation("T", "1", Pairing(("o",)), columns=(CalculatedColumn("o", "o"),))
            )
        )


# --- constants --------------------------------------------------------------------------


def test_constant_commands_validate_and_undo() -> None:
    doc = Document()
    k = ProjectConstant("k_on", 2.0, "1/s")
    doc.execute(AddConstant(k))
    with pytest.raises(CommandError, match="already a project constant"):
        doc.execute(AddConstant(ProjectConstant("k_on", 3.0)))
    with pytest.raises(CommandError, match="built-in"):
        doc.execute(AddConstant(ProjectConstant("R", 3.0)))
    edit = EditConstant(k.id, "k_off", 5.0, "")
    doc.execute(edit)
    assert edit.touched_nodes == (constant_node("k_on"), constant_node("k_off"))
    assert doc.constants[0].name == "k_off" and doc.constants[0].value == 5.0
    with pytest.raises(CommandError, match="finite"):
        doc.execute(EditConstant(k.id, "k_off", math.inf))
    doc.execute(RemoveConstant(k.id))
    assert doc.constants == []
    doc.undo()
    doc.undo()
    assert doc.constants == [k]


# --- project files ----------------------------------------------------------------------


def test_calculations_round_trip_through_a_project_file(tmp_path: Path) -> None:
    doc, _table = lab_document()
    pairing = Pairing(("a1", "a2", "a3"), ("b1", "b2", "b3"), paired=True, a_name="C", b_name="D")
    calc = Calculation(
        "T", "{D} - {C:mean}", pairing, CalculationKind.BASELINE, {"reference": "role_mean"}, "_bl"
    )
    doc.execute(add_calculation(doc, calc))
    save(doc, tmp_path / "p.cvp")
    loaded = load(tmp_path / "p.cvp")
    assert loaded.calculations == doc.calculations


def test_version_2_files_get_no_calculations(tmp_path: Path) -> None:
    path = tmp_path / "v2.cvp"
    save(Document(tables=[Table("T", [Column.new("A", "Y", [1.0])])]), path)
    with zipfile.ZipFile(path) as zf:
        files = {name: zf.read(name) for name in zf.namelist()}
    data = json.loads(files[PROJECT_JSON])
    data["schema_version"] = 2
    del data["document"]["calculations"]
    files[PROJECT_JSON] = json.dumps(data).encode()
    with zipfile.ZipFile(path, "w") as zf:
        for name, raw in files.items():
            zf.writestr(name, raw)
    assert load(path).calculations == []


def test_edit_keeps_the_column_of_a_deleted_source_as_an_error_column() -> None:
    doc, table = lab_document()
    calc = one_calc(doc, ("a1", "a2"))
    orphan = calc.columns[1]
    doc.execute(RemoveColumn("T", "a2"))
    edited = Calculation("T", "{A} * 3", Pairing(("a1",)), suffix="_x", id=calc.id)
    doc.execute(edit_calculation(doc, edited))
    (stored,) = doc.calculations
    assert stored.columns == (calc.columns[0], orphan)
    assert table.column(orphan.column_id).name == "baseline 2_calc"  # not renamed
    assert stored.target_index(orphan) == -1
