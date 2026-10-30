"""Benchmarks for CSV import. Target: import a 100,000-row CSV in under 1 s.

The file has 10 number columns and one text column (about 20 MB). ``read_csv`` times
the parsing alone; the end-to-end benchmark adds the trip to a worker process and back
(results are pickled) and the undoable command that puts the columns into a table,
which is what the user waits for after clicking Import. The worker pool is started
first, as the app does when the Import dialog opens.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from curvis.compute import Job, Scheduler
from curvis.core import Document, ImportColumns
from curvis.data import ColumnRole, Table
from curvis.io.importers import CsvData, CsvOptions, read_csv
from tests.bench.test_compute import Results

IMPORT_TARGET_S = 1.0
N_ROWS = 100_000
N_NUMBER_COLUMNS = 10


@pytest.fixture(scope="module")
def csv_100k(tmp_path_factory: pytest.TempPathFactory) -> Path:
    rng = np.random.default_rng(0)
    values = rng.normal(size=(N_ROWS, N_NUMBER_COLUMNS))
    values[rng.random(values.shape) < 0.01] = np.nan  # some empty cells
    header = ",".join([*(f"Y{i}" for i in range(N_NUMBER_COLUMNS)), "Label"])
    lines = [header]
    for r, row in enumerate(values):
        cells = ["" if np.isnan(v) else repr(float(v)) for v in row]
        lines.append(",".join([*cells, f"sample {r}"]))
    path = tmp_path_factory.mktemp("bench") / "100k.csv"
    path.write_text("\n".join(lines) + "\n")
    return path


def check(data: CsvData) -> None:
    assert data.n_rows == N_ROWS
    assert [c.role for c in data.columns] == [ColumnRole.X] + [ColumnRole.Y] * (
        N_NUMBER_COLUMNS - 1
    ) + [ColumnRole.TEXT]


OPTIONS = CsvOptions(first_column_is_x=True)


def test_read_100k_row_csv(benchmark: Any, csv_100k: Path) -> None:
    data = benchmark(read_csv, csv_100k, OPTIONS)
    check(data)
    assert benchmark.stats.stats.mean < IMPORT_TARGET_S


@pytest.fixture(scope="module")
def scheduler(qapp: QApplication) -> Iterator[Scheduler]:
    s = Scheduler(debounce_ms=0, max_processes=1)
    s.start_process_pool()
    yield s
    s.shutdown()


def test_import_100k_row_csv_end_to_end(
    benchmark: Any, scheduler: Scheduler, csv_100k: Path
) -> None:
    """Worker process → result on the main thread → ImportColumns into a blank table."""
    results = Results(scheduler.finished)
    docs: list[Document] = []

    def import_file() -> None:
        results.values.clear()
        scheduler.submit("import", Job(read_csv, (str(csv_100k), OPTIONS)), "heavy", delay_ms=0)
        results.wait(1)
        data = results.values[0]
        table = Table.blank()
        doc = Document(tables=[table])
        doc.execute(ImportColumns(table.id, data.columns, "'100k.csv'"))
        docs.append(doc)

    import_file()  # the worker imports Polars and the importer once
    benchmark(import_file)
    results.close()
    table = docs[-1].tables[0]
    assert table.n_rows == N_ROWS
    assert len(table.columns) == N_NUMBER_COLUMNS + 1
    assert benchmark.stats.stats.mean < IMPORT_TARGET_S
