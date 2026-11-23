"""Write the Excel test workbooks in this folder.

Run from the repository root (openpyxl is only needed here, not by Curvis):

    uv run --no-project --with openpyxl python tests/data/excel/make_fixtures.py
    soffice --headless --convert-to xls --outdir tests/data/excel tests/data/excel/basic.xlsx

The .xls copy is made by LibreOffice from basic.xlsx.
"""

import datetime as dt
from pathlib import Path

from openpyxl import Workbook

HERE = Path(__file__).parent


def basic() -> None:
    book = Workbook()
    results = book.active
    results.title = "Results"
    results.append(["Dose", "Response", "Label"])
    results.append([0.1, 12.5, "low"])
    results.append([1, 30.25, "mid"])
    results.append([10, 88, "high"])
    notes = book.create_sheet("Plate 2")
    notes.append(["Plate reader export"])
    notes.append([None])
    notes.append(["Well", "OD", "Time"])
    notes.append(["A1", 0.254, 0])
    notes.append(["A2", 1e-9, 5])
    notes.append(["A3", None, 10])
    notes.append([None, None, None])
    book.create_sheet("Empty")
    book.save(HERE / "basic.xlsx")


def text_cells() -> None:
    book = Workbook()
    sheet = book.active
    sheet.title = "Comma"
    sheet.append(["Conc", "Signal", "Number cells"])
    sheet.append(["0,5", "1.234,5", 0.5])
    sheet.append(["1,5", "2.500", 1.25])
    sheet.append(["2,25", "12", 2])
    ambiguous = book.create_sheet("Ambiguous")
    ambiguous.append(["Value"])
    ambiguous.append(["1,234"])
    ambiguous.append(["5,678"])
    mixed = book.create_sheet("Mixed")
    mixed.append(["Date", "Flag", "Error", "Value"])
    mixed.append([dt.datetime(2024, 1, 2), True, "=1/0", 1.5])
    mixed.append([dt.datetime(2024, 1, 3), False, "#N/A", "NA"])
    book.save(HERE / "text_cells.xlsx")


if __name__ == "__main__":
    basic()
    text_cells()
