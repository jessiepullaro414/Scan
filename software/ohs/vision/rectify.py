"""Stereo rectification: raw (distorted, unrectified) camera images -> rectified images in frame C.

The undistort+rectify maps go *directly* from the full-resolution raw intrinsics
(``K1, D1, R1`` / ``K2, D2, R2``) to the rectified projection at the requested scale
(``calib.scaled(scale).P1`` / ``.P2``) and the scaled output size. Down-sampled rectification
(e.g. ``scale=0.5`` for the live preview) is therefore a single ``cv2.remap`` with no separate
resize. With the pixel-centre convention of :meth:`StereoCalibration.scaled` and ``scale=0.5``,
each output pixel samples the raw image at the centre of a 2x2 block, so bilinear interpolation
acts as a 2x2 box filter (little aliasing) when distortion is moderate.

Maps are ``CV_16SC2`` (fixed point, 1/32 px interpolation table) and the images are interpolated
with ``INTER_LINEAR`` for speed. Pixels that map outside the raw image are set to 0.

Everything downstream (depth, laser, markers) works on the rectified images with the ideal
pinhole ``Rectifier.K`` (zero distortion) and ``Rectifier.baseline`` (ARCHITECTURE.md section 2).
"""
from __future__ import annotations

import logging

import cv2
import numpy as np

from ohs.types import StereoCalibration

__all__ = ["Rectifier"]

log = logging.getLogger(__name__)


class Rectifier:
    """Precomputed undistort+rectify maps for one stereo calibration at one output scale.

    Parameters
    ----------
    calib:
        Full-resolution stereo calibration (raw intrinsics + rectification). Raw input images
        must have ``calib.image_size``.
    scale:
        Output scale relative to the raw resolution (1.0 = full, 0.5 = half resolution).
    interpolation:
        OpenCV interpolation flag for :func:`cv2.remap` (default ``INTER_LINEAR``).
    """

    def __init__(self, calib: StereoCalibration, scale: float = 1.0, interpolation: int = cv2.INTER_LINEAR) -> None:
        scale = float(scale)
        if not np.isfinite(scale) or scale <= 0.0:
            raise ValueError(f"scale must be a positive number, got {scale!r}")
        self._raw = calib
        self._scale = scale
        self._calib = calib if scale == 1.0 else calib.scaled(scale)
        self._interp = int(interpolation)
        size = (int(self._calib.image_size[0]), int(self._calib.image_size[1]))
        if size[0] < 1 or size[1] < 1:
            raise ValueError(f"scale {scale} gives an empty output image {size}")
        # Source: full-resolution raw camera (K, D, R). Destination: rectified camera at this scale.
        self._map_l = cv2.initUndistortRectifyMap(
            calib.K1, calib.D1, calib.R1, self._calib.P1[:3, :3], size, cv2.CV_16SC2
        )
        self._map_r = cv2.initUndistortRectifyMap(
            calib.K2, calib.D2, calib.R2, self._calib.P2[:3, :3], size, cv2.CV_16SC2
        )
        log.debug(
            "Rectifier: raw %s -> rectified %s (scale %.3f), f=%.2f px, baseline=%.3f mm",
            tuple(calib.image_size), size, scale, self._calib.P1[0, 0], self._calib.baseline,
        )

    # ------------------------------------------------------------------ geometry at this scale
    @property
    def calib(self) -> StereoCalibration:
        """Stereo geometry at this scale (``image_size``, ``P1``, ``P2``, ``Q``, ``K_rect`` scaled)."""
        return self._calib

    @property
    def raw_calib(self) -> StereoCalibration:
        """The full-resolution calibration the maps were built from."""
        return self._raw

    @property
    def scale(self) -> float:
        return self._scale

    @property
    def K(self) -> np.ndarray:
        """Ideal pinhole intrinsics of the rectified images (frame C) at this scale."""
        return self._calib.K_rect

    @property
    def baseline(self) -> float:
        """Rectified stereo baseline in mm (independent of scale)."""
        return self._calib.baseline

    @property
    def image_size(self) -> tuple[int, int]:
        """(width, height) of the rectified images."""
        return int(self._calib.image_size[0]), int(self._calib.image_size[1])

    @property
    def raw_image_size(self) -> tuple[int, int]:
        """(width, height) the raw input images must have."""
        return int(self._raw.image_size[0]), int(self._raw.image_size[1])

    # ------------------------------------------------------------------ rectification
    def _check(self, img: np.ndarray, which: str) -> None:
        w, h = self.raw_image_size
        if img is None or img.ndim < 2 or img.shape[0] != h or img.shape[1] != w:
            shape = None if img is None else img.shape
            raise ValueError(f"{which} image has shape {shape}, expected raw size (h={h}, w={w})")

    def _remap(self, img: np.ndarray, maps: tuple[np.ndarray, np.ndarray]) -> np.ndarray:
        return cv2.remap(img, maps[0], maps[1], self._interp, borderMode=cv2.BORDER_CONSTANT, borderValue=0)

    def rectify(self, left: np.ndarray, right: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Rectify a raw stereo pair. Returns ``(left_r, right_r)`` at this scale (same dtype)."""
        return self.rectify_left(left), self.rectify_right(right)

    def rectify_left(self, left: np.ndarray) -> np.ndarray:
        """Rectify only the raw left image (e.g. for intensity/markers)."""
        self._check(left, "left")
        return self._remap(left, self._map_l)

    def rectify_right(self, right: np.ndarray) -> np.ndarray:
        """Rectify only the raw right image."""
        self._check(right, "right")
        return self._remap(right, self._map_r)
