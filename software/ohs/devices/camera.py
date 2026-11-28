"""Stereo USB camera capture (OpenCV UVC) and recording playback.

:class:`StereoCamera` opens the ELP dual-OV9281 module (one UVC device delivering side-by-side
frames, left | right, both sensors hardware-synchronised). A background thread grabs continuously
and keeps only the newest frame. ``read(timeout)`` returns the newest frame not yet returned (never
the same index twice) or raises ``TimeoutError``. Frames are converted to grayscale ``uint8`` and
split with :meth:`StereoFrame.from_sbs` (``swap_lr`` honoured). With ``second_index`` set, two
separate UVC cameras are opened instead. They are grabbed back to back but are **not**
synchronised, and a warning is logged.

Frame times and indices (:class:`_FrameClock`). ``t_host`` is on the ``time.perf_counter()``
timeline. The camera itself is exactly periodic, but its frames reach the PC in bursts, 3 to 6 ms
apart from a straight line, so the time a frame arrives is a poor time for it. When the capture
gives the camera's own sample time (``CAP_PROP_POS_MSEC``; Media Foundation does) and those stamps
advance in whole multiples of a steady period, ``t_host`` comes from the stamp. Stamps that are
already ``perf_counter`` times (Media Foundation's on this PC: about 26 ms before the earliest
arrival, steady over ten minutes) are used as they are, ``t_host = stamp / 1000``. Stamps on
another clock are carried onto ``perf_counter``: the stamp plus an offset that follows the
earliest arrivals (a frame can arrive late but never before it was taken) and the slow drift
between the two clocks, never stepping.
The index then counts the camera's frames, so frames lost before the PC got them leave a gap too.
The first ~20 frames (a third of a second) are used to decide this and are not given out; a
stretch that does not look like a frame clock is looked at again, up to four stretches or two
seconds, because a slow start (frames held up and handed over together) looks like that too. Without
usable stamps (DirectShow gives -1) a frame is stamped when it arrived: after the call that waited
for it, which is ``retrieve()`` on DirectShow (its ``grab()`` returns at once) and ``grab()`` on
Media Foundation; which call waits is measured, not assumed. Either way index gaps mean dropped
frames, indices count up from 0 and times strictly increase. ``info["frame_times"]`` says which
("camera" or "arrival"), ``info["frame_clock"]`` for camera times whether the stamps are used as
they are ("host") or with a learned offset ("learned"; ``None`` for arrival times),
``info["frame_period_ms"]`` the period found and ``info["frame_times_reason"]`` why the frames are
timed on arrival (``None`` for camera times).

Backends (``CameraParams.backend``): ``"auto"`` means Media Foundation then DirectShow on Windows
(Media Foundation gives the camera's frame stamps and reads 120 fps at 2560x800, DirectShow only
about 68), and V4L2 then any on Linux. A backend that does not open, or opens but gives no frame
within ``first_frame_timeout``, is closed and the next one is tried. OpenCV's Media Foundation
hardware transforms make opening this camera take 20 s per setting (80 s in all); ``import ohs``
therefore sets ``OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS=0`` unless the user set it, and
OpenCV reads it only when ``cv2`` is first imported (setting it later has no effect). The pixel
format (FOURCC, default MJPG, which the 2560x800 modes need over USB 2) is set *before* the frame
size and fps, because several UVC drivers only accept the size once the format allows it.

Exposure and gain units are backend-native (``CameraParams.exposure`` / ``set_exposure``):

* **DSHOW**: ``CAP_PROP_EXPOSURE`` is log2 of the exposure time in seconds, in integer steps:
  -5 = 1/32 s (31 ms), -7 = 1/128 s (7.8 ms), -10 = ~1 ms, -13 = ~122 us. Setting it switches the
  camera to manual exposure. ``CAP_PROP_AUTO_EXPOSURE`` 0.75 turns auto back on (OpenCV rounds
  the value, and 1 means auto).
* **V4L2**: ``CAP_PROP_AUTO_EXPOSURE`` 0.25 = manual and 0.75 = auto (legacy normalised values of
  the ``exposure_auto`` menu). Some driver/OpenCV combinations want the raw menu values 1 (manual)
  and 3 (aperture priority) instead, so 1 is tried when 0.25 is rejected. ``CAP_PROP_EXPOSURE``
  is ``exposure_time_absolute`` in 100 us units: 50 = 5 ms.
* **MSMF**: OpenCV maps the property to the same camera control as DirectShow (log2 seconds).
  On the ELP board it works (the picture gets darker as it should), but the value read back stays
  at the camera's start value; other drivers may ignore it under Media Foundation.
* **Gain** (``CAP_PROP_GAIN``) is a raw driver value on every backend.

:class:`ReplayCamera` plays a recording folder (``ohs.io.recording``) through the same
``open``/``read``/``info``/``close``/context-manager interface, so live mode can run without
hardware. See its docstring for timing and end-of-recording behaviour.

Deviations and extensions to ARCHITECTURE.md: ``StereoCamera(params, first_frame_timeout=5.0,
dead_after_s=3.0)`` waits for the first frame in ``open()`` and raises ``TimeoutError`` if none
arrives. If the camera stops delivering for ``dead_after_s``, ``read()`` raises ``RuntimeError``.
``list_cameras(max_index=8, backend="auto")`` has an optional backend. ``ReplayCamera`` takes an
extra ``restamp`` flag and raises ``EOFError`` at the end of a non-looping recording.
"""
from __future__ import annotations

import collections
import contextlib
import dataclasses
import logging
import math
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterator

