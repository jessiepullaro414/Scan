"""Live sessions (``ohs scan`` / ``record`` / ``preview`` / ``calibrate`` capture) without hardware.

Fakes used here (and imported by test_app_cli.py):

* :class:`FakeCamera`: ``StereoCamera`` stand-in delivering textured frames at a fixed rate with
  newest-frame semantics, optional brightness callback and fixed images.
* :class:`SimPico`: the REAL firmware state machine (``firmware/main.py``) with fake PWM, button
  and IMU, behind an in-memory pyserial-like port, driven by the real ``ohs.devices.pico.Pico``
  client (as in test_devices_pico.py).
* :func:`stub_modules`: temporarily replaces F2's ``ohs.app.pipeline`` / ``process`` /
  ``calib_flow`` with stubs (sys.modules and the package attribute), restoring them afterwards.
* :func:`gui_fakes`: scripted ``cv2.namedWindow/imshow/waitKey/getWindowProperty``.
"""
from __future__ import annotations

import contextlib
import importlib
import math
import sys
import threading
import time
import types
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pytest

import ohs.app
from ohs.app import cli, live
from ohs.devices.pico import Pico
from ohs.io.recording import RecordingReader
from ohs.sim.nominal import nominal_calibration
from ohs.types import ScannerCalibration, StereoFrame, TrackResult

FW_DIR = Path(__file__).resolve().parents[2] / "firmware"


def _load_firmware() -> types.ModuleType:
    path = FW_DIR / "main.py"
    mod = types.ModuleType("ohs_fw_main_app_tests")
    mod.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), mod.__dict__)
    return mod


FW = _load_firmware()


# --------------------------------------------------------------------------- fake Pico (real firmware logic)
class FakePWM:
    def __init__(self) -> None:
        self.duty = None

    def duty_u16(self, d: int) -> None:
        self.duty = d


class FakePin:
    def __init__(self) -> None:
        self.level = 1  # released (pull-up)

    def value(self) -> int:
        return self.level


class FakeImu:
    """LSM6DSOX stand-in: ODR-paced samples of a scanner at rest (z up)."""

    name = "LSM6DSOX"
    gyro_scale = math.radians(0.035)
    accel_scale = 0.244e-3 * 9.80665
    RAW = (2, -1, 1, 0, 0, 4096)

    def __init__(self, clock) -> None:
        self.clock = clock
        self.rate = 0
        self.t_start = 0
        self.consumed = -1

    def set_rate(self, rate: int) -> None:
        self.rate, self.t_start, self.consumed = rate, self.clock(), -1

    def _latest(self) -> int:
        return (self.clock() - self.t_start) * self.rate // 1_000_000 - 1

    def data_ready(self) -> bool:
        return self.rate > 0 and self._latest() > self.consumed

    def read_raw(self) -> tuple:
        self.consumed = self._latest()
        return self.RAW

    def read_temp_c100(self) -> int:
        return 3010  # 30.10 degC


class HostPort:
    """pyserial-like host end of the in-memory link."""

    def __init__(self, dev: "SimPico") -> None:
        self.dev = dev
        self.timeout = 0.05
        self._cond = threading.Condition()
        self._buf = bytearray()
        self.closed = False

    def push(self, data: bytes) -> None:
        with self._cond:
            self._buf += data
            self._cond.notify_all()

    @property
    def in_waiting(self) -> int:
        with self._cond:
            return len(self._buf)

    def read(self, size: int = 1) -> bytes:
        with self._cond:
            if not self._buf:
                self._cond.wait(self.timeout)
            out = bytes(self._buf[:size])
            del self._buf[:size]
            return out

    def write(self, data: bytes) -> int:
        self.dev.host_write(bytes(data))
        return len(data)

    def reset_input_buffer(self) -> None:
        with self._cond:
            self._buf.clear()

    def close(self) -> None:
        self.closed = True


