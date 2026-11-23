"""Prism import: the data tables of a GraphPad Prism .pzfx or .prism file.

Both formats are read into the same plain description (:class:`PrismTable`), which
:func:`build_table` turns into a Curvis table. Only data tables are read: analyses,
graphs, layouts and their styling are not imported.

**.pzfx** is XML. ``TableSequence`` lists the data tables (``Table`` or ``HugeTable``
elements); each has a ``Title``, a ``TableType`` (XY, OneWay, TwoWay, Survival, …), an
``XFormat``, a ``YFormat`` (replicates, SD, SDN, SEN, …), and the columns
``RowTitlesColumn``, ``XColumn`` (or ``XAdvancedColumn``) and ``YColumn``. Every column
holds ``Subcolumn`` elements of ``d`` cells; ``<d Excluded="1">`` marks an excluded
value. The open-source R package *pzfx* documents the same structure.

**.prism** (Prism 10) is a zip. ``document.json`` lists the data sheets;
``data/sheets/<id>/sheet.json`` holds the table: its ``format`` (xy, column, grouped,
survival, …), its ``dataFormat`` (y_single, y_replicates, y_sd_n, y_se_n, …) and its
data sets (``rowTitlesDataSet``, ``xDataSet``, ``dataSets``), each described by
``data/sets/<id>.json`` with a title and one entry per subcolumn. The cells are in
``data/tables/<table id>/data.csv``: one CSV column per subcolumn, in the order row
titles, X, then the data sets as listed. (Each subcolumn's first and last filled row,
stored with the data set, match this order in every file we have.)

Mapping to Curvis (see :func:`build_table`):

- XY: an X column, then each data set as a Y column, or a replicate group of Y columns
  "name 1", "name 2", … when it has replicates.
- Column: each data set as a Y column.
- Grouped, with replicate values: each data set is a category, and each row a Y column
  in it named by its row title, holding that row's replicates down the rows; so the
  table is ready for two-way ANOVA. A grouped table with one row and no row titles
  imports like a column table.
- Mean with SD or SEM (with or without N): the mean as a Y column, the SD or SEM as
  its Error column and N as a Text column. They are never put in categories, which
  hold only Y columns. %CV, ± errors and upper/lower limits have no Error column in
  Curvis and are kept as Text columns.
- Survival: X as the Time column and each data set as an Event column.
- Row titles become a Text column on the left (in grouped tables, the column names).
- Excluded values are left empty, as Prism's analyses leave them out. Their values and
  places are kept (:class:`ExcludedValue`) for a later version that can exclude values.

Other table types (contingency, parts of whole, multiple variables, nested) are listed
as not imported. Nothing here uses Qt, so :func:`read_prism` can run in a worker
process.
"""

from __future__ import annotations

import csv
import io
import json
import math
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from curvis.data import CategoryGroup, Column, ColumnRole, ReplicateGroup, Table
from curvis.data.column import format_number
from curvis.data.names import unique_name

PRISM_SUFFIXES = (".pzfx", ".prism")

NOT_IMPORTED_NOTE = "Analyses, graphs and their styling are not imported, only data tables."

KIND_LABELS = {
    "xy": "XY",
    "column": "Column",
    "grouped": "Grouped",
    "survival": "Survival",
    "contingency": "Contingency",
    "parts": "Parts of whole",
    "multiple": "Multiple variables",
    "nested": "Nested",
}
IMPORTED_KINDS = ("xy", "column", "grouped", "survival")

# Y formats, normalised: what the subcolumns of each data set hold.
REPLICATES = "replicates"
SUMMARY_FORMATS: dict[str, tuple[str, ...]] = {
    # format: names of the subcolumns after the mean
    "sd": ("SD",),
    "se": ("SEM",),
    "cv": ("%CV",),
    "sd_n": ("SD", "N"),
    "se_n": ("SEM", "N"),
    "cv_n": ("%CV", "N"),
    "low_high": ("plus error", "minus error"),
    "upper_lower": ("upper limit", "lower limit"),
}
ERROR_SUBCOLUMNS = ("SD", "SEM")
"""Subcolumns that become an Error column; the others become Text columns."""

