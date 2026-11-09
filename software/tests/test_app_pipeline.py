"""FramePipeline and process_recording contracts on a small simulated recording (area F2).

One 8-frame 640x400 sphere-on-plane recording (realistic lenses, dots and lasers on) is shared by
the module. Accuracy is covered by test_e2e_scan.py; here: outputs, defaults, graceful
degradation (no lasers / no IMU / tracking failure), reset/close, external poses and the files
and report written by process_recording.
"""
from __future__ import annotations

import csv
import json
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs import geometry as G  # noqa: E402
from ohs.app.pipeline import (  # noqa: E402
    FramePipeline,
    PipelineParams,
    default_fusion_params,
    tracking_depth,
)
from ohs.app.process import ProcessParams, process_recording, trajectory_errors  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from ohs.sim.datasets import make_scan_recording  # noqa: E402
from ohs.sim.nominal import nominal_calibration  # noqa: E402
from ohs.types import ScannerCalibration, StereoFrame  # noqa: E402
from ohs.vision.depth import DepthParams  # noqa: E402
from ohs.vision.laser import LaserLines, LaserParams  # noqa: E402

N = 8


@pytest.fixture(scope="module")
def rec(tmp_path_factory) -> Path:
    return make_scan_recording(tmp_path_factory.mktemp("pipe") / "rec", n_frames=N, image_size=(640, 400),
                               realistic=True, seed=4, depth_every=0)


@pytest.fixture(scope="module")
def reader(rec) -> RecordingReader:
    return RecordingReader(rec)


def feed(pipe: FramePipeline, reader: RecordingReader, positions=range(N), poses=None):
    pipe.add_imu(reader.imu_samples())
    return [pipe.process(reader.frame(i), None if poses is None else poses[i]) for i in positions]


# --------------------------------------------------------------------------- pipeline
def test_frame_results_and_outputs(reader):
    calib = reader.calibration()
    with FramePipeline(calib) as pipe:
        assert pipe.image_size == (640, 400) and pipe.dual_model and pipe.gyro_enabled and pipe.lasers_enabled
        res = feed(pipe, reader)
        for k, r in enumerate(res):
            assert r.index == k and r.t == pytest.approx(reader.times()[k])
            assert r.left_r.shape == r.right_r.shape == (400, 640) and r.left_r.dtype == np.uint8
            assert r.depth.dtype == np.float32 and (r.depth > 0).mean() > 0.5
            assert isinstance(r.lines, LaserLines) and isinstance(r.lines_right, LaserLines)
            assert set(r.laser_points) == {"vertical", "horizontal"}
            assert all(P.ndim == 2 and P.shape[1] == 3 and len(P) > 50 for P in r.laser_points.values())
            assert r.track.ok, r.track.info
            assert {"rectify", "depth", "laser", "track", "total", "track_filter"} <= set(r.timings_ms)
            assert r.timings_ms["total"] >= r.timings_ms["depth"] + r.timings_ms["track"] - 1e-6
            assert 0.2 < r.extra["track_valid_fraction"] < 0.9
        assert res[0].track.info.get("first_frame") and np.allclose(res[0].track.T_wc, np.eye(4))
        assert all(r.track.info["gyro_prior"] for r in res[1:])  # R_prior whenever the IMU covers the interval
        traj = pipe.trajectory()
        assert len(traj) == N and all(ok for _, _, ok in traj) and pipe.n_tracked == N == pipe.n_frames
        # world = first frame; poses agree with the truth (aligned at frame 0)
        gt = reader.ground_truth_poses()
        err = trajectory_errors(gt, [(r.index, r.track.T_wc, r.track.ok) for r in res])
        assert err["trans_mm"]["max"] < 1.5 and err["rot_deg"]["max"] < 0.5
        V, F, Nn = pipe.mesh()
        assert len(F) > 10000 and V.shape == Nn.shape and F.max() < len(V)
        P, Np = pipe.points()
        assert len(P) > 1000 and P.shape == Np.shape
        cloud = pipe.laser_cloud()
        assert set(cloud.names) == {"vertical", "horizontal"} and len(cloud) == sum(len(p) for r in res for p in r.laser_points.values())


