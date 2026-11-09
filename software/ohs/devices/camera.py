"""Stereo USB camera capture (OpenCV UVC) and recording playback.

:class:`StereoCamera` opens the ELP dual-OV9281 module (one UVC device delivering side-by-side
frames, left | right, both sensors hardware-synchronised). A background thread grabs continuously
and keeps only the newest frame. ``t_host = time.perf_counter()`` is taken right after ``grab()``
returns (before the MJPEG decode), and every grabbed frame gets the next index, so index gaps mean
dropped frames. ``read(timeout)`` returns the newest frame not yet returned (never the same index
twice) or raises ``TimeoutError``. Frames are converted to grayscale ``uint8`` and split with
:meth:`StereoFrame.from_sbs` (``swap_lr`` honoured). With ``second_index`` set, two separate UVC
cameras are opened instead. They are grabbed back to back but are **not** synchronised, and a
warning is logged.

Backends (``CameraParams.backend``): ``"auto"`` means DirectShow then Media Foundation on Windows,
and V4L2 then any on Linux. The pixel format (FOURCC, default MJPG, which the 2560x800 modes need
over USB 2) is set *before* the frame size and fps, because several UVC drivers only accept the
size once the format allows it.

Exposure and gain units are backend-native (``CameraParams.exposure`` / ``set_exposure``):

* **DSHOW**: ``CAP_PROP_EXPOSURE`` is log2 of the exposure time in seconds, in integer steps:
  -5 = 1/32 s (31 ms), -7 = 1/128 s (7.8 ms), -10 = ~1 ms, -13 = ~122 us. Setting it switches the
  camera to manual exposure. ``CAP_PROP_AUTO_EXPOSURE`` 0.75 turns auto back on (OpenCV rounds
  the value, and 1 means auto).
* **V4L2**: ``CAP_PROP_AUTO_EXPOSURE`` 0.25 = manual and 0.75 = auto (legacy normalised values of
  the ``exposure_auto`` menu). Some driver/OpenCV combinations want the raw menu values 1 (manual)
  and 3 (aperture priority) instead, so 1 is tried when 0.25 is rejected. ``CAP_PROP_EXPOSURE``
  is ``exposure_time_absolute`` in 100 us units: 50 = 5 ms.
* **MSMF**: best effort. OpenCV maps the property to the same camera control as DirectShow
  (usually log2 seconds), but many drivers ignore it under Media Foundation. Prefer DSHOW when
  exposure matters. Opening MSMF can take several seconds.
* **Gain** (``CAP_PROP_GAIN``) is a raw driver value on every backend.

:class:`ReplayCamera` plays a recording folder (``ohs.io.recording``) through the same
``open``/``read``/``info``/``close``/context-manager interface, so live mode can run without
hardware. See its docstring for timing and end-of-recording behaviour.

Deviations and extensions to ARCHITECTURE.md: ``StereoCamera(params, first_frame_timeout=5.0,
dead_after_s=3.0)`` waits for the first frame in ``open()`` and raises ``TimeoutError`` if none
arrives. If the camera stops delivering for ``dead_after_s``, ``read()`` raises ``RuntimeError``.
``list_cameras(max_index=8, backend="auto")`` has an optional backend. ``ReplayCamera`` takes an
extra ``restamp`` flag and raises ``EOFError`` at the end of a non-looping recording.
"""
from __future__ import annotations

import contextlib
import dataclasses
import logging
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import cv2
import numpy as np

from ohs.io.recording import RecordingReader
from ohs.types import StereoFrame

__all__ = ["CameraParams", "StereoCamera", "ReplayCamera", "list_cameras"]

log = logging.getLogger(__name__)

