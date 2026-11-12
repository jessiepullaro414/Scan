"""Tests for the shared core: types, geometry, recording."""
import numpy as np
import pytest

from ohs import geometry as G
from ohs.io.recording import RecordingReader, RecordingWriter
from ohs.types import (
    Event,
    ImuCalibration,
    ImuSample,
    LaserPlane,
    ScannerCalibration,
    StereoCalibration,
    StereoFrame,
)


def nominal_stereo(size=(1280, 800), f=720.0, baseline=60.0):
    K = np.array([[f, 0, size[0] / 2 - 0.5], [0, f, size[1] / 2 - 0.5], [0, 0, 1.0]])
    return StereoCalibration.build(size, K, np.zeros(5), K, np.zeros(5), np.eye(3), np.array([-baseline, 0, 0]))


# --------------------------------------------------------------------------- geometry
def test_so3_roundtrip():
    rng = np.random.default_rng(1)
    for _ in range(50):
        w = rng.normal(size=3) * rng.uniform(0, 3.0)
        R = G.so3_exp(w)
        assert np.allclose(R @ R.T, np.eye(3), atol=1e-10)
        assert np.allclose(G.so3_exp(G.so3_log(R)), R, atol=1e-8)
    R = G.so3_exp(np.array([0, 0, np.pi - 1e-6]))
    assert np.allclose(G.so3_exp(G.so3_log(R)), R, atol=1e-5)


def test_kabsch_recovers_transform():
    rng = np.random.default_rng(2)
    A = rng.normal(size=(30, 3)) * 50
    R = G.so3_exp(np.array([0.3, -0.2, 0.9]))
    t = np.array([10.0, -4.0, 300.0])
    B = A @ R.T + t
    R2, t2 = G.kabsch(A, B)
    assert np.allclose(R2, R, atol=1e-9) and np.allclose(t2, t, atol=1e-7)


def test_plane_fit_and_ray_intersection():
    rng = np.random.default_rng(3)
    n = np.array([0.9473, 0.0, 0.3205]); n /= np.linalg.norm(n)
    d = -109.8
    # points on the plane: pick rays that hit it in front of the camera (u > ~396) and intersect
    uv = rng.uniform([450, 0], [1280, 800], size=(200, 2))
    K = nominal_stereo().K_rect
    P = G.ray_plane_intersect(G.pixels_to_rays(uv, K), n, d)
    assert np.all(np.isfinite(P))
    assert np.allclose(P @ n + d, 0, atol=1e-9)
    assert np.allclose(G.project_points(P, K), uv, atol=1e-6)
    n2, d2, rms = G.fit_plane(P + rng.normal(scale=0.01, size=P.shape))
    assert abs(abs(n2 @ n) - 1) < 1e-5 and abs(d2 - d) < 0.05 and rms < 0.02
    outliers = rng.uniform(-200, 400, size=(60, 3))
    n3, d3, mask, _ = G.fit_plane_ransac(np.vstack([P, outliers]), thresh=0.5)
    assert mask[:200].all() and abs(d3 - d) < 1e-6


def test_integrate_gyro_constant_rate():
    t = np.linspace(0, 1, 417)
    w = np.array([0.0, 0.0, 1.0])
    R = G.integrate_gyro(t, np.tile(w, (len(t), 1)), 0.1, 0.6)
    assert np.allclose(R, G.so3_exp(w * 0.5), atol=1e-9)
    assert np.allclose(G.integrate_gyro(t, np.tile(w, (len(t), 1)), 0.6, 0.1), R.T, atol=1e-9)


