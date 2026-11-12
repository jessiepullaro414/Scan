"""The backend core of OpenHandScan Studio: devices, the scan session, projects, jobs and the hub.

:class:`Studio` is what the REST routes call (:mod:`ohs.server.app`). It enforces the rules of
APP_API.md (one scan at a time; scanning needs a device and a calibration; a recording scan can
not be processed or its project deleted) and turns every refusal into an
:class:`~ohs.server.protocol.ApiError` with a hint.

M2 (setup and calibration) adds two parts it wires in:

* :class:`~ohs.server.setup.SetupManager` (``Studio.setup``): the wizard's state in
  ``<data_dir>/setup.json``, the self-test job, the devices probe, the printables job and the
  print check;
* :class:`~ohs.server.calibration.CalibrationManager` (``Studio.calibration``): the calibration
  sessions. A scan and a calibration never run together (409 either way).

M3 phase 2 adds :class:`~ohs.server.processing.ProcessManager` (``Studio.processing``): the
processing studio of each scan (stages, mesh edits with undo / redo, recipes in
``<data_dir>/recipes``, overlays). "Make mesh" (:meth:`Studio.process_scan`) runs its Default
recipe; every processing job also updates the scan's ``process`` message.

``data_dir`` (default ``paths.data_dir``) holds Studio's own data and the simulator's sandbox
(``<data_dir>/sim/calibration.json`` and its capture recordings). Settings changes are written to
the config file ``config_path`` (default: the file the config was loaded from, else
:func:`ohs.app.config.default_config_path`).

Threads it runs (from :meth:`Studio.startup` to :meth:`Studio.shutdown`):

* ``ohs-status`` publishes the ``status`` message at 10 Hz while a client is connected. It only
  reads snapshots (the session's and the device's latest numbers), so it keeps going while the
  processing thread is busy (a rewind, a slow frame).
* ``ohs-events`` handles what the device threads report (camera lost, end of a replay, Pico
  lost, trigger presses), so that stopping a scan or cancelling a calibration never runs on, or
  joins, a device thread.

Locking: ``Studio._lock`` serialises the scan lifecycle (start / pause / resume / rewind / stop),
the calibration lifecycle (start / solve / save / cancel) and device (dis)connection. The device,
session, calibration, setup store, job and hub locks are only ever taken inside it or on their
own, never the other way round; callbacks from device threads (``on_connected``,
``on_launch_check``) only take the setup store's lock.

Trigger (Pico button): a short press pauses or resumes a running scan, or starts a new scan in
the project of the last scan when idle; a long press (>= 1 s) stops the scan. During a
calibration a short press captures a view (IMU: starts / stops the recording) and a long press
solves.
"""
from __future__ import annotations

import dataclasses
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any

from ohs import __version__
from ohs.server.calibration import CalibrationManager
from ohs.server.demo import default_demo_dir
from ohs.server.devices import DeviceManager
from ohs.server.hub import Hub
from ohs.server.jobs import JobRunner
from ohs.server.printables import MASK_DPI
from ohs.server.processing import ProcessManager
from ohs.server.projects import ProjectStore
from ohs.server.protocol import PROTOCOL_VERSION, QUALITIES, ApiError, jsonable, log_message, normalize_quality
from ohs.server.session import ScanSession
from ohs.server.setup import SetupManager
from ohs.server.util import join_thread, safe_call

__all__ = ["Studio", "MODES", "QUALITIES", "process_params"]

log = logging.getLogger(__name__)

MODES = ("markerless", "markers")
STATUS_HZ = 10.0


