"""A live scan: raw recording, the live ``FramePipeline``, previews, mesh snapshots, pause / rewind / stop.

Threads (all daemon threads, stopped and joined by :meth:`ScanSession.stop`):

* The **device threads** (:mod:`ohs.server.devices`) call :meth:`ScanSession.on_frame`,
  :meth:`~ScanSession.on_imu` and :meth:`~ScanSession.on_event`. While scanning, *every* frame goes
  to the recorder (:class:`ohs.app.live.Recorder`, its own writer thread and PNG pool); IMU samples
  and events are recorded while scanning and while paused (so offline tracking can bridge a pause
  with the gyro). The newest frame also goes to a latest-only slot for processing.
* ``ohs-scan`` **owns the** :class:`~ohs.app.pipeline.FramePipeline` (created, used and closed in
  this thread; it is not thread-safe) at ``PipelineParams(scale=cfg.live.scale (0.5), mode=...)``
  with the live depth range and voxel size (:meth:`ohs.app.live.PipelineProcessor.params`, the
  same settings as ``ohs scan``). Per frame it feeds the pending IMU samples, runs
  ``process()`` and publishes ``pose`` (and ``ghost`` = the last good pose while lost), updates
  the status numbers (tracking state, distance, rates, laser points) and hands previews and mesh
  snapshots to two encoder threads. Frames that were not recorded (before the recorder opened,
  while paused) are only *viewed*: rectified, depth and laser lines for the previews and the
  distance gauge, nothing integrated.
* ``ohs-scan-preview`` encodes the JPEG previews; ``ohs-scan-mesh`` decimates, colours and frames
  the mesh snapshots (:mod:`ohs.server.meshing`). Each keeps only the newest item.

**Mesh snapshots** are extracted from ``pipeline.mesh()`` in ``ohs-scan`` every 1.5 s (or every
5x the extraction time, whichever is longer, so extraction never takes more than ~20 % of the
thread) when the model changed and a client wants meshes.

**Rewind.** For every tracked (fused) frame the session keeps ``(t, T_wc, coverage cells, laser
count)``, and for keyframes (moved >= 4 mm or 2 deg, or 0.5 s since the last one) also the depth
map at half the live resolution as float16 (125 KB at 320x200). Above 256 MB of keyframes (about
4 minutes of continuous sweeping at the live rate) every other keyframe of the older half is
dropped, so long scans keep an ever sparser history. ``rewind(N)`` (run in ``ohs-scan``):

1. ``t1`` = the newest recorded frame time, ``t0 = t1 - N`` (frame clock; paused time counts);
2. every kept frame with ``t >= t0`` is dropped (its coverage and laser counts are subtracted);
3. the reconstruction and tracking TSDFs are reset and rebuilt from the remaining keyframes
   (depth upsampled back to the live size, the tracking model from the filtered depth as in the
   pipeline). At most ``2 s / cost-per-keyframe`` keyframes are re-integrated (~10 ms each on the
   development PC, so ~200): all of the most recent quarter of that budget and an even subsample
   of the older ones. The TSDF weights are then scaled by ``kept frames / rebuilt keyframes`` so
   that the rebuilt model keeps its "observation count" (it matters for the ray-cast and
   meshing weight thresholds); this needs the CPU device;
4. ``Event(t1, "cut", "t0,t1")`` goes into the recording; ``ohs process`` skips frames in that
   interval.

Limits: the rebuilt live model is coarser than before (half-resolution depth, subsampled keyframes)
until new frames refine it; the marker map (marker mode) is not rewound; the pipeline's own laser
cloud is not rewound (the laser count shown is the session's own, which is); and after a rewind the
tracker restarts from the last kept pose, so the user has to return near it (the ghost shows it).

Pause stops recording frames and integrating; the emitters switch off and come back on resume.
"""
from __future__ import annotations

import collections
import logging
import queue
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ohs.server.imaging import PreviewPublisher, ViewTools, central_distance, encode_jpeg, left_view
from ohs.server.meshing import CoverageGrid, decimate, dilate_on_mesh, quality_colors
from ohs.server.protocol import ApiError, ghost_message, mesh_message, pose_message
from ohs.server.util import LatestWorker, RateMeter, join_thread, safe_call

__all__ = ["ScanSession", "tracking_state", "LOST_HINTS"]

log = logging.getLogger(__name__)

