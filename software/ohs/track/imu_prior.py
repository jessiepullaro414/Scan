"""Gyro rotation prior for frame-to-frame tracking.

:class:`GyroPredictor` buffers IMU gyro samples, maps them into the camera frame and onto the
camera clock, and integrates them between two camera timestamps.

Conventions (ARCHITECTURE.md section 2):

* camera timestamp = IMU host timestamp + ``ImuCalibration.time_offset``;
* ``v_c = R_ci @ v_i``; the gyro bias (IMU frame, rad/s) is subtracted *before* rotating, so the
  buffered angular velocity is ``w_c = R_ci @ (w_i - bias)``;
* :meth:`GyroPredictor.relative_rotation` returns ``R_c0_c1`` with ``v_c0 = R_c0_c1 @ v_c1``: the
  orientation of the camera at ``t1`` relative to the camera at ``t0``. It equals the rotation
  block of ``inv(T_wc(t0)) @ T_wc(t1)``, which is exactly the ``R_prior`` that
  :meth:`ohs.track.markerless.MarkerlessTracker.track` expects (``t0`` = previous frame time).

The buffer is bounded both in duration (``max_duration_s``) and in sample count
(``max_samples``); old samples are dropped first. Samples may arrive in any order: the buffer
is kept sorted and duplicate timestamps are removed.

Between samples the angular velocity is interpolated linearly (trapezoidal integration, see
:func:`ohs.geometry.integrate_gyro`). Outside the buffered range it is held constant, so
callers should check :meth:`GyroPredictor.covers` first.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable

import numpy as np

from ohs.geometry import integrate_gyro
from ohs.types import ImuCalibration, ImuSample

__all__ = ["GyroPredictor"]

log = logging.getLogger(__name__)


class GyroPredictor:
    """Integrates bias-corrected, camera-frame gyro samples between camera timestamps.

    Parameters
    ----------
    imu_calib:
        IMU-to-camera calibration. ``None`` means identity rotation, zero offset and zero bias.
    max_duration_s:
        Samples older than ``newest - max_duration_s`` (camera time) are discarded.
    max_samples:
        Hard cap on the number of buffered samples (the newest are kept).
    max_gap_s:
        :meth:`covers` returns ``False`` if consecutive samples inside the requested interval are
        further apart than this (dropped IMU packets).
    """

    def __init__(
        self,
        imu_calib: ImuCalibration | None = None,
        max_duration_s: float = 10.0,
        max_samples: int = 20000,
        max_gap_s: float = 0.05,
    ) -> None:
        cal = imu_calib if imu_calib is not None else ImuCalibration()
        self.R_ci = np.asarray(cal.R_ci, dtype=np.float64).reshape(3, 3)
        self.time_offset = float(cal.time_offset)
        self.gyro_bias = np.asarray(cal.gyro_bias, dtype=np.float64).reshape(3)
        self.max_duration_s = float(max_duration_s)
        self.max_samples = int(max_samples)
        self.max_gap_s = float(max_gap_s)
        self._t = np.zeros(0, dtype=np.float64)  # camera time (s), sorted ascending
        self._w = np.zeros((0, 3), dtype=np.float64)  # camera-frame angular velocity (rad/s)

    # ------------------------------------------------------------------ buffer
    def __len__(self) -> int:
        return int(self._t.size)

    @property
    def time_range(self) -> tuple[float, float] | None:
        """``(first, last)`` buffered camera timestamps, or ``None`` if the buffer is empty."""
        if self._t.size == 0:
            return None
        return float(self._t[0]), float(self._t[-1])

    def reset(self) -> None:
        """Drop all buffered samples (calibration is kept)."""
        self._t = np.zeros(0, dtype=np.float64)
        self._w = np.zeros((0, 3), dtype=np.float64)

    def add(self, samples: Iterable[ImuSample]) -> None:
        """Append IMU samples (``t_host`` in seconds, ``gyro`` in rad/s, IMU frame)."""
        samples = list(samples)
        if not samples:
            return
        t = np.fromiter((s.t_host for s in samples), dtype=np.float64, count=len(samples))
        g = np.asarray([np.asarray(s.gyro, dtype=np.float64).reshape(3) for s in samples])
        self.add_arrays(t, g)

    def add_arrays(self, t_host: np.ndarray, gyro: np.ndarray) -> None:
        """Append samples given as arrays: ``t_host`` (N,) seconds, ``gyro`` (N,3) rad/s, IMU frame.

        Convenient with :meth:`ohs.io.recording.RecordingReader.imu_arrays`.
        """
        t = np.asarray(t_host, dtype=np.float64).reshape(-1) + self.time_offset
        g = np.asarray(gyro, dtype=np.float64).reshape(-1, 3)
        if t.size != g.shape[0]:
            raise ValueError(f"t_host has {t.size} entries but gyro has {g.shape[0]} rows")
        ok = np.isfinite(t) & np.all(np.isfinite(g), axis=1)
        if not ok.all():
            log.debug("dropping %d non-finite IMU samples", int((~ok).sum()))
            t, g = t[ok], g[ok]
        if t.size == 0:
            return
        w = (g - self.gyro_bias) @ self.R_ci.T
        in_order = bool(np.all(np.diff(t) > 0)) and (self._t.size == 0 or t[0] > self._t[-1])
        t_all = np.concatenate([self._t, t])
        w_all = np.concatenate([self._w, w])
        if not in_order:
            order = np.argsort(t_all, kind="stable")
            t_all, w_all = t_all[order], w_all[order]
            keep = np.concatenate([[True], np.diff(t_all) > 0])  # drop duplicate timestamps
            t_all, w_all = t_all[keep], w_all[keep]
        # bound the buffer
        start = int(np.searchsorted(t_all, t_all[-1] - self.max_duration_s, side="left"))
        start = max(start, t_all.size - self.max_samples)
        self._t, self._w = t_all[start:], w_all[start:]

    # ------------------------------------------------------------------ queries
    def covers(self, t0: float, t1: float, tol_s: float = 0.003) -> bool:
        """True if the buffer spans ``[min(t0,t1), max(t0,t1)]`` (camera time) without gaps.

        ``tol_s`` allows the interval to stick out of the buffered range by about one sample
        period (the angular velocity is held constant there).
        """
        if self._t.size < 2:
            return False
        lo, hi = min(t0, t1), max(t0, t1)
        if self._t[0] > lo + tol_s or self._t[-1] < hi - tol_s:
            return False
        i0 = max(int(np.searchsorted(self._t, lo, side="right")) - 1, 0)
        i1 = min(int(np.searchsorted(self._t, hi, side="left")), self._t.size - 1)
        if i1 > i0 and float(np.max(np.diff(self._t[i0 : i1 + 1]))) > self.max_gap_s:
            return False
        return True

    def relative_rotation(self, t0: float, t1: float) -> np.ndarray:
        """``R_c0_c1`` (3x3, ``v_c0 = R @ v_c1``) between camera timestamps ``t0`` and ``t1``.

        Raises ``ValueError`` if no samples are buffered. Outside the buffered range the angular
        velocity is held constant (a debug message is logged); use :meth:`covers` to check.
        """
        if self._t.size == 0:
            raise ValueError("GyroPredictor has no samples")
        if not self.covers(t0, t1):
            log.debug("gyro buffer [%.4f, %.4f] does not fully cover [%.4f, %.4f]",
                      self._t[0], self._t[-1], t0, t1)
        lo, hi = min(t0, t1), max(t0, t1)
        # only the samples bracketing the interval matter; slicing keeps the call cheap
        i0 = max(int(np.searchsorted(self._t, lo, side="right")) - 1, 0)
        i1 = min(int(np.searchsorted(self._t, hi, side="left")) + 1, self._t.size)
        return integrate_gyro(self._t[i0:i1], self._w[i0:i1], float(t0), float(t1))

    def angular_velocity(self, t: float) -> np.ndarray:
        """Interpolated camera-frame angular velocity (rad/s) at camera time ``t``."""
        if self._t.size == 0:
            raise ValueError("GyroPredictor has no samples")
        return np.array([np.interp(t, self._t, self._w[:, k]) for k in range(3)])
