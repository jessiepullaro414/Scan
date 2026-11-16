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
from recordings import scan_recording  # noqa: E402
from ohs.sim.nominal import nominal_calibration  # noqa: E402
from ohs.track.markerless import FusionParams  # noqa: E402
from ohs.types import ScannerCalibration, StereoFrame  # noqa: E402
from ohs.vision.depth import DepthParams  # noqa: E402
from ohs.vision.laser import LaserLines, LaserParams, laser_points  # noqa: E402

N = 8


@pytest.fixture(scope="module")
def rec(tmp_path_factory) -> Path:
    return scan_recording(tmp_path_factory.mktemp("pipe") / "rec", n_frames=N, image_size=(640, 400),
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


def test_laser_points_equal_the_inline_computation(reader):
    """The frame's laser points, the cloud and the rejected counts are what ``laser_points`` gives on
    the frame's own lines, depth and sigma, whatever thread the pipeline computes them in."""
    with FramePipeline(reader.calibration()) as pipe:
        res = feed(pipe, reader)
        rejected: dict[str, int] = {}
        world = {}
        for r in res:
            want = laser_points(r.left_r, r.right_r, pipe.K, pipe.baseline, pipe.planes, pipe.laser_params,
                                lines_left=r.lines, lines_right=r.lines_right, depth=r.depth, depth_sigma=r.sigma,
                                rejected=rejected)
            assert list(r.laser_points) == list(want)
            for name, P in want.items():
                assert r.laser_points[name].dtype == P.dtype and r.laser_points[name].tobytes() == P.tobytes()
                world.setdefault(name, []).append(G.transform_points(r.track.T_wc, P[np.all(np.isfinite(P), axis=1)]))
            assert r.timings_ms["laser"] == r.timings_ms["laser_extract"] + r.timings_ms["laser_triangulate"]
        assert set(rejected) == {"vertical", "horizontal"} and pipe.laser_rejected == rejected
        assert list(pipe.laser_rejected) == list(rejected)
        cloud = pipe.laser_cloud()
        for name, chunks in world.items():
            assert cloud.raw_points([name]).tobytes() == np.concatenate(chunks).tobytes()


def test_tracking_does_not_wait_for_the_laser_lines(reader, monkeypatch):
    """The lines are needed only for the laser points, so the frame is tracked and fused while they
    are still being extracted (with four lasers the extraction outlasts the depth). The frame's
    lines and points are the same however long the extraction takes."""
    import time

    import ohs.app.pipeline as pl

    with FramePipeline(reader.calibration()) as pipe:
        want = feed(pipe, reader, positions=range(2))
    real = pl.extract_laser_lines
    done: list[float] = []
    fused: list[float] = []

    def slow(img, params):
        time.sleep(1.0)
        out = real(img, params)
        done.append(time.perf_counter())
        return out

    with FramePipeline(reader.calibration()) as pipe:
        real_fuse = pipe._fuse

        def fuse(*args, **kwargs):
            fused.append(time.perf_counter())
            return real_fuse(*args, **kwargs)

        monkeypatch.setattr(pipe, "_fuse", fuse)
        monkeypatch.setattr(pl, "extract_laser_lines", slow)
        got = feed(pipe, reader, positions=range(2))
    assert len(fused) == 2 and len(done) == 4
    # the second frame (the first also builds the GPU programs): fused before either image's lines were there
    assert fused[1] < min(done[2:]), f"fusion started {fused[1] - min(done[2:]):.2f} s after the lines were there"
    for r, w in zip(got, want):
        assert r.track.ok and isinstance(r.lines, LaserLines) and isinstance(r.lines_right, LaserLines)
        for f in ("vertical", "horizontal", "vertical_segment", "horizontal_segment"):
            assert getattr(r.lines, f).tobytes() == getattr(w.lines, f).tobytes()
            assert getattr(r.lines_right, f).tobytes() == getattr(w.lines_right, f).tobytes()
        assert list(r.laser_points) == list(w.laser_points)
        for name, P in w.laser_points.items():
            assert r.laser_points[name].tobytes() == P.tobytes()
        # the frame waited for the lines at its end, and says so
        assert r.timings_ms["laser_extract"] > 300.0
        assert r.timings_ms["laser"] == r.timings_ms["laser_extract"] + r.timings_ms["laser_triangulate"]
        assert r.timings_ms["laser_extract_cpu"] > 1900.0


def test_a_clip_plane_leaves_out_what_is_behind_it(reader):
    """``clip_plane``: depth and laser points outside the half-space are not measured; a plane that
    cuts nothing off changes nothing."""
    T = np.eye(4)
    frame = reader.frame(0)
    with FramePipeline(reader.calibration()) as pipe:
        plain = pipe.process(frame, pose=T)
    with FramePipeline(reader.calibration()) as pipe:
        same = pipe.process(frame, pose=T, clip_plane=(np.array([0.0, 0.0, 1.0]), 0.0))  # everything in front of the camera
    assert same.depth.tobytes() == plain.depth.tobytes()
    assert all(same.laser_points[k].tobytes() == plain.laser_points[k].tobytes() for k in plain.laser_points)
    z_cut = float(np.median(plain.depth[plain.depth > 0]))
    with FramePipeline(reader.calibration()) as pipe:
        near = pipe.process(frame, pose=T, clip_plane=(np.array([0.0, 0.0, -1.0]), z_cut))  # only what is nearer than z_cut
        V, F, _ = pipe.mesh()
    kept = near.depth > 0
    assert 0.2 < kept.sum() / (plain.depth > 0).sum() < 0.8
    assert near.depth[kept].max() < z_cut and np.array_equal(near.depth[kept], plain.depth[kept])
    assert sum(len(P) for P in near.laser_points.values()) < sum(len(P) for P in plain.laser_points.values())
    assert all(np.all(P[:, 2] < z_cut) for P in near.laser_points.values())
    assert len(V) > 100 and V[:, 2].max() < z_cut + 5.0  # the model holds only what was kept


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


def test_a_gpu_filter_failure_is_reported_on_the_frame_that_switches(reader):
    class BrokenFilter:  # the GPU tracking filter, failing as on a lost device
        dev = None

        def start(self, depth):
            raise RuntimeError("simulated device loss")

        finish = __call__ = start

    with FramePipeline(reader.calibration()) as pipe:
        pipe._track_filter_gpu = BrokenFilter()
        res = feed(pipe, reader, range(3))
        assert pipe.gpu_fallbacks == {"track_filter": "simulated device loss"}
    assert res[0].extra["gpu_fallback"] == {"track_filter": "simulated device loss"}
    assert all("gpu_fallback" not in r.extra for r in res[1:])
    assert all(r.track.ok for r in res)


def test_a_gpu_matcher_failure_is_reported_on_the_frame_that_switches(reader):
    with FramePipeline(reader.calibration()) as pipe:
        est = pipe.depth_estimator
        if est.backend != "gpu":
            pytest.skip(f"the depth runs on the CPU here: {est.backend_note}")

        def broken(*a, **k):
            raise RuntimeError("simulated device loss")

        for name in ("depth", "depth_sigma", "disparity", "disparity_sigma"):
            setattr(est._gpu, name, broken)
        res = feed(pipe, reader, range(3))
        assert est.backend == "cpu" and pipe.gpu_fallbacks == {"depth": "simulated device loss"}
    assert res[0].extra["gpu_fallback"] == {"depth": "simulated device loss"}
    assert all("gpu_fallback" not in r.extra for r in res[1:])
    assert all(r.track.ok for r in res)


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


def test_tracking_filter_at_the_tracking_resolution():
    """work_size: smoothing and erosion at half resolution, edge test on the input pixels, result
    upsampled back; same map as the full-resolution filter (what the pipeline feeds the tracker)."""
    h, w = 400, 640
    vv, uu = np.mgrid[0:h, 0:w]
    plane = (300.0 + 0.2 * uu + 0.1 * vv).astype(np.float32)
    rng = np.random.default_rng(1)
    d = plane + rng.normal(0, 0.6, (h, w)).astype(np.float32)
    d[:, 400:] -= 40.0  # an occlusion step
    d[150:170, 100:130] = 0.0  # a hole
    full = tracking_depth(d, 6.0)
    half = tracking_depth(d, 6.0, work_size=(w // 2, h // 2))
    assert half.shape == d.shape and half.dtype == np.float32
    a, b = full > 0, half > 0
    assert (a ^ b).mean() < 0.02 and b.mean() > 0.4  # same pixels, to the mask's discretisation
    assert np.median(np.abs(half[a & b] - full[a & b])) < 0.01 and np.abs(half[a & b] - full[a & b]).max() < 0.1
    assert not b[:, 388:412].any() and not b[:4].any()  # eroded at the step and at the image border
    with FramePipeline(nominal_calibration(), PipelineParams(scale=1.0, use_lasers=False, use_imu=False)) as full_q:
        assert full_q.track_size == (640, 400) and full_q.tracker.image_size == (1280, 800)
        dd = np.full((800, 1280), 300.0, np.float32)
        assert full_q.tracking_input(dd).shape == (800, 1280)


def test_with_raw_depth_fills_only_what_the_filter_dropped():
    from ohs.app.pipeline import with_raw_depth

    raw = np.array([[300.0, np.nan, 0.0, 310.0]], np.float32)
    filt = np.array([[301.0, 0.0, 0.0, 0.0]], np.float32)
    out = with_raw_depth(filt, raw)
    assert out.tolist() == [[301.0, 0.0, 0.0, 310.0]] and filt[0, 3] == 0.0  # the input is left alone


class _FakeTracker:
    """Stands in for MarkerlessTracker: returns ok results with the given trans_strength values."""

    model_status = "fake"

    def __init__(self, strengths):
        self.strengths = list(strengths)
        self.closed = False
        self.detail_mode, self.detail_frame = False, None

    def track(self, depth, intensity, t, R_prior, alt_depth=None):
        from ohs.types import TrackResult

        s = self.strengths.pop(0)
        return TrackResult(np.eye(4), True, {"first_frame": True} if s is None else {"trans_strength": s})

    def close(self):
        self.closed = True


def test_detail_probe_hands_a_detailed_scan_to_the_raw_depth_tracker(monkeypatch):
    """The first frames of a scan also run a tracker on with_raw_depth. A frame whose filtered depth
    can't hold the pose hands the scan to it (detail mode); a scan that holds drops it."""
    from ohs.app import pipeline as P

    d = np.full((400, 640), 300.0, np.float32)
    for strengths, switch_at in (([None, 3e-3, 2e-3, 3e-4, 2e-3], 3), ([None] + [3e-3] * P.DETAIL_PROBE_FRAMES, None)):
        with FramePipeline(nominal_calibration(), PipelineParams(scale=0.5, use_lasers=False, use_imu=False)) as pipe:
            pipe.tracker.close()
            main, probe = _FakeTracker(strengths), _FakeTracker([None] + [1e-2] * P.DETAIL_PROBE_FRAMES)
            pipe.tracker = main
            monkeypatch.setattr(P, "MarkerlessTracker", lambda *a, **k: probe)
            for k in range(len(strengths) - 1 if switch_at else P.DETAIL_PROBE_FRAMES):
                pipe._n_frames = k
                res = pipe._probe_track(d, d, k / 30.0, None)
                if switch_at is not None and k == switch_at:
                    assert res.info["input"] == "alt" and pipe.tracker is probe and main.closed
                    assert probe.detail_mode and probe.detail_frame == switch_at and pipe._probe_left == 0
                    break
            if switch_at is None:
                assert pipe.tracker is main and probe.closed and pipe._probe is None and pipe._probe_left == 0
                assert not main.closed


def test_model_backends_per_model():
    """Only the tracking model follows fusion.model_backend. The reconstruction model is the
    confidence TSDF on a GPU (recon "auto") and Open3D otherwise; the single model of
    track_filter_px=0 is always Open3D, because it also tracks."""
    from ohs import compute

    calib = nominal_calibration()
    gpu = compute.get_device() is not None
    expect = "gpu" if gpu else "open3d"
    with FramePipeline(calib, PipelineParams(scale=0.5, use_lasers=False)) as pipe:
        assert pipe.dual_model and pipe.model_backend == expect and pipe.tracker.model_backend == expect
        assert pipe.markerless.model_backend == ("confidence-gpu" if gpu else "open3d") and pipe.has_confidence == gpu
        assert pipe.track_fusion_params.model_backend == "auto" and pipe.model_device and pipe.model_status
        assert ("confidence" in pipe.recon_status) == gpu
    with FramePipeline(calib, PipelineParams(scale=0.5, use_lasers=False, recon="open3d")) as pipe:
        assert pipe.markerless.model_backend == "open3d" and not pipe.has_confidence
    with FramePipeline(calib, PipelineParams(scale=0.5, use_lasers=False, recon="confidence")) as pipe:
        assert pipe.has_confidence  # the numpy twin when there is no GPU
    with FramePipeline(calib, PipelineParams(scale=0.5, use_lasers=False, tracking=False)) as pipe:
        assert pipe.tracker is None and pipe.has_confidence == gpu
        with pytest.raises(ValueError, match="pose"):
            pipe.process(StereoFrame(0, 0.0, np.zeros((800, 1280), np.uint8), np.zeros((800, 1280), np.uint8)))
    with FramePipeline(calib, PipelineParams(scale=0.5, use_lasers=False, fusion=FusionParams(model_backend="open3d"))) as pipe:
        assert pipe.model_backend == "open3d"
    with FramePipeline(calib, PipelineParams(scale=0.5, use_lasers=False, track_filter_px=0.0)) as pipe:
        assert not pipe.dual_model and pipe.model_backend == "open3d" and pipe.tracker is pipe.markerless
    with FramePipeline(calib, PipelineParams(mode="markers", integrate_depth=False)) as pipe:
        assert pipe.model_backend is None


WALL_MM = 4.0  # across y; 80 mm long, 50 mm high, standing on the table z = 0


def _wall_pose(angle_deg: float) -> np.ndarray:
    """T_wc of a camera 250 mm from the wall's middle, 45 deg above the table, at ``angle_deg`` round it (0: faces +y)."""
    a, e = np.radians(angle_deg), np.radians(45.0)
    target = np.array([0.0, 0.0, 20.0])
    c = target + 250.0 * np.array([np.sin(a) * np.cos(e), np.cos(a) * np.cos(e), np.sin(e)])
    z = (target - c) / np.linalg.norm(target - c)
    x = np.cross(z, [0.0, 0.0, 1.0])
    x /= np.linalg.norm(x)
    return G.make_se3(np.column_stack([x, np.cross(z, x), z]), c)


def _wall_depth(K: np.ndarray, size: tuple[int, int], T_wc: np.ndarray, wall_mm: float = WALL_MM) -> np.ndarray:
    """Exact depth (mm) of the table and the wall (``wall_mm`` thick) at the pixel centres of ``K``."""
    w, h = size
    vs, us = np.mgrid[0:h, 0:w].astype(np.float64)
    rays = np.stack([(us - K[0, 2]) / K[0, 0], (vs - K[1, 2]) / K[1, 1], np.ones((h, w))], -1).reshape(-1, 3)
    D, o = rays @ T_wc[:3, :3].T, T_wc[:3, 3]
    lo, hi = np.array([-40.0, -wall_mm / 2, 0.0]), np.array([40.0, wall_mm / 2, 50.0])
    with np.errstate(divide="ignore", invalid="ignore"):
        table = -o[2] / D[:, 2]
        table[~(table > 0)] = np.inf
        t1, t2 = (lo - o) / D, (hi - o) / D
        t_in, t_out = np.minimum(t1, t2).max(axis=1), np.maximum(t1, t2).min(axis=1)
    t = np.minimum(table, np.where((t_in < t_out) & (t_in > 0), t_in, np.inf))
    return np.where(np.isfinite(t), t, 0.0).reshape(h, w).astype(np.float32)  # the rays have z = 1: t is the depth


def _wall_face_offset(tr, T_wc: np.ndarray, wall_mm: float = WALL_MM) -> tuple[float, int]:
    """Model depth - true depth (mm) on the wall's +y face in the model's view from ``T_wc`` (negative: the face
    moved out towards the camera), and the number of pixels it is the mean of."""
    view = tr._raycast(T_wc)
    D, o = _view_rays(view) @ T_wc[:3, :3].T, T_wc[:3, 3]
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (wall_mm / 2 - o[1]) / D[:, 1]  # the face's plane y = +half the thickness
    P = o + D * t[:, None]
    m = np.where(view.valid, view.V[:, 2], 0.0)
    ok = (np.abs(P[:, 0]) < 34) & (P[:, 2] > 6) & (P[:, 2] < 44) & (t > 0) & (m > 0) & (np.abs(m - t) < 5)  # 6 mm inside its outline
    return float((m - t)[ok].mean()), int(ok.sum())


def _view_rays(view) -> np.ndarray:
    """(h*w, 3) pixel rays per unit depth of a model view."""
    w, h = view.size
    vs, us = np.mgrid[0:h, 0:w].astype(np.float64)
    return np.stack([(us - view.K[0, 2]) / view.K[0, 0], (vs - view.K[1, 2]) / view.K[1, 1], np.ones((h, w))], -1).reshape(-1, 3)


def test_the_gpu_tracking_model_keeps_a_thin_wall_in_place():
    """A 4 mm wall on a table, fused from exact depth at the true poses, first from its +y side, then from all
    round: its +y face must stay where it is in the tracking model the pipeline builds. A TSDF writes "inside"
    as far as its truncation behind every surface it sees. At 8 mm (four voxels) the views of the far side wrote
    through the wall and moved this face out by 0.8 mm; registered to such a model, cameras stood 0.3-0.5 % too
    far out and the mesh came out about 0.3 mm too fat (VALIDATION.md, "Where a tracked scan's shape errors come
    from"). The GPU model truncates at two voxels, 4 mm."""
    from ohs import compute

    if compute.get_device() is None:
        pytest.skip(f"no GPU: {compute.status()}")
    with FramePipeline(nominal_calibration(), PipelineParams(use_lasers=False, use_imu=False)) as pipe:
        tr = pipe.tracker
        assert pipe.dual_model and tr.model_backend == "gpu" and tr.params.voxel_mm == 2.0
        front = _wall_pose(0.0)
        angles = np.arange(0.0, 360.0, 10.0)
        near = [a for a in angles if _wall_pose(a)[1, 3] > 60]  # the views from the +y side
        for a in near:
            tr.integrate(_wall_depth(pipe.K, pipe.image_size, _wall_pose(a)), None, _wall_pose(a))
        own, n_own = _wall_face_offset(tr, front)
        for a in angles:
            if a not in near:
                tr.integrate(_wall_depth(pipe.K, pipe.image_size, _wall_pose(a)), None, _wall_pose(a))
        both, n_both = _wall_face_offset(tr, front)
        assert n_own > 2000 and n_both > 2000
        assert abs(own) < 0.15, f"seen from its own side the face is {own:+.2f} mm off"
        assert abs(both) < 0.15, f"once the far side was seen too the face is {both:+.2f} mm off"
        assert tr.trunc_mm == 4.0
    assert tr.trunc_mm == 4.0  # still known once the pipeline is closed (the report is written then)


def test_the_gpu_tracking_model_keeps_a_1_mm_wall_in_place():
    """A 1 mm wall in the pipeline's tracking model (2 mm voxels, 4 mm band). The band reaches 3 mm through such a
    wall, so the views of its far side moved this face out by 0.5 mm. Once a voxel has been integrated 4 times, the
    model now takes nothing more for it from views that see it more than one voxel (2 mm) behind another surface,
    measured at right angles to that surface: the far side's views stop writing through. A young model keeps the
    whole band, which a scan's first frames need. On scans this took the tracked bracket from 16 % to 13 % more than
    0.3 mm off and its mean offset from +0.12 to +0.01 mm (VALIDATION.md, "Thin walls with tracking")."""
    from ohs import compute

    if compute.get_device() is None:
        pytest.skip(f"no GPU: {compute.status()}")
    with FramePipeline(nominal_calibration(), PipelineParams(use_lasers=False, use_imu=False)) as pipe:
        tr = pipe.tracker
        assert tr.model_backend == "gpu" and tr.params.voxel_mm == 2.0 and tr.trunc_mm == 4.0
        assert tr.back_mm == 2.0 and tr.params.gpu_back_after == 4
        front = _wall_pose(0.0)
        angles = np.arange(0.0, 360.0, 10.0)
        near = [a for a in angles if _wall_pose(a)[1, 3] > 60]  # the views from the +y side
        for a in near:
            tr.integrate(_wall_depth(pipe.K, pipe.image_size, _wall_pose(a), wall_mm=1.0), None, _wall_pose(a))
        own, n_own = _wall_face_offset(tr, front, wall_mm=1.0)
        for a in angles:
            if a not in near:
                tr.integrate(_wall_depth(pipe.K, pipe.image_size, _wall_pose(a), wall_mm=1.0), None, _wall_pose(a))
        both, n_both = _wall_face_offset(tr, front, wall_mm=1.0)
        assert n_own > 2000 and n_both > 2000
        assert abs(own) < 0.2, f"seen from its own side the face is {own:+.2f} mm off"
        assert abs(both) < 0.2, f"once the far side was seen too the face is {both:+.2f} mm off"
    assert tr.back_mm == 2.0  # still known once the pipeline is closed (the report is written then)


def test_the_open3d_tracking_model_keeps_its_wide_truncation():
    """The Open3D tracking model (no GPU) stays at four voxels, 8 mm: with a shorter band it tracks worse on
    stereo depth (the half-resolution sphere scan's worst pose error 0.43 mm at 8 mm, 1.6 mm at 6 mm, 5.3 mm at
    4 mm; VALIDATION.md, "The CPU path does not take it"). So does a tracker made without the pipeline."""
    from ohs.track.markerless import MarkerlessTracker

    calib = nominal_calibration()
    with FramePipeline(calib, PipelineParams(scale=0.5, use_lasers=False, fusion=FusionParams(voxel_mm=2.0, model_backend="open3d"))) as pipe:
        assert pipe.model_backend == "open3d" and pipe.tracker.trunc_mm == 8.0
        assert pipe.tracker.back_mm is None  # and no short band behind the surface: that is the GPU model's
        assert pipe.fusion_params.trunc_mm == 8.0  # the mesh's model: untouched
    tr = MarkerlessTracker(pipe.K, pipe.image_size, FusionParams(voxel_mm=2.0))
    try:
        assert tr.trunc_mm == 8.0  # on a GPU too: only the pipeline's tracking model asks for the short band
        assert tr.back_mm is None
    finally:
        tr.close()


def _wall_thickness(vol, wall_mm: float) -> float:
    """The wall's thickness (mm) in the mesh of ``vol``: its two faces' mean positions across y, 6 mm inside the
    wall's outline."""
    V, _, N = (np.asarray(a, np.float64) for a in vol.extract_mesh()[:3])
    inside = (np.abs(V[:, 0]) < 34) & (V[:, 2] > 6) & (V[:, 2] < 44) & (np.abs(V[:, 1]) < wall_mm / 2 + 3)
    out = 0.0
    for sgn in (1.0, -1.0):
        m = inside & (sgn * N[:, 1] > 0.7)
        assert m.sum() > 1000
        out += float((sgn * V[m, 1]).mean())
    return out


def test_the_mesh_model_keeps_a_thin_wall_thin():
    """A 2 mm wall on a table, fused from exact depth at the true poses from all round, in the mesh model the
    pipeline builds (1 mm voxels, 4 mm truncation). A TSDF writes "inside" behind every surface it sees, so each
    side's views write through the wall into the air on the other side and both faces move out: the wall read
    2.6 mm (3.4 mm in a tracked scan of a sheet-metal part). In a markerless scan the pipeline's mesh model
    therefore takes an observation in full only down to two voxels behind its surface (``thin_walls``;
    VALIDATION.md, "A band that is short only behind the surface"). What is left is the voxel inside the wall
    averaging the two sides' distances."""
    from ohs import compute
    from ohs.track.recon_tsdf import ConfidenceTsdf

    if compute.get_device() is None:
        pytest.skip(f"no GPU: {compute.status()}")
    read = {}
    for setting in ("auto", "off"):
        with FramePipeline(nominal_calibration(), PipelineParams(use_lasers=False, use_imu=False, thin_walls=setting)) as pipe:
            vol = pipe.markerless
            assert isinstance(vol, ConfidenceTsdf) and vol.voxel_mm == 1.0 and vol.trunc_mm == 4.0
            for a in np.arange(0.0, 360.0, 10.0):
                vol.integrate(_wall_depth(pipe.K, pipe.image_size, _wall_pose(a), wall_mm=2.0), None, _wall_pose(a))
            read[setting] = _wall_thickness(vol, 2.0)
    assert read["off"] > 2.58, f"with the whole band behind the surface the 2 mm wall reads {read['off']:.2f} mm"
    assert read["auto"] < 2.48, f"the 2 mm wall reads {read['auto']:.2f} mm"


def test_thin_walls_follows_the_mode_unless_it_is_forced():
    """``PipelineParams.thin_walls``: "auto" gives the mesh model its short band behind the surface in a
    markerless scan and leaves marker mode as it was (frames that disagree by 0.9 mm need the whole band to
    average out); "on" and "off" force it. The cut is two voxels. Only the confidence TSDF has the rule: the
    Open3D reconstruction of the CPU path does not."""
    from ohs import compute

    calib = nominal_calibration()
    gpu = compute.get_device() is not None

    def band(**kw) -> tuple:
        with FramePipeline(calib, PipelineParams(use_lasers=False, use_imu=False, **kw)) as pipe:
            return pipe.thin_walls_mm, getattr(pipe.markerless, "back_mm", None)

    two = (2.0, 2.0) if gpu else (None, None)
    assert band() == two  # markerless, 1 mm voxels
    assert band(scale=0.5) == ((4.0, 4.0) if gpu else (None, None))  # 2 mm voxels
    assert band(mode="markers") == (None, None)
    assert band(mode="markers", thin_walls="on") == two
    assert band(thin_walls="off") == (None, None)
    assert band(tracking=False) == two  # fused at given poses (after Straighten): still a markerless scan
    assert band(recon="open3d", thin_walls="on") == (None, None)  # Open3D's model has no such rule
    with pytest.raises(ValueError, match="thin_walls"):
        PipelineParams(thin_walls="maybe")


# --------------------------------------------------------------------------- process_recording
def test_gap_guard_reaches_the_reconstruction_only():
    """The gap guard (depth next to a patch without depth is not fused) is on by default, and it is
    the reconstruction's: the confidence TSDF applies it itself, the Open3D reconstruction gets the
    depth already guarded, and a model that also tracks keeps every pixel."""
    from ohs import compute
    from ohs.track.recon_tsdf import ConfidenceTsdf, gap_guard_mask

    calib = nominal_calibration()

    def fused_depth(params: PipelineParams) -> tuple:
        with FramePipeline(calib, params) as pipe:
            w, h = pipe.image_size
            d = np.full((h, w), 300.0, np.float32)
            d[h // 2 - 20:h // 2 + 20, w // 2 - 20:w // 2 + 20] = 0.0
            d[10, 10] = 900.0  # out of the fused range: no patch on its own
            got = {}
            pipe.markerless.integrate = lambda depth, sigma, T, t=None: got.update(depth=np.array(depth))
            pipe._fuse(d.copy(), np.eye(4), 0.0, {})
            return d, got["depth"], pipe.gap_guard_px, isinstance(pipe.markerless, ConfidenceTsdf), w

    d, got, px, conf, w = fused_depth(PipelineParams(scale=0.5, use_lasers=False, recon="open3d"))
    mask = gap_guard_mask(np.where((d >= 120.0) & (d <= 500.0), d, 0.0), px)
    assert not conf and px == pytest.approx(8.0 * w / 1280.0) and mask.sum() > 300
    assert np.all(got[mask] == 0.0) and np.array_equal(got[~mask], d[~mask])
    d, got, px, conf, _ = fused_depth(PipelineParams(scale=0.5, use_lasers=False, recon="open3d", gap_guard_px=0.0))
    assert px == 0.0 and np.array_equal(got, d)  # turned off
    d, got, _, conf, _ = fused_depth(PipelineParams(scale=0.5, use_lasers=False, track_filter_px=0.0))
    assert not conf and np.array_equal(got, d)  # the single model also tracks: untouched
    if compute.get_device() is not None:
        with FramePipeline(calib, PipelineParams(scale=0.5, use_lasers=False)) as pipe:
            assert isinstance(pipe.markerless, ConfidenceTsdf) and pipe.markerless.gap_guard_px == pipe.gap_guard_px > 0
        d, got, px, conf, _ = fused_depth(PipelineParams(scale=0.5, use_lasers=False))
        assert conf and np.array_equal(got, d)  # the confidence TSDF guards inside
    with pytest.raises(ValueError):
        PipelineParams(gap_guard_px=-1.0)


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
    # the tracking model: two voxels on the GPU, four on Open3D
    assert rep["params"]["tracking"]["trunc_mm"] == (4.0 if rep["params"]["compute"]["tracking_model"] == "gpu" else 8.0)
    assert rep["params"]["tracking"]["back_mm"] == (2.0 if rep["params"]["compute"]["tracking_model"] == "gpu" else None)
    # the mesh model's short band behind the surface: two voxels in the confidence TSDF, none in Open3D's model
    assert rep["params"]["reconstruction"] == {
        "model": rep["mesh"]["reconstruction"], "thin_walls": "auto",
        "back_mm": 4.0 if "confidence" in rep["mesh"]["reconstruction"] else None}
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


def test_process_recording_frames_by_motion(rec, tmp_path, caplog):
    """F2 step 2: frames picked by the gyro, the second pass at given poses, and the fallbacks."""
    import dataclasses
    import logging

    half = PipelineParams(scale=0.5)
    # a threshold no hand reaches: the cap alone picks (and start / stop bound the candidates)
    p = ProcessParams(pipeline=half, export=("ply",), start=1, stop=8, frame_motion_deg=90.0, frame_max_gap=3)
    rep = process_recording(rec, tmp_path / "cap", params=p)
    assert [r["index"] for r in rep["per_frame"]] == [1, 4, 7] and rep["frames"]["processed"] == 3
    sel = rep["frames"]["selection"]
    assert sel == {"by": "motion", "threshold_deg": 90.0, "max_gap": 3, "picked_by_rotation": 0, "picked_by_cap": 2,
                   "picked_first": 1, "picked_without_gyro": 0, "mean_gap": 3.0, "gaps": {"3": 2}}
    assert rep["frames"]["step"] == 1 and rep["frames"]["tracked"] == 3
    # the second pass (Straighten, Fuse) fuses exactly the frames of the first, with the same settings
    with open(tmp_path / "cap" / "trajectory.csv", newline="", encoding="utf-8") as fh:
        poses = {int(r["index"]): np.array([float(r[f"T{i}{j}"]) for i in range(4) for j in range(4)]).reshape(4, 4)
                 for r in csv.DictReader(fh)}
    again = process_recording(rec, tmp_path / "fuse", params=p, poses=poses)
    assert [r["index"] for r in again["per_frame"]] == [1, 4, 7] and again["frames"]["without_pose"] == 4
    assert again["frames"]["selection"]["by"] == "given poses"
    # a threshold below every frame's rotation keeps every frame, all picked by rotation
    rep = process_recording(rec, tmp_path / "all", params=dataclasses.replace(p, start=0, stop=3, frame_motion_deg=1e-6))
    assert [r["index"] for r in rep["per_frame"]] == [0, 1, 2]
    assert rep["frames"]["selection"]["picked_by_rotation"] == 2 and rep["frames"]["selection"]["gaps"] == {"1": 2}
    # no IMU samples, or no IMU calibration: every frame, with a warning, and the report says why
    no_imu = tmp_path / "no_imu"
    shutil.copytree(rec, no_imu)
    (no_imu / "imu.csv").unlink()
    short = dataclasses.replace(p, start=0, stop=3)
    with caplog.at_level(logging.WARNING, logger="ohs.app.process"):
        rep = process_recording(no_imu, tmp_path / "o_no_imu", params=short)
    assert "cannot pick frames by motion" in caplog.text and rep["frames"]["processed"] == 3
    assert rep["frames"]["selection"] == {"by": "step", "threshold_deg": 90.0, "max_gap": 3,
                                          "fallback": "the recording has no IMU samples"}
    calib = dataclasses.replace(RecordingReader(rec).calibration(), imu=None)
    rep = process_recording(rec, tmp_path / "o_no_cal", calib=calib, params=dataclasses.replace(short, stop=2))
    assert rep["frames"]["processed"] == 2 and rep["frames"]["selection"]["fallback"] == "there is no IMU calibration"


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
