"""The live virtual calibration scanner (``ohs.sim.board_camera``).

Geometry against ``project_to_raw``, every wizard step run through the real calibration flows on
its frames (errors against its true calibration; run with ``-s`` to see the ``METRICS`` lines),
and the camera / IMU-feed behaviour the app relies on: looping, restart, end of a script, pacing
and determinism. Behaviour tests that never detect the board render at 640x400 to stay fast.
"""
from __future__ import annotations

import time
from dataclasses import asdict

import numpy as np
import pytest

from ohs import geometry as G
from ohs.app.calib_flow import check_from_recording, imu_from_recording, lasers_from_recording
from ohs.calib.board import CharucoSpec, board_corner_points, detect_charuco
from ohs.calib.drift import drift_check
from ohs.calib.stereo import calibrate_stereo
from ohs.io.recording import RecordingWriter
from ohs.sim.board_camera import IMU_PEAK_DEG, SCRIPTS, SimBoardCamera
from ohs.sim.datasets import laser_calib_board_poses, stereo_calib_board_poses
from ohs.sim.imu import so3_log_batch
from ohs.sim.nominal import nominal_calibration, nominal_emitters
from ohs.sim.render import project_to_raw
from ohs.vision.laser import extract_laser_lines
from ohs.vision.rectify import Rectifier
from test_e2e_calibration import imu_errors, metrics, plane_errors, stereo_errors

SMALL = (640, 400)
IMU_RATE = 416.0


# --------------------------------------------------------------------------- helpers / fixtures
def corner_errors(cam: SimBoardCamera, img: np.ndarray, T_cb: np.ndarray, side: str) -> np.ndarray:
    """Detected ChArUco corners minus the true corners projected with ``project_to_raw`` (px)."""
    uv, ids = detect_charuco(img, cam.spec)
    P = G.transform_points(T_cb, board_corner_points(cam.spec)[ids])
    return np.linalg.norm(uv - project_to_raw(P, cam.true_calibration.stereo, side), axis=1)


def write_recording(path, frames, calib, purpose: str):
    with RecordingWriter(path, source="sim", purpose=purpose) as w:
        for f in frames:
            w.write_frame(f)
        w.write_calibration(calib)
    return path


@pytest.fixture(scope="module")
def stereo_cam():
    with SimBoardCamera("stereo", realtime=False) as cam:
        yield cam


@pytest.fixture(scope="module")
def lasers_cam():
    with SimBoardCamera("lasers", realtime=False) as cam:
        yield cam


@pytest.fixture(scope="module")
def imu_cam():
    with SimBoardCamera("imu", realtime=False) as cam:
        yield cam


# --------------------------------------------------------------------------- truth and geometry
def test_true_calibration_has_lasers_and_imu(stereo_cam):
    true = stereo_cam.true_calibration
    nominal = nominal_calibration((1280, 800), realistic=True)
    np.testing.assert_allclose(true.stereo.K1, nominal.stereo.K1)
    np.testing.assert_allclose(true.stereo.R, nominal.stereo.R)
    assert np.any(true.stereo.D1) and not np.allclose(true.stereo.R1, np.eye(3))  # distorted, rectification non-trivial
    em = nominal_emitters(baseline=true.stereo.baseline)
    for name in ("vertical", "horizontal"):  # the sheets the simulator's renderer draws
        plane = true.lasers[name]
        assert np.degrees(np.arccos(min(1.0, abs(float(plane.normal @ em[name]["normal"]))))) < 1e-6
        assert plane.d == pytest.approx(em[name]["d"], abs=1e-9)
    assert true.imu is not None and true.imu.time_offset == pytest.approx(0.030)
    info = stereo_cam.info
    assert {k: info[k] for k in ("backend", "script", "width", "height", "fps", "fourcc")} == {
        "backend": "sim", "script": "stereo", "width": 2560, "height": 800, "fps": 10.0, "fourcc": ""}
    with pytest.raises(ValueError):
        SimBoardCamera("calibrate")
    with pytest.raises(ValueError):
        SimBoardCamera("stereo", paper="tabloid")


