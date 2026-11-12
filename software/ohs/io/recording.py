"""Recording folder format (ARCHITECTURE.md section 6): write live/simulated sessions, read them back.

```
<rec>/meta.json, frames.csv, frames/000000.png (side-by-side gray, raw), imu.csv, events.csv,
      calibration.json (optional), ground_truth/poses.csv + ground_truth/depth/000000.npy (optional)
```
PNG encoding runs on a small thread pool (cv2.imwrite releases the GIL) so recording keeps up
with the camera.

``events.csv`` kinds: ``button`` (``down`` / ``up``), ``cut`` (``"t0,t1"``, a trimmed span) and
``temperature`` (the IMU chip in degC, about once a second, firmware 1.1+). When a recording has
temperatures, :meth:`RecordingWriter.close` also writes their summary to ``meta.json``
(``temperature_c``: ``first``, ``last``, ``min``, ``max``, ``n``).
"""
from __future__ import annotations

import csv
import json
import logging
import math
import os
import queue
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

import cv2
import numpy as np

from ohs.types import Event, ImuSample, ScannerCalibration, StereoFrame

__all__ = ["RecordingWriter", "RecordingReader", "FORMAT_VERSION", "recorded_frame_rate", "effective_frame_step"]

log = logging.getLogger(__name__)
FORMAT_VERSION = 1


def recorded_frame_rate(frame_times: Iterable[float] | np.ndarray) -> float | None:
    """Frames per second of a recording: 1 / the median time between its frames (``RecordingReader.times()``,
    not the metadata). ``None`` for fewer than 2 frames."""
    t = np.asarray(list(frame_times) if not isinstance(frame_times, np.ndarray) else frame_times, dtype=np.float64)
    t = t.reshape(-1)
    t = t[np.isfinite(t)]
    if len(t) < 2:
        return None
    dt = float(np.median(np.diff(np.sort(t))))
    return 1.0 / dt if dt > 0 else None


def effective_frame_step(frame_times: Iterable[float] | np.ndarray, frame_step: int,
                         max_frame_rate: float) -> tuple[int, float | None]:
    """``(smallest gap between processed frames, recorded frame rate)``.

    With ``max_frame_rate > 0`` the gap is ``max(frame_step, round(recorded rate / max_frame_rate))``
    (at least 1, halves round up): every 2nd frame of a 60 fps recording at 30, every frame of a
    30 fps one. With ``max_frame_rate = 0``, or when the rate can not be measured, it is
    ``frame_step``.
    """
    step = max(1, int(frame_step))
    rate = recorded_frame_rate(frame_times)
    if max_frame_rate > 0 and rate is not None:
        step = max(step, int(math.floor(rate / float(max_frame_rate) + 0.5 + 1e-6)))  # 1e-6: a measured 1.5 is 1.4999...
    return step, rate


def _celsius(value: object) -> float | None:
    """A ``temperature`` event's value in degC, or None if it is not a finite number."""
    try:
        c = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return c if math.isfinite(c) else None


def _complete_lines(path: Path) -> list[str]:
    """The lines of a CSV file without a last line that has no line end: every writer ends its
    rows, so such a line was cut by a crash mid-scan."""
    with open(path, newline="", encoding="utf-8") as fh:
        lines = fh.readlines()
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines.pop()
    return lines


def _imwrite(path: str, img: np.ndarray, params: list[int]) -> None:
    """``cv2.imwrite`` that raises when it fails (it returns False, for example on a full disk)."""
    if not cv2.imwrite(path, img, params):
        raise OSError(f"could not write frame file {path}")