FORMAT_LABELS = {
    "sd": "mean and SD",
    "se": "mean and SEM",
    "cv": "mean and %CV",
    "sd_n": "mean, SD and N",
    "se_n": "mean, SEM and N",
    "cv_n": "mean, %CV and N",
    "low_high": "mean with plus and minus errors",
    "upper_lower": "mean with upper and lower limits",
}

_PZFX_KINDS = {
    "xy": "xy",
    "oneway": "column",
    "twoway": "grouped",
    "survival": "survival",
    "contingency": "contingency",
    "partsofwhole": "parts",
    "multiplevariables": "multiple",
    "nested": "nested",
}
_PZFX_FORMATS = {
    "replicates": REPLICATES,
    "sd": "sd",
    "se": "se",
    "sem": "se",
    "cv": "cv",
    "sdn": "sd_n",
    "sen": "se_n",
    "semn": "se_n",
    "cvn": "cv_n",
    "low-high": "low_high",
    "upper-lower-limits": "upper_lower",
}
_PRISM_KINDS = {
    "xy": "xy",
    "column": "column",
    "grouped": "grouped",
    "survival": "survival",
    "contingency": "contingency",
    "parts_of_whole": "parts",
    "multiple_variables": "multiple",
    "nested": "nested",
}
_PRISM_FORMATS = {
    "y_single": REPLICATES,
    "y_replicates": REPLICATES,
    "y_sd": "sd",
    "y_se": "se",
    "y_cv": "cv",
    "y_sd_n": "sd_n",
    "y_se_n": "se_n",
    "y_cv_n": "cv_n",
    "y_low_high": "low_high",
    "y_upper_lower": "upper_lower",
    "y_upper_lower_limits": "upper_lower",
}


class PrismImportError(ValueError):
    """A Prism file that cannot be read. The message is meant for the user."""


type Cell = str | None
"""A cell as text: ``None`` when empty or excluded."""


@dataclass(frozen=True)
class DataSet:
    """One Prism column (a data set): its title and its subcolumns of cells."""

    title: str
    subcolumns: tuple[tuple[Cell, ...], ...]


@dataclass(frozen=True)
class ExcludedValue:
    """A value Prism marks as excluded, kept so a later version can import it as such.

    ``data_set`` is the index in :attr:`PrismTable.data_sets`, or ``X_DATA_SET`` for
    the X column; ``subcolumn`` and ``row`` count from 0, as in the Prism table, and
    ``value`` is the cell's text without Prism's marker. Until Curvis can exclude
    values, these cells import empty.
    """

    data_set: int
    subcolumn: int
    row: int
    value: str


X_DATA_SET = -1
"""``ExcludedValue.data_set`` for a value in the X column."""


@dataclass(frozen=True)
class PrismTable:
    """One Prism data table as read from the file, before it becomes a Curvis table.

    ``kind`` is one of :data:`KIND_LABELS` (or the file's own name for an unknown
    kind), ``y_format`` :data:`REPLICATES` or a key of :data:`SUMMARY_FORMATS` (or the
    file's own name). ``excluded`` lists the values Prism marks as excluded; their
    cells are empty (``None``) in the subcolumns.
    """

    title: str
    kind: str
    y_format: str
    row_titles: tuple[Cell, ...] | None
    x: DataSet | None
    data_sets: tuple[DataSet, ...]
    excluded: tuple[ExcludedValue, ...] = ()


@dataclass(frozen=True)
class ImportedTable:
    """A Prism table that imports: the Curvis table, and notes for the user.

    ``excluded`` holds the values Prism marks as excluded, by their place in the Prism
    table (they are empty in ``table``).
    """

    title: str
    kind: str
    table: Table
    notes: tuple[str, ...] = ()
    excluded: tuple[ExcludedValue, ...] = ()

    @property
    def summary(self) -> str:
        """One line for the import dialog: "XY, 2 series, 10 rows"."""
        return table_summary(self.kind, self.table)


@dataclass(frozen=True)
class SkippedTable:
    """A Prism table that does not import, and why (one sentence)."""

    title: str
    kind: str
    reason: str


@dataclass(frozen=True)
class PrismData:
    """Every data table of a Prism file, in the file's order."""

    tables: tuple[ImportedTable, ...]
    skipped: tuple[SkippedTable, ...] = field(default=())


