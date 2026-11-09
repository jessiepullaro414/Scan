"""Small threading helpers shared by the server modules."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from ohs.app.live import LatestSlot

__all__ = ["RateMeter", "LatestWorker", "safe_call", "join_thread"]

log = logging.getLogger(__name__)


class RateMeter:
    """Event rate (Hz) from an exponential moving average of the intervals; 0 when stalled."""

    def __init__(self, alpha: float = 0.15) -> None:
        self.alpha = float(alpha)
        self._t: float | None = None
        self._dt: float | None = None
        self._lock = threading.Lock()

    def tick(self, t: float | None = None) -> None:
        t = time.monotonic() if t is None else float(t)
        with self._lock:
            if self._t is not None and t > self._t:
                dt = t - self._t
                self._dt = dt if self._dt is None else (1 - self.alpha) * self._dt + self.alpha * dt
            self._t = t

    def value(self, now: float | None = None) -> float:
        with self._lock:
            dt, last = self._dt, self._t
        if dt is None or dt <= 0:
            return 0.0
        now = time.monotonic() if now is None else now
        if last is not None and now - last > max(2.0, 5.0 * dt):
            return 0.0
        return 1.0 / dt

    def reset(self) -> None:
        with self._lock:
            self._t = self._dt = None


class LatestWorker:
    """A thread that handles only the newest submitted item (older unhandled items are dropped)."""

    def __init__(self, name: str, fn: Callable[[Any], None]) -> None:
        self._fn = fn
        self._slot = LatestSlot()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    @property
    def dropped(self) -> int:
        return self._slot.dropped

    def submit(self, item: Any) -> None:
        if item is not None and not self._stop.is_set():
            self._slot.put(item)

    def _run(self) -> None:
        while not self._stop.is_set():
            item = self._slot.take(timeout=0.1)
            if item is None:
                continue
            try:
                self._fn(item)
            except Exception:
                log.exception("%s failed", self._thread.name)

    def close(self, timeout: float = 5.0, drain: bool = False) -> None:
        """Stop the thread; with ``drain``, the pending item is handled first."""
        if drain:
            item = self._slot.take(timeout=0.0)
            self._stop.set()
            join_thread(self._thread, timeout)
            if item is not None:
                safe_call(f"{self._thread.name} (last item)", lambda: self._fn(item))
            return
        self._stop.set()
        join_thread(self._thread, timeout)


def safe_call(what: str, fn: Callable[[], Any]) -> Any:
    """Run ``fn``; log and swallow any exception (cleanup paths must go on)."""
    try:
        return fn()
    except Exception:
        log.exception("%s failed", what)
        return None


def join_thread(th: threading.Thread | None, timeout: float) -> bool:
    """Join ``th`` unless it is the current thread; returns False (and warns) if it is still alive."""
    if th is None or th is threading.current_thread():
        return True
    th.join(timeout)
    if th.is_alive():
        log.warning("thread %s did not stop within %.0f s", th.name, timeout)
        return False
    return True
