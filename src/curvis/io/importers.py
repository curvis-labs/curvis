"""CSV and TSV import: read a delimited text file into columns for a table.

Polars parses the file, with every cell read as text first. A column becomes a number
column (role Y) when every non-empty cell is a number, and is kept as Text otherwise;
with "First column is X" the first number column becomes the X column. Empty cells
become NaN in number columns and ``None`` in text columns. Rows of empty cells at the
end of the file are dropped.

The file is decoded once: UTF-8 (with or without a byte-order mark), UTF-16 with a
byte-order mark, and otherwise Windows-1252, the encoding of "CSV" files that Excel
saves on Windows. Nothing here uses Qt, so :func:`read_csv` can run in a worker process.

Row numbers (the header row, the row range, and rows named in messages) count records
from the top of the file, starting at 1. A quoted field may hold a line break; it is
still one record. Column numbers start at 1 for the first column of the file.
"""

from __future__ import annotations

import codecs
import csv
import io
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from curvis.data import Column, ColumnRole
from curvis.data.names import next_column_names, unique_name

if TYPE_CHECKING:
    import polars as pl

type Delimiter = Literal[",", ";", "\t", " "]

DELIMITERS: tuple[Delimiter, ...] = (",", ";", "\t", " ")
DELIMITER_NAMES: dict[Delimiter, str] = {",": "Comma", ";": "Semicolon", "\t": "Tab", " ": "Space"}

PREVIEW_ROWS = 100
"""Data rows shown in the import preview."""

MISSING_NUMBERS = ("", "NA", "N/A", "#N/A", "NaN")
"""Cells that count as missing in a number column (after trimming spaces)."""

_DETECT_PRIORITY: tuple[Delimiter, ...] = ("\t", ";", ",", " ")
"""Order in which delimiters that split the file equally well are preferred.

A semicolon beats a comma because files that use a decimal comma separate with
semicolons; a space comes last because text cells often contain spaces.
"""
_DETECT_RECORDS = 50
_DETECT_CONSISTENT = 0.9
_SNIFF_BYTES = 64 * 1024
_PREVIEW_BYTES = 2 * 1024 * 1024
_BINARY_PROBE = 8 * 1024
_CODECS = {"UTF-8": "utf-8-sig", "UTF-16": "utf-16", "Windows-1252": "cp1252", "Latin-1": "latin-1"}

# A quoted field (kept as is), spaces at the start or end of a line (removed), or a run
# of spaces (one delimiter). Used for space-delimited files, which often align columns.
_SPACES = re.compile(r'(?P<quoted>"(?:[^"]|"")*")|(?P<edge>(?m:^ +| +(?=\r?$)))|(?P<run> {2,})')


class CsvImportError(ValueError):
    """A file that cannot be imported. The message is meant for the user."""


@dataclass(frozen=True)
class CsvOptions:
    """How to read a file.

    ``delimiter`` ``None`` detects it. ``header_row`` is the row holding the column
    names (rows above it are skipped), or ``None`` when the file has no header and the
    columns are named A, B, C, …. ``decimal_comma`` ``None`` means: on for a semicolon
    delimiter, off otherwise. It never applies to a comma delimiter.

    Only rows ``first_row`` to ``last_row`` and columns ``first_column`` to
    ``last_column`` are imported (both ends included). ``first_row`` ``None`` starts at
    the first row under the header; ``last_row`` and ``last_column`` ``None`` go to the
    end. "First column is X" applies to the first number column in the range.
    """

    delimiter: Delimiter | None = None
    header_row: int | None = 1
    first_column_is_x: bool = False
    decimal_comma: bool | None = None
    first_row: int | None = None
    last_row: int | None = None
    first_column: int = 1
    last_column: int | None = None

    def __post_init__(self) -> None:
        if self.delimiter is not None and self.delimiter not in DELIMITERS:
            raise ValueError(f"unsupported delimiter {self.delimiter!r}")
        for name in ("header_row", "first_row", "last_row", "first_column", "last_column"):
            value = getattr(self, name)
            if value is not None and value < 1:
                raise ValueError(f"{name} starts at 1")

    @property
    def skip_rows(self) -> int:
        """Rows above the header (0 without a header)."""
        return self.header_row - 1 if self.header_row is not None else 0

    @property
    def data_row(self) -> int:
        """The first row that can hold data: the row under the header, or row 1."""
        return self.skip_rows + 2 if self.header_row is not None else 1

    @property
    def start_row(self) -> int:
        """The first row imported: ``first_row``, but never above :attr:`data_row`."""
        return max(self.data_row, self.first_row or 0)

    @property
    def read_from(self) -> int:
        """Rows not read at all: those above the header, or above the range without one.

        The first row read sets the number of columns.
        """
        return self.skip_rows if self.header_row is not None else self.start_row - 1


