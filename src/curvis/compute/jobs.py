"""Job descriptions, job errors and cooperative cancellation (no Qt here).

A job is either a zero-argument callable (light jobs only) or a :class:`Job` with a
function and its arguments. Heavy jobs run in another process, so their function must
be defined at module level and their arguments must be picklable; NumPy arrays among
the arguments are passed through shared memory instead of being pickled.
"""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

type Weight = Literal["light", "heavy"]
WEIGHTS: tuple[Weight, ...] = ("light", "heavy")


@dataclass(frozen=True)
class Job:
    """A function to call with ``args`` and ``kwargs``."""

    fn: Callable[..., Any]
    args: tuple[Any, ...] = ()
    kwargs: Mapping[str, Any] = field(default_factory=dict)

    def __call__(self) -> Any:
        return self.fn(*self.args, **self.kwargs)


type JobLike = Job | Callable[[], Any]


@dataclass(frozen=True)
class JobError:
    """What a failed job reports to its node. Plain strings, so it always pickles."""

    node_id: str
    message: str
    error_type: str
    traceback: str = ""

    @classmethod
    def from_exception(cls, node_id: str, exc: BaseException) -> JobError:
        tb = "".join(traceback.format_exception(exc))
        return cls(node_id, str(exc) or type(exc).__name__, type(exc).__name__, tb)


class Cancelled(Exception):  # noqa: N818 - reads better than CancelledError at call sites
    """Raised by :func:`check_cancelled` when the running job has been superseded."""


class CancelFlag:
    """A flag the scheduler sets when a job is superseded.

    In threads this is a plain event. In worker processes, :mod:`curvis.compute.shared`
    installs a flag backed by one byte of shared memory with the same interface.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def set(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()


_current = threading.local()


def set_current_flag(flag: Any) -> None:
    """Install the cancel flag of the job that runs on this thread (``None`` to clear)."""
    _current.flag = flag


def is_cancelled() -> bool:
    """True when the job running on this thread has been superseded or cancelled."""
    flag = getattr(_current, "flag", None)
    return flag is not None and bool(flag.is_set())


def check_cancelled() -> None:
    """Raise :class:`Cancelled` if the running job is no longer wanted.

    Long jobs should call this now and then (e.g. between fits) so a superseded job
    stops early. Outside a scheduled job it does nothing.
    """
    if is_cancelled():
        raise Cancelled("The job was cancelled.")
