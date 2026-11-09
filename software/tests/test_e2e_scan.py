"""End-to-end scans of simulated recordings through ``process_recording`` (area F2).

Simulated recordings (``make_scan_recording``, realistic lens model, dots and both lasers on) are
processed exactly as ``ohs process`` does, and the outputs are compared with the simulator's truth:

* trajectory: ``pose_error(inv(T_gt[f]) @ T_gt[i], T_est[i])`` after aligning at the first
  tracked frame ``f`` (the world frame), from ``report["ground_truth"]``;
* mesh / laser accuracy: ``mesh.ply`` vertices and ``laser_points.ply`` mapped into the sim world
  with that same alignment, distance to the analytic surfaces (sphere + table plane, or the car
  panel height field + floor), median and 95th percentile. Drift is therefore included.

Fast tests run at 640x400 (half resolution: what the live preview sees) with fewer frames, and
marker mode at 1280x800 on a few frames (tags are too small at half resolution with the dots
on). ``@pytest.mark.slow`` tests are the full runs behind VALIDATION.md; they print ``METRICS``
lines (run with ``-s``).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs import geometry as G  # noqa: E402
from ohs.app.pipeline import PipelineParams  # noqa: E402
from ohs.app.process import ProcessParams, process_recording  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from ohs.sim.datasets import make_scan_recording  # noqa: E402
from ohs.sim.scene import make_scene  # noqa: E402
from ohs.track.export import load_mesh, load_ply  # noqa: E402


# --------------------------------------------------------------------------- helpers (also used by test_e2e_calibration)
def metrics(name: str, **values: object) -> None:
    print("METRICS " + json.dumps({"test": name, **values}, default=float))


def scan_recording(path: Path, scene: str, n_frames: int, size: tuple[int, int], seed: int = 0, realistic: bool = True) -> Path:
    if not (Path(path) / "frames.csv").exists():
        make_scan_recording(path, scene=scene, n_frames=n_frames, image_size=size, realistic=realistic, depth_every=0, seed=seed)
    return Path(path)


def surface_distance(scene_name: str, P: np.ndarray, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Distance (mm) of sim-world points to the scene's analytic surfaces and the label of the nearest."""
    P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
    sc = make_scene(scene_name, seed)
    if scene_name == "sphere_on_plane":
        c, r = np.asarray(sc.meta["sphere_center"]), float(sc.meta["sphere_radius"])
        d_obj = np.abs(np.linalg.norm(P - c, axis=1) - r)
        d_floor = np.abs(P[:, 2])
    elif scene_name == "car_panel":
        panel = sc.primitives[sc.object_id("panel")]
        pl = (P - panel.t) @ panel.R
        h, hx, hy = panel.height(pl[:, 0], pl[:, 1])
        d_obj = np.abs(pl[:, 2] - h) / np.sqrt(1.0 + hx * hx + hy * hy)
        x0, x1, y0, y1 = panel.bounds
        inside = (pl[:, 0] >= x0) & (pl[:, 0] <= x1) & (pl[:, 1] >= y0) & (pl[:, 1] <= y1)
        for hx_, hy_, hr in panel.holes:
            inside &= (pl[:, 0] - hx_) ** 2 + (pl[:, 1] - hy_) ** 2 > hr * hr
        d_obj = np.where(inside, d_obj, np.inf)
        d_floor = np.abs(P[:, 2])
    else:
        raise ValueError(f"no analytic surface for {scene_name}")
    d = np.minimum(d_obj, d_floor)
    return d, np.where(d_obj <= d_floor, 1, 0)


def _read_trajectory(path: Path) -> list[tuple[int, bool, np.ndarray]]:
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return [(int(r["index"]), bool(int(r["ok"])), np.array([float(r[f"T{a}{b}"]) for a in range(4) for b in range(4)]).reshape(4, 4))
            for r in rows]


