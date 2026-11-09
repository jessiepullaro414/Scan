"""Nominal scanner geometry for the simulator: the "true" calibration and the emitter layout.

Everything is built from the CAD numbers in ``hardware/scanner.scad`` (ARCHITECTURE.md section 7)
and expressed in frame ``C`` (rectified left camera, OpenCV axes, millimetres):

* right camera centre at ``(baseline, 0, 0)``, ``fx = fy = 720`` px at 1280x800 (83 deg HFOV);
* dot projector at ``(30, -42, -2)`` pointing +z, DOE field 80 x 64.4 deg, ~10k dots;
* vertical-line laser at ``(116.4, 0, -1.4)``, plane ``n = (0.9473, 0, 0.3205)``, ``d = -109.8``;
* horizontal-line laser at ``(30, -75.1, -1.2)``, plane ``n = (0, -0.9594, 0.2822)``, ``d = -71.67``.

The laser planes and emitter origins are derived from the same CAD formulas (laser aim angles
``atan(92/272)`` and ``atan(80/272)``, 35 mm modules centred 18 mm behind the faceplate), so they
reproduce the section-7 numbers to the printed precision and are exactly self-consistent. The CAD
places the emitters relative to the camera-block centre; frame C is the left camera, ``baseline/2``
to the left of it, so emitter x coordinates shift with ``baseline`` (section 7 assumes 60 mm).

Choices made where the contract is open (documented for the integrator):

* ``fx=None`` (default) means 720 px at 1280 px width, scaled with the image width so that e.g.
  ``nominal_calibration((640, 400))`` is the same 83 deg lens at half resolution. An explicit
  ``fx`` is used as given.
* Principal point at the pixel-centre image centre ``((w - 1) / 2, (h - 1) / 2)``, which makes
  ``nominal_calibration((640, 400))`` identical to ``nominal_calibration().stereo.scaled(0.5)``.
* The IMU truth is deliberately not the CAD nominal ``R_ci = I``: it is a 90 deg axis permutation
  (IMU x = camera up, IMU y = camera right, IMU z = camera forward) times a ~3.5 deg mounting error,
  with ``time_offset = 0.030`` s and a small gyro bias, so calibration code has work to do.
* ``realistic=True`` adds Brown-Conrady distortion (k1 ~ -0.05, k2 ~ 0.02, small tangential terms),
  slightly different intrinsics per camera, a 0.30 deg rotation and a tiny off-axis component of
  the baseline. Rectification then is non-trivial and raw images differ from rectified ones.
"""
from __future__ import annotations

import logging
import math

import numpy as np

from ohs.geometry import so3_exp
from ohs.types import ImuCalibration, LaserPlane, ScannerCalibration, StereoCalibration

__all__ = [
    "NOMINAL_WIDTH",
    "NOMINAL_FX",
    "NOMINAL_TIME_OFFSET",
    "nominal_calibration",
    "nominal_emitters",
    "nominal_imu_position",
    "nominal_imu_calibration",
]

log = logging.getLogger(__name__)

NOMINAL_WIDTH = 1280
NOMINAL_FX = 720.0
NOMINAL_TIME_OFFSET = 0.030  # s, camera timestamp = IMU host timestamp + time_offset

# --- CAD parameters (hardware/scanner.scad) --------------------------------------------------
_WORK_DIST = 250.0  # laser sheets cross this far in front of the faceplate
_FP_T = 4.0  # faceplate thickness
_HEAD_D = 36.0  # head depth; laser blocks are centred head_d/2 behind the front face
_BASE_V = 92.0  # camera-block centre -> vertical-line laser (x)
_BASE_H = 80.0  # camera-block centre -> horizontal-line laser (up)
_LASER_LEN = 35.0  # 12 x 35 mm laser modules, emission at the front end
_PROJ_UP = 42.0  # dot projector height above the camera centre
_PROJ_Z = -2.0  # DOE plane, mm along the optical axis (section 7)
_DOT_FOV_DEG = (80.0, 64.4)  # DOE field (h, v); 64.4 deg = 4:3 pinhole aspect of 80 deg
_N_DOTS = 10000
_IMU_BEHIND = 31.0  # IMU board on the back cover, mm behind the lens centres
_IMU_X_WORLD = -30.0  # CAD imu_pos x (camera-block coordinates)


def _laser_layout(baseline: float) -> dict[str, dict]:
    """Origins, beam directions, fan axes and planes of both line lasers in frame C."""
    half = 0.5 * float(baseline)
    reach = _WORK_DIST + _FP_T + _HEAD_D / 2.0  # 272 mm from the laser pivots to the crossing
    a_v = math.atan(_BASE_V / reach)
    a_h = math.atan(_BASE_H / reach)
    front = _LASER_LEN / 2.0  # emission point ahead of the module centre
    # Vertical-line laser: rotated about the up axis, aims back toward the centre line.
    o_v = np.array([half + _BASE_V - front * math.sin(a_v), 0.0, -_HEAD_D / 2 + front * math.cos(a_v)])
    b_v = np.array([-math.sin(a_v), 0.0, math.cos(a_v)])
    f_v = np.array([0.0, 1.0, 0.0])  # the fan spreads along the camera y axis -> vertical line
    # Horizontal-line laser: tilted about the x axis, aims down toward the centre line.
    o_h = np.array([half, -(_BASE_H - front * math.sin(a_h)), -_HEAD_D / 2 + front * math.cos(a_h)])
    b_h = np.array([0.0, math.sin(a_h), math.cos(a_h)])
    f_h = np.array([1.0, 0.0, 0.0])  # the fan spreads along the camera x axis -> horizontal line
    out = {}
    for name, o, b, f in (("vertical", o_v, b_v, f_v), ("horizontal", o_h, b_h, f_h)):
        n = np.cross(f, b)
        n /= np.linalg.norm(n)
        d = -float(n @ o)
        if d > 0:  # contract: d <= 0
            n, d = -n, -d
        out[name] = {"origin": o, "direction": b, "fan_axis": f, "normal": n, "d": d}
    return out


