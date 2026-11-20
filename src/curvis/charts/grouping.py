"""Grouped bar-family graphs: which cluster and which place in it each series gets.

A table's category groups are the levels of a second factor; series are matched across
categories by name, as two-way ANOVA matches them (``name_key``). A grouped graph puts
one factor along the axis as clusters and tells the other apart by colour
(:class:`~curvis.charts.spec.Grouping`):

- ``BY_SERIES``: a cluster per series name, a place in it per category, coloured by
  category. The legend lists the categories.
- ``BY_CATEGORY``: a cluster per category, a place in it per series name, coloured by
  series name. The legend lists the series names.

A series without a category is in a category of its own, named :data:`NO_CATEGORY`. A
place with no series stays empty, so the bars of a cluster line up with the others.

Pure functions: the builder (colours), the axes layout (positions) and the marks
(legend) use them alike.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from curvis.charts.spec import Grouping
from curvis.data.names import name_key

NO_CATEGORY = "No category"


@dataclass(frozen=True)
class Entry:
    """A series as grouping sees it."""

    source: str
    name: str
    category: str | None
    """Name of its category group, or None."""


@dataclass(frozen=True)
class Place:
    source: str
    cluster: int
    slot: int
    color_key: str
    """Series with the same key share a colour and a legend entry."""
    legend_name: str


@dataclass(frozen=True)
class GroupedLayout:
    clusters: tuple[str, ...]
    """Names of the clusters along the axis, in order."""
    slots: int
    """Places per cluster."""
    places: tuple[Place, ...]
    """One per series, in the order given."""

    def legend(self) -> list[tuple[str, str]]:
        """(colour key, name) of each legend entry, in slot order."""
        by_slot: dict[int, tuple[str, str]] = {}
        for p in self.places:
            by_slot.setdefault(p.slot, (p.color_key, p.legend_name))
        return [by_slot[s] for s in sorted(by_slot)]


def _category(entry: Entry) -> str:
    return entry.category if entry.category is not None else NO_CATEGORY


def grouped_layout(entries: Sequence[Entry], grouping: Grouping) -> GroupedLayout:
    """Clusters and places of ``entries`` (the series drawn, left to right)."""
    names: dict[str, str] = {}
    categories: dict[str, str] = {}
    for e in entries:
        names.setdefault(name_key(e.name), e.name)
        categories.setdefault(name_key(_category(e)), _category(e))
    name_index = {k: i for i, k in enumerate(names)}
    category_index = {k: i for i, k in enumerate(categories)}
    places = []
    for e in entries:
        n, c = name_key(e.name), name_key(_category(e))
        if grouping is Grouping.BY_CATEGORY:
            places.append(Place(e.source, category_index[c], name_index[n], f"name:{n}", e.name))
        else:
            places.append(
                Place(e.source, name_index[n], category_index[c], f"cat:{c}", _category(e))
            )
    if grouping is Grouping.BY_CATEGORY:
        return GroupedLayout(tuple(categories.values()), len(names), tuple(places))
    return GroupedLayout(tuple(names.values()), len(categories), tuple(places))


__all__ = ("NO_CATEGORY", "Entry", "GroupedLayout", "Place", "grouped_layout")
