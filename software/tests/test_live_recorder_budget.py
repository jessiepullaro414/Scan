"""ohs.app.live.Recorder: what the recorder holds is bounded, in bytes and in seconds of frames.

When the disk (or the PNG encoding) cannot keep up, frames wait in the recorder's queue. Past the budget the
recording stops cleanly: the frames already accepted are all written and stay a valid recording, the reason is
in the recording's metadata, its events and the summary's ``error`` (the path a write error takes), and the
camera thread is never blocked. A fake slow disk (``cv2.imwrite`` made slow or held) and made-up frames: nothing
touches the graphics card except the one session test, which runs the real ``RecordSession`` loop on a fake camera."""
from __future__ import annotations

import csv
import threading
import time

import cv2
import numpy as np
import pytest

from ohs.app import live
from ohs.io.recording import RecordingReader
from ohs.types import StereoFrame

IMG = np.full((64, 64), 7, np.uint8)
FRAME_BYTES = 2 * IMG.nbytes  # one stereo pair: 8192 bytes


def _frame(i: int, fps: float = 60.0) -> StereoFrame:
    return StereoFrame(i, 10.0 + i / fps, IMG.copy(), IMG.copy())


@pytest.fixture
def held_disk(monkeypatch):
    """A disk that writes nothing until released (``release.set()``)."""
    release = threading.Event()
    real = cv2.imwrite

    def held(*a, **k):
        release.wait(30)
        return real(*a, **k)

    monkeypatch.setattr(cv2, "imwrite", held)
    yield release
    release.set()


def slow_disk(monkeypatch, seconds: float) -> None:
    """A disk that writes one PNG at a time, ``seconds`` each, whatever the number of PNG workers."""
    lock = threading.Lock()
    real = cv2.imwrite

    def slow(*a, **k):
        with lock:
            time.sleep(seconds)
            return real(*a, **k)

    monkeypatch.setattr(cv2, "imwrite", slow)


def _close_within(rec, seconds: float, **meta) -> dict:
    out: dict = {}
    th = threading.Thread(target=lambda: out.update(rec.close(**meta)), daemon=True)
    th.start()
    th.join(seconds)
    assert not th.is_alive(), f"close() did not return within {seconds} s"
    return out


# ----------------------------------------------------------------------------------- the defect, as a guard
def test_the_default_recorder_is_bounded(tmp_path, held_disk, monkeypatch):
    """With the disk held and frames pushed, today's queue grows past any bound. With a budget of 40 frames' bytes
    (module default, made small for the test) it holds at most that plus one frame."""
    monkeypatch.setattr(live, "RECORD_MAX_BYTES", 40 * FRAME_BYTES, raising=False)
    rec = live.Recorder(tmp_path / "r")
    t = time.perf_counter()
    for i in range(500):
        rec.put_frame(_frame(i))
    put_s = time.perf_counter() - t
    time.sleep(0.2)
    queued = rec._q.qsize()
    held_disk.set()
    _close_within(rec, 30)
    assert queued <= 41, f"{queued} frames ({queued * FRAME_BYTES / 1e6:.1f} MB) were waiting in the recorder's queue"
    assert put_s < 5.0  # the camera thread was never blocked


# ----------------------------------------------------------------------------------- the stop at the budget
def test_a_slow_disk_stops_the_recording_at_the_byte_budget(tmp_path, held_disk):
    rec = live.Recorder(tmp_path / "r", max_bytes=20 * FRAME_BYTES, max_seconds=1e9, meta={"purpose": "test"})
    took = [rec.put_frame(_frame(i)) for i in range(300)]
    assert rec.stopped is not None
    n = took.index(False)
    assert all(took[:n]) and not any(took[n:])  # accepted up to the stop, none after: never a gap, never renumbered
    assert rec.held_bytes <= 20 * FRAME_BYTES + FRAME_BYTES
    assert rec.max_held_bytes <= 20 * FRAME_BYTES + FRAME_BYTES
    held_disk.set()
    s = _close_within(rec, 30)
    text = s["stopped"]
    assert text.startswith("Recording stopped: the disk could not keep up (")
    assert f"The {n} frames recorded up to then are kept." in text
    assert s["error"] == text  # the path a write error takes to the caller
    assert s["n_frames"] == n and s["n_refused"] == 300 - n
    r = RecordingReader(tmp_path / "r")  # the frames before the stop are a recording that opens and reads
    assert len(r) == n and r.indices.tolist() == list(range(n)) and not r.incomplete
    assert np.array_equal(r.frame(n - 1).left, IMG)
    assert r.meta["n_frames"] == n and r.meta["purpose"] == "test"
    st = r.meta["recording_stopped"]
    assert st["reason"] == "disk behind" and st["text"] == text and st["frames_kept"] == n
    assert st["frames_refused"] == 300 - n and st["held_bytes"] > 0 and st["limit_bytes"] == 20 * FRAME_BYTES
    with open(tmp_path / "r" / "events.csv", newline="", encoding="utf-8") as fh:
        ev = [row for row in csv.DictReader(fh) if row["kind"] == "recorder"]
    assert len(ev) == 1 and ev[0]["value"].startswith("stopped:")