def test_geometry_matches_project_to_raw(stereo_cam, lasers_cam, imu_cam):
    """Detected corners in both raw images against the projection of the true corners."""
    d0 = stereo_cam.dwell_times()[0]
    cases = [(stereo_cam, t) for t in stereo_cam.dwell_times()[:4]] + [(stereo_cam, d0 + 1.15)]  # one mid-move
    cases += [(lasers_cam, lasers_cam.dwell_times()[1]), (imu_cam, 9.3), (imu_cam, 17.6)]
    errs = []
    for k, (cam, t) in enumerate(cases):
        f = cam.frame_at(t, k)
        for side, img in (("left", f.left), ("right", f.right)):
            e = corner_errors(cam, img, cam.pose_at(t), side)
            assert len(e) >= 40, (cam.script, t, side, len(e))
            errs.append(e)
    e = np.concatenate(errs)
    metrics("board_camera_geometry", corners=len(e), median_px=np.median(e), p90_px=np.percentile(e, 90))
    # measured: median 0.045 px, p90 0.09 px
    assert np.median(e) < 0.15 and np.percentile(e, 90) < 0.3


def test_read_follows_true_pose_and_images_are_stereo_consistent(imu_cam):
    """Frames from read() match true_pose(), and the rectified rows agree under the true calibration."""
    frames = []
    for _ in range(3):
        f = imu_cam.read()
        frames.append(f)
        e = np.concatenate([corner_errors(imu_cam, img, imu_cam.true_pose(), side)
                            for side, img in (("left", f.left), ("right", f.right))])
        assert np.median(e) < 0.15
    assert [f.index for f in frames] == [0, 1, 2]
    res = drift_check(frames, imu_cam.true_calibration.stereo)
    assert res["state"] == "ok" and res["dv_px"] < 0.2, res


# --------------------------------------------------------------------------- the wizard steps
def test_stereo_calibration_on_dwell_frames(stereo_cam):
    pairs = [(f.left, f.right) for f in (stereo_cam.frame_at(t, i) for i, t in enumerate(stereo_cam.dwell_times()))]
    est, rep = calibrate_stereo(pairs, stereo_cam.spec, stereo_cam.image_size)
    e = stereo_errors(est, stereo_cam.true_calibration.stereo)
    metrics("board_camera_stereo", views=rep["views_used"], rms=rep["rms"], **e)
    # measured: rms 0.06 px, fx 0.17 / 0.23 px, baseline 0.002 mm, rotation 0.002 deg
    assert rep["views_used"] == 16 and rep["rms"] < 0.5 and rep["verdict"] == "good"
    assert abs(e["fx_err_px"]) < 1.0 and abs(e["fx_right_err_px"]) < 1.0
    assert abs(e["baseline_err_mm"]) < 0.15 and e["rel_rotation_err_deg"] < 0.05


def test_lasers_fit_from_the_margin(lasers_cam, tmp_path):
    true = lasers_cam.true_calibration
    rect = Rectifier(true.stereo)
    frames = [lasers_cam.frame_at(t, i) for i, t in enumerate(lasers_cam.dwell_times())]
    for f in frames:  # the engine's extractor finds both lines in both rectified images
        for img in rect.rectify(f.left, f.right):
            lines = extract_laser_lines(img)
            assert len(lines.vertical) >= 40 and len(lines.horizontal) >= 40, f.index
    rec = write_recording(tmp_path / "lasers", frames, true, "laser_calibration")
    planes, rep = lasers_from_recording(rec, true, lasers_cam.spec, paper="letter")
    assert rep["ok"] and rep["region_used"] == {"vertical": "margin", "horizontal": "margin"}  # no fallback to "both"
    assert rep["frames_with_board"] == 8
    for name in ("vertical", "horizontal"):
        with_margin = sum(row["points"][name]["margin"] > 0 for row in rep["per_frame"])
        assert with_margin >= 6, (name, with_margin)
    err = plane_errors(planes, true.lasers)
    metrics("board_camera_lasers", **{f"{n}_{k}": v for n, e in err.items() for k, v in e.items()})
    for name, e in err.items():
        # measured: 0.001-0.005 deg; laser-point error median 0.01-0.02 mm, max < 0.16 mm (150-450 mm)
        assert e["angle_deg"] < 0.1 and e["point_err_median_mm"] < 0.1 and e["point_err_max_mm"] < 0.5, (name, e)