class SimPico:
    """``firmware/main.py`` Firmware + fake hardware behind a :class:`HostPort`."""

    def __init__(self, with_imu: bool = True) -> None:
        t0 = time.perf_counter()
        self.clock = lambda: int((time.perf_counter() - t0) * 1e6) + 5_000_000
        self.pwms = [FakePWM() for _ in range(3)]
        self.button = FakePin()
        self.imu = FakeImu(self.clock) if with_imu else None
        self.lock = threading.Lock()
        self.port = HostPort(self)
        self.received: list[str] = []
        self._rx = ""
        self.fw = FW.Firmware(self.pwms, self.button, self.imu, self.clock, self._device_write)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="sim-pico", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self.lock:
                self.fw.process("")
            time.sleep(0.0003)

    def host_write(self, data: bytes) -> None:
        text = data.decode("ascii")
        with self.lock:
            self._rx += text
            while "\n" in self._rx:
                line, self._rx = self._rx.split("\n", 1)
                if line.strip():
                    self.received.append(line.strip())
            self.fw.process(text)

    def _device_write(self, s: str) -> None:
        self.port.push(s.replace("\n", "\r\n").encode("ascii"))

    def duties(self) -> list:
        return [p.duty for p in self.pwms]

    def emits(self) -> list[str]:
        return [ln for ln in self.received if ln.upper().startswith("EMIT")]

    def press(self, hold: float = 0.08) -> None:
        self.button.level = 0
        time.sleep(hold)
        self.button.level = 1

    def pico(self, params=None) -> Pico:
        from ohs.devices.pico import PicoParams

        return Pico(params or PicoParams(), transport=self.port)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)


# --------------------------------------------------------------------------- fake camera
class FakeCamera:
    """``StereoCamera`` stand-in: frames at ``fps`` with newest-frame semantics (index gaps when late)."""

    def __init__(self, size=(160, 100), fps: float = 60.0, brightness=None, images=None, backend: str = "FAKE") -> None:
        self.size = (int(size[0]), int(size[1]))
        self.fps = float(fps)
        self.brightness = brightness
        self.images = images
        self.backend = backend
        self.exposures: list[float] = []
        self.opened = False
        self.closed = False
        self.n_read = 0
        self._i = 0
        self._t0 = 0.0
        w, h = self.size
        rng = np.random.default_rng(0)
        self._left = rng.integers(20, 120, (h, w), dtype=np.uint8)
        self._right = np.roll(self._left, -3, axis=1)

    def open(self) -> None:
        self.opened, self.closed = True, False
        self._t0, self._i = time.perf_counter(), 0

    def read(self, timeout: float = 1.0) -> StereoFrame:
        if not self.opened:
            raise RuntimeError("camera is not open")
        now = time.perf_counter()
        due = self._t0 + self._i / self.fps
        if due > now:
            if due - now > timeout:
                time.sleep(timeout)
                raise TimeoutError("no frame")
            time.sleep(due - now)
        else:
            self._i = max(self._i, int((now - self._t0) * self.fps))
        i = self._i
        self._i += 1
        left, right = self.images if self.images is not None else (self._left, self._right)
        if self.brightness is not None:
            b = int(self.brightness())
            left = np.clip(left.astype(np.int16) + b, 0, 255).astype(np.uint8)
            right = np.clip(right.astype(np.int16) + b, 0, 255).astype(np.uint8)
        self.n_read += 1
        return StereoFrame(i, time.perf_counter(), left, right)

    def set_exposure(self, value: float) -> None:
        self.exposures.append(float(value))

    def set_gain(self, value: float) -> None:
        pass

    @property
    def info(self) -> dict:
        w, h = self.size
        return {"backend": self.backend, "width": 2 * w, "height": h, "fps": self.fps, "fourcc": "MJPG", "open": self.opened}

    def close(self) -> None:
        self.opened, self.closed = False, True


# --------------------------------------------------------------------------- module stubs for F2's code
@contextlib.contextmanager
def stub_modules(**mods: types.ModuleType):
    """Install stub modules ``ohs.app.<name>`` for the duration of the block.

    Also hides ``ohs.app.process`` while ``pipeline`` is stubbed, so a module imported against the
    stub never leaks into later tests.
    """
    names = set(mods) | ({"process"} if "pipeline" in mods else set())
    saved = {n: (sys.modules.get(f"ohs.app.{n}"), getattr(ohs.app, n, None)) for n in names}
    try:
        for n in names:
            sys.modules.pop(f"ohs.app.{n}", None)
            if hasattr(ohs.app, n):
                delattr(ohs.app, n)
        for n, m in mods.items():
            sys.modules[f"ohs.app.{n}"] = m
            setattr(ohs.app, n, m)
        yield
    finally:
        for n, (mod, attr) in saved.items():
            if mod is None:
                sys.modules.pop(f"ohs.app.{n}", None)
            else:
                sys.modules[f"ohs.app.{n}"] = mod
            if attr is None:
                if hasattr(ohs.app, n):
                    delattr(ohs.app, n)
            else:
                setattr(ohs.app, n, attr)


