"""Benchmarks for the .cvp project format. Target: open a 10 MB project in under 1 s."""

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from curvis.core import Document
from curvis.data import Column, Table
from curvis.io import load, save

OPEN_TARGET_S = 1.0


def ten_mb_document() -> Document:
    """4 tables of 25 numeric + 1 text column, 12,500 rows each: about 10 MB on disk."""
    rng = np.random.default_rng(0)
    tables = []
    for t in range(4):
        n_rows = 12_500
        columns = [Column.new(f"Y{i}", "Y", rng.normal(size=n_rows)) for i in range(25)]
        labels = [f"sample {r}" if r % 10 else None for r in range(n_rows)]
        columns.append(Column.new("Label", "Text", labels))
        tables.append(Table(f"Table {t + 1}", columns))
    return Document(name="Benchmark", tables=tables)


@pytest.fixture(scope="module")
def ten_mb_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("bench") / "ten_mb.cvp"
    save(ten_mb_document(), path)
    size_mb = path.stat().st_size / 1e6
    assert 9.5 < size_mb < 12, f"benchmark project is {size_mb:.1f} MB"
    return path


def test_open_10mb_project(benchmark: Any, ten_mb_project: Path) -> None:
    doc = benchmark(load, ten_mb_project)
    assert len(doc.tables) == 4
    assert benchmark.stats.stats.mean < OPEN_TARGET_S


def test_save_10mb_project(benchmark: Any, tmp_path: Path) -> None:
    doc = ten_mb_document()
    path = tmp_path / "out.cvp"
    benchmark(save, doc, path)
    assert load(path) == doc
