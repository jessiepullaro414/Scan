"""The tracked camera path's own check for a slide (ENGINE_PLAN X0a): a jump no hand makes.

A hand moves the scanner smoothly: over a fraction of a second its path is close to a quadratic in time
(the simulated hand stays within 0.5 m/s^2; its tremor is 0.08 mm). When tracking slides along a direction
the surface does not hold (a flat table with a thin plate on it, without a GPU), the path does what no hand
does: its velocity jumps by tens to hundreds of mm/s from one frame to the next and back. On the 0.5 mm plate
without a GPU the path went 3.7, 10.6, 9.4, 8.1 ... mm a frame where the hand went 1.4, then back to 1.4: 37 mm
off in 8 frames, and the plate came out 0.11 mm thick with a third of its top (``VALIDATION.md``, X0a).

:func:`check_path` fits, for every window of :data:`PATH_CHECK_WINDOW_S` seconds of tracked frames, a
quadratic in time to each coordinate of the camera's position (least squares) and keeps the largest distance
of a frame from its window's fit: ``jump_mm``, near ``frame``. Above :data:`PATH_CHECK_LIMIT_MM` the result
says ``"warn"`` with a sentence for the user. It reads only the tracked path (no truth, no IMU) and changes
nothing: the scan is the same with or without it; the app warns, it does not block.

Measured on the scorecard's recordings (``tools/scorecard.py``, 2026-11-20, ``VALIDATION.md`` X0a): the 46
GPU-path scans of set ``full`` whose path stayed within 6.3 mm of the truth read 0.3 to 3.1 mm (the
30-frames-a-second plates 1.4 to 2.4: their frame-to-frame noise) and none warns; the GPU path's hole plate
120 mm up, which slid 7 and 16 mm, reads 5.7 and 7.5. Without a GPU (21 recordings): the three whose path
stayed within 4.8 mm pass (0.8 to 3.8); of the 18 that went 5.9 to 44 mm off, 12 warn (4.5 to 20 mm: the
three 0.5 mm plates, the three fins, two brackets, the four dark or speckled ones); before the CPU path's
defaults of 2026-11-20 the plates read 4.1 to 27. What it does not see: a slow drift at a steady wrong
velocity (without a GPU the three sheets, 6.1 to 9.4 mm off, read 2.1 to 3.9; a bracket 7.1 mm off reads
2.9; the 90-frame plates, 8.0 and 8.3 mm off, read 2.0 and 3.2), and a jerk of the real hand would read as
a slide (not measured: the simulator's hand is smooth).

Lost frames (``ok`` false) are left out; a window never spans a gap of more than :data:`PATH_CHECK_MAX_GAP_S`
(a cut, or frames lost for a while). A path with no window of at least :data:`PATH_CHECK_MIN_FRAMES` tracked
frames covering most of :data:`PATH_CHECK_WINDOW_S` is not judged (``state`` None).
"""
from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np

__all__ = ["check_path", "PATH_CHECK_WINDOW_S", "PATH_CHECK_LIMIT_MM", "PATH_CHECK_MIN_FRAMES", "PATH_CHECK_MAX_GAP_S",
           "PATH_CHECK_VERSION"]

PATH_CHECK_VERSION = 1
PATH_CHECK_WINDOW_S = 0.8  # s: the window a hand's path is a quadratic over
PATH_CHECK_LIMIT_MM = 4.0  # mm: a frame this far from its window's quadratic is a slide (module docstring)
PATH_CHECK_MIN_FRAMES = 6  # tracked frames a window needs (a quadratic has 3 coefficients a coordinate)
PATH_CHECK_MAX_GAP_S = 0.25  # s: no window spans a longer gap between two tracked frames


def _windows(t: np.ndarray) -> Iterable[np.ndarray]:
    """Index arrays of the windows: from each frame, the frames within PATH_CHECK_WINDOW_S, cut at a gap."""
    n = len(t)
    for i0 in range(n):
        j = i0
        while j + 1 < n and t[j + 1] - t[i0] <= PATH_CHECK_WINDOW_S + 1e-9 and t[j + 1] - t[j] <= PATH_CHECK_MAX_GAP_S:
            j += 1
        if j - i0 + 1 >= PATH_CHECK_MIN_FRAMES and t[j] - t[i0] >= 0.75 * PATH_CHECK_WINDOW_S:
            yield np.arange(i0, j + 1)


def _sentence(jump_mm: float, frame: int, cpu_path: bool) -> str:
    s = (f"The camera path jumps by {jump_mm:.0f} mm near frame {frame}, more than a hand moves in "
         f"{PATH_CHECK_WINDOW_S:g} s: tracking may have slid there. A thin part (a sheet, or a plate lying flat) "
         "can then come out thinner or partly missing, and sizes across that part of the scan may be off. "
         "Scan it again more slowly, with more of the part's edges in view.")
    if cpu_path:
        s += " This scan was processed without a graphics card, where tracking holds flat, plain scenes less well."
    return s


def check_path(rows: Iterable[tuple[int, float, bool, Any]], cpu_path: bool = False) -> dict:
    """The check of a tracked path (module docstring). ``rows`` are ``(index, t, ok, T_wc)`` per processed
    frame, as ``process_recording`` writes ``trajectory.csv`` (``t`` in s, ``T_wc`` a 4x4 pose in mm).
    ``cpu_path``: the scan was tracked without a GPU (the sentence says so).

    Returns ``{"version", "window_s", "limit_mm", "frames", "jump_mm", "frame", "state", "warning"}``:
    ``state`` "ok" or "warn", or None (and ``jump_mm`` None) when the path is too short to judge."""
    idx, ts, P = [], [], []
    for i, t, ok, T in rows:
        if not ok or t is None or not math.isfinite(float(t)):
            continue
        T = np.asarray(T, dtype=np.float64).reshape(4, 4)
        idx.append(int(i))
        ts.append(float(t))
        P.append(T[:3, 3])
    out: dict[str, Any] = {"version": PATH_CHECK_VERSION, "window_s": PATH_CHECK_WINDOW_S, "limit_mm": PATH_CHECK_LIMIT_MM,
                           "frames": len(idx), "jump_mm": None, "frame": None, "state": None, "warning": None}
    if len(idx) < PATH_CHECK_MIN_FRAMES:
        return out
    order = np.argsort(np.asarray(ts), kind="stable")
    t = np.asarray(ts)[order]
    X = np.asarray(P)[order]
    frames = np.asarray(idx)[order]
    worst, at, judged = 0.0, None, False
    for w in _windows(t):
        judged = True
        tt = t[w] - t[w[0]]
        A = np.column_stack([np.ones_like(tt), tt, tt * tt])
        coef, *_ = np.linalg.lstsq(A, X[w], rcond=None)
        r = np.linalg.norm(X[w] - A @ coef, axis=1)
        k = int(np.argmax(r))
        if r[k] > worst:
            worst, at = float(r[k]), int(frames[w[k]])
    if not judged:
        return out
    out["jump_mm"] = round(worst, 2)
    out["frame"] = at
    out["state"] = "warn" if worst > PATH_CHECK_LIMIT_MM else "ok"
    if out["state"] == "warn":
        out["warning"] = _sentence(worst, at, cpu_path)
    return out
