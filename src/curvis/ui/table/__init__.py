"""The data table view: a Qt model over a table's columns and a spreadsheet-style view."""

from curvis.ui.table.model import SPARE_COLUMNS, SPARE_ROWS, TableModel
from curvis.ui.table.view import TableView

__all__ = ["SPARE_COLUMNS", "SPARE_ROWS", "TableModel", "TableView"]