def test_a_slow_disk_stops_the_recording_at_the_seconds_budget(tmp_path, held_disk):
    rec = live.Recorder(tmp_path / "r", max_bytes=1 << 40, max_seconds=0.5)
    for i in range(300):
        rec.put_frame(_frame(i, fps=60.0))
    assert rec.stopped is not None and rec.held_seconds <= 0.5 + 1 / 60 + 1e-9
    held_disk.set()
    s = _close_within(rec, 30)
    assert "s of frames were waiting to be written" in s["stopped"]
    assert RecordingReader(tmp_path / "r").meta["recording_stopped"]["limit_seconds"] == 0.5


def test_nothing_more_is_recorded_after_the_stop(tmp_path, held_disk):
    rec = live.Recorder(tmp_path / "r", max_bytes=5 * FRAME_BYTES)
    for i in range(100):
        rec.put_frame(_frame(i))
    rec.put_imu([])  # nothing
    held_disk.set()
    s = _close_within(rec, 30)
    assert s["n_frames"] + s["n_refused"] == 100


def test_close_after_a_stop_does_not_hang_on_a_slow_disk(tmp_path, monkeypatch):
    slow_disk(monkeypatch, 0.01)
    rec = live.Recorder(tmp_path / "r", max_bytes=30 * FRAME_BYTES)
    for i in range(400):
        rec.put_frame(_frame(i))
    assert rec.stopped is not None
    s = _close_within(rec, 20)
    assert len(RecordingReader(tmp_path / "r")) == s["n_frames"] > 0


def test_a_stop_does_not_hide_a_real_write_error(tmp_path, monkeypatch):
    """The first PNG fails (a full disk), then the disk is slow: the summary names both, in that order."""
    real = cv2.imwrite
    lock = threading.Lock()
    calls = {"n": 0}

    def failing_then_slow(*a, **k):
        with lock:
            calls["n"] += 1
            if calls["n"] == 1:
                return False
            time.sleep(0.01)
            return real(*a, **k)

    monkeypatch.setattr(cv2, "imwrite", failing_then_slow)
    rec = live.Recorder(tmp_path / "r", max_bytes=10 * FRAME_BYTES)
    for i in range(200):
        rec.put_frame(_frame(i))
    s = _close_within(rec, 30)
    assert "could not write frame file" in s["error"] and "Recording stopped: the disk could not keep up" in s["error"]
    assert s["error"].index("could not write") < s["error"].index("Recording stopped")
    meta = RecordingReader(tmp_path / "r").meta
    assert meta["incomplete"] is True and meta["n_frames_failed"] == 1 and "recording_stopped" in meta


def test_a_healthy_disk_never_trips_the_default_budget(tmp_path):
    """3000 frames pushed as fast as Python can (far faster than a camera), stamped with the real time."""
    rec = live.Recorder(tmp_path / "r")
    for i in range(3000):
        assert rec.put_frame(StereoFrame(i, time.perf_counter(), IMG.copy(), IMG.copy()))
    s = _close_within(rec, 120)
    assert rec.stopped is None and s["stopped"] is None and s["error"] is None and s["n_frames"] == 3000
    assert "recording_stopped" not in RecordingReader(tmp_path / "r").meta
    assert rec.held_bytes == 0 and rec.max_held_bytes < live.RECORD_MAX_BYTES


def test_the_default_budget():
    assert live.RECORD_MAX_BYTES == 1 << 30 and live.RECORD_MAX_SECONDS == 20.0
    full = 2 * 1280 * 800  # a 1280 x 800 grey stereo pair
    assert live.RECORD_MAX_BYTES // full >= 4 * live.Recorder.BACKLOG_WARN  # far above the warning's backlog


# ----------------------------------------------------------------------------------- the session
class _Camera:
    """Frames at 60 fps, newest-frame semantics, as tests/test_app_live.py's FakeCamera."""

    def __init__(self) -> None:
        self.opened = False
        self._left = np.random.default_rng(0).integers(20, 120, (100, 160), dtype=np.uint8)

    def open(self) -> None:
        self.opened, self._t0, self._i = True, time.perf_counter(), 0

    def read(self, timeout: float = 1.0) -> StereoFrame:
        due = self._t0 + self._i / 60.0
        now = time.perf_counter()
        if due > now:
            time.sleep(due - now)
        i, self._i = self._i, self._i + 1
        return StereoFrame(i, time.perf_counter(), self._left, self._left)

    def set_exposure(self, value: float) -> None:
        pass

    @property
    def info(self) -> dict:
        return {"backend": "FAKE", "width": 320, "height": 100, "fps": 60.0}

    def close(self) -> None:
        self.opened = False


def test_the_session_ends_and_says_why(tmp_path, monkeypatch):
    """``ohs record`` on a disk that writes 20 frames a second while the camera gives 60: the session ends by
    itself with the reason, well before its 8 s limit, and the recording is valid."""
    from ohs.app.config import Config

    slow_disk(monkeypatch, 0.05)
    monkeypatch.setattr(live, "RECORD_MAX_BYTES", 15 * 2 * 160 * 100)
    lines: list[str] = []
    out = tmp_path / "rec"
    s = live.RecordSession(Config(), _Camera(), out, levels=(0.0, 0.0, 0.0), start_now=True, gui=False,
                           echo=lines.append, max_seconds=8.0)
    t = time.perf_counter()
    res = s.run()
    assert time.perf_counter() - t < 7.0
    assert res.error and res.error.startswith("recording error: Recording stopped: the disk could not keep up")
    r = RecordingReader(out)
    assert len(r) == res.n_frames > 0 and r.meta["recording_stopped"]["frames_kept"] == len(r)
    assert any("Recording stopped" in x for x in lines)