import cv2
import numpy as np

from ohs.devices.params import CameraParams
from ohs.io.recording import RecordingReader
from ohs.types import StereoFrame

__all__ = ["CameraParams", "StereoCamera", "ReplayCamera", "list_cameras"]

log = logging.getLogger(__name__)

_BACKENDS = {"dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF, "v4l2": cv2.CAP_V4L2, "any": cv2.CAP_ANY}
_AUTO_EXPOSURE_MANUAL = 0.25
_AUTO_EXPOSURE_MANUAL_V4L2_MENU = 1.0


# --------------------------------------------------------------------------- helpers
def _backend_candidates(name: str, platform: str | None = None) -> list[int]:
    """OpenCV API preferences to try, in order, for a backend name."""
    plat = sys.platform if platform is None else platform
    key = (name or "auto").lower()
    if key == "auto":
        if plat.startswith("win"):
            return [cv2.CAP_MSMF, cv2.CAP_DSHOW]
        if plat.startswith("linux"):
            return [cv2.CAP_V4L2, cv2.CAP_ANY]
        return [cv2.CAP_ANY]
    if key not in _BACKENDS:
        raise ValueError(f"unknown camera backend {name!r} (use auto, dshow, msmf, v4l2 or any)")
    return [_BACKENDS[key]]


def _backend_name(cap: Any, api: int) -> str:
    try:
        return str(cap.getBackendName())
    except cv2.error:
        return cv2.videoio_registry.getBackendName(api)


def _decode_fourcc(value: float) -> str:
    v = int(value)
    if v <= 0:
        return ""
    s = "".join(chr((v >> (8 * i)) & 0xFF) for i in range(4))
    return s if s.isprintable() else f"0x{v:08X}"


def _to_gray(img: np.ndarray) -> np.ndarray:
    """uint8 (H, W) from gray, BGR, BGRA or 16-bit camera output."""
    if img.dtype != np.uint8:
        img = (img >> 8).astype(np.uint8) if img.dtype == np.uint16 else cv2.convertScaleAbs(img)
    if img.ndim == 3:
        channels = img.shape[2]
        if channels == 1:
            return img[:, :, 0]
        if channels == 3:
            return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if channels == 4:
            return cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
        raise ValueError(f"unsupported frame with {channels} channels")
    return img


def _set_exposure(cap: Any, backend: str, value: float) -> bool:
    """Manual exposure in backend-native units (see the module docstring)."""
    if not cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, _AUTO_EXPOSURE_MANUAL) and backend.upper().startswith("V4L"):
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, _AUTO_EXPOSURE_MANUAL_V4L2_MENU)
    ok = bool(cap.set(cv2.CAP_PROP_EXPOSURE, float(value)))
    if not ok:
        log.warning("%s backend rejected exposure %s", backend, value)
    return ok


def _set_gain(cap: Any, backend: str, value: float) -> bool:
    ok = bool(cap.set(cv2.CAP_PROP_GAIN, float(value)))
    if not ok:
        log.warning("%s backend rejected gain %s", backend, value)
    return ok


@contextlib.contextmanager
def _quiet_opencv() -> Iterator[None]:
    """Silence OpenCV's own warnings while probing (process-wide, restored afterwards)."""
    cvlog = getattr(getattr(cv2, "utils", None), "logging", None)
    if cvlog is None:
        yield
        return
    old = cvlog.getLogLevel()
    cvlog.setLogLevel(cvlog.LOG_LEVEL_ERROR)
    try:
        yield
    finally:
        cvlog.setLogLevel(old)


def _v4l2_device_name(index: int) -> str | None:
    f = Path(f"/sys/class/video4linux/video{index}/name")
    try:
        return f.read_text(encoding="utf-8").strip() if f.exists() else None
    except OSError:
        return None


# --------------------------------------------------------------------------- frame times
def _lower_hull_slope(points: list[tuple[float, float]], max_abs: float) -> float:
    """Slope of the line under all ``points`` (sorted by x) that lies closest to them on average.

    That line is the edge of the points' lower convex hull above their mean x (the linear
    programme used for clock skew from one-way delays). Clamped to ``+-max_abs``.
    """
    hull: list[tuple[float, float]] = []
    for p in points:
        while len(hull) >= 2:
            (ox, oy), (ax, ay) = hull[-2], hull[-1]
            if (ax - ox) * (p[1] - oy) - (ay - oy) * (p[0] - ox) > 0:
                break
            hull.pop()
        hull.append(p)
    if len(hull) < 2:
        return 0.0
    x_mean = sum(p[0] for p in points) / len(points)
    for (ax, ay), (bx, by) in zip(hull, hull[1:]):
        if bx >= x_mean:
            slope = (by - ay) / (bx - ax) if bx > ax else 0.0
            return max(-max_abs, min(max_abs, slope))
    return 0.0