def process_params(cfg: Any, mode: str, quality: str, image_width: int) -> Any:
    """``ProcessParams`` for a quality (:func:`ohs.server.protocol.normalize_quality`; ``ValueError`` if unknown).

    "preview" = scale 0.5 and 2 mm voxels, every frame. "standard" and "best" = scale 1.0 with the
    ``[process]`` voxel size, step, frames picked by motion, Poisson and export formats (PLY and STL are
    always written); "standard" processes at most ``process.standard_frame_rate`` frames per second of
    scanning (every 2nd frame of a 60 fps recording at 30), "best" every frame. "full" is the old name
    of "standard"."""
    from ohs.app.pipeline import PipelineParams, default_fusion_params, effective_scale
    from ohs.app.process import ProcessParams
    from ohs.track.markers import MarkerParams

    quality = normalize_quality(quality)
    markers = MarkerParams(tag_mm=float(cfg.markers.tag_mm))
    export = tuple(dict.fromkeys(["ply", "stl", *[e.lower() for e in cfg.process.export]]))
    if quality == "preview":
        scale = 0.5
        fusion = dataclasses.replace(default_fusion_params(effective_scale(int(round(image_width * scale)))), voxel_mm=2.0)
        pp = PipelineParams(mode=mode, scale=scale, fusion=fusion, markers=markers)
        return ProcessParams(pipeline=pp, frame_step=1, export=("ply", "stl"), poisson=False)
    v = cfg.process.voxel_mm
    fusion = None if v is None else dataclasses.replace(default_fusion_params(effective_scale(image_width)), voxel_mm=float(v))
    pp = PipelineParams(mode=mode, scale=1.0, fusion=fusion, markers=markers)
    return ProcessParams(pipeline=pp, frame_step=int(cfg.process.frame_step), export=export, poisson=bool(cfg.process.poisson),
                         frame_motion_deg=float(cfg.process.frame_motion_deg), frame_max_gap=int(cfg.process.frame_max_gap),
                         max_frame_rate=float(cfg.process.standard_frame_rate) if quality == "standard" else 0.0)


