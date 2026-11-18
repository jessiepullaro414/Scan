"""Which signs of a scan's ``health`` block show a stale calibration, measured (FEATURE_PLAN H2).

    python tools/calib_health_study.py all <work folder>            # everything below, in order
    python tools/calib_health_study.py render <work folder>         # the simulated recordings
    python tools/calib_health_study.py true <work folder> [--rec NAME ...]
    python tools/calib_health_study.py sweep <work folder> [--rec NAME ...] [--fault NAME ...]
    python tools/calib_health_study.py table <work folder>          # the Markdown tables, from the cache
    (add --cpu to any of them for the CPU path, OHS_COMPUTE=cpu; its runs are cached apart)

A calibration goes stale when the scanner is knocked, a screw loosens or the heat bends the
housing. A scan cannot see that directly, but several numbers it writes down move when it happens
(``ohs.calib.scan_health``, H1). This study renders recordings with the simulator's TRUE
calibration and processes them (``process_recording``, the default settings: every frame, full
resolution, TSDF mesh) with a calibration that is slightly wrong, one fault at a time:

* the cameras' relative angle (:func:`rotate_cameras`): ``pitch`` about the baseline shifts the
  rows (the launch check's row error), ``yaw`` about the up axis shifts the disparity, so the
  depth, ``roll`` about the optical axis turns the rows;
* the baseline (:func:`scale_baseline`);
* one laser plane's tilt about its emitter (:func:`tilt_laser`) or its offset (:func:`shift_laser`);
* the IMU's rotation (:func:`rotate_imu`) and its time offset (:func:`shift_imu_time`).

For each run it keeps the health block's signs (:func:`signs`) and how far the products moved
against the simulator's true shapes (:func:`judge`): the mesh, a few fitted measurements of the
bracket, the laser cloud, and the mesh after "Refine with lasers" (``ohs.mesh.anchor_to_points``).
The table compares each against the run with the true calibration on the same recording, so what is
left is the fault's effect. The helpers are importable (``from tools import calib_health_study``),
for the tests of the status (H3).

Everything goes into the work folder: ``recs/<name>`` (the recordings), ``runs/<compute>/<rec>/
<fault>@<size>.json`` (one run's numbers; a run that is there is never computed again) and
``tables_<compute>.md``. The processing's own output folder is deleted after each run.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

SOFTWARE = Path(__file__).resolve().parents[1]
if str(SOFTWARE) not in sys.path:
    sys.path.insert(0, str(SOFTWARE))

from ohs import geometry as G  # noqa: E402
from ohs.types import ImuCalibration, LaserPlane, ScannerCalibration, StereoCalibration  # noqa: E402

__all__ = ["CAMERA_AXES", "IMU_AXIS", "FAULTS", "RECORDINGS", "rotate_cameras", "scale_baseline", "laser_axis",
           "tilt_laser", "shift_laser", "rotate_imu", "shift_imu_time", "perturb", "fault_part", "signs", "judge",
           "make_recording", "run_case"]

# --------------------------------------------------------------------------- the perturbations
#: the right camera's turn, about an axis of its own frame: about the baseline (rows), the up axis
#: (disparity, so depth) or the optical axis (rows, turned about the image centre)
CAMERA_AXES: dict[str, tuple[float, float, float]] = {"pitch": (1.0, 0.0, 0.0), "yaw": (0.0, 1.0, 0.0),
                                                      "roll": (0.0, 0.0, 1.0)}
#: the IMU's turn, about this axis of frame C (the camera's x: a board that tipped forward). The
#: simulated hand-held sweep turns mostly about the world's up axis, which the camera sees between
#: its y and z axes, so a turn about x changes the rotation the gyro predicts the most.
IMU_AXIS: tuple[float, float, float] = (1.0, 0.0, 0.0)


def _fresh(calib: ScannerCalibration, **changes: Any) -> ScannerCalibration:
    """``calib`` with ``changes`` and without the keys that say which calibration it is: a perturbed
    calibration is not the one it came from, so its ``id`` (and time, temperature, steps) would lie."""
    return dataclasses.replace(calib, id=None, created=None, temperature_c=None, steps=None, **changes)


def _rebuilt_stereo(stereo: StereoCalibration, R: np.ndarray, T: np.ndarray) -> StereoCalibration:
    # alpha 0, as ohs.calib.stereo and the simulator build it
    return StereoCalibration.build(stereo.image_size, stereo.K1, stereo.D1, stereo.K2, stereo.D2, R, T,
                                   rms=stereo.rms, alpha=0.0)


def _with_stereo(calib: ScannerCalibration, stereo: StereoCalibration) -> ScannerCalibration:
    """``calib`` with a new stereo pair, its laser planes and IMU carried into the new rectified frame.

    Frame C is the rectified LEFT camera; a new pair rectifies it with a new rotation ``R1``. The
    lasers and the IMU are fixed to the housing, as the left camera is: they did not move, only the
    frame they are written in turned (``M = R1_new R1_old^T``). Without this a camera fault would
    also tilt every laser plane by half the angle and be a laser fault as well."""
    M = stereo.R1 @ calib.stereo.R1.T
    lasers = {k: LaserPlane(p.name, M @ p.normal, p.d, p.rms, p.n_points) for k, p in calib.lasers.items()}
    imu = None if calib.imu is None else dataclasses.replace(calib.imu, R_ci=M @ calib.imu.R_ci)
    return _fresh(calib, stereo=stereo, lasers=lasers, imu=imu)


def rotate_cameras(calib: ScannerCalibration, deg: float, axis: str = "pitch") -> ScannerCalibration:
    """The calibration as if the right camera had been turned ``deg`` degrees about ``axis`` of its
    own frame (:data:`CAMERA_AXES`) since: ``R' = exp(axis * deg) R``, the rest of the pair the same,
    the rectification made anew, the lasers and the IMU unmoved on the left camera."""
    w = np.radians(float(deg)) * np.asarray(CAMERA_AXES[axis], dtype=np.float64)
    st = calib.stereo
    return _with_stereo(calib, _rebuilt_stereo(st, G.so3_exp(w) @ st.R, st.T))


def scale_baseline(calib: ScannerCalibration, fraction: float) -> ScannerCalibration:
    """The calibration with the baseline ``fraction`` longer (0.01 = 1 %): ``T' = (1 + fraction) T``."""
    st = calib.stereo
    return _with_stereo(calib, _rebuilt_stereo(st, st.R, st.T * (1.0 + float(fraction))))