def make_stub_pipeline() -> types.ModuleType:
    """``ohs.app.pipeline`` with the agreed interface; records how it is used."""
    from ohs.vision.laser import LaserLines
    from ohs.vision.rectify import Rectifier

    mod = types.ModuleType("ohs.app.pipeline")

    @dataclass
    class PipelineParams:
        mode: str = "markerless"
        scale: float = 1.0
        use_imu: bool = True
        use_lasers: bool = True
        integrate_depth: bool = True
        depth: object = None
        laser: object = None
        fusion: object = None
        markers: object = None
        recon: str = "auto"

    @dataclass
    class FrameResult:
        index: int
        t: float
        left_r: np.ndarray
        right_r: np.ndarray
        depth: np.ndarray
        lines: object
        laser_points: dict
        track: TrackResult
        timings_ms: dict

    class FramePipeline:
        instances: list = []

        def __init__(self, calib, params=None) -> None:
            self.calib, self.params = calib, params or PipelineParams()
            self.threads = {threading.current_thread().name}
            self.n_process = self.n_imu = self.resets = 0
            self.frame_indices: list[int] = []
            self.closed = False
            self.rect = Rectifier(calib.stereo, self.params.scale)
            FramePipeline.instances.append(self)

        def _seen(self) -> None:
            self.threads.add(threading.current_thread().name)

        def add_imu(self, samples) -> None:
            self._seen()
            self.n_imu += len(samples)

        def process(self, frame) -> FrameResult:
            self._seen()
            self.n_process += 1
            self.frame_indices.append(frame.index)
            left, right = self.rect.rectify(frame.left, frame.right)
            h, w = left.shape
            depth = np.full((h, w), 300.0, np.float32)
            depth[: h // 4] = 0.0
            v = np.arange(h, dtype=np.float64)
            vert = np.stack([np.full(h, w / 2.0), v], axis=1)
            lines = LaserLines(vert, np.zeros((0, 2)), np.full(h, 80.0), np.zeros(0), np.zeros(h, np.int32), np.zeros(0, np.int32))
            ok = self.n_process % 3 != 0
            track = TrackResult(np.eye(4), ok, {} if ok else {"reason": "few_inliers"})
            return FrameResult(frame.index, frame.t_host, left, right, depth, lines, {"vertical": np.zeros((7, 3))}, track,
                               {"total": 1.0})

        def reset(self) -> None:
            self._seen()
            self.resets += 1

        def close(self) -> None:
            self._seen()
            self.closed = True

    mod.PipelineParams, mod.FrameResult, mod.FramePipeline = PipelineParams, FrameResult, FramePipeline
    return mod


@pytest.fixture
def stub_pipeline():
    mod = make_stub_pipeline()
    with stub_modules(pipeline=mod):
        yield mod


# --------------------------------------------------------------------------- GUI fakes
class GuiScript:
    """Scripted OpenCV HighGUI: ``keys(n_call, session_state) -> key``."""

    def __init__(self, keys, closed_after: int | None = None) -> None:
        self.keys = keys
        self.closed_after = closed_after
        self.calls = 0
        self.images: list[np.ndarray] = []
        self.windows: list[str] = []
        self.resizes: list[tuple] = []

    def install(self, monkeypatch) -> "GuiScript":
        monkeypatch.setattr(cv2, "namedWindow", lambda name, flags=0: self.windows.append(name))
        monkeypatch.setattr(cv2, "imshow", lambda name, img: self.images.append(np.asarray(img).copy()))
        monkeypatch.setattr(cv2, "resizeWindow", lambda name, w, h: self.resizes.append((w, h)))
        monkeypatch.setattr(cv2, "destroyWindow", lambda *a: None)
        monkeypatch.setattr(cv2, "waitKey", self._wait)
        monkeypatch.setattr(cv2, "getWindowProperty", self._prop)
        return self

    def _wait(self, ms: int = 0) -> int:
        self.calls += 1
        time.sleep(max(1, ms) / 1000.0)
        k = self.keys(self.calls)
        return -1 if k is None else (ord(k) if isinstance(k, str) else int(k))

    def _prop(self, name, prop) -> float:
        return 0.0 if self.closed_after is not None and self.calls >= self.closed_after else 1.0


# --------------------------------------------------------------------------- shared fixtures / helpers
@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Never touch the real ~/.ohs: home and the config path point into tmp_path."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OHS_CONFIG", str(tmp_path / "home" / "no-config.toml"))