MESH_PERIOD_S = 1.5
MAX_TRIANGLES = 300_000
REWIND_BUDGET_S = 2.0
KEYFRAME_MM = 4.0
KEYFRAME_DEG = 2.0
KEYFRAME_S = 0.5
MAX_KEYFRAME_BYTES = 256 * 1024 * 1024
MAX_CONSECUTIVE_ERRORS = 25
VIEW_HZ = 10.0
WEAK_FITNESS = 0.45
WEAK_RMSE_MM = 1.5
# what the surface can't pin down (MarkerlessTracker info["weak"]); "what happened: what to do"
SLIDE_HINT = "Flat surface: the scanner may slide. Keep an edge, a crease or a hole in view, or add AprilTags"
TURN_HINT = "Round or symmetric shape: the scanner may turn around it. Keep other shapes in view, or add AprilTags"
WEAK_HINT_WINDOW = 6  # the weak-direction hint shows when WEAK_HINT_MIN of the last WEAK_HINT_WINDOW
WEAK_HINT_MIN = 4  # frames have it, and goes when fewer do: it doesn't flicker as features come and go

LOST_HINTS = {
    "no_depth": "No depth: hold the scanner 200-350 mm from the surface (dark or shiny parts need matte spray)",
    "bad_depth": "Depth too noisy: hold steadier, 200-350 mm from the surface",
    "model_not_visible": "Pointing away from the scanned area: go back to where you were",
    "diverged": "Tracking failed: go back slowly to the scanned area",
    "few_inliers": "Too little overlap: slow down and go back to the scanned area",
    "low_fitness": "Too little overlap: slow down and go back to the scanned area",
    "high_rmse": "Surfaces do not line up: go back slowly to the scanned area",
    "jump": "Moved too fast: go back slowly to the scanned area",
    "no_tags": "No AprilTags in view: point at the tagged area",
    "few_tags": "Too few AprilTags in view: keep at least 2-3 tags visible",
}


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


def weak_kinds(info: dict) -> list[str]:
    """``"slide"`` / ``"turn"``: what the surface couldn't pin down in this frame (``info["weak"]``)."""
    return sorted({str(w.get("kind")) for w in info.get("weak") or () if isinstance(w, dict)})


def tracking_state(track: Any, mode: str) -> dict:
    """``{"state": "ok"|"weak"|"lost", "reason", "code", "fitness", "rmse_mm", "weak"}`` of a ``TrackResult``.

    ``weak`` lists what the surface couldn't pin down (``"slide"``, ``"turn"``). Sliding is a hint
    even with the gyro, which holds rotations only; turning only without it. Both use the code
    ``degenerate``.
    """
    info = getattr(track, "info", None) or {}
    fitness = _num(info.get("fitness"))
    rmse = _num(info.get("rmse_mm", info.get("rms_mm")))
    weak = weak_kinds(info)
    out: dict[str, Any] = {"state": "ok", "reason": None, "code": None, "fitness": fitness, "rmse_mm": rmse,
                           "weak": weak}
    if not getattr(track, "ok", False):
        code = str(info.get("reason", "lost"))
        out.update(state="lost", code=code,
                   reason=LOST_HINTS.get(code, f"Tracking lost ({code}): go back slowly to the scanned area"))
        return out
    if mode == "markers":
        n = int(info.get("n_tags", 0) or 0)
        if info.get("method") == "pnp" or 0 < n < 3:
            out.update(state="weak", code="few_tags", reason="Few AprilTags in view: keep at least 3 tags visible")
        return out
    if fitness is not None and fitness < WEAK_FITNESS:
        out.update(state="weak", code="low_overlap", reason="Little overlap with the model: slow down, keep scanned areas in view")
    elif rmse is not None and rmse > WEAK_RMSE_MM:
        out.update(state="weak", code="high_rmse", reason="Surfaces match poorly: move slower")
    elif "slide" in weak:
        out.update(state="weak", code="degenerate", reason=SLIDE_HINT)
    elif "turn" in weak and not info.get("gyro_prior"):
        out.update(state="weak", code="degenerate", reason=TURN_HINT)
    elif not weak and info.get("degenerate") and not info.get("gyro_prior"):  # an engine without info["weak"]
        out.update(state="weak", code="degenerate",
                   reason="Few shape features here: the scanner may slide (add AprilTags or include more shape)")
    return out


class WeakHintFilter:
    """Majority vote over the last frames for the weak-direction hint (code ``degenerate``).

    It shows once ``need`` of the last ``window`` frames have it and goes when fewer do; lost
    frames and the other warnings pass through unchanged.
    """

    def __init__(self, window: int = WEAK_HINT_WINDOW, need: int = WEAK_HINT_MIN) -> None:
        self._recent: collections.deque[bool] = collections.deque(maxlen=window)
        self._need = need
        self._last: dict | None = None

    def apply(self, trk: dict) -> dict:
        hinted = trk.get("code") == "degenerate"
        if hinted:
            self._last = trk
        self._recent.append(hinted)
        on = sum(self._recent) >= self._need
        if hinted and not on:
            return {**trk, "state": "ok", "code": None, "reason": None}
        if not hinted and on and trk.get("state") == "ok" and self._last is not None:
            return {**trk, "state": "weak", "code": "degenerate", "reason": self._last["reason"],
                    "weak": self._last.get("weak", [])}
        return trk


