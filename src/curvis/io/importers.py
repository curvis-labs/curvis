"""CSV and TSV import: read a delimited text file into columns for a table.

Polars parses the file, with every cell read as text first. A column becomes a number
column (role Y) when every non-empty cell is a number, and is kept as Text otherwise;
with "First column is X" the first number column becomes the X column. Empty cells
become NaN in number columns and ``None`` in text columns. Rows of empty cells at the
end of the file are dropped.

The file is decoded once: UTF-8 (with or without a byte-order mark), UTF-16 with a
byte-order mark, and otherwise Windows-1252, the encoding of "CSV" files that Excel
saves on Windows. Nothing here uses Qt, so :func:`read_csv` can run in a worker process.

Row numbers (the header row, and rows named in messages) count records from the top of
the file, starting at 1. A quoted field may hold a line break; it is still one record.
"""

from __future__ import annotations

import codecs
import csv
import io
import re
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Literal

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
    """

    delimiter: Delimiter | None = None
    header_row: int | None = 1
    first_column_is_x: bool = False
    decimal_comma: bool | None = None

    def __post_init__(self) -> None:
        if self.delimiter is not None and self.delimiter not in DELIMITERS:
            raise ValueError(f"unsupported delimiter {self.delimiter!r}")
        if self.header_row is not None and self.header_row < 1:
            raise ValueError("header_row starts at 1")

    @property
    def skip_rows(self) -> int:
        """Rows above the header (0 without a header)."""
        return self.header_row - 1 if self.header_row is not None else 0


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

    ``rows`` are the records from the top of the file as text: the skipped rows, the
    header (at ``header_index``, ``None`` without a header) and up to
    :data:`PREVIEW_ROWS` data rows. ``names`` and ``roles`` describe the columns the
    import would make, judged from the data rows shown.
    """

    rows: tuple[tuple[str, ...], ...]
    header_index: int | None
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
    try:
        frame = pl.read_csv(
            source,
            has_header=False,
            infer_schema=False,
            separator=delimiter,
            quote_char='"',
            skip_rows=options.skip_rows,
            raise_if_empty=False,
        )
    except pl.exceptions.PolarsError as exc:
        error = _shape_error(text, delimiter, options)
        raise (error or CsvImportError(f"The file could not be read: {exc}")) from None

    if options.header_row is not None:
        if frame.height == 0:
            raise CsvImportError(_past_end_message(text, delimiter, options.header_row))
        header = frame.row(0)
        if all(cell is None or not cell.strip() for cell in header):
            raise CsvImportError(_empty_header_message(options.header_row))
        frame = frame.slice(1)
        names = _column_names(header, frame.width)
    else:
        if frame.width == 0 or frame.height == 0:
            raise CsvImportError("The file is empty.")
        names = _column_names((), frame.width)
    frame = _drop_trailing_empty_rows(frame)
    columns = _typed_columns(frame, names, decimal_comma, options.first_column_is_x)
    return CsvData(tuple(columns), delimiter, decimal_comma, encoding)


def read_preview(
    path: str | Path, options: CsvOptions | None = None, rows: int = PREVIEW_ROWS
) -> CsvPreview:
    """Read the top of the file at ``path`` for the import dialog.

    Uses the same rules as :func:`read_csv`, applied to the rows shown, and raises the
    same :class:`CsvImportError` for those rows.
    """
    import polars as pl

    options = options or CsvOptions()
    with open(path, "rb") as file:
        data = file.read(_PREVIEW_BYTES + 1)
    complete = len(data) <= _PREVIEW_BYTES
    text, encoding = _decode(data[:_PREVIEW_BYTES], final=complete)
    if not complete and "\n" in text:
        text = text[: text.rindex("\n") + 1]  # drop the cut-off last line
    delimiter, decimal_comma = _resolve(text, options)
    if delimiter == " ":
        text = _collapse_spaces(text)

    start = options.skip_rows
    data_start = start + 1 if options.header_row is not None else start
    records = [tuple(r) for r in islice(_records(text, delimiter), data_start + rows + 1)]
    more_rows = len(records) > data_start + rows or not complete
    records = records[: data_start + rows]

    if options.header_row is not None:
        if len(records) <= start:
            raise CsvImportError(_past_end_message(text, delimiter, options.header_row))
        header: Sequence[str] = records[start]
        if not any(cell.strip() for cell in header):
            raise CsvImportError(_empty_header_message(options.header_row))
        width = len(header)
    else:
        header = ()
        width = len(records[0]) if records else 0
        if width == 0:
            raise CsvImportError("The file is empty.")
    data_rows = records[data_start:]
    for offset, record in enumerate(data_rows):
        if len(record) > width:
            raise CsvImportError(_ragged_message(data_start + offset + 1, len(record), width))

    frame = pl.DataFrame(
        {str(i): [r[i] if i < len(r) else None for r in data_rows] for i in range(width)},
        schema={str(i): pl.String for i in range(width)},
    )
    if not more_rows:
        frame = _drop_trailing_empty_rows(frame)
    names = _column_names(header, width)
    columns = _typed_columns(frame, names, decimal_comma, options.first_column_is_x)
    return CsvPreview(
        rows=tuple(records),
        header_index=start if options.header_row is not None else None,
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


# --- messages ----------------------------------------------------------------------


def _ragged_message(row: int, fields: int, width: int) -> str:
    return (
        f"Row {row} has {fields} values, but there are only {width} columns. "
        "Check the delimiter and the header row."
    )


def _empty_header_message(row: int) -> str:
    return f"Row {row} is empty. Choose the row that holds the column names."


def _past_end_message(text: str, delimiter: Delimiter, header_row: int) -> str:
    try:
        count = sum(1 for _ in _records(text, delimiter))
    except csv.Error:
        return f"The file has fewer than {header_row} rows."
    return f"The file has only {count} rows, so row {header_row} cannot be the header."


def _shape_error(text: str, delimiter: Delimiter, options: CsvOptions) -> CsvImportError | None:
    """A clear message for a file Polars refused: the first row with too many values."""
    width: int | None = None
    try:
        for number, record in enumerate(_records(text, delimiter), start=1):
            if number <= options.skip_rows:
                continue
            if width is None:
                if options.header_row is not None and not any(c.strip() for c in record):
                    return CsvImportError(_empty_header_message(number))
                width = len(record)
            elif len(record) > width:
                return CsvImportError(_ragged_message(number, len(record), width))
    except csv.Error as exc:
        return CsvImportError(f"The file could not be read: {exc}")
    if width is None and options.header_row is not None:
        return CsvImportError(_past_end_message(text, delimiter, options.header_row))
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