@pytest.fixture(scope="module")
def sim_rec(tmp_path_factory) -> Path:
    from ohs.sim.datasets import make_scan_recording

    return make_scan_recording(tmp_path_factory.mktemp("sim") / "rec", n_frames=10, image_size=(160, 100), depth_every=0)


@pytest.fixture
def sim():
    s = SimPico()
    yield s
    s.close()


def _devices(monkeypatch, camera, sim_pico=None) -> None:
    monkeypatch.setattr(live, "make_camera", lambda cfg, replay=None: camera)
    if sim_pico is not None:
        monkeypatch.setattr(live, "make_pico", lambda cfg: sim_pico.pico(cfg.pico))


def _calib_file(tmp_path, size=(160, 100)) -> Path:
    p = tmp_path / "calibration.json"
    nominal_calibration(size).save(p)
    return p


def _wait_for(cond, timeout: float = 10.0) -> None:
    t0 = time.perf_counter()
    while not cond():
        if time.perf_counter() - t0 > timeout:
            raise AssertionError("condition not reached")
        time.sleep(0.01)


# --------------------------------------------------------------------------- scan
def test_scan_replay_headless_records_frames_and_calibration(tmp_path, sim_rec, stub_pipeline, capsys):
    out = tmp_path / "scan"
    rc = cli.main(["scan", "--replay", str(sim_rec), "--no-pico", "--no-gui", "--max-frames", "5", "--out", str(out)])
    assert rc == 0
    r = RecordingReader(out)
    src = RecordingReader(sim_rec)
    assert len(r) == 5
    assert set(r.indices) <= set(src.indices) and np.all(np.diff(r.indices) > 0)
    assert r.meta["purpose"] == "scan" and r.meta["mode"] == "markerless" and r.meta["n_frames"] == 5
    assert r.meta["source"] == "sim" and r.meta["replay_of"] == str(sim_rec)
    snap = r.calibration()
    assert snap is not None and np.allclose(snap.stereo.P1, src.calibration().stereo.P1)  # calibration snapshot
    assert len(r.imu_arrays()["t_host"]) > 100  # IMU fed from ReplayCamera.reader
    np.testing.assert_allclose(r.times(), [src.times()[list(src.indices).index(i)] for i in r.indices])
    (pipe,) = stub_pipeline.FramePipeline.instances
    assert pipe.threads == {"ohs-process"}  # created, used and closed in ONE thread
    assert pipe.closed and 1 <= pipe.n_process <= 5 and pipe.n_imu > 0 and pipe.resets == 1
    p = pipe.params
    assert (p.mode, p.scale, p.use_imu, p.use_lasers) == ("markerless", 0.5, True, True)
    assert (p.depth.min_depth_mm, p.depth.max_depth_mm) == (150.0, 450.0) and p.fusion is None and p.markers.tag_mm == 20.0
    outp = capsys.readouterr().out
    assert "Recorded 5 frames" in outp and "ohs process" in outp


def test_scan_trigger_starts_and_stops_with_emitters(tmp_path, sim, stub_pipeline, monkeypatch, capsys):
    cam = FakeCamera(fps=60)
    _devices(monkeypatch, cam, sim)
    out = tmp_path / "scan"

    def presser() -> None:
        _wait_for(lambda: sim.fw.imu_rate == 416)  # session ready (IMU streaming)
        time.sleep(0.3)
        sim.press()
        _wait_for(lambda: (out / "meta.json").exists())
        time.sleep(0.6)
        sim.press()

    th = threading.Thread(target=presser, daemon=True)
    th.start()
    rc = cli.main(["--calib", str(_calib_file(tmp_path)), "scan", "--no-gui", "--out", str(out)])
    th.join(5)
    assert rc == 0
    r = RecordingReader(out)
    assert 10 <= len(r) <= 80
    assert "EMIT 100 100 100" in sim.emits() and sim.emits()[-1] == "EMIT 0 0 0"
    assert sim.duties() == [0, 0, 0]
    assert r.meta["emitters"] == [100.0, 100.0, 100.0] and r.meta["pico"]["firmware"] == "ohs-fw"
    assert len(r.imu_arrays()["t_host"]) > 50
    assert ("button", "down") in [(e.kind, e.value) for e in r.events()]  # the stop press is recorded
    assert cam.closed and sim.port.closed is False
    (pipe,) = stub_pipeline.FramePipeline.instances
    assert pipe.threads == {"ohs-process"} and pipe.n_process >= 1 and pipe.closed
    assert "live tracking:" in capsys.readouterr().out


