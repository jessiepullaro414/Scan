"""Live sessions: camera + Pico + recorder + processing threads behind an OpenCV preview window.

Used by ``ohs preview``, ``ohs record`` and ``ohs scan``, and (via :mod:`ohs.app.capture`) by
``ohs calibrate``. :class:`LiveSession` is the engine and the subclasses add the mode-specific
behaviour (keys, start/stop, what is drawn).

Threads (daemon threads, all stopped and joined by :meth:`LiveSession.run`):

* ``camera-grab`` (inside :class:`~ohs.devices.camera.StereoCamera`) grabs continuously and keeps
  the newest frame.
* ``ohs-capture`` reads every frame from the camera (``read()`` never returns a frame twice) and
  fans it out. While recording, *every* frame goes to the recorder. The processing slot keeps only
  the newest frame. With a :class:`~ohs.devices.camera.ReplayCamera` this thread also feeds the
  recording's IMU samples and events up to each frame's time (``ReplayCamera.reader``).
* ``ohs-pico`` drains the Pico's IMU and button queues every 5 ms. A trigger press (button
  "down") is queued as an action for the main thread.
* ``ohs-recorder`` (:class:`Recorder`) writes frames, IMU, events and the calibration snapshot
  through :class:`~ohs.io.recording.RecordingWriter`, whose own pool encodes the PNGs.
* ``ohs-process`` runs the session's processor on the newest frame and drops older frames when it
  falls behind. Processors such as ``FramePipeline`` are not thread-safe, so they are created,
  used and closed in this thread only. When idle (for example before a scan starts) the
  processor only produces a cheap view for the display.
* The main thread runs the window (``cv2.imshow``/``waitKey``) or, with ``gui=False``, a status
  loop that prints a line per second. It owns all session state: start/stop, keys, emitters.

Safety: emitters are switched on only while recording (scan, record), on request (preview keys)
or for the laser-plane capture. :meth:`LiveSession.run` always switches them off and flushes the
recording in ``finally``: normal end, Ctrl-C, window closed, camera failure, end of replay.
SIGINT is ignored while that cleanup runs, so a second Ctrl-C cannot leave a half-written
recording.

Replay: a ``ReplayCamera`` session never uses the Pico. IMU samples and events come from the
recording (its button events are copied into the new recording but do not start or stop it), and
recording starts with the first frame.
"""
from __future__ import annotations

import collections
import contextlib
import logging
import queue
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

import cv2
import numpy as np

from ohs import __version__
from ohs.devices.camera import ReplayCamera, StereoCamera
from ohs.devices.pico import Pico
from ohs.io.recording import RecordingWriter
from ohs.types import Event, ImuSample, ScannerCalibration, StereoFrame

__all__ = [
    "SessionResult",
    "Recorder",
    "LatestSlot",
    "ViewResult",
    "Processed",
    "Window",
    "LiveSession",
    "PreviewProcessor",
    "PipelineProcessor",
    "PreviewSession",
    "RecordSession",
    "ScanSession",
    "make_camera",
    "make_pico",
    "new_recording_dir",
    "check_output_dir",
    "run_preview",
    "run_record",
    "run_scan",
]

log = logging.getLogger(__name__)

Echo = Callable[[str], None]

# BGR colours for the HUD
WHITE = (240, 240, 240)
GREY = (170, 170, 170)
GREEN = (80, 220, 80)
RED = (60, 60, 255)
YELLOW = (40, 220, 255)
CYAN = (230, 210, 60)
FONT = cv2.FONT_HERSHEY_SIMPLEX
_COLORMAP = getattr(cv2, "COLORMAP_TURBO", cv2.COLORMAP_JET)

IMU_HISTORY_S = 1.0  # IMU samples recorded / given to the pipeline from just before the start
REPLAY_IMU_LOOKAHEAD_S = 0.05


# --------------------------------------------------------------------------- devices / folders
def make_camera(cfg: Any, replay: str | Path | None = None) -> Any:
    """Session camera: a real-time ``ReplayCamera`` for a recording, else ``StereoCamera(cfg.camera)``."""
    if replay is not None:
        return ReplayCamera(replay, realtime=True)
    return StereoCamera(cfg.camera)


def make_pico(cfg: Any) -> Pico:
    """Session Pico client (not opened yet)."""
    return Pico(cfg.pico)


def new_recording_dir(base: str | Path, prefix: str) -> Path:
    """``base/prefix-YYYYmmdd-HHMMSS`` (with a counter if that exists already)."""
    base = Path(base).expanduser()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = base / f"{prefix}-{stamp}"
    k = 2
    while path.exists():
        path = base / f"{prefix}-{stamp}-{k}"
        k += 1
    return path


def check_output_dir(path: str | Path) -> Path:
    """Fail early (before opening devices) if a recording folder exists and is not empty."""
    p = Path(path)
    if p.exists() and (not p.is_dir() or any(p.iterdir())):
        raise FileExistsError(f"output folder {p} is not empty; choose another one")
    return p


# --------------------------------------------------------------------------- small helpers
@contextlib.contextmanager
def _sigint_ignored() -> Iterator[None]:
    """Ignore Ctrl-C while cleanup runs (main thread only)."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    try:
        old = signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):  # pragma: no cover - embedded interpreters
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, old)


def _safe(what: str, fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except Exception:  # cleanup must go on
        log.exception("%s failed", what)
        return None


def fmt_time(seconds: float) -> str:
    m, s = divmod(max(0.0, seconds), 60.0)
    return f"{int(m):02d}:{s:04.1f}"


def emitter_text(levels: tuple[float, float, float], have_pico: bool) -> str:
    v, h, d = (float(x) for x in levels)
    txt = f"emitters V{v:.0f}% H{h:.0f}% D{d:.0f}%"
    return txt if have_pico else txt + " (no Pico)"


class LatestSlot:
    """Holds only the newest item. ``put`` replaces an item nobody took (counted in ``dropped``)."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._item: Any = None
        self.dropped = 0

    def put(self, item: Any) -> None:
        with self._cond:
            if self._item is not None:
                self.dropped += 1
            self._item = item
            self._cond.notify_all()

    def take(self, timeout: float) -> Any:
        with self._cond:
            if self._item is None:
                self._cond.wait(timeout)
            item, self._item = self._item, None
            return item


class _Rate:
    """Event rate (Hz) from an EMA of the intervals."""

    def __init__(self, alpha: float = 0.1) -> None:
        self.alpha, self._t, self._dt = alpha, None, None

    def tick(self, t: float) -> None:
        if self._t is not None and t > self._t:
            dt = t - self._t
            self._dt = dt if self._dt is None else (1 - self.alpha) * self._dt + self.alpha * dt
        self._t = t

    def value(self, now: float | None = None) -> float:
        if self._dt is None or self._dt <= 0:
            return 0.0
        if now is not None and self._t is not None and now - self._t > max(2.0, 5 * self._dt):
            return 0.0  # stalled
        return 1.0 / self._dt


# --------------------------------------------------------------------------- drawing
def gray_to_bgr(img: np.ndarray) -> np.ndarray:
    a = np.asarray(img)
    if a.ndim == 3:
        return a.copy()
    if a.dtype != np.uint8:
        a = cv2.convertScaleAbs(a)
    return cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)


def fit_width(img: np.ndarray, width: int) -> np.ndarray:
    h, w = img.shape[:2]
    if w == width or w == 0:
        return img
    s = width / w
    return cv2.resize(img, (width, max(1, int(round(h * s)))), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)


