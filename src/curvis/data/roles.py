"""Column roles: how Curvis analyzes and plots a column."""

from enum import StrEnum


class ColumnRole(StrEnum):
    """The role of a column in a table."""

    X = "X"
    Y = "Y"
    ERROR = "Error"
    TIME = "Time"
    EVENT = "Event"
    TEXT = "Text"

    @property
    def is_numeric(self) -> bool:
        """True for roles that hold numbers (every role except TEXT)."""
        return self is not ColumnRole.TEXT
