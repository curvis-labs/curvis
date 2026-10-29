"""Replicate groups and category groups.

A column's ``group_id`` points to a :class:`ReplicateGroup`, which combines several
Y columns into one series. A replicate group's ``category_id`` points to a
:class:`CategoryGroup`: a level of the second factor used by two-way ANOVA and
grouped charts.
"""

from dataclasses import dataclass, field

from curvis.data.ids import new_id


@dataclass
class ReplicateGroup:
    """Several Y columns that form one series (mean, SD and SEM per row)."""

    name: str
    category_id: str | None = None
    id: str = field(default_factory=new_id)


@dataclass
class CategoryGroup:
    """One level of the second factor; replicate groups refer to it."""

    name: str
    id: str = field(default_factory=new_id)