def test_check_script_passes_the_health_check(tmp_path):
    cam = SimBoardCamera("check", realtime=False)
    assert len(cam.dwell_times()) == 4
    rec = write_recording(tmp_path / "check", [cam.frame_at(t, i) for i, t in enumerate(cam.dwell_times())],
                          cam.true_calibration, "calibration_check")
    chk = check_from_recording(rec, cam.true_calibration, cam.spec)
    assert chk["ok"], chk["verdict"]
    assert chk["stereo"]["mean_abs_dv_px"] < 0.2
    assert chk["lasers"]["lasers"]["vertical"]["median_abs_mm"] < 0.1
    assert chk["lasers"]["lasers"]["horizontal"]["median_abs_mm"] < 0.1


@pytest.mark.slow
def test_imu_calibration_from_one_pass(tmp_path):
    """One pass of the imu script recorded like the app does (read, then the feed up to the frame)."""
    cam = SimBoardCamera("imu", realtime=False, loop=False)
    true = cam.true_calibration
    rec = tmp_path / "imu"
    with cam, RecordingWriter(rec, source="sim", purpose="imu_calibration") as w:
        w.write_calibration(true)
        while True:
            try:
                f = cam.read()
            except EOFError:
                break
            w.write_frame(f)
            samples, events = cam.feed.take(f.t_host + 0.05)
            assert events == []
            w.write_imu(samples)
    est, rep = imu_from_recording(rec, true, cam.spec)
    e = imu_errors(est, true.imu)
    metrics("board_camera_imu", frames=rep["frames"], frames_with_pose=rep["frames_with_pose"],
            residual_rms_deg=rep["residual_rms_deg"], **e)
    assert rep["frames"] == cam.frames_per_pass == 310
    assert rep["frames_with_pose"] >= 0.95 * rep["frames"] and rep["bias_source"] == "static"
    # measured: 0.015 deg, -0.01 ms, 0.11 mrad/s
    assert e["R_ci_err_deg"] < 0.5 and abs(e["time_offset_err_ms"]) < 3.0 and e["gyro_bias_err_mrad_s"] < 1.0


# --------------------------------------------------------------------------- scripts
def test_board_tours():
    cams = {s: SimBoardCamera(s, realtime=False) for s in ("stereo", "lasers", "check")}
    calib, spec = cams["stereo"].true_calibration, asdict(CharucoSpec())
    expected = {"stereo": stereo_calib_board_poses(16, calib, spec, 0), "lasers": laser_calib_board_poses(8, calib, spec, 0),
                "check": laser_calib_board_poses(4, calib, spec, 1)}
    for script, cam in cams.items():
        poses, dwell = expected[script], cam.dwell_times()
        n, seg = len(poses), 1.6 + 0.7
        assert cam.period_s == pytest.approx(n * seg) and dwell == pytest.approx([i * seg + 0.8 for i in range(n)])
        for i, T in enumerate(poses):
            np.testing.assert_allclose(cam.pose_at(dwell[i]), T, atol=1e-9)
            # continuous at both ends of each move; the last move goes back to the first pose
            np.testing.assert_allclose(cam.pose_at(i * seg + 1.6 + 1e-9), T, atol=1e-6)
            np.testing.assert_allclose(cam.pose_at((i + 1) * seg - 1e-9), poses[(i + 1) % n], atol=1e-6)
        assert cam.phase_at(dwell[2]) == f"dwell 3/{n}" and cam.phase_at(dwell[2] + 1.15) == "move"