class RecordingWriter:
    """Write a recording folder. Use as a context manager or call :meth:`close`."""

    def __init__(
        self,
        path: str | Path,
        overwrite: bool = False,
        png_compression: int = 1,
        workers: int | None = None,
        source: str = "camera",
        **meta: object,
    ) -> None:
        self.path = Path(path)
        if self.path.exists() and any(self.path.iterdir()) and not overwrite:
            raise FileExistsError(f"recording folder {self.path} is not empty")
        (self.path / "frames").mkdir(parents=True, exist_ok=True)
        self._png = [cv2.IMWRITE_PNG_COMPRESSION, int(png_compression)]
        workers = max(1, min(8, os.cpu_count() or 4) if workers is None else int(workers))
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rec-png")
        self._max_in_flight = 2 * workers  # write_frame waits beyond this many unwritten frames
        self._pending: deque[tuple[Future, list]] = deque()  # PNG writes in frame order, with their frames.csv row
        self._t_flush = time.monotonic()
        self.error: BaseException | None = None  # the first failed frame write
        self._lock = threading.Lock()
        self._frames = open(self.path / "frames.csv", "w", newline="", encoding="utf-8")
        self._imu = open(self.path / "imu.csv", "w", newline="", encoding="utf-8")
        self._events = open(self.path / "events.csv", "w", newline="", encoding="utf-8")
        self._fw, self._iw, self._ew = csv.writer(self._frames), csv.writer(self._imu), csv.writer(self._events)
        self._fw.writerow(["index", "t_host", "file"])
        self._iw.writerow(["t_host", "t_device", "gx", "gy", "gz", "ax", "ay", "az"])
        self._ew.writerow(["t_host", "kind", "value"])
        self._gt_poses = None
        self.meta: dict = {
            "format_version": FORMAT_VERSION,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": source,
            "n_frames": 0,
            **meta,
        }
        self._n = 0
        self._temps: list[float] = []  # temperature events (degC), for the meta.json summary
        self._closed = False
        self._write_meta()

    # ------------------------------------------------------------------ writing
    @property
    def backlog(self) -> int:
        """Frames handed to :meth:`write_frame` whose rows are not written yet (PNG queued or being written)."""
        return len(self._pending)

    def write_frame(self, frame: StereoFrame) -> None:
        """Queue one frame's PNG. Waits for the oldest PNG write while twice the workers' frames are
        unwritten, so slow encoding can not pile up frames in memory here."""
        name = f"{frame.index:06d}.png"
        while True:
            with self._lock:
                self._write_done_rows()
                if len(self._pending) < self._max_in_flight:
                    break
                oldest = self._pending[0][0]
            futures_wait([oldest])
        sbs = frame.to_sbs()
        if "image_size" not in self.meta:
            self.meta["image_size"] = list(frame.image_size)
        fut = self._pool.submit(_imwrite, str(self.path / "frames" / name), sbs, self._png)
        with self._lock:
            self._pending.append((fut, [frame.index, f"{frame.t_host:.6f}", name]))
            self._n += 1
            self._write_done_rows()
            self._flush_now_and_then()

    def _write_done_rows(self) -> None:
        """Write the ``frames.csv`` rows of the PNG writes that have finished, in frame order (so a
        row never points at a PNG that is not yet complete). Hold ``self._lock``."""
        while self._pending and self._pending[0][0].done():
            fut, row = self._pending.popleft()
            if self.error is None and fut.exception() is not None:
                self.error = fut.exception()
            self._fw.writerow(row)

    def _flush_now_and_then(self) -> None:
        """Flush the CSV files about once a second (no fsync), so a crash loses little. Hold ``self._lock``."""
        if time.monotonic() - self._t_flush >= 1.0:
            self._flush()

    def _flush(self) -> None:
        self._write_done_rows()
        for fh in (self._frames, self._imu, self._events):
            fh.flush()
        self._t_flush = time.monotonic()

    def flush(self) -> None:
        """Write the rows of the finished frames and flush the CSV files (no fsync)."""
        with self._lock:
            self._flush()

    def write_imu(self, samples: Iterable[ImuSample]) -> None:
        with self._lock:
            for s in samples:
                g, a = np.asarray(s.gyro).reshape(3), np.asarray(s.accel).reshape(3)
                self._iw.writerow(
                    [f"{s.t_host:.6f}", f"{s.t_device:.6f}", *(f"{x:.6f}" for x in g), *(f"{x:.5f}" for x in a)]
                )
            self._flush_now_and_then()

    def write_event(self, event: Event) -> None:
        with self._lock:
            self._ew.writerow([f"{event.t_host:.6f}", event.kind, event.value])
            if event.kind == "temperature":
                c = _celsius(event.value)
                if c is not None:
                    self._temps.append(c)
            self._flush_now_and_then()

    def write_calibration(self, calib: ScannerCalibration) -> None:
        calib.save(self.path / "calibration.json")

    def write_ground_truth_pose(self, index: int, t_host: float, T_wc: np.ndarray) -> None:
        with self._lock:
            if self._gt_poses is None:
                (self.path / "ground_truth").mkdir(exist_ok=True)
                self._gt_file = open(self.path / "ground_truth" / "poses.csv", "w", newline="", encoding="utf-8")
                self._gt_poses = csv.writer(self._gt_file)
                self._gt_poses.writerow(["index", "t_host"] + [f"T{r}{c}" for r in range(4) for c in range(4)])
            self._gt_poses.writerow([index, f"{t_host:.6f}"] + [f"{x:.9g}" for x in np.asarray(T_wc).reshape(16)])

    def write_ground_truth_depth(self, index: int, depth_mm: np.ndarray) -> None:
        d = self.path / "ground_truth" / "depth"
        d.mkdir(parents=True, exist_ok=True)
        np.save(d / f"{index:06d}.npy", np.asarray(depth_mm, dtype=np.float32))

    def update_meta(self, **kw: object) -> None:
        self.meta.update(kw)

    # ------------------------------------------------------------------ closing
    def _write_meta(self) -> None:
        (self.path / "meta.json").write_text(json.dumps(self.meta, indent=2, default=str), encoding="utf-8")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pool.shutdown(wait=True)
        with self._lock:
            self._write_done_rows()  # all are done now
        if self.error is not None:
            log.error("frame write failed: %s", self.error)
        for fh in (self._frames, self._imu, self._events):
            fh.close()
        if self._gt_poses is not None:
            self._gt_file.close()
        self.meta["n_frames"] = self._n
        if self._temps:
            t = self._temps
            self.meta["temperature_c"] = {"first": round(t[0], 2), "last": round(t[-1], 2), "min": round(min(t), 2),
                                          "max": round(max(t), 2), "n": len(t)}
        self._write_meta()
        if self.error is not None:  # raised after the folder is closed, so it stays readable
            raise self.error

    def __enter__(self) -> "RecordingWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class RecordingReader:
    """Read a recording folder written by :class:`RecordingWriter` (or the simulator)."""

    def __init__(self, path: str | Path, swap_lr: bool = False) -> None:
        self.path = Path(path)
        if not (self.path / "frames.csv").exists():
            raise FileNotFoundError(f"{self.path} is not a recording (frames.csv missing)")
        meta_file = self.path / "meta.json"
        self.meta: dict = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
        self.swap_lr = swap_lr
        rows = list(csv.DictReader(_complete_lines(self.path / "frames.csv")))
        self._index = np.array([int(r["index"]) for r in rows], dtype=np.int64)
        self._times = np.array([float(r["t_host"]) for r in rows], dtype=np.float64)
        self._files = [r["file"] for r in rows]
        self._imu_cache: dict[str, np.ndarray] | None = None

    def __len__(self) -> int:
        return len(self._files)

    @property
    def indices(self) -> np.ndarray:
        return self._index.copy()

    def times(self) -> np.ndarray:
        """Host timestamps of all frames (s)."""
        return self._times.copy()

    def frame(self, i: int) -> StereoFrame:
        """Frame at position ``i`` (0..len-1) in recording order."""
        img = cv2.imread(str(self.path / "frames" / self._files[i]), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise IOError(f"could not read frame file {self._files[i]}")
        return StereoFrame.from_sbs(int(self._index[i]), float(self._times[i]), img, swap_lr=self.swap_lr)

    def iter_frames(self, start: int = 0, stop: int | None = None, step: int = 1) -> Iterator[StereoFrame]:
        for i in range(start, len(self) if stop is None else min(stop, len(self)), step):
            yield self.frame(i)

    def __iter__(self) -> Iterator[StereoFrame]:
        return self.iter_frames()

    def iter_ahead(self, positions: Iterable[int], ahead: int = 2) -> Iterator[StereoFrame]:
        """The frames at ``positions``, in that order, read up to ``ahead`` frames before they are
        asked for.

        A thread reads and decodes the files (``cv2.imread`` releases Python's lock) while the
        caller works on the frame before, so the caller does not wait for the disk. At most
        ``ahead + 2`` frames are in memory at a time. An error while reading is raised here, at
        the frame it belongs to, after the frames before it. Close the iterator (or use it up)
        to end the thread; ``ahead <= 0`` reads in the caller's thread, like :meth:`frame`.
        """
        positions = [int(i) for i in positions]
        if ahead <= 0 or len(positions) < 2:
            for i in positions:
                yield self.frame(i)
            return
        box: queue.Queue = queue.Queue(maxsize=int(ahead))
        stop = threading.Event()

        def read() -> None:
            for i in positions:
                try:
                    item: tuple = (self.frame(i), None)
                except Exception as e:  # noqa: BLE001  (handed to the caller, raised there)
                    item = (None, e)
                while not stop.is_set():
                    try:
                        box.put(item, timeout=0.05)
                        break
                    except queue.Full:
                        pass
                if stop.is_set() or item[1] is not None:
                    return

        thread = threading.Thread(target=read, name="ohs-frame-reader", daemon=True)
        thread.start()
        try:
            for _ in positions:
                frame, error = box.get()
                if error is not None:
                    raise error
                yield frame
        finally:
            stop.set()
            thread.join()

    # ------------------------------------------------------------------ imu / events
    def imu_arrays(self) -> dict[str, np.ndarray]:
        """``{"t_host": (N,), "t_device": (N,), "gyro": (N,3), "accel": (N,3)}`` (empty arrays if none)."""
        if self._imu_cache is None:
            f = self.path / "imu.csv"
            data = np.zeros((0, 8))
            if f.exists() and f.stat().st_size > 0:
                rows = [list(map(float, r)) for r in csv.reader(_complete_lines(f)[1:]) if r]
                if rows:
                    data = np.asarray(rows, dtype=np.float64)
            self._imu_cache = {
                "t_host": data[:, 0].copy(),
                "t_device": data[:, 1].copy(),
                "gyro": data[:, 2:5].copy(),
                "accel": data[:, 5:8].copy(),
            }
        return {k: v.copy() for k, v in self._imu_cache.items()}

    def imu_samples(self, t0: float = -np.inf, t1: float = np.inf) -> list[ImuSample]:
        """IMU samples with ``t0 <= t_host <= t1``."""
        a = self.imu_arrays()
        m = (a["t_host"] >= t0) & (a["t_host"] <= t1)
        return [
            ImuSample(float(t), float(td), g, ac)
            for t, td, g, ac in zip(a["t_host"][m], a["t_device"][m], a["gyro"][m], a["accel"][m])
        ]

    def events(self) -> list[Event]:
        f = self.path / "events.csv"
        if not f.exists():
            return []
        return [Event(float(r["t_host"]), r["kind"], r["value"]) for r in csv.DictReader(_complete_lines(f))]

    def temperature(self) -> dict[str, np.ndarray]:
        """The IMU chip's temperature: ``{"t_host": (N,), "celsius": (N,)}`` from the
        ``temperature`` events, in time order (empty arrays when the recording has none)."""
        rows = sorted((e.t_host, c) for e in self.events() if e.kind == "temperature"
                      for c in [_celsius(e.value)] if c is not None)
        a = np.asarray(rows, dtype=np.float64).reshape(-1, 2)
        return {"t_host": a[:, 0].copy(), "celsius": a[:, 1].copy()}

    # ------------------------------------------------------------------ extras
    def calibration(self) -> ScannerCalibration | None:
        f = self.path / "calibration.json"
        return ScannerCalibration.load(f) if f.exists() else None

    def ground_truth_poses(self) -> dict[int, np.ndarray] | None:
        f = self.path / "ground_truth" / "poses.csv"
        if not f.exists():
            return None
        out: dict[int, np.ndarray] = {}
        with open(f, newline="", encoding="utf-8") as fh:
            rd = csv.reader(fh)
            next(rd, None)
            for r in rd:
                if r:
                    out[int(r[0])] = np.asarray(list(map(float, r[2:18])), dtype=np.float64).reshape(4, 4)
        return out

    def ground_truth_depth(self, index: int) -> np.ndarray | None:
        f = self.path / "ground_truth" / "depth" / f"{index:06d}.npy"
        return np.load(f) if f.exists() else None