@dataclass(frozen=True)
class CsvData:
    """The columns read from a file, and the settings that were used."""

    columns: tuple[Column, ...]
    delimiter: Delimiter
    decimal_comma: bool
    encoding: str

    @property
    def n_rows(self) -> int:
        return len(self.columns[0]) if self.columns else 0


@dataclass(frozen=True)
class CsvPreview:
    """The top of a file for the import dialog.

    ``rows`` are records of the file as text: the rows above the header, the header (at
    ``header_index``, ``None`` without a header) and up to :data:`PREVIEW_ROWS` data
    rows from the start of the row range; ``row_numbers`` gives each one's row number in
    the file. ``names`` and ``roles`` describe the columns the import would make,
    judged from the data rows shown; the first of them is column ``column_start`` of
    the file (0-based).
    """

    rows: tuple[tuple[str, ...], ...]
    row_numbers: tuple[int, ...]
    header_index: int | None
    column_start: int
    names: tuple[str, ...]
    roles: tuple[ColumnRole, ...]
    delimiter: Delimiter
    decimal_comma: bool
    encoding: str
    more_rows: bool

    @property
    def data_start(self) -> int:
        """Index in ``rows`` of the first data row."""
        if self.header_index is None:
            return 0
        return self.header_index + 1


# --- public API --------------------------------------------------------------------


def read_csv(path: str | Path, options: CsvOptions | None = None) -> CsvData:
    """Read the whole file at ``path``. Raises :class:`CsvImportError` with a message."""
    import polars as pl

    options = options or CsvOptions()
    _check_ranges(options)
    data = _read_bytes(path)
    text, encoding = _decode(data)
    delimiter, decimal_comma = _resolve(text, options)
    if delimiter == " ":
        text = _collapse_spaces(text)
        source = text.encode("utf-8")
    elif encoding == "UTF-8":
        source = data  # Polars skips a UTF-8 byte-order mark itself
    else:
        source = text.encode("utf-8")

    def parse(truncate: bool) -> pl.DataFrame:
        return pl.read_csv(
            source,
            has_header=False,
            infer_schema=False,
            separator=delimiter,
            quote_char='"',
            skip_rows=options.read_from,
            n_rows=None if options.last_row is None else options.last_row - options.read_from,
            raise_if_empty=False,
            truncate_ragged_lines=truncate,
        )

    try:
        frame = parse(truncate=False)
    except pl.exceptions.PolarsError as exc:
        error = _shape_error(text, delimiter, options)
        if error is not None:
            raise error from None
        try:  # the long rows are outside the row range, which drops them anyway
            frame = parse(truncate=True)
        except pl.exceptions.PolarsError:
            raise CsvImportError(f"The file could not be read: {exc}") from None

    if options.header_row is not None:
        if frame.height == 0:
            count = _count_records(text, delimiter)
            raise CsvImportError(_past_end_message(count, options.header_row))
        header: Sequence[str | None] = frame.row(0)
        if all(cell is None or not cell.strip() for cell in header):
            raise CsvImportError(_empty_header_message(options.header_row))
        frame = frame.slice(1)
    else:
        if options.first_row is not None and frame.height == 0:
            count = _count_records(text, delimiter)
            raise CsvImportError(_row_past_end_message(options.first_row, count))
        if frame.width == 0 or frame.height == 0:
            raise CsvImportError("The file is empty.")
        header = ()
    first_read = options.read_from + (2 if options.header_row is not None else 1)
    offset = options.start_row - first_read  # rows between the header and the range
    if options.first_row is not None and offset >= frame.height:
        count = _count_records(text, delimiter)
        raise CsvImportError(_row_past_end_message(options.first_row, count))
    start, stop = _column_span(options, frame.width)
    frame = frame.slice(offset).select(frame.columns[start:stop])
    names = _column_names(header[start:stop], stop - start)
    frame = _drop_trailing_empty_rows(frame)
    columns = _typed_columns(frame, names, decimal_comma, options.first_column_is_x)
    return CsvData(tuple(columns), delimiter, decimal_comma, encoding)