def laser_axis(plane: LaserPlane, axis: str = "aim") -> np.ndarray:
    """A unit line in a laser sheet to turn it about. ``"aim"``: the line across the image (normal x
    optical axis), so the sheet tips toward or away from the cameras and the triangulation angle
    changes, as a module knocked in its clamp does; ``"roll"``: the line along the beam, so the
    drawn line turns."""
    n = np.asarray(plane.normal, dtype=np.float64)
    a = np.cross(n, np.array([0.0, 0.0, 1.0]))
    a /= np.linalg.norm(a)
    if axis == "aim":
        return a
    if axis == "roll":
        b = np.cross(n, a)
        return b / np.linalg.norm(b)
    raise ValueError(f"unknown laser axis {axis!r}: 'aim' or 'roll'")


def _emitter(calib: ScannerCalibration, name: str) -> np.ndarray:
    """Where the laser leaves its module (frame C): the simulator's emitter for a known laser, put
    onto the plane (it is on it for the true calibration); else the plane's point nearest the camera."""
    from ohs.sim.nominal import LASER_MODULES, nominal_emitters

    p = calib.lasers[name]
    if name in LASER_MODULES:
        o = np.asarray(nominal_emitters(baseline=calib.stereo.baseline, lasers=4)[name]["origin"], dtype=np.float64)
    else:
        o = np.zeros(3)
    return o - p.distance(o[None])[0] * p.normal


def tilt_laser(calib: ScannerCalibration, name: str, deg: float, axis: str = "aim",
               pivot: np.ndarray | None = None) -> ScannerCalibration:
    """The calibration with laser ``name``'s plane turned ``deg`` degrees about :func:`laser_axis`
    through ``pivot`` (default: its emitter, about which a module turns). KeyError for an unknown laser."""
    p = calib.lasers[name]
    o = _emitter(calib, name) if pivot is None else np.asarray(pivot, dtype=np.float64)
    n = G.so3_exp(np.radians(float(deg)) * laser_axis(p, axis)) @ p.normal
    lasers = dict(calib.lasers)
    lasers[name] = LaserPlane(p.name, n, -float(n @ o), p.rms, p.n_points)
    return _fresh(calib, lasers=lasers)


def shift_laser(calib: ScannerCalibration, name: str, mm: float) -> ScannerCalibration:
    """The calibration with laser ``name``'s plane moved ``mm`` along its normal (+ = away from the
    camera centre). KeyError for an unknown laser."""
    p = calib.lasers[name]
    lasers = dict(calib.lasers)
    lasers[name] = LaserPlane(p.name, p.normal, p.d - float(mm), p.rms, p.n_points)
    return _fresh(calib, lasers=lasers)


def _imu(calib: ScannerCalibration) -> ImuCalibration:
    if calib.imu is None:
        raise ValueError("the calibration has no IMU part")
    return calib.imu


def rotate_imu(calib: ScannerCalibration, deg: float, axis: tuple[float, float, float] = IMU_AXIS) -> ScannerCalibration:
    """The calibration with the IMU's rotation turned ``deg`` degrees about ``axis`` of frame C:
    ``R_ci' = exp(axis * deg) R_ci``."""
    a = np.asarray(axis, dtype=np.float64)
    imu = _imu(calib)
    R = G.so3_exp(np.radians(float(deg)) * a / np.linalg.norm(a)) @ imu.R_ci
    return _fresh(calib, imu=dataclasses.replace(imu, R_ci=R))


def shift_imu_time(calib: ScannerCalibration, seconds: float) -> ScannerCalibration:
    """The calibration with the camera-to-IMU time offset ``seconds`` later."""
    imu = _imu(calib)
    return _fresh(calib, imu=dataclasses.replace(imu, time_offset=float(imu.time_offset) + float(seconds)))


