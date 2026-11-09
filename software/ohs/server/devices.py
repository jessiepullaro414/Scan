"""Scanner sources for the app: the camera (+ Pico), the replay of a recording, the simulator demo.

:class:`DeviceManager` owns one connection at a time:

* ``camera``: :class:`~ohs.devices.camera.StereoCamera` (``cfg.camera``) plus the
  :class:`~ohs.devices.pico.Pico` if one answers. Without a Pico it still connects, with a
  warning (no emitters, IMU or trigger). The calibration is ``cfg.paths.calibration``.
* ``replay``: ``ReplayCamera(path, realtime=True, loop=loop)``; its IMU samples and events come
  from the recording (``ReplayCamera.reader``) and are fed in step with the frames. With
  ``loop``, every pass repeats the IMU and events of one recording period shifted by the period,
  exactly like the frames (ReplayCamera's ``k * duration`` shift). The calibration is the
  recording's ``calibration.json``, else the configured one. A replay never uses the Pico.
* ``sim``: the simulator demo (:mod:`ohs.server.demo`) replayed in a loop, with its true
  calibration. The first time, a ``generate_demo`` job renders it (about a minute): ``connect``
  returns that job, ``busy`` says so, and the device connects by itself when the job is done.

Threads per connection (daemon threads, joined on disconnect):

* ``ohs-capture`` reads every frame (``read()`` never returns the same frame twice), feeds the
  replay IMU/events up to the frame time, and routes the frame: to the attached
  :class:`~ohs.server.session.ScanSession` while one is attached, else to the idle preview.
* ``ohs-pico`` drains the Pico's IMU and button queues every 5 ms. Trigger: a press shorter
  than 1 s is a "short" press, longer a "long" press (``on_trigger``).
* ``ohs-preview`` (idle only): rectify + depth + laser lines at ~10 Hz for the previews and the
  distance gauge, only while a client is connected.

Failures (camera gone, end of a non-looping replay, Pico lost) switch the emitters off at once in
the device thread, then are reported through ``on_lost(kind, message)`` for the owner to stop a
scan and call :meth:`DeviceManager.handle_lost` (never from the device thread itself).

**Emitters off** on disconnect, on every failure and on shutdown (``Pico.close`` also switches
them off, and the firmware does after 1.5 s without a host line).
"""
from __future__ import annotations

import collections
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ohs.server.demo import DEFAULT_DEMO, DemoParams, demo_path, demo_ready, generate_demo
from ohs.server.imaging import PreviewPublisher, ViewTools, central_distance
from ohs.server.protocol import ApiError
from ohs.server.util import RateMeter, join_thread, safe_call

__all__ = ["DeviceManager", "ReplayFeed", "SOURCES"]

log = logging.getLogger(__name__)

SOURCES = ("camera", "replay", "sim")
IMU_HISTORY_S = 1.0
REPLAY_LOOKAHEAD_S = 0.05
LONG_PRESS_S = 1.0
IDLE_VIEW_HZ = 10.0
IDLE_VIEW_BUSY_HZ = 2.0  # while a scan is being processed: leave the CPU to the processing job
_LEVEL_NAMES = ("vertical", "horizontal", "dots")


