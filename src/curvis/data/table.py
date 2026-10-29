"""Tables: an ordered list of equal-length columns plus their groups."""

from dataclasses import dataclass, field

from curvis.data.column import Column, resized
from curvis.data.groups import CategoryGroup, ReplicateGroup
from curvis.data.ids import new_id


@dataclass
class Table:
    """A data table. All columns have the same number of rows."""

    name: str
    columns: list[Column] = field(default_factory=list)
    replicate_groups: list[ReplicateGroup] = field(default_factory=list)
    category_groups: list[CategoryGroup] = field(default_factory=list)
    id: str = field(default_factory=new_id)

    @property
    def n_rows(self) -> int:
        """Number of rows (0 for a table without columns)."""
        return len(self.columns[0]) if self.columns else 0

    def column(self, column_id: str) -> Column:
        """The column with ``column_id``; raises ``KeyError`` if there is none."""
        return self.columns[self.column_index(column_id)]

    def column_index(self, column_id: str) -> int:
        """Position of the column with ``column_id``; raises ``KeyError`` if there is none."""
        for i, column in enumerate(self.columns):
            if column.id == column_id:
                return i
        raise KeyError(column_id)

    def column_by_name(self, name: str) -> Column | None:
        """The column called ``name``, or ``None``."""
        return next((c for c in self.columns if c.name == name), None)

    def resize(self, n_rows: int) -> None:
        """Truncate or pad every column to ``n_rows`` (for use inside commands)."""
        for column in self.columns:
            if len(column) != n_rows:
                column.values = resized(column.values, n_rows)

    def check(self) -> None:
        """Raise ``ValueError`` if the table is inconsistent."""
        names = [c.name for c in self.columns]
        if len(set(names)) != len(names):
            raise ValueError(f"table '{self.name}' has duplicate column names")
        ids = [c.id for c in self.columns]
        if len(set(ids)) != len(ids):
            raise ValueError(f"table '{self.name}' has duplicate column ids")
        for column in self.columns:
            column.check()
            if len(column) != self.n_rows:
                raise ValueError(f"columns in table '{self.name}' have different lengths")