def test_imu_script_motion(imu_cam):
    cam = imu_cam
    assert cam.period_s == pytest.approx(31.0) and cam.dwell_times() == [] and cam.laser_lines_at(10.0) == {}
    assert [cam.phase_at(t) for t in (1.0, 10.0, 28.0, 30.0)] == ["still", "rotate", "return", "still"]
    T0 = cam.pose_at(0.0)
    for t in (2.4, 29.1, 30.99):  # still at the start, back at the start pose at the end: seamless loop
        np.testing.assert_allclose(cam.pose_at(t), T0, atol=1e-9)
    R0 = T0[:3, :3]  # camera rotation relative to the start: R_cb(0) R_cb(t)^T
    th = np.degrees(so3_log_batch(np.stack([R0 @ cam.pose_at(t)[:3, :3].T for t in np.arange(0.0, cam.period_s, 0.01)])))
    np.testing.assert_allclose(np.abs(th).max(axis=0), IMU_PEAK_DEG, atol=0.2)
    ts = np.arange(0.0, cam.period_s, 0.1)
    st = cam.true_calibration.stereo
    W, H = cam.spec.size_mm
    outer = np.array([[0, 0, 0], [W, 0, 0], [W, H, 0], [0, H, 0]], dtype=np.float64)
    inside = []
    for t in ts:  # the whole board stays in both raw images
        ok = True
        for side in ("left", "right"):
            uv = project_to_raw(G.transform_points(cam.pose_at(t), outer), st, side)
            ok &= bool(np.all((uv > 5) & (uv < np.array(st.image_size) - 6)))
        inside.append(ok)
    assert np.mean(inside) >= 0.98


# --------------------------------------------------------------------------- camera / feed behaviour
def test_frame_at_is_deterministic(stereo_cam):
    a, b = stereo_cam.frame_at(3.3, 7), stereo_cam.frame_at(3.3, 7)
    assert a.index == 7 and a.t_host == b.t_host
    assert np.array_equal(a.left, b.left) and np.array_equal(a.right, b.right)
    c = stereo_cam.frame_at(3.3, 8)  # other index: other noise, same scene
    assert not np.array_equal(a.left, c.left)
    assert np.mean(np.abs(a.left.astype(float) - c.left.astype(float))) < 2.0
    cam = SimBoardCamera("stereo", image_size=SMALL, realtime=False)
    with cam:
        for k in range(3):  # frame_at(k / fps, k) is frame k of a fresh camera
            f, g = cam.read(), cam.frame_at(k / cam.fps, k)
            assert f.index == g.index == k and f.t_host == g.t_host
            assert np.array_equal(f.left, g.left) and np.array_equal(f.right, g.right)
            assert f.t_host == pytest.approx(cam.t0 + k / cam.fps + 0.030, abs=1e-3)


def test_loop_restart_and_feed_continuity():
    cam = SimBoardCamera("check", image_size=SMALL, realtime=False, dwell_s=0.3, move_s=0.2)
    assert cam.period_s == pytest.approx(2.0) and cam.frames_per_pass == 20
    frames, poses, phases, samples = [], [], [], []

    def read():
        f = cam.read()
        frames.append(f)
        poses.append(cam.true_pose())
        phases.append(cam.phase())
        s, ev = cam.feed.take(f.t_host + 0.05)
        assert ev == []
        samples.extend(s)
        return f

    with cam:
        for _ in range(47):  # 2.35 passes
            read()
        cam.restart()  # mid-pass, from "another thread"
        restart_frame = read()
        for _ in range(25):
            read()
    idx = [f.index for f in frames]
    assert idx == list(range(len(frames)))  # never the same frame twice
    dt = np.diff([f.t_host for f in frames])
    assert np.all(np.abs(dt - 0.1) < 2e-3)  # the virtual clock keeps running through loops and the restart
    for k in (0, 1, 5):  # the tour loops: frames k + 20 and k + 40 show the pose of frame k
        np.testing.assert_allclose(poses[k + 20], poses[k], atol=1e-9)
        np.testing.assert_allclose(poses[k + 40], poses[k], atol=1e-9)
    assert phases[47] == "dwell 1/4" and phases[46] != "dwell 1/4"  # the restart starts the script again
    np.testing.assert_allclose(poses[47], cam.pose_at(0.0), atol=1e-9)
    np.testing.assert_allclose(poses[47 + 7], poses[7], atol=1e-9)
    assert restart_frame.index == 47
    t = np.array([s.t_host for s in samples])
    assert np.all(np.diff(t) > 0)  # strictly increasing, nothing returned twice
    assert np.diff(t).max() < 2.5 / IMU_RATE  # and no gaps, across loops and the restart
    assert t[0] < frames[0].t_host and t[-1] <= frames[-1].t_host + 0.05
    g = np.array([s.gyro for s in samples])
    bias = cam.true_calibration.imu.gyro_bias
    assert np.abs(g - bias).max() < 0.02  # the scanner stands still during the board tours