#: the faults of the study: ``name -> (part, unit, sizes)``. ``laser_tilt`` and ``laser_offset``
#: take the laser after a colon (``"laser_tilt:vertical"``). Sizes are in the table's units.
FAULTS: dict[str, tuple[str, str, tuple[float, ...]]] = {
    "camera_pitch": ("cameras", "deg", (0.01, 0.02, 0.05, 0.1)),
    "camera_yaw": ("cameras", "deg", (0.01, 0.02, 0.05, 0.1)),
    "camera_roll": ("cameras", "deg", (0.02, 0.05, 0.1)),
    "baseline": ("cameras", "%", (0.1, 0.2, 0.5, 1.0)),
    "laser_tilt": ("lasers", "deg", (0.02, 0.05, 0.1, 0.2)),
    "laser_offset": ("lasers", "mm", (0.05, 0.1, 0.2, 0.5)),
    "imu_rotation": ("imu", "deg", (0.5, 1.0, 2.0, 5.0)),
    "imu_time": ("imu", "ms", (2.0, 5.0, 10.0, 20.0)),
}


#: a fault too small to matter (0.013 px of rows): how far the products move from any change at all,
#: the tracking taking a slightly different path (the floor under every cost of the table)
FLOOR: tuple[tuple[str, float], ...] = (("camera_pitch", 0.001),)


def _split(fault: str) -> tuple[str, str | None]:
    kind, _, line = fault.partition(":")
    if kind == "none":
        return kind, None
    if kind not in FAULTS or (kind.startswith("laser_") != bool(line)):
        raise ValueError(f"unknown fault {fault!r}; faults: none, {', '.join(FAULTS)} (laser_*: with ':<laser>')")
    return kind, line or None


def fault_part(fault: str) -> str:
    """The part a fault belongs to: ``"cameras"``, ``"lasers"`` or ``"imu"`` (``"none"``: none)."""
    kind, _ = _split(fault)
    return "none" if kind == "none" else FAULTS[kind][0]


def perturb(calib: ScannerCalibration, fault: str, size: float) -> ScannerCalibration:
    """``calib`` with one fault of :data:`FAULTS` of ``size`` in the table's units (degrees, % of
    the baseline, mm, ms); ``"none"`` gives a copy without its ``id``. ValueError for an unknown fault."""
    kind, line = _split(fault)
    s = float(size)
    if kind == "none":
        return _fresh(calib)
    if kind.startswith("camera_"):
        return rotate_cameras(calib, s, kind.split("_", 1)[1])
    if kind == "baseline":
        return scale_baseline(calib, s / 100.0)
    if kind == "laser_tilt":
        return tilt_laser(calib, str(line), s)
    if kind == "laser_offset":
        return shift_laser(calib, str(line), s)
    if kind == "imu_rotation":
        return rotate_imu(calib, s)
    return shift_imu_time(calib, s / 1000.0)


# --------------------------------------------------------------------------- the recordings
#: the simulated recordings (1280 x 800, the scanner's camera mode): the bracket twice for the
#: faults, and the hard but well-calibrated scenes the true calibration must stay quiet on. The
#: renderer has no shine (no specular reflection), so "shiny" is stood in for by strong laser
#: speckle and sensor noise on the bracket: what a glossy part does to the lines and the dots that
#: the simulator can make.
RECORDINGS: dict[str, dict] = {
    "bracket-s0": {"scene": "bracket", "seed": 0, "frames": 120},
    "bracket-s1": {"scene": "bracket", "seed": 1, "frames": 120},
    "bracket-dark": {"scene": "bracket", "seed": 2, "frames": 90, "dark": 0.12},
    "bracket-speckle": {"scene": "bracket", "seed": 3, "frames": 90,
                        "render": {"speckle": 0.45, "noise_sigma": 4.0, "shot_noise": 0.05}},
    "plate-0.5mm": {"scene": "thin_plate", "seed": 4, "frames": 90, "thickness": 0.5},
    "sphere": {"scene": "sphere_on_plane", "seed": 5, "frames": 90},
    "panel": {"scene": "car_panel", "seed": 6, "frames": 90},
}
HARD = ("bracket-dark", "bracket-speckle", "plate-0.5mm")


def scene_of(spec: dict):
    """The :class:`~ohs.sim.scene.Scene` of a recording spec (the same object rendered and judged)."""
    from ohs.sim.scene import NoiseAlbedo, make_scene, thin_plate

    if spec["scene"] == "thin_plate":
        return thin_plate(seed=int(spec["seed"]), thickness=float(spec.get("thickness", 1.0)))
    sc = make_scene(spec["scene"], int(spec["seed"]))
    if spec.get("dark"):
        for k, p in enumerate(sc.primitives):
            if p.name != "table":  # a dark part (anodised, cast iron) on the usual bench
                p.albedo = NoiseAlbedo(mean=float(spec["dark"]), contrast=0.03, scale_mm=2.0, seed=int(spec["seed"]) + k)
    return sc