@dataclass
class _Kept:
    """A fused frame, as far as rewind needs it."""

    t: float
    T: np.ndarray
    keys: np.ndarray
    depth: np.ndarray | None  # half live resolution, float16, keyframes only
    n_laser: int


def _idle_tracking(reason: str | None) -> dict:
    return {"state": "idle", "reason": reason, "code": None, "fitness": None, "rmse_mm": None}


class ScanSession:
    """One scan from start to stop; see the module docstring. Methods are thread-safe."""

    def __init__(self, *, calib: Any, mode: str, rec_dir: str | Path, project_id: str, scan_id: str, cfg: Any,
                 hub: Any, use_imu: bool, use_lasers: bool, source_name: str = "camera", scan_name: str = "",
                 meta: dict | None = None, emitters_on: Callable[[], None] | None = None,
                 emitters_off: Callable[[], None] | None = None, notify: Callable[[str, str], None] | None = None,
                 has_emitters: bool = False) -> None:
        from ohs.app.live import LatestSlot, PipelineProcessor

        self.calib = calib
        self.mode = mode
        self.rec_dir = Path(rec_dir)
        self.scan_dir = self.rec_dir.parent
        self.project_id = project_id
        self.scan_id = scan_id
        self.scan_name = scan_name
        self.cfg = cfg
        self.hub = hub
        self.use_imu = bool(use_imu)
        self.use_lasers = bool(use_lasers)
        self.source_name = source_name
        self.meta = dict(meta or {})
        self._emitters_on = emitters_on or (lambda: None)
        self._emitters_off = emitters_off or (lambda: None)
        self._notify = notify or (lambda level, msg: None)
        self._has_emitters = bool(has_emitters)
        self.params = PipelineProcessor(calib, cfg, mode, use_imu=self.use_imu, use_lasers=self.use_lasers).params()
        self.depth_range = (float(cfg.live.depth_min_mm), float(cfg.live.depth_max_mm))

        self._lock = threading.RLock()
        self._state = "created"  # created -> starting -> scanning <-> paused -> stopping -> stopped
        self._stop_called = False
        self._summary: dict = {}
        self._slot = LatestSlot()
        self._cmds: queue.Queue = queue.Queue()
        self._stop_evt = threading.Event()
        self._ready = threading.Event()
        self._start_error: BaseException | None = None
        self._thread: threading.Thread | None = None
        self._recorder: Any = None
        self._imu_lock = threading.Lock()
        self._imu_pending: list = []
        self._previews = PreviewPublisher(hub, self.depth_range)
        self._preview_worker: LatestWorker | None = None
        self._mesh_worker: LatestWorker | None = None
        # recording bookkeeping
        self.n_recorded = 0
        self._t_first_rec: float | None = None
        self._t_last_rec: float | None = None
        self._t_last_frame: float | None = None
        self._active_s = 0.0
        self._t_active: float | None = None
        self._cuts: list[tuple[float, float]] = []
        # live model bookkeeping (processing thread)
        self._ring: list[_Kept] = []
        self._kf_bytes = 0
        self._last_kf: tuple[float, np.ndarray] | None = None
        self._coverage = CoverageGrid()
        self._last_good: np.ndarray | None = None
        self._laser_total = 0
        self._mesh_dirty = False
        self._t_mesh = 0.0
        self._mesh_period = MESH_PERIOD_S
        self._mesh_seq = 0
        self.latest_mesh: bytes | None = None
        self._rebuild_cost_s: float | None = None
        self._last_left: tuple[np.ndarray, Any] | None = None
        self._weak_hint = WeakHintFilter()
        self._t_view = 0.0
        self._errors = 0
        self._gpu_fallback: dict[str, str] | None = None  # stage -> reason, once a GPU stage failed in this scan
        self.disabled = False
        self.n_processed = 0
        self.n_tracked = 0
        self.n_lost = 0
        self.proc_rate = RateMeter()
        self._stats: dict = {"tracking": _idle_tracking("Starting the live processing..."), "distance_mm": None,
                             "laser_points": 0, "laser_points_frame": 0, "model": {"vertices": 0, "triangles": 0},
                             "latency_ms": None}

    # ------------------------------------------------------------------ status (any thread)
    @property
    def state(self) -> str:
        return self._state

    @property
    def active(self) -> bool:
        return self._state in ("starting", "scanning", "paused", "stopping")

    @property
    def recording(self) -> bool:
        return self._state in ("scanning", "paused")

    def elapsed_s(self) -> float:
        with self._lock:
            extra = 0.0 if self._t_active is None else time.monotonic() - self._t_active
            return self._active_s + extra

    def state_dict(self) -> dict:
        st = self._state
        public = {"starting": "scanning", "scanning": "scanning", "paused": "paused", "stopping": "stopping"}.get(st, "idle")
        return {"state": public, "project_id": self.project_id, "scan_id": self.scan_id, "mode": self.mode,
                "elapsed_s": round(self.elapsed_s(), 2), "frames": int(self.n_recorded), "name": self.scan_name}

    def live_stats(self) -> dict:
        s = dict(self._stats)
        s["fps_pipeline"] = round(self.proc_rate.value(), 2)
        s["dropped_frames"] = int(self._slot.dropped)
        s["gpu_fallback"] = self._gpu_fallback
        return s

    # ------------------------------------------------------------------ lifecycle (Studio, under its lock)
    def start(self, timeout: float = 120.0) -> None:
        """Create the pipeline (in its thread), switch the emitters on and open the recording.

        Frames are recorded from the moment the device feeds the session (``DeviceManager.attach``),
        which also hands over the recent IMU history through :meth:`seed_imu`.
        """
        with self._lock:
            if self._state != "created":
                raise RuntimeError("a session can only be started once")
            self._state = "starting"
        self._thread = threading.Thread(target=self._run, name="ohs-scan", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            self._abort_start()
            raise ApiError(500, f"the live processing did not start within {timeout:.0f} s",
                           "the PC may be overloaded; close other programs and try again")
        if self._start_error is not None:
            err = self._start_error
            self._abort_start()
            raise ApiError(500, f"the live processing could not start: {err}",
                           "check the calibration (ohs calibrate ...) and that Open3D is installed (pip install -e .[fusion])")
        try:
            self._start_recording()
        except BaseException:
            safe_call("cleaning up the failed scan start", self.stop)
            raise

    def _start_recording(self) -> None:
        from ohs.app.live import Recorder

        self._preview_worker = LatestWorker("ohs-scan-preview", self._encode_preview)
        self._mesh_worker = LatestWorker("ohs-scan-mesh", self._encode_mesh)
        safe_call("switching the emitters on", self._emitters_on)
        if self._has_emitters:
            time.sleep(0.08)  # let the exposure in flight finish before the first recorded frame
        try:
            rec = Recorder(self.rec_dir, calib=self.calib, source=self.source_name, meta=self.meta)
        except Exception as e:
            safe_call("switching the emitters off", self._emitters_off)
            self._abort_start()
            raise ApiError(500, f"cannot create the recording folder {self.rec_dir}: {e}",
                           "check that the projects folder is writable and the disk is not full") from e
        with self._lock:
            self._recorder = rec
            self._state = "scanning"
            self._t_active = time.monotonic()
        self.hub.publish_binary("mesh", mesh_message(0, np.zeros((0, 3)), np.zeros((0, 3)), np.zeros((0, 4)),
                                                     np.zeros((0, 3))))
        log.info("scan %s/%s started (%s, %s)", self.project_id, self.scan_id, self.mode, self.rec_dir)

    def seed_imu(self, history: list) -> None:
        """IMU samples from just before the first frame (recorded, and given to the gyro prior)."""
        if not history:
            return
        with self._lock:
            if self._recorder is not None:
                self._recorder.put_imu(list(history))
        with self._imu_lock:
            self._imu_pending = list(history) + self._imu_pending

    def _abort_start(self) -> None:
        self._stop_evt.set()
        join_thread(self._thread, 15.0)
        for w in (self._preview_worker, self._mesh_worker):
            if w is not None:
                w.close()
        with self._lock:
            self._state = "stopped"

    def pause(self) -> None:
        with self._lock:
            if self._state == "paused":
                return
            if self._state != "scanning":
                raise ApiError(409, "no scan is running", "start a scan first")
            self._state = "paused"
            if self._t_active is not None:
                self._active_s += time.monotonic() - self._t_active
                self._t_active = None
            self._put_event("session", "pause")
        safe_call("switching the emitters off", self._emitters_off)
        self._stats = {**self._stats, "tracking": _idle_tracking("Paused: press Resume to continue scanning")}
        log.info("scan %s paused", self.scan_id)

    def resume(self) -> None:
        with self._lock:
            if self._state == "scanning":
                return
            if self._state != "paused":
                raise ApiError(409, "no paused scan", "start a scan first")
        safe_call("switching the emitters on", self._emitters_on)
        with self._lock:
            self._state = "scanning"
            self._t_active = time.monotonic()
            self._put_event("session", "resume")
        log.info("scan %s resumed", self.scan_id)

    def rewind(self, seconds: float, timeout: float = 30.0) -> dict:
        """Drop the last ``seconds`` from the live model and record a cut (see the module docstring)."""
        s = float(seconds)
        if not (np.isfinite(s) and s > 0):
            raise ApiError(400, f"rewind needs a positive number of seconds, got {seconds!r}", 'send {"seconds": 2.0}')
        with self._lock:
            if self._state not in ("scanning", "paused"):
                raise ApiError(409, "no scan is running", "start a scan first")
        if self._thread is None or not self._thread.is_alive() or self.disabled:
            raise ApiError(409, "the live processing is not running, so there is no live model to rewind",
                           "stop the scan; the recording is complete and can be processed")
        fut: Future = Future()
        self._cmds.put(("rewind", s, fut))
        try:
            return fut.result(timeout=timeout)
        except TimeoutError:
            raise ApiError(503, "the rewind did not finish in time", "try again in a moment") from None

    def mark_stopping(self) -> None:
        """Enter ``stopping`` (no more frames are recorded); :meth:`stop` does the rest."""
        with self._lock:
            if self._state in ("starting", "scanning", "paused", "created"):
                self._state = "stopping"
                if self._t_active is not None:
                    self._active_s += time.monotonic() - self._t_active
                    self._t_active = None

    def stop(self) -> dict:
        """Stop processing, close the recording, write the thumbnail; returns the recording summary."""
        self.mark_stopping()
        with self._lock:
            if self._stop_called:
                return dict(self._summary)
            self._stop_called = True
            rec, self._recorder = self._recorder, None
        safe_call("switching the emitters off", self._emitters_off)
        self._stop_evt.set()
        join_thread(self._thread, 20.0)
        for w in (self._preview_worker, self._mesh_worker):
            if w is not None:
                w.close(timeout=5.0)
        summary: dict = {"n_frames": int(self.n_recorded), "duration_s": 0.0, "error": None}
        if rec is not None:
            s = safe_call("closing the recording", lambda: rec.close(
                mode=self.mode, live_stats={"processed": self.n_processed, "tracked": self.n_tracked,
                                            "lost": self.n_lost, "laser_points": self._laser_total},
                cuts=[list(c) for c in self._cuts], active_s=round(self._active_s, 3)))
            if s:
                summary.update(n_frames=int(s["n_frames"]), duration_s=float(s["duration_s"]), error=s["error"])
        summary["elapsed_s"] = round(self._active_s, 3)
        summary["thumbnail"] = safe_call("writing the thumbnail", self._write_thumbnail)
        # release the rewind buffer and the last mesh (up to a few hundred MB on long scans)
        self._ring = []
        self._kf_bytes = 0
        self._coverage.clear()
        self.latest_mesh = None
        self._last_left = None
        with self._lock:
            self._state = "stopped"
            self._summary = summary
        log.info("scan %s stopped: %d frames, %.1f s", self.scan_id, summary["n_frames"], summary["duration_s"])
        return dict(summary)

    def _write_thumbnail(self) -> str | None:
        last = self._last_left
        if last is None:
            return None
        img = left_view(*last)
        p = self.scan_dir / "thumbnail.jpg"
        p.write_bytes(encode_jpeg(img, 85))
        return str(p)

    def _put_event(self, kind: str, value: str) -> None:
        from ohs.types import Event

        t = self._t_last_frame
        if self._recorder is not None and t is not None:
            self._recorder.put_event(Event(float(t), kind, value))

    # ------------------------------------------------------------------ inputs (device threads)
    def on_frame(self, frame: Any, t_recv: float) -> None:
        with self._lock:
            self._t_last_frame = float(frame.t_host)
            recorded = self._state == "scanning" and self._recorder is not None
            if recorded:
                self._recorder.put_frame(frame)
                self.n_recorded += 1
                if self._t_first_rec is None:
                    self._t_first_rec = float(frame.t_host)
                self._t_last_rec = float(frame.t_host)
        if self._state in ("starting", "scanning", "paused"):
            self._slot.put((frame, float(t_recv), recorded))

    def on_imu(self, samples: list) -> None:
        if not samples:
            return
        with self._lock:
            if self._recorder is not None and self._state in ("scanning", "paused"):
                self._recorder.put_imu(samples)
        with self._imu_lock:
            self._imu_pending.extend(samples)
            if len(self._imu_pending) > 50000:
                del self._imu_pending[:-50000]

    def on_event(self, event: Any) -> None:
        with self._lock:
            if self._recorder is not None and self._state in ("scanning", "paused"):
                self._recorder.put_event(event)

    def _take_imu(self) -> list:
        with self._imu_lock:
            out, self._imu_pending = self._imu_pending, []
        return out

    # ------------------------------------------------------------------ processing thread
    def _run(self) -> None:
        from ohs.app.pipeline import FramePipeline

        try:
            pipe = FramePipeline(self.calib, self.params)
            tools = ViewTools.from_pipeline(pipe, self.depth_range)
        except BaseException as e:  # reported by start()
            log.exception("the live pipeline could not be created")
            self._start_error = e
            self._ready.set()
            return
        self._ready.set()
        try:
            self._loop(pipe, tools)
        except Exception as e:  # keep the recording going; tell the user
            log.exception("live processing stopped")
            self.disabled = True
            self._stats = {**self._stats, "tracking": _idle_tracking(
                f"Live processing stopped ({e}); the recording continues")}
            self._notify("error", f"Live processing stopped ({e}). The recording continues; stop the scan and process it.")
        finally:
            self._fail_commands("the scan stopped")
            safe_call("closing the live pipeline", pipe.close)

    def _loop(self, pipe: Any, tools: ViewTools) -> None:
        while not self._stop_evt.is_set():
            self._handle_commands(pipe)
            item = self._slot.take(timeout=0.05)
            if item is None:
                try:
                    self._maybe_mesh(pipe)
                except Exception:
                    log.exception("live mesh snapshot failed")
                    self._mesh_dirty = False
                continue
            frame, t_recv, recorded = item
            imu = self._take_imu()
            if imu:
                pipe.add_imu(imu)
            if not recorded or self._state != "scanning" or self.disabled:
                try:
                    self._view(tools, frame, t_recv)
                except Exception:
                    log.exception("live preview failed on frame %d", frame.index)
                continue
            try:
                res = pipe.process(frame)
                self._after(pipe, res, t_recv)
                self._errors = 0
            except Exception as e:
                self._frame_error(frame, e)
                continue
            try:
                self._maybe_mesh(pipe)
            except Exception:
                log.exception("live mesh snapshot failed")
                self._mesh_dirty = False

    def _frame_error(self, frame: Any, e: Exception) -> None:
        self._errors += 1
        if self._errors == 1:
            log.exception("live processing failed on frame %d", frame.index)
            self._notify("warn", f"Live processing error: {e}")
        if self._errors >= MAX_CONSECUTIVE_ERRORS and not self.disabled:
            self.disabled = True
            self._stats = {**self._stats, "tracking": _idle_tracking(
                f"Live processing disabled after repeated errors ({e}); the recording continues")}
            self._notify("error", f"Live processing disabled after {self._errors} errors in a row ({e}). "
                                  "The recording continues; stop the scan and process it.")

    def _view(self, tools: ViewTools, frame: Any, t_recv: float) -> None:
        """Preview / distance only (not recorded, paused, starting or processing disabled)."""
        now = time.monotonic()
        if now - self._t_view < 1.0 / VIEW_HZ:
            return
        self._t_view = now
        v = tools.view(frame)
        self._last_left = (v.left, v.lines)
        self._stats = {**self._stats, "distance_mm": central_distance(v.depth),
                       "latency_ms": round(1e3 * (time.perf_counter() - t_recv), 1)}
        if self._previews.due() and self._preview_worker is not None:
            self._preview_worker.submit((v.left, v.lines, v.depth))

    def _after(self, pipe: Any, res: Any, t_recv: float) -> None:
        track = res.track
        ok = bool(track.ok)
        T = np.asarray(track.T_wc, dtype=np.float64)
        self.n_processed += 1
        self.proc_rate.tick()
        n_laser = int(sum(len(p) for p in (res.laser_points or {}).values())) if ok else 0
        if ok:
            self.n_tracked += 1
            self._last_good = T.copy()
            keys = (self._coverage.frame_keys(res.depth, pipe.K, T) if res.depth is not None
                    else np.zeros(0, np.int64))
            self._coverage.add(keys)
            depth_small = None
            if res.depth is not None and self._is_keyframe(res.t, T):
                depth_small = np.ascontiguousarray(res.depth[::2, ::2]).astype(np.float16)
                self._kf_bytes += depth_small.nbytes
                self._last_kf = (float(res.t), T.copy())
            self._ring.append(_Kept(float(res.t), T.copy(), keys, depth_small, n_laser))
            self._laser_total += n_laser
            self._mesh_dirty = True
            self._thin_keyframes()
            tim = res.timings_ms
            cost = 1e-3 * (float(tim.get("integrate", 0.0)) + float(tim.get("track_integrate", 0.0))) + 0.0015
            if self._rebuild_cost_s is None:
                self._rebuild_cost_s = max(cost, 0.002)
        else:
            self.n_lost += 1
        latency = round(1e3 * (time.perf_counter() - t_recv), 1)
        self._stats = {
            "tracking": self._weak_hint.apply(tracking_state(track, self.mode)),
            "distance_mm": central_distance(res.depth),
            "laser_points": int(self._laser_total),
            "laser_points_frame": n_laser,
            "model": self._stats.get("model", {"vertices": 0, "triangles": 0}),
            "latency_ms": latency,
            "process_ms": round(float(res.timings_ms.get("total", 0.0)), 1),
        }
        self.hub.publish_json(pose_message(T, ok))
        if not ok and self._last_good is not None:
            self.hub.publish_json(ghost_message(self._last_good))
        self._last_left = (res.left_r, res.lines)
        if pipe.gpu_fallbacks and self._gpu_fallback is None:  # read here: the pipeline belongs to this thread
            self._gpu_fallback = dict(pipe.gpu_fallbacks)
            self._notify("warn", "The graphics card stopped working for this scan (%s): it continues on the "
                                 "processor, slower." % ", ".join(self._gpu_fallback))
        elif self._gpu_fallback is not None and len(pipe.gpu_fallbacks) > len(self._gpu_fallback):
            self._gpu_fallback = dict(pipe.gpu_fallbacks)  # a second stage failed later: shown, not announced again
        if self._previews.due() and self._preview_worker is not None:
            self._preview_worker.submit((res.left_r, res.lines, res.depth))

    def _is_keyframe(self, t: float, T: np.ndarray) -> bool:
        if self._last_kf is None:
            return True
        from ohs.geometry import invert_se3, rotation_angle_deg

        t_kf, T_kf = self._last_kf
        D = invert_se3(T_kf) @ T
        return (t - t_kf >= KEYFRAME_S or float(np.linalg.norm(D[:3, 3])) >= KEYFRAME_MM
                or rotation_angle_deg(D[:3, :3]) >= KEYFRAME_DEG)

    def _thin_keyframes(self) -> None:
        if self._kf_bytes <= MAX_KEYFRAME_BYTES:
            return
        kfs = [e for e in self._ring if e.depth is not None]
        for e in kfs[: len(kfs) // 2][1::2]:
            self._kf_bytes -= e.depth.nbytes
            e.depth = None
        log.info("rewind buffer: thinned the older keyframes (%d MB kept)", self._kf_bytes >> 20)

    # ------------------------------------------------------------------ previews / mesh
    def _encode_preview(self, item: tuple) -> None:
        left, lines, depth = item
        self._previews.publish(left, lines, depth)

    def _maybe_mesh(self, pipe: Any, force: bool = False) -> None:
        now = time.monotonic()
        if not force:
            if not self._mesh_dirty or now - self._t_mesh < self._mesh_period or not self.hub.wants("mesh"):
                return
        if self._mesh_worker is None:
            return
        t0 = time.perf_counter()
        V, F, N = pipe.mesh()
        ex = time.perf_counter() - t0
        self._t_mesh = now
        self._mesh_dirty = False
        self._mesh_period = max(MESH_PERIOD_S, 5.0 * ex)
        self._mesh_seq += 1
        self._stats = {**self._stats, "model": {"vertices": int(len(V)), "triangles": int(len(F))}}
        self._mesh_worker.submit((self._mesh_seq, V, F, N, self._coverage.snapshot()))

    def _encode_mesh(self, item: tuple) -> None:
        seq, V, F, N, snap = item
        n_raw = int(len(F))
        V2, F2, N2, _ = decimate(V, F, N, None, MAX_TRIANGLES)
        if N2 is None or len(N2) != len(V2):
            N2 = np.zeros_like(np.asarray(V2, dtype=np.float64))
        counts = dilate_on_mesh(CoverageGrid.lookup(V2, snap, self._coverage.cell_mm), F2)
        data = mesh_message(seq, V2, N2, quality_colors(counts), F2, decimated=bool(len(F2) < n_raw))
        self.latest_mesh = data
        self.hub.publish_binary("mesh", data)

    # ------------------------------------------------------------------ commands (processing thread)
    def _handle_commands(self, pipe: Any) -> None:
        while True:
            try:
                kind, arg, fut = self._cmds.get_nowait()
            except queue.Empty:
                return
            if fut.set_running_or_notify_cancel():
                try:
                    if kind == "rewind":
                        fut.set_result(self._do_rewind(pipe, float(arg)))
                    else:  # pragma: no cover - no other commands
                        raise ValueError(kind)
                except BaseException as e:
                    log.exception("%s failed", kind)
                    fut.set_exception(e if isinstance(e, ApiError) else ApiError(500, f"{kind} failed: {e}",
                                                                                 "see the log; the recording is not affected"))

    def _fail_commands(self, why: str) -> None:
        while True:
            try:
                _, _, fut = self._cmds.get_nowait()
            except queue.Empty:
                return
            if fut.set_running_or_notify_cancel():
                fut.set_exception(ApiError(409, f"cannot rewind: {why}", "start a new scan"))

    def _do_rewind(self, pipe: Any, seconds: float) -> dict:
        from ohs.types import Event

        t_start = time.perf_counter()
        with self._lock:
            t1 = self._t_last_rec
        if t1 is None:
            return {"t0": None, "t1": None, "seconds": seconds, "dropped_frames": 0, "kept_frames": len(self._ring),
                    "rebuilt_keyframes": 0, "rebuild_s": 0.0}
        t0 = t1 - seconds
        prev_tracking = self._stats.get("tracking")
        self._stats = {**self._stats, "tracking": _idle_tracking("Rewinding: rebuilding the live model...")}
        keep = [e for e in self._ring if e.t < t0]
        drop = [e for e in self._ring if e.t >= t0]
        for e in drop:
            self._coverage.remove(e.keys)
            self._laser_total -= e.n_laser
            if e.depth is not None:
                self._kf_bytes -= e.depth.nbytes
        self._ring = keep
        n_rebuilt = self._rebuild(pipe, keep)
        kfs = [e for e in keep if e.depth is not None]
        self._last_kf = (kfs[-1].t, kfs[-1].T.copy()) if kfs else None
        self._last_good = keep[-1].T.copy() if keep else None
        with self._lock:
            if self._recorder is not None:
                self._recorder.put_event(Event(float(t1), "cut", f"{t0:.6f},{t1:.6f}"))
            self._cuts.append((float(t0), float(t1)))
        self._mesh_dirty = True
        safe_call("the mesh snapshot after the rewind", lambda: self._maybe_mesh(pipe, force=True))
        if self._last_good is not None:
            self.hub.publish_json(ghost_message(self._last_good))
        dt = time.perf_counter() - t_start
        self._stats = {**self._stats, "laser_points": int(self._laser_total),
                       "tracking": prev_tracking if self._last_good is None else
                       {**_idle_tracking("Rewound: go back to the ghost to continue"), "state": "lost",
                        "code": "rewound"}}
        out = {"t0": t0, "t1": t1, "seconds": seconds, "dropped_frames": len(drop), "kept_frames": len(keep),
               "rebuilt_keyframes": n_rebuilt, "rebuild_s": round(dt, 3)}
        log.info("rewind %.1f s: dropped %d frames, rebuilt from %d keyframes in %.2f s", seconds, len(drop), n_rebuilt, dt)
        return out

    def _rebuild(self, pipe: Any, keep: list[_Kept]) -> int:
        """Reset the TSDFs and re-integrate (a budgeted subset of) the kept keyframes."""
        import cv2

        from ohs.app.pipeline import tracking_depth, with_raw_depth

        rec_t = getattr(pipe, "markerless", None)
        trk = pipe.tracker if getattr(pipe, "dual_model", False) else None
        if rec_t is None:
            return 0
        rec_t.reset()
        detail = bool(getattr(trk, "detail_mode", False))
        if trk is not None:
            trk.reset()
            if detail:  # the scan has a detailed part: keep tracking it with the raw depth too
                trk.detail_mode, trk.detail_frame = True, None
        kfs = [e for e in keep if e.depth is not None]
        if not kfs:
            return 0
        cost = self._rebuild_cost_s or 0.012
        m = max(10, int(REWIND_BUDGET_S / max(cost, 1e-4)))
        if len(kfs) > m:
            n_recent = max(1, m // 4)
            recent, older = kfs[-n_recent:], kfs[:-n_recent]
            idx = np.unique(np.linspace(0, len(older) - 1, m - n_recent).round().astype(np.int64))
            sel = [older[i] for i in idx] + recent
        else:
            sel = kfs
        w, h = pipe.image_size
        sigma = 0.5 * float(getattr(pipe, "track_filter_px", 0.0))
        t0 = time.perf_counter()
        for e in sel:
            d = e.depth.astype(np.float32)
            rec_t.integrate(cv2.resize(d, (w, h), interpolation=cv2.INTER_NEAREST), None, e.T)
            if trk is not None:
                td = tracking_depth(d, sigma) if sigma > 0 else d
                if detail:
                    td = with_raw_depth(td, d)
                trk.integrate(cv2.resize(td, (w, h), interpolation=cv2.INTER_NEAREST), None, e.T)
        per = (time.perf_counter() - t0) / len(sel)
        self._rebuild_cost_s = per if self._rebuild_cost_s is None else 0.5 * (self._rebuild_cost_s + per)
        factor = min(8.0, len(keep) / float(len(sel)))
        if factor > 1.05:
            for tr in (rec_t, trk):
                if tr is not None:
                    _scale_weights(tr, factor)
        return len(sel)


def _scale_weights(tracker: Any, factor: float) -> None:
    """Multiply the TSDF weights of a ``MarkerlessTracker`` in place: the Open3D model or the GPU
    volume (whose ray cast needs a minimum weight, so a subsampled rebuild must restore them)."""
    try:
        scale = getattr(tracker, "scale_weights", None)
        if scale is not None:
            scale(float(factor))
            return
        attr = tracker.voxel_grid.attribute("weight")  # an older tracker without scale_weights()
        if "CPU" not in str(attr.device).upper():
            return
        arr = attr.numpy()
        arr *= np.float32(factor)
    except Exception as e:  # the rebuilt part of the model stays lighter than before the rewind
        log.warning("could not scale the TSDF weights: %s", e)
