"""Camera tests: StereoCamera against a fake ``cv2.VideoCapture``, ReplayCamera on a real recording."""
import logging
import time

import cv2
import numpy as np
import pytest

from ohs.devices import camera as cam_mod
from ohs.devices.camera import CameraParams, ReplayCamera, StereoCamera, list_cameras
from ohs.io.recording import RecordingWriter
from ohs.types import StereoFrame

LEFT_BGR = (10, 50, 90)
RIGHT_BGR = (200, 100, 0)
W, H = cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT


def gray_of(bgr) -> int:
    return int(cv2.cvtColor(np.array([[bgr]], np.uint8), cv2.COLOR_BGR2GRAY)[0, 0])


class FakeCapture:
    """cv2.VideoCapture stand-in producing synthetic BGR frames.

    Side-by-side mode: left half LEFT_BGR, right half RIGHT_BGR. With ``single`` (two-camera
    fallback) the whole image is LEFT_BGR for index 0 and RIGHT_BGR otherwise. Pixel (0, 0) of each
    half holds the grab counter (equal channels, so it survives the gray conversion exactly).
    """

    instances: list = []
    available = {0, 1}
    fail_apis: set = set()
    fps = 200.0
    stall_after: int | None = None
    single = False
    backend_names = {cv2.CAP_DSHOW: "DSHOW", cv2.CAP_MSMF: "MSMF", cv2.CAP_V4L2: "V4L2"}

    def __init__(self, index, api=cv2.CAP_ANY):
        self.index, self.api = index, api
        self.calls: list[tuple[int, float]] = []
        self.props = {W: 640.0, H: 480.0, cv2.CAP_PROP_FPS: 30.0,
                      cv2.CAP_PROP_FOURCC: float(cv2.VideoWriter_fourcc(*"YUY2"))}
        self.opened = index in self.available and api not in self.fail_apis
        self.n = 0
        self.released = False
        type(self).instances.append(self)

    def isOpened(self):
        return self.opened and not self.released

    def set(self, prop, value):
        self.calls.append((prop, value))
        self.props[prop] = float(value)
        return True

    def get(self, prop):
        return self.props.get(prop, 0.0)

    def getBackendName(self):
        return self.backend_names.get(self.api, "ANY")

    def grab(self):
        time.sleep(1.0 / self.fps)
        if self.stall_after is not None and self.n >= self.stall_after:
            return False
        self.n += 1
        return True

    def retrieve(self):
        w, h = int(self.props[W]), int(self.props[H])
        img = np.empty((h, w, 3), np.uint8)
        c = (self.n - 1) % 250
        if self.single:
            img[:] = LEFT_BGR if self.index == 0 else RIGHT_BGR
            img[0, 0] = c
        else:
            img[:, : w // 2] = LEFT_BGR
            img[:, w // 2 :] = RIGHT_BGR
            img[0, 0] = img[0, w // 2] = c
        return True, img

    def release(self):
        self.released = True


@pytest.fixture
def fake_cv(monkeypatch):
    class Cap(FakeCapture):  # fresh class state per test
        instances = []
        available = {0, 1}
        fail_apis = set()

    monkeypatch.setattr(cv2, "VideoCapture", Cap)
    return Cap


SMALL = dict(width=320, height=100, fps=60.0)


# --------------------------------------------------------------------------- StereoCamera
def test_backend_candidates():
    assert cam_mod._backend_candidates("auto", "win32") == [cv2.CAP_DSHOW, cv2.CAP_MSMF]
    assert cam_mod._backend_candidates("auto", "linux") == [cv2.CAP_V4L2, cv2.CAP_ANY]
    assert cam_mod._backend_candidates("auto", "darwin") == [cv2.CAP_ANY]
    assert cam_mod._backend_candidates("DShow") == [cv2.CAP_DSHOW]
    assert cam_mod._backend_candidates("v4l2") == [cv2.CAP_V4L2]
    with pytest.raises(ValueError):
        cam_mod._backend_candidates("gstreamer")


def test_open_sets_fourcc_before_size_and_fps_then_exposure_and_gain(fake_cv):
    p = CameraParams(backend="dshow", exposure=-7, gain=5, **SMALL)
    with StereoCamera(p) as cam:
        cap = fake_cv.instances[0]
        assert cap.api == cv2.CAP_DSHOW
        props = [c[0] for c in cap.calls]
        assert props[:4] == [cv2.CAP_PROP_FOURCC, W, H, cv2.CAP_PROP_FPS]  # format first
        assert cap.calls[0][1] == cv2.VideoWriter_fourcc(*"MJPG")
        assert dict(cap.calls)[W] == 320 and dict(cap.calls)[H] == 100 and dict(cap.calls)[cv2.CAP_PROP_FPS] == 60
        assert cap.calls[4:] == [(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25), (cv2.CAP_PROP_EXPOSURE, -7.0),
                                 (cv2.CAP_PROP_GAIN, 5.0)]
        info = cam.info
        assert (info["width"], info["height"], info["fps"]) == (320, 100, 60.0)
        assert info["backend"] == "DSHOW" and info["fourcc"] == "MJPG" and info["synced"]
        cam.set_exposure(-6)
        cam.set_gain(2)
        assert cap.calls[-3:] == [(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25), (cv2.CAP_PROP_EXPOSURE, -6.0),
                                  (cv2.CAP_PROP_GAIN, 2.0)]
    assert cap.released and p.exposure == -7  # the caller's params are not modified


def test_frames_are_gray_and_split_and_swapped(fake_cv):
    gl, gr = gray_of(LEFT_BGR), gray_of(RIGHT_BGR)
    with StereoCamera(CameraParams(**SMALL)) as cam:
        f = cam.read()
    assert f.left.shape == (100, 160) and f.right.shape == (100, 160) and f.left.dtype == np.uint8
    assert np.all(f.left[1:] == gl) and np.all(f.right[1:] == gr) and gl != gr
    with StereoCamera(CameraParams(swap_lr=True, **SMALL)) as cam:
        f = cam.read()
    assert np.all(f.left[1:] == gr) and np.all(f.right[1:] == gl)


def test_read_never_repeats_indices_and_keeps_newest(fake_cv):
    with StereoCamera(CameraParams(**SMALL)) as cam:
        frames = []
        for i in range(12):
            frames.append(cam.read(timeout=1.0))
            if i % 3 == 2:
                time.sleep(0.06)  # slow consumer: frames pile up, only the newest is returned
        info = cam.info
    idx = [f.index for f in frames]
    assert all(b > a for a, b in zip(idx, idx[1:]))  # strictly increasing, never repeated
    assert max(b - a for a, b in zip(idx, idx[1:])) >= 3  # older frames were skipped
    assert all(int(f.left[0, 0]) == f.index % 250 for f in frames)  # index = grab counter
    assert all(b.t_host > a.t_host for a, b in zip(frames, frames[1:]))
    assert info["frames"] >= idx[-1] + 1 and 20 < info["measured_fps"] < 300


def test_read_timeout(fake_cv):
    fake_cv.stall_after = 3
    with StereoCamera(CameraParams(**SMALL)) as cam:
        last = cam.read()
        while last.index < 2:  # consume the 3 frames the camera delivers before it stalls
            last = cam.read(timeout=1.0)
        assert last.index == 2
        t0 = time.perf_counter()
        with pytest.raises(TimeoutError):
            cam.read(timeout=0.2)
        assert time.perf_counter() - t0 >= 0.19


def test_dead_camera_raises(fake_cv, caplog):
    fake_cv.stall_after = 2
    with StereoCamera(CameraParams(**SMALL), dead_after_s=0.3) as cam:
        cam.read()
        with pytest.raises(RuntimeError, match="delivered no frame"):
            for _ in range(10):  # plain timeouts until the grab thread gives up after 0.3 s
                try:
                    cam.read(timeout=0.2)
                except TimeoutError:
                    pass
    assert "delivered no frame" in caplog.text


def test_backend_fallback_and_open_errors(fake_cv):
    first, second = cam_mod._backend_candidates("auto")
    fake_cv.fail_apis = {first}
    with StereoCamera(CameraParams(**SMALL)) as cam:
        # a frame arrives through the second backend; not necessarily frame 0: the fake grabs every
        # 5 ms and read() returns the newest, so a busy machine can skip the first one
        assert cam.read().index >= 0
    assert [c.api for c in fake_cv.instances] == [first, second]
    with pytest.raises(RuntimeError, match="could not open camera 5"):
        StereoCamera(CameraParams(index=5, **SMALL)).open()
    with pytest.raises(ValueError):
        StereoCamera(CameraParams(fourcc="MJPEG", **SMALL)).open()
    with pytest.raises(RuntimeError, match="not open"):
        StereoCamera(CameraParams(**SMALL)).read()


def test_no_first_frame_times_out(fake_cv):
    fake_cv.stall_after = 0
    cam = StereoCamera(CameraParams(**SMALL), first_frame_timeout=0.2)
    with pytest.raises(TimeoutError, match="no frame"):
        cam.open()
    assert all(c.released for c in fake_cv.instances)


def test_two_camera_fallback(fake_cv, caplog):
    fake_cv.single = True
    gl, gr = gray_of(LEFT_BGR), gray_of(RIGHT_BGR)
    with caplog.at_level(logging.WARNING, logger="ohs.devices.camera"):
        with StereoCamera(CameraParams(second_index=1, **SMALL)) as cam:
            f = cam.read()
            info = cam.info
    assert "NOT hardware-synchronised" in caplog.text
    assert [c.index for c in fake_cv.instances] == [0, 1]
    assert all(dict(c.calls)[W] == 160 for c in fake_cv.instances)  # half the side-by-side width each
    assert f.left.shape == (100, 160) and np.all(f.left[1:] == gl) and np.all(f.right[1:] == gr)
    assert info["width"] == 320 and not info["synced"]
    with StereoCamera(CameraParams(second_index=1, swap_lr=True, **SMALL)) as cam:
        f = cam.read()
    assert np.all(f.left[1:] == gr) and np.all(f.right[1:] == gl)


def test_v4l2_exposure_semantics(fake_cv):
    class Rejecting(fake_cv):
        def set(self, prop, value):  # a driver that only takes the raw menu value
            super().set(prop, value)
            return not (prop == cv2.CAP_PROP_AUTO_EXPOSURE and value == 0.25)

    cap = Rejecting(0, cv2.CAP_V4L2)
    cam_mod._set_exposure(cap, "V4L2", 50)  # 50 x 100 us = 5 ms
    assert cap.calls == [(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25), (cv2.CAP_PROP_AUTO_EXPOSURE, 1.0),
                         (cv2.CAP_PROP_EXPOSURE, 50.0)]


def test_list_cameras(fake_cv):
    fake_cv.available = {0, 2}
    found = list_cameras(max_index=4, backend="dshow")
    assert [c["index"] for c in found] == [0, 2]
    assert found[0]["backend"] == "DSHOW" and found[0]["fourcc"] == "YUY2"
    assert (found[0]["width"], found[0]["height"]) == (640, 480)
    assert all(c.released for c in fake_cv.instances) and len(fake_cv.instances) == 4


@pytest.mark.hardware
def test_real_camera_reads_frames():
    cams = list_cameras(4)
    if not cams:
        pytest.skip("no camera connected")
    with StereoCamera(CameraParams(index=cams[0]["index"])) as cam:
        f1, f2 = cam.read(2.0), cam.read(2.0)
        info = cam.info
    assert f2.index > f1.index and f2.t_host > f1.t_host
    assert f1.left.shape == f1.right.shape and f1.left.dtype == np.uint8
    assert info["width"] == 2 * f1.left.shape[1]


# --------------------------------------------------------------------------- ReplayCamera
@pytest.fixture
def recording(tmp_path):
    rng = np.random.default_rng(0)
    frames = [StereoFrame(2 * i, 100.0 + 0.01 * i, rng.integers(0, 255, (40, 64), dtype=np.uint8),
                          rng.integers(0, 255, (40, 64), dtype=np.uint8)) for i in range(8)]  # 100 fps, gaps
    with RecordingWriter(tmp_path / "rec", source="camera") as w:
        for f in frames:
            w.write_frame(f)
    return tmp_path / "rec", frames


def test_replay_in_order_then_eof(recording):
    path, frames = recording
    with ReplayCamera(path, realtime=False) as cam:
        got = [cam.read() for _ in frames]
        assert cam.finished
        with pytest.raises(EOFError):
            cam.read()
        info = cam.info
    assert [g.index for g in got] == [f.index for f in frames]
    assert [g.t_host for g in got] == pytest.approx([f.t_host for f in frames], abs=1e-6)
    assert all(np.array_equal(g.left, f.left) and np.array_equal(g.right, f.right) for g, f in zip(got, frames))
    assert (info["width"], info["height"], info["backend"]) == (128, 40, "replay")
    assert info["fps"] == pytest.approx(100.0, rel=1e-3) and info["frames"] == 8
    with pytest.raises(RuntimeError):
        cam.read()


def test_replay_loop_keeps_indices_and_time_monotonic(recording):
    path, frames = recording
    with ReplayCamera(path, realtime=False, loop=True) as cam:
        got = [cam.read() for _ in range(20)]
    idx = [g.index for g in got]
    ts = [g.t_host for g in got]
    assert all(b > a for a, b in zip(idx, idx[1:])) and all(b > a for a, b in zip(ts, ts[1:]))
    span = frames[-1].index + 1
    assert idx[8] == frames[0].index + span and idx[8] % span == frames[0].index
    assert np.array_equal(got[8].left, frames[0].left)
    assert ts[8] - ts[7] == pytest.approx(0.01, abs=1e-6)  # one frame period across the wrap


def test_replay_realtime_pacing_and_newest_frame(recording):
    path, frames = recording
    with ReplayCamera(path, realtime=True) as cam:
        t0 = time.perf_counter()
        first = cam.read()
        second = cam.read()  # due 10 ms after the first
        dt = time.perf_counter() - t0
        time.sleep(0.045)  # consumer falls behind: older frames are skipped like on a live camera
        third = cam.read()
        rest = []
        while True:
            try:
                rest.append(cam.read(timeout=0.5))
            except EOFError:
                break
        total = time.perf_counter() - t0
    assert (first.index, second.index) == (0, 2) and 0.007 < dt < 0.5
    assert third.index >= 8
    idx = [f.index for f in [first, second, third, *rest]]
    assert idx == sorted(set(idx)) and idx[-1] == frames[-1].index
    assert 0.065 < total < 2.0


def test_replay_realtime_timeout(tmp_path):
    img = np.zeros((8, 8), np.uint8)
    with RecordingWriter(tmp_path / "slow") as w:  # two frames 0.5 s apart
        w.write_frame(StereoFrame(0, 10.0, img, img))
        w.write_frame(StereoFrame(1, 10.5, img, img))
    with ReplayCamera(tmp_path / "slow", realtime=True) as cam:
        assert cam.read().index == 0
        t0 = time.perf_counter()
        with pytest.raises(TimeoutError):
            cam.read(timeout=0.05)  # the next frame is not due for another ~0.45 s
        assert time.perf_counter() - t0 >= 0.045
        assert cam.read(timeout=1.0).index == 1
        with pytest.raises(EOFError):
            cam.read()


def test_replay_restamp(recording):
    path, _ = recording
    with ReplayCamera(path, realtime=False, restamp=True) as cam:
        t = time.perf_counter()
        f = cam.read()
    assert abs(f.t_host - t) < 0.5
