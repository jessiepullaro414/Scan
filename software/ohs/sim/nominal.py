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

The 4-laser variant (``lasers=4``; ``lasers = 4`` in the CAD) adds ``"vertical_2"``, the mirror of
the vertical-line laser on the left of the cameras, and ``"horizontal_2"``, a horizontal-line laser
58 mm right of and 56 mm below the camera centre (beside the grip's yoke), tilted up. All four
sheets cross the cameras' centre line 254 mm ahead of the left camera. The module positions are in
one table, :data:`LASER_MODULES`, their aims in another, :data:`LASER_LAYOUTS`: the layouts as
built (``"2"``, ``"4"``) and the aims compared for the second pair (``layout=`` of
:func:`nominal_calibration`; ``lasers=4`` is the layout as built).

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
    "LASER_MODULES",
    "LASER_SETS",
    "LASER_LAYOUTS",
    "layout_planes",
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


#: The line lasers' module centres in the housing's frame (X right, Z up, mm from the camera-block
#: centre; ``hardware/scanner.scad``), by name: ``(x, z)``. Each one aims at the cameras' centre
#: line, crossing it ``reach`` = 272 mm ahead of the module centres: a vertical-line laser is turned
#: about the up axis by ``atan(x / reach)``, a horizontal-line one tilted by ``atan(z / reach)``.
#: The 4-laser variant (``lasers = 4``) adds the mirror of the right laser on the left and a low
#: horizontal laser beside the grip's yoke (``yoke_reach + lblk_l / 2`` = 58 mm right, ``base_h2``
#: = 56 mm below).
LASER_MODULES: dict[str, tuple[float, float]] = {
    "vertical": (_BASE_V, 0.0),
    "horizontal": (0.0, _BASE_H),
    "vertical_2": (-_BASE_V, 0.0),
    "horizontal_2": (58.0, -56.0),
}
#: the lasers of each variant, in order
LASER_SETS: dict[int, tuple[str, ...]] = {
    2: ("vertical", "horizontal"),
    4: ("vertical", "horizontal", "vertical_2", "horizontal_2"),
}

#: Where each laser aims, per layout: ``{layout: {laser: aim}}``, the modules at
#: :data:`LASER_MODULES`. An aim is a number ``D``: the sheet crosses the cameras' centre line
#: ``D`` mm ahead of the faceplate (``math.inf``: straight ahead, parallel to the optical axis), or
#: ``("deg", a)``: turned ``a`` degrees toward the centre line (negative: away from it).
#: ``"2"`` and ``"4"`` are the housing as built (``lasers = 2`` / ``4``: every sheet crosses at the
#: working distance, 250 mm); the others are the aims compared for the second pair (``lasers=4``
#: stays the layout as built until one is chosen).
LASER_LAYOUTS: dict[str, dict[str, float | tuple[str, float]]] = {
    "2": {"vertical": _WORK_DIST, "horizontal": _WORK_DIST},
    "4": {"vertical": _WORK_DIST, "horizontal": _WORK_DIST, "vertical_2": _WORK_DIST, "horizontal_2": _WORK_DIST},
    # the second pair straight ahead
    "4-straight": {"vertical": _WORK_DIST, "horizontal": _WORK_DIST, "vertical_2": math.inf, "horizontal_2": math.inf},
    # the second pair crossing the centre line far out
    "4-cross600": {"vertical": _WORK_DIST, "horizontal": _WORK_DIST, "vertical_2": 600.0, "horizontal_2": 600.0},
    # the second pair crossing the centre line a little farther out: its lines still near the image
    # centre at 250 mm, each family's sheets meeting at about 290-295 mm
    "4-cross350": {"vertical": _WORK_DIST, "horizontal": _WORK_DIST, "vertical_2": 350.0, "horizontal_2": 350.0},
    # the second pair crossing the centre line near, so that each family's sheets meet at about 195-200 mm
    "4-cross150": {"vertical": _WORK_DIST, "horizontal": _WORK_DIST, "vertical_2": 150.0, "horizontal_2": 150.0},
    # the second pair turned slightly away from the centre line, so that each family's sheets meet
    # beyond the working range (about 590 and 640 mm) while the left line stays inside the right
    # image at 150 mm (2 deg: 26 px from its edge) and the low line inside the images (5 deg)
    "4-apart": {"vertical": _WORK_DIST, "horizontal": _WORK_DIST, "vertical_2": ("deg", -2.0), "horizontal_2": ("deg", -5.0)},
}


def _laser_names(lasers: int) -> tuple[str, ...]:
    if int(lasers) not in LASER_SETS:
        raise ValueError(f"lasers must be one of {sorted(LASER_SETS)}, got {lasers!r}")
    return LASER_SETS[int(lasers)]


def _layout_aims(lasers: int = 2, layout: str | dict | None = None) -> dict[str, float | tuple[str, float]]:
    """The aims of a layout (a name of :data:`LASER_LAYOUTS` or such a dict); ``None``: as built for ``lasers``."""
    if layout is None:
        return LASER_LAYOUTS[str(len(_laser_names(lasers)))]
    if isinstance(layout, str):
        if layout not in LASER_LAYOUTS:
            raise ValueError(f"unknown laser layout {layout!r}; layouts: {sorted(LASER_LAYOUTS)}")
        return LASER_LAYOUTS[layout]
    return dict(layout)


def _aim_angle(offset: float, aim: float | tuple[str, float]) -> float:
    """Aim angle (rad, positive toward the centre line) of a laser ``offset`` mm off the centre line."""
    if isinstance(aim, tuple):
        return math.radians(float(aim[1])) * (1.0 if offset >= 0 else -1.0)
    return math.atan(offset / (aim + _FP_T + _HEAD_D / 2.0))  # crossing reach from the module centres


def _laser_layout(baseline: float, lasers: int = 2, layout: str | dict | None = None) -> dict[str, dict]:
    """Origins, beam directions, fan axes and planes of the line lasers in frame C."""
    half = 0.5 * float(baseline)
    front = _LASER_LEN / 2.0  # emission point ahead of the module centre
    out = {}
    for name, aim in _layout_aims(lasers, layout).items():
        x, z = LASER_MODULES[name]
        if name.startswith("vertical"):
            # rotated about the up axis, aims back toward the centre line; the fan spreads along
            # the camera y axis -> vertical line
            a = _aim_angle(x, aim)
            o = np.array([half + x - front * math.sin(a), 0.0 - z, -_HEAD_D / 2 + front * math.cos(a)])
            b = np.array([-math.sin(a), 0.0, math.cos(a)])
            f = np.array([0.0, 1.0, 0.0])
        else:
            # tilted about the x axis, aims toward the centre line (down from above, up from below);
            # the fan spreads along the camera x axis -> horizontal line
            a = _aim_angle(z, aim)
            o = np.array([half + x, -(z - front * math.sin(a)), -_HEAD_D / 2 + front * math.cos(a)])
            b = np.array([0.0, math.sin(a), math.cos(a)])
            f = np.array([1.0, 0.0, 0.0])
        n = np.cross(f, b)
        n /= np.linalg.norm(n)
        d = -float(n @ o)
        if d > 0:  # contract: d <= 0
            n, d = -n, -d
        out[name] = {"origin": o, "direction": b, "fan_axis": f, "normal": n, "d": d}
    return out


def layout_planes(layout: str | dict, baseline: float = 60.0) -> dict[str, tuple[np.ndarray, float]]:
    """``{laser: (normal, d)}`` of a layout's sheets in frame C."""
    return {k: (g["normal"], g["d"]) for k, g in _laser_layout(baseline, 2, layout).items()}


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
    lasers: int = 2,
    layout: str | None = None,
) -> ScannerCalibration:
    """True scanner calibration for simulation (stereo + laser planes + IMU).

    ``fx=None`` scales the nominal 720 px (at 1280 px width) with ``image_size``. With
    ``realistic=False`` the cameras are ideal, parallel and undistorted, so raw == rectified.
    ``lasers`` is 2 (``"vertical"``, ``"horizontal"``) or 4 (also ``"vertical_2"`` and
    ``"horizontal_2"``, see :data:`LASER_MODULES`), aimed as built; ``layout`` (a name of
    :data:`LASER_LAYOUTS`) picks other aims and then decides the lasers.
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
    planes = {
        name: LaserPlane(name, g["normal"], g["d"], rms=0.0, n_points=0)
        for name, g in _laser_layout(baseline, lasers, layout).items()
    }
    calib = ScannerCalibration(stereo=stereo, lasers=planes, imu=nominal_imu_calibration())
    log.debug("nominal calibration %dx%d fx=%.2f baseline=%.1f realistic=%s", w, h, f, baseline, realistic)
    return calib


def nominal_emitters(fan_deg: float = 60.0, baseline: float = 60.0, lasers: int = 2, layout: str | None = None) -> dict:
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
    for name, g in _laser_layout(baseline, lasers, layout).items():
        out[name] = {"type": "line_laser", "fan_deg": float(fan_deg), **{k: np.array(v) if isinstance(v, np.ndarray) else v for k, v in g.items()}}
    return out
