"""Module-level job functions for the compute tests (worker processes must import them)."""

import importlib
import os
import threading
import time
from typing import Any

import numpy as np

from curvis.compute import check_cancelled


def add(a: float, b: float) -> float:
    return a + b


def where() -> tuple[int, int]:
    """(process id, thread id) of the worker that runs the job."""
    return os.getpid(), threading.get_ident()


def imported_on(module: str) -> int:
    """The thread id the module recorded when it was first imported."""
    return int(importlib.import_module(module).imported_on)


def describe(arr: np.ndarray[Any, Any]) -> dict[str, Any]:
    """Facts about an array as the worker sees it."""
    return {
        "pid": os.getpid(),
        "shape": arr.shape,
        "dtype": arr.dtype,
        "sum": float(np.sum(arr)) if arr.dtype.kind in "biuf" and arr.size else None,
        "copy": np.array(arr),
        "writeable": arr.flags.writeable,
    }


def nested_sum(data: dict[str, Any], scale: float = 1.0) -> float:
    return scale * (float(data["a"].sum()) + sum(float(x.sum()) for x in data["more"]))


def echo(arr: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
    """Return the shared input itself (the worker must not close a block still in use)."""
    return arr


def column_mean(arr: np.ndarray[Any, Any]) -> float:
    return float(np.nanmean(arr))


def boom(message: str = "boom") -> None:
    raise ValueError(message)


class UnpicklableError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.lock = threading.Lock()  # locks cannot be pickled

    def __reduce__(self) -> Any:
        raise TypeError("cannot pickle this error")


def raise_unpicklable() -> None:
    raise UnpicklableError("bad error")


def crash() -> None:
    os._exit(3)


def sleep_then(seconds: float, value: Any) -> Any:
    time.sleep(seconds)
    return value


def loop_until_cancelled(seconds: float) -> str:
    """Poll check_cancelled for up to ``seconds``; return "not cancelled" if never stopped."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(0.005)
    return "not cancelled"