class Studio:
    """See the module docstring."""

    def __init__(self, cfg: Any = None, *, projects_dir: str | Path | None = None, demo_dir: str | Path | None = None,
                 static_dir: str | Path | None = None, data_dir: str | Path | None = None,
                 config_path: str | Path | None = None, **device_kw: Any) -> None:
        from ohs.app.config import default_config_path

        if cfg is None:
            from ohs.app.config import load_config

            cfg = load_config()
        self.cfg = cfg
        self.projects_dir = Path(projects_dir).expanduser() if projects_dir is not None else cfg.paths.projects_path
        self.demo_dir = Path(demo_dir).expanduser() if demo_dir is not None else default_demo_dir()
        self.static_dir = Path(static_dir) if static_dir is not None else Path(__file__).with_name("static")
        self.data_dir = Path(data_dir).expanduser() if data_dir is not None else cfg.paths.data_path
        if config_path is not None:
            self.config_path = Path(config_path).expanduser()
        else:
            self.config_path = Path(cfg.source) if getattr(cfg, "source", None) else default_config_path()
        self.sim_dir = self.data_dir / "sim"
        self.sim_calib_path = self.sim_dir / "calibration.json"
        self.printables_mask_dpi = MASK_DPI  # tests lower it: the 1200 dpi mask sheet takes ~20 s
        self.hub = Hub()
        self.projects = ProjectStore(self.projects_dir)
        self.jobs = JobRunner(on_update=self._job_update)
        self.devices = DeviceManager(cfg, hub=self.hub, jobs=self.jobs, demo_dir=self.demo_dir, notify=self.notify,
                                     on_change=self.publish_device, on_lost=self._device_lost,
                                     on_trigger=self._trigger, sim_calib_path=self.sim_calib_path,
                                     on_connected=self._on_connected, on_launch_check=self._on_launch_check,
                                     **device_kw)
        self.setup = SetupManager(self)
        self.calibration = CalibrationManager(self)
        self.processing = ProcessManager(self)
        self.session: ScanSession | None = None
        self.last_session: ScanSession | None = None
        self.last_project: str | None = None
        self._lock = threading.RLock()
        self._events: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._started = False
        self._shut = False

    # ------------------------------------------------------------------ lifecycle
    def startup(self) -> None:
        if self._started:
            return
        self._started = True
        n = safe_call("checking for interrupted scans", self.projects.recover_stale) or 0
        if n:
            log.warning("%d scan(s) were interrupted when the app last stopped; their recordings are kept", n)
        for name, fn in (("ohs-status", self._status_loop), ("ohs-events", self._event_loop)):
            th = threading.Thread(target=fn, name=name, daemon=True)
            th.start()
            self._threads.append(th)
        # Pick the GPU (or the CPU) now, in the background: the first OpenCL setup takes about a second
        from ohs import compute

        compute.configure(getattr(getattr(self.cfg, "compute", None), "device", "auto"))
        threading.Thread(target=lambda: safe_call("choosing the compute device", compute.get_device),
                         name="ohs-compute", daemon=True).start()
        log.info("OpenHandScan Studio backend %s: projects in %s", __version__, self.projects_dir)

    def shutdown(self) -> None:
        """Stop a running scan (the recording is finalised), emitters off, disconnect, cancel jobs. Idempotent."""
        if self._shut:
            return
        self._shut = True
        log.info("shutting down")
        with self._lock:
            if self.session is not None and self.session.active:
                safe_call("stopping the scan", lambda: self._stop_scan_locked("the app is closing"))
            safe_call("cancelling the calibration", self.calibration.shutdown)
        safe_call("disconnecting the device", self.devices.shutdown)
        safe_call("stopping the jobs", lambda: self.jobs.shutdown(timeout=10.0))
        self._stop.set()
        for th in self._threads:
            join_thread(th, 3.0)

    # ------------------------------------------------------------------ messages
    def notify(self, level: str, message: str) -> None:
        getattr(log, {"warn": "warning"}.get(level, level), log.info)("%s", message)
        self.hub.publish_json(log_message(level, message))

    def publish_device(self) -> None:
        self.hub.publish_json({"type": "device", **self.devices.state()})

    def publish_session(self) -> None:
        self.hub.publish_json({"type": "session", **self.session_state()})

    def _job_update(self, job: dict) -> None:
        self.hub.publish_json({"type": "job", **job})
        processing = getattr(self, "processing", None)
        if processing is not None:
            processing.on_job(job)

    def session_state(self) -> dict:
        s = self.session
        if s is not None and s.active:
            return s.state_dict()
        return {"state": "idle", "project_id": None, "scan_id": None, "mode": "markerless", "elapsed_s": 0.0, "frames": 0}

    def status_message(self) -> dict:
        s = self.session
        dev = self.devices
        if s is not None and s.active:
            st = s.live_stats()
            tracking = st["tracking"]
            rec = getattr(s, "_recorder", None)  # frames waiting to be written (recorder queue + PNG writes)
            out = {"distance_mm": st.get("distance_mm"), "pipeline": st.get("fps_pipeline", 0.0),
                   "laser_points": st.get("laser_points", 0), "model": st.get("model"),
                   "extra": {"laser_points_frame": st.get("laser_points_frame", 0), "latency_ms": st.get("latency_ms"),
                             "process_ms": st.get("process_ms"), "dropped_frames": st.get("dropped_frames", 0),
                             "record_backlog": int(rec.backlog) if rec is not None else 0}}
        else:
            reason = "Not scanning" if dev.connected else ("Connecting..." if dev.busy else "No device connected")
            tracking = {"state": "idle", "reason": reason, "code": None, "fitness": None, "rmse_mm": None}
            idle = dev.idle_stats()
            last = self.last_session
            model = last.live_stats().get("model") if last is not None else None
            out = {"distance_mm": idle.get("distance_mm"), "pipeline": 0.0, "laser_points": 0,
                   "model": model or {"vertices": 0, "triangles": 0}, "extra": {"record_backlog": 0}}
        msg = {"type": "status", "t": round(time.time(), 3), "session": self.session_state(), "tracking": tracking,
               "distance_mm": out["distance_mm"], "fps": {"camera": dev.camera_fps(), "pipeline": out["pipeline"]},
               "laser_points": out["laser_points"], "model": out["model"], "emitters": dev.emitter_levels()}
        msg.update(out["extra"])
        return msg

    def seed_client(self, client: Any) -> None:
        """Current device, session, calibration session, setup, active jobs and (while scanning) the
        latest mesh for a new client."""
        self.hub.offer(client, "device", {"type": "device", **self.devices.state()})
        self.hub.offer(client, "session", {"type": "session", **self.session_state()})
        self.hub.offer(client, "calib_session", {"type": "calib_session", **self.calibration.state()})
        setup = safe_call("reading the setup state", self.setup.state)
        if setup is not None:
            self.hub.offer(client, "setup", {"type": "setup", **setup})
        for job in self.jobs.active():
            self.hub.offer(client, f"job:{job.id}", {"type": "job", **job.to_dict()})
        safe_call("seeding the process states", lambda: self.processing.seed(client))
        s = self.session
        if s is not None and s.active and s.latest_mesh is not None:
            self.hub.offer(client, "mesh", s.latest_mesh)

    def _on_connected(self, source: str) -> None:
        """A device connected (from DeviceManager, under its connect lock): record it for the setup."""
        self.setup.record_connect(source)

    def _on_launch_check(self, source: str, result: dict) -> None:
        """The launch check finished (its own thread): record it for the setup."""
        self.setup.record_launch_check(source, result)

    def _status_loop(self) -> None:
        period = 1.0 / STATUS_HZ
        while not self._stop.wait(period):
            if self.hub.n_clients:
                safe_call("publishing the status", lambda: self.hub.publish_json(self.status_message()))

    # ------------------------------------------------------------------ device events
    def _device_lost(self, kind: str, message: str) -> None:
        self._events.put(("lost", kind, message))

    def _trigger(self, kind: str) -> None:
        self._events.put(("trigger", kind, ""))

    def _event_loop(self) -> None:
        while not self._stop.is_set():
            try:
                ev, kind, msg = self._events.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if ev == "lost":
                    self._handle_lost(kind, msg)
                elif ev == "trigger":
                    self._handle_trigger(kind)
            except ApiError as e:
                self.notify("warn", str(e))
            except Exception as e:  # never let the event thread die
                log.exception("handling %s/%s failed", ev, kind)
                self.notify("error", f"internal error while handling {ev} ({e}); see the log")

    def _handle_lost(self, kind: str, message: str) -> None:
        with self._lock:
            stopped = None
            if self.session is not None and self.session.active:
                stopped = safe_call("stopping the scan", lambda: self._stop_scan_locked(message))
            safe_call("cancelling the calibration", lambda: self.calibration.on_device_lost(kind, message))
            self.devices.handle_lost(kind, message)
        tail = " The scan was stopped and saved." if stopped else ""
        if kind == "eof":
            self.notify("info", "The replayed recording ended; the device disconnected." + tail +
                        " Connect again (or replay with loop) to continue.")
        else:
            self.notify("error", f"Device problem: {message}.{tail}")

    def _handle_trigger(self, kind: str) -> None:
        if self.calibration.active:
            self.calibration.on_trigger(kind)
            return
        s = self.session
        if kind == "long":
            if s is not None and s.active:
                self.stop_scan()
            return
        if s is not None and s.active:
            if s.state == "paused":
                self.resume_scan()
            elif s.state == "scanning":
                self.pause_scan()
            return
        if self.last_project is not None:
            try:
                self.projects.get(self.last_project)
            except ApiError:
                self.last_project = None
        if self.last_project is None:
            self.notify("info", "Trigger pressed: choose a project and press Start in the app to begin scanning")
            return
        self.start_scan(self.last_project)

    # ------------------------------------------------------------------ device operations
    def connect(self, source: str, recording: str | None = None, loop: bool | None = None) -> tuple[int, dict]:
        with self._lock:
            if self.session is not None and self.session.active:
                raise ApiError(409, "a scan is running", "stop the scan before changing the device")
            if self.calibration.active:
                raise ApiError(409, "a calibration is running", "finish or cancel the calibration before changing the device")
            job = self.devices.connect(source, recording, loop)
        if job is not None:
            return 202, {"job": job.to_dict()}
        return 200, self.devices.state()

    def disconnect(self) -> dict:
        with self._lock:
            if self.session is not None and self.session.active:
                self._stop_scan_locked("the device was disconnected")
            if self.calibration.active:
                s = self.calibration.session
                folder = s.recording if s is not None else None
                self.calibration._end(s)
                self.notify("info", "Calibration cancelled: the device was disconnected"
                                    + (f"; the captured views are kept in {folder}" if folder else ""))
                self.calibration.publish()
            self.devices.disconnect("requested")
        return self.devices.state()

    def set_emitters(self, levels: dict) -> dict:
        """Set emitter levels now; during a scan they are also kept for the rest of it, and while
        paused they are only stored (the emitters stay off until Resume)."""
        s = self.session
        state = s.state if s is not None else "idle"
        self.devices.set_emitters(levels, scanning=state in ("scanning", "paused"), apply=state != "paused")
        return self.devices.state()

    # ------------------------------------------------------------------ scanning
    def _wait_first_frame(self, timeout: float = 3.0) -> tuple[int, int] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            size = self.devices.frame_size
            if size is not None:
                return size
            time.sleep(0.02)
        return self.devices.frame_size

    def start_scan(self, project_id: str, mode: str = "markerless", name: str | None = None) -> dict:
        if mode not in MODES:
            raise ApiError(400, f"unknown mode {mode!r}", "use markerless or markers")
        with self._lock:
            if self.session is not None and self.session.active:
                raise ApiError(409, "a scan is already running", "stop it first (Stop, or POST /api/scan/stop)")
            if self.calibration.active:
                raise ApiError(409, "a calibration is running", "finish or cancel the calibration in Setup first")
            dev = self.devices
            if dev.busy:
                raise ApiError(409, f"the device is not ready: {dev.busy}", "wait until it finishes")
            if not dev.connected:
                raise ApiError(409, "no device is connected",
                               "connect the camera, a replay or the simulator in the device panel first")
            calib = dev.calib
            if calib is None:
                raise ApiError(409, "no calibration is loaded",
                               "calibrate the scanner first (ohs calibrate stereo, then lasers and imu), "
                               "or connect the simulator, which brings its own calibration")
            self.projects.get(project_id)  # 404 if missing
            size = self._wait_first_frame()
            if size is None:
                raise ApiError(409, "the device delivers no frames", "check the camera, or connect again")
            want = tuple(int(v) for v in calib.stereo.image_size)
            if tuple(size) != want:
                raise ApiError(409, f"the camera delivers {size[0]}x{size[1]} images per camera but the calibration is "
                                    f"for {want[0]}x{want[1]}",
                               "set camera.width / camera.height to the calibrated mode, or calibrate this mode")
            scan, rec_dir = self.projects.add_scan(project_id, name, mode)
            use_imu = calib.imu is not None and dev.imu_available()
            meta = {"purpose": "scan", "ohs_version": __version__, "mode": mode, "live_scale": float(self.cfg.live.scale),
                    "app": "OpenHandScan Studio", "calibration_source": dev.calib_path, "device": dev.source,
                    "camera": jsonable((dev.state().get("camera") or {}).get("info", {})),
                    "pico": (dev.state().get("pico") or None), "project_id": project_id, "scan_id": scan["id"],
                    "scan_name": scan["name"]}
            if dev.source in ("replay", "sim"):
                meta["replay_of"] = str((dev.state().get("replay") or {}).get("path"))
            session = ScanSession(calib=calib, mode=mode, rec_dir=rec_dir, project_id=project_id, scan_id=scan["id"],
                                  scan_name=scan["name"], cfg=self.cfg, hub=self.hub, use_imu=use_imu,
                                  use_lasers=bool(calib.lasers), source_name=dev.source_name(), meta=meta,
                                  emitters_on=lambda: dev.scan_emitters_on(mode), emitters_off=dev.emitters_off,
                                  notify=self.notify, has_emitters=dev.has_pico)
            try:
                session.start()
            except BaseException:
                safe_call("stopping the failed scan", session.stop)
                safe_call("removing the failed scan", lambda: self.projects.remove_scan(project_id, scan["id"]))
                raise
            self.session = session
            self.last_project = project_id
            dev.attach(session)
            notes = []
            if calib.imu is None:
                notes.append("no IMU calibration, so no gyro prior")
            elif not use_imu:
                notes.append("no IMU data, so no gyro prior")
            if not calib.lasers:
                notes.append("no laser planes, so no laser points")
            self.notify("info", f"Scanning '{scan['name']}' ({mode})" + (f"; note: {', '.join(notes)}" if notes else ""))
        self.publish_session()
        self.publish_device()
        return session.state_dict()

    def _session_or_409(self) -> ScanSession:
        s = self.session
        if s is None or not s.active:
            raise ApiError(409, "no scan is running", "start a scan first")
        return s

    def pause_scan(self) -> dict:
        with self._lock:
            s = self._session_or_409()
            s.pause()
        self.publish_session()
        self.publish_device()
        return s.state_dict()

    def resume_scan(self) -> dict:
        with self._lock:
            s = self._session_or_409()
            s.resume()
        self.publish_session()
        self.publish_device()
        return s.state_dict()

    def rewind_scan(self, seconds: float = 2.0) -> dict:
        with self._lock:
            s = self._session_or_409()
            res = s.rewind(seconds)
        if res.get("t0") is not None:
            self.notify("info", f"Rewound {res['seconds']:g} s: dropped {res['dropped_frames']} live frames, rebuilt the "
                                f"live model from {res['rebuilt_keyframes']} keyframes in {res['rebuild_s']:.1f} s. "
                                "Go back to the ghost to continue.")
        self.publish_session()
        out = s.state_dict()
        out["rewind"] = res
        return out

    def stop_scan(self) -> dict:
        with self._lock:
            self._session_or_409()
            return self._stop_scan_locked(None)

    def _stop_scan_locked(self, reason: str | None) -> dict:
        s = self.session
        assert s is not None
        s.mark_stopping()
        self.publish_session()
        self.devices.detach()
        summary = s.stop()
        fields: dict[str, Any] = {"status": "recorded", "n_frames": summary.get("n_frames", 0),
                                  "duration_s": round(float(summary.get("duration_s", 0.0)), 3)}
        if summary.get("error"):
            fields["error"] = f"recording problem: {summary['error']}"
        try:
            scan = self.projects.update_scan(s.project_id, s.scan_id, **fields)
        except ApiError as e:  # the project vanished meanwhile: the recording folder is still there
            log.error("could not update the scan entry: %s", e)
            scan = {"id": s.scan_id, "status": "recorded", **fields}
        self.last_session = s
        self.session = None
        self.publish_session()
        self.publish_device()
        msg = f"Scan saved: {fields['n_frames']} frames, {fields['duration_s']:.1f} s"
        self.notify("info", msg + (f" ({reason})" if reason else ""))
        return {"scan": scan}

    # ------------------------------------------------------------------ projects / processing
    def delete_project(self, pid: str) -> None:
        with self._lock:
            s = self.session
            if s is not None and s.active and s.project_id == pid:
                raise ApiError(409, "a scan of this project is recording", "stop the scan first")
            for job in self.jobs.active():
                if job.meta.get("project_id") == pid:
                    raise ApiError(409, "a scan of this project is being processed", "wait for the job or cancel it")
            self.processing.wait_moves(pid)  # an undo / redo of one of its scans finishes writing first
            self.projects.delete(pid)
            self.processing.forget(pid)

    def process_scan(self, pid: str, sid: str, quality: str = "standard") -> Any:
        """"Make mesh" (M1 ``POST .../process``): the processing studio's Default recipe at
        ``quality`` (standard, best or preview; "full" = standard): track, straighten ``auto``, then its
        edits. Job kind ``process_scan``."""
        try:
            quality = normalize_quality(quality)
        except ValueError:
            raise ApiError(400, f"unknown quality {quality!r}", "use standard, best or preview") from None
        return self.processing.make_mesh(pid, sid, quality)

    # ------------------------------------------------------------------ info
    def health(self) -> dict:
        import cv2

        try:
            import open3d

            o3d_version = open3d.__version__
        except Exception:  # optional
            o3d_version = None
        from ohs import compute

        return {"app": "openhandscan", "version": __version__, "api": PROTOCOL_VERSION,
                "engine": {"opencv": cv2.__version__, "open3d": o3d_version, "compute": compute.describe()}}

    def settings(self) -> dict:
        cfg = self.cfg
        out: dict[str, Any] = {}
        for f in dataclasses.fields(cfg):
            if not f.metadata.get("toml", True):
                continue
            out[f.name] = jsonable(dataclasses.asdict(getattr(cfg, f.name)))
        out["source"] = str(cfg.source) if cfg.source else None
        out["warnings"] = list(cfg.warnings)
        out["effective"] = {"projects_dir": str(self.projects_dir), "demo_dir": str(self.demo_dir),
                            "calibration": str(cfg.paths.calibration_path), "static_dir": str(self.static_dir),
                            "ui_built": (self.static_dir / "index.html").is_file(), "data_dir": str(self.data_dir),
                            "config_path": str(self.config_path), "printables_dir": str(cfg.paths.printables_path),
                            "sim_calibration": str(self.sim_calib_path)}
        from ohs import compute

        # one readable line (the Settings page lists `effective` as text); the structured form is
        # in /api/health under engine.compute
        out["effective"]["compute"] = compute.status()
        return out

    # ------------------------------------------------------------------ settings (M2)
    def set_camera_settings(self, index: int | None = None, swap_lr: bool | None = None) -> dict:
        """Save ``camera.index`` / ``camera.swap_lr`` to the config file; used on the next connect."""
        from ohs.app.config import ConfigError, set_config_values

        updates: dict[str, Any] = {}
        if index is not None:
            updates["index"] = int(index)
        if swap_lr is not None:
            updates["swap_lr"] = bool(swap_lr)
        with self._lock:
            if self.session is not None and self.session.active:
                raise ApiError(409, "a scan is running", "stop the scan before changing the camera settings")
            if self.calibration.active:
                raise ApiError(409, "a calibration is running", "finish or cancel it before changing the camera settings")
            if updates:
                try:
                    set_config_values({"camera": updates}, self.config_path, cfg=self.cfg)
                except (ConfigError, OSError) as e:
                    raise ApiError(500, f"the camera settings could not be saved to {self.config_path}: {e}",
                                   "check that the config file is valid TOML and writable") from e
        if updates:
            self.notify("info", "Camera settings saved"
                        + ("; connect the camera again to use them" if self.devices.source == "camera" else ""))
        return self.settings()
