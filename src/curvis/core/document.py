"""The document: all tables of a project plus its change log and undo stack."""

from dataclasses import dataclass, field

from curvis.core.change_log import ChangeLog
from curvis.core.commands import Command
from curvis.core.undo_stack import UndoStack
from curvis.data import Table, new_id
from curvis.data.names import name_key


@dataclass
class Document:
    """A project document. Change it only through :meth:`execute` with a command."""

    name: str = "Untitled"
    tables: list[Table] = field(default_factory=list)
    change_log: ChangeLog = field(default_factory=ChangeLog)
    id: str = field(default_factory=new_id)
    undo_stack: UndoStack = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.undo_stack = UndoStack(self)

    def execute(self, command: Command) -> None:
        """Run ``command``, put it on the undo stack and log it."""
        self.undo_stack.push(command)

    def undo(self) -> None:
        self.undo_stack.undo()

    def redo(self) -> None:
        self.undo_stack.redo()

    def table(self, table_id: str) -> Table:
        """The table with ``table_id``; raises ``KeyError`` if there is none."""
        return self.tables[self.table_index(table_id)]

    def table_index(self, table_id: str) -> int:
        """Position of the table with ``table_id``; raises ``KeyError`` if there is none."""
        for i, table in enumerate(self.tables):
            if table.id == table_id:
                return i
        raise KeyError(table_id)

    def table_by_name(self, name: str) -> Table | None:
        """The table called ``name`` (ignoring case), or ``None``."""
        key = name_key(name)
        return next((t for t in self.tables if name_key(t.name) == key), None)