def test_markers_mode_dims_the_dot_projector(tmp_path, sim, stub_pipeline, monkeypatch):
    _devices(monkeypatch, FakeCamera(fps=60), sim)
    out = tmp_path / "scan"
    rc = cli.main(["--calib", str(_calib_file(tmp_path)), "scan", "--mode", "markers", "--no-gui", "--start-now",
                   "--max-frames", "5", "--out", str(out)])
    assert rc == 0
    assert "EMIT 100 100 25" in sim.emits() and sim.emits()[-1] == "EMIT 0 0 0"
    r = RecordingReader(out)
    assert len(r) == 5 and r.meta["emitters"] == [100.0, 100.0, 25.0] and r.meta["mode"] == "markers"
    assert stub_pipeline.FramePipeline.instances[0].params.mode == "markers"


def test_ctrl_c_switches_emitters_off_and_flushes_the_recording(tmp_path, sim, stub_pipeline, monkeypatch, capsys):
    _devices(monkeypatch, FakeCamera(fps=60), sim)

    def tick(self) -> None:
        if self.n_recorded >= 6:
            raise KeyboardInterrupt

    monkeypatch.setattr(live.LiveSession, "tick", tick)
    out = tmp_path / "scan"
    rc = cli.main(["--calib", str(_calib_file(tmp_path)), "scan", "--no-gui", "--start-now", "--out", str(out)])
    assert rc == 130
    r = RecordingReader(out)
    assert len(r) >= 6 and r.meta["n_frames"] == len(r)  # meta written: the recording was closed
    assert all((out / "frames" / f"{i:06d}.png").exists() for i in r.indices)
    assert sim.duties() == [0, 0, 0] and sim.emits()[-1] == "EMIT 0 0 0"
    assert "interrupted" in capsys.readouterr().out


def test_scan_window_keys(tmp_path, stub_pipeline, monkeypatch):
    cam = FakeCamera(fps=60)
    _devices(monkeypatch, cam)
    t_space: list[float] = []

    def keys(n: int):
        if n == 3:
            t_space.append(time.perf_counter())
            return " "
        if t_space and time.perf_counter() - t_space[0] > 0.6:
            return "q" if cam.exposures else "+"
        return None

    gui = GuiScript(keys).install(monkeypatch)
    out = tmp_path / "scan"
    rc = cli.main(["--calib", str(_calib_file(tmp_path)), "scan", "--no-pico", "--out", str(out)])
    assert rc == 0 and gui.windows == ["OpenHandScan scan"]
    assert len(RecordingReader(out)) >= 5
    assert cam.exposures == [-6.0]  # default -7 (log2 s) + 1 step
    assert gui.images and all(im.ndim == 3 and im.shape[2] == 3 for im in gui.images)
    widths = [im.shape[1] for im in gui.images]
    k = widths.index(960)  # only "waiting for the camera" images (640 wide) may come before the first view
    assert set(widths[:k]) <= {640} and set(widths[k:]) == {960}
    assert gui.resizes[-1] == (960, 600)  # the window follows the real view, not the waiting image
    # the depth inset (top right) shows the colour map (non-grey pixels) once the scan runs
    assert any(len(set(im[150, -50].tolist())) > 1 for im in gui.images[-5:])


def test_record_stops_when_the_window_is_closed(tmp_path, monkeypatch):
    _devices(monkeypatch, FakeCamera(fps=60))
    gui = GuiScript(lambda n: None, closed_after=40).install(monkeypatch)
    out = tmp_path / "rec"
    rc = cli.main(["record", str(out), "--no-pico", "--start-now"])
    assert rc == 0 and gui.calls >= 40
    r = RecordingReader(out)
    assert len(r) >= 3 and r.meta["purpose"] == "record" and r.meta["profile"] == "scan"
    assert r.calibration() is None  # no calibration configured


def test_record_rejects_unknown_profile_and_used_folder(tmp_path, capsys):
    assert cli.main(["record", str(tmp_path / "a"), "--profile", "nope", "--no-pico"]) == 1
    assert "unknown emitter profile" in capsys.readouterr().err
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "x.txt").write_text("x")
    assert cli.main(["record", str(tmp_path / "b"), "--no-pico"]) == 1
    assert "not empty" in capsys.readouterr().err