def evaluate_scan(rec: Path, report: dict, out_dir: Path, seed: int = 0, calib=None) -> dict:
    """Trajectory, mesh and laser accuracy of a processed simulated recording (see the module docstring).

    ``calib``: the calibration the recording was processed with, if not its true one. The
    estimated frame C then differs from the true rectified frame by the rectification rotation
    ``M = R1_true @ R1_est^T`` (same optical centre); that frame definition is compensated (it is
    not a shape error), nothing is fitted to the data.
    """
    reader = RecordingReader(rec)
    scene_name = reader.meta["scene"]
    gt = reader.ground_truth_poses()
    traj = _read_trajectory(Path(out_dir) / "trajectory.csv")
    M = np.eye(4)
    if calib is not None:
        M = G.make_se3(reader.calibration().stereo.R1 @ calib.stereo.R1.T)
    first = next((i, T) for i, ok, T in traj if ok and i in gt)
    A = gt[first[0]] @ M @ G.invert_se3(first[1])  # estimated world -> sim world
    errs = [G.pose_error(gt[i] @ M, A @ T) for i, ok, T in traj if ok and i in gt]
    rot = np.array([e[0] for e in errs])
    trans = np.array([e[1] for e in errs])
    m: dict = {
        "scene": scene_name, "frames": report["frames"]["processed"], "tracked_fraction": report["frames"]["tracked_fraction"],
        "traj_median_trans_mm": float(np.median(trans)), "traj_max_trans_mm": float(trans.max()),
        "traj_final_trans_mm": float(trans[-1]), "traj_median_rot_deg": float(np.median(rot)),
        "traj_max_rot_deg": float(rot.max()), "traj_final_rot_deg": float(rot[-1]),
        "frame_correction_deg": G.rotation_angle_deg(M[:3, :3]), "failures": report["failures"],
    }
    V, F, _ = load_mesh(Path(out_dir) / "mesh.ply")
    if len(V):
        d, lab = surface_distance(scene_name, G.transform_points(A, V), seed)
        m.update(mesh_n=int(len(V)), mesh_median_mm=float(np.median(d)), mesh_p95_mm=float(np.percentile(d, 95)),
                 mesh_object_median_mm=float(np.median(d[lab == 1])) if (lab == 1).any() else None,
                 mesh_floor_median_mm=float(np.median(d[lab == 0])) if (lab == 0).any() else None)
    L = load_ply(Path(out_dir) / "laser_points.ply")["vertices"]
    if len(L):
        d, lab = surface_distance(scene_name, G.transform_points(A, L), seed)
        m.update(laser_n=int(len(L)), laser_median_mm=float(np.median(d)), laser_p95_mm=float(np.percentile(d, 95)),
                 laser_object_median_mm=float(np.median(d[lab == 1])) if (lab == 1).any() else None)
    m["timings_median_ms"] = {k: v["median"] for k, v in report["timings_ms"].items() if "median" in v}
    return m


def _process(rec: Path, out: Path, **pipeline) -> tuple[dict, dict]:
    rep = process_recording(rec, out, params=ProcessParams(pipeline=PipelineParams(**pipeline)))
    return rep, evaluate_scan(rec, rep, out)


# --------------------------------------------------------------------------- fast tests
@pytest.fixture(scope="module")
def sphere_half(tmp_path_factory):
    return scan_recording(tmp_path_factory.mktemp("sh") / "rec", "sphere_on_plane", 24, (640, 400))


def test_markerless_sphere_half_resolution(sphere_half, tmp_path):
    rep, m = _process(sphere_half, tmp_path / "out")
    metrics("scan_sphere_half", **m)
    # measured (24 frames): ~0.4 mm / 0.1 deg drift, mesh median ~0.3 mm at 2 mm voxels, lasers ~0.05 mm
    assert m["tracked_fraction"] == 1.0
    assert m["traj_max_trans_mm"] < 1.5 and m["traj_max_rot_deg"] < 0.5
    assert m["mesh_median_mm"] < 0.6 and m["mesh_p95_mm"] < 2.5
    assert m["laser_median_mm"] < 0.25 and m["laser_n"] > 2000
    assert rep["imu"]["frames_with_prior"] == 23