# --------------------------------------------------------------------------- replay IMU/events
class ReplayFeed:
    """IMU samples and events of a replayed recording, in step with ReplayCamera's frames.

    With ``loop``, pass ``k`` repeats the samples of ``[t_first, t_first + span)`` shifted by
    ``k * span`` (``span`` = last - first frame time + one frame period, as in ReplayCamera), so the
    passes tile the timeline without overlaps.
    """

    def __init__(self, reader: Any, loop: bool) -> None:
        times = reader.times()
        steps = np.diff(times)
        med = float(np.median(steps)) if len(steps) else 0.0
        period = med if med > 0 else 1.0 / 30.0
        self.loop = bool(loop)
        self.t_first = float(times[0])
        self.span = float(times[-1] - times[0]) + period
        a = reader.imu_arrays()
        order = np.argsort(a["t_host"], kind="stable")
        t = a["t_host"][order]
        keep = (t >= self.t_first) & (t < self.t_first + self.span) if self.loop else np.ones(len(t), bool)
        self.imu_t = t[keep]
        self.imu_dev = a["t_device"][order][keep]
        self.gyro = a["gyro"][order][keep]
        self.accel = a["accel"][order][keep]
        ev = sorted(reader.events(), key=lambda e: e.t_host)
        if self.loop:
            ev = [e for e in ev if self.t_first <= e.t_host < self.t_first + self.span]
        self.events = ev
        self._n_imu = 0
        self._n_ev = 0

    @property
    def has_imu(self) -> bool:
        return len(self.imu_t) > 0

    def _shift(self, n: int, size: int) -> tuple[int, float]:
        k, i = divmod(n, size)
        return i, k * self.span

    def take(self, until: float) -> tuple[list, list]:
        """IMU samples and events with (shifted) ``t_host <= until`` not returned before."""
        from ohs.types import Event, ImuSample

        samples: list = []
        n_imu = len(self.imu_t)
        while n_imu and (self.loop or self._n_imu < n_imu):
            i, dt = self._shift(self._n_imu, n_imu)
            t = float(self.imu_t[i]) + dt
            if t > until:
                break
            samples.append(ImuSample(t, float(self.imu_dev[i]) + dt, self.gyro[i].copy(), self.accel[i].copy()))
            self._n_imu += 1
            if len(samples) > 100000:  # pragma: no cover - a huge jump in time
                break
        events: list = []
        n_ev = len(self.events)
        while n_ev and (self.loop or self._n_ev < n_ev):
            i, dt = self._shift(self._n_ev, n_ev)
            e = self.events[i]
            if e.t_host + dt > until:
                break
            events.append(Event(float(e.t_host) + dt, e.kind, e.value))
            self._n_ev += 1
        return samples, events


# --------------------------------------------------------------------------- one connection
@dataclass
class _Connection:
    id: int
    source: str
    camera: Any
    calib: Any = None
    calib_path: str | None = None
    pico: Any = None
    pico_hello: dict | None = None
    pico_error: str | None = None
    imu_live: bool = False
    replay_path: Path | None = None
    loop: bool = False
    feed: ReplayFeed | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    threads: dict = field(default_factory=dict)
    cam_rate: RateMeter = field(default_factory=RateMeter)
    n_frames: int = 0
    frame_size: tuple[int, int] | None = None
    last_frame_t: float | None = None
    idle_stats: dict = field(default_factory=dict)
    scan_levels: tuple[float, float, float] | None = None
    preview_slot: Any = None
    warnings: list[str] = field(default_factory=list)


def _calib_summary(calib: Any, path: str | None) -> dict:
    if calib is None:
        return {"loaded": False, "path": path, "image_size": None, "baseline_mm": None, "lasers": [], "imu": False}
    st = calib.stereo
    return {"loaded": True, "path": path, "image_size": [int(st.image_size[0]), int(st.image_size[1])],
            "baseline_mm": round(float(st.baseline), 3), "lasers": sorted(calib.lasers or {}), "imu": calib.imu is not None}


