"""Run Python's cyclic garbage collector on the Qt main thread only.

Python starts an automatic collection on whichever thread happens to allocate when a
generation's threshold is reached. While a scheduler's worker threads run Python code,
that can be a worker. A collection frees *every* unreachable cycle, including PySide
wrappers of Python-owned Qt objects (e.g. pyqtgraph's parentless context menus), and
freeing such a wrapper deletes its C++ object on that worker thread. Qt objects must be
deleted on the thread they live on; deleting a widget elsewhere races with the event
loop and can crash the app (a segfault in plain Python code on a worker thread).

While at least one holder (a :class:`~curvis.compute.Scheduler`) is active, automatic
collection is switched off and a main-thread timer does the same work: every
:data:`INTERVAL_MS` it collects the generations whose thresholds
(:func:`gc.get_threshold`) have been reached, as Python itself would. pyqtgraph's
``GarbageCollector`` uses the same approach. When the last holder lets go, automatic
collection is restored.

Like Python, a full collection (generation 2) waits until the objects that reached the
oldest generation since the last one are at least a quarter of those that were there
after it. A full collection visits every tracked object, which takes 100 ms or more on a
large heap; run each time the generation-2 count passes its threshold, it would stall the
window every few seconds while the app allocates.

Call :func:`hold` and :func:`release` from the main thread only.
"""

from __future__ import annotations

import gc
import threading

from PySide6.QtCore import QCoreApplication, QObject, Qt, QTimer
from shiboken6 import Shiboken

INTERVAL_MS = 200
"""How often the main thread checks whether a collection is due."""


class _Collector(QObject):
    """The timer that collects on the main thread. A child of the application."""

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._timer.timeout.connect(collect_if_due)

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        self._timer.start(INTERVAL_MS)

    def stop(self) -> None:
        self._timer.stop()


_holders = 0
_frozen = False
_reenable = False  # automatic collection was on when it was switched off here
_collector: _Collector | None = None
_long_lived_total: int | None = None  # objects in generation 2 after the last full collection
_long_lived_pending = 0  # objects that have reached generation 2 since then


def _check_main_thread() -> None:
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Garbage collection can only be managed from the main thread.")


def _active() -> bool:
    return _collector is not None and Shiboken.isValid(_collector) and _collector.running


def hold() -> None:
    """Collect only on the main thread until the matching :func:`release`.

    Needs a ``QCoreApplication`` (the timer runs in its event loop). Without one,
    automatic collection is left as it is.
    """
    global _holders, _reenable, _collector
    _check_main_thread()
    _holders += 1
    if _active():
        return
    app = QCoreApplication.instance()
    if app is None:
        return
    if _collector is None or not Shiboken.isValid(_collector):
        _collector = _Collector(app)
    _reenable = _reenable or gc.isenabled()
    gc.disable()
    _count_long_lived()
    _collector.start()


def release() -> None:
    """Undo one :func:`hold`. The last one restores automatic collection."""
    global _holders, _reenable
    _check_main_thread()
    if _holders == 0:
        return
    _holders -= 1
    if _holders > 0:
        return
    if _collector is not None and Shiboken.isValid(_collector):
        _collector.stop()
    if _reenable:
        _reenable = False
        gc.enable()


def is_held() -> bool:
    """True while collection is limited to the main thread."""
    return _active() and not gc.isenabled()


def _count_long_lived() -> None:
    """Count the oldest generation once, so the first full collection waits as it would."""
    global _long_lived_total, _long_lived_pending
    if _long_lived_total is None:
        _long_lived_total = len(gc.get_objects(2))
        _long_lived_pending = 0


def collect_if_due() -> int:
    """Collect the generations whose thresholds have been passed, on this thread, as
    Python's automatic collection would. Returns the number of unreachable objects
    found (0 if nothing was due)."""
    global _long_lived_total, _long_lived_pending
    threshold = gc.get_threshold()
    count = gc.get_count()
    if threshold[0] <= 0 or count[0] <= threshold[0]:
        return 0
    generation = 0
    if count[1] > threshold[1]:
        generation = 1
    _count_long_lived()
    total = _long_lived_total or 0
    if count[2] > threshold[2] and _long_lived_pending >= total / 4:
        generation = 2
    if generation == 0:
        return gc.collect(0)
    # what survives a collection of the young generations moves to the oldest one
    young = len(gc.get_objects(0)) + len(gc.get_objects(1))
    found = gc.collect(generation)
    if generation == 1:
        _long_lived_pending += max(young - found, 0)
    else:
        _long_lived_total = max(total + _long_lived_pending + young - found, 0)
        _long_lived_pending = 0
    return found


def freeze_startup_objects() -> None:
    """Collect once, then exclude every surviving object from later collections
    (:func:`gc.freeze`). Call it once, when start-up is over; later calls do nothing.

    Frozen objects are still freed when their reference count drops to zero; only a
    reference cycle made entirely of start-up objects would no longer be collected.
    """
    global _frozen, _long_lived_total, _long_lived_pending
    _check_main_thread()
    if _frozen:
        return
    _frozen = True
    gc.collect()
    gc.freeze()
    _long_lived_total, _long_lived_pending = 0, 0  # frozen objects are not collected