def test_defaults_follow_resolution():
    with pytest.raises(ValueError):
        PipelineParams(mode="slam")
    with pytest.raises(ValueError):
        PipelineParams(scale=0.0)
    calib = nominal_calibration()  # 1280x800
    with FramePipeline(calib, PipelineParams(scale=0.5)) as live:
        assert live.image_size == (640, 400)
        assert live.fusion_params.voxel_mm == 2.0 and live.track_fusion_params.voxel_mm == 2.0
        assert live.laser_params == LaserParams().scaled(0.5) and live.depth_params == DepthParams()
        assert live.track_filter_px == pytest.approx(4.0)
    with FramePipeline(calib, PipelineParams(scale=1.0, use_lasers=False, use_imu=False)) as full:
        assert full.fusion_params.voxel_mm == 1.0 and full.track_fusion_params.voxel_mm == 2.0
        assert full.depth_params.block_size == 7 and full.depth_params.speckle_window_size == 200
        assert full.laser_params == LaserParams() and full.track_filter_px == 6.0
        assert not full.lasers_enabled and not full.gyro_enabled
    assert default_fusion_params(1.0).gyro_sigma_deg == pytest.approx(0.02)
    with FramePipeline(calib, PipelineParams(mode="markers", integrate_depth=False)) as m:
        assert m.markerless is None and m.depth_estimator is None and m.tracker is None and m.markers is not None


def test_degrades_gracefully(reader):
    calib = reader.calibration()
    bare = ScannerCalibration(calib.stereo, {}, None)  # no laser planes, no IMU calibration
    with FramePipeline(bare) as pipe:
        res = feed(pipe, reader, range(4))
        assert all(r.track.ok for r in res)
        assert all(r.lines is None and r.laser_points == {} and "laser" not in r.timings_ms for r in res)
        assert len(pipe.laser_cloud()) == 0 and not any(r.track.info.get("gyro_prior") for r in res)
        # tracking failure (lens cap on): no fusion, no laser points, last good pose kept
        n_int = pipe.markerless.n_integrated
        f = reader.frame(4)
        dark = StereoFrame(f.index, f.t_host, np.zeros_like(f.left), np.zeros_like(f.right))
        bad = pipe.process(dark)
        assert not bad.track.ok and bad.track.info["reason"] in ("no_depth", "bad_depth")
        assert np.allclose(bad.track.T_wc, res[-1].track.T_wc)
        assert pipe.markerless.n_integrated == n_int and pipe.n_tracked == 4 and pipe.trajectory()[-1][2] is False
        # ... and it recovers on the next good frame
        assert pipe.process(reader.frame(5)).track.ok
        with pytest.raises(ValueError):
            pipe.process(StereoFrame(0, 0.0, np.zeros((10, 10), np.uint8), np.zeros((10, 10), np.uint8)))
    with FramePipeline(calib) as pipe:  # failed frame with lasers on: its laser points are not fused
        feed(pipe, reader, range(2))
        n = len(pipe.laser_cloud())
        f = reader.frame(2)
        r = pipe.process(StereoFrame(f.index, f.t_host, f.left, np.zeros_like(f.right)))  # right camera dark
        assert not r.track.ok and len(pipe.laser_cloud()) == n


def test_reset_close_and_external_poses(reader):
    calib = reader.calibration()
    gt = reader.ground_truth_poses()
    T0_inv = G.invert_se3(gt[0])
    poses = [T0_inv @ gt[i] for i in range(N)]
    pipe = FramePipeline(calib)
    res = feed(pipe, reader, range(3), poses)
    assert all(r.track.ok and r.track.info["method"] == "external" for r in res)
    assert np.allclose(pipe.trajectory()[2][1], poses[2])
    # tracking continues from the external poses
    r3 = pipe.process(reader.frame(3))
    assert r3.track.ok and G.pose_error(poses[3], r3.track.T_wc)[1] < 1.0
    pipe.reset()
    assert pipe.trajectory() == [] and len(pipe.mesh()[0]) == 0 and len(pipe.laser_cloud()) == 0
    r = pipe.process(reader.frame(5))
    assert r.track.ok and np.allclose(r.track.T_wc, np.eye(4))  # the next frame defines a new world
    pipe.close()
    with pytest.raises(RuntimeError):
        pipe.process(reader.frame(6))


def test_marker_mode_fails_cleanly_at_half_resolution(reader):
    """640x400 with the dots on: tags are too small; the pipeline reports ok=False and fuses nothing."""
    with FramePipeline(reader.calibration(), PipelineParams(mode="markers")) as pipe:
        res = feed(pipe, reader, range(4))
        for r in res:
            assert r.track.ok or r.track.info.get("reason"), r.track.info
            assert r.depth is not None
        n_ok = sum(r.track.ok for r in res)
        assert pipe.markerless.n_integrated == n_ok and pipe.n_tracked == n_ok
        assert len(pipe.laser_cloud()) == sum(len(p) for r in res if r.track.ok for p in r.laser_points.values())