def test_imu_feed_repeats_each_pass(imu_cam):
    cam = imu_cam
    P, t0, bias = cam.period_s, cam.t0, cam.true_calibration.imu.gyro_bias
    feed = SimBoardCamera("imu", realtime=False).feed  # a fresh feed: imu_cam's has been read from
    samples, until = [], t0
    while until < t0 + 2.5 * P:  # in steps, like the app's take() per frame
        until += 0.1
        samples.extend(feed.take(until)[0])
    t = np.array([s.t_host for s in samples])
    g = np.array([s.gyro for s in samples])
    assert np.all(np.diff(t) > 0) and np.diff(t).max() < 2.5 / IMU_RATE
    assert t0 <= t[0] < t0 + 0.005  # starts at the exposure of frame 0
    p1 = (t >= t0) & (t < t0 + P)
    p2 = (t >= t0 + P) & (t < t0 + 2 * P)
    assert p1.sum() == p2.sum() > 0.99 * IMU_RATE * P
    np.testing.assert_allclose(t[p2] - P, t[p1], atol=1e-9)  # pass 2 = pass 1 shifted by the period
    np.testing.assert_array_equal(g[p2], g[p1])
    s = t[p1] - t0  # script time of each sample (camera time minus the time offset of the start)
    rate = np.linalg.norm(g[p1] - bias, axis=1)
    assert rate[(s < 2.4) | (s > 29.1)].max() < 0.02  # still: noise and bias only
    assert np.median(rate[(s > 4.0) & (s < 27.0)]) > 0.3  # rotating
    R = G.integrate_gyro(t[p1], g[p1] - bias, t[p1][0], t[p1][-1])
    assert G.rotation_angle_deg(R) < 1.0  # one pass returns to the start pose


def test_end_of_script_and_restart():
    cam = SimBoardCamera("check", image_size=SMALL, realtime=False, loop=False, dwell_s=0.3, move_s=0.2)
    with cam:
        frames = []
        with pytest.raises(EOFError):
            while True:
                frames.append(cam.read())
        assert len(frames) == cam.frames_per_pass == 20
        with pytest.raises(EOFError):
            cam.read()
        cam.restart()  # plays the script again; the frame index and the virtual clock continue
        f = cam.read()
        assert f.index == 20 and cam.phase() == "dwell 1/4" and f.t_host > frames[-1].t_host
    with pytest.raises(RuntimeError):  # closed
        cam.read()


def test_realtime_pacing():
    cam = SimBoardCamera("stereo", image_size=SMALL, fps=20.0, realtime=True)
    with cam:
        t_start = time.perf_counter()
        frames = [cam.read() for _ in range(11)]
        elapsed = time.perf_counter() - t_start
        assert [f.index for f in frames] == list(range(11))
        assert 0.8 * 0.5 < elapsed < 1.2 * 0.5, elapsed  # 10 frame periods at 20 fps
        with pytest.raises(TimeoutError):  # the next frame is 50 ms away
            cam.read(timeout=0.005)
        assert cam.read().index == 11  # nothing was lost
        time.sleep(1.3)  # a reader that falls behind is paced from then on, without a burst
        t_late = time.perf_counter()
        for _ in range(3):
            cam.read()
        assert time.perf_counter() - t_late > 0.08


def test_all_scripts_render():
    for script in SCRIPTS:
        cam = SimBoardCamera(script, image_size=SMALL, realtime=False)
        with cam:
            f = cam.read()
        assert f.left.shape == f.right.shape == (400, 640) and f.left.dtype == np.uint8
        paper = np.percentile(f.left, 90)
        assert (50 < paper < 240) and f.left.min() > 0  # paper and a dark (not black) backdrop
        assert cam.info["script"] == script and cam.info["width"] == 1280
