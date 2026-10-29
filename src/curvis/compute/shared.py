"""Pass NumPy arrays to worker processes through shared memory instead of pickling.

The parent calls :func:`pack` on a job's arguments: every numeric array is copied once
into a shared-memory block and replaced by a small :class:`SharedArray` handle. The
worker runs :func:`run_packed`, which maps the blocks back to read-only arrays. The
parent owns the blocks and frees them with :func:`release` when the job has ended.

Results travel back by pickle. On Windows a block disappears when its last handle
closes, so a worker cannot safely hand a block it created to the parent. The worker
pickles the result *before* it closes the input blocks: a result may be (or contain) a
view of an input array, and NumPy does not stop a block from being unmapped while a
view of it exists. For the same reason, input arrays are valid only during the call;
a job must not keep them after it returns.
"""

from __future__ import annotations

import contextlib
import pickle
import traceback
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from multiprocessing import shared_memory
from typing import Any

import numpy as np

from curvis.compute import jobs


@dataclass(frozen=True)
class SharedArray:
    """Handle to an array stored in the shared-memory block ``name``."""

    name: str
    shape: tuple[int, ...]
    dtype: np.dtype[Any]


@dataclass(frozen=True)
class PickledResult:
    """A job's return value, pickled in the worker before its input blocks closed."""

    data: bytes

    def load(self) -> Any:
        return pickle.loads(self.data)


@dataclass(frozen=True)
class WorkerFailure:
    """An exception raised in a worker, reduced to strings so it always pickles."""

    error_type: str
    message: str
    traceback: str


def _shareable(value: object) -> bool:
    return isinstance(value, np.ndarray) and not value.dtype.hasobject


def pack(obj: Any, blocks: list[shared_memory.SharedMemory]) -> Any:
    """Copy of ``obj`` with numeric arrays moved to new shared blocks (added to ``blocks``).

    Arrays are found at the top level and inside tuples, lists and dicts. Arrays of
    Python objects (text) cannot be shared and are left to be pickled.
    """
    if _shareable(obj):
        arr: np.ndarray[Any, Any] = obj
        block = shared_memory.SharedMemory(create=True, size=max(arr.nbytes, 1))
        blocks.append(block)
        view: np.ndarray[Any, Any] = np.ndarray(arr.shape, arr.dtype, buffer=block.buf)
        view[...] = arr
        del view  # no exported buffer may outlive the block
        return SharedArray(block.name, arr.shape, arr.dtype)
    if isinstance(obj, tuple):
        return tuple(pack(v, blocks) for v in obj)
    if isinstance(obj, list):
        return [pack(v, blocks) for v in obj]
    if isinstance(obj, dict):
        return {k: pack(v, blocks) for k, v in obj.items()}
    return obj


def release(blocks: Iterable[shared_memory.SharedMemory]) -> None:
    """Close and free blocks created by :func:`pack`. Safe to call more than once."""
    for block in blocks:
        # BufferError: a view still exists here; unlinking frees the name anyway.
        with contextlib.suppress(BufferError):
            block.close()
        with contextlib.suppress(FileNotFoundError):
            block.unlink()


def new_cancel_block() -> shared_memory.SharedMemory:
    """A one-byte block used as a cancel flag for a job in another process."""
    block = shared_memory.SharedMemory(create=True, size=1)
    _buf(block)[0] = 0
    return block


def _buf(block: shared_memory.SharedMemory) -> memoryview:
    buf = block.buf
    if buf is None:
        raise ValueError("The shared block is closed.")
    return buf


class SharedCancelFlag:
    """Cancel flag backed by one byte of a shared block (same interface as CancelFlag)."""

    def __init__(self, block: shared_memory.SharedMemory) -> None:
        self._block = block

    def set(self) -> None:
        if self._block.buf is not None:  # closed once the job has ended
            self._block.buf[0] = 1

    def is_set(self) -> bool:
        buf = self._block.buf
        return buf is not None and buf[0] == 1


def _unpack(obj: Any, handles: list[shared_memory.SharedMemory]) -> Any:
    if isinstance(obj, SharedArray):
        handle = shared_memory.SharedMemory(name=obj.name)
        handles.append(handle)
        arr: np.ndarray[Any, Any] = np.ndarray(obj.shape, obj.dtype, buffer=_buf(handle))
        arr.flags.writeable = False
        return arr
    if isinstance(obj, tuple):
        return tuple(_unpack(v, handles) for v in obj)
    if isinstance(obj, list):
        return [_unpack(v, handles) for v in obj]
    if isinstance(obj, dict):
        return {k: _unpack(v, handles) for k, v in obj.items()}
    return obj


def run_packed(
    fn: Callable[..., Any], args: Any, kwargs: Any, cancel_block_name: str | None
) -> Any:
    """Entry point in the worker process: map shared arrays, run ``fn``, report errors.

    Returns a :class:`PickledResult`, or a :class:`WorkerFailure` for any exception
    (including a result that cannot be pickled), so the error always reaches the parent.
    """
    handles: list[shared_memory.SharedMemory] = []
    result: Any = None
    flag_block: shared_memory.SharedMemory | None = None
    try:
        if cancel_block_name is not None:
            flag_block = shared_memory.SharedMemory(name=cancel_block_name)
            jobs.set_current_flag(SharedCancelFlag(flag_block))
        result = fn(*_unpack(args, handles), **_unpack(kwargs, handles))
        return PickledResult(pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL))
    except BaseException as exc:
        return WorkerFailure(type(exc).__name__, str(exc), "".join(traceback.format_exception(exc)))
    finally:
        result = None  # drop any view of the inputs before unmapping them
        jobs.set_current_flag(None)
        for handle in handles if flag_block is None else [*handles, flag_block]:
            handle.close()