_BACKENDS = {"dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF, "v4l2": cv2.CAP_V4L2, "any": cv2.CAP_ANY}
_AUTO_EXPOSURE_MANUAL = 0.25
_AUTO_EXPOSURE_MANUAL_V4L2_MENU = 1.0


@dataclass
class CameraParams:
    index: int = 0
    backend: str = "auto"  # "auto" | "dshow" | "msmf" | "v4l2" | "any"
    width: int = 2560  # full side-by-side frame
    height: int = 800
    fps: float = 60.0
    fourcc: str = "MJPG"
    exposure: float | None = None  # backend-native units; None = leave camera default
    gain: float | None = None
    swap_lr: bool = False
    second_index: int | None = None  # fallback: two separate (unsynced) UVC cameras


# --------------------------------------------------------------------------- helpers
def _backend_candidates(name: str, platform: str | None = None) -> list[int]:
    """OpenCV API preferences to try, in order, for a backend name."""
    plat = sys.platform if platform is None else platform
    key = (name or "auto").lower()
    if key == "auto":
        if plat.startswith("win"):
            return [cv2.CAP_DSHOW, cv2.CAP_MSMF]
        if plat.startswith("linux"):
            return [cv2.CAP_V4L2, cv2.CAP_ANY]
        return [cv2.CAP_ANY]
    if key not in _BACKENDS:
        raise ValueError(f"unknown camera backend {name!r} (use auto, dshow, msmf, v4l2 or any)")
    return [_BACKENDS[key]]


def _backend_name(cap: Any, api: int) -> str:
    try:
        return str(cap.getBackendName())
    except cv2.error:
        return cv2.videoio_registry.getBackendName(api)


def _decode_fourcc(value: float) -> str:
    v = int(value)
    if v <= 0:
        return ""
    s = "".join(chr((v >> (8 * i)) & 0xFF) for i in range(4))
    return s if s.isprintable() else f"0x{v:08X}"


def _to_gray(img: np.ndarray) -> np.ndarray:
    """uint8 (H, W) from gray, BGR, BGRA or 16-bit camera output."""
    if img.dtype != np.uint8:
        img = (img >> 8).astype(np.uint8) if img.dtype == np.uint16 else cv2.convertScaleAbs(img)
    if img.ndim == 3:
        channels = img.shape[2]
        if channels == 1:
            return img[:, :, 0]
        if channels == 3:
            return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if channels == 4:
            return cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
        raise ValueError(f"unsupported frame with {channels} channels")
    return img


def _set_exposure(cap: Any, backend: str, value: float) -> bool:
    """Manual exposure in backend-native units (see the module docstring)."""
    if not cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, _AUTO_EXPOSURE_MANUAL) and backend.upper().startswith("V4L"):
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, _AUTO_EXPOSURE_MANUAL_V4L2_MENU)
    ok = bool(cap.set(cv2.CAP_PROP_EXPOSURE, float(value)))
    if not ok:
        log.warning("%s backend rejected exposure %s", backend, value)
    return ok


def _set_gain(cap: Any, backend: str, value: float) -> bool:
    ok = bool(cap.set(cv2.CAP_PROP_GAIN, float(value)))
    if not ok:
        log.warning("%s backend rejected gain %s", backend, value)
    return ok


@contextlib.contextmanager
def _quiet_opencv() -> Iterator[None]:
    """Silence OpenCV's own warnings while probing (process-wide, restored afterwards)."""
    cvlog = getattr(getattr(cv2, "utils", None), "logging", None)
    if cvlog is None:
        yield
        return
    old = cvlog.getLogLevel()
    cvlog.setLogLevel(cvlog.LOG_LEVEL_ERROR)
    try:
        yield
    finally:
        cvlog.setLogLevel(old)


def _v4l2_device_name(index: int) -> str | None:
    f = Path(f"/sys/class/video4linux/video{index}/name")
    try:
        return f.read_text(encoding="utf-8").strip() if f.exists() else None
    except OSError:
        return None