def test_scan_without_calibration_explains(tmp_path, capsys):
    assert cli.main(["scan", "--no-pico", "--no-gui"]) == 1
    assert "ohs calibrate stereo" in capsys.readouterr().err


# --------------------------------------------------------------------------- preview
def test_preview_replay_headless(sim_rec, capsys):
    rc = cli.main(["preview", "--replay", str(sim_rec), "--no-gui", "--max-frames", "4", "--depth"])
    assert rc == 0
    assert "Preview (rectified)" in capsys.readouterr().out


def test_preview_processor_views():
    calib = nominal_calibration((160, 100))
    rng = np.random.default_rng(1)
    left = rng.integers(0, 255, (100, 160), dtype=np.uint8)
    frame = StereoFrame(0, 1.0, left, np.roll(left, -6, axis=1))
    p = live.PreviewProcessor(calib, 0.5, lines=True, depth=True)
    p.start()
    v = p.process(frame)
    assert v.rectified and v.left.shape == (50, 80) and v.depth.shape == (50, 80) and v.lines is not None
    p.raw = True
    v = p.process(frame)
    assert not v.rectified and v.depth is None and v.left.shape == (50, 80)
    raw_only = live.PreviewProcessor(None, 0.5, depth=True)
    raw_only.start()
    assert raw_only.raw and raw_only.process(frame).depth is None


def test_preview_keys_drive_emitters_exposure_and_swap(tmp_path, sim, monkeypatch):
    cam = FakeCamera(fps=60)
    _devices(monkeypatch, cam, sim)
    script = {5: "1", 10: "3", 15: "1", 20: "s", 25: "d", 30: "g", 35: "r", 40: "+", 45: "-", 50: " ", 55: " ", 60: "q"}
    gui = GuiScript(lambda n: script.get(n)).install(monkeypatch)
    rc = cli.main(["--calib", str(_calib_file(tmp_path)), "preview"])
    assert rc == 0 and gui.windows == ["OpenHandScan preview"]
    emits = [e for e in sim.emits() if e != "EMIT 0 0 0"]
    assert emits == ["EMIT 100 0 0", "EMIT 100 0 100", "EMIT 0 0 100", "EMIT 100 100 100"]
    assert sim.emits()[-1] == "EMIT 0 0 0" and sim.duties() == [0, 0, 0]
    assert cam.exposures == [-6.0, -7.0]
    assert gui.images and gui.images[-1].shape[1] == 1280  # left | right side by side


