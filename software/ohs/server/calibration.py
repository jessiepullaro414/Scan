"""Calibration sessions of the setup wizard (APP_API.md, "M2: setup and calibration").

:class:`CalibrationSession` is a device *sink* like :class:`~ohs.server.session.ScanSession`
(``seed_imu`` / ``on_frame`` / ``on_imu`` / ``on_event``) for one kind: ``stereo``, ``lasers``,
``check`` (views) or ``imu`` (a continuous recording).

Threads:

* the **device threads** call the sink methods: the newest frame goes to a latest-only slot; while
  the IMU step records, every frame, IMU sample and event also goes to the recorder.
* ``ohs-calib`` (processing) owns the :class:`~ohs.server.calib_guide.BoardDetector`: per frame it
  detects the board, tracks steadiness, decides auto-captures, and publishes the ``calib`` live
  message (about 10 Hz) and the ``preview:calib_left`` / ``preview:calib_right`` images (about
  9 Hz, through ``ohs-calib-preview``, which downscales to 640 px and encodes the JPEGs).

**Views** (stereo, lasers, check) go at once into a snapshot recording folder
(:class:`SnapshotRecording`, the ordinary recording format of ARCHITECTURE.md section 6):
the PNG is written, then ``frames.csv`` and ``meta.json`` are rewritten (temporary file +
rename), so the folder is complete after every capture or delete. Frame numbers are the view
indices: 0, 1, 2... in capture order, never reused, so a deleted view leaves a gap. lasers / check
include the target's calibration as ``calibration.json``. Device target:
``<paths.scans_dir>/calibration/<kind>-YYYYmmdd-HHMMSS/`` (like ``ohs calibrate``); simulator:
``<data_dir>/sim/calibration/...``.

**Auto-capture** (stereo, lasers, check): a view is taken when it is usable, steady and new
(:mod:`ohs.server.calib_guide`), at least 1 s of frame time after the previous capture, until the
upper target view count. A manual capture needs only a usable view and a frame not captured yet.

**One laser at a time** (lasers, when every laser has its own channel: ``[lasers] own_channels``
with a family of two, and a Pico with firmware 1.2.0, or the simulator): a capture first keeps the
frame with every laser on (the view the guide judged), then lights each configured laser alone,
waits :data:`BURST_SETTLE_FRAMES` frames after each switch (the camera's frames already under way
still show the old lasers) and keeps the next frame, then lights them all again. The board must not
move meanwhile (corners within :data:`BURST_MOVE_PX`): else nothing is captured and the guide says
"Hold still". The view's one-laser frames go into the snapshot recording with indices from
:data:`BURST_INDEX0` on, and ``meta["lit"]`` says which laser each shows and which view it belongs
to; the laser solve then uses only those (``ohs.app.calib_flow``). A manual capture waits for its
frames (at most :data:`BURST_TIMEOUT_S`).

**IMU**: ``armed`` -> ``imu_start`` -> ``recording`` (every frame, the IMU and events through
:class:`ohs.app.live.Recorder`, with the IMU history of the last second) -> ``imu_stop`` ->
``capturing`` (ready to solve). Phases by frame time since the first recorded frame: still for
2 s, then rotate; stopping needs 22 s (2 s still + 20 s of rotation); it stops by itself at 62 s.
On the simulator, ``imu_start`` restarts the virtual scanner's script, so its still phase lines up.

:class:`CalibrationManager` is what :class:`~ohs.server.studio.Studio` calls: start (the rules:
no scan, a camera or the simulator, the stereo calibration first, the IMU for imu), capture,
delete, auto, imu start / stop, solve (the ``calibrate`` job via ``capture.run_flow``, then
:func:`ohs.server.calib_results.build_result`), continue, save (``capture.merge_calibration`` +
``capture.save_calibration``, with a ``.bak``), cancel. Emitters: the kind's profile while
capturing, off while solving and after every exit. The simulator switches to the kind's virtual
board on start and back to the scan demo on save or cancel.

Locking: lifecycle changes run under ``Studio._lock`` (so a scan and a calibration never start
together). ``CalibrationSession._lock`` guards the session's own state and is never held while
calling out (hub, devices, recorder close); ``_cap_lock`` serialises captures and deletes.
"""
from __future__ import annotations

import collections
import csv
import io
import json
import logging
import math
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ohs import __version__
from ohs.server import calib_guide as guide
from ohs.server import calib_results
from ohs.server.imaging import encode_jpeg
from ohs.server.protocol import ApiError, jsonable, now_iso, preview_message
from ohs.server.util import LatestWorker, RateMeter, join_thread, safe_call

__all__ = ["CalibrationSession", "CalibrationManager", "SnapshotRecording", "KINDS", "GUIDANCE", "TARGET_VIEWS",
           "MIN_VIEWS", "IMU_STILL_S", "IMU_MIN_S", "IMU_MAX_S"]

log = logging.getLogger(__name__)

KINDS = ("stereo", "lasers", "imu", "check")
TARGET_VIEWS = {"stereo": (15, 25), "lasers": (6, 10), "check": (3, 5), "imu": (0, 0)}
MIN_VIEWS = {"stereo": 4, "lasers": 3, "check": 1, "imu": 0}
IMU_STILL_S = 2.0
IMU_MIN_S = 22.0  # 2 s still + 20 s of rotation
IMU_MAX_S = 62.0  # 2 s still + 60 s of rotation
LIVE_HZ = 10.0
PREVIEW_HZ = 9.0
PREVIEW_W = 640
THUMB_W = 240
MAX_LINE_POINTS = 200  # per laser line in the live message
IMU_FLOW_FRAMES = 600  # the IMU flow uses at most about this many frames (step)
GUIDANCE = {
    "stereo": ("Hold the board 200–500 mm from the scanner in 15–25 different poses: cover the corners and edges of "
               "both pictures and tilt it 20–45° in different directions. Hold still for half a second at each pose."),
    "lasers": ("Hold the board 150–450 mm away in 6–10 poses at different distances and tilts up to 30°, with both "
               "laser lines crossing the white margin around the pattern."),
    "imu": ("Put the board on the table. Press Start, hold the scanner still for 2 s, then rotate it slowly about all "
            "three axes (±20–40°) for 20–60 s, keeping the board in view."),
    "check": "Hold the board near, in the middle and far away (3–5 views), with both laser lines on the page.",
}
_KIND_TITLE = {"stereo": "Stereo calibration", "lasers": "Laser calibration", "imu": "IMU calibration",
               "check": "Calibration check"}
_REASON_HINTS = (
    ("both cameras", "Hold the whole board in front of both cameras, 200–500 mm away."),
    ("not found", "Show the whole board to the camera and avoid reflections on it."),
    ("margin", "Move or turn the board so the line crosses the blank paper around the pattern."),
    ("no camera image", "Wait a moment for the camera, then try again."),
)


_COUNT_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def _reason_hint(reason: str) -> str:
    return next((h for k, h in _REASON_HINTS if k in reason), "Move the board to a new pose and try again.")


def guidance(kind: str, names: Any = None) -> str:
    """The step's guidance for the configured lasers ``names`` (the two standard ones: :data:`GUIDANCE`)."""
    names = tuple(names) if names else guide.STANDARD_LINES
    if kind not in ("lasers", "check") or names == guide.STANDARD_LINES:
        return GUIDANCE[kind]
    n = _COUNT_WORDS.get(len(names), str(len(names)))
    pairs = [fam for fam, members in guide.families(names).items() if len(members) > 1]
    if kind == "check":
        return f"Hold the board near, in the middle and far away (3–5 views), with all {n} laser lines on the page."
    text = (f"Hold the board 150–450 mm away in 6–10 poses at different distances and tilts up to 30°, with all {n} "
            "laser lines crossing the white margin around the pattern.")
    if pairs:
        what = " and the two ".join(pairs)
        text += f" If the two {what} lines run into each other on the board, hold it a little nearer or farther away."
    return text


