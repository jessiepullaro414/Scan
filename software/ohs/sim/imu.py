"""IMU synthesis (LSM6DSOX-like, 416 Hz) from a continuous camera trajectory.

Conventions (ARCHITECTURE.md section 2):

* ``v_c = R_ci @ v_i``: gyro / accel are reported in the IMU frame ``i``.
* **camera timestamp = IMU host timestamp + time_offset**. Trajectories are parameterised in the
  camera-timestamp domain (:mod:`ohs.sim.trajectory`), so a sample whose (ideal) host timestamp
  is ``s`` measures the motion at trajectory time ``s + time_offset``.
* Gyro: body rate of the scanner, ``R_ci.T @ omega_c + bias(t) + noise`` (rad/s); ``bias`` starts
  at ``params.gyro_bias`` (the value stored in the true ``ImuCalibration``) and random-walks.
* Accel: specific force ``R_ci.T @ R_wc.T @ (a_w - g_w) + accel_bias + noise`` (m/s^2) at the
  IMU's position (lever arm ``params.lever_arm_mm`` in frame C, ~31 mm behind the lenses), with
  ``g_w = (0, 0, -9.81)`` (Z-up sim world). At rest it reads +9.81 m/s^2 "up". Trajectory
  positions are in mm, so accelerations are divided by 1000.
* Clocks: samples are evenly spaced at ``rate_hz`` in the **device** clock (Pico ``ticks_us``,
  1 us resolution) which runs ``clock_drift_ppm`` fast with an offset:
  ``t_device = device_offset_s + (1 + drift) * t_true``. The recorded ``t_host`` is the true host
  time of the sample plus a small clock-sync residual ``host_jitter_s`` (Gaussian).
* Optional int16 quantisation / saturation for +-1000 dps and +-8 g full scale.

Derivatives are central finite differences of the trajectory: angular rate from
``log(R(t-h)^T R(t+h)) / 2h`` (h = 0.1 ms) and acceleration from the second difference of the IMU
position (h = 2 ms), both accurate to well below the sensor noise for hand-held motion.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, replace
from typing import Sequence

import numpy as np

from ohs.sim.nominal import nominal_imu_position
from ohs.sim.scene import GRAVITY_W
from ohs.sim.trajectory import Trajectory
from ohs.types import ImuSample

__all__ = [
    "ImuSimParams",
    "body_rates",
    "point_acceleration",
    "so3_log_batch",
    "synthesize_imu_arrays",
    "synthesize_imu",
]

log = logging.getLogger(__name__)

_G0 = 9.80665


def _vec(x: Sequence[float] | np.ndarray) -> np.ndarray:
    return np.asarray(x, dtype=np.float64).reshape(3)


@dataclass
class ImuSimParams:
    """IMU error model and clock model. ``ideal()`` gives a noise-free, bias-free sensor."""

    rate_hz: float = 416.0
    gyro_bias: np.ndarray = field(default_factory=lambda: np.array([0.0042, -0.0031, 0.0017]))  # rad/s
    accel_bias: np.ndarray = field(default_factory=lambda: np.array([0.06, -0.04, 0.09]))  # m/s^2
    gyro_noise: float = 1.0e-3  # rad/s per sample (~3.8 mdps/sqrt(Hz) at 208 Hz bandwidth)
    accel_noise: float = 0.012  # m/s^2 per sample (~80 ug/sqrt(Hz))
    gyro_bias_walk: float = 2.0e-5  # rad/s/sqrt(s)
    lever_arm_mm: np.ndarray = field(default_factory=lambda: nominal_imu_position(60.0))  # IMU position in C
    gravity: np.ndarray = field(default_factory=lambda: GRAVITY_W.copy())
    device_offset_s: float = 12.5  # t_device - t_host at t_host = 0
    clock_drift_ppm: float = 20.0
    host_jitter_s: float = 2.0e-5  # clock-sync residual on t_host
    quantize: bool = True
    gyro_lsb: float = math.radians(0.035)  # +-1000 dps: 35 mdps/LSB
    accel_lsb: float = 0.244e-3 * _G0  # +-8 g: 0.244 mg/LSB
    gyro_range: float = math.radians(1000.0)
    accel_range: float = 8.0 * _G0

    @classmethod
    def ideal(cls, **kw: object) -> "ImuSimParams":
        """No noise, bias, quantisation or host jitter (clock offset/drift kept)."""
        base = cls(
            gyro_bias=np.zeros(3),
            accel_bias=np.zeros(3),
            gyro_noise=0.0,
            accel_noise=0.0,
            gyro_bias_walk=0.0,
            host_jitter_s=0.0,
            quantize=False,
        )
        return replace(base, **kw)


def so3_log_batch(R: np.ndarray) -> np.ndarray:
    """Rotation vectors (N,3) of rotation matrices (N,3,3) with angles well below pi."""
    R = np.asarray(R, dtype=np.float64).reshape(-1, 3, 3)
    v = np.stack([R[:, 2, 1] - R[:, 1, 2], R[:, 0, 2] - R[:, 2, 0], R[:, 1, 0] - R[:, 0, 1]], axis=1)
    c = np.clip((np.trace(R, axis1=1, axis2=2) - 1.0) / 2.0, -1.0, 1.0)
    s = 0.5 * np.linalg.norm(v, axis=1)
    th = np.arctan2(s, c)
    k = np.where(s > 1e-12, th / np.maximum(s, 1e-300), 1.0 + th**2 / 6.0)
    return 0.5 * k[:, None] * v


def body_rates(traj: Trajectory, t: np.ndarray, h: float = 1e-4) -> np.ndarray:
    """Angular velocity of frame C expressed in C (rad/s) at trajectory times ``t`` (N,)."""
    t = np.atleast_1d(np.asarray(t, dtype=np.float64))
    Ra = traj.poses(t - h)[:, :3, :3]
    Rb = traj.poses(t + h)[:, :3, :3]
    return so3_log_batch(np.transpose(Ra, (0, 2, 1)) @ Rb) / (2.0 * h)


def point_acceleration(traj: Trajectory, t: np.ndarray, point_c: Sequence[float] = (0.0, 0.0, 0.0), h: float = 2e-3) -> np.ndarray:
    """World-frame acceleration (m/s^2) of a point fixed in frame C (mm) at times ``t``."""
    t = np.atleast_1d(np.asarray(t, dtype=np.float64))
    pc = _vec(point_c)

    def pos(tt: np.ndarray) -> np.ndarray:
        T = traj.poses(tt)
        return T[:, :3, :3] @ pc + T[:, :3, 3]

    return (pos(t + h) - 2.0 * pos(t) + pos(t - h)) / (h * h) / 1000.0


def synthesize_imu_arrays(
    traj: Trajectory,
    t_start: float,
    t_end: float,
    R_ci: np.ndarray,
    time_offset: float,
    params: ImuSimParams | None = None,
    seed: int = 0,
) -> dict[str, np.ndarray]:
    """IMU samples with host time in ``[t_start, t_end]`` as arrays.

    Returns ``{"t_host": (N,), "t_device": (N,), "gyro": (N,3), "accel": (N,3)}`` (IMU frame).
    """
    p = params or ImuSimParams()
    rng = np.random.default_rng(seed)
    R_ci = np.asarray(R_ci, dtype=np.float64).reshape(3, 3)
    drift = 1.0 + p.clock_drift_ppm * 1e-6
    dev0 = p.device_offset_s + drift * float(t_start)
    dev1 = p.device_offset_s + drift * float(t_end)
    k0 = math.ceil(dev0 * p.rate_hz - 1e-9)
    k1 = math.floor(dev1 * p.rate_hz + 1e-9)
    t_dev = np.round(np.arange(k0, k1 + 1) / p.rate_hz, 6)  # ticks_us resolution
    t_true = (t_dev - p.device_offset_s) / drift
    n = len(t_true)
    t_traj = t_true + float(time_offset)
    w_c = body_rates(traj, t_traj)
    a_w = point_acceleration(traj, t_traj, p.lever_arm_mm)
    R_wc = traj.poses(t_traj)[:, :3, :3]
    f_w = a_w - _vec(p.gravity)[None]
    f_c = np.einsum("nji,nj->ni", R_wc, f_w)  # R_wc^T f_w
    gyro = w_c @ R_ci  # rows: R_ci^T w_c
    accel = f_c @ R_ci
    bias = np.broadcast_to(_vec(p.gyro_bias), (n, 3)).copy()
    if p.gyro_bias_walk > 0 and n > 1:
        dt = np.diff(t_true, prepend=t_true[0])
        bias += np.cumsum(rng.standard_normal((n, 3)) * p.gyro_bias_walk * np.sqrt(dt)[:, None], axis=0)
    gyro = gyro + bias + p.gyro_noise * rng.standard_normal((n, 3))
    accel = accel + _vec(p.accel_bias)[None] + p.accel_noise * rng.standard_normal((n, 3))
    if p.quantize:
        gyro = np.clip(np.round(gyro / p.gyro_lsb), -32768, 32767) * p.gyro_lsb
        accel = np.clip(np.round(accel / p.accel_lsb), -32768, 32767) * p.accel_lsb
        gyro = np.clip(gyro, -p.gyro_range, p.gyro_range)
        accel = np.clip(accel, -p.accel_range, p.accel_range)
    t_host = t_true + p.host_jitter_s * rng.standard_normal(n)
    log.debug("synthesised %d IMU samples over %.3f s", n, t_end - t_start)
    return {"t_host": t_host, "t_device": t_dev, "gyro": gyro, "accel": accel}


def synthesize_imu(
    traj: Trajectory,
    t_start: float,
    t_end: float,
    R_ci: np.ndarray,
    time_offset: float,
    params: ImuSimParams | None = None,
    seed: int = 0,
) -> list[ImuSample]:
    """IMU samples (host time in ``[t_start, t_end]``) as :class:`~ohs.types.ImuSample` objects."""
    a = synthesize_imu_arrays(traj, t_start, t_end, R_ci, time_offset, params, seed)
    return [
        ImuSample(float(t), float(td), g.copy(), ac.copy())
        for t, td, g, ac in zip(a["t_host"], a["t_device"], a["gyro"], a["accel"])
    ]