def test_preview_without_pico_continues(tmp_path, monkeypatch, capsys):
    _devices(monkeypatch, FakeCamera(fps=60))

    class NoPico:
        connected = False

        def open(self):
            raise ConnectionError("no Pico found (USB VID 0x2E8A)")

        def close(self):
            pass

    monkeypatch.setattr(live, "make_pico", lambda cfg: NoPico())
    rc = cli.main(["preview", "--no-gui", "--max-frames", "3"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "continuing without emitters" in out and "Preview (raw)" in out


# --------------------------------------------------------------------------- calibration capture
@pytest.fixture(scope="module")
def board_pair():
    from ohs.calib.board import BoardViewRenderer, CharucoSpec
    from ohs.geometry import make_se3

    calib = nominal_calibration((640, 400))
    r = BoardViewRenderer(CharucoSpec(), calib.stereo.K1, (640, 400))
    T = make_se3(np.eye(3), [-80.0, -80.0, 320.0])
    T_r = T.copy()
    T_r[0, 3] -= 60.0
    return r.render(T), r.render(T_r)


def test_stereo_capture_with_the_trigger(tmp_path, sim, board_pair, monkeypatch, capsys):
    _devices(monkeypatch, FakeCamera(size=(640, 400), fps=30, images=board_pair), sim)
    calls: list = []
    flow = types.ModuleType("ohs.app.calib_flow")

    def stereo_from_recording(rec_path, spec, step=1):
        calls.append((Path(rec_path), spec, step))
        return nominal_calibration((640, 400), baseline=61.0).stereo, {"rms": 0.25, "quality_ok": True, "verdict": "good"}

    flow.stereo_from_recording = stereo_from_recording
    done = threading.Event()

    def presser() -> None:
        _wait_for(lambda: any(ln.startswith("IMU") for ln in sim.received))
        while not done.is_set():
            time.sleep(0.35)
            sim.press()

    th = threading.Thread(target=presser, daemon=True)
    th.start()
    calib_path = tmp_path / "calib.json"
    out = tmp_path / "cap"
    try:
        with stub_modules(calib_flow=flow):
            rc = cli.main(["--calib", str(calib_path), "calibrate", "stereo", "--no-gui", "--views", "4", "--out", str(out)])
    finally:
        done.set()
        th.join(5)
    assert rc == 0
    r = RecordingReader(out)
    assert len(r) == 4 and list(r.indices) == [0, 1, 2, 3]  # exactly the captured views, renumbered
    assert r.meta["purpose"] == "calib_stereo" and r.meta["board"]["squares_x"] == 11
    assert r.calibration() is None  # the stereo capture does not carry an old calibration
    assert calls and calls[0][0] == out and calls[0][2] == 1
    assert all(e == "EMIT 0 0 0" for e in sim.emits())  # stereo profile: all emitters off
    saved = ScannerCalibration.load(calib_path)
    assert saved.stereo.baseline == pytest.approx(61.0) and saved.lasers == {} and saved.imu is None
    outp = capsys.readouterr().out
    assert "view 1 captured" in outp and "verdict: good" in outp and "Saved" in outp


def test_capture_session_coverage_and_rejects(board_pair):
    from ohs.app.capture import BoardProcessor, CoverageMap
    from ohs.calib.board import CharucoSpec

    proc = BoardProcessor("stereo", CharucoSpec(), None)
    proc.start()
    left, right = board_pair
    bv = proc.process(StereoFrame(0, 0.0, left, right))
    assert bv.common >= 8 and 250 < bv.distance_mm < 450 and bv.tilt_deg < 5.0
    cov = CoverageMap((640, 400))
    cov.add(bv.corners_l)
    assert 0.05 < cov.fraction < 0.6
    img = cv2.cvtColor(left, cv2.COLOR_GRAY2BGR)
    cov.draw(img)
    assert img.shape == (400, 640, 3)
    blank = proc.process(StereoFrame(1, 0.0, np.full_like(left, 128), np.full_like(right, 128)))
    assert blank.common == 0 and blank.T_cb is None
    with pytest.raises(ValueError, match="stereo calibration"):
        BoardProcessor("lasers", CharucoSpec(), None)


def test_imu_capture_needs_the_pico(tmp_path, capsys):
    calib = _calib_file(tmp_path)
    assert cli.main(["--calib", str(calib), "calibrate", "imu", "--no-pico", "--no-gui"]) == 1
    assert "needs the Pico" in capsys.readouterr().err


# --------------------------------------------------------------------------- small pieces
def test_latest_slot_and_recorder(tmp_path):
    slot = live.LatestSlot()
    slot.put(1)
    slot.put(2)
    assert slot.take(0.01) == 2 and slot.dropped == 1 and slot.take(0.01) is None
    rec = live.Recorder(tmp_path / "r", meta={"purpose": "test"})
    img = np.zeros((8, 8), np.uint8)
    for i in range(3):
        rec.put_frame(StereoFrame(i, 10.0 + i, img, img))
    s = rec.close(extra=1)
    assert s["n_frames"] == 3 and s["duration_s"] == pytest.approx(2.0)
    r = RecordingReader(tmp_path / "r")
    assert len(r) == 3 and r.meta["purpose"] == "test" and r.meta["extra"] == 1
    assert rec.close()["n_frames"] == 3  # idempotent


def test_recorder_reports_failed_frame_write(tmp_path, monkeypatch):
    real_imwrite = cv2.imwrite
    calls = {"n": 0}

    def flaky_imwrite(*args, **kw):
        calls["n"] += 1
        return False if calls["n"] == 1 else real_imwrite(*args, **kw)

    monkeypatch.setattr(cv2, "imwrite", flaky_imwrite)
    rec = live.Recorder(tmp_path / "r")
    img = np.zeros((8, 8), np.uint8)
    for i in range(3):
        rec.put_frame(StereoFrame(i, 10.0 + i, img, img))
    s = rec.close()
    assert s["error"] is not None and rec.error is not None
    assert s["n_frames"] == 3 and RecordingReader(tmp_path / "r").meta["n_frames"] == 3


def test_recorder_backlog_counts_waiting_frames_and_warns_once(tmp_path, monkeypatch, caplog):
    release = threading.Event()
    real_imwrite = cv2.imwrite

    def slow_imwrite(*args, **kw):
        release.wait(10)
        return real_imwrite(*args, **kw)

    monkeypatch.setattr(cv2, "imwrite", slow_imwrite)
    monkeypatch.setattr(live.Recorder, "BACKLOG_WARN", 10, raising=False)
    rec = live.Recorder(tmp_path / "r")
    img = np.zeros((8, 8), np.uint8)
    try:
        with caplog.at_level("WARNING", logger=live.log.name):
            for i in range(30):
                rec.put_frame(StereoFrame(i, 10.0 + i / 30, img, img))
            time.sleep(0.3)
            assert rec.backlog == 30  # none written yet: in the queue, waiting for a slot, or in flight
            warnings = [r for r in caplog.records if "backlog" in r.getMessage()]
            assert len(warnings) == 1
    finally:
        release.set()
        s = rec.close()
    assert s["n_frames"] == 30 and rec.backlog == 0


def test_drawing_helpers():
    d = np.zeros((40, 60), np.float32)
    d[10:30, 10:50] = np.linspace(150, 450, 40)[None, :]
    col = live.depth_to_bgr(d, 150, 450)
    assert col.shape == (40, 60, 3) and col[0, 0].sum() == 0 and col[20, 20].sum() > 0
    dst = np.zeros((300, 400, 3), np.uint8)
    live.paste_inset(dst, col)
    assert dst[10:30, -100:-10].sum() > 0
    live.draw_hud(dst, [("hello", live.WHITE)], [("bottom", live.GREY)])
    assert live.fmt_time(75.25) == "01:15.2"
    assert live.check_output_dir(Path("does-not-exist-xyz")) == Path("does-not-exist-xyz")


# --------------------------------------------------------------------------- real F2 modules (smoke)
def _f2_available() -> bool:
    try:
        importlib.import_module("ohs.app.pipeline")
        importlib.import_module("ohs.app.process")
        importlib.import_module("ohs.app.calib_flow")
    except Exception:
        return False
    return True


@pytest.mark.skipif(not _f2_available(), reason="F2's pipeline / process / calib_flow modules are not available")
def test_f2_smoke_scan_replay_then_process(tmp_path, capsys):
    from ohs.sim.datasets import make_scan_recording

    pytest.importorskip("open3d")
    src = make_scan_recording(tmp_path / "src", n_frames=8, image_size=(320, 200), depth_every=0)
    out = tmp_path / "scan"
    rc = cli.main(["scan", "--replay", str(src), "--no-pico", "--no-gui", "--max-frames", "6", "--out", str(out), "--process"])
    assert rc == 0
    r = RecordingReader(out)
    assert len(r) == 6 and r.calibration() is not None
    res = out / "result"
    for name in ("mesh.ply", "mesh.stl", "laser_points.ply", "trajectory.csv", "report.json"):
        assert (res / name).exists(), name
    outp = capsys.readouterr().out
    assert "frames: 6 processed" in outp and "mesh:" in outp


@pytest.mark.slow
@pytest.mark.skipif(not _f2_available(), reason="F2's pipeline / process / calib_flow modules are not available")
def test_f2_smoke_calibrate_stereo_from_a_board_recording(tmp_path, capsys):
    """``ohs calibrate stereo --from`` with the real flow on simulated board views (~8 s of rendering)."""
    from ohs.io.recording import RecordingWriter
    from ohs.sim.datasets import make_stereo_calib_set

    pairs, true, spec = make_stereo_calib_set(n_views=6, image_size=(1280, 800), realistic=True)
    rec = tmp_path / "board"
    with RecordingWriter(rec, source="sim", purpose="calib_stereo") as w:
        for i, (left, right) in enumerate(pairs):
            w.write_frame(StereoFrame(i, 10.0 + i, left, right))
    calib_path = tmp_path / "calibration.json"
    old = nominal_calibration((1280, 800))
    old.save(calib_path)  # with laser planes and IMU, which a new stereo calibration drops
    rc = cli.main(["--calib", str(calib_path), "calibrate", "stereo", "--from", str(rec)])
    assert rc == 0
    c = ScannerCalibration.load(calib_path)
    assert c.stereo.baseline == pytest.approx(true.stereo.baseline, abs=0.5) and c.lasers == {} and c.imu is None
    cap = capsys.readouterr()
    assert "rms:" in cap.out and "rectification_check:" in cap.out and "dropped" in cap.err
    assert (rec / "calib_report_stereo.json").stat().st_size > 0