def live_lines(d: guide.Detection) -> dict | None:
    """The ``lines`` of the live ``calib`` message: the two families' margin points and image
    points (the fields of the two-laser app), plus per configured laser ``points`` (None: not
    named yet), per family ``found``, ``line_uv`` (named lasers of a family with several) and
    ``meet_mm`` (where a family's two sheets meet, when known and inside the range of the laser
    views: :func:`ohs.server.calib_guide.meets_in_range`)."""
    if d.lines is None:
        return None
    m = d.margin or {}
    out = {"vertical": int(m.get("vertical", 0)), "horizontal": int(m.get("horizontal", 0)),
           "vertical_uv": _flat(d.lines["vertical"], 1, MAX_LINE_POINTS) or [],
           "horizontal_uv": _flat(d.lines["horizontal"], 1, MAX_LINE_POINTS) or []}
    pts = d.line_points if d.line_points is not None else {n: int(m.get(n, 0)) for n in d.names}
    out["points"] = {n: (None if v is None else int(v)) for n, v in pts.items()}
    out["found"] = {f: [int(x) for x in v] for f, v in (d.lines_found or {}).items()}
    out["line_uv"] = {n: _flat(uv, 1, MAX_LINE_POINTS) or [] for n, uv in (d.line_uv or {}).items()}
    out["meet_mm"] = guide.meets_in_range(d.meet_mm)
    return out


def _flat(a: np.ndarray | None, nd: int = 1, max_points: int | None = None) -> list[float] | None:
    if a is None:
        return None
    a = np.asarray(a, np.float64).reshape(-1, 2)
    if len(a) == 0:
        return []
    if max_points is not None and len(a) > max_points:
        a = a[np.linspace(0, len(a) - 1, max_points).round().astype(int)]
    if not np.all(np.isfinite(a)):
        return None
    return [round(float(v), nd) for v in a.reshape(-1)]


def _r(x: float | None, nd: int = 1) -> float | None:
    return None if x is None or not math.isfinite(float(x)) else round(float(x), nd)


def _replace(tmp: Path, dst: Path) -> None:
    for attempt in range(50):  # Windows: a reader (the thumbnail route, a virus scanner) may hold the file
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.02)


BURST_SETTLE_FRAMES = {"camera": 3, "sim": 1}  # frames to let pass after a laser switch before one is kept
BURST_MOVE_PX = 2.0  # the board may move this much (90th percentile of its corners) while the lasers take turns
BURST_INDEX0 = 100000  # frame indices of the one-laser frames in the snapshot recording start here
BURST_TIMEOUT_S = 8.0  # a manual capture waits this long for its one-laser frames
ONE_AT_A_TIME_TEXT = " For each view the lasers light up one at a time: hold the board still until the view is captured."


