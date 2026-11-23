"""Excel import: read a sheet of an .xlsx (fastexcel) or .xls (xlrd) workbook.

A sheet is read into two grids of the same shape: the text of every cell (a number
cell as its shortest exact form, "0.1"; a date as "2024-01-02 00:00:00"), and the
value of every number cell (null elsewhere). Number cells are used as they are; only
text cells go through the decimal mark (a text cell "1,5" with a decimal comma). Then
the options of :class:`curvis.io.importers.CsvOptions` apply as for a CSV file: the
header row, the row and column range and "First column is X". Columns that are empty
at the right of the sheet are left out.

Rows and columns are numbered as in Excel: row 1 is the sheet's first row, column 1
its column A. Nothing here uses Qt, so the readers can run in a worker process.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from curvis.data.column import format_number
from curvis.io.importers import (
    PREVIEW_ROWS,
    CsvData,
    CsvImportError,
    CsvOptions,
    CsvPreview,
    check_ranges,
    column_names,
    column_span,
    decide_decimal,
    drop_trailing_empty_rows,
    empty_header_message,
    past_end_message,
    row_past_end_message,
    typed_columns,
)

if TYPE_CHECKING:
    import polars as pl

ENCODING = "Excel"
"""What :class:`CsvData` and :class:`CsvPreview` give as the encoding of a sheet."""


@dataclass(frozen=True)
class Sheet:
    """One sheet as two grids (see the module docstring), and the workbook's sheets."""

    text: pl.DataFrame
    numbers: pl.DataFrame
    name: str
    sheets: tuple[str, ...]

    @property
    def n_rows(self) -> int:
        return self.text.height

    @property
    def width(self) -> int:
        return self.text.width


def sheet_names(path: str | Path) -> tuple[str, ...]:
    """The sheets of the workbook at ``path``, in workbook order."""
    return read_sheet(path, None, names_only=True).sheets


def read_excel(path: str | Path, options: CsvOptions | None = None) -> CsvData:
    """Read the chosen sheet (``options.sheet``, or the first) of the workbook at ``path``."""
    options = options or CsvOptions()
    check_ranges(options)
    sheet = read_sheet(path, options.sheet)
    text, numbers, names = _data_rows(sheet, options, None)
    guess = decide_decimal(options, None, text.head(PREVIEW_ROWS), numbers.head(PREVIEW_ROWS))
    text = drop_trailing_empty_rows(text)
    numbers = numbers.head(text.height)
    columns = typed_columns(text, names, guess.comma, options.first_column_is_x, numbers)
    return CsvData(tuple(columns), None, guess.comma, ENCODING, guess.unsure)


def read_excel_preview(
    path: str | Path, options: CsvOptions | None = None, rows: int = PREVIEW_ROWS
) -> CsvPreview:
    """The rows of the chosen sheet that the import dialog shows (as :func:`read_preview`)."""
    options = options or CsvOptions()
    check_ranges(options)
    sheet = read_sheet(path, options.sheet)
    text, numbers, names = _data_rows(sheet, options, rows + 1)
    more_rows = text.height > rows
    text, numbers = text.head(rows), numbers.head(rows)
    guess = decide_decimal(options, None, text, numbers)
    if not more_rows:
        text = drop_trailing_empty_rows(text)
        numbers = numbers.head(text.height)
    columns = typed_columns(text, names, guess.comma, options.first_column_is_x, numbers)

    start, _ = column_span(options, sheet.width)
    data_rows = list(range(options.start_row, options.start_row + text.height))
    shown = sheet.text.slice(options.start_row - 1, text.height)
    head_rows = sheet.text.head(options.data_row - 1) if options.header_row is not None else None
    shown_numbers = sheet.numbers.slice(options.start_row - 1, text.height)
    records: list[tuple[str, ...]] = []
    if head_rows is not None:
        head_numbers_grid = sheet.numbers.head(options.data_row - 1)
        records += [
            _record(r, n) for r, n in zip(head_rows.rows(), head_numbers_grid.rows(), strict=True)
        ]
    records += [_record(r, n) for r, n in zip(shown.rows(), shown_numbers.rows(), strict=True)]
    head_numbers = list(range(1, (options.data_row - 1) + 1)) if head_rows is not None else []
    return CsvPreview(
        rows=tuple(records),
        row_numbers=(*head_numbers, *data_rows),
        header_index=options.skip_rows if options.header_row is not None else None,
        column_start=start,
        names=tuple(c.name for c in columns),
        roles=tuple(c.role for c in columns),
        delimiter=None,
        decimal_comma=guess.comma,
        encoding=ENCODING,
        more_rows=more_rows,
        decimal_unsure=guess.unsure,
        sheets=sheet.sheets,
        sheet=sheet.name,
    )


