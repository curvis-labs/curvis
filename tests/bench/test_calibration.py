"""Calibration benchmark: fixed work that tests/bench/compare.py uses to scale timings.

The baseline is recorded on one machine and checked on another (a CI runner). Dividing
every timing by this benchmark cancels most of the difference in machine speed. Never
change this workload without recording a new baseline.
"""

from typing import Any

import numpy as np


def calibration_work() -> float:
    """A mix of interpreter work and NumPy work, about 10 ms on a laptop."""
    total = 0
    for i in range(100_000):
        total += i * i % 7
    rng = np.random.default_rng(0)
    values = rng.normal(size=200_000)
    values.sort()
    matrix = rng.normal(size=(150, 150))
    return float(total) + float(values[1000]) + float((matrix @ matrix).trace())


def test_calibration(benchmark: Any) -> None:
    benchmark(calibration_work)
