"""Replicate statistics and series: what analyses and graphs read from a table.

A *series* is one replicate group, or one Y column that is not in a group. For each
row, :func:`row_stats` gives the mean, SD, SEM and n of a series' replicates, leaving
out missing values (NaN), and :func:`mean_ci` the 95% confidence interval of the mean.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from curvis.data.roles import ColumnRole
from curvis.data.table import Table

type FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class ReplicateStats:
    """Per-row statistics of replicate columns; each array has one value per row.

    - ``n``: number of values that are not missing.
    - ``mean``: NaN when n = 0.
    - ``sd``: sample standard deviation (n - 1 in the denominator); NaN when n < 2.
    - ``sem``: SD / √n; NaN when n < 2.
    """

    mean: FloatArray
    sd: FloatArray
    sem: FloatArray
    n: npt.NDArray[np.int64]


@dataclass(frozen=True)
class Series:
    """One replicate group, or one Y column without a group (which can be in a category)."""

    name: str
    column_ids: tuple[str, ...]
    group_id: str | None
    category_id: str | None


def row_stats(arrays: Sequence[npt.ArrayLike]) -> ReplicateStats:
    """Mean, SD, SEM and n for each row across ``arrays`` (the replicates), ignoring NaN.

    The arrays must have the same length. The mean is refined with a second pass over
    the deviations (as R's ``mean`` does), so equal values give an SD of exactly 0.
    Infinite values give an infinite (or NaN) mean and a NaN SD.
    """
    if not arrays:
        raise ValueError("row_stats needs at least one array")
    # One row per replicate: sums over axis 0 add whole contiguous arrays, which is
    # much faster than reducing many short rows.
    data = np.vstack([np.asarray(a, dtype=np.float64) for a in arrays])
    missing = np.isnan(data)
    n = data.shape[0] - missing.sum(axis=0)
    filled = np.where(missing, 0.0, data)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = filled.sum(axis=0) / n
        deviation = filled - mean
        deviation[missing] = 0.0
        correction = deviation.sum(axis=0) / n
        mean = np.where(np.isfinite(mean), mean + correction, mean)
        np.subtract(filled, mean, out=deviation)
        deviation[missing] = 0.0
        np.multiply(deviation, deviation, out=deviation)
        variance = deviation.sum(axis=0) / (n - 1)
        variance[n < 2] = np.nan
        sd = np.sqrt(variance)
        sem = sd / np.sqrt(n)
    return ReplicateStats(mean=mean, sd=sd, sem=sem, n=n.astype(np.int64))


def mean_ci(stats: ReplicateStats) -> tuple[FloatArray, FloatArray]:
    """Lower and upper limits of the 95% confidence interval of each row's mean.

    The limits are mean ∓ t·SEM, with t the 97.5% quantile of Student's t
    distribution with n - 1 degrees of freedom (as R's ``t.test``). They are NaN where
    n < 2 or the SEM is not finite, and equal to the mean where the SD is 0.
    """
    from scipy.special import stdtrit  # imported here: SciPy is slow to import

    df = (stats.n - 1).astype(np.float64)
    df[stats.n < 2] = np.nan
    with np.errstate(invalid="ignore"):
        margin = stdtrit(df, 0.975) * stats.sem
    margin[~np.isfinite(margin)] = np.nan
    return stats.mean - margin, stats.mean + margin


def replicate_stats(table: Table, group_id: str) -> ReplicateStats:
    """Per-row mean, SD, SEM and n of a replicate group's columns.

    Raises ``KeyError`` if the table has no group with ``group_id``.
    """
    table.replicate_group(group_id)
    return row_stats([c.values for c in table.group_columns(group_id)])


def series_stats(table: Table, series: Series) -> ReplicateStats:
    """Per-row mean, SD, SEM and n of a series (n is 0 or 1 for a single column)."""
    return row_stats([table.column(c).values for c in series.column_ids])


def series(table: Table) -> list[Series]:
    """The table's series, left to right: each replicate group and each ungrouped Y column."""
    found: list[Series] = []
    seen: set[str] = set()
    for column in table.columns:
        if column.group_id is None:
            if column.role is ColumnRole.Y:
                found.append(Series(column.name, (column.id,), None, column.category_id))
            continue
        if column.group_id in seen:
            continue
        seen.add(column.group_id)
        group = table.replicate_group(column.group_id)
        ids = tuple(c.id for c in table.group_columns(group.id))
        found.append(Series(group.name, ids, group.id, group.category_id))
    return found
