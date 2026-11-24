"""Benchmark session setup."""

import pytest

RUN_LAST = ("tests/bench/test_autosave.py",)
"""Benchmarks that run after all others.

The autosave benchmark writes eleven copies of a 10 MB project from a worker thread.
Afterwards the process's memory allocator hands out large buffers more slowly: opening
a 10 MB project (tests/bench/test_project_io.py) measured 13.6 ms instead of 8.7 ms on
CI and locally, with no change to the code it times. Running it last keeps every other
benchmark comparable with the base commit.
"""


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    items.sort(key=lambda item: item.nodeid.split("::")[0].replace("\\", "/") in RUN_LAST)
