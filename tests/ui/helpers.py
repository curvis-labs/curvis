"""Helpers shared by UI tests."""

from curvis.core import AddTable
from curvis.data import Column, ColumnRole, Table
from curvis.ui.document_tab import DocumentTab


def add_table(tab: DocumentTab, name: str = "Data 1") -> AddTable:
    """Change the tab's document through a command, as the UI will."""
    command = AddTable(Table(name=name, columns=[Column.new("A", ColumnRole.Y, [1.0, 2.0, 3.0])]))
    tab.document.execute(command)
    return command