# --- public API ------------------------------------------------------------------------


def is_prism(path: str | Path) -> bool:
    """True for a .pzfx or .prism file (by its extension)."""
    return Path(path).suffix.lower() in PRISM_SUFFIXES


def read_prism(path: str | Path) -> PrismData:
    """Read the data tables of the Prism file at ``path``.

    Raises :class:`PrismImportError` when the file cannot be read at all; a single
    table that cannot be imported is listed in :attr:`PrismData.skipped` instead.
    """
    raw = read_tables(path)
    imported: list[ImportedTable] = []
    skipped: list[SkippedTable] = []
    taken: list[str] = []
    for table in raw:
        result = build_table(table)
        if isinstance(result, SkippedTable):
            skipped.append(result)
            continue
        name = unique_name(result.table.name, taken)
        taken.append(name)
        result.table.name = name
        imported.append(result)
    if not raw:
        raise PrismImportError("The file has no data tables.")
    return PrismData(tuple(imported), tuple(skipped))


def read_tables(path: str | Path) -> list[PrismTable]:
    """The data tables of a .pzfx or .prism file, as written in the file."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PrismImportError(f"The file could not be opened: {exc.strerror or exc}") from None
    if path.suffix.lower() == ".prism" or data[:4] == b"PK\x03\x04":
        return _read_prism_zip(data)
    return _read_pzfx(data)


def table_summary(kind: str, table: Table) -> str:
    """ "XY, 2 series, 10 rows": the kind, how many series or curves, and the rows."""
    label = KIND_LABELS.get(kind, kind)
    if kind == "survival":
        count = sum(c.role is ColumnRole.EVENT for c in table.columns)
        what = "curve" if count == 1 else "curves"
    else:
        from curvis.data import series

        count = len(series(table))
        what = "series"
    rows = table.n_rows
    return f"{label}, {count} {what}, {rows} row{'s' if rows != 1 else ''}"


# --- .pzfx -----------------------------------------------------------------------------


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if _local(child.tag) == name]


def _text(element: ET.Element | None) -> str:
    """All text inside ``element`` (titles may hold formatting tags), trimmed."""
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def _read_pzfx(data: bytes) -> list[PrismTable]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise PrismImportError(f"This is not a Prism file that can be read ({exc}).") from None
    if _local(root.tag) != "GraphPadPrismFile":
        raise PrismImportError("This is not a Prism file.")
    tables = {
        element.get("ID"): element
        for element in root
        if _local(element.tag) in ("Table", "HugeTable")
    }
    order: list[str | None] = []
    for sequence in _children(root, "TableSequence"):
        order += [ref.get("ID") for ref in _children(sequence, "Ref")]
    if not order:
        order = list(tables)
    return [_pzfx_table(tables[key]) for key in order if key in tables]


def _pzfx_table(element: ET.Element) -> PrismTable:
    title = _text(next(iter(_children(element, "Title")), None)) or "Data"
    table_type = (element.get("TableType") or "").lower()
    kind = _PZFX_KINDS.get(table_type, table_type or "column")
    if not table_type:  # older files: XY when there is an X column
        kind = "xy" if (element.get("XFormat") or "none") != "none" else "column"
    y_format_name = (element.get("YFormat") or "replicates").lower()
    y_format = _PZFX_FORMATS.get(y_format_name, y_format_name)
    excluded: list[ExcludedValue] = []

    def subcolumns(column: ET.Element, where: int | None) -> tuple[tuple[Cell, ...], ...]:
        result = []
        for s, sub in enumerate(_children(column, "Subcolumn")):
            cells: list[Cell] = []
            for r, d in enumerate(_children(sub, "d")):
                value = _text(d)
                marked = d.get("Excluded") == "1" or value.endswith("*")
                if marked and where is not None:
                    if value.rstrip("*").strip():
                        excluded.append(ExcludedValue(where, s, r, value.rstrip("*").strip()))
                    cells.append(None)
                else:
                    cells.append(value or None)
            result.append(tuple(cells))
        return tuple(result)

    def data_set(column: ET.Element, where: int) -> DataSet:
        title = _text(next(iter(_children(column, "Title")), None))
        return DataSet(title, subcolumns(column, where))

    row_titles: tuple[Cell, ...] | None = None
    for column in _children(element, "RowTitlesColumn"):
        subs = subcolumns(column, None)
        row_titles = subs[0] if subs else ()
    x_columns = _children(element, "XColumn") or _children(element, "XAdvancedColumn")
    x = data_set(x_columns[0], X_DATA_SET) if x_columns else None
    y_columns = [c for c in element if _local(c.tag) in ("YColumn", "YAdvancedColumn")]
    data_sets = [data_set(c, i) for i, c in enumerate(y_columns)]
    return PrismTable(title, kind, y_format, row_titles, x, tuple(data_sets), tuple(excluded))


# --- .prism ----------------------------------------------------------------------------


def _title(value: Any) -> str:
    """A title: plain text, or rich text ``{"string": …}``."""
    if isinstance(value, dict):
        value = value.get("string", "")
    return " ".join(str(value or "").split())


def _read_prism_zip(data: bytes) -> list[PrismTable]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PrismImportError("This is not a Prism file that can be read.") from None
    with archive:
        names = set(archive.namelist())

        def load(name: str) -> Any:
            if name not in names:
                raise PrismImportError(f"The file is damaged: '{name}' is missing.")
            try:
                return json.loads(archive.read(name))
            except (ValueError, UnicodeDecodeError):
                raise PrismImportError(f"The file is damaged: '{name}' cannot be read.") from None

        document = load("document.json")
        if not isinstance(document, dict):
            raise PrismImportError("This is not a Prism file.")
        attributes = document.get("sheetAttributesMap", {}) or {}
        tables: list[PrismTable] = []
        for sheet_id in (document.get("sheets", {}) or {}).get("data", []) or []:
            sheet = load(f"data/sheets/{sheet_id}/sheet.json")
            fallback = _title((attributes.get(sheet_id) or {}).get("title"))
            tables.append(_prism_table(sheet, fallback, load, archive, names))
        return tables


def _prism_table(
    sheet: dict[str, Any], fallback: str, load: Any, archive: zipfile.ZipFile, names: set[str]
) -> PrismTable:
    table = sheet.get("table") or {}
    title = _title(sheet.get("title")) or fallback or "Data"
    raw_kind = str(table.get("format", "")).lower()
    kind = _PRISM_KINDS.get(raw_kind, raw_kind or "column")
    raw_format = str(table.get("dataFormat", "y_single")).lower()
    y_format = _PRISM_FORMATS.get(raw_format, raw_format)
    replicates = int(table.get("replicatesCount", 1) or 1)

    sets: list[tuple[str, dict[str, Any]]] = []
    if table.get("rowTitlesDataSet"):
        sets.append(("rows", load(f"data/sets/{table['rowTitlesDataSet']}.json")))
    if table.get("xDataSet"):
        sets.append(("x", load(f"data/sets/{table['xDataSet']}.json")))
    sets += [("y", load(f"data/sets/{d}.json")) for d in table.get("dataSets", []) or []]
    widths = [_subcolumn_count(s, replicates) for _, s in sets]

    csv_name = f"data/tables/{table.get('uid')}/data.csv"
    rows: list[list[str]] = []
    if csv_name in names:
        text = archive.read(csv_name).decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text, newline="")))
    width = max((len(r) for r in rows), default=0)
    if rows and width != sum(widths):
        return PrismTable(title, f"damaged:{width}:{sum(widths)}", y_format, None, None, ())
    excluded: list[ExcludedValue] = []

    def column(index: int, where: int | None, subcolumn: int) -> tuple[Cell, ...]:
        cells: list[Cell] = []
        for r, row in enumerate(rows):
            value = row[index].strip() if index < len(row) else ""
            if value.endswith("*") and where is not None:
                if value.rstrip("*").strip():
                    excluded.append(ExcludedValue(where, subcolumn, r, value.rstrip("*").strip()))
                value = ""
            cells.append(value or None)
        return tuple(cells)

    row_titles: tuple[Cell, ...] | None = None
    x: DataSet | None = None
    data_sets: list[DataSet] = []
    start = 0
    for (role, data_set), count in zip(sets, widths, strict=True):
        where = None if role == "rows" else X_DATA_SET if role == "x" else len(data_sets)
        subs = tuple(column(start + i, where, i) for i in range(count))
        start += count
        if role == "rows":
            row_titles = subs[0] if subs else ()
        elif role == "x":
            x = DataSet(_title(data_set.get("title")), subs)
        else:
            data_sets.append(DataSet(_title(data_set.get("title")), subs))
    return PrismTable(title, kind, y_format, row_titles, x, tuple(data_sets), tuple(excluded))


def _subcolumn_count(data_set: dict[str, Any], replicates: int) -> int:
    """How many subcolumns (CSV columns) a data set has."""
    if data_set.get("replicates"):
        return len(data_set["replicates"])
    ranges = data_set.get("replicate ranges")
    if ranges:
        last = 0
        for entry in ranges:
            text = str(entry.get("range", "0"))
            last = max(last, int(text.split("~")[-1]) + 1)
        return max(last, replicates if data_set.get("format") == "y_replicates" else last)
    return replicates if data_set.get("format") == "y_replicates" else 1


# --- building Curvis tables ------------------------------------------------------------


class _Builder:
    """Collects the columns and groups of one table, keeping full names unique."""

    def __init__(self, n_rows: int) -> None:
        self.n_rows = n_rows
        self.columns: list[Column] = []
        self.groups: list[ReplicateGroup] = []
        self.categories: list[CategoryGroup] = []
        self._full_names: list[str] = []
        self._short: dict[str | None, list[str]] = {}

    def add(
        self,
        name: str,
        role: ColumnRole,
        values: Sequence[Any],
        *,
        group_id: str | None = None,
        category: CategoryGroup | None = None,
    ) -> Column:
        taken = self._short.setdefault(category.id if category else None, [])
        if category is None:
            name = unique_name(name, self._full_names)
            full = name
        else:
            name = unique_name(name, taken)
            full = f"{category.name}: {name}"
        taken.append(name)
        self._full_names.append(full)
        column = Column.new(
            name,
            role,
            _padded(values, self.n_rows, role),
            group_id=group_id,
            category_id=category.id if category else None,
        )
        self.columns.append(column)
        return column

    def category(self, name: str) -> CategoryGroup:
        category = CategoryGroup(unique_name(name, [c.name for c in self.categories]))
        self.categories.append(category)
        return category

    def table(self, name: str) -> Table:
        table = Table(name, self.columns, self.groups, self.categories)
        table.check()
        return table


def _padded(values: Sequence[Any], n: int, role: ColumnRole) -> Any:
    values = list(values)[:n]
    values += [None] * (n - len(values))
    if role.is_numeric:
        return np.array([math.nan if v is None else v for v in values], dtype=np.float64)
    array = np.empty(n, dtype=object)
    array[:] = values
    return array


def _number(cell: Cell) -> float | None:
    """The number in ``cell`` (Prism writes a decimal point), or None if it is not one."""
    if cell is None:
        return math.nan
    try:
        return float(cell)
    except ValueError:
        return None


def _numbers(cells: Sequence[Cell]) -> list[float] | None:
    """The cells as numbers (NaN when empty), or None when one is not a number."""
    result: list[float] = []
    for cell in cells:
        number = _number(cell)
        if number is None:
            return None
        result.append(number)
    return result


def _text_values(cells: Sequence[Cell]) -> list[str | None]:
    return [cell for cell in cells]


def _n_rows(table: PrismTable) -> int:
    """Rows up to the last one with a value or a row title."""
    lengths = [0]
    columns: list[tuple[Cell, ...]] = []
    if table.row_titles is not None:
        columns.append(table.row_titles)
    if table.x is not None:
        columns += table.x.subcolumns
    for data_set in table.data_sets:
        columns += data_set.subcolumns
    for cells in columns:
        filled = [i for i, cell in enumerate(cells) if cell is not None]
        if filled:
            lengths.append(filled[-1] + 1)
    return max(lengths)


def _set_name(data_set: DataSet, index: int) -> str:
    """The data set's title, or "Data set B" (Prism's letter for it) when it has none."""
    from curvis.data.names import column_letters

    return data_set.title or f"Data set {column_letters(index)}"


def build_table(raw: PrismTable) -> ImportedTable | SkippedTable:
    """The Curvis table for one Prism table, or why it does not import."""
    if raw.kind.startswith("damaged:"):
        _, found, expected = raw.kind.split(":")
        return SkippedTable(
            raw.title,
            "?",
            f"The table is damaged: it has {found} columns of data, but should have {expected}.",
        )
    label = KIND_LABELS.get(raw.kind, raw.kind)
    if raw.kind not in IMPORTED_KINDS:
        return SkippedTable(raw.title, raw.kind, f"{label} tables cannot be imported.")
    if raw.y_format != REPLICATES and raw.y_format not in SUMMARY_FORMATS:
        return SkippedTable(
            raw.title, raw.kind, f"Data entered as '{raw.y_format}' cannot be imported."
        )
    if not raw.data_sets and raw.x is None:
        return SkippedTable(raw.title, raw.kind, "The table is empty.")
    notes: list[str] = []
    try:
        if raw.kind == "survival":
            builder = _survival(raw, notes)
        elif raw.kind == "grouped" and raw.y_format == REPLICATES and _has_rows(raw):
            builder = _grouped(raw, notes)
        else:
            builder = _columns(raw, notes)
    except _RefusedError as refused:
        return SkippedTable(raw.title, raw.kind, str(refused))
    if raw.excluded:
        count = len(raw.excluded)
        values = "value" if count == 1 else "values"
        notes.append(f"{count} excluded {values} left empty.")
    try:
        table = builder.table(raw.title)
    except ValueError as exc:
        return SkippedTable(raw.title, raw.kind, f"The table could not be imported: {exc}.")
    if not table.columns:
        return SkippedTable(raw.title, raw.kind, "The table is empty.")
    return ImportedTable(raw.title, raw.kind, table, tuple(notes), raw.excluded)


class _RefusedError(Exception):
    """A table that cannot be imported; the message says why."""


def _has_rows(raw: PrismTable) -> bool:
    """True when a grouped table has row titles or more than one row."""
    titles = [t for t in raw.row_titles or () if t]
    return bool(titles) or _n_rows(raw) > 1


def _row_titles(raw: PrismTable, builder: _Builder) -> None:
    if raw.row_titles is not None and any(raw.row_titles):
        builder.add("Row title", ColumnRole.TEXT, raw.row_titles)


def _columns(raw: PrismTable, notes: list[str]) -> _Builder:
    """XY and column tables, grouped tables of one row, and every table of means."""
    n_rows = _n_rows(raw)
    builder = _Builder(n_rows)
    _row_titles(raw, builder)
    if raw.kind == "xy" and raw.x is not None and raw.x.subcolumns:
        x_cells = raw.x.subcolumns[0]
        x_values = _numbers(x_cells)
        name = raw.x.title or "X"
        if x_values is None:
            builder.add(name, ColumnRole.TEXT, _text_values(x_cells))
            notes.append("The X values are not numbers, so they are kept as text.")
        else:
            builder.add(name, ColumnRole.X, x_values)
        if len(raw.x.subcolumns) > 1:
            notes.append("X error values are not imported.")
    text_kept: list[str] = []
    for index, data_set in enumerate(raw.data_sets):
        name = _set_name(data_set, index)
        subs = data_set.subcolumns
        if raw.y_format == REPLICATES:
            _replicates(builder, name, subs, text_kept)
        else:
            _summary(builder, name, subs, raw.y_format, text_kept)
    if raw.y_format in SUMMARY_FORMATS:
        extra = SUMMARY_FORMATS[raw.y_format]
        kept = [s for s in extra if s not in ERROR_SUBCOLUMNS]
        if raw.kind == "grouped" and _has_rows(raw):
            notes.append(
                f"Entered as {FORMAT_LABELS[raw.y_format]}: each Prism column is a Y "
                "column with its error values, not a category, since categories hold "
                "only Y columns."
            )
        if kept:
            notes.append(f"{_join(kept)} values are kept as text columns.")
    if text_kept:
        notes.append(f"{_join(text_kept)} hold text, so they are kept as text columns.")
    return builder


def _replicates(
    builder: _Builder, name: str, subs: Sequence[Sequence[Cell]], text_kept: list[str]
) -> None:
    if not subs:
        builder.add(name, ColumnRole.Y, [])
        return
    if len(subs) == 1:
        _y_or_text(builder, name, subs[0], text_kept)
        return
    group = ReplicateGroup(name)
    builder.groups.append(group)
    values = [_numbers(cells) for cells in subs]
    if any(v is None for v in values):
        builder.groups.pop()
        for i, cells in enumerate(subs):
            _y_or_text(builder, f"{name} {i + 1}", cells, text_kept)
        return
    group.name = unique_name(name, [g.name for g in builder.groups[:-1]])
    for i, numbers in enumerate(values):
        assert numbers is not None
        builder.add(f"{name} {i + 1}", ColumnRole.Y, numbers, group_id=group.id)


def _y_or_text(
    builder: _Builder,
    name: str,
    cells: Sequence[Cell],
    text_kept: list[str],
    category: CategoryGroup | None = None,
) -> None:
    numbers = _numbers(cells)
    if numbers is None:
        if category is not None:
            raise _RefusedError(f"'{name}' holds text, which a grouped table cannot keep.")
        builder.add(name, ColumnRole.TEXT, _text_values(cells))
        text_kept.append(f"'{name}'")
    else:
        builder.add(name, ColumnRole.Y, numbers, category=category)


def _summary(
    builder: _Builder,
    name: str,
    subs: Sequence[Sequence[Cell]],
    y_format: str,
    text_kept: list[str],
) -> None:
    extra = SUMMARY_FORMATS[y_format]
    mean = subs[0] if subs else ()
    _y_or_text(builder, name, mean, text_kept)
    for label, cells in zip(extra, subs[1:], strict=False):
        column_name = f"{name} {label}"
        numbers = _numbers(cells)
        if label in ERROR_SUBCOLUMNS and numbers is not None:
            builder.add(column_name, ColumnRole.ERROR, numbers)
        else:
            builder.add(column_name, ColumnRole.TEXT, [_as_text(c) for c in cells])


def _as_text(cell: Cell) -> str | None:
    """A cell kept as text: numbers written as Curvis writes them ("5", not "5.0")."""
    number = _number(cell)
    if number is None or cell is None:
        return cell
    return format_number(number)


def _grouped(raw: PrismTable, notes: list[str]) -> _Builder:
    """Grouped tables of replicate values: one category per Prism column."""
    rows = _n_rows(raw)
    titles = list(raw.row_titles or ())
    titles += [None] * (rows - len(titles))
    used_rows = [
        r
        for r in range(rows)
        if titles[r]
        or any(
            r < len(cells) and cells[r] is not None for d in raw.data_sets for cells in d.subcolumns
        )
    ]
    depth = max((len(d.subcolumns) for d in raw.data_sets), default=1)
    builder = _Builder(max(depth, 1))
    for index, data_set in enumerate(raw.data_sets):
        category = builder.category(_set_name(data_set, index))
        for r in used_rows:
            cells = [cells[r] if r < len(cells) else None for cells in data_set.subcolumns]
            _y_or_text(builder, titles[r] or f"Row {r + 1}", cells, [], category)
    notes.append(
        "Each Prism column is a category, and each row a column in it, holding the "
        "row's replicates."
    )
    return builder


def _survival(raw: PrismTable, notes: list[str]) -> _Builder:
    if raw.x is None or not raw.x.subcolumns:
        raise _RefusedError("The table has no X (time) column.")
    if raw.y_format != REPLICATES:
        raise _RefusedError("Survival data must be entered as one value per subject.")
    builder = _Builder(_n_rows(raw))
    _row_titles(raw, builder)
    times = _numbers(raw.x.subcolumns[0])
    if times is None:
        raise _RefusedError("The times are not all numbers.")
    if any(t < 0 for t in times if not math.isnan(t)):
        raise _RefusedError("A time is negative.")
    builder.add(raw.x.title or "Time", ColumnRole.TIME, times)
    for index, data_set in enumerate(raw.data_sets):
        name = _set_name(data_set, index)
        events = _numbers(data_set.subcolumns[0]) if data_set.subcolumns else []
        if events is None or any(e not in (0.0, 1.0) for e in events if not math.isnan(e)):
            raise _RefusedError(f"'{name}' holds values other than 1 (event) and 0 (censored).")
        builder.add(name, ColumnRole.EVENT, events)
    return builder


def _join(items: Sequence[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]