def nominal_imu_position(baseline: float = 60.0) -> np.ndarray:
    """IMU position in frame C (mm): on the back cover, ~31 mm behind the lenses."""
    return np.array([_IMU_X_WORLD + 0.5 * float(baseline), 0.0, -_IMU_BEHIND])


def nominal_imu_calibration() -> ImuCalibration:
    """True IMU-camera calibration used by the simulator (not identity on purpose)."""
    # IMU axes expressed in C (columns): x_i = camera up (-y_c), y_i = camera right (x_c), z_i = z_c.
    R_perm = np.array([[0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    R_mount = so3_exp(np.radians([1.5, -2.0, 2.5]))  # ~3.5 deg board mounting error
    R_ci = R_perm @ R_mount
    gyro_bias = np.array([0.0042, -0.0031, 0.0017])  # rad/s, IMU frame (~0.1-0.25 dps)
    return ImuCalibration(R_ci=R_ci, time_offset=NOMINAL_TIME_OFFSET, gyro_bias=gyro_bias)


def nominal_calibration(
    image_size: tuple[int, int] = (1280, 800),
    fx: float | None = None,
    baseline: float = 60.0,
    realistic: bool = False,
) -> ScannerCalibration:
    """True scanner calibration for simulation (stereo + laser planes + IMU).

    ``fx=None`` scales the nominal 720 px (at 1280 px width) with ``image_size``. With
    ``realistic=False`` the cameras are ideal, parallel and undistorted, so raw == rectified.
    """
    w, h = int(image_size[0]), int(image_size[1])
    s = w / NOMINAL_WIDTH
    f = NOMINAL_FX * s if fx is None else float(fx)
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    if realistic:
        K1 = np.array([[f, 0.0, cx + 4.2 * s], [0.0, f * 1.0008, cy - 3.1 * s], [0.0, 0.0, 1.0]])
        K2 = np.array([[f * 0.9975, 0.0, cx - 2.7 * s], [0.0, f * 0.9982, cy + 1.9 * s], [0.0, 0.0, 1.0]])
        D1 = np.array([-0.050, 0.020, 0.0006, -0.0004, 0.0])
        D2 = np.array([-0.046, 0.017, -0.0003, 0.0005, 0.0])
        axis = np.array([0.35, 0.9, -0.25])
        R = so3_exp(np.radians(0.30) * axis / np.linalg.norm(axis))
        t_dir = np.array([-1.0, 0.006, -0.004])
        T = float(baseline) * t_dir / np.linalg.norm(t_dir)
    else:
        K1 = np.array([[f, 0.0, cx], [0.0, f, cy], [0.0, 0.0, 1.0]])
        K2 = K1.copy()
        D1 = np.zeros(5)
        D2 = np.zeros(5)
        R = np.eye(3)
        T = np.array([-float(baseline), 0.0, 0.0])
    stereo = StereoCalibration.build((w, h), K1, D1, K2, D2, R, T, rms=0.0)
    lasers = {
        name: LaserPlane(name, g["normal"], g["d"], rms=0.0, n_points=0)
        for name, g in _laser_layout(baseline).items()
    }
    calib = ScannerCalibration(stereo=stereo, lasers=lasers, imu=nominal_imu_calibration())
    log.debug("nominal calibration %dx%d fx=%.2f baseline=%.1f realistic=%s", w, h, f, baseline, realistic)
    return calib


def nominal_emitters(fan_deg: float = 60.0, baseline: float = 60.0) -> dict:
    """Emitter layout in frame C, consistent with :func:`nominal_calibration` laser planes.

    Returns ``{"dots": {...}, "vertical": {...}, "horizontal": {...}}``:

    * ``dots``: ``type="dot_projector"``, ``origin`` (3,), ``direction`` (3,) (+z), ``R`` (3x3,
      projector axes -> C; the projector images like a camera with x right, y down),
      ``fov_deg`` (h, v) of the DOE field, ``n_dots``.
    * ``vertical`` / ``horizontal``: ``type="line_laser"``, ``origin`` (3,) emission point,
      ``direction`` (3,) centre of the fan, ``fan_axis`` (3,) in-plane direction the fan spreads
      along, ``fan_deg`` full fan angle, ``normal`` / ``d`` of the sheet (``n . X + d = 0``).
    """
    half = 0.5 * float(baseline)
    out: dict = {
        "dots": {
            "type": "dot_projector",
            "origin": np.array([half, -_PROJ_UP, _PROJ_Z]),
            "direction": np.array([0.0, 0.0, 1.0]),
            "R": np.eye(3),
            "fov_deg": tuple(_DOT_FOV_DEG),
            "n_dots": _N_DOTS,
        }
    }
    for name, g in _laser_layout(baseline).items():
        out[name] = {"type": "line_laser", "fan_deg": float(fan_deg), **{k: np.array(v) if isinstance(v, np.ndarray) else v for k, v in g.items()}}
    return out