def read_preview(
    path: str | Path, options: CsvOptions | None = None, rows: int = PREVIEW_ROWS
) -> CsvPreview:
    """Read the rows of the file at ``path`` that the import dialog shows.

    Uses the same rules as :func:`read_csv`, applied to the rows shown, and raises the
    same :class:`CsvImportError` for those rows. The delimiter and encoding are judged
    from the start of the file; only the rows up to the last one shown are read.
    """
    import polars as pl

    options = options or CsvOptions()
    _check_ranges(options)
    with open(path, "rb") as file:
        data = file.read(_PREVIEW_BYTES + 1)
    complete = len(data) <= _PREVIEW_BYTES
    text, encoding = _decode(data[:_PREVIEW_BYTES], final=complete)
    if not complete and "\n" in text:
        text = text[: text.rindex("\n") + 1]  # drop the cut-off last line
    delimiter, decimal_comma = _resolve(text, options)

    head: list[tuple[str, ...]] = []  # rows above the header, and the header
    shown: list[tuple[int, tuple[str, ...]]] = []
    width: int | None = None
    count = 0
    more_rows = False
    with _file_records(path, encoding, delimiter) as records:
        for count, cells in enumerate(records, start=1):
            record = tuple(cells)
            if count < options.data_row:
                head.append(record)
                if count == options.header_row:
                    width = len(record)
                continue
            if count < options.start_row:
                continue
            if width is None:
                width = len(record)
            if options.last_row is not None and count > options.last_row:
                break
            if len(shown) == rows:
                more_rows = True
                break
            shown.append((count, record))

    if options.header_row is not None:
        if count < options.header_row:
            raise CsvImportError(_past_end_message(count, options.header_row))
        header: Sequence[str] = head[options.skip_rows]
        if not any(cell.strip() for cell in header):
            raise CsvImportError(_empty_header_message(options.header_row))
    else:
        header = ()
    if options.first_row is not None and not shown and count < options.start_row:
        raise CsvImportError(_row_past_end_message(options.first_row, count))
    if not width:
        raise CsvImportError("The file is empty.")
    for number, record in shown:
        if len(record) > width:
            raise CsvImportError(_ragged_message(number, len(record), width))

    start, stop = _column_span(options, width)
    frame = pl.DataFrame(
        {str(i): [r[i] if i < len(r) else None for _, r in shown] for i in range(start, stop)},
        schema={str(i): pl.String for i in range(start, stop)},
    )
    if not more_rows:
        frame = _drop_trailing_empty_rows(frame)
    names = _column_names(header[start:stop], stop - start)
    columns = _typed_columns(frame, names, decimal_comma, options.first_column_is_x)
    return CsvPreview(
        rows=(*head, *(record for _, record in shown)),
        row_numbers=(*range(1, len(head) + 1), *(number for number, _ in shown)),
        header_index=options.skip_rows if options.header_row is not None else None,
        column_start=start,
        names=tuple(c.name for c in columns),
        roles=tuple(c.role for c in columns),
        delimiter=delimiter,
        decimal_comma=decimal_comma,
        encoding=encoding,
        more_rows=more_rows,
    )


