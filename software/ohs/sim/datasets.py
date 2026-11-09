"""Ready-made synthetic datasets for end-to-end tests: scan recordings and calibration sets.

* :func:`make_scan_recording` writes a full recording folder (ARCHITECTURE.md section 6):
  raw side-by-side frames, IMU at 416 Hz, trigger events (down before the first frame, up after
  the last), ``calibration.json`` (the TRUE calibration) and ``ground_truth/`` (``T_wc`` per frame
  in the Z-up sim world, rectified-left depth maps).
* :func:`make_stereo_calib_set` renders a ChArUco board (ambient light only) at varied poses for
  stereo calibration; :func:`make_laser_calib_set` renders the board with the line lasers on
  (dots off) for laser-plane calibration and returns the true board poses ``T_cb``.
* :func:`stereo_calib_board_poses` / :func:`laser_calib_board_poses` give the deterministic true
  board poses (frame C) behind those sets.

Timing model of a recording (all host seconds): frame ``i`` is exposed at IMU-host time
``t0 + i / fps``; its camera timestamp ``t_host`` adds the camera latency ``time_offset`` (the true
``ImuCalibration.time_offset``) plus a small grab jitter. The ground-truth pose is the pose at the
exposure instant; trajectories are parameterised in camera time (:mod:`ohs.sim.trajectory`).
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Sequence

import numpy as np

from ohs.geometry import invert_se3, make_se3, so3_exp
from ohs.io.recording import RecordingWriter
from ohs.sim.imu import ImuSimParams, synthesize_imu
from ohs.sim.nominal import nominal_calibration, nominal_emitters
from ohs.sim.render import Renderer, RenderParams, project_to_raw
from ohs.sim.scene import Scene, charuco_board, make_scene
from ohs.sim.trajectory import HandheldTrajectory, Trajectory
from ohs.types import Event, ScannerCalibration, StereoFrame

__all__ = [
    "DEFAULT_BOARD",
    "make_scan_recording",
    "make_stereo_calib_set",
    "make_laser_calib_set",
    "stereo_calib_board_poses",
    "laser_calib_board_poses",
    "board_corners",
]

log = logging.getLogger(__name__)

DEFAULT_BOARD: dict = {"squares_x": 11, "squares_y": 8, "square_mm": 20.0, "marker_mm": 15.0, "dictionary": "DICT_5X5_100"}


def _spec(spec: dict | None) -> dict:
    s = dict(DEFAULT_BOARD)
    if spec:
        s.update(spec)
    return s


def board_corners(spec: dict | None = None) -> np.ndarray:
    """Inner chessboard corners (N,3) in the board frame, in ChArUco id order (row-major)."""
    s = _spec(spec)
    sq = float(s["square_mm"])
    xs = np.arange(1, int(s["squares_x"])) * sq
    ys = np.arange(1, int(s["squares_y"])) * sq
    X, Y = np.meshgrid(xs, ys)
    return np.column_stack([X.ravel(), Y.ravel(), np.zeros(X.size)])


# --------------------------------------------------------------------------- scan recording
def make_scan_recording(
    out_dir: str | Path,
    scene: str | Scene = "sphere_on_plane",
    n_frames: int = 60,
    fps: float = 30.0,
    image_size: tuple[int, int] = (1280, 800),
    seed: int = 0,
    realistic: bool = False,
    dots: bool = True,
    lines: bool = True,
    *,
    params: RenderParams | None = None,
    trajectory: Trajectory | None = None,
    imu_params: ImuSimParams | None = None,
    overwrite: bool = False,
    t0: float = 100.0,
    depth_every: int = 1,
    frame_jitter_s: float = 2e-4,
    png_compression: int = 1,
) -> Path:
    """Simulate a hand-held scan and write it as a recording folder; returns its path.

    ``scene`` is a preset name or a :class:`~ohs.sim.scene.Scene`. ``dots``, ``lines`` and
    ``seed`` override the corresponding fields of ``params``. ``trajectory`` (camera-time
    ``pose(t)``) defaults to a :class:`HandheldTrajectory` around the scene target with the
    preset's suggested ranges. Sizes at 1280x800: ~1.3 MB PNG + 4.1 MB float32 depth per frame;
    ``depth_every`` = k keeps ground-truth depth for every k-th frame only (0 = none).
    """
    out = Path(out_dir)
    wall = time.perf_counter()
    calib = nominal_calibration(image_size, realistic=realistic)
    sc = make_scene(scene, seed)
    rp = replace(params or RenderParams(), dots_on=bool(dots), lines_on=bool(lines), seed=int(seed))
    to = float(calib.imu.time_offset)
    rng = np.random.default_rng(seed + 991)
    t_exp = float(t0) + np.arange(int(n_frames)) / float(fps)  # IMU-host clock
    t_cam = t_exp + to  # true camera timestamps (trajectory time)
    jitter = np.clip(rng.normal(0.0, frame_jitter_s, len(t_cam)), -3 * frame_jitter_s, 3 * frame_jitter_s) if frame_jitter_s > 0 else np.zeros(len(t_cam))
    t_host = t_cam + jitter
    duration = max(n_frames / float(fps), 1e-3)
    if trajectory is None:
        kw = dict(sc.meta.get("trajectory", {}))
        trajectory = HandheldTrajectory(target=sc.target, t0=float(t_cam[0]), duration=duration, seed=seed, baseline=calib.stereo.baseline, **kw)
    poses = trajectory.poses(t_cam)
    ip = imu_params or ImuSimParams(gyro_bias=calib.imu.gyro_bias.copy())
    imu = synthesize_imu(trajectory, float(t_exp[0]) - 0.5, float(t_exp[-1]) + 0.5, calib.imu.R_ci, to, ip, seed=seed)
    meta = {
        "scene": sc.name,
        "seed": int(seed),
        "fps": float(fps),
        "realistic": bool(realistic),
        "dots": bool(dots),
        "lines": bool(lines),
        "sim_world": "Z up, gravity (0, 0, -9.81) m/s^2, mm",
        "time_offset": to,
        "imu_rate_hz": ip.rate_hz,
        "render_params": asdict(rp),
        "trajectory": repr(trajectory),
    }
    with Renderer(calib, nominal_emitters(baseline=calib.stereo.baseline), sc, rp) as renderer, RecordingWriter(
        out, overwrite=overwrite, png_compression=png_compression, source="sim", **meta
    ) as w:
        w.write_calibration(calib)
        w.write_event(Event(float(t_exp[0]) - 0.2, "button", "down"))
        w.write_imu(imu)
        for i in range(len(t_cam)):
            left, right, depth = renderer.render(poses[i])
            w.write_frame(StereoFrame(i, float(t_host[i]), left, right))
            w.write_ground_truth_pose(i, float(t_host[i]), poses[i])
            if depth_every and i % depth_every == 0:
                w.write_ground_truth_depth(i, depth)
        w.write_event(Event(float(t_host[-1]) + 0.1, "button", "up"))
    log.info("wrote %d-frame %s recording to %s in %.1fs", n_frames, sc.name, out, time.perf_counter() - wall)
    return out


# --------------------------------------------------------------------------- board poses
def _board_pose(center_c: np.ndarray, tilt_axis: np.ndarray, tilt: float, roll: float, spec: dict) -> np.ndarray:
    """T_cb with the board centre at ``center_c``: fronto-parallel (board z = camera z), then
    tilted by ``tilt`` (rad) about an in-plane axis and rolled about its normal."""
    s = _spec(spec)
    W = float(s["squares_x"]) * float(s["square_mm"])
    H = float(s["squares_y"]) * float(s["square_mm"])
    ax = np.array([tilt_axis[0], tilt_axis[1], 0.0])
    ax /= np.linalg.norm(ax)
    R = so3_exp(ax * tilt) @ so3_exp(np.array([0.0, 0.0, roll]))
    t = np.asarray(center_c, dtype=np.float64) - R @ np.array([W / 2, H / 2, 0.0])
    return make_se3(R, t)


def _visible_fraction(T_cb: np.ndarray, calib: ScannerCalibration, spec: dict, margin: float = 12.0, z_max: float = 1e9) -> float:
    P = board_corners(spec) @ T_cb[:3, :3].T + T_cb[:3, 3]
    if np.any(P[:, 2] < 150.0) or np.any(P[:, 2] > z_max):
        return 0.0
    w, h = calib.stereo.image_size
    fr = []
    for side in ("left", "right"):
        uv = project_to_raw(P, calib.stereo, side)
        ok = np.isfinite(uv).all(1) & (uv[:, 0] > margin) & (uv[:, 0] < w - 1 - margin) & (uv[:, 1] > margin) & (uv[:, 1] < h - 1 - margin)
        fr.append(ok.mean())
    return float(min(fr))


def stereo_calib_board_poses(
    n_views: int = 20,
    calib: ScannerCalibration | None = None,
    spec: dict | None = None,
    seed: int = 0,
) -> list[np.ndarray]:
    """Board poses ``T_cb`` (frame C) for stereo calibration: the board centre is aimed at a
    cycle of image regions (centre, edges, corners), 260-420 mm away (no corner beyond 440 mm),
    tilted 5-40 deg; at least 75 % (central views 100 %) of the inner corners are inside both raw
    images. Meant for 1280x800: at half resolution the 15 mm markers get too small to decode."""
    calib = calib or nominal_calibration(realistic=True)
    s = _spec(spec)
    rng = np.random.default_rng(seed)
    K = calib.stereo.K_rect
    w, h = calib.stereo.image_size
    regions = [(0.5, 0.5), (0.25, 0.3), (0.75, 0.3), (0.25, 0.7), (0.75, 0.7), (0.5, 0.25), (0.5, 0.75), (0.2, 0.5), (0.8, 0.5),
               (0.15, 0.2), (0.85, 0.2), (0.15, 0.8), (0.85, 0.8)]
    poses = []
    for i in range(int(n_views)):
        fu, fv = regions[i % len(regions)]
        need = 1.0 if (fu, fv) == (0.5, 0.5) else 0.75
        for attempt in range(500):
            u = (fu + rng.uniform(-0.05, 0.05)) * w
            v = (fv + rng.uniform(-0.05, 0.05)) * h
            z = rng.uniform(260.0, 420.0)
            c = z * np.array([(u - K[0, 2]) / K[0, 0], (v - K[1, 2]) / K[1, 1], 1.0])
            T = _board_pose(c, rng.normal(size=2), math.radians(rng.uniform(5.0, 40.0)), math.radians(rng.uniform(-25, 25)), s)
            if T[:3, 2] @ c <= 0.3 * np.linalg.norm(c):  # must see the front side, not too oblique
                continue
            if _visible_fraction(T, calib, s, z_max=440.0) >= need:  # far corners stay decodable
                poses.append(T)
                break
        else:
            raise RuntimeError(f"could not place calibration view {i}")
    return poses


def _laser_hits_board(T_cb: np.ndarray, calib: ScannerCalibration, spec: dict, emitters: dict) -> int:
    """Smallest number (over both lasers) of fan samples that land on the board inside the image."""
    s = _spec(spec)
    W = float(s["squares_x"]) * float(s["square_mm"])
    H = float(s["squares_y"]) * float(s["square_mm"])
    n_b = T_cb[:3, 2]
    o_b = T_cb[:3, 3]
    w, h = calib.stereo.image_size
    K = calib.stereo.K_rect
    counts = []
    for name in ("vertical", "horizontal"):
        em = emitters[name]
        phi = np.radians(np.linspace(-25.0, 25.0, 51))
        dirs = np.cos(phi)[:, None] * em["direction"][None] + np.sin(phi)[:, None] * em["fan_axis"][None]
        den = dirs @ n_b
        with np.errstate(divide="ignore", invalid="ignore"):
            t = ((o_b - em["origin"]) @ n_b) / den
        X = em["origin"][None] + t[:, None] * dirs
        q = (X - o_b) @ T_cb[:3, :3]
        uv = X[:, :2] / X[:, 2:3] * [K[0, 0], K[1, 1]] + [K[0, 2], K[1, 2]]
        ok = (t > 0) & (q[:, 0] > 2) & (q[:, 0] < W - 2) & (q[:, 1] > 2) & (q[:, 1] < H - 2)
        ok &= (uv[:, 0] > 5) & (uv[:, 0] < w - 6) & (uv[:, 1] > 5) & (uv[:, 1] < h - 6)
        counts.append(int(ok.sum()))
    return min(counts)


def laser_calib_board_poses(
    n_views: int = 8,
    calib: ScannerCalibration | None = None,
    spec: dict | None = None,
    seed: int = 0,
) -> list[np.ndarray]:
    """Board poses ``T_cb`` for laser-plane calibration: board near the laser cross, at depths
    spread over 200-390 mm, tilted 0-35 deg, both laser lines crossing it (>= 15 of 51 fan
    samples each) and >= 90 % of the corners visible in both cameras."""
    calib = calib or nominal_calibration()
    s = _spec(spec)
    em = nominal_emitters(baseline=calib.stereo.baseline)
    rng = np.random.default_rng(seed + 17)
    depths = np.linspace(200.0, 390.0, max(int(n_views), 1))
    rng.shuffle(depths)
    poses = []
    for i in range(int(n_views)):
        for attempt in range(500):
            z = depths[i] + rng.uniform(-10, 10)
            c = np.array([30.0 + rng.uniform(-30, 30), rng.uniform(-25, 25), z])
            T = _board_pose(c, rng.normal(size=2), math.radians(rng.uniform(0.0, 35.0)), math.radians(rng.uniform(-30, 30)), s)
            if T[:3, 2] @ c <= 0.4 * np.linalg.norm(c):
                continue
            if _visible_fraction(T, calib, s) >= 0.9 and _laser_hits_board(T, calib, s, em) >= 15:
                poses.append(T)
                break
        else:
            raise RuntimeError(f"could not place laser calibration view {i}")
    return poses


# --------------------------------------------------------------------------- calibration sets
def _render_board_views(
    calib: ScannerCalibration, spec: dict, poses: Sequence[np.ndarray], params: RenderParams, rectified: bool = False
) -> list[tuple[np.ndarray, np.ndarray]]:
    scene = charuco_board(None, **spec)
    T_wb = np.asarray(scene.meta["board"]["T_wb"])
    pairs = []
    with Renderer(calib, nominal_emitters(baseline=calib.stereo.baseline), scene, params) as r:
        for T_cb in poses:
            left, right, _ = r.render(T_wb @ invert_se3(T_cb), rectified=rectified)
            pairs.append((left, right))
    return pairs


def make_stereo_calib_set(
    n_views: int = 20,
    image_size: tuple[int, int] = (1280, 800),
    realistic: bool = True,
    seed: int = 0,
    *,
    params: RenderParams | None = None,
    spec: dict | None = None,
) -> tuple[list[tuple[np.ndarray, np.ndarray]], ScannerCalibration, dict]:
    """``(pairs of raw (left, right) uint8 images, true calibration, board spec dict)``.

    Ambient light only (dots and lasers off). The spec dict has the ``CharucoSpec`` fields
    (squares_x, squares_y, square_mm, marker_mm, dictionary); true poses are
    ``stereo_calib_board_poses(n_views, calib, spec, seed)``.
    """
    calib = nominal_calibration(image_size, realistic=realistic)
    s = _spec(spec)
    poses = stereo_calib_board_poses(n_views, calib, s, seed)
    rp = params or RenderParams(ambient=170.0, seed=seed)
    rp = replace(rp, dots_on=False, lines_on=False, ambient_on=True)
    t0 = time.perf_counter()
    pairs = _render_board_views(calib, s, poses, rp)
    log.info("rendered %d stereo calibration views in %.1fs", len(pairs), time.perf_counter() - t0)
    return pairs, calib, s


def make_laser_calib_set(
    n_views: int = 8,
    image_size: tuple[int, int] = (1280, 800),
    realistic: bool = False,
    seed: int = 0,
    *,
    rectified: bool = False,
    dots: bool = False,
    lines: bool = True,
    ambient: float = 90.0,
    params: RenderParams | None = None,
    spec: dict | None = None,
) -> tuple[list[tuple[np.ndarray, np.ndarray]], ScannerCalibration, list[np.ndarray], dict]:
    """``(pairs of (left, right) images, true calibration, true board poses T_cb, spec)``.

    Board under ambient light with both line lasers on and the dot projector off. Images are raw
    (``rectified=False``; identical to rectified when ``realistic=False``) or rendered directly
    in the rectified geometry. Call again with ``lines=False`` (same seed) for laser-off images
    of the same views.
    """
    calib = nominal_calibration(image_size, realistic=realistic)
    s = _spec(spec)
    poses = laser_calib_board_poses(n_views, calib, s, seed)
    rp = params or RenderParams(seed=seed)
    rp = replace(rp, ambient=float(ambient), dots_on=bool(dots), lines_on=bool(lines), ambient_on=True)
    pairs = _render_board_views(calib, s, poses, rp, rectified=rectified)
    return pairs, calib, poses, s