def make_recording(path: Path, spec: dict, image_size: tuple[int, int] = (1280, 800)) -> Path:
    """Render one recording of :data:`RECORDINGS` (once: an existing one is kept)."""
    from ohs.sim.datasets import make_scan_recording
    from ohs.sim.render import RenderParams

    if (path / "frames.csv").is_file() and (path / "done").is_file():
        return path
    shutil.rmtree(path, ignore_errors=True)
    rp = RenderParams(**spec["render"]) if spec.get("render") else None
    make_scan_recording(path, scene=scene_of(spec), n_frames=int(spec["frames"]), image_size=image_size, seed=int(spec["seed"]),
                        realistic=True, depth_every=0, params=rp)
    (path / "done").write_text("", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- the signs
def _get(d: Any, *keys: str) -> Any:
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def signs(report: dict) -> dict[str, float | None]:
    """The health block's signs as one flat dict (``name`` or ``name:<laser>``), from a report."""
    h = report.get("health") or {}
    out: dict[str, float | None] = {
        "row_error_px": _get(h, "row_error", "median_px"), "row_error_p90_px": _get(h, "row_error", "p90_px"),
        "row_error_signed_px": _get(h, "row_error", "signed_px"),
        "laser_mesh_p50_mm": _get(h, "laser_mesh", "all", "stats", "p50_mm"),
        "tracking_rmse_mm": _get(h, "tracking", "rmse_mm", "median"),
        "tracking_rmse_p95_mm": _get(h, "tracking", "rmse_mm", "p95"),
        "gyro_jump_deg": _get(h, "tracking", "gyro_jump_deg", "median"),
        "gyro_jump_p95_deg": _get(h, "tracking", "gyro_jump_deg", "p95"),
        "tracked_fraction": _get(report, "frames", "tracked_fraction"),
    }
    for name, s in (_get(h, "laser_depth", "per_line") or {}).items():
        out[f"depth_gap_mm:{name}"] = s.get("median_mm")
        out[f"depth_gap_abs_mm:{name}"] = s.get("abs_median_mm")
        p05, p95 = s.get("p05_mm"), s.get("p95_mm")
        out[f"depth_gap_spread_mm:{name}"] = None if p05 is None or p95 is None else round(p95 - p05, 4)
        out[f"depth_rejected_share:{name}"] = round(s["rejected"] / s["points"], 5) if s.get("points") else None
        # the line's points that passed the right-image check, per frame (the depth check comes after)
        out[f"laser_points_per_frame:{name}"] = round(s["points"] / s["frames"], 1) if s.get("frames") else None
    for name, s in (_get(h, "laser_mesh", "per_line") or {}).items():
        out[f"mesh_gap_mm:{name}"] = s.get("median_mm")
        out[f"mesh_gap_abs_p95_mm:{name}"] = s.get("abs_p95_mm")
    return out


# --------------------------------------------------------------------------- the truth
def _box_sdf(pl: np.ndarray, half: np.ndarray) -> np.ndarray:
    q = np.abs(pl) - half
    return np.linalg.norm(np.maximum(q, 0.0), axis=1) + np.minimum(q.max(axis=1), 0.0)


def _cyl_sdf(pl: np.ndarray, r: float, hh: float) -> np.ndarray:
    d = np.column_stack([np.hypot(pl[:, 0], pl[:, 1]) - r, np.abs(pl[:, 2]) - hh])
    return np.linalg.norm(np.maximum(d, 0.0), axis=1) + np.minimum(d.max(axis=1), 0.0)


class Truth:
    """The scene's solids as signed distances (mm, + outside): boxes, capped cylinders, spheres and
    the table as the half-space under it. ``prim_sdf(P)`` gives each solid's (N, k); the scene's is
    their minimum. A height field (the car panel) has none: ValueError."""

    def __init__(self, scene) -> None:
        from ohs.sim.scene import Box, Cylinder, Rect, Sphere

        self.prims = []
        for p in scene.primitives:
            if not isinstance(p, (Box, Cylinder, Rect, Sphere)):
                raise ValueError(f"no signed distance for {type(p).__name__} ({p.name})")
            self.prims.append(p)
        self.names = [p.name for p in self.prims]
        self._types = (Box, Cylinder, Rect, Sphere)

    def prim_sdf(self, P: np.ndarray) -> np.ndarray:
        Box, Cylinder, Rect, Sphere = self._types
        out = np.empty((len(P), len(self.prims)))
        for k, p in enumerate(self.prims):
            pl = (P - p.t) @ p.R
            if isinstance(p, Box):
                out[:, k] = _box_sdf(pl, p.half)
            elif isinstance(p, Cylinder):
                out[:, k] = _cyl_sdf(pl, p.radius, p.half_height)
            elif isinstance(p, Sphere):
                out[:, k] = np.linalg.norm(pl, axis=1) - p.radius
            else:  # a table: its geometric normal is local -z, the solid lies behind it
                out[:, k] = -pl[:, 2]
        return out

    def sdf(self, P: np.ndarray) -> np.ndarray:
        return self.prim_sdf(P).min(axis=1)

    def closest(self, P: np.ndarray, h: float = 0.05) -> np.ndarray:
        """The nearest surface point of each point (one gradient step; exact on faces)."""
        d = self.sdf(P)
        g = np.zeros_like(P)
        for a in range(3):
            e = np.zeros(3)
            e[a] = h
            g[:, a] = (self.sdf(P + e) - self.sdf(P - e)) / (2 * h)
        g /= np.maximum(np.linalg.norm(g, axis=1, keepdims=True), 1e-9)
        return P - d[:, None] * g


def _kabsch(P: np.ndarray, Q: np.ndarray) -> np.ndarray:
    cp, cq = P.mean(axis=0), Q.mean(axis=0)
    U, _, Wt = np.linalg.svd((P - cp).T @ (Q - cq))
    D = np.diag([1.0, 1.0, np.sign(np.linalg.det(Wt.T @ U.T))])
    R = Wt.T @ D @ U.T
    return G.make_se3(R, cq - R @ cp)


def place(V: np.ndarray, T0: np.ndarray, truth: Truth, iters: int = 20, gate: float = 1.5) -> np.ndarray:
    """``T0`` (the scan's world into the simulator's) refined by ICP of the scan onto the true
    surface, so a rigid misplacement (the first frame's pose, drift) is not counted as shape."""
    rng = np.random.default_rng(0)
    S = V[rng.choice(len(V), size=min(len(V), 25000), replace=False)] if len(V) else V
    T = T0.copy()
    for k in range(iters):
        P = G.transform_points(T, S)
        Q = truth.closest(P)
        sel = np.linalg.norm(P - Q, axis=1) < (2.0 * gate if k < 5 else gate)  # wider at first: a scaled scan
        if sel.sum() < 100:
            break
        dT = _kabsch(P[sel], Q[sel])
        T = dT @ T
        if np.linalg.norm(dT[:3, 3]) < 1e-4 and G.rotation_angle_deg(dT[:3, :3]) < 1e-5:
            break
    return T


def _feature_points(P: np.ndarray, truth: Truth, edge_mm: float = 1.5, near_mm: float = 0.8) -> dict[str, np.ndarray]:
    """Indices of the points on each true face of the bracket's solids, away from edges and joints:
    ``"<solid>:<face>"`` with face ``+x``, ``-y``, ... for a box and ``side`` for a cylinder."""
    from ohs.sim.scene import Box, Cylinder, Rect

    D = truth.prim_sdf(P)
    A = np.abs(D)
    order = np.argsort(A, axis=1)
    own = order[:, 0]
    first = A[np.arange(len(P)), own]
    second = A[np.arange(len(P)), order[:, 1]] if D.shape[1] > 1 else np.full(len(P), np.inf)
    ok = (first < near_mm) & (second > edge_mm)
    out: dict[str, np.ndarray] = {}
    for k, p in enumerate(truth.prims):
        idx = np.flatnonzero(ok & (own == k))
        if not len(idx):
            continue
        pl = (P[idx] - p.t) @ p.R
        if isinstance(p, Box):
            q = np.abs(pl) / p.half
            ax = np.argmax(q, axis=1)
            for a in range(3):
                others = [b for b in range(3) if b != a]
                inside = np.all(np.abs(pl[:, others]) < p.half[others] - edge_mm, axis=1)
                for sgn, s in ((1, "+"), (-1, "-")):
                    sel = (ax == a) & (np.sign(pl[:, a]) == sgn) & inside
                    if sel.any():
                        out[f"{p.name}:{s}{'xyz'[a]}"] = idx[sel]
        elif isinstance(p, Cylinder):
            rad = np.hypot(pl[:, 0], pl[:, 1])
            side = (np.abs(rad - p.radius) < np.abs(np.abs(pl[:, 2]) - p.half_height)) & (np.abs(pl[:, 2]) < p.half_height - edge_mm)
            if side.any():
                out[f"{p.name}:side"] = idx[side]
            for sgn, s in ((1, "+"), (-1, "-")):  # the caps, away from the rim
                cap = ~side & (np.sign(pl[:, 2]) == sgn) & (rad < p.radius - edge_mm)
                if cap.any():
                    out[f"{p.name}:{s}z"] = idx[cap]
        elif isinstance(p, Rect):
            out[p.name] = idx
    return out


def measures(P: np.ndarray, truth: Truth, min_points: int = 200) -> dict[str, float | None]:
    """A few fitted measurements of the bracket (mm; true values in :data:`BRACKET_TRUE`): the
    shaft's diameter, and distances between faces: the heights above the table of the base's top,
    the tab's top, the bosses' tops, the upright's top and the shaft's axis, and the upright's
    thickness and length where both its faces were scanned. From the points in the simulator's world
    (after :func:`place`), fitted with ``ohs.measure`` (a plane or a cylinder per face). None where
    fewer than ``min_points`` were on a face. The bosses' diameters are left out: their side is 8 mm
    tall, and what is left of it away from the edges does not hold a cylinder fit (it moved 4 mm
    between two runs of the same calibration)."""
    from ohs.measure import fit_cylinder, fit_plane

    if "base" not in truth.names:
        return {}
    pts = _feature_points(P, truth)
    prim = {p.name: p for p in truth.prims}

    def plane(key):
        i = pts.get(key)
        return fit_plane(P[i]) if i is not None and len(i) >= min_points else None

    def gap(pa, pb):
        return None if pa is None or pb is None else abs(float(pa.normal @ (pb.point - pa.point)))

    out: dict[str, float | None] = {}
    shaft = None
    i = pts.get("shaft:side")
    if i is not None and len(i) >= min_points:
        try:
            shaft = fit_cylinder(P[i], axis0=prim["shaft"].R[:, 2])
        except ValueError:
            shaft = None
    out["shaft_d"] = None if shaft is None else float(shaft.diameter_mm)
    # the table next to the part, as one would measure a height from it (a far table bends with a depth error)
    i = pts.get("table")
    if i is not None:
        pts["table"] = i[np.hypot(P[i, 0], P[i, 1]) < TABLE_NEAR_MM]
    table = plane("table")
    out["base_height"] = gap(table, plane("base:+z"))
    out["tab_height"] = gap(table, plane("tab:+z"))
    tops = [gap(table, plane(f"{b}:+z")) for b in ("boss_left", "boss_right")]
    tops = [t for t in tops if t is not None]
    out["boss_height"] = float(np.mean(tops)) if tops else None
    out["upright_height"] = gap(table, plane("upright:+z"))
    out["shaft_height"] = None if table is None or shaft is None else abs(float(table.normal @ (shaft.point - table.point)))
    out["upright_thickness"] = gap(plane("upright:+y"), plane("upright:-y"))
    out["upright_length"] = gap(plane("upright:+x"), plane("upright:-x"))
    return out


TABLE_NEAR_MM = 80.0  # the table within this of the bracket's centre (its corners are 54 mm out)
BRACKET_TRUE = {"shaft_d": 16.0, "base_height": 8.0, "tab_height": 12.0, "boss_height": 16.0, "upright_height": 60.0,
                "shaft_height": 42.0, "upright_thickness": 8.0, "upright_length": 90.0}


def _surface_stats(P: np.ndarray, truth: Truth, gate: float = 3.0) -> dict:
    """Distances of points to the true surface: all within ``gate`` mm, and the part's alone."""
    if not len(P):
        return {"n": 0}
    D = truth.prim_sdf(P)
    d = D.min(axis=1)
    near = np.abs(d) < gate
    table = np.array([n == "table" for n in truth.names])
    part = near & (np.argmin(np.abs(D), axis=1) != int(np.flatnonzero(table)[0])) if table.any() else near
    def st(x):
        return {"n": int(len(x)), "median_abs": float(np.median(np.abs(x))) if len(x) else None,
                "p95_abs": float(np.percentile(np.abs(x), 95)) if len(x) else None,
                "mean": float(np.mean(x)) if len(x) else None, "median": float(np.median(x)) if len(x) else None}
    return {"all": st(d[near]), "part": st(d[part]), "far": int((~near).sum())}


def judge(rec: Path, out: Path, spec: dict, calib_used: ScannerCalibration, report: dict) -> dict:
    """How far the products of a processed recording are from the simulator's truth (module docstring)."""
    from ohs.io.recording import RecordingReader
    from ohs.mesh import anchor_to_points
    from ohs.track.export import load_mesh, load_ply
    from ohs.track.posegraph import read_trajectory

    reader = RecordingReader(rec)
    try:
        truth = Truth(scene_of(spec))
    except ValueError as e:
        return {"skipped": str(e)}
    gt = reader.ground_truth_poses()
    traj = [(i, T) for i, _, ok, T in read_trajectory(out / "trajectory.csv") if ok and i in gt]
    if not traj:
        return {"skipped": "nothing tracked"}
    M = G.make_se3(reader.calibration().stereo.R1 @ calib_used.stereo.R1.T)  # the used frame C in the true one
    i0, T_est0 = traj[0]
    T0 = gt[i0] @ M @ G.invert_se3(T_est0)
    V, F, _ = load_mesh(out / "mesh.ply")
    T = place(V, T0, truth)
    Vs = G.transform_points(T, V)
    res: dict = {"placed_moved_mm": float(np.linalg.norm(T[:3, 3] - T0[:3, 3])),
                 "placed_turned_deg": float(G.rotation_angle_deg(T[:3, :3] @ T0[:3, :3].T)),
                 "mesh": _surface_stats(Vs, truth), "measures": measures(Vs, truth)}
    # each product placed on its own (from the same start): its shape, not how it sits against the mesh
    # (that is what the health block's laser-against-mesh sign measures)
    L = load_ply(out / "laser_points.ply")["vertices"]
    res["laser"] = _surface_stats(G.transform_points(place(L, T0, truth), L), truth) if len(L) >= 100 else {"n": 0}
    res["laser"]["exported"] = int(len(L))
    if len(L) >= 100 and len(F):
        V2, _, arep = anchor_to_points(V, F, L)
        V2s = G.transform_points(place(V2, T0, truth), V2)
        res["refined"] = {"mesh": _surface_stats(V2s, truth), "measures": measures(V2s, truth),
                          "moved_share": arep.get("moved_share"), "moved_p95": _get(arep, "moved", "p95")}
    res["trajectory"] = {k: _get(report, "ground_truth", *k.split(".")) for k in ("trans_mm.max", "rot_deg.max")}
    return res


# --------------------------------------------------------------------------- one run
def compute_name() -> str:
    return "cpu" if os.environ.get("OHS_COMPUTE", "").strip().lower() == "cpu" else "gpu"


def run_case(work: Path, rec_name: str, fault: str, size: float, *, keep: bool = False) -> dict:
    """Process recording ``rec_name`` with ``fault`` of ``size`` and judge it; cached as JSON."""
    from ohs.app.process import ProcessParams, process_recording
    from ohs.io.recording import RecordingReader

    spec = RECORDINGS[rec_name]
    cache = work / "runs" / compute_name() / rec_name / f"{fault.replace(':', '-')}@{size:g}.json"
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8"))
    rec = work / "recs" / rec_name
    true = RecordingReader(rec).calibration()
    calib = perturb(true, fault, size)
    out = work / "tmp" / f"{rec_name}-{fault.replace(':', '-')}@{size:g}"
    shutil.rmtree(out, ignore_errors=True)
    t0 = time.perf_counter()
    rep = process_recording(rec, out, calib=calib, params=ProcessParams(export=("ply",)))
    secs = time.perf_counter() - t0
    t1 = time.perf_counter()
    row = {"rec": rec_name, "fault": fault, "size": size, "part": fault_part(fault), "compute": compute_name(),
           "process_s": round(secs, 1), "signs": signs(rep), "judge": judge(rec, out, spec, calib, rep),
           "device": rep["params"]["compute"]}
    row["judge_s"] = round(time.perf_counter() - t1, 1)
    if not keep:
        shutil.rmtree(out, ignore_errors=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(row, indent=1, default=float), encoding="utf-8")
    return row


def _faults(names: list[str] | None, lines: tuple[str, ...] = ("vertical", "horizontal")) -> list[tuple[str, float]]:
    out = []
    for kind, (_, _, sizes) in FAULTS.items():
        for f in ([f"{kind}:{ln}" for ln in lines] if kind.startswith("laser_") else [kind]):
            if names and f not in names and kind not in names:
                continue
            out += [(f, s) for s in sizes]
    return out


# --------------------------------------------------------------------------- the tables
def _load(work: Path, compute: str) -> dict[str, dict[tuple[str, float], dict]]:
    runs: dict[str, dict[tuple[str, float], dict]] = {}
    for p in sorted((work / "runs" / compute).glob("*/*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        runs.setdefault(r["rec"], {})[(r["fault"], float(r["size"]))] = r
    return runs


def costs(row: dict, base: dict) -> dict[str, float | None]:
    """How far a run's products moved from the true calibration's on the same recording (mm):
    ``mesh`` (the part's median distance and median offset to the true surface), ``measure`` (the
    largest change of a fitted measurement), ``laser`` (the laser cloud's median distance and median
    offset), ``refined`` (the mesh after Refine with lasers, both of the above); ``worst`` of them."""
    j, b = row.get("judge") or {}, base.get("judge") or {}

    def surf(x, y):
        a, c = _get(x, "part"), _get(y, "part")
        if not a or not c or a.get("n", 0) == 0 or c.get("n", 0) == 0:
            return None
        # medians only: the mean moved 0.08 mm on a 0.001 deg fault (a few far vertices at edges and joints)
        return max(abs(a["median_abs"] - c["median_abs"]), abs(a["median"] - c["median"]))

    def meas(x, y):
        d = [abs(x[k] - y[k]) for k in (x or {}) if x.get(k) is not None and (y or {}).get(k) is not None]
        return max(d) if d else None

    def laser(x, y):
        a, c = _get(x, "all"), _get(y, "all")
        if not a or not c or a.get("n", 0) == 0 or c.get("n", 0) == 0:
            return None
        return max(abs(a["median_abs"] - c["median_abs"]), abs(a["median"] - c["median"]))

    out = {"mesh": surf(j.get("mesh"), b.get("mesh")), "measure": meas(j.get("measures"), b.get("measures")),
           "laser": laser(j.get("laser"), b.get("laser")),
           "refined": max([v for v in (surf(_get(j, "refined", "mesh"), _get(b, "refined", "mesh")),
                                       meas(_get(j, "refined", "measures"), _get(b, "refined", "measures"))) if v is not None],
                          default=None)}
    out["worst"] = max([v for v in out.values() if v is not None], default=None)
    return out


#: the signs shown in the tables (the per-line ones for each laser)
SHOWN = ("row_error_px", "row_error_signed_px", "depth_gap_mm:vertical", "depth_gap_mm:horizontal",
         "depth_gap_abs_mm:vertical", "depth_gap_abs_mm:horizontal", "depth_rejected_share:horizontal",
         "mesh_gap_mm:vertical", "mesh_gap_mm:horizontal", "laser_mesh_p50_mm", "tracking_rmse_mm",
         "gyro_jump_deg", "gyro_jump_p95_deg")


def spread(runs: dict, names: list[str]) -> dict[str, dict]:
    """Each sign with the true calibration over the recordings ``names``: mean, min, max."""
    out = {}
    for s in SHOWN + ("depth_gap_spread_mm:vertical", "depth_gap_spread_mm:horizontal", "tracking_rmse_p95_mm",
                      "row_error_p90_px", "depth_rejected_share:vertical", "tracked_fraction"):
        v = [runs[n][("none", 0.0)]["signs"].get(s) for n in names if ("none", 0.0) in runs.get(n, {})]
        v = [x for x in v if x is not None]
        out[s] = {"n": len(v), "min": min(v) if v else None, "max": max(v) if v else None,
                  "values": dict(zip([n for n in names if ("none", 0.0) in runs.get(n, {})], v))}
    return out


COST_MM = 0.1  # the stop rule: a sign must move clearly before the products are this far off


def crossing(rr: dict, fault: str, cost_mm: float = COST_MM) -> dict | None:
    """The smallest size of ``fault`` that puts a product ``cost_mm`` off on one recording, and what
    each sign reads there: linear between the two sizes around the crossing (from the true
    calibration, size 0, below the first). A size counts only when every larger size measured costs
    as much too: near the floor (0.03 mm, :data:`FLOOR`) one run can cross by chance. None when no
    size measured reaches it."""
    base = rr.get(("none", 0.0))
    if base is None:
        return None
    pts = [(0.0, 0.0, base["signs"])] + [(s, costs(r, base)["worst"] or 0.0, r["signs"])
                                         for (f, s), r in sorted(rr.items(), key=lambda kv: kv[0][1]) if f == fault]
    for k, ((s0, c0, g0), (s1, c1, g1)) in enumerate(zip(pts, pts[1:])):
        if all(c >= cost_mm for _, c, _ in pts[k + 1:]):
            w = min(max((cost_mm - c0) / (c1 - c0), 0.0), 1.0) if c1 > c0 else 1.0
            sig = {k: (None if g0.get(k) is None or g1.get(k) is None else g0[k] + w * (g1[k] - g0[k])) for k in g1}
            return {"size": s0 + w * (s1 - s0), "between": [s0, s1], "signs": sig}
    return None


def _f(x: Any, nd: int = 3) -> str:
    return "–" if x is None else f"{x:.{nd}f}"


def tables(work: Path, compute: str = "gpu") -> str:
    runs = _load(work, compute)
    lines = [f"# Calibration health study ({compute} path)", ""]
    allrec = [n for n in RECORDINGS if ("none", 0.0) in runs.get(n, {})]
    sp = spread(runs, allrec)
    lines += ["## The true calibration, every recording", "",
              "| Sign | " + " | ".join(allrec) + " |", "|---|" + "---|" * len(allrec)]
    for s, v in sp.items():
        lines.append(f"| {s} | " + " | ".join(_f(v["values"].get(n), 4) for n in allrec) + " |")
    lines.append("")
    for rec, rr in runs.items():
        base = rr.get(("none", 0.0))
        if base is None:
            continue
        faults = sorted({f for f, _ in rr if f != "none"}, key=lambda f: list(FAULTS).index(_split(f)[0]))
        if not faults:
            continue
        lines += [f"## {rec}", "", "| Fault | Size | " + " | ".join(SHOWN) + " | mesh | measure | laser | refined |",
                  "|---|---|" + "---|" * (len(SHOWN) + 4)]
        lines.append("| true | 0 | " + " | ".join(_f(base["signs"].get(s)) for s in SHOWN) + " | 0 | 0 | 0 | 0 |")
        for f in faults:
            for (ff, size), r in sorted(((k, v) for k, v in rr.items() if k[0] == f), key=lambda kv: kv[0][1]):
                c = costs(r, base)
                lines.append(f"| {f} | {size:g} {FAULTS[_split(f)[0]][1]} | " + " | ".join(_f(r["signs"].get(s)) for s in SHOWN)
                             + " | " + " | ".join(_f(c[k]) for k in ("mesh", "measure", "laser", "refined")) + " |")
        lines.append("")
        lines += [f"### {rec}: the smallest fault that puts a product {COST_MM:g} mm off", "",
                  "| Fault | Size | " + " | ".join(SHOWN) + " |", "|---|---|" + "---|" * len(SHOWN)]
        for f in faults:
            x = crossing(rr, f)
            unit = FAULTS[_split(f)[0]][1]
            if x is None:
                top = max(s for ff, s in rr if ff == f)
                lines.append(f"| {f} | > {top:g} {unit} | " + " | ".join("" for _ in SHOWN) + " |")
            else:
                lines.append(f"| {f} | {x['size']:.3g} {unit} | " + " | ".join(_f(x["signs"].get(s)) for s in SHOWN) + " |")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- the command line
def log(msg: str) -> None:
    print(msg, flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("what", choices=("all", "render", "true", "sweep", "table"))
    ap.add_argument("work", type=Path)
    ap.add_argument("--rec", nargs="+", default=None, help="recordings (default: all for 'true', the brackets for 'sweep')")
    ap.add_argument("--fault", nargs="+", default=None, help="faults of the sweep (default: all)")
    ap.add_argument("--size", nargs="+", type=float, default=None, help="only these sizes")
    ap.add_argument("--cpu", action="store_true", help="the CPU path (OHS_COMPUTE=cpu)")
    a = ap.parse_args(argv)
    if a.cpu:
        os.environ["OHS_COMPUTE"] = "cpu"
    work = a.work
    t0 = time.perf_counter()
    if a.what in ("all", "render"):
        for name, spec in RECORDINGS.items():
            if a.rec and name not in a.rec:
                continue
            t = time.perf_counter()
            make_recording(work / "recs" / name, spec)
            log(f"{name}: ready ({time.perf_counter() - t:.0f} s)")
    if a.what in ("all", "true"):
        for name in a.rec or list(RECORDINGS):
            r = run_case(work, name, "none", 0.0)
            log(f"{name} true: {r['process_s']} s; {json.dumps({k: r['signs'].get(k) for k in SHOWN})}")
    if a.what in ("all", "sweep"):
        for name in a.rec or ["bracket-s0", "bracket-s1"]:
            run_case(work, name, "none", 0.0)
            for f, s in ([] if a.fault else list(FLOOR)) + _faults(a.fault):
                if a.size and s not in a.size:
                    continue
                r = run_case(work, name, f, s)
                log(f"{name} {f} {s:g}: {r['process_s']} s + {r['judge_s']} s; "
                    f"{json.dumps({k: r['signs'].get(k) for k in SHOWN})}")
    if a.what in ("all", "table", "sweep", "true"):
        text = tables(work, compute_name())
        (work / f"tables_{compute_name()}.md").write_text(text, encoding="utf-8")
        if a.what == "table":
            print(text)
    log(f"done in {time.perf_counter() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
