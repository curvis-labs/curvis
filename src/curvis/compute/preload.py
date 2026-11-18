"""Import heavy modules on the main thread a little at a time, so the UI keeps responding.

Light jobs need their modules imported on the main thread (see :mod:`curvis.compute.jobs`),
but one ``import scipy.stats`` takes ~0.6 s and would freeze the window. The
:class:`Preloader` imports such a module in steps instead: the packages it is made of,
dependencies first (:data:`curvis.compute.import_order.ORDER`), as many as fit in
:data:`STEP_MS`, then it lets the event loop run before the next step. A package whose
``__init__`` imports its whole subtree (``scipy.stats``, ~0.2 s) is one step: it cannot
be split without importing on another thread.

- :meth:`Preloader.ensure` is for a job that waits: its steps run at once, back to back.
- :meth:`Preloader.preload` is for start-up: its steps run only while nobody has used
  the keyboard or mouse for :data:`IDLE_MS`, so the first analysis usually finds its
  modules loaded.

A module without an entry in ``ORDER`` is imported in one step. A step that fails to
import is skipped; the job that needs it then reports the error.
"""

from __future__ import annotations

import contextlib
import importlib
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QTimer

from curvis.compute.import_order import ORDER

STEP_MS = 15
"""Imports in one step stop once this much time has passed (a single import may take longer)."""
IDLE_MS = 500
"""Start-up preloading waits until there has been no user input for this long."""

_INPUT_EVENTS = frozenset(
    {
        QEvent.Type.KeyPress,
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonDblClick,
        QEvent.Type.MouseMove,
        QEvent.Type.Wheel,
        QEvent.Type.ShortcutOverride,
    }
)


@dataclass(eq=False)
class Waiter:
    """A request from :meth:`Preloader.ensure`. Identity matters (see :meth:`discard`)."""

    names: tuple[str, ...]
    ready: Callable[[], None]
    position: int = field(default=0)


def steps(modules: Iterable[str], order: Mapping[str, Sequence[str]] = ORDER) -> tuple[str, ...]:
    """Every module to import, in order, to load ``modules`` step by step."""
    names: dict[str, None] = {}
    for module in modules:
        names.update(dict.fromkeys(order.get(module, ())))
        names[module] = None
    return tuple(names)


class Preloader(QObject):
    """Imports modules in small steps on the main thread. Create it on the main thread."""

    def __init__(
        self, parent: QObject | None = None, order: Mapping[str, Sequence[str]] = ORDER
    ) -> None:
        super().__init__(parent)
        self._order = order
        self._failed: set[str] = set()
        self._waiters: list[Waiter] = []
        self._background: tuple[str, ...] = ()
        self._background_position = 0
        self._last_input = float("-inf")
        self._watching_input = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._step)

    # --- public API ----------------------------------------------------------------

    def is_ready(self, modules: Iterable[str]) -> bool:
        """True when every module is loaded (or failed to load, so there is no point waiting)."""
        return all(self._done(m) for m in modules)

    def ensure(self, modules: Iterable[str], ready: Callable[[], None]) -> Waiter:
        """Import ``modules`` step by step, starting now; then call ``ready`` (always later,
        from the event loop, never from inside this call)."""
        waiter = Waiter(steps(modules, self._order), ready)
        self._waiters.append(waiter)
        self._timer.start(0)
        return waiter

    def discard(self, waiter: Waiter) -> None:
        """Forget a request: its ``ready`` is not called, and its remaining steps are
        only run if another request (or idle preloading) needs them."""
        with contextlib.suppress(ValueError):
            self._waiters.remove(waiter)

    def preload(self, modules: Iterable[str]) -> None:
        """Import ``modules`` step by step whenever the user is idle (replaces an earlier
        :meth:`preload`)."""
        self._background = steps(modules, self._order)
        self._background_position = 0
        self._watch_input(True)
        self._timer.start(0)

    @property
    def busy(self) -> bool:
        """True while a request or idle preloading has steps left."""
        return bool(self._waiters) or self._next_background() is not None

    def stop(self) -> None:
        """Drop every request and idle preloading."""
        self._timer.stop()
        self._waiters.clear()
        self._background = ()
        self._watch_input(False)

    # --- internals -----------------------------------------------------------------

    def _done(self, name: str) -> bool:
        return name in sys.modules or name in self._failed

    def _import(self, name: str) -> None:
        try:
            importlib.import_module(name)
        except Exception:  # e.g. a module another platform does not have
            self._failed.add(name)

    def _next_waiting(self) -> str | None:
        for waiter in self._waiters:
            while waiter.position < len(waiter.names):
                name = waiter.names[waiter.position]
                if not self._done(name):
                    return name
                waiter.position += 1
        return None

    def _next_background(self) -> str | None:
        while self._background_position < len(self._background):
            name = self._background[self._background_position]
            if not self._done(name):
                return name
            self._background_position += 1
        return None

    def _idle_wait(self) -> float:
        """Seconds until the user counts as idle (0 or less: idle now)."""
        return self._last_input + IDLE_MS / 1000 - time.perf_counter()

    def _step(self) -> None:
        deadline = time.perf_counter() + STEP_MS / 1000
        while True:
            name = self._next_waiting()
            if name is None and self._idle_wait() <= 0:
                name = self._next_background()
            if name is None:
                break
            self._import(name)
            if time.perf_counter() >= deadline:
                break
        ready = [w for w in self._waiters if self._finished(w)]
        for waiter in ready:
            self._waiters.remove(waiter)
        for waiter in ready:
            waiter.ready()
        if self._next_waiting() is not None:
            self._timer.start(0)
        elif self._next_background() is not None:
            self._timer.start(round(max(0.0, self._idle_wait()) * 1000))
        else:
            self._watch_input(False)

    def _finished(self, waiter: Waiter) -> bool:
        return all(self._done(n) for n in waiter.names[waiter.position :])

    def _watch_input(self, on: bool) -> None:
        app = QCoreApplication.instance()
        if on == self._watching_input or app is None:
            return
        if on:
            app.installEventFilter(self)
        else:
            app.removeEventFilter(self)
        self._watching_input = on

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt API)
        if event.type() in _INPUT_EVENTS:
            self._last_input = time.perf_counter()
        return False
