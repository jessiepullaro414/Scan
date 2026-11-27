"""Matcher depth of a few recorded frames, on demand, for a reading at a click (ENGINE_PLAN X16, the wall's
thickness; the hole's rim reading may use it too).

A measurement that reads the scan's own camera frames near a click needs, for a handful of frames: the frame's depth
on the rectified left camera, that camera's intrinsics and image size, and the frame's pose (``T_wc`` of the rectified
left camera, from the scan's camera path). :class:`FrameDepth` gives the first three for one recording; the poses come
from the scan's ``trajectory.csv`` (``ohs.track.posegraph.read_trajectory``), in the same frame as the scan's mesh.

The depth is recomputed, not kept from Make mesh: keeping every frame's depth costs about 7.8 MB a frame on disk
(0.9 to 1.9 GB for a scan of 120 to 240 frames, for as long as the scan exists), while a reading needs 6 to 30
frames, a few seconds with a graphics card (about 0.2 s a frame) and well under a minute without one (about 0.6 s).
The matcher runs at full resolution with the pipeline's settings for that width and the scan's depth range, on the
graphics card when there is one (``ohs.vision.depth.DepthEstimator`` decides), as Make mesh ran it.

    fd = FrameDepth(recording_dir, calib, (min_mm, max_mm))
    D = fd.depth(index)                       # (h, w) float32 mm, 0 = no depth; index = the recording's frame index
    P, N = fd.points(index, T_wc, near=(p, r))  # world points and normals (facing the camera) within r of p
    fd.K, fd.image_size                       # the rectified left camera's intrinsics and (w, h)
    L, R = fd.images(index)                   # the raw left and right images, grey, as recorded (the hole's rim)
    D, S = fd.depth_and_sigma(index)          # the same depth and the matcher's sigma of it (mm)

The wall reading uses :meth:`FrameDepth.depth` (made each time); the hole reading (ENGINE_PLAN X15) uses
:meth:`FrameDepth.images` and :meth:`FrameDepth.depth_and_sigma`, the last :data:`KEEP_FRAMES` of each kept, since each
click on a hole reads the same frames again.

Not thread-safe: one reading at a time (the server holds a lock around it).
"""
from __future__ import annotations

import dataclasses
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["FrameDepth", "frame_points", "visible_from", "KEEP_FRAMES"]

#: the hole reading's frames kept for the next click (images and depth with sigma: about 10 MB a frame at 1280 x 800)
KEEP_FRAMES = 24