class _FrameClock:
    """Frame index and ``t_host`` from the camera's sample stamps and the frames' arrival times.

    ``push(stamp_ms, arrival)`` per grabbed frame returns ``(index, t_host)``, or ``None`` while
    the first frames are used to decide (they are not given out). ``mode`` is "learning",
    "camera" or "arrival".

    Deciding: a stamp that is not a positive number (DirectShow gives -1, captures without it 0)
    means arrival times at once. Otherwise after ``LEARN_FRAMES`` frames (or ``LEARN_SECONDS``):
    the period is the median of the smallest steps, and the stamps are used when every step is a
    whole number of periods to within ``LEARN_TOLERANCE`` and the stamps advance at the arrivals'
    pace to within ``RATE_MATCH`` (so a frame counter or stamps in other units are not taken for
    milliseconds). A stretch that fails this, or a stamp that does not advance, is not the last
    word: a PC busy while the camera starts hands held-up frames over together, and a stream's
    first stamps can be off its frame clock, which look the same. A fresh stretch is looked at, up
    to ``LEARN_TRIES`` stretches or ``LEARN_MAX_SECONDS``; then arrival times, with ``reason``.

    Stamps on the PC's clock (``source`` "host"): when every frame used to decide arrived 0 to
    ``HOST_MAX_LAG`` s after its stamp, the stamp is a ``perf_counter`` time in the recent past and
    ``t_host = stamp / 1000`` exactly: nothing learned, nothing to settle. They are still watched:
    the lowest ``arrival - stamp`` of each ``HOST_WINDOW_SECONDS`` window is kept, and when those
    lows spread over more than ``HOST_DRIFT`` (8 ms), or a frame arrives more than ``HOST_EARLY``
    before its stamp, the stamps are not on the PC's clock after all. The learned offset below
    takes over from that frame on, with a warning, starting exactly where the direct times were
    (it keeps their distance below the arrivals' edge and follows that edge from there), so
    ``t_host`` does not step. Why 8 ms: on the real camera the 30 s lows of ten minutes spread over
    3.9 ms (per minute 2.9 ms), so 8 ms is twice what was seen; a drift of 20 ppm is caught after
    about seven minutes, 100 ppm after about two.

    Learned offset (``source`` "learned", for stamps on another clock): with ``s`` the stamp in
    seconds since the first one and ``d = arrival - s``,
    every frame's ``d`` is at least the true offset between the clocks (a frame arrives late,
    never before it was taken), so the offset is the lower edge of the ``d`` points. That edge is
    a line: the two clocks may run some parts per million apart (on the ELP board under Media
    Foundation the stamps are already on the ``perf_counter`` clock, about 26 ms before the
    earliest arrival, and the measured rate is within 20 ppm of zero). Each ``BLOCK_SECONDS`` the
    lowest ``d`` of the block is kept (the last ``WINDOW_BLOCKS``, two minutes). Once they span
    ``RATE_AFTER_SECONDS`` the edge's slope (the rate) is the slope of their lower convex hull
    (clamped to ``MAX_RATE``); before that the rate is taken as zero and only the last
    ``EDGE_SECONDS_BEFORE_RATE`` count, so a drift is still followed with little lag. (The lowest
    arrivals are rare, at 120 fps a few a minute: a rate from fewer seconds was off by 140 ppm on
    the real camera.) The edge's height is the lowest point under that slope, a new frame counting
    at once. ``t_host = s + offset``, where the offset moves with the rate and is pulled
    toward the edge by at most ``SLEW`` of the elapsed time and ``MAX_STEP_CORRECTION`` per frame:
    t_host never jumps, a frame's time step differs from the period by tens of microseconds at
    most. A new lowest arrival 5 ms under the offset is reached in about 2.5 s at 60 fps. The
    constant delay between the exposure and the earliest arrival stays in ``t_host``; the motion
    sensor's calibrated time offset absorbs it.

    The index adds the number of periods each stamp step spans, so frames lost before the PC got
    them leave a gap. A step that is not a whole number of periods to within ``BREAK_TOLERANCE``
    (or goes back) means the stamps broke: arrival times from then on, with a warning. Arrival
    times are never earlier than camera times, so time keeps increasing.
    """

    LEARN_FRAMES = 20
    LEARN_SECONDS = 1.0
    LEARN_TRIES = 4
    LEARN_MAX_SECONDS = 2.0
    LEARN_TOLERANCE = 0.05
    BREAK_TOLERANCE = 0.25
    RATE_MATCH = 0.25
    MIN_PERIOD_MS, MAX_PERIOD_MS = 0.5, 2000.0
    BLOCK_SECONDS = 0.25
    WINDOW_BLOCKS = 480
    RATE_AFTER_SECONDS = 30.0
    EDGE_SECONDS_BEFORE_RATE = 15.0
    MAX_RATE = 300e-6
    SLEW = 2000e-6
    MAX_STEP_CORRECTION = 50e-6
    MIN_STEP = 1e-6
    HOST_MAX_LAG = 0.5
    HOST_WINDOW_SECONDS = 30.0
    HOST_DRIFT = 8e-3
    HOST_EARLY = 1e-3

    def __init__(self) -> None:
        self.mode = "learning"
        self.reason = ""
        self.period = 0.0  # seconds: the camera's in "camera" mode, the arrivals' in "arrival" mode
        self.source = ""  # camera mode: "host" (stamps used as they are) or "learned" (learned offset)
        self.rate = 0.0  # drift of perf_counter against the camera clock (arrival seconds per stamp second - 1)
        self._learn: list[tuple[float, float]] = []
        self._tries = 0
        self._learn_start: float | None = None
        self._next_index = 0
        self._last_t: float | None = None
        self._last_arrival: float | None = None
        # camera mode
        self._stamp0 = 0.0
        self._prev_stamp = 0.0
        self._prev_s = 0.0
        self._period_ms = 0.0
        self._d0 = 0.0
        self._offset = 0.0  # relative to _d0
        self._edge = 0.0  # height of the lower edge (at s = 0), relative to _d0
        self._points: collections.deque = collections.deque(maxlen=self.WINDOW_BLOCKS)
        self._block_end = 0.0
        self._block_min: tuple[float, float] | None = None

    # ------------------------------------------------------------------ entry
    def push(self, stamp_ms: float, arrival: float) -> tuple[int, float] | None:
        if self.mode == "camera":
            return self._camera(stamp_ms, arrival)
        if self.mode == "arrival":
            return self._arrival(arrival)
        if not (math.isfinite(stamp_ms) and stamp_ms > 0):
            return self._to_arrival(arrival, f"the capture gives no frame stamps ({stamp_ms})")
        if self._learn_start is None:
            self._learn_start = arrival
        if self._learn and stamp_ms <= self._learn[-1][0]:
            return self._look_again(arrival, "the frame stamps do not advance")
        self._learn.append((stamp_ms, arrival))
        n, elapsed = len(self._learn), arrival - self._learn[0][1]
        if n < self.LEARN_FRAMES and elapsed < self.LEARN_SECONDS:
            return None
        why = self._judge_stamps()
        if why:
            return self._look_again(arrival, why)
        return self._start_camera()

    def _look_again(self, arrival: float, why: str) -> tuple[int, float] | None:
        """This stretch is not a frame clock (``why``): start a fresh one, or give the stamps up."""
        self._tries += 1
        since = arrival if self._learn_start is None else self._learn_start
        if self._tries >= self.LEARN_TRIES or arrival - since >= self.LEARN_MAX_SECONDS:
            return self._to_arrival(arrival, why)
        log.info("camera frame stamps are not usable yet (%s); looking at the next frames", why)
        self._learn = []
        return None

    def _judge_stamps(self) -> str:
        st = np.array([p[0] for p in self._learn])
        ar = np.array([p[1] for p in self._learn])
        if len(st) < 5:
            return f"only {len(st)} frames in {self.LEARN_SECONDS:.1f} s"
        steps = np.diff(st)
        period = float(np.median(steps[steps <= 1.5 * steps.min()]))
        if not self.MIN_PERIOD_MS <= period <= self.MAX_PERIOD_MS:
            return f"implausible stamp period {period:.4g} ms"
        k = np.maximum(np.round(steps / period), 1.0)
        worst = float(np.max(np.abs(steps - k * period)) / period)
        if worst > self.LEARN_TOLERANCE:
            return f"stamp steps are not whole periods (off by {100 * worst:.0f} % of {period:.3f} ms)"
        span_a = float(ar[-1] - ar[0])
        pace = (float(st[-1] - st[0]) / 1e3) / span_a if span_a > 0 else math.inf
        if abs(pace - 1.0) > self.RATE_MATCH:
            return f"stamps advance {pace:.3g} times as fast as the arrivals"
        self._period_ms = period
        return ""

    # ------------------------------------------------------------------ arrival times
    def _to_arrival(self, arrival: float, reason: str) -> tuple[int, float]:
        was = self.mode
        self.mode, self.reason, self.source = "arrival", reason, ""
        if was == "camera":
            log.warning("camera frame stamps stopped being usable (%s); frames are timed on arrival now", reason)
        elif self._tries:  # the capture gives stamps, and they were looked at more than once
            log.warning("camera frame stamps are not usable (%s); frames are timed on arrival", reason)
        else:
            log.info("camera frames are timed on arrival: %s", reason)
        self.period = 0.0
        self._last_arrival = None
        return self._arrival(arrival)

    def _arrival(self, arrival: float) -> tuple[int, float]:
        if self._last_arrival is not None and arrival > self._last_arrival:
            step = arrival - self._last_arrival
            self.period = step if self.period == 0.0 else 0.98 * self.period + 0.02 * step
        self._last_arrival = arrival
        return self._give(1, arrival)

    def _give(self, k: int, t: float) -> tuple[int, float]:
        if self._last_t is not None and t <= self._last_t:
            t = self._last_t + self.MIN_STEP
        index = self._next_index if self._last_t is None else self._next_index + k - 1
        self._next_index = index + 1
        self._last_t = t
        return index, t

    # ------------------------------------------------------------------ camera times
    def _start_camera(self) -> tuple[int, float]:
        self.mode, self.reason = "camera", ""
        self.period = self._period_ms / 1e3
        self._stamp0 = self._learn[0][0]
        pts = [((st - self._stamp0) / 1e3, a) for st, a in self._learn]
        self._d0 = pts[0][1] - pts[0][0]
        self._block_end = self.BLOCK_SECONDS
        for s, a in pts:
            self._add_point(s, a - s - self._d0)
        self._edge = min(a - s - self._d0 for s, a in pts)
        self._offset = self._edge
        self._gap = 0.0
        lags = [a - st / 1e3 for st, a in self._learn]
        self.source = "host" if all(0.0 <= g <= self.HOST_MAX_LAG for g in lags) else "learned"
        if self.source == "host":  # the learned offset, kept where the direct times are, for a later switch
            self._offset = self._stamp0 / 1e3 - self._d0
            self._host_end, self._host_min = self.HOST_WINDOW_SECONDS, min(lags)
            self._host_lo = self._host_hi = math.nan
        st, a = self._learn[-1]
        self._prev_stamp, self._prev_s = st, pts[-1][0]
        self._learn = []
        if self.source == "host":
            log.info("camera frames are timed by the camera's own stamps, which are perf_counter times "
                     "(%.1f ms before the earliest arrival): period %.5f ms", 1e3 * min(lags), self._period_ms)
            return self._give(1, st / 1e3)
        log.info("camera frames are timed by the camera's clock with a learned offset: period %.5f ms", self._period_ms)
        return self._give(1, self._prev_s + self._d0 + self._offset)

    def _host_check(self, s: float, lag: float) -> str:
        """Why the stamps are not perf_counter times after all, or "" while they still are."""
        if lag < -self.HOST_EARLY:
            return f"a frame arrived {-1e3 * lag:.1f} ms before its stamp"
        if s >= self._host_end:
            lo, hi = self._host_lo, self._host_hi
            self._host_lo = self._host_min if math.isnan(lo) else min(lo, self._host_min)
            self._host_hi = self._host_min if math.isnan(hi) else max(hi, self._host_min)
            self._host_min = math.inf
            self._host_end = (math.floor(s / self.HOST_WINDOW_SECONDS) + 1) * self.HOST_WINDOW_SECONDS
            if self._host_hi - self._host_lo > self.HOST_DRIFT:
                return (f"the earliest arrivals moved {1e3 * (self._host_hi - self._host_lo):.1f} ms against "
                        "the stamps")
        self._host_min = min(self._host_min, lag)
        return ""

    def _add_point(self, s: float, d: float) -> None:
        if s >= self._block_end:
            if self._block_min is not None:
                self._points.append(self._block_min)
                self._refit()
            self._block_min = None
            self._block_end = (math.floor(s / self.BLOCK_SECONDS) + 1) * self.BLOCK_SECONDS
        if self._block_min is None or d < self._block_min[1]:
            self._block_min = (s, d)

    def _refit(self) -> None:
        pts = list(self._points)
        if pts[-1][0] - pts[0][0] >= self.RATE_AFTER_SECONDS:
            self.rate = _lower_hull_slope(pts, self.MAX_RATE)
        else:  # rate not known yet: only the recent edge, so a drift is followed with little lag
            pts = [p for p in pts if p[0] >= pts[-1][0] - self.EDGE_SECONDS_BEFORE_RATE]
        r = self.rate
        edge = min(d - r * s for s, d in pts)
        if self._block_min is not None:
            edge = min(edge, self._block_min[1] - r * self._block_min[0])
        self._edge = edge

    def _camera(self, stamp_ms: float, arrival: float) -> tuple[int, float]:
        step = stamp_ms - self._prev_stamp
        k = round(step / self._period_ms) if math.isfinite(step) else 0
        if k < 1 or abs(step - k * self._period_ms) > self.BREAK_TOLERANCE * self._period_ms:
            return self._to_arrival(arrival, f"stamp step {step:.3f} ms on a period of {self._period_ms:.3f} ms")
        self._period_ms += 0.01 * (step / k - self._period_ms)
        self.period = self._period_ms / 1e3
        s = (stamp_ms - self._stamp0) / 1e3
        d = arrival - s - self._d0
        self._add_point(s, d)
        self._edge = min(self._edge, d - self.rate * s)
        if self.source == "host":
            why = self._host_check(s, arrival - stamp_ms / 1e3)
            if not why:
                self._prev_stamp, self._prev_s = stamp_ms, s
                return self._give(k, stamp_ms / 1e3)
            log.warning("camera stamps are not perf_counter times after all (%s); a learned offset carries "
                        "them over from here, starting where they were", why)
            self.source, self.reason = "learned", why
            self._gap = self._edge + self.rate * self._prev_s - self._offset  # the target starts at the offset
        dt = s - self._prev_s
        pred = self._offset + self.rate * dt
        target = self._edge + self.rate * s - self._gap
        limit = min(self.SLEW * dt, self.MAX_STEP_CORRECTION)
        self._offset = pred + max(-limit, min(limit, target - pred))
        self._prev_stamp, self._prev_s = stamp_ms, s
        return self._give(k, s + self._d0 + self._offset)