def detect_delimiter(text: str, skip_rows: int = 0) -> Delimiter:
    """The delimiter that splits the rows of ``text`` (after ``skip_rows``) most evenly.

    A candidate must split most rows into the same number of fields, more than one.
    Among candidates that do so for at least 90% of the first 50 non-empty rows, the
    first in the order tab, semicolon, comma, space wins; otherwise the most even one.
    A file with one column reads as comma-separated.
    """
    sample = text[:_SNIFF_BYTES]
    if len(text) > _SNIFF_BYTES and "\n" in sample:
        sample = sample[: sample.rindex("\n") + 1]
    scores: dict[Delimiter, float] = {}
    for delimiter in _DETECT_PRIORITY:
        lines = _collapse_spaces(sample) if delimiter == " " else sample
        try:
            records = islice(_records(lines, delimiter), skip_rows, None)
            counts = [len(r) for r in records if any(cell.strip() for cell in r)]
        except csv.Error:
            continue
        counts = counts[:_DETECT_RECORDS]
        if not counts:
            continue
        width, hits = Counter(counts).most_common(1)[0]
        if width > 1:
            scores[delimiter] = hits / len(counts)
    for delimiter in _DETECT_PRIORITY:
        if scores.get(delimiter, 0.0) >= _DETECT_CONSISTENT:
            return delimiter
    if scores:
        return max(_DETECT_PRIORITY, key=lambda d: scores.get(d, 0.0))
    return ","


# --- decoding ----------------------------------------------------------------------


def _read_bytes(path: str | Path) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise CsvImportError(f"The file could not be opened: {exc.strerror or exc}") from None


def _decode(data: bytes, final: bool = True) -> tuple[str, str]:
    """Text of ``data`` and the name of its encoding. ``final=False`` for a file's head."""
    if data.startswith(codecs.BOM_UTF8):
        candidates = [("utf-8-sig", "UTF-8")]
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        candidates = [("utf-16", "UTF-16")]
    elif b"\x00" in data[:_BINARY_PROBE]:
        raise CsvImportError("This is not a text file. Choose a CSV or TSV file.")
    else:
        candidates = [("utf-8", "UTF-8"), ("cp1252", "Windows-1252"), ("latin-1", "Latin-1")]
    for codec, name in candidates:
        try:
            text = codecs.getincrementaldecoder(codec)().decode(data, final=final)
        except UnicodeDecodeError:
            continue
        if not text.strip():
            raise CsvImportError("The file is empty.")
        return text, name
    raise CsvImportError("The file is not readable text. Save it as UTF-8 and try again.")


def _resolve(text: str, options: CsvOptions) -> tuple[Delimiter, bool]:
    """The delimiter and decimal-comma setting to use for ``text``."""
    delimiter = options.delimiter or detect_delimiter(text, options.skip_rows)
    if delimiter == ",":
        return delimiter, False
    if options.decimal_comma is None:
        return delimiter, delimiter == ";"
    return delimiter, options.decimal_comma


def _collapse_spaces(text: str) -> str:
    """Make runs of spaces one delimiter and trim each line, leaving quoted fields alone."""

    def replace(match: re.Match[str]) -> str:
        if match["quoted"] is not None:
            return match["quoted"]
        return "" if match["edge"] is not None else " "

    return _SPACES.sub(replace, text)


def _records(text: str, delimiter: Delimiter) -> Iterator[list[str]]:
    return csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, quotechar='"')


def _count_records(text: str, delimiter: Delimiter) -> int:
    try:
        return sum(1 for _ in _records(text, delimiter))
    except csv.Error:
        return 0


@contextmanager
def _file_records(path: str | Path, encoding: str, delimiter: Delimiter) -> Iterator[Any]:
    """The records of the file, read as they are needed."""
    with open(path, encoding=_CODECS[encoding], errors="replace", newline="") as file:
        lines: Iterable[str] = file
        if delimiter == " ":
            lines = (_collapse_spaces(line) for line in file)
        yield csv.reader(lines, delimiter=delimiter, quotechar='"')


def _check_ranges(options: CsvOptions) -> None:
    """Refuse a row or column range that ends before it starts."""
    first, last = options.first_row, options.last_row
    if first is not None and last is not None and last < first:
        raise CsvImportError(f"The rows run from {first} to {last}. Swap them around.")
    if last is not None and last < options.data_row:
        raise CsvImportError(f"Row {last} is above the first data row ({options.data_row}).")
    first_column, last_column = options.first_column, options.last_column
    if last_column is not None and last_column < first_column:
        raise CsvImportError(
            f"The columns run from {first_column} to {last_column}. Swap them around."
        )