def test_depth_to_points():
    K = nominal_stereo().K_rect
    depth = np.full((800, 1280), 250.0, dtype=np.float32)
    depth[:10] = 0
    P = G.depth_to_points(depth, K, stride=4)
    assert np.allclose(P[:, 2], 250.0) and len(P) == (800 // 4 - 3) * (1280 // 4)


# --------------------------------------------------------------------------- types
def test_stereo_calibration_build_and_scale():
    sc = nominal_stereo()
    assert sc.baseline == pytest.approx(60.0)
    assert np.allclose(sc.K_rect[0, 0], 720.0)
    half = sc.scaled(0.5)
    assert half.image_size == (640, 400)
    assert half.baseline == pytest.approx(60.0)
    assert half.K_rect[0, 0] == pytest.approx(360.0)
    # Q reprojects disparity to the same depth: z = f * B / disp
    disp = 100.0
    X = half.Q @ np.array([300.0, 200.0, disp, 1.0])
    assert X[2] / X[3] == pytest.approx(360.0 * 60.0 / disp)


def test_calibration_roundtrip(tmp_path):
    cal = ScannerCalibration(
        nominal_stereo(),
        {"vertical": LaserPlane("vertical", np.array([0.9473, 0, 0.3205]), -109.8, rms=0.1, n_points=10)},
        ImuCalibration(G.so3_exp(np.array([0.01, 0.02, 0.0])), 0.025, np.array([1e-3, 0, 0])),
    )
    cal.save(tmp_path / "c.json")
    back = ScannerCalibration.load(tmp_path / "c.json")
    assert np.allclose(back.stereo.P2, cal.stereo.P2)
    assert back.lasers["vertical"].d == pytest.approx(cal.lasers["vertical"].d)
    assert np.allclose(back.imu.R_ci, cal.imu.R_ci)
    lp = LaserPlane("x", np.array([0, 0, -2.0]), 10.0)  # gets normalised and sign-flipped
    assert lp.d <= 0 and np.isclose(np.linalg.norm(lp.normal), 1)


# --------------------------------------------------------------------------- recording
def test_recording_roundtrip(tmp_path):
    rng = np.random.default_rng(0)
    rec = tmp_path / "rec"
    frames = [StereoFrame(i, 10.0 + i / 30, rng.integers(0, 255, (80, 128), dtype=np.uint8),
                          rng.integers(0, 255, (80, 128), dtype=np.uint8)) for i in range(5)]
    with RecordingWriter(rec, source="sim", note="test") as w:
        for f in frames:
            w.write_frame(f)
        w.write_imu([ImuSample(10.0 + k / 416, 5.0 + k / 416, np.array([0.1, 0.2, 0.3]), np.array([0, 0, 9.81])) for k in range(20)])
        w.write_event(Event(10.05, "button", "down"))
        w.write_ground_truth_pose(0, 10.0, np.eye(4))
        w.write_ground_truth_depth(0, np.ones((80, 128)))
        w.write_calibration(ScannerCalibration(nominal_stereo((128, 80), 72.0)))
    r = RecordingReader(rec)
    assert len(r) == 5 and r.meta["source"] == "sim" and r.meta["n_frames"] == 5
    assert r.meta["image_size"] == [128, 80]
    f3 = r.frame(3)
    assert np.array_equal(f3.left, frames[3].left) and np.array_equal(f3.right, frames[3].right)
    assert f3.t_host == pytest.approx(frames[3].t_host, abs=1e-6)
    a = r.imu_arrays()
    assert a["gyro"].shape == (20, 3) and np.allclose(a["accel"][:, 2], 9.81)
    assert len(r.imu_samples(10.0, 10.01)) == 5
    assert r.events()[0].value == "down"
    assert np.allclose(r.ground_truth_poses()[0], np.eye(4))
    assert r.ground_truth_depth(0).shape == (80, 128)
    assert r.calibration().stereo.image_size == (128, 80)
    assert "temperature_c" not in r.meta and r.temperature()["celsius"].shape == (0,)  # none recorded
    with pytest.raises(FileExistsError):
        RecordingWriter(rec)


@pytest.mark.parametrize("mode", ["returns_false", "raises"])
def test_recording_failed_frame_write_is_reported(tmp_path, monkeypatch, mode):
    """A frame write that fails (cv2.imwrite returns False, or raises) is reported by close(), even
    after its future was pruned from the pending list (more than 64 frames written)."""
    import cv2

    real_imwrite = cv2.imwrite
    calls = {"n": 0}

    def flaky_imwrite(*args, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            if mode == "raises":
                raise OSError("disk full")
            return False
        return real_imwrite(*args, **kw)

    monkeypatch.setattr(cv2, "imwrite", flaky_imwrite)
    img = np.zeros((8, 8), np.uint8)
    rec = tmp_path / "rec"
    with pytest.raises(OSError):
        with RecordingWriter(rec, source="sim") as w:
            for i in range(70):
                w.write_frame(StereoFrame(i, 10.0 + i / 30, img, img))
    assert calls["n"] == 70
    assert RecordingReader(rec).meta["n_frames"] == 70  # the folder is still closed properly


def test_recording_lists_only_written_frames_before_close(tmp_path, monkeypatch):
    """As after a crash mid-scan: before close(), after a flush, every frame listed in frames.csv
    has a complete, readable PNG, even while later PNGs are still half written."""
    import threading
    import time

    import cv2

    real_imwrite = cv2.imwrite
    release = threading.Event()
    done = []

    def slow_imwrite(path, img, params):
        if int(path[-10:-4]) >= 5:  # frames 5..7: half a file on disk, then wait
            ok, buf = cv2.imencode(".png", img, params)
            with open(path, "wb") as fh:
                fh.write(buf.tobytes()[:20])
            release.wait(10)
        ok = real_imwrite(path, img, params)
        done.append(path)
        return ok

    monkeypatch.setattr(cv2, "imwrite", slow_imwrite)
    rng = np.random.default_rng(4)
    rec = tmp_path / "rec"
    w = RecordingWriter(rec, source="sim")
    try:
        for i in range(8):
            w.write_frame(StereoFrame(i, 10.0 + i / 30, rng.integers(0, 255, (40, 64), dtype=np.uint8),
                                      rng.integers(0, 255, (40, 64), dtype=np.uint8)))
        t_end = time.monotonic() + 10
        while len(done) < 5 and time.monotonic() < t_end:
            time.sleep(0.01)
        w.flush()
        r = RecordingReader(rec)  # what a reader sees after a crash at this point
        assert len(r) == 5
        for i in range(len(r)):
            assert r.frame(i).index == i
    finally:
        release.set()
        w.close()
    r = RecordingReader(rec)
    assert len(r) == 8 and list(r.indices) == list(range(8))


def _small_recording(path, n=9):
    rng = np.random.default_rng(3)
    with RecordingWriter(path, source="sim") as w:
        for i in range(n):
            w.write_frame(StereoFrame(i, 10.0 + i / 30, rng.integers(0, 255, (40, 64), dtype=np.uint8),
                                      rng.integers(0, 255, (40, 64), dtype=np.uint8)))
    return RecordingReader(path)


def _reader_threads():
    import threading

    return [t for t in threading.enumerate() if t.name == "ohs-frame-reader"]


@pytest.mark.parametrize("ahead", [0, 1, 2, 5, 50])
@pytest.mark.parametrize("positions", [list(range(9)), [0, 2, 4, 6, 8], [7, 1, 1, 5], [4], []])
def test_iter_ahead_gives_the_same_frames_in_the_same_order(tmp_path, ahead, positions):
    """Reading ahead in a thread changes when a frame is read, never which frame comes next."""
    r = _small_recording(tmp_path / "rec")
    got = list(r.iter_ahead(positions, ahead))
    assert [f.index for f in got] == [r.frame(i).index for i in positions]
    for f, i in zip(got, positions):
        ref = r.frame(i)
        assert f.t_host == ref.t_host and np.array_equal(f.left, ref.left) and np.array_equal(f.right, ref.right)
    assert not _reader_threads()


def test_iter_ahead_raises_a_read_error_at_its_frame(tmp_path):
    """A frame that cannot be read fails the pass at that frame, after the frames before it."""
    r = _small_recording(tmp_path / "rec")
    (tmp_path / "rec" / "frames" / r._files[4]).unlink()
    with pytest.raises(IOError):
        r.frame(4)  # the plain read fails the same way
    got = []
    with pytest.raises(IOError):
        for f in r.iter_ahead(range(9), 2):
            got.append(f.index)
    assert got == [0, 1, 2, 3]
    assert not _reader_threads()


def test_iter_ahead_stops_reading_when_closed_early(tmp_path, monkeypatch):
    """A cancelled or failed pass closes the iterator: the thread ends and reads no further."""
    r = _small_recording(tmp_path / "rec")
    read = []
    plain = r.frame
    monkeypatch.setattr(r, "frame", lambda i: (read.append(i), plain(i))[1])
    frames = r.iter_ahead(range(9), 2)
    assert next(frames).index == 0
    frames.close()
    assert not _reader_threads()
    assert len(read) <= 4 and read == list(range(len(read)))  # frame 0, two in the queue, one in hand


def test_recording_temperature_events_and_summary(tmp_path):
    rec = tmp_path / "rec"
    with RecordingWriter(rec, source="camera") as w:
        for t, value in ((12.0, "31.25"), (10.0, "30.50"), (11.0, "nan"), (13.0, "hot"), (14.0, "29.75")):
            w.write_event(Event(t, "temperature", value))  # arrival order; unreadable values are skipped
        w.write_event(Event(10.5, "button", "down"))
    r = RecordingReader(rec)
    temp = r.temperature()
    assert temp["t_host"].tolist() == [10.0, 12.0, 14.0] and temp["celsius"].tolist() == [30.5, 31.25, 29.75]
    assert r.meta["temperature_c"] == {"first": 31.25, "last": 29.75, "min": 29.75, "max": 31.25, "n": 3}
    assert [e.kind for e in r.events()].count("temperature") == 5  # the log itself keeps every line