# --- options -------------------------------------------------------------------------


def _record(row: tuple[Any, ...], numbers: tuple[Any, ...]) -> tuple[str, ...]:
    """A row as the preview shows it: number cells in their shortest form ("1e-09")."""
    return tuple(
        format_number(n) or "" if n is not None else (cell or "")
        for cell, n in zip(row, numbers, strict=True)
    )


def _data_rows(
    sheet: Sheet, options: CsvOptions, limit: int | None
) -> tuple[pl.DataFrame, pl.DataFrame, list[str]]:
    """The text and number grids of the data rows in range (at most ``limit`` rows),
    and the column names. Raises :class:`CsvImportError` as the CSV reader does."""
    if sheet.width == 0 or sheet.n_rows == 0:
        raise CsvImportError(f"The sheet '{sheet.name}' is empty.")
    if options.header_row is not None:
        if options.header_row > sheet.n_rows:
            raise CsvImportError(past_end_message(sheet.n_rows, options.header_row))
        header: list[str | None] = list(sheet.text.row(options.header_row - 1))
        if all(cell is None or not cell.strip() for cell in header):
            raise CsvImportError(empty_header_message(options.header_row))
    else:
        header = []
    start_index = options.start_row - 1
    if options.first_row is not None and start_index >= sheet.n_rows:
        raise CsvImportError(row_past_end_message(options.first_row, sheet.n_rows))
    end = sheet.n_rows if options.last_row is None else min(options.last_row, sheet.n_rows)
    length = max(0, end - start_index)
    if limit is not None:
        length = min(length, limit)
    start, stop = column_span(options, sheet.width)
    selected = sheet.text.columns[start:stop]
    text = sheet.text.slice(start_index, length).select(selected)
    numbers = sheet.numbers.slice(start_index, length).select(selected)
    names = column_names(header[start:stop], stop - start)
    return text, numbers, names


# --- reading -------------------------------------------------------------------------


def read_sheet(path: str | Path, name: str | None, *, names_only: bool = False) -> Sheet:
    """Sheet ``name`` (the first when ``None``) of the workbook at ``path``."""
    path = Path(path)
    if not path.is_file():
        raise CsvImportError("The file could not be opened: it does not exist.")
    if path.suffix.lower() == ".xls":
        return _read_xls(path, name, names_only)
    return _read_xlsx(path, name, names_only)


def _pick(sheets: tuple[str, ...], name: str | None) -> str:
    if not sheets:
        raise CsvImportError("The workbook has no sheets.")
    if name is None:
        return sheets[0]
    if name not in sheets:
        raise CsvImportError(f"The workbook has no sheet called '{name}'.")
    return name


def _read_xlsx(path: Path, name: str | None, names_only: bool) -> Sheet:
    import fastexcel
    import polars as pl

    try:
        reader = fastexcel.read_excel(str(path))
        sheets = tuple(reader.sheet_names)
    except Exception as exc:  # fastexcel raises its own types and OSError
        raise CsvImportError(f"The workbook could not be read: {exc}") from None
    chosen = _pick(sheets, name)
    if names_only:
        return Sheet(pl.DataFrame(), pl.DataFrame(), chosen, sheets)
    try:
        text = reader.load_sheet(
            chosen, header_row=None, dtypes="string", schema_sample_rows=None
        ).to_polars()
        values = reader.load_sheet(
            chosen, header_row=None, dtypes="float", schema_sample_rows=None
        ).to_polars()
    except fastexcel.FastExcelError as exc:
        message = str(exc).lower()
        if "empty" in message or "no data" in message:
            return _empty(chosen, sheets)
        raise CsvImportError(f"The sheet '{chosen}' could not be read: {exc}") from None
    columns = [f"c{i}" for i in range(text.width)]
    text = text.rename(dict(zip(text.columns, columns, strict=True)))
    values = values.rename(dict(zip(values.columns, columns, strict=True)))
    # fastexcel also parses text cells that look like numbers ("2.500" typed as text)
    # into the float grid. A number cell's text is always in canonical form, so a cell
    # counts as a number only when its text is: "2.500" (thousands, with a decimal
    # comma) stays text, and so do "+1", "01" and "1e3".
    numbers = values.select(
        pl.when(text[c].str.contains(_CANONICAL)).then(pl.col(c)).otherwise(None).alias(c)
        for c in columns
    )
    return _trimmed(Sheet(text, numbers, chosen, sheets))


