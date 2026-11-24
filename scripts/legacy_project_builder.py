"""Build a sample project with an *older* Curvis and describe what it holds.

Not run directly: ``scripts/make_legacy_projects.py`` runs it once per file format, with
``sys.path`` pointing at the ``src`` folder of the last commit that wrote that format.
It must therefore use only the API that every one of those commits shares, and detect
the rest (category groups from format 2, calculations from 3, analyses from 4, graphs
from 5, and so on).

    python legacy_project_builder.py <out.cvp> <out.json>

The project gets every feature the old build has, with values that are easy to check:
missing values, a text column, units, replicate groups, a category factor, a
calculation, one analysis of every type that can run on its table (some with a changed
parameter) and a graph of every chart family. The JSON manifest records what the old
build had in memory when it saved, so the test can check that the current build reads
back the same thing.
"""

from __future__ import annotations

import dataclasses
import json
import math
import sys
from pathlib import Path
from typing import Any

from curvis.core.commands import RenameColumn
from curvis.core.constants import ProjectConstant
from curvis.core.document import Document
from curvis.data.column import Column
from curvis.data.groups import CategoryGroup, ReplicateGroup
from curvis.data.roles import ColumnRole
from curvis.data.table import Table
from curvis.io import project

NAN = math.nan


def has_field(cls: type, name: str) -> bool:
    return any(f.name == name for f in dataclasses.fields(cls))


def column(name: str, role: ColumnRole, values: list[Any], **extra: Any) -> Column:
    extra = {k: v for k, v in extra.items() if v is not None}
    if "category_id" in extra and not has_field(Column, "category_id"):
        del extra["category_id"]
    return Column.new(name, role, values, **extra)


def dose_response_table() -> Table:
    """X plus two replicate groups of three Y columns, with a missing value, a unit, an
    Error column and a Text column."""
    doses = [0.001, 0.01, 0.1, 1.0, 10.0, 100.0, 1000.0, 10000.0]
    ctrl = ReplicateGroup("Control", id="g_ctrl")
    drug = ReplicateGroup("Drug", id="g_drug")
    curve = [4.0, 6.5, 14.0, 37.0, 66.0, 88.0, 95.5, 98.0]
    columns = [column("Dose", ColumnRole.X, doses, unit="nM", id="x")]
    for i, offset in enumerate((0.0, 1.5, -1.2)):
        values = [v + offset for v in curve]
        if i == 1:
            values[3] = NAN  # a missing value
        columns.append(column(f"Drug {i + 1}", ColumnRole.Y, values, group_id=drug.id, id=f"d{i}"))
    for i, offset in enumerate((0.0, 0.8, -0.6)):
        values = [5.0 + offset + 0.1 * k for k in range(len(doses))]
        columns.append(column(f"Ctrl {i + 1}", ColumnRole.Y, values, group_id=ctrl.id, id=f"c{i}"))
    columns.append(column("Single", ColumnRole.Y, [2.0 + 3 * k for k in range(8)], id="single"))
    columns.append(column("Single SD", ColumnRole.ERROR, [0.5] * 8, id="single_sd"))
    columns.append(
        column("Notes", ColumnRole.TEXT, ["", "plate 1", "", "", "redo", "", "", "µ ±"], id="notes")
    )
    return Table("Dose response", columns, replicate_groups=[drug, ctrl], id="t_dose")


def groups_table() -> Table:
    """Three unequal groups as Y columns, for t-tests and one-way comparisons."""
    a = [10.1, 11.4, 9.8, 10.7, 12.0, 10.9]
    b = [12.3, 13.1, 12.8, 14.0, 13.6, NAN]
    c = [9.0, 8.7, 9.9, 9.4, NAN, NAN]
    return Table(
        "Groups",
        [
            column("Control", ColumnRole.Y, a, id="ga"),
            column("Treated", ColumnRole.Y, b, id="gb"),
            column("Vehicle", ColumnRole.Y, c, id="gc"),
        ],
        id="t_groups",
    )


