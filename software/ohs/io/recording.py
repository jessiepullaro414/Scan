"""Recording folder format (ARCHITECTURE.md section 6): write live/simulated sessions, read them back.

```
<rec>/meta.json, frames.csv, frames/000000.png (side-by-side gray, raw), imu.csv, events.csv,
      calibration.json (optional), ground_truth/poses.csv + ground_truth/depth/000000.npy (optional)
```
PNG encoding runs on a small thread pool (cv2.imwrite releases the GIL) so recording keeps up
with the camera.
"""
from __future__ import annotations

import csv
import json
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

import cv2
import numpy as np

from ohs.types import Event, ImuSample, ScannerCalibration, StereoFrame

__all__ = ["RecordingWriter", "RecordingReader", "FORMAT_VERSION"]

log = logging.getLogger(__name__)
FORMAT_VERSION = 1


class RecordingWriter:
    """Write a recording folder. Use as a context manager or call :meth:`close`."""

    def __init__(
        self,
        path: str | Path,
        overwrite: bool = False,
        png_compression: int = 1,
        workers: int = 4,
        source: str = "camera",
        **meta: object,
    ) -> None:
        self.path = Path(path)
        if self.path.exists() and any(self.path.iterdir()) and not overwrite:
            raise FileExistsError(f"recording folder {self.path} is not empty")
        (self.path / "frames").mkdir(parents=True, exist_ok=True)
        self._png = [cv2.IMWRITE_PNG_COMPRESSION, int(png_compression)]
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="rec-png")
        self._pending: list[Future] = []
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
        self._closed = False
        self._write_meta()

    # ------------------------------------------------------------------ writing
    def write_frame(self, frame: StereoFrame) -> None:
        name = f"{frame.index:06d}.png"
        sbs = frame.to_sbs()
        if "image_size" not in self.meta:
            self.meta["image_size"] = list(frame.image_size)
        fut = self._pool.submit(cv2.imwrite, str(self.path / "frames" / name), sbs, self._png)
        with self._lock:
            self._pending.append(fut)
            self._fw.writerow([frame.index, f"{frame.t_host:.6f}", name])
            self._n += 1
            if len(self._pending) > 64:
                self._pending = [f for f in self._pending if not f.done()]

    def write_imu(self, samples: Iterable[ImuSample]) -> None:
        with self._lock:
            for s in samples:
                g, a = np.asarray(s.gyro).reshape(3), np.asarray(s.accel).reshape(3)
                self._iw.writerow(
                    [f"{s.t_host:.6f}", f"{s.t_device:.6f}", *(f"{x:.6f}" for x in g), *(f"{x:.5f}" for x in a)]
                )

    def write_event(self, event: Event) -> None:
        with self._lock:
            self._ew.writerow([f"{event.t_host:.6f}", event.kind, event.value])

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
        for f in self._pending:
            if f.done() and f.exception() is not None:
                log.error("frame write failed: %s", f.exception())
        for fh in (self._frames, self._imu, self._events):
            fh.close()
        if self._gt_poses is not None:
            self._gt_file.close()
        self.meta["n_frames"] = self._n
        self._write_meta()

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
        with open(self.path / "frames.csv", newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
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

    # ------------------------------------------------------------------ imu / events
    def imu_arrays(self) -> dict[str, np.ndarray]:
        """``{"t_host": (N,), "t_device": (N,), "gyro": (N,3), "accel": (N,3)}`` (empty arrays if none)."""
        if self._imu_cache is None:
            f = self.path / "imu.csv"
            data = np.zeros((0, 8))
            if f.exists() and f.stat().st_size > 0:
                with open(f, newline="", encoding="utf-8") as fh:
                    next(fh, None)
                    rows = [list(map(float, r)) for r in csv.reader(fh) if r]
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
        with open(f, newline="", encoding="utf-8") as fh:
            return [Event(float(r["t_host"]), r["kind"], r["value"]) for r in csv.DictReader(fh)]

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