def _column_span(options: CsvOptions, width: int) -> tuple[int, int]:
    """The 0-based start and end of the column range in a file with ``width`` columns."""
    last = options.last_column or width
    if options.first_column > width or last > width:
        column = "column" if width == 1 else "columns"
        raise CsvImportError(f"The file has only {width} {column}.")
    return options.first_column - 1, last


# --- messages ----------------------------------------------------------------------


def _ragged_message(row: int, fields: int, width: int) -> str:
    return (
        f"Row {row} has {fields} values, but there are only {width} columns. "
        "Check the delimiter and the header row."
    )


def _empty_header_message(row: int) -> str:
    return f"Row {row} is empty. Choose the row that holds the column names."


def _past_end_message(count: int, header_row: int) -> str:
    return f"The file has only {count} rows, so row {header_row} cannot be the header."


def _row_past_end_message(row: int, count: int) -> str:
    return f"The file has only {count} rows, so there is no row {row}."


def _shape_error(text: str, delimiter: Delimiter, options: CsvOptions) -> CsvImportError | None:
    """A clear message for a file Polars refused: the first row in range with too many values.

    ``None`` when every row in the range is fine (the long rows are outside it).
    """
    width: int | None = None
    number = 0
    try:
        for number, record in enumerate(_records(text, delimiter), start=1):
            if number <= options.read_from:
                continue
            if options.last_row is not None and number > options.last_row:
                break
            if width is None:
                if options.header_row is not None and not any(c.strip() for c in record):
                    return CsvImportError(_empty_header_message(number))
                width = len(record)
            elif number >= options.start_row and len(record) > width:
                return CsvImportError(_ragged_message(number, len(record), width))
    except csv.Error as exc:
        return CsvImportError(f"The file could not be read: {exc}")
    if width is None and options.header_row is not None:
        return CsvImportError(_past_end_message(number, options.header_row))
    return None


# --- columns -----------------------------------------------------------------------


def _column_names(header: Sequence[str | None], width: int) -> list[str]:
    """Names from the header cells: blanks become A, B, …, duplicates get " (2)"."""
    cells = [(cell or "").strip() for cell in header[:width]]
    cells += [""] * (width - len(cells))
    names: list[str] = []
    for cell in cells:
        if not cell:
            cell = next_column_names([*cells, *names])[0]
        names.append(unique_name(cell, names))
    return names


def _drop_trailing_empty_rows(frame: pl.DataFrame) -> pl.DataFrame:
    import polars as pl

    if frame.width == 0 or frame.height == 0:
        return frame
    filled = frame.select(
        pl.any_horizontal(pl.all().str.strip_chars().fill_null("") != "").alias("filled")
    )["filled"]
    if filled[-1]:
        return frame
    last = filled.arg_true()
    return frame.head(int(last[-1]) + 1 if len(last) else 0)


def _typed_columns(
    frame: pl.DataFrame, names: Sequence[str], decimal_comma: bool, first_column_is_x: bool
) -> list[Column]:
    """Columns from text cells: number columns (Y, or the first one X) and Text columns."""
    import polars as pl

    columns: list[Column] = []
    want_x = first_column_is_x
    for name, cells in zip(names, frame.get_columns(), strict=True):
        cells = cells.str.strip_chars()
        missing = cells.is_null() | cells.is_in(list(MISSING_NUMBERS))
        number_text = cells.str.replace_all(",", ".", literal=True) if decimal_comma else cells
        numbers = number_text.cast(pl.Float64, strict=False)
        if (numbers.is_null() & ~missing).any():
            values = np.empty(len(cells), dtype=object)
            values[:] = [cell or None for cell in cells.to_list()]
            columns.append(Column(name, ColumnRole.TEXT, values))
        else:
            role = ColumnRole.X if want_x else ColumnRole.Y
            want_x = False
            values = numbers.fill_null(np.nan).to_numpy().astype(np.float64, copy=True)
            columns.append(Column(name, role, values))
    return columns