# --------------------------------------------------------------------------- live camera
class StereoCamera:
    """Side-by-side UVC stereo camera with a background grab thread. Use as a context manager."""

    def __init__(self, params: CameraParams | None = None, *, first_frame_timeout: float = 5.0,
                 dead_after_s: float = 3.0) -> None:
        self.params = dataclasses.replace(params) if params is not None else CameraParams()
        self._first_frame_timeout = float(first_frame_timeout)
        self._dead_after_s = float(dead_after_s)
        self._caps: list[Any] = []
        self._api = cv2.CAP_ANY
        self._backend = ""
        self._cap_lock = threading.Lock()  # VideoCapture is not thread-safe: grab vs set/get
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._latest: StereoFrame | None = None
        self._last_read = -1
        self._next_index = 0
        self._n_frames = 0
        self._fps_est = 0.0
        self._error: str | None = None

    # ------------------------------------------------------------------ lifecycle
    def open(self) -> None:
        if self._caps:
            return
        p = self.params
        if p.fourcc and len(p.fourcc) != 4:
            raise ValueError(f"fourcc must have exactly 4 characters, got {p.fourcc!r}")
        two = p.second_index is not None
        width = p.width // 2 if two else p.width
        caps: list[Any] = []
        try:
            cap, api = self._open_first(p.index, width)
            caps.append(cap)
            if two:
                log.warning(
                    "second_index=%d: using two separate UVC cameras; their frames are NOT "
                    "hardware-synchronised, so stereo on a moving scanner will be wrong", p.second_index)
                cap2 = self._open_one(p.second_index, api, width)
                if cap2 is None:
                    raise RuntimeError(f"could not open the second camera {p.second_index}")
                caps.append(cap2)
            self._backend = _backend_name(caps[0], api)
            for c in caps:
                if p.exposure is not None:
                    _set_exposure(c, self._backend, p.exposure)
                if p.gain is not None:
                    _set_gain(c, self._backend, p.gain)
        except BaseException:
            for c in caps:
                c.release()
            raise
        self._caps, self._api = caps, api
        self._latest, self._last_read, self._next_index, self._n_frames = None, -1, 0, 0
        self._fps_est, self._error = 0.0, None
        self._stop.clear()
        self._thread = threading.Thread(target=self._grab_loop, name="camera-grab", daemon=True)
        self._thread.start()
        with self._cond:
            self._cond.wait_for(lambda: self._latest is not None or self._error is not None,
                                timeout=self._first_frame_timeout)
            first, error = self._latest, self._error
        if first is None:
            self.close()
            if error is not None:
                raise RuntimeError(error)
            raise TimeoutError(f"camera {p.index} opened but delivered no frame within {self._first_frame_timeout:.1f} s")
        w, h = first.image_size
        if (2 * w, h) != (p.width, p.height):
            log.warning("camera delivers %dx%d stereo frames, requested %dx%d", 2 * w, h, p.width, p.height)
        log.info("camera open: %s", self.info)

    def _open_first(self, index: int, width: int) -> tuple[Any, int]:
        tried = []
        for api in _backend_candidates(self.params.backend):
            cap = self._open_one(index, api, width)
            if cap is not None:
                return cap, api
            tried.append(cv2.videoio_registry.getBackendName(api))
        raise RuntimeError(f"could not open camera {index} (backends tried: {', '.join(tried)})")

    def _open_one(self, index: int, api: int, width: int) -> Any | None:
        cap = cv2.VideoCapture(index, api)
        if not cap.isOpened():
            cap.release()
            return None
        p = self.params
        if p.fourcc:  # format first: the large MJPG modes are only offered once it is selected
            cap.set(cv2.CAP_PROP_FOURCC, float(cv2.VideoWriter_fourcc(*p.fourcc)))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(width))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(p.height))
        cap.set(cv2.CAP_PROP_FPS, float(p.fps))
        return cap

    def close(self) -> None:
        self._stop.set()
        th = self._thread
        if th is not None:
            th.join(timeout=2.0)
            if th.is_alive():
                log.warning("camera grab thread did not stop")
        self._thread = None
        if self._cap_lock.acquire(timeout=2.0):
            try:
                for c in self._caps:
                    c.release()
            finally:
                self._cap_lock.release()
        elif self._caps:
            log.warning("camera is stuck in grab(); not releasing it")
        self._caps = []

    def __enter__(self) -> "StereoCamera":
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ frames
    def _grab_loop(self) -> None:
        caps = self._caps
        last_ok = time.perf_counter()
        t_prev: float | None = None
        while not self._stop.is_set():
            imgs = []
            try:
                with self._cap_lock:
                    ok = all(c.grab() for c in caps)
                    t = time.perf_counter()  # right after grab() returns
                    if ok:
                        for c in caps:
                            got, img = c.retrieve()
                            if not got or img is None:
                                ok = False
                                break
                            imgs.append(img)
            except cv2.error as e:
                log.debug("grab failed: %s", e)
                ok, t = False, time.perf_counter()
            if not ok:
                if t - last_ok > self._dead_after_s:
                    self._fail(f"camera {self.params.index} delivered no frame for {self._dead_after_s:.1f} s")
                    return
                time.sleep(0.005)
                continue
            last_ok = t
            index = self._next_index
            self._next_index += 1
            try:
                frame = self._make_frame(index, t, imgs)
            except (ValueError, cv2.error) as e:
                log.warning("dropping malformed frame %d: %s", index, e)
                continue
            if t_prev is not None and t > t_prev:
                inst = 1.0 / (t - t_prev)
                self._fps_est = inst if self._fps_est == 0.0 else 0.9 * self._fps_est + 0.1 * inst
            t_prev = t
            with self._cond:
                self._latest = frame
                self._n_frames += 1
                self._cond.notify_all()

    def _make_frame(self, index: int, t: float, imgs: list[np.ndarray]) -> StereoFrame:
        if len(imgs) == 1:
            return StereoFrame.from_sbs(index, t, _to_gray(imgs[0]), swap_lr=self.params.swap_lr)
        left, right = _to_gray(imgs[0]), _to_gray(imgs[1])
        if left.shape != right.shape:
            raise ValueError(f"camera sizes differ: {left.shape} vs {right.shape}")
        if self.params.swap_lr:
            left, right = right, left
        return StereoFrame(index, t, np.ascontiguousarray(left), np.ascontiguousarray(right))

    def _fail(self, msg: str) -> None:
        log.error(msg)
        with self._cond:
            self._error = msg
            self._cond.notify_all()

    def read(self, timeout: float = 1.0) -> StereoFrame:
        """Newest frame not returned before; waits up to ``timeout`` s, else ``TimeoutError``."""
        if not self._caps:
            raise RuntimeError("camera is not open")
        deadline = time.perf_counter() + timeout
        with self._cond:
            while True:
                f = self._latest
                if f is not None and f.index > self._last_read:
                    self._last_read = f.index
                    return f
                if self._error is not None:
                    raise RuntimeError(self._error)
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    raise TimeoutError(f"no new camera frame within {timeout:.3f} s")
                self._cond.wait(remaining)

    # ------------------------------------------------------------------ controls / info
    def set_exposure(self, value: float) -> None:
        """Manual exposure in backend-native units (module docstring). Also used on re-open."""
        self.params.exposure = float(value)
        with self._cap_lock:
            for c in self._caps:
                _set_exposure(c, self._backend, value)

    def set_gain(self, value: float) -> None:
        self.params.gain = float(value)
        with self._cap_lock:
            for c in self._caps:
                _set_gain(c, self._backend, value)

    @property
    def info(self) -> dict:
        """Actual stereo frame width/height (side-by-side), fps, backend and fourcc."""
        p = self.params
        d: dict[str, Any] = {"index": p.index, "second_index": p.second_index, "synced": p.second_index is None,
                             "swap_lr": p.swap_lr, "open": bool(self._caps), "backend": self._backend}
        if not self._caps:
            d.update(width=p.width, height=p.height, fps=p.fps, fourcc=p.fourcc, measured_fps=0.0, frames=0)
            return d
        with self._cap_lock:
            c = self._caps[0]
            w, h = int(c.get(cv2.CAP_PROP_FRAME_WIDTH)), int(c.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps, fourcc = float(c.get(cv2.CAP_PROP_FPS)), _decode_fourcc(c.get(cv2.CAP_PROP_FOURCC))
        if len(self._caps) == 2:
            w *= 2
        f = self._latest
        if f is not None:  # the delivered frames are the ground truth
            w, h = 2 * f.image_size[0], f.image_size[1]
        d.update(width=w, height=h, fps=fps, fourcc=fourcc, measured_fps=round(self._fps_est, 2), frames=self._n_frames)
        return d


def list_cameras(max_index: int = 8, backend: str = "auto") -> list[dict]:
    """Probe indices ``0..max_index-1``: ``[{"index", "backend", "width", "height", "fps", "fourcc"[, "name"]}]``.

    Sizes are the camera's default mode (not necessarily 2560x800). Uses the first backend of
    ``backend`` (DirectShow on Windows for "auto"). ``name`` is only available on Linux.
    """
    api = _backend_candidates(backend)[0]
    found: list[dict] = []
    with _quiet_opencv():
        for i in range(max_index):
            cap = cv2.VideoCapture(i, api)
            try:
                if not cap.isOpened():
                    continue
                entry: dict[str, Any] = {
                    "index": i,
                    "backend": _backend_name(cap, api),
                    "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                    "fps": float(cap.get(cv2.CAP_PROP_FPS)),
                    "fourcc": _decode_fourcc(cap.get(cv2.CAP_PROP_FOURCC)),
                }
                name = _v4l2_device_name(i)
                if name:
                    entry["name"] = name
                found.append(entry)
            finally:
                cap.release()
    log.debug("cameras found: %s", found)
    return found


# --------------------------------------------------------------------------- replay
class ReplayCamera:
    """Plays a recording folder through the :class:`StereoCamera` interface.

    * ``realtime=True``: frames become due at their recorded spacing, starting with the first
      ``read()``. Like a live camera, ``read()`` returns the newest due frame not yet returned
      (a slow consumer skips frames) and raises ``TimeoutError`` if none is due within
      ``timeout``. ``realtime=False`` returns every frame in order, as fast as requested.
    * Frames keep the recording's own ``t_host``, so the recording's IMU samples and events stay
      aligned (``reader`` gives access to them). With ``restamp=True``, ``t_host`` is
      ``perf_counter()`` at read time instead, like a live camera.
    * ``loop=True`` restarts at the end. Pass ``k`` adds ``k * (max_index + 1)`` to the indices and
      ``k * duration`` (duration = last - first + one frame period) to ``t_host``, so both stay
      monotonic. ``frame.index % (max_index + 1)`` recovers the recorded index.
    * Without ``loop``, ``read()`` raises ``EOFError`` once every frame was returned.
    """

    def __init__(self, recording_path: str | Path, realtime: bool = True, loop: bool = False,
                 restamp: bool = False) -> None:
        self.path = Path(recording_path)
        self.realtime = bool(realtime)
        self.loop = bool(loop)
        self.restamp = bool(restamp)
        self.reader: RecordingReader | None = None
        self._lock = threading.Lock()
        self._pos: tuple[int, int] = (0, -1)  # (pass, position) of the last frame returned
        self._t0: float | None = None
        self._n_read = 0

    def open(self) -> None:
        if self.reader is not None:
            return
        reader = RecordingReader(self.path)
        if len(reader) == 0:
            raise ValueError(f"recording {self.path} has no frames")
        self._times = reader.times()
        self._idx = reader.indices
        self._rel = self._times - self._times[0]
        steps = np.diff(self._times)
        med = float(np.median(steps)) if len(steps) else 0.0
        self._period = med if med > 0 else 1.0 / 30.0
        self._span_t = float(self._rel[-1]) + self._period
        self._span_i = int(self._idx.max()) + 1
        self._pos, self._t0, self._n_read = (0, -1), None, 0
        self.reader = reader
        log.info("replaying %s: %d frames at %.1f fps%s", self.path, len(reader), 1.0 / self._period,
                 " (loop)" if self.loop else "")

    def close(self) -> None:
        self.reader = None

    def __enter__(self) -> "ReplayCamera":
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def finished(self) -> bool:
        """True once a non-looping replay has returned its last frame."""
        return self.reader is not None and not self.loop and self._pos[1] >= len(self._rel) - 1

    def _next_after(self, pos: tuple[int, int]) -> tuple[int, int] | None:
        k, i = pos
        if i + 1 < len(self._rel):
            return k, i + 1
        return (k + 1, 0) if self.loop else None

    def _due_at(self, elapsed: float) -> tuple[int, int]:
        if self.loop:
            k = int(elapsed // self._span_t)
            e = elapsed - k * self._span_t
        else:
            k, e = 0, min(elapsed, float(self._rel[-1]))
        return k, max(int(np.searchsorted(self._rel, e, side="right")) - 1, 0)

    def _emit(self, k: int, i: int) -> StereoFrame:
        f = self.reader.frame(i)
        t = time.perf_counter() if self.restamp else float(self._times[i]) + k * self._span_t
        self._pos = (k, i)
        self._n_read += 1
        return StereoFrame(int(self._idx[i]) + k * self._span_i, t, f.left, f.right)

    def read(self, timeout: float = 1.0) -> StereoFrame:
        with self._lock:
            if self.reader is None:
                raise RuntimeError("ReplayCamera is not open")
            if not self.realtime:
                nxt = self._next_after(self._pos)
                if nxt is None:
                    raise EOFError(f"end of recording {self.path}")
                return self._emit(*nxt)
            now = time.perf_counter()
            if self._t0 is None:
                self._t0 = now
            deadline = now + timeout
            while True:
                now = time.perf_counter()
                due = self._due_at(now - self._t0)
                if due > self._pos:
                    return self._emit(*due)
                nxt = self._next_after(self._pos)
                if nxt is None:
                    raise EOFError(f"end of recording {self.path}")
                t_next = self._t0 + nxt[0] * self._span_t + float(self._rel[nxt[1]])
                if t_next > deadline:
                    time.sleep(max(0.0, deadline - now))
                    raise TimeoutError(f"no replay frame due within {timeout:.3f} s")
                time.sleep(max(0.0, t_next - now))

    def set_exposure(self, value: float) -> None:
        log.debug("ReplayCamera ignores set_exposure(%s)", value)

    def set_gain(self, value: float) -> None:
        log.debug("ReplayCamera ignores set_gain(%s)", value)

    @property
    def info(self) -> dict:
        d: dict[str, Any] = {"backend": "replay", "path": str(self.path), "fourcc": "PNG", "synced": True,
                             "realtime": self.realtime, "loop": self.loop, "open": self.reader is not None}
        if self.reader is None:
            return d
        size = self.reader.meta.get("image_size")
        if not size:
            size = list(self.reader.frame(0).image_size)
        d.update(width=2 * int(size[0]), height=int(size[1]), fps=1.0 / self._period, frames=len(self._rel),
                 frames_read=self._n_read, source=self.reader.meta.get("source"))
        return d