_CANONICAL = r"^-?(?:0|[1-9]\d*)(?:\.\d*[1-9])?$"
"""How fastexcel writes a number cell as text: digits, no exponent, no trailing zeros."""


def _read_xls(path: Path, name: str | None, names_only: bool) -> Sheet:
    import polars as pl
    import xlrd

    try:
        book = xlrd.open_workbook(str(path), on_demand=True)
    except Exception as exc:  # xlrd raises XLRDError, CompDocError and OSError
        raise CsvImportError(f"The workbook could not be read: {exc}") from None
    try:
        sheets = tuple(book.sheet_names())
        chosen = _pick(sheets, name)
        if names_only:
            return Sheet(pl.DataFrame(), pl.DataFrame(), chosen, sheets)
        sheet = book.sheet_by_name(chosen)
        width = sheet.ncols
        text_cols: list[list[str | None]] = [[] for _ in range(width)]
        number_cols: list[list[float | None]] = [[] for _ in range(width)]
        for r in range(sheet.nrows):
            types = sheet.row_types(r)
            values = sheet.row_values(r)
            for c in range(width):
                kind = types[c] if c < len(types) else xlrd.XL_CELL_EMPTY
                value = values[c] if c < len(values) else ""
                text, number = _xls_cell(kind, value, book.datemode, xlrd)
                text_cols[c].append(text)
                number_cols[c].append(number)
    finally:
        book.release_resources()
    columns = [f"c{i}" for i in range(width)]
    text_frame = pl.DataFrame(
        {c: v for c, v in zip(columns, text_cols, strict=True)},
        schema={c: pl.String for c in columns},
    )
    number_frame = pl.DataFrame(
        {c: v for c, v in zip(columns, number_cols, strict=True)},
        schema={c: pl.Float64 for c in columns},
    )
    return _trimmed(Sheet(text_frame, number_frame, chosen, sheets))


_XLS_ERRORS = {0x00: "#NULL!", 0x07: "#DIV/0!", 0x0F: "#VALUE!", 0x17: "#REF!"}
_XLS_ERRORS |= {0x1D: "#NAME?", 0x24: "#NUM!", 0x2A: "#N/A"}


def _xls_cell(kind: int, value: Any, datemode: int, xlrd: Any) -> tuple[str | None, float | None]:
    """The text and number of one .xls cell."""
    if kind == xlrd.XL_CELL_NUMBER:
        number = float(value)
        return format_number(number), number
    if kind == xlrd.XL_CELL_TEXT:
        return (str(value) or None), None
    if kind == xlrd.XL_CELL_DATE:
        try:
            moment: dt.datetime = xlrd.xldate_as_datetime(value, datemode)
        except Exception:  # dates before 1900 or out of range
            return format_number(float(value)), None
        return moment.isoformat(sep=" "), None
    if kind == xlrd.XL_CELL_BOOLEAN:
        return ("true" if value else "false"), None
    if kind == xlrd.XL_CELL_ERROR:
        return _XLS_ERRORS.get(int(value), "#N/A"), None
    return None, None


def _empty(name: str, sheets: tuple[str, ...]) -> Sheet:
    import polars as pl

    return Sheet(pl.DataFrame(), pl.DataFrame(), name, sheets)


def _trimmed(sheet: Sheet) -> Sheet:
    """``sheet`` without the empty columns at its right."""
    text = sheet.text
    keep = text.width
    while keep and text.to_series(keep - 1).drop_nulls().str.strip_chars().eq("").all():
        keep -= 1
    if keep == text.width:
        return sheet
    columns = text.columns[:keep]
    return Sheet(text.select(columns), sheet.numbers.select(columns), sheet.name, sheet.sheets)
