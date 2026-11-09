"""Preview images for the live view: laser overlay, depth colour map, JPEG, distance gauge.

* ``left`` preview: the rectified left image at the live scale with the laser-line centre
  polylines drawn by :func:`ohs.vision.laser.draw_overlay` (red = vertical, green = horizontal);
  without a calibration, the raw left image scaled down.
* ``depth`` preview: :func:`ohs.app.live.depth_to_bgr` (TURBO colour map, near = red, far =
  blue, invalid = black) over the live depth range.
* :func:`central_distance`: the median valid depth in the central 40 % (width and height) of the
  depth map, the number behind the distance gauge.

:class:`ViewTools` computes the cheap "view only" products of a frame (rectify, SGBM depth, laser
lines) for when the full pipeline is not running: before a scan starts and while it is paused.
:class:`PreviewPublisher` rate-limits, encodes and publishes previews to the hub.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from ohs.server.protocol import preview_message

__all__ = ["central_distance", "left_view", "depth_view", "encode_jpeg", "ViewTools", "PreviewPublisher"]

log = logging.getLogger(__name__)

JPEG_QUALITY = 80


def central_distance(depth: np.ndarray | None, frac: float = 0.4) -> float | None:
    """Median valid depth (mm) in the central ``frac`` x ``frac`` of the image, or None."""
    if depth is None:
        return None
    d = np.asarray(depth)
    if d.ndim != 2 or d.size == 0:
        return None
    h, w = d.shape
    y0, y1 = int(round(h * (0.5 - frac / 2))), int(round(h * (0.5 + frac / 2)))
    x0, x1 = int(round(w * (0.5 - frac / 2))), int(round(w * (0.5 + frac / 2)))
    c = d[y0:y1, x0:x1]
    v = c[np.isfinite(c) & (c > 0)]
    if v.size < max(10, 0.02 * c.size):
        return None
    return float(np.median(v))


def left_view(left: np.ndarray, lines: Any = None) -> np.ndarray:
    """BGR image of the (rectified) left camera with the laser overlay if ``lines`` is given."""
    if lines is not None:
        from ohs.vision.laser import draw_overlay

        return draw_overlay(left, lines, 1)
    a = np.asarray(left)
    if a.ndim == 3:
        return a
    if a.dtype != np.uint8:
        a = cv2.convertScaleAbs(a)
    return cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)


def depth_view(depth: np.ndarray, dmin: float, dmax: float) -> np.ndarray:
    from ohs.app.live import depth_to_bgr

    return depth_to_bgr(depth, dmin, dmax)


def encode_jpeg(img: np.ndarray, quality: int = JPEG_QUALITY) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return buf.tobytes()


@dataclass
class View:
    left: np.ndarray
    depth: np.ndarray | None
    lines: Any
    rectified: bool


class ViewTools:
    """Rectifier + depth estimator + laser extraction at the live scale (used in one thread only)."""

    def __init__(self, calib: Any, scale: float, depth_range: tuple[float, float], *, rectifier: Any = None,
                 depth_estimator: Any = None, laser_params: Any = None) -> None:
        self.calib = calib
        self.scale = float(scale)
        self.depth_range = (float(depth_range[0]), float(depth_range[1]))
        self._rect = rectifier
        self._depth = depth_estimator
        self._lp = laser_params
        self._ready = False

    @classmethod
    def from_pipeline(cls, pipe: Any, depth_range: tuple[float, float]) -> "ViewTools":
        return cls(pipe.calib, pipe.params.scale, depth_range, rectifier=pipe.rectifier,
                   depth_estimator=pipe.depth_estimator, laser_params=pipe.laser_params)

    def _setup(self) -> None:
        self._ready = True
        if self.calib is None:
            return
        from ohs.app.pipeline import default_depth_params, default_laser_params, effective_scale
        from ohs.vision.depth import DepthEstimator
        from ohs.vision.rectify import Rectifier

        if self._rect is None:
            self._rect = Rectifier(self.calib.stereo, self.scale)
        s_eff = effective_scale(self._rect.image_size[0])
        if self._depth is None:
            import dataclasses

            dp = dataclasses.replace(default_depth_params(s_eff), min_depth_mm=self.depth_range[0],
                                     max_depth_mm=self.depth_range[1])
            self._depth = DepthEstimator(self._rect.K, self._rect.baseline, dp)
        if self._lp is None:
            self._lp = default_laser_params(s_eff)

    def view(self, frame: Any, depth: bool = True, lines: bool = True) -> View:
        if not self._ready:
            self._setup()
        if self._rect is None:  # no calibration: raw left image at the live scale
            left = frame.left
            if self.scale != 1.0:
                h, w = left.shape[:2]
                left = cv2.resize(left, (max(1, int(round(w * self.scale))), max(1, int(round(h * self.scale)))),
                                  interpolation=cv2.INTER_AREA)
            return View(left, None, None, False)
        left_r, right_r = self._rect.rectify(frame.left, frame.right)
        d = self._depth.compute(left_r, right_r) if (depth and self._depth is not None) else None
        ln = None
        if lines and self._lp is not None:
            from ohs.vision.laser import extract_laser_lines

            ln = extract_laser_lines(left_r, self._lp)
        return View(left_r, d, ln, True)


class PreviewPublisher:
    """Encodes and publishes ``left`` / ``depth`` previews at most at the clients' requested rate."""

    def __init__(self, hub: Any, depth_range: tuple[float, float], max_hz: float = 15.0) -> None:
        self.hub = hub
        self.depth_range = (float(depth_range[0]), float(depth_range[1]))
        self.max_hz = float(max_hz)
        self._t_last = 0.0
        self._seq = 0
        self._lock = threading.Lock()
        self.encode_ms = 0.0

    def due(self) -> bool:
        """True when a preview should be produced now (someone wants it and the rate allows it)."""
        hz = min(self.max_hz, self.hub.preview_hz())
        if hz <= 0:
            return False
        return time.monotonic() - self._t_last >= 1.0 / hz - 0.004

    def publish(self, left: np.ndarray, lines: Any = None, depth: np.ndarray | None = None) -> None:
        t0 = time.perf_counter()
        with self._lock:
            self._t_last = time.monotonic()
            self._seq += 1
            seq = self._seq
        img = left_view(left, lines)
        h, w = img.shape[:2]
        self.hub.publish_binary("preview:left", preview_message("left", encode_jpeg(img), w, h, seq))
        if depth is not None:
            dimg = depth_view(depth, *self.depth_range)
            dh, dw = dimg.shape[:2]
            self.hub.publish_binary("preview:depth", preview_message("depth", encode_jpeg(dimg), dw, dh, seq))
        self.encode_ms = 1e3 * (time.perf_counter() - t0)
