"""How long each launch on the graphics card takes (ENGINE_PLAN X5, "Small things found by the review").

Windows resets a display driver whose command runs for more than 2 s, and on a mini PC the same card
draws the desktop: on 2026-11-22 a 3 s launch of "Refine from the images" timed the Radeon driver out
and switched the PC off (``VALIDATION.md``, "A driver timeout in Refine from the images, mended"). So
every launch must stay short, and this module is how that is seen.

**Off by default, and then it changes nothing:** :func:`launch` queues the kernel exactly as the
module did before (same arguments, same queue, no wait). **On** (the environment variable
``OHS_GPU_TIMES`` set to ``1``, or to a file path, or :func:`enable`), every command queue made by
:meth:`ohs.compute.ComputeDevice.new_queue` *afterwards* has profiling on, and every command queued
through :func:`launch` or handed to :func:`note` keeps its event; once the event has finished, its time
on the device (start to end) is added to the statistics of its name (:func:`summary`): how many, the
longest, the total, how many over :data:`SLOW_S`, the largest work size, and how many ended in an
error (a driver reset ends the commands in flight that way). Nothing waits for a launch because it is
recorded: finished events are collected when the next command is noted, the rest by :func:`summary`.
A launch waited for by its caller can be added with its wall time (:func:`timed`; the refine's).
Each launch is a debug line (logger ``ohs.compute.launches``), one over :data:`SLOW_S` a warning.
With ``OHS_GPU_TIMES`` a path, the summary is written there as JSON when the process ends
(``{pid}`` in the path is replaced by the process id).

What bounds the launches is in the modules themselves (``ohs.track.recon_tsdf``: the fusion in
batches of bricks, the pool's fills and copies in chunks; ``ohs.track.spacetime``: the refine's
batches). The names are ``<module>.<kernel>``.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import threading
from collections import deque
from pathlib import Path
from typing import Any

__all__ = ["SLOW_S", "ENV_VAR", "GpuResetError", "GPU_RESET_TEXT", "recording", "enable", "reset", "launch", "note",
           "timed", "summary", "longest_s"]

log = logging.getLogger(__name__)

ENV_VAR = "OHS_GPU_TIMES"
SLOW_S = 0.25  # a launch longer than this is counted (and logged as a warning); Windows' limit is 2 s
_MAX_PENDING = 20000  # events kept before the oldest is waited for (only while recording)

GPU_RESET_TEXT = ("The graphics driver seems to have been reset while the mesh was made (the graphics card's work "
                  "did not finish, or its memory changed), so no mesh was written. Make the mesh again; if it happens "
                  "again, close other programs that use the graphics card.")


class GpuResetError(RuntimeError):
    """The graphics card's work cannot be trusted: a command ended in an error or a self-check failed (a driver
    reset part way). Raised so that a job fails rather than writing a wrong mesh."""


_lock = threading.Lock()
_forced: bool | None = None
_pending: deque = deque()  # (name, items, event)
_stats: dict[str, dict] = {}


def recording() -> bool:
    """Whether launch times are recorded (:func:`enable`, else ``OHS_GPU_TIMES``)."""
    if _forced is not None:
        return _forced
    v = os.environ.get(ENV_VAR, "").strip()
    return bool(v) and v.lower() not in ("0", "false", "no", "off")


def enable(on: bool | None = True) -> None:
    """Record (True), do not (False), or follow ``OHS_GPU_TIMES`` again (None). Queues made before keep
    their profiling as it was, so call this before the GPU modules make theirs."""
    global _forced
    _forced = on


def reset() -> None:
    """Forget what was recorded (tests)."""
    with _lock:
        _pending.clear()
        _stats.clear()


def _items(global_size: Any) -> int:
    n = 1
    for g in (global_size if isinstance(global_size, (tuple, list)) else (global_size,)):
        n *= int(g)
    return n


def _add(name: str, items: int, seconds: float | None, error: bool = False) -> None:
    s = _stats.setdefault(name, {"launches": 0, "longest_s": 0.0, "total_s": 0.0, "over_slow": 0,
                                 "largest_items": 0, "errors": 0})
    s["launches"] += 1
    s["largest_items"] = max(s["largest_items"], int(items))
    if error:
        s["errors"] += 1
        log.warning("graphics card: %s ended in an error (%d items)", name, items)
        return
    if seconds is None:
        return
    s["total_s"] += seconds
    if seconds > s["longest_s"]:
        s["longest_s"] = seconds
    if seconds > SLOW_S:
        s["over_slow"] += 1
        log.warning("graphics card: %s took %.3f s (%d items), over %.2f s", name, seconds, items, SLOW_S)
    else:
        log.debug("launch %s: %d items, %.4f s", name, items, seconds)


def _collect(ev: Any) -> tuple[float | None, bool]:
    """(device seconds or None, ended in an error) of a finished event."""
    try:
        if int(ev.command_execution_status) < 0:
            return None, True
        return 1e-9 * (ev.profile.end - ev.profile.start), False
    except Exception as e:  # a queue without profiling (made before recording was on)
        log.debug("no time for a launch: %s", e)
        return None, False


def _harvest(block: bool) -> None:
    """Add the finished events to the statistics, oldest first (all of them, waiting, with ``block``)."""
    while _pending:
        name, items, ev = _pending[0]
        try:
            done = int(ev.command_execution_status) <= 0  # COMPLETE is 0, an error is negative
        except Exception:
            done = True
        if not done:
            if not block and len(_pending) < _MAX_PENDING:
                return
            try:
                ev.wait()
            except Exception:
                pass
        _pending.popleft()
        sec, err = _collect(ev)
        _add(name, items, sec, err)


def note(name: str, event: Any, items: int = 0) -> Any:
    """Record the command of ``event`` (a launch, a fill or a copy queued by the caller) under ``name``
    when recording; returns ``event``."""
    if event is not None and recording():
        with _lock:
            _pending.append((name, int(items), event))
            _harvest(block=False)
    return event


def launch(queue: Any, kernel: Any, name: str, global_size: tuple, local_size: tuple | None, *args: Any,
           offset: tuple | None = None) -> Any:
    """Queue ``kernel`` (with ``args`` set, when given; else as its arguments stand) over ``global_size``
    (``local_size``; ``offset`` the global offset) and return its event, recorded under ``name``
    (:func:`note`). Nothing waits for it."""
    if args:
        ev = kernel(queue, global_size, local_size, *args, global_offset=offset)
    else:
        import pyopencl as cl  # noqa: PLC0415

        ev = cl.enqueue_nd_range_kernel(queue, kernel, global_size, local_size, global_work_offset=offset)
    return note(name, ev, _items(global_size))


def timed(name: str, items: int, seconds: float) -> None:
    """Add a launch its caller waited for and timed itself (wall time) when recording."""
    if recording():
        with _lock:
            _add(name, items, float(seconds))


def summary(wait: bool = True) -> dict[str, dict]:
    """``{name: {launches, longest_s, total_s, over_slow, largest_items, errors}}`` of what was recorded
    (waiting for the launches still running, with ``wait``)."""
    with _lock:
        _harvest(block=wait)
        return {k: {**v, "longest_s": round(v["longest_s"], 5), "total_s": round(v["total_s"], 4)}
                for k, v in sorted(_stats.items())}


def longest_s() -> float:
    """The longest launch recorded (s), 0 without any."""
    return max((v["longest_s"] for v in summary().values()), default=0.0)


def _at_exit() -> None:
    v = os.environ.get(ENV_VAR, "").strip()
    if not recording() or not v or v.lower() in ("1", "true", "yes", "on"):
        if recording() and _stats:
            log.info("graphics card launches: %s", summary())
        return
    try:
        path = Path(v.replace("{pid}", str(os.getpid())))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"pid": os.getpid(), "slow_s": SLOW_S, "launches": summary()}, indent=1),
                        encoding="utf-8")
    except Exception as e:  # never fail the exit of a process for this
        log.warning("could not write the launch times to %s: %s", v, e)


atexit.register(_at_exit)