def side_by_side(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if a.shape[0] != b.shape[0]:
        b = cv2.resize(b, (max(1, int(round(b.shape[1] * a.shape[0] / b.shape[0]))), a.shape[0]))
    return np.hstack([a, b])


def depth_to_bgr(depth: np.ndarray, dmin: float, dmax: float) -> np.ndarray:
    """Depth (mm, 0/NaN invalid) as a colour map: near = red, far = blue, invalid = black."""
    d = np.nan_to_num(np.asarray(depth, dtype=np.float32), nan=0.0)
    valid = d > 0
    norm = np.clip((d - dmin) / max(dmax - dmin, 1e-6), 0.0, 1.0)
    col = cv2.applyColorMap((255.0 * (1.0 - norm)).astype(np.uint8), _COLORMAP)
    col[~valid] = 0
    return col


def paste_inset(dst: np.ndarray, src: np.ndarray, frac: float = 0.3, margin: int = 8) -> None:
    """Paste ``src`` scaled to ``frac`` of the width into the top-right corner of ``dst``."""
    h, w = dst.shape[:2]
    iw = max(8, int(w * frac))
    ih = max(8, int(round(src.shape[0] * iw / src.shape[1])))
    if ih + 2 * margin > h:
        return
    small = cv2.resize(src, (iw, ih), interpolation=cv2.INTER_AREA)
    x0, y0 = w - iw - margin, margin
    dst[y0 : y0 + ih, x0 : x0 + iw] = small
    cv2.rectangle(dst, (x0 - 1, y0 - 1), (x0 + iw, y0 + ih), WHITE, 1)


def draw_text(img: np.ndarray, text: str, org: tuple[int, int], scale: float = 0.55, color=WHITE, thick: int = 1) -> None:
    cv2.putText(img, text, org, FONT, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(img, text, org, FONT, scale, color, thick, cv2.LINE_AA)


def draw_hud(img: np.ndarray, lines: list[tuple[str, tuple]], bottom: list[tuple[str, tuple]] | None = None) -> None:
    """Text lines at the top-left and (optionally) the bottom-left of ``img``."""
    scale = max(0.4, min(0.8, img.shape[1] / 1600.0))
    step = int(round(30 * scale / 0.6))
    for k, (text, color) in enumerate(lines):
        draw_text(img, text, (10, step + k * step), scale, color)
    for k, (text, color) in enumerate(reversed(bottom or [])):
        draw_text(img, text, (10, img.shape[0] - 10 - k * step), scale * 0.9, color)


def waiting_image(text: str = "waiting for the camera...") -> np.ndarray:
    img = np.zeros((400, 640, 3), np.uint8)
    draw_text(img, text, (20, 200), 0.7, WHITE)
    return img


def _scale_image(img: np.ndarray, scale: float) -> np.ndarray:
    if scale == 1.0:
        return img
    h, w = img.shape[:2]
    return cv2.resize(img, (max(1, int(round(w * scale))), max(1, int(round(h * scale)))), interpolation=cv2.INTER_AREA)


class Window:
    """Thin wrapper around an OpenCV HighGUI window (resizable, closed-by-user detection)."""

    def __init__(self, title: str) -> None:
        self.title = title
        self.closed = False
        self._shown = False
        self._size: tuple[int, int] | None = None
        try:
            cv2.namedWindow(title, cv2.WINDOW_NORMAL)
        except cv2.error as e:
            raise RuntimeError(f"cannot open a window ({e}); use --no-gui") from None

    def show(self, img: np.ndarray | None, wait_ms: int = 5) -> int:
        """Show ``img`` (None = keep the old one) and return the key pressed (-1 = none)."""
        if img is not None:
            cv2.imshow(self.title, img)
            size = (int(img.shape[1]), int(img.shape[0]))
            if size != self._size:  # e.g. the "waiting" image, then the first real view
                with contextlib.suppress(cv2.error):
                    cv2.resizeWindow(self.title, *size)
                self._size = size
            self._shown = True
        key = cv2.waitKey(max(1, int(wait_ms)))
        if self._shown:
            try:
                if cv2.getWindowProperty(self.title, cv2.WND_PROP_VISIBLE) < 1:
                    self.closed = True
            except cv2.error:
                self.closed = True
        return -1 if key is None or key < 0 else int(key) & 0xFF

    def close(self) -> None:
        with contextlib.suppress(cv2.error):
            cv2.destroyWindow(self.title)
        with contextlib.suppress(cv2.error):
            cv2.waitKey(1)


# --------------------------------------------------------------------------- recorder
class Recorder:
    """Writes one recording folder from a queue in thread ``ohs-recorder``."""

    BACKLOG_WARN = 120  # frames waiting to be written before a warning (once): ~0.5 GB, ~2 s at 60 fps

    def __init__(self, path: str | Path, *, calib: ScannerCalibration | None = None, source: str = "camera",
                 meta: dict | None = None) -> None:
        self.path = Path(path)
        self.writer = RecordingWriter(self.path, source=source, **(meta or {}))
        if calib is not None:
            self.writer.write_calibration(calib)
        self._q: queue.Queue = queue.Queue()
        self.n_frames = 0
        self.n_imu = 0
        self.n_events = 0
        self.t_first: float | None = None
        self.t_last: float | None = None
        self.error: BaseException | None = None
        self._n_put = 0  # frames put (camera side) / handed to the writer (recorder thread), for backlog
        self._n_taken = 0
        self._backlog_warned = False
        self._closed = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="ohs-recorder", daemon=True)
        self._thread.start()

    def put_frame(self, frame: StereoFrame) -> None:
        if not self._closed:
            self._n_put += 1
            self._q.put(("f", frame))
            if not self._backlog_warned and self.backlog > self.BACKLOG_WARN:
                self._backlog_warned = True
                log.warning("recording %s: %d frames are waiting to be written (PNG encoding is behind the camera)",
                            self.path, self.backlog)

    def put_imu(self, samples: list[ImuSample]) -> None:
        if samples and not self._closed:
            self._q.put(("i", list(samples)))

    def put_event(self, event: Event) -> None:
        if not self._closed:
            self._q.put(("e", event))

    @property
    def backlog(self) -> int:
        """Frames put here and not written yet: in the queue, plus the writer's unwritten PNGs."""
        return max(0, self._n_put - self._n_taken) + self.writer.backlog

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            kind, x = item
            try:
                if kind == "f":
                    try:
                        self.writer.write_frame(x)  # waits while the writer has too many frames in flight
                    finally:
                        self._n_taken += 1
                    self.n_frames += 1
                    self.t_first = x.t_host if self.t_first is None else self.t_first
                    self.t_last = x.t_host
                elif kind == "i":
                    self.writer.write_imu(x)
                    self.n_imu += len(x)
                else:
                    self.writer.write_event(x)
                    self.n_events += 1
            except Exception as e:  # keep draining; the error is reported at close
                if self.error is None:
                    log.exception("writing to %s failed", self.path)
                self.error = e

    def summary(self) -> dict:
        dur = 0.0 if self.t_first is None or self.t_last is None else self.t_last - self.t_first
        return {"path": self.path, "n_frames": self.n_frames, "n_imu": self.n_imu, "n_events": self.n_events,
                "duration_s": dur, "error": None if self.error is None else str(self.error)}

    def close(self, **meta: Any) -> dict:
        """Write everything still queued, update ``meta.json`` and close the folder."""
        with self._lock:
            if self._closed:
                return self.summary()
            self._closed = True
        self._q.put(None)
        self._thread.join()
        s = self.summary()
        self.writer.update_meta(n_imu=s["n_imu"], n_events=s["n_events"], duration_s=round(s["duration_s"], 4), **meta)
        try:
            self.writer.close()
        except Exception as e:  # a failed frame write, reported in the summary
            if self.error is None:
                self.error = e
            s = self.summary()
        log.info("recording %s: %d frames, %d IMU samples, %.1f s", self.path, s["n_frames"], s["n_imu"], s["duration_s"])
        return s


# --------------------------------------------------------------------------- results
@dataclass
class ViewResult:
    """A display-oriented processing result (preview, idle views). Images are at the processed scale."""

    left: np.ndarray
    right: np.ndarray | None = None
    rectified: bool = False
    depth: np.ndarray | None = None
    lines: Any = None  # ohs.vision.laser.LaserLines in ``left`` pixel coordinates
    info: dict = field(default_factory=dict)


@dataclass
class Processed:
    """Latest output of the processing thread."""

    frame: StereoFrame
    value: Any  # ViewResult, the pipeline's FrameResult, or a capture-specific result
    active: bool  # True: processor.process(); False: processor.idle()
    proc_ms: float
    seq: int


@dataclass
class SessionResult:
    recording: Path | None = None
    n_frames: int = 0
    duration_s: float = 0.0
    interrupted: bool = False
    aborted: bool = False
    error: str | None = None
    reason: str = ""
    stats: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- the engine
class LiveSession:
    """Camera + Pico + recorder + processing threads with a window or a headless status loop.

    Subclasses override the hooks: :meth:`make_processor`, :meth:`processing_active`,
    :meth:`on_start`, :meth:`on_action` (SPACE or trigger), :meth:`on_key`, :meth:`on_quit`,
    :meth:`on_limit`, :meth:`on_end_of_stream`, :meth:`tick`, :meth:`observe`, :meth:`render` and
    :meth:`status_text`. Hooks run on the main thread, except :meth:`observe` (processing thread).
    """

    title = "OpenHandScan"
    record_imu = True  # start the Pico's IMU stream when it has one
    require_calib_size = False  # the camera must deliver calib.stereo.image_size (rectifying sessions)
    status_period = 1.0
    keys_help = "SPACE/trigger: start/stop   q/ESC: quit"

    def __init__(self, cfg: Any, camera: Any, *, pico: Pico | None = None, calib: ScannerCalibration | None = None,
                 gui: bool = True, echo: Echo = print, max_frames: int | None = None,
                 max_seconds: float | None = None, pico_optional: bool = False) -> None:
        self.cfg = cfg
        self.camera = camera
        self.replay = isinstance(camera, ReplayCamera)
        self.pico = None if self.replay else pico
        self.pico_optional = bool(pico_optional)  # continue without the Pico if it cannot be opened
        self.calib = calib
        self.gui = bool(gui)
        self.echo = echo
        self.max_frames = None if max_frames is None else int(max_frames)
        self.max_seconds = None if max_seconds is None else float(max_seconds)
        self.result = SessionResult()
        self.camera_info: dict = {}
        self.pico_hello: dict | None = None
        self.imu_live = False
        self.levels: tuple[float, float, float] = (0.0, 0.0, 0.0)  # intended emitter levels
        self.exposure: float | None = getattr(getattr(cfg, "camera", None), "exposure", None)
        self.swap_lr = False
        # threads and shared state
        self._stop = threading.Event()
        self._done = False
        self._actions: queue.Queue = queue.Queue()
        self._rec_lock = threading.Lock()
        self._rec: Recorder | None = None
        self._continuous = False
        self._n_recorded = 0
        self._rec_t0: float | None = None
        self._proc_slot = LatestSlot()
        self._processor: Any = None
        self._proc_ready = threading.Event()
        self._proc_error: BaseException | None = None
        self._proc_reset = threading.Event()
        self._proc_disabled = False
        self._result: Processed | None = None
        self._proc_seq = 0
        self._imu_lock = threading.Lock()
        self._imu_pending: list[ImuSample] = []
        self._imu_recent: collections.deque = collections.deque(maxlen=4096)
        self.last_imu: ImuSample | None = None
        self._threads: dict[str, threading.Thread] = {}
        self._window: Window | None = None
        self._flash: tuple[str, float] | None = None
        self._replay_imu: dict | None = None
        self._replay_imu_pos = 0
        self._replay_events: list[Event] = []
        self._replay_ev_pos = 0
        self._last_frame_t: float | None = None
        self._last_frame_wall: float | None = None
        self._last_index: int | None = None
        self._size_checked = False
        self._t_start = time.perf_counter()
        # statistics
        self.cam_rate = _Rate()
        self.proc_rate = _Rate()
        self.n_frames_read = 0
        self.n_index_gaps = 0
        self.n_processed = 0
        self.n_proc_errors = 0
        self.n_imu = 0

    # ------------------------------------------------------------------ hooks (main thread)
    def make_processor(self) -> Any:
        """Object with ``start/idle/process/add_imu/reset/close`` (all called in ``ohs-process``), or None."""
        return None

    def processing_active(self) -> bool:
        return True

    def on_start(self) -> None:
        pass

    def on_action(self, source: str) -> None:
        pass

    def on_key(self, key: int) -> bool:
        return False

    def on_quit(self, reason: str) -> None:
        self.finish(f"quit ({reason})")

    def on_limit(self, reason: str) -> None:
        self.finish(reason)

    def on_end_of_stream(self) -> None:
        self.finish("end of the replayed recording")

    def tick(self) -> None:
        pass

    def observe(self, value: Any, active: bool) -> None:
        """Called in the processing thread after each result (keep it cheap)."""

    def render(self, proc: Processed | None) -> np.ndarray | None:
        if proc is None:
            return waiting_image()
        v = proc.value
        img = gray_to_bgr(getattr(v, "left", proc.frame.left))
        draw_hud(img, [(self.title, WHITE)])
        return img

    def status_text(self) -> str:
        return f"camera {self.cam_rate.value(time.perf_counter()):.1f} fps, {self.n_frames_read} frames"

    # ------------------------------------------------------------------ public helpers (main thread)
    @property
    def recording(self) -> bool:
        return self._rec is not None and self._continuous

    @property
    def n_recorded(self) -> int:
        return self._n_recorded

    @property
    def recording_elapsed(self) -> float:
        return 0.0 if self._rec_t0 is None else time.perf_counter() - self._rec_t0

    def flash(self, msg: str, seconds: float = 4.0) -> None:
        """Short message on the HUD (and on the console when headless)."""
        self._flash = (msg, time.perf_counter() + seconds)
        if not self.gui:
            self.echo(msg)
        log.info("%s", msg)

    def flash_text(self) -> str | None:
        f = self._flash
        if f is None or time.perf_counter() > f[1]:
            return None
        return f[0]

    def finish(self, reason: str = "") -> None:
        """End the session: stop any recording (emitters off, recording flushed) and leave the loop."""
        if self._rec is not None:
            self.stop_recording()
        if reason and not self.result.reason:
            self.result.reason = reason
        self._done = True

    def source_name(self) -> str:
        if not self.replay:
            return "camera"
        reader = getattr(self.camera, "reader", None)
        src = getattr(reader, "meta", {}).get("source") if reader is not None else None
        return str(src or "replay")

    def base_meta(self, purpose: str, **extra: Any) -> dict:
        meta: dict[str, Any] = {
            "purpose": purpose,
            "ohs_version": __version__,
            "camera": self.camera_info,
            "pico": self.pico_hello,
            "emitters": list(self.levels),
        }
        if self.replay:
            meta["replay_of"] = str(getattr(self.camera, "path", ""))
        meta.update(extra)
        return meta

    def set_emitters(self, levels: tuple[float, float, float]) -> None:
        self.levels = tuple(float(x) for x in levels)  # type: ignore[assignment]
        if self.pico is None or not self.pico.connected:
            return
        try:
            self.pico.set_emitters(*self.levels)
        except Exception as e:
            self.flash(f"emitters: {e}")
            log.warning("set_emitters failed: %s", e)

    def emitters_off(self) -> None:
        self.levels = (0.0, 0.0, 0.0)
        if self.pico is not None and self.pico.connected:
            try:
                self.pico.all_off()
            except Exception as e:  # the firmware watchdog switches them off 1.5 s later anyway
                log.warning("could not switch the emitters off: %s", e)

    def start_recording(self, path: str | Path, *, meta: dict, calib: ScannerCalibration | None = None,
                        levels: tuple[float, float, float] | None = None, continuous: bool = True) -> Recorder:
        """Open a recording. ``continuous``: every following frame (+ IMU, events) is recorded;
        otherwise only frames passed to :meth:`snapshot`. Emitters go to ``levels`` first."""
        if self._rec is not None:
            raise RuntimeError("already recording")
        if levels is not None:
            self.set_emitters(levels)
            if any(levels) and self.pico is not None:
                time.sleep(0.08)  # let the exposure in flight finish before the first recorded frame
        meta = dict(meta)
        meta["emitters"] = list(self.levels)
        rec = Recorder(path, calib=calib, source=self.source_name(), meta=meta)
        now = time.perf_counter()
        with self._rec_lock:
            self._rec = rec
            self._continuous = bool(continuous)
            self._n_recorded = 0
            history = list(self._imu_recent)
        if continuous and history:
            t_ref = self._last_frame_t if self._last_frame_t is not None else history[-1].t_host
            rec.put_imu([s for s in history if s.t_host >= t_ref - IMU_HISTORY_S])
        self._rec_t0 = now
        self.result.recording = rec.path
        log.info("recording to %s (%s)", rec.path, "continuous" if continuous else "snapshots")
        return rec

    def snapshot(self, frame: StereoFrame) -> int:
        """Record one frame into a snapshot recording (renumbered 0, 1, 2...). Returns its index."""
        rec = self._rec
        if rec is None:
            raise RuntimeError("no snapshot recording is open")
        with self._rec_lock:
            k = self._n_recorded
            self._n_recorded += 1
        rec.put_frame(StereoFrame(k, frame.t_host, frame.left, frame.right))
        return k

    def stop_recording(self, **meta: Any) -> dict | None:
        """Stop recording: emitters off, queue flushed, folder closed. Returns the recorder summary."""
        with self._rec_lock:
            rec, self._rec = self._rec, None
            self._continuous = False
        self.emitters_off()
        if rec is None:
            return None
        s = rec.close(**meta)
        self.result.recording = rec.path
        self.result.n_frames = int(s["n_frames"])
        self.result.duration_s = float(s["duration_s"])
        self.result.stats.update(n_imu=s["n_imu"], n_events=s["n_events"])
        if s["error"]:
            self.result.error = f"recording error: {s['error']}"
        return s

    def change_exposure(self, step: int) -> None:
        backend = str(self.camera_info.get("backend", "")).upper()
        v4l = backend.startswith("V4L")
        cur = self.exposure if self.exposure is not None else (100.0 if v4l else -7.0)
        new = cur * (1.25 ** step) if v4l else cur + step
        new = float(np.clip(new, 1.0, 10000.0)) if v4l else float(np.clip(round(new), -13.0, 0.0))
        try:
            self.camera.set_exposure(new)
        except Exception as e:
            self.flash(f"exposure: {e}")
            return
        self.exposure = new
        desc = f"{new * 0.1:.1f} ms" if v4l else f"1/{2.0 ** -new:.0f} s"
        note = " (ignored in replay)" if self.replay else ""
        self.flash(f"exposure {new:g} = {desc}{note}; keep it: camera.exposure = {new:g} in the config")

    # ------------------------------------------------------------------ run
    def run(self) -> SessionResult:
        """Open the devices, run until done, always clean up. Returns the :class:`SessionResult`."""
        try:
            self._open()
            self.on_start()
            self._loop()
        except KeyboardInterrupt:
            self.result.interrupted = True
            if not self.result.reason:
                self.result.reason = "interrupted (Ctrl-C)"
        finally:
            self._shutdown()
        self.result.stats.update(frames_read=self.n_frames_read, frames_processed=self.n_processed,
                                 frames_skipped_by_processing=self._proc_slot.dropped,
                                 camera_index_gaps=self.n_index_gaps, processing_errors=self.n_proc_errors)
        return self.result

    def _open(self) -> None:
        self.camera.open()
        self.camera_info = dict(getattr(self.camera, "info", {}) or {})
        if self.pico is not None:
            try:
                self.pico_hello = self.pico.open()
            except Exception as e:
                if not self.pico_optional:
                    raise
                self.echo(f"warning: no Pico ({e}); continuing without emitters, IMU and trigger")
                _safe("closing the Pico", self.pico.close)
                self.pico = None
        if self.pico is not None:
            if self.record_imu and str(self.pico_hello.get("imu", "NONE")).upper() != "NONE":
                self.pico.start_imu()
                self.imu_live = True
        self._processor = self.make_processor()
        if self._processor is not None:
            self._start_thread("ohs-process", self._process_loop)
            if not self._proc_ready.wait(timeout=120.0):
                raise RuntimeError("live processing did not start within 120 s")
            if self._proc_error is not None:
                raise RuntimeError(f"live processing could not start: {self._proc_error}") from self._proc_error
        self._start_thread("ohs-capture", self._capture_loop)
        if self.pico is not None:
            self._start_thread("ohs-pico", self._pico_loop)

    def _start_thread(self, name: str, target: Callable[[], None]) -> None:
        th = threading.Thread(target=target, name=name, daemon=True)
        self._threads[name] = th
        th.start()

    def _join(self, name: str, timeout: float) -> None:
        th = self._threads.get(name)
        if th is not None and th is not threading.current_thread():
            th.join(timeout)
            if th.is_alive():
                log.warning("thread %s did not stop within %.0f s", name, timeout)

    def _shutdown(self) -> None:
        with _sigint_ignored():
            self._stop.set()
            _safe("switching the emitters off", self.emitters_off)
            self._join("ohs-capture", 3.0)
            if self._rec is not None:
                if not self.gui:
                    self.echo("finishing the recording...")
                _safe("closing the recording", self.stop_recording)
            self._join("ohs-pico", 2.0)
            self._join("ohs-process", 15.0)
            if self.pico is not None:
                _safe("closing the Pico", self.pico.close)
            _safe("closing the camera", self.camera.close)
            if self._window is not None:
                _safe("closing the window", self._window.close)

    def _post(self, kind: str, msg: str = "") -> None:
        self._actions.put((kind, msg))

    def _loop(self) -> None:
        window = Window(self.title) if self.gui else None
        self._window = window
        self._t_start = time.perf_counter()
        last_status = time.perf_counter()
        last_render = 0.0
        shown = -1
        while not self._done:
            self._drain_actions()
            if self._done:
                break
            self._generic_tick()
            if self._done:
                break
            self.tick()
            if self._done:
                break
            now = time.perf_counter()
            if window is not None:
                proc = self._result
                seq = proc.seq if proc is not None else -1
                img = None
                if seq != shown or now - last_render > 0.1:
                    img = self.render(proc)
                    shown, last_render = seq, now
                key = window.show(img, wait_ms=5)
                if window.closed:
                    self.on_quit("window closed")
                    break
                if key >= 0:
                    self._handle_key(key)
            else:
                if now - last_status >= self.status_period:
                    self.echo(self.status_text())
                    last_status = now
                time.sleep(0.01)

    def _generic_tick(self) -> None:
        if self.max_seconds is not None and self.recording and self.recording_elapsed >= self.max_seconds:
            self.on_limit(f"{self.max_seconds:g} s recorded")

    def _drain_actions(self) -> None:
        while not self._done:
            try:
                kind, msg = self._actions.get_nowait()
            except queue.Empty:
                return
            if kind == "trigger":
                self.on_action("trigger")
            elif kind == "limit":
                self.on_limit(msg)
            elif kind == "eof":
                self.on_end_of_stream()
            elif kind in ("error", "pico-lost"):
                text = msg if kind == "error" else f"Pico disconnected ({msg}); emitters are off"
                self.result.error = text
                self.echo(f"error: {text}")
                self.finish(text)

    def _handle_key(self, key: int) -> None:
        if key == ord(" "):
            self.on_action("space")
        elif key in (ord("q"), ord("Q")):
            self.on_quit("q")
        elif key == 27:
            self.on_quit("esc")
        elif key in (ord("+"), ord("=")):
            self.change_exposure(+1)
        elif key in (ord("-"), ord("_")):
            self.change_exposure(-1)
        else:
            self.on_key(key)

    # ------------------------------------------------------------------ threads
    def _capture_loop(self) -> None:
        cam = self.camera
        while not self._stop.is_set():
            try:
                f = cam.read(timeout=0.5)
            except TimeoutError:
                continue
            except EOFError:
                self._post("eof")
                return
            except Exception as e:  # camera died, unreadable replay frame, ...
                if not self._stop.is_set():
                    self._post("error", f"camera: {e}")
                return
            now = time.perf_counter()
            if not self._size_checked:
                self._size_checked = True
                if self.require_calib_size and self.calib is not None:
                    want = tuple(int(x) for x in self.calib.stereo.image_size)
                    if tuple(f.image_size) != want:
                        self._post("error", f"the camera delivers {f.image_size[0]}x{f.image_size[1]} per camera but the "
                                            f"calibration is for {want[0]}x{want[1]}; set camera.width/height to match or recalibrate")
                        return
            self.cam_rate.tick(now)
            if self._last_index is not None and f.index > self._last_index + 1:
                self.n_index_gaps += f.index - self._last_index - 1
            self._last_index = f.index
            if self.swap_lr:
                f = StereoFrame(f.index, f.t_host, f.right, f.left)
            self._last_frame_t, self._last_frame_wall = f.t_host, now
            if self.replay:
                self._feed_replay(f.t_host + REPLAY_IMU_LOOKAHEAD_S)
            with self._rec_lock:
                rec = self._rec if self._continuous else None
                if rec is not None and (self.max_frames is None or self._n_recorded < self.max_frames):
                    rec.put_frame(f)
                    self._n_recorded += 1
                    if self.max_frames is not None and self._n_recorded >= self.max_frames:
                        self._post("limit", f"{self.max_frames} frames recorded")
            if self._processor is not None:
                self._proc_slot.put(f)
            self.n_frames_read += 1

    def _feed_replay(self, t_until: float) -> None:
        reader = getattr(self.camera, "reader", None)
        if reader is None:
            return
        if self._replay_imu is None:
            self._replay_imu = reader.imu_arrays()
            self._replay_events = sorted(reader.events(), key=lambda e: e.t_host)
        a = self._replay_imu
        t = a["t_host"]
        j = int(np.searchsorted(t, t_until, side="right"))
        i = self._replay_imu_pos
        if j > i:
            self._replay_imu_pos = j
            self._on_imu([ImuSample(float(t[k]), float(a["t_device"][k]), a["gyro"][k].copy(), a["accel"][k].copy())
                          for k in range(i, j)])
        while self._replay_ev_pos < len(self._replay_events) and self._replay_events[self._replay_ev_pos].t_host <= t_until:
            self._on_event(self._replay_events[self._replay_ev_pos], control=False)
            self._replay_ev_pos += 1

    def _pico_loop(self) -> None:
        pico = self.pico
        while not self._stop.is_set():
            try:
                samples = pico.read_imu() if self.imu_live else []
                events = pico.read_events()
            except Exception:
                log.exception("reading the Pico queues failed")
                samples, events = [], []
            if samples:
                self._on_imu(samples)
            for e in events:
                self._on_event(e, control=True)
            if not pico.connected:
                if not self._stop.is_set():
                    self._post("pico-lost", str(pico.error or "disconnected"))
                return
            self._stop.wait(0.005)

    def _on_imu(self, samples: list[ImuSample]) -> None:
        self._imu_recent.extend(samples)
        self.last_imu = samples[-1]
        self.n_imu += len(samples)
        if self._processor is not None:
            with self._imu_lock:
                self._imu_pending.extend(samples)
                if len(self._imu_pending) > 50000:
                    del self._imu_pending[:-50000]
        with self._rec_lock:
            if self._rec is not None and self._continuous:
                self._rec.put_imu(samples)

    def _on_event(self, event: Event, control: bool) -> None:
        with self._rec_lock:
            if self._rec is not None and self._continuous:
                self._rec.put_event(event)
        if control and event.kind == "button" and event.value == "down":
            self._post("trigger")

    def _take_imu(self) -> list[ImuSample]:
        with self._imu_lock:
            out, self._imu_pending = self._imu_pending, []
        return out

    def _trim_imu(self, t_min: float) -> None:
        with self._imu_lock:
            if self._imu_pending and self._imu_pending[0].t_host < t_min:
                self._imu_pending = [s for s in self._imu_pending if s.t_host >= t_min]

    def _process_loop(self) -> None:
        proc = self._processor
        try:
            proc.start()
        except BaseException as e:
            log.exception("live processing could not start")
            self._proc_error = e
            self._proc_ready.set()
            return
        self._proc_ready.set()
        consecutive = 0
        try:
            while not self._stop.is_set():
                f = self._proc_slot.take(timeout=0.1)
                if f is None:
                    continue
                if self._proc_reset.is_set():
                    self._proc_reset.clear()
                    try:
                        proc.reset()
                    except Exception:
                        log.exception("resetting the live processor failed")
                active = bool(self.processing_active()) and not self._proc_disabled
                t0 = time.perf_counter()
                try:
                    if active:
                        imu = self._take_imu()
                        if imu:
                            proc.add_imu(imu)
                        value = proc.process(f)
                    else:
                        self._trim_imu(f.t_host - IMU_HISTORY_S)
                        value = proc.idle(f)
                    consecutive = 0
                except Exception as e:
                    consecutive += 1
                    self.n_proc_errors += 1
                    if consecutive == 1:
                        log.exception("live processing failed on frame %d", f.index)
                    if consecutive >= 25 and not self._proc_disabled:
                        self._proc_disabled = True
                        self.flash(f"live processing disabled after repeated errors ({e}); the recording continues", 30)
                    else:
                        self.flash(f"live processing error: {e}")
                    continue
                t1 = time.perf_counter()
                if active:
                    self.n_processed += 1
                self.proc_rate.tick(t1)
                try:
                    self.observe(value, active)
                except Exception:
                    log.exception("observe() failed")
                self._proc_seq += 1
                self._result = Processed(f, value, active, (t1 - t0) * 1e3, self._proc_seq)
        finally:
            try:
                proc.close()
            except Exception:
                log.exception("closing the live processor failed")


# --------------------------------------------------------------------------- processors
class PreviewProcessor:
    """Rectified (with a calibration) or raw view at ``scale``, optional laser lines and depth.

    ``raw``, ``want_lines`` and ``want_depth`` may be toggled from the main thread. Depth needs
    rectified images. Raw-image laser overlays are for focusing and aligning the lasers only.
    """

    def __init__(self, calib: ScannerCalibration | None, scale: float = 0.5, depth_range: tuple[float, float] = (150.0, 450.0),
                 raw: bool = False, lines: bool = False, depth: bool = False) -> None:
        self.calib = calib
        self.scale = float(scale)
        self.depth_range = (float(depth_range[0]), float(depth_range[1]))
        self.raw = bool(raw) or calib is None
        self.want_lines = bool(lines)
        self.want_depth = bool(depth)
        self._rect = None
        self._depth = None
        self._lp = None

    def start(self) -> None:
        from ohs.vision.laser import LaserParams
        from ohs.vision.rectify import Rectifier

        if self.calib is not None:
            self._rect = Rectifier(self.calib.stereo, self.scale)
        self._lp = LaserParams().scaled(self.scale)

    def idle(self, frame: StereoFrame) -> ViewResult:
        return self.process(frame)

    def process(self, frame: StereoFrame) -> ViewResult:
        from ohs.vision.laser import extract_laser_lines

        rectified = self._rect is not None and not self.raw
        if rectified:
            left, right = self._rect.rectify(frame.left, frame.right)
        else:
            left, right = _scale_image(frame.left, self.scale), _scale_image(frame.right, self.scale)
        lines = extract_laser_lines(left, self._lp) if self.want_lines else None
        depth = None
        if self.want_depth and rectified:
            if self._depth is None:
                from ohs.vision.depth import DepthEstimator, DepthParams

                self._depth = DepthEstimator(self._rect.K, self._rect.baseline,
                                             DepthParams(min_depth_mm=self.depth_range[0], max_depth_mm=self.depth_range[1]))
            depth = self._depth.compute(left, right)
        return ViewResult(left, right, rectified, depth, lines)

    def add_imu(self, samples: list[ImuSample]) -> None:
        pass

    def reset(self) -> None:
        pass

    def close(self) -> None:
        self._depth = None


def _pipeline_module() -> Any:
    """``ohs.app.pipeline``, imported lazily (through ``sys.modules``, so tests can stub it)."""
    import importlib

    return importlib.import_module("ohs.app.pipeline")


class PipelineProcessor:
    """The live ``FramePipeline`` (``ohs.app.pipeline``) at ``cfg.live.scale``.

    Created, used and closed in the processing thread. ``idle`` (before the scan starts) only
    rectifies the left image for the display.
    """

    def __init__(self, calib: ScannerCalibration, cfg: Any, mode: str = "markerless", use_imu: bool = True,
                 use_lasers: bool = True) -> None:
        self.calib = calib
        self.cfg = cfg
        self.mode = mode
        self.use_imu = bool(use_imu)
        self.use_lasers = bool(use_lasers)
        self.pipeline: Any = None
        self._rect = None

    def params(self) -> Any:
        """Live ``PipelineParams``: the pipeline's per-resolution defaults with the configured
        depth range, voxel size (unless "auto") and tag size."""
        import dataclasses

        pl = _pipeline_module()
        from ohs.track.markerless import FusionParams
        from ohs.track.markers import MarkerParams
        from ohs.vision.depth import DepthParams

        live = self.cfg.live
        width = int(round(self.calib.stereo.image_size[0] * float(live.scale)))
        s_eff = pl.effective_scale(width) if hasattr(pl, "effective_scale") else float(live.scale)
        depth = pl.default_depth_params(s_eff) if hasattr(pl, "default_depth_params") else DepthParams()
        depth = dataclasses.replace(depth, min_depth_mm=float(live.depth_min_mm), max_depth_mm=float(live.depth_max_mm))
        return pl.PipelineParams(
            mode=self.mode,
            scale=float(live.scale),
            use_imu=self.use_imu,
            use_lasers=self.use_lasers,
            integrate_depth=True,
            depth=depth,
            fusion=None if live.voxel_mm is None else FusionParams(voxel_mm=float(live.voxel_mm)),
            markers=MarkerParams(tag_mm=float(self.cfg.markers.tag_mm)),
            recon="open3d",  # the live preview: rewind re-fuses keyframes; the confidence TSDF is for the final mesh
        )

    def start(self) -> None:
        from ohs.vision.rectify import Rectifier

        self._rect = Rectifier(self.calib.stereo, float(self.cfg.live.scale))
        self.pipeline = _pipeline_module().FramePipeline(self.calib, self.params())

    def idle(self, frame: StereoFrame) -> ViewResult:
        return ViewResult(self._rect.rectify_left(frame.left), rectified=True)

    def add_imu(self, samples: list[ImuSample]) -> None:
        self.pipeline.add_imu(samples)

    def process(self, frame: StereoFrame) -> Any:
        return self.pipeline.process(frame)

    def reset(self) -> None:
        self.pipeline.reset()

    def close(self) -> None:
        if self.pipeline is not None:
            self.pipeline.close()


def _display_gray(value: Any, frame: StereoFrame, scale: float) -> np.ndarray:
    for name in ("left_r", "left"):
        img = getattr(value, name, None)
        if isinstance(img, np.ndarray) and img.ndim >= 2:
            return img
    return _scale_image(frame.left, scale)


def _laser_count(value: Any) -> int:
    pts = getattr(value, "laser_points", None)
    if not pts:
        return 0
    try:
        return int(sum(len(p) for p in pts.values()))
    except (AttributeError, TypeError):
        return 0


# --------------------------------------------------------------------------- preview
class PreviewSession(LiveSession):
    """``ohs preview``: live raw or rectified view, laser overlay, depth, emitter keys, exposure, swap."""

    title = "OpenHandScan preview"
    keys_help = "1/2/3 lasers+dots  +/- exposure  s swap L/R  l lines  d depth  r raw/rect  g guides  SPACE all  q quit"

    def __init__(self, cfg: Any, camera: Any, *, raw: bool = False, depth: bool = False, lines: bool = True, **kw: Any) -> None:
        super().__init__(cfg, camera, **kw)
        self.start_raw, self.start_depth, self.start_lines = raw, depth, lines
        self.guides = False
        scan = cfg.emitters.levels("scan")
        self.on_levels = tuple(x if x > 0 else 100.0 for x in scan)
        self.on_state = [False, False, False]
        self.proc: PreviewProcessor | None = None

    def make_processor(self) -> PreviewProcessor:
        live = self.cfg.live
        self.proc = PreviewProcessor(self.calib, live.scale, (live.depth_min_mm, live.depth_max_mm),
                                     raw=self.start_raw, lines=self.start_lines, depth=self.start_depth)
        return self.proc

    def on_start(self) -> None:
        mode = "raw" if self.proc.raw else "rectified"
        self.echo(f"Preview ({mode}). Keys: {self.keys_help}")
        if self.calib is None:
            self.echo("No calibration found: showing raw images (depth needs `ohs calibrate stereo`).")

    def _apply_emitters(self) -> None:
        if self.pico is None:
            self.flash("no Pico connected: emitters unavailable")
            return
        self.set_emitters(tuple(lv if on else 0.0 for lv, on in zip(self.on_levels, self.on_state)))
        self.flash(emitter_text(self.levels, True))

    def on_action(self, source: str) -> None:
        self.on_state = [not any(self.on_state)] * 3
        self._apply_emitters()

    def on_key(self, key: int) -> bool:
        p = self.proc
        if key in (ord("1"), ord("2"), ord("3")):
            i = key - ord("1")
            self.on_state[i] = not self.on_state[i]
            self._apply_emitters()
        elif key in (ord("s"), ord("S")):
            self.swap_lr = not self.swap_lr
            self.flash("left/right swapped: if depth now works, set camera.swap_lr = true in the config"
                       if self.swap_lr else "left/right as delivered by the camera")
        elif key in (ord("d"), ord("D")):
            if p.raw:
                self.flash("depth needs rectified images (a stereo calibration; key r)")
            else:
                p.want_depth = not p.want_depth
        elif key in (ord("l"), ord("L")):
            p.want_lines = not p.want_lines
        elif key in (ord("r"), ord("R")):
            if self.calib is None:
                self.flash("no calibration: only raw images are available")
            else:
                p.raw = not p.raw
        elif key in (ord("g"), ord("G")):
            self.guides = not self.guides
        else:
            return False
        return True

    def tick(self) -> None:
        if self.max_frames is not None and self.n_frames_read >= self.max_frames:
            self.finish(f"{self.max_frames} frames shown")

    def render(self, proc: Processed | None) -> np.ndarray | None:
        if proc is None:
            return waiting_image()
        v: ViewResult = proc.value
        left = gray_to_bgr(v.left)
        if v.lines is not None:
            from ohs.vision.laser import draw_overlay

            left = draw_overlay(v.left, v.lines, 1)
        if v.depth is not None:
            right = depth_to_bgr(v.depth, *self.proc.depth_range)
        elif v.right is not None:
            right = gray_to_bgr(v.right)
        else:
            right = None
        img = fit_width(side_by_side(left, right) if right is not None else left, 1280)
        if self.guides:  # rectified: matching features share a row; raw or rectified: laser roll alignment
            for y in range(20, img.shape[0], 40):
                cv2.line(img, (0, y), (img.shape[1] - 1, y), CYAN, 1)
            for x in range(20, img.shape[1], 40):
                cv2.line(img, (x, 0), (x, img.shape[0] - 1), CYAN, 1)
        now = time.perf_counter()
        n_lines = "" if v.lines is None else f"  lines V{len(v.lines.vertical)} H{len(v.lines.horizontal)}"
        hud = [
            (f"PREVIEW {'rectified' if v.rectified else 'raw'}{'  L/R SWAPPED' if self.swap_lr else ''}", YELLOW if self.swap_lr else WHITE),
            (f"camera {self.cam_rate.value(now):.1f} fps  view {self.proc_rate.value(now):.1f} fps  "
             f"exposure {'camera default' if self.exposure is None else f'{self.exposure:g}'}{n_lines}", WHITE),
            (emitter_text(self.levels, self.pico is not None), WHITE),
        ]
        bottom = [(self.keys_help, GREY)]
        msg = self.flash_text()
        if msg:
            bottom.insert(0, (msg, YELLOW))
        draw_hud(img, hud, bottom)
        return img

    def status_text(self) -> str:
        now = time.perf_counter()
        return (f"preview: camera {self.cam_rate.value(now):.1f} fps, {self.n_frames_read} frames, "
                f"{emitter_text(self.levels, self.pico is not None)}")


# --------------------------------------------------------------------------- record / scan
class RecordSession(LiveSession):
    """``ohs record``: one raw recording; the trigger or SPACE starts it and stops it."""

    title = "OpenHandScan record"
    purpose = "record"

    def __init__(self, cfg: Any, camera: Any, out_dir: str | Path, *, levels: tuple[float, float, float],
                 start_now: bool = False, meta: dict | None = None, **kw: Any) -> None:
        super().__init__(cfg, camera, **kw)
        self.out_dir = Path(out_dir)
        self.rec_levels = tuple(float(x) for x in levels)
        self.start_now = bool(start_now)
        self.extra_meta = dict(meta or {})
        self.state = "armed"  # armed -> recording -> done
        self.keys_help = "SPACE/trigger: start/stop   +/- exposure   q/ESC: stop and quit"

    def make_processor(self) -> Any:
        live = self.cfg.live
        return PreviewProcessor(self.calib, live.scale, (live.depth_min_mm, live.depth_max_mm))

    def processing_active(self) -> bool:
        return False  # display only

    def auto_start(self) -> bool:
        return self.start_now or self.replay or (not self.gui and self.pico is None)

    def on_start(self) -> None:
        if self.auto_start():
            self.begin()
        else:
            how = "the trigger or SPACE" if self.gui else "the trigger"
            self.echo(f"Ready. Press {how} to start recording, again to stop.")

    def recording_meta(self) -> dict:
        return self.base_meta(self.purpose, **self.extra_meta)

    def begin(self) -> None:
        self.start_recording(self.out_dir, meta=self.recording_meta(), calib=self.calib, levels=self.rec_levels)
        self.state = "recording"
        self.echo(f"Recording to {self.out_dir} ...")

    def end(self, reason: str) -> None:
        if self.state == "recording":
            self.stop_recording()
        self.state = "done"
        self.finish(reason)

    def on_action(self, source: str) -> None:
        if self.state == "armed":
            self.begin()
        elif self.state == "recording":
            self.end(f"stopped by {source}")

    def on_limit(self, reason: str) -> None:
        self.end(reason)

    def on_end_of_stream(self) -> None:
        self.end("end of the replayed recording")

    def on_quit(self, reason: str) -> None:
        self.end(f"quit ({reason})")

    def _state_line(self) -> tuple[str, tuple]:
        if self.state == "recording":
            return f"REC {fmt_time(self.recording_elapsed)}  {self.n_recorded} frames", RED
        return "READY: press the trigger or SPACE to start", YELLOW

    def render(self, proc: Processed | None) -> np.ndarray | None:
        if proc is None:
            return waiting_image()
        img = fit_width(gray_to_bgr(_display_gray(proc.value, proc.frame, self.cfg.live.scale)), 960)
        now = time.perf_counter()
        hud = [self._state_line(),
               (f"camera {self.cam_rate.value(now):.1f} fps", WHITE),
               (emitter_text(self.levels, self.pico is not None), WHITE)]
        msg = self.flash_text()
        draw_hud(img, hud, ([(msg, YELLOW)] if msg else []) + [(self.keys_help, GREY)])
        return img

    def status_text(self) -> str:
        now = time.perf_counter()
        state = f"REC {fmt_time(self.recording_elapsed)}, {self.n_recorded} frames" if self.state == "recording" else "ready"
        return f"{self.purpose}: {state}, camera {self.cam_rate.value(now):.1f} fps, {emitter_text(self.levels, self.pico is not None)}"


class ScanSession(RecordSession):
    """``ohs scan``: :class:`RecordSession` plus the live pipeline on the newest frames."""

    title = "OpenHandScan scan"
    purpose = "scan"
    require_calib_size = True

    def __init__(self, cfg: Any, camera: Any, out_dir: str | Path, *, calib: ScannerCalibration, mode: str = "markerless",
                 levels: tuple[float, float, float] | None = None, processor: Any = None, **kw: Any) -> None:
        if levels is None:
            levels = cfg.emitters.scan_levels(mode, live=True)
        super().__init__(cfg, camera, out_dir, levels=levels, calib=calib, **kw)
        self.mode = mode
        self._custom_processor = processor
        self.n_tracked = 0
        self.n_lost = 0
        self.laser_total = 0
        self.last_reason = ""
        self.use_imu = False
        self.use_lasers = bool(calib.lasers)

    def recording_meta(self) -> dict:
        return self.base_meta(self.purpose, mode=self.mode, live_scale=float(self.cfg.live.scale), **self.extra_meta)

    def make_processor(self) -> Any:
        reader = getattr(self.camera, "reader", None) if self.replay else None
        imu_source = self.imu_live or (reader is not None and reader.imu_arrays()["t_host"].size > 0)
        self.use_imu = bool(self.calib.imu is not None and imu_source)
        if self._custom_processor is not None:
            return self._custom_processor
        return PipelineProcessor(self.calib, self.cfg, self.mode, use_imu=self.use_imu, use_lasers=self.use_lasers)

    def processing_active(self) -> bool:
        return self.state == "recording"

    def on_start(self) -> None:
        notes = []
        if self.calib.imu is None:
            notes.append("no IMU calibration: tracking without the gyro prior (run `ohs calibrate imu`)")
        elif not self.use_imu:
            notes.append("no IMU data: tracking without the gyro prior")
        if not self.use_lasers:
            notes.append("no laser planes: no laser points (run `ohs calibrate lasers`)")
        for n in notes:
            self.echo(f"note: {n}")
        super().on_start()

    def begin(self) -> None:
        self._proc_reset.set()
        super().begin()

    def observe(self, value: Any, active: bool) -> None:
        if not active:
            return
        track = getattr(value, "track", None)
        ok = bool(getattr(track, "ok", False))
        if track is not None:
            if ok:
                self.n_tracked += 1
            else:
                self.n_lost += 1
                self.last_reason = str(getattr(track, "info", {}).get("reason", "?"))
        if ok:
            self.laser_total += _laser_count(value)

    def end(self, reason: str) -> None:
        super().end(reason)
        self.result.stats.update(live_tracked=self.n_tracked, live_lost=self.n_lost, live_laser_points=self.laser_total)

    def render(self, proc: Processed | None) -> np.ndarray | None:
        if proc is None:
            return waiting_image()
        v = proc.value
        gray = _display_gray(v, proc.frame, self.cfg.live.scale)
        lines = getattr(v, "lines", None) if proc.active else None
        if lines is not None:
            from ohs.vision.laser import draw_overlay

            img = draw_overlay(gray, lines, 1)
        else:
            img = gray_to_bgr(gray)
        img = fit_width(img, 960)
        depth = getattr(v, "depth", None) if proc.active else None
        if isinstance(depth, np.ndarray) and depth.ndim == 2:
            paste_inset(img, depth_to_bgr(depth, self.cfg.live.depth_min_mm, self.cfg.live.depth_max_mm))
        now = time.perf_counter()
        hud = [self._state_line()]
        track = getattr(v, "track", None) if proc.active else None
        if track is not None:
            if track.ok:
                info = getattr(track, "info", {}) or {}
                tags = info.get("n_tags", info.get("n_markers"))
                hud.append(("TRACKING OK" + (f"  tags {tags}" if tags is not None else ""), GREEN))
            else:
                hud.append((f"TRACKING LOST: {getattr(track, 'info', {}).get('reason', '?')} - move back to scanned areas", RED))
        hud.append((f"{self.mode}  live {self.proc_rate.value(now):.1f} fps  camera {self.cam_rate.value(now):.1f} fps", WHITE))
        if proc.active:
            hud.append((f"laser points {_laser_count(v)}  (total {self.laser_total})", WHITE))
        hud.append((emitter_text(self.levels, self.pico is not None), WHITE))
        msg = self.flash_text()
        draw_hud(img, hud, ([(msg, YELLOW)] if msg else []) + [(self.keys_help, GREY)])
        return img

    def status_text(self) -> str:
        base = super().status_text()
        if self.state != "recording":
            return base
        now = time.perf_counter()
        lost = f", lost {self.n_lost} ({self.last_reason})" if self.n_lost else ""
        return (f"{base}, live {self.proc_rate.value(now):.1f} fps, tracked {self.n_tracked}{lost}, "
                f"laser points {self.laser_total}")


# --------------------------------------------------------------------------- entry points (used by the CLI)
def _devices(cfg: Any, replay: str | Path | None, use_pico: bool) -> tuple[Any, Pico | None]:
    camera = make_camera(cfg, replay)
    pico = make_pico(cfg) if (use_pico and replay is None) else None
    return camera, pico


def run_preview(cfg: Any, *, calib: ScannerCalibration | None = None, replay: str | Path | None = None, use_pico: bool = True,
                gui: bool = True, raw: bool = False, depth: bool = False, max_frames: int | None = None,
                echo: Echo = print) -> SessionResult:
    camera, pico = _devices(cfg, replay, use_pico)
    s = PreviewSession(cfg, camera, pico=pico, calib=calib, gui=gui, echo=echo, raw=raw, depth=depth, max_frames=max_frames,
                       pico_optional=True)
    return s.run()


def run_record(cfg: Any, out_dir: str | Path, *, profile: str = "scan", calib: ScannerCalibration | None = None,
               replay: str | Path | None = None, use_pico: bool = True, gui: bool = True, max_frames: int | None = None,
               max_seconds: float | None = None, start_now: bool = False, echo: Echo = print) -> SessionResult:
    levels = cfg.emitters.levels(profile)
    check_output_dir(out_dir)
    camera, pico = _devices(cfg, replay, use_pico)
    s = RecordSession(cfg, camera, out_dir, levels=levels, start_now=start_now, meta={"profile": profile}, pico=pico,
                      calib=calib, gui=gui, echo=echo, max_frames=max_frames, max_seconds=max_seconds)
    return s.run()


def run_scan(cfg: Any, calib: ScannerCalibration, out_dir: str | Path, *, mode: str = "markerless",
             replay: str | Path | None = None, use_pico: bool = True, gui: bool = True, max_frames: int | None = None,
             max_seconds: float | None = None, start_now: bool = False, calib_source: str | None = None,
             echo: Echo = print) -> SessionResult:
    if mode not in ("markerless", "markers"):
        raise ValueError(f"unknown scan mode {mode!r}")
    check_output_dir(out_dir)
    camera, pico = _devices(cfg, replay, use_pico)
    meta = {"calibration_source": calib_source} if calib_source else {}
    s = ScanSession(cfg, camera, out_dir, calib=calib, mode=mode, start_now=start_now, meta=meta, pico=pico, gui=gui,
                    echo=echo, max_frames=max_frames, max_seconds=max_seconds)
    return s.run()
