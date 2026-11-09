"""The backend core of OpenHandScan Studio: devices, the scan session, projects, jobs and the hub.

:class:`Studio` is what the REST routes call (:mod:`ohs.server.app`). It enforces the rules of
APP_API.md (one scan at a time; scanning needs a device and a calibration; a recording scan can
not be processed or its project deleted) and turns every refusal into an
:class:`~ohs.server.protocol.ApiError` with a hint.

Threads it runs (from :meth:`Studio.startup` to :meth:`Studio.shutdown`):

* ``ohs-status`` publishes the ``status`` message at 10 Hz while a client is connected. It only
  reads snapshots (the session's and the device's latest numbers), so it keeps going while the
  processing thread is busy (a rewind, a slow frame).
* ``ohs-events`` handles what the device threads report (camera lost, end of a replay, Pico
  lost, trigger presses), so that stopping a scan never runs on, or joins, a device thread.

Locking: ``Studio._lock`` serialises the scan lifecycle (start / pause / resume / rewind / stop)
and device (dis)connection. The device, session, store, job and hub locks are only ever taken
inside it or on their own, never the other way round.

Trigger (Pico button): a short press pauses or resumes a running scan, or starts a new scan in
the project of the last scan when idle; a long press (>= 1 s) stops the scan.
"""
from __future__ import annotations

import dataclasses
import logging
import queue
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from ohs import __version__
from ohs.server.demo import default_demo_dir
from ohs.server.devices import DeviceManager
from ohs.server.hub import Hub
from ohs.server.jobs import JobRunner
from ohs.server.projects import ProjectStore, report_summary
from ohs.server.protocol import PROTOCOL_VERSION, ApiError, jsonable, log_message
from ohs.server.session import ScanSession
from ohs.server.util import join_thread, safe_call

__all__ = ["Studio", "MODES", "QUALITIES", "process_params"]

log = logging.getLogger(__name__)

MODES = ("markerless", "markers")
QUALITIES = ("preview", "full")
STATUS_HZ = 10.0


def process_params(cfg: Any, mode: str, quality: str, image_width: int) -> Any:
    """``ProcessParams`` for a quality: "preview" = scale 0.5 and 2 mm voxels, "full" = scale 1.0
    (``[process]`` voxel size, step, Poisson and export formats; PLY and STL are always written)."""
    from ohs.app.pipeline import PipelineParams, default_fusion_params, effective_scale
    from ohs.app.process import ProcessParams
    from ohs.track.markers import MarkerParams

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
    return ProcessParams(pipeline=pp, frame_step=int(cfg.process.frame_step), export=export, poisson=bool(cfg.process.poisson))


def _replace_dir(src: Path, dst: Path) -> None:
    """Move ``src`` to ``dst``, replacing ``dst`` (the old one is moved aside first, then removed)."""
    old = None
    if dst.exists():
        old = dst.with_name(f"{dst.name}.old-{int(time.time() * 1000)}")
        for attempt in range(50):
            try:
                dst.rename(old)
                break
            except OSError:  # Windows: a file of the old result is open (e.g. being downloaded)
                if attempt == 49:
                    raise
                time.sleep(0.1)
    for attempt in range(50):
        try:
            src.rename(dst)
            break
        except OSError:
            if attempt == 49:
                raise
            time.sleep(0.1)
    if old is not None:
        shutil.rmtree(old, ignore_errors=True)


