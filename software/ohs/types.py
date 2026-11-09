"""Shared data types and calibration I/O.

Conventions (ARCHITECTURE.md section 2):
* millimetres for 3D, seconds for time, rad/s gyro, m/s^2 accel;
* camera frame C = rectified LEFT camera, OpenCV axes (x right, y down, z forward);
* poses are 4x4 ``T_wc`` with ``p_w = T_wc @ p_c``;
* stereo extrinsics follow OpenCV: ``x_right = R @ x_left + T``.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

__all__ = [
    "StereoFrame",
    "ImuSample",
    "Event",
    "StereoCalibration",
    "LaserPlane",
    "ImuCalibration",
    "ScannerCalibration",
    "TrackResult",
]

CALIBRATION_FORMAT_VERSION = 1


# --------------------------------------------------------------------------- frames / samples
@dataclass
class StereoFrame:
    """One synchronised stereo exposure (raw, unrectified, grayscale uint8)."""

    index: int
    t_host: float
    left: np.ndarray
    right: np.ndarray

    @staticmethod
    def from_sbs(index: int, t_host: float, sbs: np.ndarray, swap_lr: bool = False) -> "StereoFrame":
        """Split a side-by-side frame (left | right). Colour input is converted to gray."""
        if sbs.ndim == 3:
            sbs = cv2.cvtColor(sbs, cv2.COLOR_BGR2GRAY)
        w = sbs.shape[1] // 2
        a, b = sbs[:, :w], sbs[:, w : 2 * w]
        if swap_lr:
            a, b = b, a
        return StereoFrame(index, t_host, np.ascontiguousarray(a), np.ascontiguousarray(b))

    def to_sbs(self) -> np.ndarray:
        return np.hstack([self.left, self.right])

    @property
    def image_size(self) -> tuple[int, int]:
        """(width, height) of one camera."""
        return int(self.left.shape[1]), int(self.left.shape[0])


@dataclass
class ImuSample:
    """One IMU reading in the IMU frame."""

    t_host: float
    t_device: float
    gyro: np.ndarray  # (3,) rad/s
    accel: np.ndarray  # (3,) m/s^2


@dataclass
class Event:
    """Discrete event, e.g. kind="button", value="down"."""

    t_host: float
    kind: str
    value: str


# --------------------------------------------------------------------------- calibration
def _arr(x: Any, shape: tuple[int, ...] | None = None) -> np.ndarray:
    a = np.asarray(x, dtype=np.float64)
    if shape is not None:
        a = a.reshape(shape)
    return a


def _q_from_projections(P1: np.ndarray, P2: np.ndarray) -> np.ndarray:
    """Disparity-to-depth matrix Q (OpenCV convention) from rectified projections."""
    f = P1[0, 0]
    cx1, cy = P1[0, 2], P1[1, 2]
    cx2 = P2[0, 2]
    tx = P2[0, 3] / P2[0, 0]  # = -baseline
    return np.array(
        [
            [1.0, 0.0, 0.0, -cx1],
            [0.0, 1.0, 0.0, -cy],
            [0.0, 0.0, 0.0, f],
            [0.0, 0.0, -1.0 / tx, (cx1 - cx2) / tx],
        ]
    )


@dataclass
class StereoCalibration:
    """Intrinsics/extrinsics of the stereo pair plus the rectification derived from them.

    ``P1``/``P2`` are the rectified projection matrices (``K_rect`` = ``P1[:3, :3]``), ``R1``/``R2``
    rotate raw camera rays into the rectified frames. Frame C = rectified left camera.
    """

    image_size: tuple[int, int]  # (w, h) of ONE camera
    K1: np.ndarray
    D1: np.ndarray
    K2: np.ndarray
    D2: np.ndarray
    R: np.ndarray
    T: np.ndarray
    R1: np.ndarray
    R2: np.ndarray
    P1: np.ndarray
    P2: np.ndarray
    Q: np.ndarray
    rms: float = float("nan")

    @classmethod
    def build(
        cls,
        image_size: tuple[int, int],
        K1: np.ndarray,
        D1: np.ndarray,
        K2: np.ndarray,
        D2: np.ndarray,
        R: np.ndarray,
        T: np.ndarray,
        rms: float = float("nan"),
        alpha: float = 0.0,
    ) -> "StereoCalibration":
        """Compute rectification (cv2.stereoRectify, zero-disparity, ``alpha``) from raw calibration."""
        K1, K2 = _arr(K1, (3, 3)), _arr(K2, (3, 3))
        D1, D2 = _arr(D1).ravel(), _arr(D2).ravel()
        R, T = _arr(R, (3, 3)), _arr(T).reshape(3)
        size = (int(image_size[0]), int(image_size[1]))
        R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(
            K1, D1, K2, D2, size, R, T.reshape(3, 1), flags=cv2.CALIB_ZERO_DISPARITY, alpha=alpha
        )
        return cls(size, K1, D1, K2, D2, R, T, R1, R2, P1, P2, Q, float(rms))

    @property
    def K_rect(self) -> np.ndarray:
        """Ideal pinhole intrinsics of the rectified images (frame C)."""
        return self.P1[:3, :3].copy()

    @property
    def baseline(self) -> float:
        """Rectified baseline in mm (positive)."""
        return float(-self.P2[0, 3] / self.P2[0, 0])

    def scaled(self, factor: float) -> "StereoCalibration":
        """Calibration for images resized by ``factor`` (e.g. 0.5 for half resolution).

        Uses the pixel-centre convention ``u' = (u + 0.5) * factor - 0.5``.
        """
        f = float(factor)

        def scale_k(K: np.ndarray) -> np.ndarray:
            K = K.copy()
            K[0, 0] *= f
            K[1, 1] *= f
            K[0, 2] = (K[0, 2] + 0.5) * f - 0.5
            K[1, 2] = (K[1, 2] + 0.5) * f - 0.5
            return K

        def scale_p(P: np.ndarray) -> np.ndarray:
            P = P.copy()
            P[0, :] *= f
            P[1, :] *= f
            P[0, 2] = P[0, 2] + 0.5 * f - 0.5
            P[1, 2] = P[1, 2] + 0.5 * f - 0.5
            return P

        P1, P2 = scale_p(self.P1), scale_p(self.P2)
        size = (int(round(self.image_size[0] * f)), int(round(self.image_size[1] * f)))
        return StereoCalibration(
            size,
            scale_k(self.K1),
            self.D1.copy(),
            scale_k(self.K2),
            self.D2.copy(),
            self.R.copy(),
            self.T.copy(),
            self.R1.copy(),
            self.R2.copy(),
            P1,
            P2,
            _q_from_projections(P1, P2),
            self.rms,
        )

    def to_dict(self) -> dict:
        return {
            "image_size": list(self.image_size),
            **{k: getattr(self, k).tolist() for k in ("K1", "D1", "K2", "D2", "R", "T", "R1", "R2", "P1", "P2", "Q")},
            "rms": None if math.isnan(self.rms) else self.rms,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StereoCalibration":
        rms = d.get("rms")
        return cls(
            (int(d["image_size"][0]), int(d["image_size"][1])),
            _arr(d["K1"], (3, 3)),
            _arr(d["D1"]).ravel(),
            _arr(d["K2"], (3, 3)),
            _arr(d["D2"]).ravel(),
            _arr(d["R"], (3, 3)),
            _arr(d["T"]).reshape(3),
            _arr(d["R1"], (3, 3)),
            _arr(d["R2"], (3, 3)),
            _arr(d["P1"], (3, 4)),
            _arr(d["P2"], (3, 4)),
            _arr(d["Q"], (4, 4)),
            float("nan") if rms is None else float(rms),
        )


@dataclass
class LaserPlane:
    """Laser sheet ``n . X + d = 0`` in frame C (mm), ``|n| = 1``, sign normalised so ``d <= 0``."""

    name: str
    normal: np.ndarray
    d: float
    rms: float = float("nan")
    n_points: int = 0

    def __post_init__(self) -> None:
        n = np.asarray(self.normal, dtype=np.float64).reshape(3)
        norm = np.linalg.norm(n)
        if norm == 0:
            raise ValueError("laser plane normal must be non-zero")
        n, d = n / norm, float(self.d) / norm
        if d > 0:
            n, d = -n, -d
        self.normal, self.d = n, d

    def distance(self, X: np.ndarray) -> np.ndarray:
        """Signed distance (mm) of points ``X`` (N,3) to the plane."""
        return np.asarray(X, dtype=np.float64) @ self.normal + self.d

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "normal": self.normal.tolist(),
            "d": self.d,
            "rms": None if math.isnan(self.rms) else self.rms,
            "n_points": int(self.n_points),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LaserPlane":
        rms = d.get("rms")
        return cls(d["name"], _arr(d["normal"]), float(d["d"]), float("nan") if rms is None else float(rms), int(d.get("n_points", 0)))


@dataclass
class ImuCalibration:
    """IMU-to-camera calibration.

    ``v_c = R_ci @ v_i``; camera timestamp = IMU host timestamp + ``time_offset``;
    ``gyro_bias`` (IMU frame, rad/s) is subtracted from raw gyro readings.
    """

    R_ci: np.ndarray = field(default_factory=lambda: np.eye(3))
    time_offset: float = 0.0
    gyro_bias: np.ndarray = field(default_factory=lambda: np.zeros(3))

    def to_dict(self) -> dict:
        return {"R_ci": np.asarray(self.R_ci).tolist(), "time_offset": float(self.time_offset), "gyro_bias": np.asarray(self.gyro_bias).tolist()}

    @classmethod
    def from_dict(cls, d: dict) -> "ImuCalibration":
        return cls(_arr(d["R_ci"], (3, 3)), float(d["time_offset"]), _arr(d["gyro_bias"]).reshape(3))


@dataclass
class ScannerCalibration:
    """Everything the pipeline needs to turn images into geometry."""

    stereo: StereoCalibration
    lasers: dict[str, LaserPlane] = field(default_factory=dict)
    imu: ImuCalibration | None = None

    def to_dict(self) -> dict:
        return {
            "format_version": CALIBRATION_FORMAT_VERSION,
            "stereo": self.stereo.to_dict(),
            "lasers": {k: v.to_dict() for k, v in self.lasers.items()},
            "imu": None if self.imu is None else self.imu.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ScannerCalibration":
        imu = d.get("imu")
        return cls(
            StereoCalibration.from_dict(d["stereo"]),
            {k: LaserPlane.from_dict(v) for k, v in d.get("lasers", {}).items()},
            None if imu is None else ImuCalibration.from_dict(imu),
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "ScannerCalibration":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- tracking
@dataclass
class TrackResult:
    """Pose estimate for one frame. ``info`` holds diagnostics (fitness, rmse, n_markers, reason, ...)."""

    T_wc: np.ndarray
    ok: bool
    info: dict = field(default_factory=dict)