class DeviceManager:
    """The scanner source of the app; see the module docstring. Thread-safe."""

    def __init__(self, cfg: Any, *, hub: Any, jobs: Any, demo_dir: str | Path,
                 notify: Callable[[str, str], None] | None = None,
                 on_change: Callable[[], None] | None = None,
                 on_lost: Callable[[str, str], None] | None = None,
                 on_trigger: Callable[[str], None] | None = None,
                 camera_factory: Callable[[Any], Any] | None = None,
                 pico_factory: Callable[[Any], Any] | None = None,
                 demo_params: DemoParams = DEFAULT_DEMO) -> None:
        self.cfg = cfg
        self.hub = hub
        self.jobs = jobs
        self.demo_dir = Path(demo_dir).expanduser()
        self.demo_params = demo_params
        self._notify = notify or (lambda level, msg: None)
        self._on_change = on_change or (lambda: None)
        self._on_lost = on_lost or (lambda kind, msg: None)
        self._on_trigger = on_trigger or (lambda kind: None)
        self.camera_factory = camera_factory or self._default_camera
        self.pico_factory = pico_factory or self._default_pico
        self._lock = threading.RLock()  # short critical sections (state, sink)
        self._op_lock = threading.Lock()  # serialises connect / disconnect (long)
        self._imu_lock = threading.Lock()
        self._recent_imu: collections.deque = collections.deque(maxlen=8192)
        self._conn: _Connection | None = None
        self._sink: Any = None
        self._ids = 0
        self.busy: str | None = None
        self.demo_job: Any = None
        self.last_error: str | None = None
        self._t_down: float | None = None
        self._closed = False

    # ------------------------------------------------------------------ factories
    @staticmethod
    def _default_camera(cfg: Any) -> Any:
        from ohs.devices.camera import StereoCamera

        return StereoCamera(cfg.camera)

    @staticmethod
    def _default_pico(cfg: Any) -> Any:
        from ohs.devices.pico import Pico

        return Pico(cfg.pico)

    # ------------------------------------------------------------------ queries
    @property
    def connected(self) -> bool:
        return self._conn is not None

    @property
    def source(self) -> str:
        c = self._conn
        return "none" if c is None else c.source

    @property
    def calib(self) -> Any:
        c = self._conn
        return None if c is None else c.calib

    @property
    def calib_path(self) -> str | None:
        c = self._conn
        return None if c is None else c.calib_path

    @property
    def has_pico(self) -> bool:
        c = self._conn
        return c is not None and c.pico is not None and bool(getattr(c.pico, "connected", False))

    @property
    def frame_size(self) -> tuple[int, int] | None:
        c = self._conn
        return None if c is None else c.frame_size

    @property
    def last_frame_t(self) -> float | None:
        c = self._conn
        return None if c is None else c.last_frame_t

    def imu_available(self) -> bool:
        c = self._conn
        if c is None:
            return False
        if c.feed is not None:
            return c.feed.has_imu
        return bool(c.imu_live)

    def source_name(self) -> str:
        """``source`` for the recording's meta.json ("camera", or the replayed recording's source)."""
        c = self._conn
        if c is None:
            return "camera"
        if c.source == "camera":
            return "camera"
        reader = getattr(c.camera, "reader", None)
        src = getattr(reader, "meta", {}).get("source") if reader is not None else None
        return str(src or c.source)

    def emitter_levels(self) -> dict | None:
        c = self._conn
        if c is None or c.pico is None:
            return None
        lv = tuple(getattr(c.pico, "emitters", (0, 0, 0)))
        return {n: float(v) for n, v in zip(_LEVEL_NAMES, lv)}

    def camera_fps(self) -> float:
        c = self._conn
        return 0.0 if c is None else round(c.cam_rate.value(), 2)

    def idle_stats(self) -> dict:
        c = self._conn
        return {} if c is None else dict(c.idle_stats)

    def state(self) -> dict:
        """``DeviceState`` (APP_API.md) plus ``error``, ``warnings`` and ``imu_stream``."""
        c = self._conn
        if c is None:
            calib, path = self._config_calibration()
            return {"source": "none", "connected": False, "busy": self.busy, "camera": None, "pico": None,
                    "calibration": _calib_summary(calib, path), "replay": None, "error": self.last_error,
                    "warnings": [], "imu_stream": False}
        info = dict(safe_call("reading the camera info", lambda: dict(getattr(c.camera, "info", {}) or {})) or {})
        info["measured_fps"] = round(c.cam_rate.value(), 2)
        info["frames"] = c.n_frames
        if c.frame_size is not None:
            info["frame_size"] = list(c.frame_size)
        pico = None
        if c.source == "camera":
            hello = c.pico_hello or {}
            pico = {"connected": self.has_pico,
                    "firmware": (f"{hello.get('firmware', '')} {hello.get('version', '')}".strip() or None) if hello else None,
                    "emitters": self.emitter_levels() or {n: 0.0 for n in _LEVEL_NAMES}}
            if c.pico_error:
                pico["error"] = c.pico_error
            if hello:
                pico["imu"] = hello.get("imu")
                pico["port"] = hello.get("port")
        warnings = list(c.warnings)
        if c.calib is not None and c.frame_size is not None:
            want = tuple(int(v) for v in c.calib.stereo.image_size)
            if tuple(c.frame_size) != want:
                warnings.append(f"the camera delivers {c.frame_size[0]}x{c.frame_size[1]} images per camera but the "
                                f"calibration is for {want[0]}x{want[1]}: set camera.width / camera.height to the "
                                "calibrated mode, or calibrate this mode")
        return {
            "source": c.source, "connected": True, "busy": self.busy,
            "camera": {"connected": True, "info": info}, "pico": pico,
            "calibration": _calib_summary(c.calib, c.calib_path),
            "replay": {"path": str(c.replay_path), "loop": bool(c.loop)} if c.source in ("replay", "sim") else None,
            "error": self.last_error, "warnings": warnings, "imu_stream": self.imu_available(),
        }

    def _config_calibration(self) -> tuple[Any, str | None]:
        p = self.cfg.paths.calibration_path
        if not p.is_file():
            return None, str(p)
        try:
            from ohs.types import ScannerCalibration

            return ScannerCalibration.load(p), str(p)
        except Exception as e:  # reported as "not loaded"
            log.warning("cannot read the calibration %s: %s", p, e)
            return None, str(p)

    # ------------------------------------------------------------------ connect / disconnect
    def connect(self, source: str, recording: str | None = None, loop: bool | None = None) -> Any:
        """Connect ``source``; returns ``None``, or the ``generate_demo`` job for a first ``sim`` connect."""
        if self._closed:
            raise ApiError(503, "the app is shutting down", "start OpenHandScan Studio again")
        if source not in SOURCES:
            raise ApiError(400, f"unknown source {source!r}", "use camera, replay or sim")
        if self.busy:
            raise ApiError(409, f"the device is busy: {self.busy}", "wait until it finishes (or cancel its job)")
        if self._sink is not None:
            raise ApiError(409, "a scan is running", "stop the scan before changing the device")
        if source == "replay" and not recording:
            raise ApiError(400, "replay needs a recording folder", 'send {"source": "replay", "recording": "<folder>"}')
        if source == "sim":
            path = demo_path(self.demo_dir)
            if not demo_ready(path):
                if self._conn is not None:
                    with self._op_lock:
                        self._disconnect_locked("switching to the simulator")
                return self._start_demo_job(path)
        with self._op_lock:
            if self._conn is not None:
                self._disconnect_locked("switching the source")
            if source == "camera":
                conn = self._open_camera()
            elif source == "replay":
                conn = self._open_replay(Path(str(recording)).expanduser(), bool(loop) if loop is not None else False, "replay")
            else:
                conn = self._open_replay(demo_path(self.demo_dir), True, "sim")
            self._start(conn)
        self._on_change()
        return None

    def _next_id(self) -> int:
        with self._lock:
            self._ids += 1
            return self._ids

    def _open_camera(self) -> _Connection:
        calib, path = self._config_calibration()
        self.busy = "Connecting to the camera..."
        self._on_change()
        try:
            cam = self.camera_factory(self.cfg)
            try:
                cam.open()
            except Exception as e:
                safe_call("closing the camera", cam.close)
                raise ApiError(503, f"the camera could not be opened: {e}",
                               "check the USB cable, close other programs that use the camera, then run `ohs devices` "
                               "and set camera.index in the settings") from e
            conn = _Connection(self._next_id(), "camera", cam, calib=calib, calib_path=path)
            if calib is None:
                conn.warnings.append(f"no calibration at {path}: run `ohs calibrate stereo`, then lasers and imu")
            pico = None
            try:
                pico = self.pico_factory(self.cfg)
                conn.pico_hello = dict(pico.open() or {})
                conn.pico = pico
            except Exception as e:
                if pico is not None:
                    safe_call("closing the Pico", pico.close)
                conn.pico_error = str(e)
                msg = (f"No Pico ({e}): scanning works, but without emitters, IMU and trigger. Check the data USB cable "
                       "and the firmware (firmware/README.md), then connect again.")
                conn.warnings.append(msg)
                self._notify("warn", msg)
            if conn.pico is not None and str((conn.pico_hello or {}).get("imu", "NONE")).upper() != "NONE":
                try:
                    conn.pico.start_imu()
                    conn.imu_live = True
                except Exception as e:
                    conn.warnings.append(f"the Pico's IMU did not start ({e}): tracking runs without the gyro prior")
            if calib is not None and conn.imu_live and calib.imu is None:
                conn.warnings.append("no IMU calibration: tracking runs without the gyro prior (run `ohs calibrate imu`)")
            return conn
        finally:
            self.busy = None

    def _open_replay(self, path: Path, loop: bool, source: str) -> _Connection:
        from ohs.devices.camera import ReplayCamera
        from ohs.types import ScannerCalibration

        if not (path / "frames.csv").is_file():
            raise ApiError(404, f"{path} is not a recording folder (no frames.csv)",
                           "pick a recording folder: a scan's recording/ folder or one written by `ohs sim` or `ohs record`")
        cam = ReplayCamera(path, realtime=True, loop=loop)
        try:
            cam.open()
        except Exception as e:
            raise ApiError(400, f"the recording {path} cannot be replayed: {e}", "pick another recording folder") from e
        calib, cpath = None, None
        if (path / "calibration.json").is_file():
            cpath = str(path / "calibration.json")
            try:
                calib = ScannerCalibration.load(cpath)
            except Exception as e:
                log.warning("cannot read %s: %s", cpath, e)
        if calib is None and source == "replay":
            calib, cpath = self._config_calibration()
        conn = _Connection(self._next_id(), source, cam, calib=calib, calib_path=cpath, replay_path=path, loop=loop)
        conn.feed = ReplayFeed(cam.reader, loop)
        if calib is None:
            conn.warnings.append("no calibration for this recording: live scanning is not possible (add calibration.json)")
        elif not conn.feed.has_imu:
            conn.warnings.append("the recording has no IMU data: tracking runs without the gyro prior")
        return conn

    def _start(self, conn: _Connection) -> None:
        from ohs.app.live import LatestSlot

        conn.preview_slot = LatestSlot()
        with self._lock:
            self._conn = conn
            self.last_error = None
        with self._imu_lock:
            self._recent_imu.clear()
        self._thread(conn, "ohs-capture", self._capture_loop)
        if conn.pico is not None:
            self._thread(conn, "ohs-pico", self._pico_loop)
        self._thread(conn, "ohs-preview", self._preview_loop)
        what = {"camera": "camera", "replay": f"replay of {conn.replay_path}", "sim": "simulator demo"}[conn.source]
        self._notify("info", f"Connected: {what}")
        for w in conn.warnings:
            log.warning("%s", w)
        log.info("device connected: %s", what)

    @staticmethod
    def _thread(conn: _Connection, name: str, fn: Callable[[_Connection], None]) -> None:
        th = threading.Thread(target=fn, args=(conn,), name=name, daemon=True)
        conn.threads[name] = th
        th.start()

    def disconnect(self, reason: str = "") -> None:
        """Disconnect (emitters off first). A running scan must be stopped by the caller first."""
        with self._op_lock:
            self._disconnect_locked(reason)
        self._on_change()

    def _disconnect_locked(self, reason: str) -> None:
        with self._lock:
            conn, self._conn = self._conn, None
            self._sink = None
        if conn is None:
            return
        self._emitters_off(conn)
        conn.stop.set()
        for name in ("ohs-capture", "ohs-pico", "ohs-preview"):
            join_thread(conn.threads.get(name), 5.0)
        if conn.pico is not None:
            safe_call("closing the Pico", conn.pico.close)
        safe_call("closing the camera", conn.camera.close)
        log.info("device disconnected (%s)", reason or "requested")
        self._notify("info", "Disconnected" + (f": {reason}" if reason else ""))

    def handle_lost(self, kind: str, message: str) -> None:
        """Act on a reported failure (called by the owner, not from a device thread)."""
        conn = self._conn
        if conn is None:
            return
        if kind == "pico":
            if conn.pico is not None:
                safe_call("closing the Pico", conn.pico.close)
            conn.pico = None
            conn.imu_live = False
            conn.pico_error = message
            conn.warnings.append(message)
            self._on_change()
            return
        self.last_error = message if kind != "eof" else None
        self.disconnect(message)
        if kind != "eof":
            self.last_error = message
            self._on_change()

    def shutdown(self) -> None:
        self._closed = True
        with self._op_lock:
            self._disconnect_locked("the app is closing")

    # ------------------------------------------------------------------ simulator demo
    def _start_demo_job(self, path: Path) -> Any:
        params = self.demo_params

        def run(ctx: Any) -> dict:
            generate_demo(path, params, progress=lambda f, m: ctx.progress(f, m), cancelled=lambda: ctx.cancelled)
            return {"path": str(path), "frames": params.n_frames, "image_size": list(params.image_size),
                    "message": "Simulator demo ready"}

        def done(job: Any) -> None:
            self.busy = None
            self.demo_job = None
            if job.state == "done" and not self._closed and self._conn is None and self._sink is None:
                try:
                    self.connect("sim")
                except ApiError as e:
                    self._notify("error", f"The simulator demo is ready but could not be connected: {e}")
                except Exception as e:  # report, never raise in the worker
                    log.exception("connecting the simulator demo failed")
                    self._notify("error", f"The simulator demo is ready but could not be connected: {e}")
            elif job.state == "failed":
                self._notify("error", f"The simulator demo could not be generated: {job.error}. "
                                      "Check free disk space in the demo folder and try again.")
            self._on_change()

        with self._lock:
            if self.busy:
                raise ApiError(409, f"the device is busy: {self.busy}", "wait until it finishes (or cancel its job)")
            self.busy = "Generating simulator demo…"
        est = params.estimate_s()
        try:
            job = self.jobs.submit("generate_demo", run, message=f"Generating the simulator demo (about {est:.0f} s)...",
                                   on_done=done)
        except Exception:
            self.busy = None
            raise
        self.demo_job = job
        self._notify("info", f"Generating the simulator demo once (about {est:.0f} s); it connects when ready.")
        self._on_change()
        return job

    # ------------------------------------------------------------------ sessions
    def attach(self, session: Any) -> None:
        """Route frames, IMU and events to ``session`` (hands over the last second of IMU first)."""
        conn = self._conn
        if conn is None:
            raise ApiError(409, "no device is connected", "connect the camera, a replay or the simulator first")
        with self._imu_lock:
            t_ref = conn.last_frame_t
            hist = list(self._recent_imu)
            if t_ref is not None:
                hist = [s for s in hist if s.t_host >= t_ref - IMU_HISTORY_S]
            with self._lock:
                self._sink = session
            safe_call("seeding the IMU history", lambda: session.seed_imu(hist))

    def detach(self) -> None:
        """Stop routing to the session; the next scan starts from the configured emitter levels again."""
        with self._imu_lock, self._lock:
            self._sink = None
            if self._conn is not None:
                self._conn.scan_levels = None

    # ------------------------------------------------------------------ emitters
    def _emitters_off(self, conn: _Connection | None = None) -> None:
        c = conn or self._conn
        if c is None or c.pico is None:
            return
        if getattr(c.pico, "connected", False):
            try:
                c.pico.all_off()
            except Exception as e:  # the firmware watchdog switches them off 1.5 s later anyway
                log.warning("could not switch the emitters off: %s", e)

    def emitters_off(self) -> None:
        self._emitters_off()
        self._on_change()

    def scan_emitters_on(self, mode: str) -> None:
        """Emitters to the scan levels (``[emitters]`` scan profile, or the levels set during this scan)."""
        c = self._conn
        if c is None or c.pico is None or not getattr(c.pico, "connected", False):
            return
        if c.scan_levels is None:
            c.scan_levels = tuple(float(v) for v in self.cfg.emitters.scan_levels(mode, live=True))  # type: ignore[assignment]
        levels = c.scan_levels
        try:
            c.pico.set_emitters(*levels)
        except Exception as e:
            self._notify("warn", f"the emitters could not be switched on ({e}): check the Pico's USB cable")
        self._on_change()

    def set_emitters(self, levels: dict, scanning: bool = False, apply: bool = True) -> None:
        """Set some or all levels (percent). Ignored (no error) without a Pico, e.g. for replay and sim.

        ``scanning``: also keep them as this scan's levels (used on resume); ``apply=False`` only
        stores them (paused scan).
        """
        c = self._conn
        if c is None:
            raise ApiError(409, "no device is connected", "connect the camera first")
        if c.pico is None or not getattr(c.pico, "connected", False):
            if c.source == "camera":
                self._notify("warn", "No Pico connected: the emitters cannot be switched")
            return
        base = c.scan_levels if (not apply and c.scan_levels is not None) else getattr(c.pico, "emitters", (0, 0, 0))
        cur = dict(zip(_LEVEL_NAMES, (float(v) for v in base)))
        for k, v in levels.items():
            if v is None:
                continue
            fv = float(v)
            if k not in cur or not math.isfinite(fv) or not 0.0 <= fv <= 100.0:
                raise ApiError(400, f"invalid emitter level {k}={v!r}", "levels are percent, 0 to 100")
            cur[k] = fv
        new = tuple(cur[n] for n in _LEVEL_NAMES)
        if apply:
            try:
                c.pico.set_emitters(*new)
            except Exception as e:
                raise ApiError(503, f"the Pico did not accept the emitter levels: {e}",
                               "check the Pico's USB cable and connect again") from e
        if scanning:
            c.scan_levels = new  # type: ignore[assignment]
        self._on_change()

    # ------------------------------------------------------------------ threads
    def _report_lost(self, conn: _Connection, kind: str, message: str) -> None:
        """From a device thread: emitters off now, then tell the owner."""
        if conn.stop.is_set() or self._conn is not conn:
            return
        log.error("device problem (%s): %s", kind, message)
        if kind != "pico":
            self._emitters_off(conn)
        safe_call("reporting the device problem", lambda: self._on_lost(kind, message))

    def _capture_loop(self, conn: _Connection) -> None:
        cam = conn.camera
        while not conn.stop.is_set():
            try:
                f = cam.read(timeout=0.5)
            except TimeoutError:
                continue
            except EOFError:
                self._report_lost(conn, "eof", "the replayed recording ended")
                return
            except Exception as e:  # camera unplugged, unreadable frame file, ...
                self._report_lost(conn, "error", f"camera: {e}")
                return
            t_recv = time.perf_counter()
            conn.cam_rate.tick()
            conn.n_frames += 1
            if conn.frame_size is None:
                conn.frame_size = tuple(int(v) for v in f.image_size)  # type: ignore[assignment]
            if conn.feed is not None:
                samples, events = conn.feed.take(float(f.t_host) + REPLAY_LOOKAHEAD_S)
                if samples:
                    self._on_imu(samples)
                for e in events:
                    self._on_event(e, control=False)
            conn.last_frame_t = float(f.t_host)
            sink = self._sink
            if sink is not None:
                try:
                    sink.on_frame(f, t_recv)
                except Exception:
                    log.exception("the scan session rejected frame %d", f.index)
            else:
                conn.preview_slot.put(f)

    def _pico_loop(self, conn: _Connection) -> None:
        pico = conn.pico
        while not conn.stop.is_set() and conn.pico is pico:
            try:
                samples = pico.read_imu() if conn.imu_live else []
                events = pico.read_events()
            except Exception:
                log.exception("reading the Pico queues failed")
                samples, events = [], []
            if samples:
                self._on_imu(samples)
            for e in events:
                self._on_event(e, control=True)
            if not getattr(pico, "connected", True):
                self._report_lost(conn, "pico", f"the Pico disconnected ({getattr(pico, 'error', None) or 'cable?'}); "
                                                "the emitters are off. Check the USB cable and connect again")
                return
            conn.stop.wait(0.005)

    def _on_imu(self, samples: list) -> None:
        with self._imu_lock:
            self._recent_imu.extend(samples)
            sink = self._sink
        if sink is not None:
            try:
                sink.on_imu(samples)
            except Exception:
                log.exception("the scan session rejected IMU samples")

    def _on_event(self, event: Any, control: bool) -> None:
        sink = self._sink
        if sink is not None:
            safe_call("recording an event", lambda: sink.on_event(event))
        if control and event.kind == "button":
            if event.value == "down":
                self._t_down = float(event.t_host)
            elif event.value == "up" and self._t_down is not None:
                held = float(event.t_host) - self._t_down
                self._t_down = None
                safe_call("handling the trigger", lambda: self._on_trigger("long" if held >= LONG_PRESS_S else "short"))

    def _processing(self) -> bool:
        """True while a scan is being processed (a ``process_scan`` job is queued or running)."""
        try:
            return bool(self.jobs.active("process_scan"))
        except Exception:  # a jobs stand-in without active()
            return False

    def _preview_loop(self, conn: _Connection) -> None:
        live = self.cfg.live
        rng = (float(live.depth_min_mm), float(live.depth_max_mm))
        tools = ViewTools(conn.calib, float(live.scale), rng)
        previews = PreviewPublisher(self.hub, rng)
        t_view = 0.0
        while not conn.stop.is_set():
            f = conn.preview_slot.take(timeout=0.1)
            # Idle previews (and the distance gauge) only for a UI that shows them (the Scan page)
            if f is None or self._sink is not None or not self.hub.wants("preview"):
                continue
            now = time.monotonic()
            if now - t_view < 1.0 / (IDLE_VIEW_BUSY_HZ if self._processing() else IDLE_VIEW_HZ):
                continue
            t_view = now
            try:
                v = tools.view(f)
                conn.idle_stats = {"distance_mm": central_distance(v.depth)}
                if previews.due():
                    previews.publish(v.left, v.lines, v.depth)
            except Exception:
                log.exception("idle preview failed")
                conn.stop.wait(1.0)
