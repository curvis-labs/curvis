"""The document: tables, calculations, constants and settings of a project, its change log
and undo stack."""

from dataclasses import dataclass, field

from curvis.core.calculations import Calculation
from curvis.core.change_log import ChangeLog
from curvis.core.commands import Command
from curvis.core.constants import JsonValue, ProjectConstant
from curvis.core.undo_stack import UndoStack
from curvis.data import Table, new_id
from curvis.data.names import name_key


@dataclass
class Document:
    """A project document. Change it only through :meth:`execute` with a command."""

    name: str = "Untitled"
    tables: list[Table] = field(default_factory=list)
    constants: list[ProjectConstant] = field(default_factory=list)
    calculations: list[Calculation] = field(default_factory=list)
    settings: dict[str, JsonValue] = field(default_factory=dict)
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

    def calculation(self, calculation_id: str) -> Calculation:
        """The calculation with ``calculation_id``; raises ``KeyError`` if there is none."""
        for calculation in self.calculations:
            if calculation.id == calculation_id:
                return calculation
        raise KeyError(calculation_id)

    def calculation_of(self, column_id: str) -> Calculation | None:
        """The calculation that fills the column ``column_id``, or ``None``."""
        for calculation in self.calculations:
            if calculation.output_for(column_id) is not None:
                return calculation
        return None

    def is_calculated(self, column_id: str) -> bool:
        """True if the column ``column_id`` is a calculated column."""
        return self.calculation_of(column_id) is not None

    def constant(self, constant_id: str) -> ProjectConstant:
        """The project constant with ``constant_id``; raises ``KeyError`` if there is none."""
        for constant in self.constants:
            if constant.id == constant_id:
                return constant
        raise KeyError(constant_id)