def two_way_table() -> Table | None:
    """Replicate groups in two categories (formats that store the category factor)."""
    if not has_field(ReplicateGroup, "category_id"):
        return None
    male = CategoryGroup("Male", id="cat_m")
    female = CategoryGroup("Female", id="cat_f")
    groups = [
        ReplicateGroup("WT", category_id=male.id, id="rg_mwt"),
        ReplicateGroup("KO", category_id=male.id, id="rg_mko"),
        ReplicateGroup("WT", category_id=female.id, id="rg_fwt"),
        ReplicateGroup("KO", category_id=female.id, id="rg_fko"),
    ]
    base = {"rg_mwt": 20.0, "rg_mko": 26.0, "rg_fwt": 18.0, "rg_fko": 30.0}
    columns = []
    for gi, g in enumerate(groups):
        for r in range(3):
            # small fixed "noise", so no two rows differ by the same amount everywhere
            values = [
                base[g.id] + r * 0.7 + k * 0.3 + ((7 * r + 3 * k * (gi + 1)) % 5) * 0.21
                for k in range(4)
            ]
            columns.append(
                column(
                    f"{g.id[3]} {g.name} r{r + 1}",
                    ColumnRole.Y,
                    values,
                    group_id=g.id,
                    id=f"{g.id}_{r}",
                )
            )
    kwargs: dict[str, Any] = {"replicate_groups": groups, "category_groups": [male, female]}
    if has_field(Table, "category_factor"):
        kwargs["category_factor"] = "Sex"
    return Table("Two-way", columns, id="t_two_way", **kwargs)


def survival_table() -> Table:
    return Table(
        "Survival",
        [
            column(
                "Days", ColumnRole.TIME, [3, 5, 5, 8, 12, 15, 20, 21, 30, 34], unit="d", id="s_time"
            ),
            column("Placebo", ColumnRole.EVENT, [1, 1, 0, 1, 1, NAN, NAN, NAN, NAN, NAN], id="s_a"),
            column("Drug", ColumnRole.EVENT, [NAN, NAN, NAN, NAN, NAN, 1, 0, 1, 0, 1], id="s_b"),
        ],
        id="t_survival",
    )


def add_calculation(document: Document) -> None:
    try:
        from curvis.core.calculation_commands import add_calculation as add
        from curvis.core.calculations import Calculation
        from curvis.data.pairing import Pairing
    except ImportError:
        return
    calc = Calculation("t_groups", "{A} * 2", Pairing(("ga",)), suffix="_x2")
    document.execute(add(document, calc))


def changed_params(analysis_type: Any, table: Table, inputs: tuple[str, ...]) -> Any:
    """Default parameters with one setting changed where the analysis still accepts it,
    so the test sees that saved parameters are read back, not replaced by defaults."""
    from curvis.analysis.base import param_fields

    params = analysis_type.default_params()
    for f, info in param_fields(type(params)):
        value = getattr(params, f.name)
        if isinstance(value, bool):
            candidate = dataclasses.replace(params, **{f.name: not value})
        elif info.choices and len(info.choices) > 1:
            other = next(v for v, _ in info.choices if v != value)
            candidate = dataclasses.replace(params, **{f.name: other})
        else:
            continue
        try:
            if analysis_type.check(table, inputs, candidate) is None:
                return candidate
        except Exception:
            continue
    return params


def add_analyses(document: Document) -> None:
    try:
        from curvis.analysis import base
    except ImportError:
        return
    if not has_field(Document, "analyses"):
        return
    base.load_builtin()
    for table in document.tables:
        for i, analysis_type in enumerate(base.runnable(table)):
            inputs = tuple(analysis_type.default_inputs(table))
            params = analysis_type.default_params()
            if i % 2 == 0:
                params = changed_params(analysis_type, table, inputs)
            if analysis_type.check(table, inputs, params) is not None:
                continue
            document.analyses.append(
                base.Analysis(
                    analysis_type.id,
                    table.id,
                    inputs,
                    params,
                    id=f"a_{table.id}_{analysis_type.id}",
                )
            )