def test_markerless_without_imu_still_tracks_but_is_flagged(sphere_half, tmp_path):
    """No gyro: the sphere-on-plane yaw is unobservable, the tracker says so (degenerate) and drifts."""
    rep, m = _process(sphere_half, tmp_path / "out", use_imu=False)
    metrics("scan_sphere_half_no_imu", **m)
    assert rep["imu"]["gyro_prior"] is False and rep["imu"]["frames_with_prior"] == 0
    assert m["tracked_fraction"] > 0.9
    assert any(r.get("degenerate") for r in rep["per_frame"][1:])


def test_marker_mode_full_resolution_short(tmp_path):
    rec = scan_recording(tmp_path / "rec", "sphere_on_plane", 6, (1280, 800), seed=1)
    rep, m = _process(rec, tmp_path / "out", mode="markers")
    metrics("scan_markers_short", **m, map_size=rep["markers"]["map_size"])
    assert m["tracked_fraction"] == 1.0 and rep["markers"]["map_size"] >= 3
    assert m["traj_max_trans_mm"] < 1.5 and m["traj_max_rot_deg"] < 0.3
    assert m["mesh_median_mm"] < 0.5 and m["laser_median_mm"] < 0.3
    assert rep["markers"]["refine"]["rms_mm"] < 0.5
    assert (tmp_path / "out" / "marker_map.json").exists()


# --------------------------------------------------------------------------- full runs (VALIDATION.md)
@pytest.fixture(scope="module")
def sphere_full(tmp_path_factory):
    return scan_recording(tmp_path_factory.mktemp("sf") / "rec", "sphere_on_plane", 36, (1280, 800))


@pytest.mark.slow
def test_full_markerless_sphere(sphere_full, tmp_path):
    rep, m = _process(sphere_full, tmp_path / "out")
    metrics("scan_sphere_full", **m)
    assert m["tracked_fraction"] > 0.9
    assert m["traj_max_trans_mm"] < 3.0 and m["traj_max_rot_deg"] < 1.0
    assert m["mesh_median_mm"] < 0.3 and m["laser_median_mm"] < 0.2


@pytest.mark.slow
def test_full_marker_mode_sphere(sphere_full, tmp_path):
    """Marker mode on the same recording. Only 2-3 of the six 20 mm tags are seen in stereo in most
    frames, under the dot pattern and laser lines (corner reprojection 0.3-0.55 px rms), so the
    poses are less accurate than markerless+IMU here (measured: median ~0.9 mm / 0.2 deg, worst
    3.8 mm / 0.9 deg on a single-tag PnP frame). Thresholds guard that level; see VALIDATION.md."""
    rep, m = _process(sphere_full, tmp_path / "out", mode="markers")
    metrics("scan_sphere_markers_full", **m, map_size=rep["markers"]["map_size"], refine=rep["markers"]["refine"])
    assert m["tracked_fraction"] > 0.9
    assert m["traj_median_trans_mm"] < 1.5 and m["traj_median_rot_deg"] < 0.4
    assert m["traj_max_trans_mm"] < 5.0 and m["traj_max_rot_deg"] < 1.5
    assert m["mesh_median_mm"] < 1.0 and m["laser_median_mm"] < 0.3


@pytest.mark.slow
def test_full_markerless_car_panel(tmp_path):
    rec = scan_recording(tmp_path / "rec", "car_panel", 36, (1280, 800))
    rep, m = _process(rec, tmp_path / "out")
    metrics("scan_car_panel_full", **m)
    assert m["tracked_fraction"] > 0.9
    assert m["traj_max_trans_mm"] < 3.0 and m["traj_max_rot_deg"] < 1.0
    # single-model tracking on the raw depth (track_filter_px=0) for comparison (the pre-fix design)
    rep0, m0 = _process(rec, tmp_path / "out_raw", track_filter_px=0.0)
    metrics("scan_car_panel_full_raw_depth_tracking", **m0)