class Studio:
    """See the module docstring."""

    def __init__(self, cfg: Any = None, *, projects_dir: str | Path | None = None, demo_dir: str | Path | None = None,
                 static_dir: str | Path | None = None, **device_kw: Any) -> None:
        if cfg is None:
            from ohs.app.config import load_config

            cfg = load_config()
        self.cfg = cfg
        self.projects_dir = Path(projects_dir).expanduser() if projects_dir is not None else cfg.paths.projects_path
        self.demo_dir = Path(demo_dir).expanduser() if demo_dir is not None else default_demo_dir()
        self.static_dir = Path(static_dir) if static_dir is not None else Path(__file__).with_name("static")
        self.hub = Hub()
        self.projects = ProjectStore(self.projects_dir)
        self.jobs = JobRunner(on_update=self._job_update)
        self.devices = DeviceManager(cfg, hub=self.hub, jobs=self.jobs, demo_dir=self.demo_dir, notify=self.notify,
                                     on_change=self.publish_device, on_lost=self._device_lost,
                                     on_trigger=self._trigger, **device_kw)
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
            out = {"distance_mm": st.get("distance_mm"), "pipeline": st.get("fps_pipeline", 0.0),
                   "laser_points": st.get("laser_points", 0), "model": st.get("model"),
                   "extra": {"laser_points_frame": st.get("laser_points_frame", 0), "latency_ms": st.get("latency_ms"),
                             "process_ms": st.get("process_ms"), "dropped_frames": st.get("dropped_frames", 0)}}
        else:
            reason = "Not scanning" if dev.connected else ("Connecting..." if dev.busy else "No device connected")
            tracking = {"state": "idle", "reason": reason, "code": None, "fitness": None, "rmse_mm": None}
            idle = dev.idle_stats()
            last = self.last_session
            model = last.live_stats().get("model") if last is not None else None
            out = {"distance_mm": idle.get("distance_mm"), "pipeline": 0.0, "laser_points": 0,
                   "model": model or {"vertices": 0, "triangles": 0}, "extra": {}}
        msg = {"type": "status", "t": round(time.time(), 3), "session": self.session_state(), "tracking": tracking,
               "distance_mm": out["distance_mm"], "fps": {"camera": dev.camera_fps(), "pipeline": out["pipeline"]},
               "laser_points": out["laser_points"], "model": out["model"], "emitters": dev.emitter_levels()}
        msg.update(out["extra"])
        return msg

    def seed_client(self, client: Any) -> None:
        """Current device, session, active jobs and (while scanning) the latest mesh for a new client."""
        self.hub.offer(client, "device", {"type": "device", **self.devices.state()})
        self.hub.offer(client, "session", {"type": "session", **self.session_state()})
        for job in self.jobs.active():
            self.hub.offer(client, f"job:{job.id}", {"type": "job", **job.to_dict()})
        s = self.session
        if s is not None and s.active and s.latest_mesh is not None:
            self.hub.offer(client, "mesh", s.latest_mesh)

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
            self.devices.handle_lost(kind, message)
        tail = " The scan was stopped and saved." if stopped else ""
        if kind == "eof":
            self.notify("info", "The replayed recording ended; the device disconnected." + tail +
                        " Connect again (or replay with loop) to continue.")
        else:
            self.notify("error", f"Device problem: {message}.{tail}")

    def _handle_trigger(self, kind: str) -> None:
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
            job = self.devices.connect(source, recording, loop)
        if job is not None:
            return 202, {"job": job.to_dict()}
        return 200, self.devices.state()

    def disconnect(self) -> dict:
        with self._lock:
            if self.session is not None and self.session.active:
                self._stop_scan_locked("the device was disconnected")
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
            self.projects.delete(pid)

    def process_scan(self, pid: str, sid: str, quality: str = "full") -> Any:
        if quality not in QUALITIES:
            raise ApiError(400, f"unknown quality {quality!r}", "use preview or full")
        with self._lock:
            entry = self.projects.scan_entry(pid, sid)
            s = self.session
            if s is not None and s.active and s.project_id == pid and s.scan_id == sid:
                raise ApiError(409, "this scan is still recording", "stop the scan first")
            for job in self.jobs.active("process_scan"):
                if job.meta.get("project_id") == pid and job.meta.get("scan_id") == sid:
                    raise ApiError(409, "this scan is already being processed",
                                   f"wait for job {job.id} to finish, or cancel it")
            rec = self.projects.recording_dir(pid, sid)
            if not (rec / "frames.csv").is_file():
                raise ApiError(409, "this scan has no recording", "record the scan again")
            self.projects.update_scan(pid, sid, status="processing", error=None)
            mode = entry.get("mode") if entry.get("mode") in MODES else "markerless"
            result_dir = self.projects.result_dir(pid, sid)
            cfg = self.cfg
            store = self.projects

            def run(ctx: Any) -> dict:
                from ohs.app.process import process_recording
                from ohs.io.recording import RecordingReader

                reader = RecordingReader(rec)
                if len(reader) == 0:
                    raise ApiError(409, "the recording has no frames", "record the scan again")
                size = reader.meta.get("image_size") or list(reader.frame(0).image_size)
                params = process_params(cfg, mode, quality, int(size[0]))
                tmp = result_dir.with_name(f"result.tmp-{ctx.job.id}")
                shutil.rmtree(tmp, ignore_errors=True)
                ctx.progress(0.01, "Loading the recording", force=True)

                def progress(k: int, n: int, res: Any) -> None:
                    ctx.check()
                    lost = "" if res.track.ok else " (tracking lost)"
                    ctx.progress(0.02 + 0.9 * (k + 1) / max(n, 1), f"Processing frame {k + 1} of {n}{lost}")

                try:
                    report = process_recording(rec, tmp, params=params, progress=progress)
                    ctx.check()
                    ctx.progress(0.95, "Saving the result", force=True)
                    report["quality"] = quality
                    _replace_dir(tmp, result_dir)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                summary = report_summary(report)
                summary["quality"] = quality
                scan = store.update_scan(pid, sid, status="processed", report=summary, quality=quality, error=None)
                return {"project_id": pid, "scan_id": sid, "scan": scan, "message": "Mesh ready"}

            def done(job: Any) -> None:
                if job.state == "done":
                    self.notify("info", f"Processing done: {entry.get('name', sid)}")
                    return
                has_result = (result_dir / "mesh.ply").is_file()
                if job.state == "failed":
                    safe_call("updating the scan", lambda: store.update_scan(pid, sid, status="failed", error=job.error))
                    self.notify("error", f"Processing failed: {job.error}. The raw recording is kept; try again "
                                         "(or with quality 'preview').")
                else:
                    safe_call("updating the scan", lambda: store.update_scan(
                        pid, sid, status="processed" if has_result else "recorded"))
                    self.notify("info", "Processing cancelled; the raw recording is kept")

            job = self.jobs.submit("process_scan", run, message="Queued for processing",
                                   meta={"project_id": pid, "scan_id": sid, "quality": quality}, on_done=done)
        return job

    # ------------------------------------------------------------------ info
    def health(self) -> dict:
        import cv2

        try:
            import open3d

            o3d_version = open3d.__version__
        except Exception:  # optional
            o3d_version = None
        return {"app": "openhandscan", "version": __version__, "api": PROTOCOL_VERSION,
                "engine": {"opencv": cv2.__version__, "open3d": o3d_version}}

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
                            "ui_built": (self.static_dir / "index.html").is_file()}
        return out