def frame_points(depth: np.ndarray, K: np.ndarray, T_wc: np.ndarray, near: tuple[np.ndarray, float] | None = None,
                 step: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """A depth map (z in the rectified left camera, mm; 0 = none) back-projected to world points with normals (central
    differences over 2 px, facing the camera), every ``step`` px, only where all four neighbours 2 px away have depth;
    with ``near = (point, radius)`` only the points within that radius of the point."""
    D = np.asarray(depth, dtype=np.float64)
    H, W = D.shape
    v, u = np.mgrid[0:H, 0:W]
    X = np.stack([(u - K[0, 2]) / K[0, 0] * D, (v - K[1, 2]) / K[1, 1] * D, D], -1)
    N = np.zeros_like(X)
    N[2:-2, 2:-2] = np.cross(X[2:-2, 4:] - X[2:-2, :-4], X[4:, 2:-2] - X[:-4, 2:-2])
    nn = np.linalg.norm(N, axis=-1)
    N = N / np.maximum(nn, 1e-12)[..., None]
    N[N[..., 2] > 0] *= -1.0
    ok = (D > 0) & (nn > 0)
    for dy, dx in ((0, 2), (2, 0), (0, -2), (-2, 0)):
        ok &= np.roll(D, (dy, dx), axis=(0, 1)) > 0
    sel = np.zeros_like(ok)
    sel[::step, ::step] = True
    ok &= sel
    T = np.asarray(T_wc, dtype=np.float64)
    P, Nw = X[ok] @ T[:3, :3].T + T[:3, 3], N[ok] @ T[:3, :3].T
    if near is not None:
        m = np.linalg.norm(P - np.asarray(near[0], dtype=np.float64), axis=1) <= float(near[1])
        P, Nw = P[m], Nw[m]
    return P, Nw


def visible_from(T_wc: np.ndarray, K: np.ndarray, size: tuple[int, int], point: np.ndarray,
                 depth_range: tuple[float, float], border_px: float = 40.0) -> bool:
    """Whether ``point`` (world) falls in the camera's image at ``T_wc``, at least ``border_px`` from its edges, at a
    depth inside ``depth_range``."""
    T = np.asarray(T_wc, dtype=np.float64)
    q = (np.asarray(point, dtype=np.float64) - T[:3, 3]) @ T[:3, :3]
    if not depth_range[0] <= q[2] <= depth_range[1]:
        return False
    u, v = K[0, 0] * q[0] / q[2] + K[0, 2], K[1, 1] * q[1] / q[2] + K[1, 2]
    return bool(border_px <= u < size[0] - border_px and border_px <= v < size[1] - border_px)


class FrameDepth:
    """The stereo matcher's depth of a recording's frames, made on demand (module docstring)."""

    def __init__(self, recording_dir: str | Path, calib: Any, depth_range: tuple[float, float]) -> None:
        from ohs.app.pipeline import default_depth_params, effective_scale  # noqa: PLC0415
        from ohs.io.recording import RecordingReader  # noqa: PLC0415
        from ohs.vision.depth import DepthEstimator  # noqa: PLC0415
        from ohs.vision.rectify import Rectifier  # noqa: PLC0415

        self.recording_dir = Path(recording_dir)
        self.depth_range = (float(depth_range[0]), float(depth_range[1]))
        self._reader = RecordingReader(self.recording_dir)
        self._rect = Rectifier(calib.stereo, 1.0)
        w, h = self._rect.image_size
        dp = dataclasses.replace(default_depth_params(effective_scale(w)), min_depth_mm=self.depth_range[0],
                                 max_depth_mm=self.depth_range[1])
        self._est = DepthEstimator(self._rect.K, self._rect.baseline, dp)
        self._pos = {int(ix): p for p, ix in enumerate(self._reader.indices)}
        self.K = np.asarray(self._rect.K, dtype=np.float64)
        self.image_size = (int(w), int(h))
        self._images: OrderedDict[int, tuple[np.ndarray, np.ndarray]] = OrderedDict()
        self._sigma: OrderedDict[int, tuple[np.ndarray, np.ndarray]] = OrderedDict()

    @property
    def backend(self) -> str:
        """Where the matcher runs ("gpu" or the processor's)."""
        return str(self._est.backend)

    def has(self, index: int) -> bool:
        return int(index) in self._pos

    def depth(self, index: int) -> np.ndarray:
        """The depth (mm, float32, 0 = none) of the frame with the recording's frame ``index``."""
        fr = self._reader.frame(self._pos[int(index)])
        left, right = self._rect.rectify(fr.left, fr.right)
        return self._est.compute(left, right)

    def points(self, index: int, T_wc: np.ndarray, near: tuple[np.ndarray, float] | None = None,
               step: int = 2) -> tuple[np.ndarray, np.ndarray]:
        """The frame's depth as world points and normals at its pose ``T_wc`` (:func:`frame_points`)."""
        return frame_points(self.depth(index), self.K, T_wc, near=near, step=step)

    # ------------------------------------------------------------------ for the hole's rim (ENGINE_PLAN X15)
    def _kept(self, store: "OrderedDict[int, Any]", index: int, make) -> Any:
        """``make()`` for ``index``, kept for the next click (the last :data:`KEEP_FRAMES`; the frames of one hole
        are read again by each click on it)."""
        index = int(index)
        if index in store:
            store.move_to_end(index)
            return store[index]
        out = store[index] = make()
        while len(store) > KEEP_FRAMES:
            store.popitem(last=False)
        return out

    def images(self, index: int) -> tuple[np.ndarray, np.ndarray]:
        """The frame's raw left and right images, grey, as recorded (not rectified; the recording's left and right,
        its ``swap_lr`` applied): what :func:`ohs.measure.rim.read_hole` reads the rim on."""
        def make():
            fr = self._reader.frame(self._pos[int(index)])
            return np.ascontiguousarray(fr.left), np.ascontiguousarray(fr.right)

        return self._kept(self._images, index, make)

    def depth_and_sigma(self, index: int) -> tuple[np.ndarray, np.ndarray]:
        """The frame's depth (mm, float32, 0 = none; the same map as :meth:`depth`) and the matcher's sigma of it (mm,
        0 where there is no depth), on the rectified left camera
        (:meth:`ohs.vision.depth.DepthEstimator.compute_with_sigma`)."""
        def make():
            left, right = self._rect.rectify(*self.images(index))
            d, s = self._est.compute_with_sigma(left, right)
            return np.asarray(d, dtype=np.float32), np.asarray(s, dtype=np.float32)

        return self._kept(self._sigma, index, make)