def add_graphs(document: Document) -> None:
    if not has_field(Document, "graphs"):
        return
    from curvis.charts.builder import default_spec
    from curvis.charts.spec import ChartFamily

    for table in document.tables:
        analyses = [a for a in document.analyses if a.table_id == table.id]
        for family in ChartFamily:
            try:
                spec = default_spec(table, family, analyses)
            except Exception:
                continue
            if spec is None:
                continue
            spec = dataclasses.replace(
                spec, title=f"{table.name}: {family.value}", id=f"g_{table.id}_{family.value}"
            )
            document.graphs.append(spec)


def build() -> Document:
    tables = [dose_response_table(), groups_table()]
    two_way = two_way_table()
    if two_way is not None:
        tables.append(two_way)
    tables.append(survival_table())
    document = Document(
        name="Legacy sample",
        tables=tables,
        constants=[ProjectConstant("k_on", 1.25e5, "1/(M*s)", id="k_on")],
        settings={"note": "saved by an older Curvis"},
    )
    document.execute(RenameColumn("t_groups", "gc", "Vehicle only"))
    add_calculation(document)
    add_analyses(document)
    add_graphs(document)
    return document


# --- manifest ------------------------------------------------------------------------------


def plain(value: Any) -> Any:
    if isinstance(value, float):
        return None if math.isnan(value) else value
    if hasattr(value, "item"):
        return plain(value.item())
    return value


def describe(document: Document) -> dict[str, Any]:
    from curvis import __version__

    calculated: set[str] = set()
    calculations = []
    for calc in getattr(document, "calculations", []):
        calculations.append(
            {
                "table_id": calc.table_id,
                "formula": calc.formula,
                "columns": [[c.column_id, c.source_id] for c in calc.columns],
            }
        )
        calculated |= {c.column_id for c in calc.columns}
    tables = []
    for table in document.tables:
        tables.append(
            {
                "id": table.id,
                "name": table.name,
                "category_factor": getattr(table, "category_factor", None),
                "columns": [
                    {
                        "id": c.id,
                        "name": c.name,
                        "role": c.role.value,
                        "unit": c.unit,
                        "group_id": c.group_id,
                        "category_id": getattr(c, "category_id", None),
                        "values": None
                        if c.id in calculated
                        else [plain(v) for v in c.values.tolist()],
                    }
                    for c in table.columns
                ],
                "replicate_groups": [
                    {"id": g.id, "name": g.name, "category_id": getattr(g, "category_id", None)}
                    for g in table.replicate_groups
                ],
                "category_groups": [{"id": g.id, "name": g.name} for g in table.category_groups],
            }
        )
    analyses = []
    if getattr(document, "analyses", None):
        from curvis.analysis.base import params_to_json

        analyses = [
            {
                "id": a.id,
                "type": a.type,
                "table_id": a.table_id,
                "inputs": list(a.inputs),
                "params": params_to_json(a.params),
            }
            for a in document.analyses
        ]
    graphs = [
        {
            "id": g.id,
            "table_id": g.table_id,
            "family": g.family.value,
            "variant": g.variant.value,
            "title": g.title,
            "series": [s.source for s in g.series],
            "overlays": len(getattr(g, "overlays", ())),
        }
        for g in getattr(document, "graphs", [])
    ]
    return {
        "schema_version": project.SCHEMA_VERSION,
        "app_version": __version__,
        "name": document.name,
        "tables": tables,
        "constants": [
            {"name": c.name, "value": c.value, "unit": c.unit} for c in document.constants
        ],
        "settings": document.settings,
        "calculations": calculations,
        "analyses": analyses,
        "graphs": graphs,
        "change_log": [[e.command_type, e.summary] for e in document.change_log],
    }


def main() -> None:
    out, manifest = Path(sys.argv[1]), Path(sys.argv[2])
    document = build()
    project.save(document, out)
    # what this build reads back is what it saved (a check of the old build, not ours)
    project.load(out)
    manifest.write_text(
        json.dumps(describe(document), indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