class _WaitJudge:
    """Which capture call waits for the frame, so arrival times are taken after it.

    DirectShow's ``grab()`` returns at once and ``retrieve()`` waits (16 ms at 60 fps); Media
    Foundation's ``grab()`` waits. The call with the longer median duration over the first
    ``FRAMES`` frames is taken (until then the running medians decide), then kept.
    """

    FRAMES = 30

    def __init__(self) -> None:
        self.after: str | None = None
        self._grab: list[float] = []
        self._retrieve: list[float] = []

    def arrival(self, t_before: float, t_grab: float, t_retrieve: float) -> float:
        if self.after is None:
            self._grab.append(t_grab - t_before)
            self._retrieve.append(t_retrieve - t_grab)
            after = "grab" if float(np.median(self._grab)) >= float(np.median(self._retrieve)) else "retrieve"
            if len(self._grab) >= self.FRAMES:
                self.after = after
                log.debug("camera frames are stamped after %s()", after)
        else:
            after = self.after
        return t_grab if after == "grab" else t_retrieve


# --------------------------------------------------------------------------- live camera
class StereoCamera:
    """Side-by-side UVC stereo camera with a background grab thread. Use as a context manager."""

    def __init__(self, params: CameraParams | None = None, *, first_frame_timeout: float = 5.0,
                 dead_after_s: float = 3.0) -> None:
        self.params = dataclasses.replace(params) if params is not None else CameraParams()
        self._first_frame_timeout = float(first_frame_timeout)
        self._dead_after_s = float(dead_after_s)
        self._caps: list[Any] = []
        self._api = cv2.CAP_ANY
        self._backend = ""
        self._cap_lock = threading.Lock()  # VideoCapture is not thread-safe: grab vs set/get
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest: StereoFrame | None = None
        self._last_read = -1
        self._n_frames = 0
        self._fps_est = 0.0
        self._error: str | None = None
        self._clock = _FrameClock()
        self._judge = _WaitJudge()

    # ------------------------------------------------------------------ lifecycle
    def open(self) -> None:
        """Open the camera with the first backend that opens it and gives a frame."""
        if self._caps:
            return
        p = self.params
        if p.fourcc and len(p.fourcc) != 4:
            raise ValueError(f"fourcc must have exactly 4 characters, got {p.fourcc!r}")
        apis = _backend_candidates(p.backend)
        tried = []
        for n, api in enumerate(apis):
            name = cv2.videoio_registry.getBackendName(api)
            caps = self._open_caps(api)
            if caps is None:
                tried.append(name)
                continue
            try:
                self._start(caps, api)
            except (TimeoutError, RuntimeError) as e:
                if n == len(apis) - 1:
                    raise
                tried.append(name)
                log.warning("camera %d opened with %s but gave no frame (%s); trying %s", p.index, name, e,
                            cv2.videoio_registry.getBackendName(apis[n + 1]))
                continue
            return
        raise RuntimeError(f"could not open camera {p.index} (backends tried: {', '.join(tried)})")

    def _open_caps(self, api: int) -> list[Any] | None:
        """The capture(s) set up with one backend; ``None`` if the (first) camera does not open."""
        p = self.params
        two = p.second_index is not None
        width = p.width // 2 if two else p.width
        cap = self._open_one(p.index, api, width)
        if cap is None:
            return None
        caps = [cap]
        try:
            if two:
                log.warning(
                    "second_index=%d: using two separate UVC cameras; their frames are NOT "
                    "hardware-synchronised, so stereo on a moving scanner will be wrong", p.second_index)
                cap2 = self._open_one(p.second_index, api, width)
                if cap2 is None:
                    raise RuntimeError(f"could not open the second camera {p.second_index}")
                caps.append(cap2)
            backend = _backend_name(caps[0], api)
            for c in caps:
                if p.exposure is not None:
                    _set_exposure(c, backend, p.exposure)
                if p.gain is not None:
                    _set_gain(c, backend, p.gain)
        except BaseException:
            for c in caps:
                c.release()
            raise
        return caps

    def _start(self, caps: list[Any], api: int) -> None:
        """Start the grab thread on opened captures and wait for the first frame (else close and raise)."""
        p = self.params
        self._caps, self._api, self._backend = caps, api, _backend_name(caps[0], api)
        self._latest, self._last_read, self._n_frames = None, -1, 0
        self._fps_est, self._error = 0.0, None
        self._clock, self._judge = _FrameClock(), _WaitJudge()
        self._stop.clear()
        self._thread = threading.Thread(target=self._grab_loop, name="camera-grab", daemon=True)
        self._thread.start()
        with self._cond:
            self._cond.wait_for(lambda: self._latest is not None or self._error is not None,
                                timeout=self._first_frame_timeout)
            first, error = self._latest, self._error
        if first is None:
            self.close()
            if error is not None:
                raise RuntimeError(error)
            raise TimeoutError(f"camera {p.index} opened but delivered no frame within {self._first_frame_timeout:.1f} s")
        w, h = first.image_size
        if (2 * w, h) != (p.width, p.height):
            log.warning("camera delivers %dx%d stereo frames, requested %dx%d", 2 * w, h, p.width, p.height)
        log.info("camera open: %s", self.info)

    def _open_one(self, index: int, api: int, width: int) -> Any | None:
        cap = cv2.VideoCapture(index, api)
        if not cap.isOpened():
            cap.release()
            return None
        p = self.params
        fourcc = float(cv2.VideoWriter_fourcc(*p.fourcc)) if p.fourcc else None
        if fourcc is not None:  # format first: some cameras only offer the large MJPG modes once it is selected
            cap.set(cv2.CAP_PROP_FOURCC, fourcc)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(width))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(p.height))
        cap.set(cv2.CAP_PROP_FPS, float(p.fps))
        if fourcc is not None and _decode_fourcc(cap.get(cv2.CAP_PROP_FOURCC)) != p.fourcc:
            # Others (the ELP OV9281 stereo board on DirectShow) drop it when the size is set and fall
            # back to raw YUY2 at 5 fps; asking again after the size gives MJPG at 60 fps.
            cap.set(cv2.CAP_PROP_FOURCC, fourcc)
        return cap

    def close(self) -> None:
        self._stop.set()
        th = self._thread
        if th is not None:
            th.join(timeout=2.0)
            if th.is_alive():
                log.warning("camera grab thread did not stop")
        self._thread = None
        if self._cap_lock.acquire(timeout=2.0):
            try:
                for c in self._caps:
                    c.release()
            finally:
                self._cap_lock.release()
        elif self._caps:
            log.warning("camera is stuck in grab(); not releasing it")
        self._caps = []

    def __enter__(self) -> "StereoCamera":
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ frames
    def _grab_loop(self) -> None:
        caps = self._caps
        clock, judge = self._clock, self._judge
        last_ok = time.perf_counter()
        t_prev: float | None = None
        while not self._stop.is_set():
            imgs = []
            stamp = math.nan
            try:
                with self._cap_lock:
                    t_before = time.perf_counter()
                    ok = bool(caps[0].grab())
                    t_grab = t_retrieve = time.perf_counter()
                    if ok:
                        stamp = float(caps[0].get(cv2.CAP_PROP_POS_MSEC))  # the camera's sample time, if any
                        ok = all(c.grab() for c in caps[1:])
                    if ok:
                        for j, c in enumerate(caps):
                            got, img = c.retrieve()
                            if j == 0:
                                t_retrieve = time.perf_counter()
                            if not got or img is None:
                                ok = False
                                break
                            imgs.append(img)
            except cv2.error as e:
                log.debug("grab failed: %s", e)
                ok = False
            if not ok:
                if time.perf_counter() - last_ok > self._dead_after_s:
                    self._fail(f"camera {self.params.index} delivered no frame for {self._dead_after_s:.1f} s")
                    return
                time.sleep(0.005)
                continue
            arrival = judge.arrival(t_before, t_grab, t_retrieve)  # after the call that waited for the frame
            last_ok = arrival
            timed = clock.push(stamp, arrival)
            if timed is None:  # still deciding how to time the frames
                continue
            index, t = timed
            try:
                frame = self._make_frame(index, t, imgs)
            except (ValueError, cv2.error) as e:
                log.warning("dropping malformed frame %d: %s", index, e)
                continue
            if t_prev is not None and t > t_prev:
                inst = 1.0 / (t - t_prev)
                self._fps_est = inst if self._fps_est == 0.0 else 0.9 * self._fps_est + 0.1 * inst
            t_prev = t
            with self._cond:
                self._latest = frame
                self._n_frames += 1
                self._cond.notify_all()

    def _make_frame(self, index: int, t: float, imgs: list[np.ndarray]) -> StereoFrame:
        if len(imgs) == 1:
            return StereoFrame.from_sbs(index, t, _to_gray(imgs[0]), swap_lr=self.params.swap_lr)
        left, right = _to_gray(imgs[0]), _to_gray(imgs[1])
        if left.shape != right.shape:
            raise ValueError(f"camera sizes differ: {left.shape} vs {right.shape}")
        if self.params.swap_lr:
            left, right = right, left
        return StereoFrame(index, t, np.ascontiguousarray(left), np.ascontiguousarray(right))

    def _fail(self, msg: str) -> None:
        log.error(msg)
        with self._cond:
            self._error = msg
            self._cond.notify_all()

    def read(self, timeout: float = 1.0) -> StereoFrame:
        """Newest frame not returned before; waits up to ``timeout`` s, else ``TimeoutError``."""
        if not self._caps:
            raise RuntimeError("camera is not open")
        deadline = time.perf_counter() + timeout
        with self._cond:
            while True:
                f = self._latest
                if f is not None and f.index > self._last_read:
                    self._last_read = f.index
                    return f
                if self._error is not None:
                    raise RuntimeError(self._error)
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    raise TimeoutError(f"no new camera frame within {timeout:.3f} s")
                self._cond.wait(remaining)

    # ------------------------------------------------------------------ controls / info
    def set_exposure(self, value: float) -> None:
        """Manual exposure in backend-native units (module docstring). Also used on re-open."""
        self.params.exposure = float(value)
        with self._cap_lock:
            for c in self._caps:
                _set_exposure(c, self._backend, value)

    def set_gain(self, value: float) -> None:
        self.params.gain = float(value)
        with self._cap_lock:
            for c in self._caps:
                _set_gain(c, self._backend, value)

    @property
    def info(self) -> dict:
        """Actual stereo frame width/height (side-by-side), fps, backend and fourcc.

        Once open, also where the frame times come from (``frame_times``: "camera" for the
        camera's frame clock, "arrival" for arrival times) and the frame period found in ms
        (``frame_period_ms``: the camera's, or the arrivals' average).
        """
        p = self.params
        d: dict[str, Any] = {"index": p.index, "second_index": p.second_index, "synced": p.second_index is None,
                             "swap_lr": p.swap_lr, "open": bool(self._caps), "backend": self._backend}
        if not self._caps:
            d.update(width=p.width, height=p.height, fps=p.fps, fourcc=p.fourcc, measured_fps=0.0, frames=0)
            return d
        with self._cap_lock:
            c = self._caps[0]
            w, h = int(c.get(cv2.CAP_PROP_FRAME_WIDTH)), int(c.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps, fourcc = float(c.get(cv2.CAP_PROP_FPS)), _decode_fourcc(c.get(cv2.CAP_PROP_FOURCC))
        if len(self._caps) == 2:
            w *= 2
        f = self._latest
        if f is not None:  # the delivered frames are the ground truth
            w, h = 2 * f.image_size[0], f.image_size[1]
        d.update(width=w, height=h, fps=fps, fourcc=fourcc, measured_fps=round(self._fps_est, 2), frames=self._n_frames,
                 frame_times=self._clock.mode, frame_clock=self._clock.source or None,
                 frame_period_ms=round(self._clock.period * 1e3, 6),
                 frame_times_reason=self._clock.reason or None)
        return d


def list_cameras(max_index: int = 8, backend: str = "auto") -> list[dict]:
    """Probe indices ``0..max_index-1``: ``[{"index", "backend", "width", "height", "fps", "fourcc"[, "name"]}]``.

    Sizes are the camera's default mode (not necessarily 2560x800). Uses the first backend of
    ``backend`` (Media Foundation on Windows for "auto"). ``name`` is only available on Linux.
    """
    api = _backend_candidates(backend)[0]
    found: list[dict] = []
    with _quiet_opencv():
        for i in range(max_index):
            cap = cv2.VideoCapture(i, api)
            try:
                if not cap.isOpened():
                    continue
                entry: dict[str, Any] = {
                    "index": i,
                    "backend": _backend_name(cap, api),
                    "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                    "fps": float(cap.get(cv2.CAP_PROP_FPS)),
                    "fourcc": _decode_fourcc(cap.get(cv2.CAP_PROP_FOURCC)),
                }
                name = _v4l2_device_name(i)
                if name:
                    entry["name"] = name
                found.append(entry)
            finally:
                cap.release()
    log.debug("cameras found: %s", found)
    return found


# --------------------------------------------------------------------------- replay
class ReplayCamera:
    """Plays a recording folder through the :class:`StereoCamera` interface.

    * ``realtime=True``: frames become due at their recorded spacing, starting with the first
      ``read()``. Like a live camera, ``read()`` returns the newest due frame not yet returned
      (a slow consumer skips frames) and raises ``TimeoutError`` if none is due within
      ``timeout``. ``realtime=False`` returns every frame in order, as fast as requested.
    * Frames keep the recording's own ``t_host``, so the recording's IMU samples and events stay
      aligned (``reader`` gives access to them). With ``restamp=True``, ``t_host`` is
      ``perf_counter()`` at read time instead, like a live camera.
    * ``loop=True`` restarts at the end. Pass ``k`` adds ``k * (max_index + 1)`` to the indices and
      ``k * duration`` (duration = last - first + one frame period) to ``t_host``, so both stay
      monotonic. ``frame.index % (max_index + 1)`` recovers the recorded index.
    * Without ``loop``, ``read()`` raises ``EOFError`` once every frame was returned.
    """

    def __init__(self, recording_path: str | Path, realtime: bool = True, loop: bool = False,
                 restamp: bool = False) -> None:
        self.path = Path(recording_path)
        self.realtime = bool(realtime)
        self.loop = bool(loop)
        self.restamp = bool(restamp)
        self.reader: RecordingReader | None = None
        self._lock = threading.Lock()
        self._pos: tuple[int, int] = (0, -1)  # (pass, position) of the last frame returned
        self._t0: float | None = None
        self._n_read = 0

    def open(self) -> None:
        if self.reader is not None:
            return
        reader = RecordingReader(self.path)
        if len(reader) == 0:
            raise ValueError(f"recording {self.path} has no frames")
        self._times = reader.times()
        self._idx = reader.indices
        self._rel = self._times - self._times[0]
        steps = np.diff(self._times)
        med = float(np.median(steps)) if len(steps) else 0.0
        self._period = med if med > 0 else 1.0 / 30.0
        self._span_t = float(self._rel[-1]) + self._period
        self._span_i = int(self._idx.max()) + 1
        self._pos, self._t0, self._n_read = (0, -1), None, 0
        self.reader = reader
        log.info("replaying %s: %d frames at %.1f fps%s", self.path, len(reader), 1.0 / self._period,
                 " (loop)" if self.loop else "")

    def close(self) -> None:
        self.reader = None

    def __enter__(self) -> "ReplayCamera":
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def finished(self) -> bool:
        """True once a non-looping replay has returned its last frame."""
        return self.reader is not None and not self.loop and self._pos[1] >= len(self._rel) - 1

    def _next_after(self, pos: tuple[int, int]) -> tuple[int, int] | None:
        k, i = pos
        if i + 1 < len(self._rel):
            return k, i + 1
        return (k + 1, 0) if self.loop else None

    def _due_at(self, elapsed: float) -> tuple[int, int]:
        if self.loop:
            k = int(elapsed // self._span_t)
            e = elapsed - k * self._span_t
        else:
            k, e = 0, min(elapsed, float(self._rel[-1]))
        return k, max(int(np.searchsorted(self._rel, e, side="right")) - 1, 0)

    def _emit(self, k: int, i: int) -> StereoFrame:
        f = self.reader.frame(i)
        t = time.perf_counter() if self.restamp else float(self._times[i]) + k * self._span_t
        self._pos = (k, i)
        self._n_read += 1
        return StereoFrame(int(self._idx[i]) + k * self._span_i, t, f.left, f.right)

    def read(self, timeout: float = 1.0) -> StereoFrame:
        with self._lock:
            if self.reader is None:
                raise RuntimeError("ReplayCamera is not open")
            if not self.realtime:
                nxt = self._next_after(self._pos)
                if nxt is None:
                    raise EOFError(f"end of recording {self.path}")
                return self._emit(*nxt)
            now = time.perf_counter()
            if self._t0 is None:
                self._t0 = now
            deadline = now + timeout
            while True:
                now = time.perf_counter()
                due = self._due_at(now - self._t0)
                if due > self._pos:
                    return self._emit(*due)
                nxt = self._next_after(self._pos)
                if nxt is None:
                    raise EOFError(f"end of recording {self.path}")
                t_next = self._t0 + nxt[0] * self._span_t + float(self._rel[nxt[1]])
                if t_next > deadline:
                    time.sleep(max(0.0, deadline - now))
                    raise TimeoutError(f"no replay frame due within {timeout:.3f} s")
                time.sleep(max(0.0, t_next - now))

    def set_exposure(self, value: float) -> None:
        log.debug("ReplayCamera ignores set_exposure(%s)", value)

    def set_gain(self, value: float) -> None:
        log.debug("ReplayCamera ignores set_gain(%s)", value)

    @property
    def info(self) -> dict:
        d: dict[str, Any] = {"backend": "replay", "path": str(self.path), "fourcc": "PNG", "synced": True,
                             "realtime": self.realtime, "loop": self.loop, "open": self.reader is not None}
        if self.reader is None:
            return d
        size = self.reader.meta.get("image_size")
        if not size:
            size = list(self.reader.frame(0).image_size)
        d.update(width=2 * int(size[0]), height=int(size[1]), fps=1.0 / self._period, frames=len(self._rel),
                 frames_read=self._n_read, source=self.reader.meta.get("source"))
        return d