def test_tracking_depth_filter():
    h, w = 200, 320
    vv, uu = np.mgrid[0:h, 0:w]
    plane = (300.0 + 0.2 * uu + 0.1 * vv).astype(np.float32)
    rng = np.random.default_rng(0)
    pattern = rng.normal(0, 0.6, (h, w)).astype(np.float32)
    d = plane + pattern
    d[:, 200:] -= 40.0  # an occlusion step
    out = tracking_depth(d, 3.0)
    ok = out > 0
    assert ok.mean() > 0.5
    truth = plane.copy()
    truth[:, 200:] -= 40.0
    assert np.std((out - truth)[ok]) < 0.25 * np.std(pattern)  # dot-scale pattern suppressed
    assert not ok[:, 194:206].any()  # no smoothing across the step: eroded around it
    assert not ok[:5].any() and not ok[:, :5].any()  # image border counts as invalid
    assert np.array_equal(tracking_depth(d, 0.0), d)


# --------------------------------------------------------------------------- process_recording
def test_process_recording_writes_everything(rec, tmp_path):
    calls = []
    out = tmp_path / "out"
    rep = process_recording(rec, out, progress=lambda k, n, r: calls.append((k, n, r.index)))
    assert calls == [(k, N, k) for k in range(N)]
    for f in ("mesh.ply", "mesh.stl", "laser_points.ply", "trajectory.csv", "report.json"):
        assert (out / f).exists() and (out / f).stat().st_size > 0, f
    assert json.loads((out / "report.json").read_text(encoding="utf-8")) == rep
    assert rep["calibration"] == "recording" and rep["mode"] == "markerless" and rep["image_size"] == [640, 400]
    assert rep["frames"]["processed"] == N and rep["frames"]["tracked_fraction"] == 1.0 and rep["failures"] == {}
    assert rep["imu"]["gyro_prior"] and rep["imu"]["frames_with_prior"] == N - 1
    assert rep["mesh"]["n_faces"] > 10000 and rep["mesh"]["method"] == "tsdf" and rep["mesh"]["voxel_mm"] == 2.0
    assert rep["lasers"]["n_points_exported"] > 1000 and set(rep["lasers"]["planes"]) == {"vertical", "horizontal"}
    assert rep["ground_truth"]["n"] == N and rep["ground_truth"]["trans_mm"]["max"] < 1.5
    assert {"depth", "track", "laser", "total"} <= set(rep["timings_ms"]) and rep["timings_ms"]["total"]["n"] == N
    assert len(rep["per_frame"]) == N and rep["params"]["fusion"]["voxel_mm"] == 2.0
    with open(out / "trajectory.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == N and rows[0]["ok"] == "1" and float(rows[0]["T00"]) == pytest.approx(1.0)


def test_process_recording_options(rec, tmp_path):
    rep = process_recording(rec, tmp_path / "a", params=ProcessParams(frame_step=2, start=1, stop=7, export=("obj",)))
    assert rep["frames"]["processed"] == 3 and [r["index"] for r in rep["per_frame"]] == [1, 3, 5]
    assert (tmp_path / "a" / "mesh.obj").exists() and not (tmp_path / "a" / "mesh.ply").exists()
    rep = process_recording(rec, tmp_path / "b", params=ProcessParams(stop=4, poisson=True, poisson_depth=8,
                                                                     pipeline=PipelineParams(use_lasers=False)))
    assert rep["mesh"]["method"] == "poisson" and rep["mesh"]["n_faces"] > 1000
    assert rep["lasers"]["n_points_exported"] == 0 and (tmp_path / "b" / "laser_points.ply").exists()
    with pytest.raises(ValueError):
        process_recording(rec, tmp_path / "c", params=ProcessParams(export=("fbx",)))
    assert not (tmp_path / "c").exists()  # rejected before any work


def test_process_recording_calibration_errors(rec, tmp_path):
    bare = tmp_path / "bare"
    shutil.copytree(rec, bare)
    (bare / "calibration.json").unlink()
    with pytest.raises(ValueError, match="no calibration"):
        process_recording(bare, tmp_path / "o1")
    with pytest.raises(ValueError, match="1280x800"):
        process_recording(bare, tmp_path / "o2", calib=nominal_calibration((1280, 800)))
    rep = process_recording(bare, tmp_path / "o3", calib=RecordingReader(rec).calibration(), params=ProcessParams(stop=2))
    assert rep["calibration"] == "argument" and rep["frames"]["processed"] == 2
