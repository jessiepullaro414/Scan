"""Smooth, hand-held-like scanner trajectories with a continuous-time API.

A trajectory is any object with ``pose(t) -> T_wc`` (4x4, frame C -> sim world, mm) and
``poses(t_array) -> (N, 4, 4)``; both accept arbitrary real ``t`` so the IMU synthesiser can
differentiate them. **Time base:** ``t`` is the *camera timestamp* domain, i.e. the ``t_host``
that a frame exposed at that instant would carry. An IMU sample with host timestamp ``s``
observes the motion at camera time ``s + time_offset`` (ARCHITECTURE.md: camera timestamp = IMU
host timestamp + time_offset); see :mod:`ohs.sim.imu`.

:class:`HandheldTrajectory` orbits a target in the Z-up sim world: azimuth sweeps at a steady
rate, elevation / distance / roll wander slowly (sums of random low-frequency sinusoids), the
aim point drifts and pans, and a small 6-11 Hz physiological tremor is added to position and
orientation. The *scanner centre* (midpoint of the stereo pair, where the laser cross and the dot
field are centred) looks at the target; the left camera (frame C) sits ``baseline / 2`` to its
left. Distance stays inside ``distance`` (default 220-300 mm, always within 150-400 mm).

:class:`RotatingTrajectory` is the IMU-calibration exercise in front of a board: hold still, then
rotate about all three camera axes (optionally about a pivot in front of the camera, and for a
limited time with a smooth return to the start pose).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Protocol, Sequence

import numpy as np

__all__ = [
    "Trajectory",
    "look_at",
    "look_at_batch",
    "StaticTrajectory",
    "HandheldTrajectory",
    "RotatingTrajectory",
    "sample_frames",
    "so3_exp_batch",
]

log = logging.getLogger(__name__)

_UP = np.array([0.0, 0.0, 1.0])


class Trajectory(Protocol):
    def pose(self, t: float) -> np.ndarray: ...

    def poses(self, t: np.ndarray) -> np.ndarray: ...


def so3_exp_batch(w: np.ndarray) -> np.ndarray:
    """Rodrigues for (N,3) rotation vectors -> (N,3,3), stable for small angles."""
    w = np.asarray(w, dtype=np.float64).reshape(-1, 3)
    th = np.linalg.norm(w, axis=1)
    small = th < 1e-8
    ths = np.where(small, 1.0, th)
    a = np.where(small, 1.0 - th**2 / 6.0, np.sin(ths) / ths)
    b = np.where(small, 0.5 - th**2 / 24.0, (1.0 - np.cos(ths)) / ths**2)
    W = np.zeros((len(w), 3, 3))
    W[:, 0, 1], W[:, 0, 2], W[:, 1, 2] = -w[:, 2], w[:, 1], -w[:, 0]
    W[:, 1, 0], W[:, 2, 0], W[:, 2, 1] = w[:, 2], -w[:, 1], w[:, 0]
    return np.eye(3)[None] + a[:, None, None] * W + b[:, None, None] * (W @ W)


def look_at_batch(eye: np.ndarray, target: np.ndarray, roll: np.ndarray | float = 0.0, up: Sequence[float] = (0.0, 0.0, 1.0)) -> np.ndarray:
    """Camera rotations ``R_wc`` (N,3,3): optical axis toward ``target``, image-up toward ``up``,
    then rotated by ``roll`` (rad) about the optical axis."""
    eye = np.atleast_2d(np.asarray(eye, dtype=np.float64))
    target = np.atleast_2d(np.asarray(target, dtype=np.float64))
    upv = np.asarray(up, dtype=np.float64)
    z = target - eye
    z /= np.linalg.norm(z, axis=1, keepdims=True)
    y = -(upv[None, :] - (z @ upv)[:, None] * z)
    ny = np.linalg.norm(y, axis=1, keepdims=True)
    if np.any(ny < 1e-9):
        raise ValueError("look_at: viewing direction parallel to the up vector")
    y /= ny
    x = np.cross(y, z)
    roll = np.broadcast_to(np.asarray(roll, dtype=np.float64), (len(z),))
    c, s = np.cos(roll)[:, None], np.sin(roll)[:, None]
    x2, y2 = c * x + s * y, -s * x + c * y
    return np.stack([x2, y2, z], axis=2)


def look_at(eye: Sequence[float], target: Sequence[float], up: Sequence[float] = (0.0, 0.0, 1.0), roll: float = 0.0) -> np.ndarray:
    """``T_wc`` of a camera at ``eye`` looking at ``target`` (image-up toward ``up``)."""
    T = np.eye(4)
    T[:3, :3] = look_at_batch(np.asarray(eye, dtype=np.float64), np.asarray(target, dtype=np.float64), roll, up)[0]
    T[:3, 3] = np.asarray(eye, dtype=np.float64)
    return T


@dataclass
class StaticTrajectory:
    """The scanner held perfectly still at ``T_wc``."""

    T_wc: np.ndarray

    def pose(self, t: float) -> np.ndarray:
        return np.asarray(self.T_wc, dtype=np.float64).copy()

    def poses(self, t: np.ndarray) -> np.ndarray:
        t = np.atleast_1d(np.asarray(t, dtype=np.float64))
        return np.repeat(np.asarray(self.T_wc, dtype=np.float64)[None], len(t), axis=0)


class _Wander:
    """Sum of random sinusoids, values in [-1, 1] (deterministic from ``rng``)."""

    def __init__(self, rng: np.random.Generator, dims: int, n: int, f_lo: float, f_hi: float) -> None:
        self.f = rng.uniform(f_lo, f_hi, (n, dims))
        self.ph = rng.uniform(0.0, 2 * math.pi, (n, dims))
        a = rng.uniform(0.5, 1.0, (n, dims))
        self.a = a / a.sum(axis=0, keepdims=True)

    def __call__(self, t: np.ndarray) -> np.ndarray:
        arg = 2 * math.pi * t[:, None, None] * self.f[None] + self.ph[None]
        return (self.a[None] * np.sin(arg)).sum(axis=1)


@dataclass
class HandheldTrajectory:
    """Hand-held scan around ``target`` (world, mm). ``t0`` is the start (camera time),
    ``duration`` the nominal scan length; the pose is defined (smoothly) for all ``t``."""

    target: np.ndarray
    t0: float = 0.0
    duration: float = 2.0
    seed: int = 0
    distance: tuple[float, float] = (220.0, 300.0)
    elevation_deg: tuple[float, float] = (40.0, 70.0)
    azimuth0_deg: float | None = None  # None: random
    sweep_deg_s: float = 20.0  # mean azimuth rate
    pan_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)  # aim-point travel over `duration`
    roll_deg: float = 10.0
    aim_jitter_mm: float = 8.0
    tremor_mm: float = 0.08
    tremor_deg: float = 0.03
    baseline: float = 60.0
    _rng: np.random.Generator = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.target = np.asarray(self.target, dtype=np.float64).reshape(3)
        lo, hi = float(self.distance[0]), float(self.distance[1])
        if not (150.0 <= lo <= hi <= 400.0):
            log.warning("trajectory distance range %s outside the 150-400 mm working range", self.distance)
        if max(self.elevation_deg) >= 85.0:
            raise ValueError("elevation must stay below 85 deg (look_at singularity)")
        rng = np.random.default_rng(self.seed)
        self._az0 = math.radians(self.azimuth0_deg) if self.azimuth0_deg is not None else rng.uniform(-math.pi, math.pi)
        self._sweep_dir = 1.0 if rng.random() < 0.5 else -1.0
        self._w_az = _Wander(rng, 1, 3, 0.05, 0.35)
        self._w_el = _Wander(rng, 1, 3, 0.05, 0.35)
        self._w_r = _Wander(rng, 1, 3, 0.08, 0.5)
        self._w_roll = _Wander(rng, 1, 3, 0.05, 0.3)
        self._w_aim = _Wander(rng, 3, 3, 0.1, 0.6)
        self._w_tp = _Wander(rng, 3, 4, 6.0, 11.0)
        self._w_tr = _Wander(rng, 3, 4, 6.0, 11.0)
        self._rng = rng

    def poses(self, t: np.ndarray) -> np.ndarray:
        t = np.atleast_1d(np.asarray(t, dtype=np.float64))
        s = t - self.t0
        u = s / max(self.duration, 1e-9) - 0.5  # -0.5 .. 0.5 over the scan
        el_lo, el_hi = (math.radians(v) for v in self.elevation_deg)
        az = self._az0 + self._sweep_dir * math.radians(self.sweep_deg_s) * (s - 0.5 * self.duration) + math.radians(8.0) * self._w_az(s)[:, 0]
        el = 0.5 * (el_lo + el_hi) + 0.5 * (el_hi - el_lo) * self._w_el(s)[:, 0]
        r = 0.5 * (self.distance[0] + self.distance[1]) + 0.5 * (self.distance[1] - self.distance[0]) * self._w_r(s)[:, 0]
        aim = self.target[None] + u[:, None] * np.asarray(self.pan_mm, dtype=np.float64)[None] + self.aim_jitter_mm * self._w_aim(s)
        offs = np.stack([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)], axis=1)
        centre = aim + r[:, None] * offs + self.tremor_mm * self._w_tp(s)
        roll = math.radians(self.roll_deg) * self._w_roll(s)[:, 0]
        R = look_at_batch(centre, aim, roll)
        R = R @ so3_exp_batch(math.radians(self.tremor_deg) * self._w_tr(s))
        T = np.zeros((len(t), 4, 4))
        T[:, :3, :3] = R
        # frame C (left camera) is baseline/2 to the left (-x) of the scanner centre
        T[:, :3, 3] = centre - 0.5 * self.baseline * R[:, :, 0]
        T[:, 3, 3] = 1.0
        return T

    def pose(self, t: float) -> np.ndarray:
        return self.poses(np.array([float(t)]))[0]


def _min_jerk(x: np.ndarray) -> np.ndarray:
    """Minimum-jerk ramp from 0 to 1 over ``x`` in [0, 1] (clipped outside)."""
    x = np.clip(x, 0.0, 1.0)
    return 10 * x**3 - 15 * x**4 + 6 * x**5


class RotatingTrajectory:
    """Hand-held IMU-calibration motion in front of the board: still for ``t_still`` s, then smooth
    rotations about all three camera axes (sums of sinusoids, +-amp deg) with a few mm of sway.

    ``T0`` is the still pose ``T_wc`` and ``t0`` the start (camera time). Per camera axis (x, y, z
    of ``T0``) the rotation vector is a sum of three sinusoids (0.4-1.1 Hz) whose amplitudes add up
    to ``amp_deg``; it fades in over 0.8 s (minimum jerk) after ``t_still``. Two optional extras
    (their defaults give the plain motion above):

    * ``pivot`` (mm, frame C of ``T0``): rotate about that point instead of the camera centre. A
      point near the board makes the camera orbit it, so the board stays in view at larger angles.
    * ``rotate_s``: stop rotating that many seconds after ``t_still``. The motion fades out over
      ``ease_s`` (minimum jerk, so the rate and the acceleration stay continuous) and ends exactly at
      ``T0``. ``None`` rotates forever.
    """

    def __init__(self, T0: np.ndarray, t0: float, t_still: float = 1.2, amp_deg=(7.0, 8.0, 15.0), seed: int = 0, *,
                 pivot: Sequence[float] = (0.0, 0.0, 0.0), rotate_s: float | None = None, ease_s: float = 1.5):
        rng = np.random.default_rng(seed)
        self.T0, self.t0, self.t_still = np.asarray(T0, float), float(t0), float(t_still)
        self.f = rng.uniform(0.4, 1.1, (3, 3))
        self.ph = rng.uniform(0, 2 * np.pi, (3, 3))
        a = rng.uniform(0.5, 1.0, (3, 3))
        self.a = a / a.sum(axis=0, keepdims=True) * np.radians(amp_deg)[None]
        self.sway = rng.uniform(-6.0, 6.0, (3, 3))
        self.pivot = np.asarray(pivot, dtype=np.float64).reshape(3)
        self.rotate_s = None if rotate_s is None else float(rotate_s)
        self.ease_s = float(ease_s)

    def poses(self, t: np.ndarray) -> np.ndarray:
        t = np.atleast_1d(np.asarray(t, float))
        s = np.clip(t - self.t0 - self.t_still, 0.0, None)
        env = _min_jerk(s / 0.8)
        if self.rotate_s is not None:
            env = env * (1.0 - _min_jerk((s - self.rotate_s) / max(self.ease_s, 1e-9)))
        env = env[:, None]
        arg = 2 * np.pi * self.f[None] * s[:, None, None] + self.ph[None]
        th = env * (self.a[None] * (np.sin(arg) - np.sin(self.ph)[None])).sum(axis=1)
        tr = env * (self.sway[None] / 3.0 * (np.sin(arg) - np.sin(self.ph)[None])).sum(axis=1)
        R = so3_exp_batch(th)
        rel = np.zeros((len(t), 4, 4))
        rel[:, :3, :3] = R
        rel[:, :3, 3] = tr + self.pivot - R @ self.pivot  # rotation about the pivot
        rel[:, 3, 3] = 1.0
        return self.T0[None] @ rel

    def pose(self, t: float) -> np.ndarray:
        return self.poses(np.array([t]))[0]


def sample_frames(traj: Trajectory, n_frames: int, fps: float, t0: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Frame times ``t0 + i / fps`` (camera time) and poses (N,4,4)."""
    times = float(t0) + np.arange(int(n_frames)) / float(fps)
    return times, traj.poses(times)
