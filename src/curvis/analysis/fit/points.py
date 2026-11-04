"""The points a curve fit uses, from one X column and a series' replicate columns.

Every replicate value is its own point, paired with the X of its row (Prism's default).
A point with a missing (or infinite) X or Y is left out, and so is a point outside the X range. The
X range is in the units of the X column.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

type FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class XRange:
    """Use points with ``low <= X <= high``. The default uses every point."""

    low: float = -math.inf
    high: float = math.inf

    def __post_init__(self) -> None:
        if math.isnan(self.low) or math.isnan(self.high) or self.low > self.high:
            raise ValueError("the X range must have low <= high")

    @property
    def limited(self) -> bool:
        return self.low > -math.inf or self.high < math.inf


ALL_X = XRange()
"""Every point."""


@dataclass(frozen=True)
class Points:
    x: FloatArray
    y: FloatArray

    @property
    def n(self) -> int:
        return len(self.x)


def fit_points(
    x: npt.ArrayLike, columns: Sequence[npt.ArrayLike], x_range: XRange | None = None
) -> Points:
    """The (X, Y) points of ``columns`` (replicates, each the length of ``x``)."""
    x_range = x_range or ALL_X
    xs = np.asarray(x, dtype=np.float64)
    if not columns:
        return Points(np.empty(0), np.empty(0))
    arrays = [np.asarray(c, dtype=np.float64) for c in columns]
    if any(len(a) != len(xs) for a in arrays):
        raise ValueError("every Y column must have the length of the X column")
    ys = np.concatenate(arrays)
    xx = np.tile(xs, len(columns))
    keep = np.isfinite(xx) & np.isfinite(ys) & (xx >= x_range.low) & (xx <= x_range.high)
    return Points(xx[keep], ys[keep])
