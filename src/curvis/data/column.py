"""Columns: a named NumPy array with a role, a unit and an optional replicate group.

Numeric columns hold ``float64`` arrays with NaN for missing values. Text columns
hold ``object`` arrays of ``str`` with ``None`` for missing values.

Column arrays are stored read-only so nothing outside a command can change cells
by accident. Commands unlock an array briefly with :func:`writable`.
"""

import math
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from curvis.data.ids import new_id
from curvis.data.roles import ColumnRole

type CellValue = float | int | str | None
"""A value written into a single cell."""


def _freeze(arr: npt.NDArray[Any]) -> npt.NDArray[Any]:
    arr.flags.writeable = False
    return arr


@contextmanager
def writable(arr: npt.NDArray[Any]) -> Iterator[npt.NDArray[Any]]:
    """Make a column array writable for the duration of the block (commands only)."""
    arr.flags.writeable = True
    try:
        yield arr
    finally:
        arr.flags.writeable = False


def format_number(value: float) -> str | None:
    """Text form of a number, used when a numeric column becomes a text column."""
    if math.isnan(value):
        return None
    if value.is_integer():
        return str(int(value))
    return repr(value)


def parse_number(text: str | None) -> float:
    """Number from text, used when a text column becomes numeric; NaN if not a number."""
    if text is None:
        return math.nan
    try:
        return float(text.strip())
    except ValueError:
        return math.nan


def coerce_cell(value: CellValue, role: ColumnRole) -> CellValue:
    """Validate one cell value for a column with ``role``.

    Numeric columns accept numbers, numeric strings and ``None`` or ``""`` (missing,
    stored as NaN). Text columns accept strings, numbers (stored as text) and ``None``.
    Raises ``ValueError`` for anything else.
    """
    if role.is_numeric:
        if value is None:
            return math.nan
        if isinstance(value, str):
            if not value.strip():
                return math.nan
            try:
                return float(value.strip())
            except ValueError:
                raise ValueError(f"'{value}' is not a number") from None
        if isinstance(value, int | float):
            return float(value)
        raise ValueError(f"{value!r} is not a number")
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return format_number(float(value))
    raise ValueError(f"{value!r} is not text")


def missing_value(role: ColumnRole) -> float | None:
    """The missing-value marker for a column role: NaN, or None for text."""
    return math.nan if role.is_numeric else None


def empty_values(role: ColumnRole, n: int) -> npt.NDArray[Any]:
    """A read-only array of ``n`` missing values for ``role``."""
    if role.is_numeric:
        return _freeze(np.full(n, np.nan, dtype=np.float64))
    return _freeze(np.full(n, None, dtype=object))


def coerce_values(
    values: Iterable[CellValue] | npt.NDArray[Any], role: ColumnRole
) -> npt.NDArray[Any]:
    """A new read-only array holding ``values`` in the storage format for ``role``."""
    if role.is_numeric:
        if isinstance(values, np.ndarray) and values.dtype.kind in "fiub":
            arr = np.array(values, dtype=np.float64, copy=True)
        else:
            arr = np.array([coerce_cell(v, role) for v in values], dtype=np.float64)
    else:
        cells = [coerce_cell(v, role) for v in values]
        arr = np.empty(len(cells), dtype=object)
        arr[:] = cells
    if arr.ndim != 1:
        raise ValueError("column values must be one-dimensional")
    return _freeze(arr)


def convert_values(values: npt.NDArray[Any], role: ColumnRole) -> npt.NDArray[Any]:
    """Convert a column array to the storage format for ``role`` (for role changes).

    Numbers become text; text that is not a number becomes NaN.
    """
    is_text = values.dtype == object
    if role.is_numeric and is_text:
        return _freeze(np.array([parse_number(v) for v in values], dtype=np.float64))
    if not role.is_numeric and not is_text:
        arr = np.empty(len(values), dtype=object)
        arr[:] = [format_number(float(v)) for v in values]
        return _freeze(arr)
    return values


def resized(values: npt.NDArray[Any], n: int) -> npt.NDArray[Any]:
    """A new read-only array with ``values`` truncated or padded with missing values."""
    if n <= len(values):
        return _freeze(values[:n].copy())
    pad_role = ColumnRole.TEXT if values.dtype == object else ColumnRole.Y
    return _freeze(np.concatenate([values, empty_values(pad_role, n - len(values))]))


def _values_equal(a: npt.NDArray[Any], b: npt.NDArray[Any]) -> bool:
    if a.dtype != b.dtype or a.shape != b.shape:
        return False
    if a.dtype == object:
        return bool(a.tolist() == b.tolist())
    return bool(np.array_equal(a, b, equal_nan=True))


@dataclass(eq=False)
class Column:
    """A named column of values with a role, an optional unit and replicate group.

    A Y column that is not in a replicate group is a series of its own; it can sit in
    a category directly (``category_id``). A grouped column leaves ``category_id``
    unset: its group carries the category.
    """

    name: str
    role: ColumnRole
    values: npt.NDArray[Any] = field(default_factory=lambda: np.empty(0, dtype=np.float64))
    unit: str = ""
    group_id: str | None = None
    id: str = field(default_factory=new_id)
    category_id: str | None = None

    def __post_init__(self) -> None:
        self.role = ColumnRole(self.role)
        self.values = coerce_values(self.values, self.role)

    @classmethod
    def new(
        cls,
        name: str,
        role: ColumnRole | str,
        values: Iterable[CellValue] | npt.NDArray[Any] = (),
        *,
        unit: str = "",
        group_id: str | None = None,
        id: str | None = None,
        category_id: str | None = None,
    ) -> "Column":
        """A column from any values (lists, ranges, arrays), coerced for ``role``."""
        role = ColumnRole(role)
        values = coerce_values(values, role)
        return cls(name, role, values, unit, group_id, id if id else new_id(), category_id)

    def __len__(self) -> int:
        return len(self.values)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Column):
            return NotImplemented
        return (
            self.id == other.id
            and self.name == other.name
            and self.role == other.role
            and self.unit == other.unit
            and self.group_id == other.group_id
            and self.category_id == other.category_id
            and _values_equal(self.values, other.values)
        )

    __hash__ = None  # type: ignore[assignment]

    def check(self) -> None:
        """Raise ``ValueError`` if the array does not match the role."""
        if self.values.ndim != 1:
            raise ValueError(f"column '{self.name}' must be one-dimensional")
        expected = np.dtype(np.float64) if self.role.is_numeric else np.dtype(object)
        if self.values.dtype != expected:
            raise ValueError(f"column '{self.name}' holds {self.values.dtype}, expected {expected}")