# --------------------------------------------------------------------------- the snapshot recording
class SnapshotRecording:
    """A recording folder of captured views, complete on disk after every change (see the module docstring)."""

    def __init__(self, path: str | Path, *, source: str, meta: dict, calib: Any = None) -> None:
        from ohs.io.recording import FORMAT_VERSION

        self.path = Path(path)
        (self.path / "frames").mkdir(parents=True, exist_ok=False)
        for name, header in (("imu.csv", "t_host,t_device,gx,gy,gz,ax,ay,az"), ("events.csv", "t_host,kind,value")):
            (self.path / name).write_text(header + "\n", encoding="utf-8")
        if calib is not None:
            calib.save(self.path / "calibration.json")
        self.meta: dict = {"format_version": FORMAT_VERSION,
                           "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "source": source, "n_frames": 0, **meta}
        self._lock = threading.Lock()
        self.write_index([])

    def add(self, index: int, frame: Any) -> str:
        import cv2

        name = f"{int(index):06d}.png"
        with self._lock:
            self.meta.setdefault("image_size", [int(v) for v in frame.image_size])
        if not cv2.imwrite(str(self.path / "frames" / name), frame.to_sbs(), [cv2.IMWRITE_PNG_COMPRESSION, 1]):
            raise OSError(f"cannot write {self.path / 'frames' / name}")
        return name

    def write_index(self, rows: list[tuple[int, float, str]]) -> None:
        """Rewrite ``frames.csv`` (index, t_host, file) and ``meta.json`` for exactly these frames."""
        with self._lock:
            buf = io.StringIO()
            w = csv.writer(buf, lineterminator="\n")
            w.writerow(["index", "t_host", "file"])
            for index, t, name in rows:
                w.writerow([int(index), f"{float(t):.6f}", name])
            tmp = self.path / "frames.csv.tmp"
            tmp.write_text(buf.getvalue(), encoding="utf-8")
            _replace(tmp, self.path / "frames.csv")
            self.meta["n_frames"] = len(rows)
            tmp = self.path / "meta.json.tmp"
            tmp.write_text(json.dumps(jsonable(self.meta), indent=2), encoding="utf-8")
            _replace(tmp, self.path / "meta.json")

    def remove(self, name: str) -> None:
        try:
            (self.path / "frames" / name).unlink()
        except OSError as e:
            log.warning("could not remove %s: %s", name, e)


# --------------------------------------------------------------------------- views
@dataclass
class _View:
    index: int
    t: float  # seconds since the session's first frame (frame clock)
    t_host: float
    auto: bool
    file: str
    corners_l: np.ndarray
    ids_l: np.ndarray
    corners_r: np.ndarray | None
    ids_r: np.ndarray | None
    distance_mm: float | None
    tilt_deg: float | None
    face: np.ndarray | None
    center_uv: np.ndarray | None
    lines: dict | None
    warnings: list[str] = field(default_factory=list)
    error_px: float | None = None
    bad: bool = False
    line_points: dict | None = None  # per configured laser: margin points, None when not named yet
    lines_found: dict | None = None  # per family: margin points per line found, largest first
    line_sep: dict | None = None  # per family with two lasers: the gap between its lines on the board (mm)
    lit: list = field(default_factory=list)  # one laser at a time: (frame index, t_host, file, laser) per laser

    def rows(self) -> list[tuple[int, float, str]]:
        """This view's frames in the snapshot recording: its own, then its one-laser frames."""
        return [(self.index, self.t_host, self.file)] + [(i, th, f) for i, th, f, _ in self.lit]

    def to_dict(self, stereo: bool) -> dict:
        return {"index": self.index, "t": round(self.t, 3), "auto": self.auto, "corners_left": int(len(self.ids_l)),
                "corners_right": int(len(self.ids_r)) if stereo and self.ids_r is not None else None,
                "distance_mm": _r(self.distance_mm), "tilt_deg": _r(self.tilt_deg),
                "lines": dict(self.lines) if self.lines is not None else None,
                "line_points": dict(self.line_points) if self.line_points is not None else None,
                "lines_found": {k: list(v) for k, v in self.lines_found.items()} if self.lines_found is not None else None,
                "thumb_url": f"/api/calibration/views/{self.index}/thumb.jpg",
                "lit": [laser for _, _, _, laser in self.lit] or None,
                "error_px": _r(self.error_px, 3), "bad": bool(self.bad), "warnings": list(self.warnings)}


# --------------------------------------------------------------------------- the session
class CalibrationSession:
    """One calibration session (one kind) from start to save / cancel; see the module docstring."""

    def __init__(self, *, kind: str, target: str, spec: Any, calib: Any, paper: str, hub: Any, rec_base: str | Path,
                 source_name: str, meta: dict | None = None, emitters: str = "", truth: Any = None,
                 restart: Callable[[], None] | None = None, notify: Callable[[str, str], None] | None = None,
                 on_change: Callable[[], None] | None = None, sim: bool = False, lines: Any = None,
                 light: Callable[[Any], None] | None = None, settle_frames: int = 3) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown calibration kind {kind!r}")
        from ohs.app.live import LatestSlot

        self.kind, self.target, self.spec, self.calib, self.paper = kind, target, spec, calib, paper
        self.lines = tuple(lines) if lines else guide.STANDARD_LINES  # the configured lasers ([lasers] lines)
        self.hub = hub
        self.rec_base = Path(rec_base)
        self.source_name = source_name
        self.meta = dict(meta or {})
        self.emitters = emitters
        self.truth = truth
        self.sim = bool(sim)
        self._restart = restart
        self._notify = notify or (lambda level, msg: None)
        self._on_change = on_change or (lambda: None)
        self.id = f"cal-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        self.started = now_iso()
        self.state = "armed" if kind == "imu" else "capturing"
        self.auto = True
        self.views: list[_View] = []
        self.result: dict | None = None
        self.report: dict | None = None
        self.value: Any = None
        self.job_id: str | None = None
        self.saved: dict | None = None
        self.recording: Path | None = None
        self._lock = threading.RLock()
        self._cap_lock = threading.Lock()
        self._slot = LatestSlot()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._start_error: BaseException | None = None
        self._thread: threading.Thread | None = None
        self._preview: LatestWorker | None = None
        self._snap: SnapshotRecording | None = None
        self._next_index = 0
        self._latest: tuple[Any, guide.Detection] | None = None
        self._steady = guide.SteadyTracker()
        self._steady_reset = False
        self._t0: float | None = None
        self._t_last_capture: float | None = None
        self._last_capture_frame: int | None = None
        self._thumbs: dict[int, bytes] = {}
        self.rate = RateMeter()
        self._t_live = 0.0
        self._t_preview = 0.0
        self._preview_seq = 0
        self._errors = 0
        # IMU recording
        self._imu_rec: Any = None
        self._imu_t0: float | None = None
        self._imu_t_last: float | None = None
        self._imu_seen = 0
        self._imu_board = 0
        self._imu_done_s: float | None = None
        self._imu_skip = 0
        self._imu_finish = threading.Event()
        self._imu_hist: collections.deque = collections.deque(maxlen=4096)
        self._gyro: collections.deque = collections.deque(maxlen=512)  # (t_host, |gyro| deg/s)
        self._last_frame_t: float | None = None
        # one laser at a time (lasers): light(names) lights only those lasers (None: all of them)
        self._light = light if kind == "lasers" else None
        self._settle = max(0, int(settle_frames))
        self._burst: dict | None = None
        self._seen_index = -1  # the newest frame index the device delivered
        self._next_lit = BURST_INDEX0

    @property
    def one_at_a_time(self) -> bool:
        """Does a capture light the lasers one at a time (module docstring)?"""
        return self._light is not None
    @property
    def active(self) -> bool:
        return self.state not in ("saved", "stopped")

    @property
    def stereo(self) -> bool:
        return self.kind == "stereo"

    def to_dict(self) -> dict:
        with self._lock:
            return {"state": self.state, "kind": self.kind, "target": self.target, "id": self.id, "started": self.started,
                    "auto": self.auto, "views": [v.to_dict(self.stereo) for v in self.views],
                    "target_views": list(TARGET_VIEWS[self.kind]), "min_views": MIN_VIEWS[self.kind],
                    "guidance": guidance(self.kind, self.lines) + (ONE_AT_A_TIME_TEXT if self.one_at_a_time else ""),
                    "emitters": self.emitters, "job_id": self.job_id,
                    "result": self.result, "recording": str(self.recording) if self.recording is not None else None,
                    "saved": self.saved, "lasers": list(self.lines), "one_at_a_time": self.one_at_a_time}

    # ------------------------------------------------------------------ lifecycle
    def start(self, timeout: float = 30.0) -> None:
        self._preview = LatestWorker("ohs-calib-preview", self._encode_previews)
        self._thread = threading.Thread(target=self._run, name="ohs-calib", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout) or self._start_error is not None:
            err = self._start_error
            self.stop()
            raise ApiError(500, f"the calibration could not start: {err or 'timeout'}",
                           "check the calibration file of this step; if it keeps failing, see the log (ohs -v app)")

    def stop(self) -> None:
        """Stop the threads; an IMU recording in progress is closed (kept on disk)."""
        with self._lock:
            if self.state != "saved":
                self.state = "stopped"
            rec, self._imu_rec = self._imu_rec, None
            burst = self._burst
        self._stop.set()
        if burst is not None:  # a manual capture waiting for its frames: wake it, light nothing
            self._end_burst(burst, ApiError(409, "Not captured: the calibration ended", "start it again"))
        join_thread(self._thread, 10.0)
        if self._preview is not None:
            self._preview.close(timeout=3.0)
        if rec is not None:
            s = safe_call("closing the IMU recording", lambda: rec.close(stopped="cancelled"))
            if s:
                log.info("IMU recording kept: %s", s.get("path"))

    def mark_saved(self, saved: dict) -> None:
        with self._lock:
            self.saved = saved
            self.state = "saved"

    # ------------------------------------------------------------------ sink (device threads)
    def seed_imu(self, history: list) -> None:
        with self._lock:
            self._imu_hist.extend(history or [])

    def on_frame(self, frame: Any, t_recv: float) -> None:
        with self._lock:
            self._last_frame_t = float(frame.t_host)
            self._seen_index = max(self._seen_index, int(frame.index))
            rec = self._imu_rec if self.state == "recording" else None
            if rec is not None and self._imu_skip > 0:
                self._imu_skip -= 1  # rendered before the simulator's restart took effect
                rec = None
            if rec is not None:
                t = float(frame.t_host)
                if self._imu_t0 is None:
                    self._imu_t0 = t
                if t - self._imu_t0 <= IMU_MAX_S:
                    rec.put_frame(frame)
                    self._imu_t_last = t
                else:
                    self._imu_finish.set()
        self._slot.put(frame)

    def on_imu(self, samples: list) -> None:
        if not samples:
            return
        with self._lock:
            self._imu_hist.extend(samples)
            for s in samples[-64:]:
                self._gyro.append((float(s.t_host), float(np.degrees(np.linalg.norm(s.gyro)))))
            if self._imu_rec is not None and self.state == "recording":
                if self._restart is not None and self._imu_t0 is None:
                    return  # simulator: nothing from before the restarted script's first recorded frame
                self._imu_rec.put_imu(samples)

    def on_event(self, event: Any) -> None:
        with self._lock:
            if self._imu_rec is not None and self.state == "recording":
                self._imu_rec.put_event(event)

    # ------------------------------------------------------------------ processing thread
    def _run(self) -> None:
        try:
            det = guide.BoardDetector(self.kind, self.spec, self.calib, self.paper, names=self.lines)
        except BaseException as e:  # reported by start()
            log.exception("the board detector could not be created")
            self._start_error = e
            self._ready.set()
            return
        self._ready.set()
        while not self._stop.is_set():
            if self._imu_finish.is_set():
                self._imu_finish.clear()
                safe_call("stopping the IMU recording", lambda: self.imu_stop(auto=True))
            frame = self._slot.take(timeout=0.05)
            if frame is None:
                continue
            try:
                d = det.detect(frame)
            except Exception as e:
                self._errors += 1
                if self._errors == 1:
                    log.exception("board detection failed on frame %d", frame.index)
                    self._notify("warn", f"Board detection failed ({e}); the calibration keeps trying")
                continue
            self._errors = 0
            try:
                self._after(frame, d)
            except Exception:
                log.exception("calibration live update failed")

    def _after(self, frame: Any, d: guide.Detection) -> None:
        if self._stop.is_set() or self.state in ("stopped", "saved"):
            return  # ended meanwhile: publish nothing more
        self.rate.tick()
        with self._lock:
            burst = self._burst
        if burst is not None:
            self._burst_step(burst, frame, d)
            return
        if self._steady_reset:
            self._steady_reset = False
            self._steady.reset()
        steady = self._steady.update(d)
        with self._lock:
            if self._t0 is None:
                self._t0 = d.t
            self._latest = (frame, d)
            views = list(self.views)
            state, auto = self.state, self.auto
            if state == "recording":
                self._imu_seen += 1
                self._imu_board += int(d.T_cb is not None)
        lo, hi = TARGET_VIEWS[self.kind]
        unusable = guide.usable_reason(d)
        reason = None
        capture = False
        if state == "capturing" and self.kind != "imu":
            new, close = guide.novelty(d, views) if unusable is None else (False, None)
            cooled = self._t_last_capture is None or d.t - self._t_last_capture >= guide.COOLDOWN_S
            if unusable is not None:
                reason = unusable[:1].upper() + unusable[1:]
            elif not new:
                reason = (f"Too close to view {close.index + 1}: move or tilt the board" if close is not None
                          else "Move the board to a new place")
            elif len(views) >= hi:
                reason = f"Enough views: press {guide.finish_label(self.kind)}"
            elif steady < 1.0:
                reason = "Hold still"
            elif not cooled:
                reason = "Hold still"
            capture = auto and reason is None and frame.index != self._last_capture_frame
        elif state == "solving":
            reason = "Calibrating…"
        elif state == "result":
            reason = "Press Continue to add more views"
        elif self.kind == "imu":
            reason = None if d.T_cb is not None else "Keep the whole board in view"
        ready = state == "capturing" and self.kind != "imu" and unusable is None
        if capture and self._light is not None:
            self._begin_burst(frame, d, auto=True)  # the view is stored when every laser has had its frame
            capture = False
            reason = "Lighting the lasers one at a time: hold still"
        if capture:
            try:
                view = self._store(frame, d, auto=True)
                self._steady.reset()
                views = list(self.views)
                steady, ready = 0.0, False
                reason = f"View {view.index + 1} captured: move the board to a new pose"
                safe_call("publishing the session", self._on_change)
            except ApiError:
                pass
            except Exception as e:
                log.exception("auto-capture failed")
                self._notify("error", f"The view could not be saved ({e}): check the free disk space")
        now = time.monotonic()
        if now - self._t_live >= 1.0 / LIVE_HZ - 0.004 or capture:
            self._t_live = now
            msg = self._live(d, steady, ready, reason, views)
            self.hub.publish_json(msg)
        hz = min(PREVIEW_HZ, float(self.hub.preview_hz()))
        if hz > 0 and now - self._t_preview >= 1.0 / hz - 0.004 and self._preview is not None:
            self._t_preview = now
            self._preview.submit((d.left, d.right if self.stereo else None))

    # ------------------------------------------------------------------ one laser at a time
    def _begin_burst(self, frame: Any, d: guide.Detection, auto: bool) -> dict:
        """Start lighting the lasers one at a time for a view of ``frame`` (module docstring)."""
        burst = {"frame": frame, "d": d, "auto": auto, "names": list(self.lines), "i": 0, "got": [],
                 "after": 0, "done": threading.Event(), "result": None}
        with self._lock:
            if self._burst is not None:
                raise ApiError(409, "Not captured: the lasers are still taking turns for the last view",
                               "wait a moment, then capture")
            self._burst = burst
        self._switch(burst)
        return burst

    def _capturing(self) -> bool:
        with self._lock:
            return self.state == "capturing" and not self._stop.is_set()

    def _switch(self, burst: dict) -> None:
        name = burst["names"][burst["i"]]
        if not self._capturing():  # solving or ended meanwhile: the manager has the emitters now
            self._end_burst(burst, ApiError(409, "Not captured: the calibration moved on", "capture again"))
            return
        try:
            self._light((name,))
        except Exception as e:
            self._end_burst(burst, ApiError(409, f"Not captured: the {guide.line_label(name)} laser could not be "
                                                 f"switched on alone ({e})", "check the Pico's USB cable"))
            return
        with self._lock:
            burst["after"] = self._seen_index + self._settle

    def _burst_step(self, burst: dict, frame: Any, d: guide.Detection) -> None:
        if burst["done"].is_set():
            return
        if not self._capturing():
            self._end_burst(burst, ApiError(409, "Not captured: the calibration moved on", "capture again"))
            return
        if int(frame.index) <= burst["after"]:
            return
        d0 = burst["d"]
        moved = None
        if d.ids_l is not None and d0.ids_l is not None and len(d.ids_l) and len(d0.ids_l):
            moved = guide._corner_motion(d0.corners_l, d0.ids_l, d.corners_l, d.ids_l)
        if moved is None or moved > BURST_MOVE_PX:
            self._end_burst(burst, ApiError(409, "Not captured: the board moved while the lasers took turns",
                                            "hold the board still until the view is captured"))
            return
        burst["got"].append((burst["names"][burst["i"]], frame))
        burst["i"] += 1
        if burst["i"] < len(burst["names"]):
            self._switch(burst)
            return
        self._end_burst(burst, None)

    def _end_burst(self, burst: dict, error: ApiError | None) -> None:
        """All lasers on again (while still capturing: else the manager has switched the emitters
        already); store the view (unless ``error``) and wake a waiting manual capture."""
        with self._lock:
            if burst.get("ending"):
                return  # ended by another thread
            burst["ending"] = True
        if self._capturing():
            safe_call("lighting every laser again", lambda: self._light(None))
        result: Any = error
        if error is None:
            try:
                result = self._store(burst["frame"], burst["d"], burst["auto"], lit=burst["got"])
                if burst["auto"]:
                    self._steady.reset()
                    safe_call("publishing the session", self._on_change)
            except ApiError as e:
                result = e
            except Exception as e:
                log.exception("capturing the view failed")
                self._notify("error", f"The view could not be saved ({e}): check the free disk space")
                result = ApiError(500, f"the view could not be saved: {e}", "check the free disk space")
        elif burst["auto"]:
            self._steady.reset()
        burst["result"] = result
        with self._lock:
            if self._burst is burst:
                self._burst = None
        burst["done"].set()

    def _live(self, d: guide.Detection, steady: float, ready: bool, reason: str | None, views: list) -> dict:
        lo, hi = TARGET_VIEWS[self.kind]
        lines = live_lines(d)
        if lines is not None and self.kind == "lasers":
            for fam, members in guide.families(self.lines).items():
                if len(members) == 2 and fam not in lines["meet_mm"]:
                    z0 = guide.meet_distance(views, fam)
                    if z0 is not None and guide.meets_in_range({fam: z0}):
                        lines["meet_mm"][fam] = z0
        imu = None
        if self.kind == "imu":
            imu = self._imu_live()
        return {
            "type": "calib", "t": round(time.time(), 3), "kind": self.kind, "state": self.state,
            "image_size": [int(v) for v in d.image_size], "fps": round(self.rate.value(), 2),
            "left": {"corners": _flat(d.corners_l)} if len(d.ids_l) else None,
            "right": ({"corners": _flat(d.corners_r)} if d.ids_r is not None and len(d.ids_r) else None)
            if self.stereo else None,
            "common": d.common if self.stereo else None,
            "board_found": bool(d.board_found), "distance_mm": _r(d.distance_mm), "tilt_deg": _r(d.tilt_deg),
            "outline": _flat(d.outline), "page": _flat(d.page), "lines": lines,
            "ready": bool(ready), "reason": reason, "steady": round(float(steady), 3), "auto": self.auto,
            "hint": guide.next_hint(self.kind, d, views, d.image_size, (lo, hi)),
            "coverage": guide.coverage(views, d.image_size, self.stereo) if self.kind != "imu" else None,
            "diversity": guide.diversity(self.kind, views) if self.kind != "imu" else None,
            "views": len(views), "target_views": [lo, hi], "imu": imu, "frame_t": round(d.t, 4),
        }

    def _imu_live(self) -> dict:
        with self._lock:
            state = self.state
            if state == "recording" and self._imu_t0 is not None and self._imu_t_last is not None:
                elapsed = self._imu_t_last - self._imu_t0
            else:
                elapsed = self._imu_done_s or 0.0
            seen, board = self._imu_seen, self._imu_board
            t_now = self._last_frame_t
            g = [w for t, w in self._gyro if t_now is None or t >= t_now - 0.15]
        if state == "recording":
            phase = "still" if elapsed < IMU_STILL_S else "rotate"
        elif self._imu_done_s is not None:
            phase = "done"
        else:
            phase = "armed"
        gyro = float(np.sqrt(np.mean(np.square(g)))) if g else 0.0
        return {"phase": phase, "elapsed_s": round(float(elapsed), 2), "still_s": IMU_STILL_S, "min_s": IMU_MIN_S,
                "max_s": IMU_MAX_S, "gyro_dps": round(gyro, 1),
                "board_pct": round(100.0 * board / seen, 1) if seen else 0.0}

    def _encode_previews(self, item: tuple) -> None:
        import cv2

        self._preview_seq += 1
        for key, cam, img in (("preview:calib_left", "calib_left", item[0]), ("preview:calib_right", "calib_right", item[1])):
            if img is None:
                continue
            h, w = img.shape[:2]
            if w > PREVIEW_W:
                img = cv2.resize(img, (PREVIEW_W, max(1, int(round(h * PREVIEW_W / w)))), interpolation=cv2.INTER_AREA)
            ph, pw = img.shape[:2]
            self.hub.publish_binary(key, preview_message(cam, encode_jpeg(img), pw, ph, self._preview_seq))

    # ------------------------------------------------------------------ views (API and processing threads)
    def _ensure_snapshot(self) -> SnapshotRecording:
        if self._snap is None:
            from ohs.app.live import new_recording_dir

            path = new_recording_dir(self.rec_base, self.kind)
            calib = self.calib if self.kind in ("lasers", "check") else None
            self._snap = SnapshotRecording(path, source=self.source_name, meta=self.meta, calib=calib)
            with self._lock:
                self.recording = path
            log.info("calibration views (%s) go to %s", self.kind, path)
        return self._snap

    def _view_warnings(self, d: guide.Detection) -> list[str]:
        out = []
        lo, hi = guide.DISTANCE_RANGE[self.kind]
        if d.distance_mm is not None and not lo <= d.distance_mm <= hi:
            out.append(f"Board at {d.distance_mm:.0f} mm (aim for {lo:.0f}–{hi:.0f} mm)")
        if self.stereo and d.ids_r is not None and min(len(d.ids_l), len(d.ids_r)) < 0.5 * self.spec.n_corners:
            out.append("Part of the board is outside one of the pictures")
        return out

    def _store(self, frame: Any, d: guide.Detection, auto: bool, lit: list | None = None) -> _View:
        with self._cap_lock:  # begin_solve takes it too: no view slips into a solve
            if self.state != "capturing":
                raise ApiError(409, f"views can only be captured while capturing (now: {self.state})",
                               "wait until the calibration finishes")
            if frame.index == self._last_capture_frame:
                raise ApiError(409, "Not captured: this camera frame was captured already",
                               "move the board to a new pose, then capture")
            snap = self._ensure_snapshot()
            with self._lock:
                idx = self._next_index
                self._next_index += 1
                t0 = self._t0 if self._t0 is not None else d.t
            name = snap.add(idx, frame)
            lit_rows = []
            for laser, f in lit or []:
                with self._lock:
                    li = self._next_lit
                    self._next_lit += 1
                lit_rows.append((li, float(f.t_host), snap.add(li, f), str(laser)))
            lines = None
            if d.margin is not None:
                lines = {"vertical": int(d.margin.get("vertical", 0)), "horizontal": int(d.margin.get("horizontal", 0))}
            view = _View(idx, float(d.t - t0), float(frame.t_host), bool(auto), name, np.array(d.corners_l),
                         np.array(d.ids_l), None if d.corners_r is None else np.array(d.corners_r),
                         None if d.ids_r is None else np.array(d.ids_r), d.distance_mm, d.tilt_deg,
                         None if d.face is None else np.array(d.face),
                         None if d.center_uv is None else np.array(d.center_uv), lines, self._view_warnings(d),
                         line_points=dict(d.line_points) if d.line_points is not None else None,
                         lines_found={k: list(v) for k, v in d.lines_found.items()} if d.lines_found is not None else None,
                         line_sep=dict(d.line_sep) if d.line_sep else None, lit=lit_rows)
            with self._lock:
                rows = [r for v in (*self.views, view) for r in v.rows()]
                lit_meta = {str(i): {"laser": laser, "view": v.index} for v in (*self.views, view) for i, _, _, laser in v.lit}
            if lit_meta or "lit" in snap.meta:
                snap.meta["lit"] = lit_meta
            snap.write_index(rows)  # on disk first: whoever sees the view in the session finds it in the recording
            with self._lock:
                self.views.append(view)
                self._last_capture_frame = int(frame.index)
                self._t_last_capture = float(d.t)
        log.info("calibration view %d captured (%s, %s)", idx, self.kind, "auto" if auto else "manual")
        return view

    def capture(self) -> _View:
        """A manual capture of the newest processed frame (409 with the plain reason if it cannot be used)."""
        with self._lock:
            if self.kind == "imu":
                raise ApiError(409, "the IMU step records continuously and takes no views",
                               "press Start to record, then Stop after 20–60 s of rotation")
            if self.state != "capturing":
                raise ApiError(409, f"views can only be captured while capturing (now: {self.state})",
                               "press Continue to add more views" if self.state == "result" else
                               "wait until the calibration finishes")
            latest = self._latest
        d = latest[1] if latest is not None else None
        reason = guide.usable_reason(d)
        if reason is not None:
            raise ApiError(409, f"Not captured: {reason}", _reason_hint(reason))
        if self._light is not None:
            burst = self._begin_burst(latest[0], d, auto=False)
            if not burst["done"].wait(BURST_TIMEOUT_S):
                self._end_burst(burst, ApiError(409, "Not captured: the camera sent no frames while the lasers took turns",
                                                "check the camera, then capture again"))
            if isinstance(burst["result"], ApiError):
                raise burst["result"]
            self._steady_reset = True
            return burst["result"]
        view = self._store(latest[0], d, auto=False)
        self._steady_reset = True
        return view

    def delete_view(self, index: int) -> None:
        with self._cap_lock:
            with self._lock:
                if self.state not in ("capturing", "result"):
                    raise ApiError(409, f"views cannot be removed now (state: {self.state})",
                                   "wait until the calibration finishes")
                view = next((v for v in self.views if v.index == int(index)), None)
                if view is None:
                    raise ApiError(404, f"no view {index}", "use an index from the session's views")
                self.views.remove(view)
                self._thumbs.pop(view.index, None)
                rows = [r for v in self.views for r in v.rows()]
                lit_meta = {str(i): {"laser": laser, "view": v.index} for v in self.views for i, _, _, laser in v.lit}
            if self._snap is not None:
                if "lit" in self._snap.meta:
                    self._snap.meta["lit"] = lit_meta
                self._snap.write_index(rows)
                for _, _, f in view.rows():
                    self._snap.remove(f)
        log.info("calibration view %d removed", index)

    def set_auto(self, enabled: bool) -> None:
        with self._lock:
            self.auto = bool(enabled)

    def thumb(self, index: int) -> bytes:
        import cv2

        with self._lock:
            view = next((v for v in self.views if v.index == int(index)), None)
            cached = self._thumbs.get(int(index))
            folder = self.recording
        if view is None or folder is None:
            raise ApiError(404, f"no view {index}", "use an index from the session's views")
        if cached is not None:
            return cached
        img = cv2.imread(str(folder / "frames" / view.file), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ApiError(404, f"the image of view {index} is missing", "capture the view again")
        left = img[:, : img.shape[1] // 2]
        h, w = left.shape[:2]
        small = cv2.resize(left, (THUMB_W, max(1, int(round(h * THUMB_W / w)))), interpolation=cv2.INTER_AREA)
        data = encode_jpeg(small, 80)
        with self._lock:
            self._thumbs[int(index)] = data
        return data

    # ------------------------------------------------------------------ IMU recording
    def imu_start(self) -> None:
        from ohs.app.live import Recorder, new_recording_dir

        with self._lock:
            if self.kind != "imu":
                raise ApiError(409, "only the IMU step records continuously", "capture views instead")
            if self.state not in ("armed", "capturing", "result"):
                raise ApiError(409, f"cannot start recording now (state: {self.state})",
                               "stop the recording first" if self.state == "recording" else "wait until it finishes")
        if self._restart is not None:
            safe_call("restarting the virtual scanner", self._restart)
        path = new_recording_dir(self.rec_base, "imu")
        try:
            rec = Recorder(path, calib=self.calib, source=self.source_name, meta=self.meta)
        except Exception as e:
            raise ApiError(500, f"cannot create the recording folder {path}: {e}",
                           "check that the folder is writable and the disk is not full") from e
        with self._lock:
            t_ref = self._last_frame_t
            hist = [] if self._restart is not None else [s for s in self._imu_hist
                                                        if t_ref is None or s.t_host >= t_ref - 1.0]
            self._imu_rec = rec
            self.recording = path
            self.state = "recording"
            self._imu_t0 = self._imu_t_last = None
            self._imu_seen = self._imu_board = 0
            self._imu_done_s = None
            self._imu_skip = 1 if self._restart is not None else 0
            self.result = None
            self._imu_finish.clear()
        if hist:
            rec.put_imu(hist)
        log.info("IMU recording to %s", path)

    def imu_stop(self, auto: bool = False) -> None:
        with self._lock:
            if self.state != "recording":
                if auto:
                    return
                raise ApiError(409, "the IMU is not recording", "press Start first")
            elapsed = (self._imu_t_last - self._imu_t0) if (self._imu_t0 is not None and self._imu_t_last is not None) else 0.0
            if not auto and elapsed < IMU_MIN_S:
                raise ApiError(409, f"Keep rotating: {max(1, math.ceil(IMU_MIN_S - elapsed))} s more",
                               "the IMU calibration needs 2 s of holding still and then at least 20 s of rotation; "
                               "or cancel to start over")
            rec, self._imu_rec = self._imu_rec, None
            self.state = "capturing"
            self._imu_done_s = float(elapsed)
            seen, board = self._imu_seen, self._imu_board
        s = rec.close(stopped="auto" if auto else "user", board_fraction=round(board / seen, 3) if seen else None)
        log.info("IMU recording stopped after %.1f s: %d frames", elapsed, s.get("n_frames", 0))
        if auto:
            self._notify("info", f"The IMU recording stopped after {elapsed:.0f} s: press Finish & calibrate")
        safe_call("publishing the session", self._on_change)

    # ------------------------------------------------------------------ solving
    def begin_solve(self) -> dict:
        with self._cap_lock, self._lock:
            if self.state not in ("capturing", "result"):
                raise ApiError(409, f"cannot calibrate now (state: {self.state})",
                               "stop the IMU recording first" if self.state == "recording" else
                               "press Start and record first" if self.state == "armed" else "wait for it to finish")
            if self.kind == "imu":
                if self.recording is None or self._imu_done_s is None:
                    raise ApiError(409, "there is no IMU recording yet", "press Start, hold still 2 s, rotate 20–60 s, "
                                                                       "then press Stop")
            else:
                need, n = MIN_VIEWS[self.kind], len(self.views)
                if n < need:
                    raise ApiError(409, f"capture at least {need} view{'s' if need != 1 else ''} first ({n} so far)",
                                   guidance(self.kind, self.lines))
            self.state = "solving"
            self.result = None
            return {"views": [v.index for v in self.views], "recording": self.recording,
                    "n_frames": len(self.views)}

    def finish_solve(self, result: dict, report: dict | None, value: Any) -> bool:
        with self._lock:
            if self.state != "solving":
                return False
            self.result = result
            self.report = report
            self.value = value
            per = {int(p["index"]): p for p in result.get("per_view") or []}
            if self.kind != "imu":
                for v in self.views:
                    p = per.get(v.index)
                    v.error_px = None if p is None else p.get("error_px")
                    v.bad = bool(p.get("bad")) if p is not None else False
            self.state = "capturing" if result.get("grade") == "failed" else "result"
        return True

    def solve_aborted(self, why: str | None = None) -> None:
        with self._lock:
            if self.state != "solving":
                return
            self.state = "capturing"
            if why:
                self.result = calib_results.failed_result(self.kind, why)

    def continue_(self) -> None:
        with self._lock:
            if self.state not in ("result", "capturing"):
                raise ApiError(409, f"nothing to continue (state: {self.state})", "start a calibration first")
            self.state = "armed" if (self.kind == "imu" and self.state == "result") else "capturing"
            if self.kind == "imu" and self.state == "armed":
                self._imu_done_s = None
            self.result = None
        self._steady_reset = True


# --------------------------------------------------------------------------- the manager
def _idle(target: str, lines: Any = None) -> dict:
    return {"state": "idle", "kind": None, "target": target, "id": None, "started": None, "auto": True, "views": [],
            "target_views": [0, 0], "min_views": 0, "guidance": "", "emitters": "", "job_id": None, "result": None,
            "recording": None, "saved": None, "lasers": list(lines or guide.STANDARD_LINES), "one_at_a_time": False}


class CalibrationManager:
    """The calibration lifecycle for the :class:`~ohs.server.studio.Studio` (see the module docstring)."""

    def __init__(self, studio: Any) -> None:
        self.st = studio
        self.session: CalibrationSession | None = None  # the current one (active), or the last one saved
        self.last_report: dict | None = None
        self._job: Any = None

    # ------------------------------------------------------------------ status
    @property
    def active(self) -> bool:
        s = self.session
        return s is not None and s.active

    def state(self) -> dict:
        s = self.session
        if s is None:
            return _idle(self.st.setup.target(), self.lines())
        return s.to_dict()

    def lines(self) -> tuple[str, ...]:
        """The configured lasers (``[lasers] lines``)."""
        lasers = getattr(self.st.cfg, "lasers", None)
        return tuple(getattr(lasers, "lines", None) or guide.STANDARD_LINES)

    def publish(self) -> None:
        self.st.hub.publish_json({"type": "calib_session", **self.state()})

    def _active_or_409(self) -> CalibrationSession:
        s = self.session
        if s is None or not s.active:
            raise ApiError(409, "no calibration is running", "start one in Setup (stereo, lasers, IMU or check)")
        return s

    # ------------------------------------------------------------------ emitters
    def _levels(self, kind: str) -> tuple[float, float, float]:
        from ohs.app.capture import PROFILE

        return self.st.cfg.emitters.levels(PROFILE[kind])

    def _emitters_on(self, s: CalibrationSession) -> None:
        dev = self.st.devices
        if dev.source == "camera" and dev.has_pico and any(self._levels(s.kind)):
            dev.emitters_to(self._levels(s.kind))

    def _one_at_a_time(self, kind: str, source: str, cam: Any, lines: tuple[str, ...]) -> tuple[Any, str | None]:
        """``(light, why_not)``: the function that lights only some lasers, when the laser step can
        light them one at a time (module docstring), else None and, when the build asks for it but
        the device cannot, why."""
        lasers = getattr(self.st.cfg, "lasers", None)
        if kind != "lasers" or lasers is None or not lasers.separate():
            return None, None
        if source == "sim":
            if cam is None or not hasattr(cam, "light") or not set(lines) <= set(getattr(cam, "laser_names", ())):
                return None, None
            return cam.light, None
        dev = self.st.devices
        if not dev.has_pico:
            return None, None
        if not dev.pico_per_laser():
            return None, ("The Pico's firmware switches each pair of lasers together, so the lasers are calibrated "
                          "all at once. Update the firmware to 1.2.0 (firmware/main.py) to light them one at a time.")
        levels = self._levels(kind)

        def light(names: Any) -> None:
            only = None if names is None else tuple(lasers.channel_of(n) for n in names)
            if not dev.emitters_to(levels, only=only):
                raise RuntimeError("the Pico did not take the new levels")

        return light, None

    def _emitters_text(self, kind: str, source: str, has_pico: bool) -> str:
        lines = any(self._levels(kind))
        if source == "sim":
            return "Simulated line lasers on" if lines else "Emitters off: ambient light only (simulated)"
        if not lines:
            return "Emitters off: ambient light only"
        if not has_pico:
            return "No Pico: the line lasers stay off, so switch them on another way (Class 3R, never look into the beams)"
        return "Line lasers on: Class 3R, never look into the beams"

    # ------------------------------------------------------------------ start / end
    def start(self, kind: str) -> dict:
        st = self.st
        if kind not in KINDS:
            raise ApiError(400, f"unknown calibration kind {kind!r}", "use stereo, lasers, imu or check")
        warn = None
        with st._lock:
            if st.session is not None and st.session.active:
                raise ApiError(409, "a scan is running", "stop the scan first, then calibrate")
            if self.active:
                raise ApiError(409, f"a {self.session.kind} calibration is already running",
                               "finish or cancel it first")
            dev = st.devices
            if dev.busy:
                raise ApiError(409, f"the device is not ready: {dev.busy}", "wait until it finishes")
            if not dev.connected:
                raise ApiError(409, "no scanner is connected", "connect the camera or the simulator first (step 1)")
            source = dev.source
            if source == "replay":
                raise ApiError(409, "calibration needs the live camera or the simulator, not a replayed recording",
                               "connect the camera or the simulator first")
            target = "sim" if source == "sim" else "device"
            calib = st.setup.load_calibration(target)
            if kind != "stereo" and calib is None:
                raise ApiError(409, f"the {kind} step needs the stereo calibration first",
                               "calibrate the two cameras first (step 4, Stereo)")
            if kind == "imu" and source == "camera" and not dev.imu_available():
                raise ApiError(409, "the IMU calibration needs the Pico's IMU, which is not streaming",
                               "connect the Pico with a data USB cable, then connect the camera again")
            if source == "camera" and kind in ("lasers", "check") and not dev.has_pico:
                warn = ("No Pico: the line lasers cannot be switched on by the app. Switch them on another way, or "
                        "connect the Pico and start again. Class 3R lasers: never look into the beams.")
            if source == "sim":
                dev.set_sim_scene(f"calib_{kind}")
            s = None
            try:
                if kind != "stereo":
                    size = st._wait_first_frame(5.0)
                    want = tuple(int(v) for v in calib.stereo.image_size)
                    if size is not None and tuple(size) != want:
                        raise ApiError(409, f"the camera delivers {size[0]}x{size[1]} images but the stereo calibration "
                                            f"is for {want[0]}x{want[1]}",
                                       "calibrate the stereo pair again in this camera mode (step 4)")
                cam = dev.sim_camera() if source == "sim" else None
                spec = st.setup.calib_spec(target)
                lines = self.lines()
                base = (st.sim_dir / "calibration") if target == "sim" else (st.cfg.paths.scans_path / "calibration")
                board = {"squares_x": spec.squares_x, "squares_y": spec.squares_y, "square_mm": spec.square_mm,
                         "marker_mm": spec.marker_mm, "dictionary": spec.dictionary}
                meta = {"purpose": f"calib_{kind}", "ohs_version": __version__, "app": "OpenHandScan Studio",
                        "target": target, "board": board, "board_scale": st.setup.board_scale(target),
                        "paper": st.cfg.board.paper, "guidance": guidance(kind, lines), "camera": jsonable(dev.camera_info()),
                        "pico": dev.pico_hello() or None, "emitters": list(self._levels(kind)) if source == "camera" else None}
                if kind in ("lasers", "check"):
                    meta["lasers"] = list(lines)
                light, why_not = self._one_at_a_time(kind, source, cam, tuple(lines))
                if why_not:
                    warn = why_not
                if light is not None:
                    meta["one_at_a_time"] = True
                s = CalibrationSession(
                    kind=kind, target=target, spec=spec, calib=calib, paper=str(st.cfg.board.paper).lower(), hub=st.hub,
                    rec_base=base, source_name=dev.source_name(), meta=meta,
                    emitters=self._emitters_text(kind, source, dev.has_pico),
                    truth=getattr(cam, "true_calibration", None) if cam is not None else None,
                    restart=getattr(cam, "restart", None) if (cam is not None and kind == "imu") else None,
                    notify=st.notify, on_change=self.publish, sim=source == "sim", lines=lines,
                    light=light, settle_frames=BURST_SETTLE_FRAMES["sim" if source == "sim" else "camera"])
                s.start()
                self._emitters_on(s)
                dev.attach(s)
            except BaseException:
                dev.detach()
                if s is not None:
                    safe_call("stopping the failed calibration", s.stop)
                safe_call("switching the emitters off", dev.emitters_off)
                if source == "sim":
                    safe_call("switching the simulator back", lambda: dev.set_sim_scene("demo"))
                raise
            self.session = s
        st.notify("info", f"{_KIND_TITLE[kind]} started" + (f" ({s.emitters})" if source == "camera" else ""))
        if warn:
            st.notify("warn", warn)
        self.publish()
        st.publish_device()
        return s.to_dict()

    def _end(self, s: CalibrationSession, keep: bool = False) -> None:
        """Detach, stop, emitters off, cancel a solve, simulator back to the demo (under ``Studio._lock``)."""
        st = self.st
        st.devices.detach()
        s.stop()
        safe_call("switching the emitters off", st.devices.emitters_off)
        job = self._job
        if job is not None and job.active:
            safe_call("cancelling the calibration job", lambda: st.jobs.cancel(job.id))
        if s.target == "sim" and st.devices.source == "sim":
            try:
                st.devices.set_sim_scene("demo")
            except Exception as e:
                log.warning("could not switch the simulator back to the demo: %s", e)
        if not keep:
            self.session = None

    def cancel(self) -> dict:
        st = self.st
        with st._lock:
            s = self.session
            if s is None or not s.active:
                self.session = None
                return self.state()
            folder = s.recording
            self._end(s)
        st.notify("info", "Calibration cancelled" + (f"; the captured views are kept in {folder}" if folder else ""))
        self.publish()
        st.publish_device()
        return self.state()

    def on_device_lost(self, kind: str, message: str) -> None:
        """The device reported a failure (events thread, under ``Studio._lock``)."""
        s = self.session
        if s is None or not s.active:
            return
        if kind == "pico" and s.kind != "imu":
            self.st.notify("warn", "The Pico disconnected: the lasers are off. Connect it again before you capture more views.")
            return
        folder = s.recording
        self._end(s)
        self.st.notify("error", "The calibration was cancelled because the device stopped"
                                + (f"; the captured views are kept in {folder}" if folder else ""))
        self.publish()

    def shutdown(self) -> None:
        with self.st._lock:
            s = self.session
            if s is not None and s.active:
                self._end(s)

    def on_trigger(self, kind: str) -> None:
        """The Pico trigger during a session (events thread): short = capture (IMU: start / stop), long = solve."""
        s = self.session
        if s is None or not s.active:
            return
        try:
            if kind == "long":
                if s.state in ("capturing", "result"):
                    self.solve()
                return
            if s.kind == "imu":
                if s.state == "armed":
                    self.imu_start()
                elif s.state == "recording":
                    self.imu_stop()
                else:
                    self.st.notify("info", "Long-press the trigger to calibrate, or press Start in the app to record again")
                return
            if s.state == "capturing":
                self.capture()
            elif s.state == "result":
                self.st.notify("info", "Press Continue in the app to add more views, or save the calibration")
        except ApiError as e:
            self.st.notify("warn", e.detail[:1].upper() + e.detail[1:] + (f": {e.hint}" if e.hint else ""))

    # ------------------------------------------------------------------ views
    def capture(self) -> dict:
        s = self._active_or_409()
        view = s.capture()
        self.publish()
        return {"view": view.to_dict(s.stereo), "session": s.to_dict()}

    def delete_view(self, index: int) -> dict:
        s = self._active_or_409()
        s.delete_view(index)
        self.publish()
        return s.to_dict()

    def set_auto(self, enabled: bool) -> dict:
        s = self._active_or_409()
        s.set_auto(enabled)
        self.publish()
        return s.to_dict()

    def imu_start(self) -> dict:
        with self.st._lock:
            s = self._active_or_409()
            s.imu_start()
        self.st.notify("info", "IMU recording: hold the scanner still for 2 s, then rotate it slowly about all three axes")
        self.publish()
        return s.to_dict()

    def imu_stop(self) -> dict:
        s = self._active_or_409()
        s.imu_stop()
        return s.to_dict()

    def thumb(self, index: int) -> bytes:
        s = self.session
        if s is None:
            raise ApiError(404, f"no view {index}", "start a calibration and capture views first")
        return s.thumb(index)

    def report(self) -> dict:
        if self.last_report is None:
            raise ApiError(404, "no calibration has been solved yet", "press Finish & calibrate first")
        return self.last_report

    # ------------------------------------------------------------------ solve
    def solve(self) -> Any:
        st = self.st
        with st._lock:
            s = self._active_or_409()
            if self._job is not None and self._job.active:
                raise ApiError(409, "the calibration is already being computed", "wait for it to finish")
            snap = s.begin_solve()
        safe_call("switching the emitters off", st.devices.emitters_off)
        kind = s.kind

        def run(ctx: Any) -> dict:
            from ohs.app import capture
            from ohs.server.jobs import JobCancelled

            rec = snap["recording"]
            n_frames = 0
            try:
                from ohs.io.recording import RecordingReader

                n_frames = len(RecordingReader(rec))
            except Exception as e:
                raise ApiError(409, f"the recording {rec} cannot be read: {e}", "capture again") from e
            step = max(1, math.ceil(n_frames / IMU_FLOW_FRAMES)) if kind == "imu" else 1
            first = {"stereo": "Calibrating the two cameras (up to a minute)…",
                     "lasers": "Finding the laser lines and the board…", "imu": "Following the board in every frame…",
                     "check": "Checking the calibration…"}[kind]
            ctx.progress(0.03, first, force=True)

            def progress(k: int, n: int) -> None:
                ctx.check()
                what = "frame" if kind in ("imu", "check") else "view"
                ctx.progress(0.05 + 0.85 * (k + 1) / max(n, 1), f"Looking at {what} {k + 1} of {n}")

            try:
                value, report = capture.run_flow(kind, rec, s.spec, s.calib, step=step, paper=s.paper, progress=progress,
                                                 lasers=s.lines)
            except JobCancelled:
                raise
            except Exception as e:
                reason = calib_results.plain_error(e)
                res = calib_results.failed_result(kind, reason)
                self.last_report = {"kind": kind, "error": str(e), "recording": str(rec)}
                s.finish_solve(res, self.last_report, None)
                log.warning("%s calibration failed: %s", kind, e)
                raise ApiError(422, reason, res["advice"][0]["text"] if res["advice"] else None) from e
            ctx.progress(0.93, "Checking the result…", force=True)
            res = calib_results.build_result(kind, value, report if isinstance(report, dict) else {"report": report},
                                             view_ids=snap["views"], target=s.calib, truth=s.truth, sim=s.sim,
                                             lines=s.lines)
            full = jsonable(report if isinstance(report, dict) else {"report": report})
            full = {**full, "studio": {"kind": kind, "session": s.id, "target": s.target, "grade": res["grade"],
                                       "views": snap["views"]}}
            self.last_report = full
            safe_call("writing the report", lambda: capture.write_report(full, Path(rec) / f"calib_report_{kind}.json"))
            s.finish_solve(res, full, value)
            if kind == "check":
                parts = calib_results.check_failed_parts(report if isinstance(report, dict) else {}, bool(s.sim))
                st.setup.record_last_check(s.target, bool(res["ok"]), res["verdict"], parts=parts)
                safe_call("publishing the setup", st.setup.publish)
            if res["grade"] == "failed":
                raise ApiError(422, f"{res['headline']}: {res['verdict']}",
                               res["advice"][0]["text"] if res["advice"] else None)
            return {"kind": kind, "session": s.id, "result": res, "message": res["headline"]}

        def done(job: Any) -> None:
            if job.state == "cancelled":
                s.solve_aborted()
            elif job.state == "failed" and s.state == "solving":  # an unexpected error before the result
                s.solve_aborted(job.error or "the calibration failed")
            if s.active and s.state == "capturing":
                safe_call("switching the emitters on", lambda: self._emitters_on(s))
            if job.state == "done":
                self.st.notify("info", f"{s.result['headline']}: {s.result['verdict']}" if s.result else "Calibrated")
            elif job.state == "failed":
                self.st.notify("warn", f"Calibration failed: {job.error}")
            self.publish()

        job = st.jobs.submit("calibrate", run, message="Queued for calibration",
                             meta={"calib_kind": kind, "calib_session": s.id}, on_done=done)
        with s._lock:
            s.job_id = job.id
        self._job = job
        self.publish()
        return job

    def continue_(self) -> dict:
        with self.st._lock:
            s = self._active_or_409()
            s.continue_()
        if s.kind != "imu":
            safe_call("switching the emitters on", lambda: self._emitters_on(s))
        self.publish()
        return s.to_dict()

    # ------------------------------------------------------------------ save
    def save(self, force: bool = False) -> dict:
        from ohs.app import capture

        st = self.st
        with st._lock:
            s = self._active_or_409()
            r = s.result
            if s.kind == "check":
                raise ApiError(409, "a check is not saved: it only tells you whether the calibration still holds",
                               "its result is already in Setup; recalibrate the parts it flagged")
            if r is None or s.state != "result":
                raise ApiError(409, "there is no calibration result to save", "press Finish & calibrate first")
            if r.get("grade") == "failed":
                raise ApiError(409, "a failed calibration cannot be saved", "add views and calibrate again")
            if not r.get("ok") and not force:
                raise ApiError(409, f"this calibration is only {r.get('grade')}: saving it needs force",
                               "add views and calibrate again, or save it anyway (force)")
            path = st.setup.calibration_path(s.target)
            existing = st.setup.load_calibration(s.target)
            # the temperature of the step: its recording's (an IMU recording has it), else the IMU's now
            temp = capture.recording_temperature(s.recording) if s.recording is not None else None
            if temp is None:
                temp = safe_call("reading the IMU temperature", lambda: st.devices.imu_temp_c())
            new, _engine_notes = capture.merge_calibration(existing, s.kind, s.value, lasers=s.lines,
                                                           temperature_c=temp)
            try:
                backup = capture.save_calibration(new, path)
            except OSError as e:
                raise ApiError(500, f"the calibration could not be written to {path}: {e}",
                               "check that the folder is writable and the disk is not full") from e
            notes = self._notes(s.kind, existing, new, s.value, s.lines)
            if backup is not None:
                notes.append(f"The previous calibration is kept as {Path(backup).name}.")
            st.setup.record_part(s.target, s.kind, calib_results.part_summary(s.kind, s.report or {}, s.value), s.id)
            if s.kind == "stereo":
                for part, gone in (("lasers", not new.lasers), ("imu", new.imu is None)):
                    if gone:
                        st.setup.drop_part(s.target, part)
            s.mark_saved({"path": str(path), "backup": str(backup) if backup is not None else None, "notes": notes})
            self._end(s, keep=True)
            if s.target == "device":
                st.devices.reload_calibration()
        setup_state = st.setup.state()
        st.notify("info", f"Saved the {s.kind} calibration to {path}" + (f". {' '.join(notes)}" if notes else ""))
        st.hub.publish_json({"type": "setup", **setup_state})
        self.publish()
        st.publish_device()
        return {"session": s.to_dict(), "setup": setup_state}

    @staticmethod
    def _notes(kind: str, existing: Any, new: Any, value: Any, lines: Any = None) -> list[str]:
        notes: list[str] = []
        if kind == "stereo" and existing is not None:
            gone = (["laser planes"] if existing.lasers else []) + (["IMU calibration"] if existing.imu is not None else [])
            if gone:
                notes.append(f"The new stereo calibration replaces the old one, so the {' and the '.join(gone)} "
                             f"{'were' if len(gone) > 1 else 'was'} removed: calibrate "
                             f"{'them' if len(gone) > 1 else 'it'} again (steps 5 and 6).")
        if kind == "lasers":
            for name in (tuple(lines) if lines else guide.STANDARD_LINES):
                if name not in (value or {}):
                    kept = existing is not None and name in existing.lasers
                    notes.append(f"The {guide.line_label(name)} laser could not be calibrated from these views: "
                                 + ("its previous calibration was kept." if kept else "it is still missing."))
        return notes
