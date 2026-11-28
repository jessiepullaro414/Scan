"""Frame times and indices of the live camera: the camera's own frame clock carried onto
``perf_counter``, and arrival stamping after the call that really waited for the frame.

The clock tests drive ``camera._FrameClock`` with simulated stamps and arrivals (no waiting); the
capture tests run ``StereoCamera`` against fake captures that behave like DirectShow (``grab()``
returns at once, ``retrieve()`` waits) and like Media Foundation (``grab()`` waits and the capture
gives exact stamps).
"""
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from ohs.devices import camera as cam_mod
from ohs.devices.camera import CameraParams, StereoCamera

SOFTWARE = Path(__file__).resolve().parents[1]
ENV = "OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS"
POS = cv2.CAP_PROP_POS_MSEC
W, H = cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT


# --------------------------------------------------------------------------- simulated clocks
def bursty_arrivals(true_times: np.ndarray, period: float, rng, max_gap: float = 3.0) -> np.ndarray:
    """The PC collects frames at irregular moments (0 to ``max_gap`` periods apart), so several
    frames arrive together; each arrives at the first collection after it was taken, plus up to
    0.2 ms of handling. Arrivals are late, never early."""
    span = true_times[-1] - true_times[0] + 10 * period
    polls = true_times[0] + np.cumsum(rng.uniform(0.0, max_gap * period, int(span / period) * 2 + 10))
    ready = true_times + rng.exponential(0.0001, len(true_times))
    return polls[np.searchsorted(polls, ready)]


def run_clock(stamps_ms, arrivals) -> tuple:
    """Push every frame; returns the clock and, for the frames it gave out, (position, index, t)."""
    clock = cam_mod._FrameClock()
    pos, idx, ts = [], [], []
    for i, (s, a) in enumerate(zip(stamps_ms, arrivals)):
        out = clock.push(float(s), float(a))
        if out is not None:
            pos.append(i)
            idx.append(out[0])
            ts.append(out[1])
    return clock, pos, idx, ts


def strictly_increasing(v) -> bool:
    return all(b > a for a, b in zip(v, v[1:]))


def test_exact_stamps_and_bursty_arrivals_give_steady_times():
    rng = np.random.default_rng(1)
    period = 1 / 60
    n = 1200
    stamps = 4.5e8 + np.arange(n) * period * 1e3  # ms, like Media Foundation's sample times
    true = 1000.0 + np.arange(n) * period
    arrivals = bursty_arrivals(true, period, rng)
    assert np.std(np.diff(arrivals)) > 0.005  # the arrivals really jitter by several ms
    clock, pos, idx, ts = run_clock(stamps, arrivals)
    assert clock.mode == "camera" and clock.period == pytest.approx(period, rel=1e-6)
    assert pos[0] < 25 and idx[0] == 0  # gives frames out after a short learning stretch, from index 0
    assert idx == [p - pos[0] for p in pos]  # no frame lost: no gap
    steps = np.diff(ts)
    assert np.max(np.abs(steps - period)) < 1e-4
    assert strictly_increasing(ts)


@pytest.mark.parametrize("ppm", [50.0, -50.0])
def test_clock_rate_difference_is_followed_without_steps(ppm):
    rng = np.random.default_rng(2)
    period = 1 / 60
    n = 60 * 60 * 10  # ten minutes
    k = np.arange(n)
    stamps = 4.5e8 + k * period * 1e3
    true = 2000.0 + k * period * (1 + ppm * 1e-6)  # the camera's frame clock seen on perf_counter
    arrivals = bursty_arrivals(true, period, rng)
    clock, pos, idx, ts = run_clock(stamps, arrivals)
    assert clock.mode == "camera"
    ts, pos = np.asarray(ts), np.asarray(pos)
    steps = np.diff(ts)
    assert np.max(np.abs(steps - period * (1 + ppm * 1e-6))) < 1e-4  # no step over 0.1 ms
    assert np.all(steps > 0)
    edge = true[pos] - true[pos[0]]
    off = ts - ts[0] - edge  # how far t_host wanders from the arrivals' lower edge (up to a constant)
    off_edge = ts - true[pos]
    settled = (true[pos] - true[pos[0]]) > 5.0
    assert np.max(np.abs(off_edge[settled])) < 1e-3, np.max(np.abs(off_edge[settled]))
    assert np.ptp(off[settled]) < 1e-3
    assert abs(clock.rate - ppm * 1e-6) < 20e-6


def test_lost_frames_leave_an_index_gap_and_a_time_step():
    rng = np.random.default_rng(3)
    period = 1 / 60
    frames = np.arange(600)
    frames = np.concatenate([frames[:300], frames[303:]])  # three lost: a stamp step of 4 periods
    stamps = 4.5e8 + frames * period * 1e3
    true = 500.0 + frames * period
    arrivals = bursty_arrivals(true, period, rng)
    clock, pos, idx, ts = run_clock(stamps, arrivals)
    assert clock.mode == "camera"
    gaps = np.diff(idx)
    assert sorted(set(gaps.tolist())) == [1, 4] and int(np.sum(gaps == 4)) == 1
    j = int(np.argmax(gaps))
    assert ts[j + 1] - ts[j] == pytest.approx(4 * period, abs=1e-4)
    assert idx[0] == 0 and strictly_increasing(idx) and strictly_increasing(ts)
    assert idx[-1] - idx[0] == frames[pos[-1]] - frames[pos[0]]


@pytest.mark.parametrize("kind", ["minus_one", "zero", "constant", "irregular", "too_fast", "nan"])
def test_unusable_stamps_fall_back_to_arrival_times(kind):
    rng = np.random.default_rng(4)
    period = 1 / 60
    n = 200
    true = 300.0 + np.arange(n) * period
    arrivals = bursty_arrivals(true, period, rng)
    stamps = {
        "minus_one": np.full(n, -1.0),  # DirectShow
        "zero": np.zeros(n),  # a capture without the property
        "constant": np.full(n, 1234.0),
        "irregular": 1000.0 + np.cumsum(rng.uniform(1.0, 40.0, n)),
        "too_fast": 1000.0 + np.arange(n) * 1.0,  # advances 1 ms a frame: not time in ms
        "nan": np.full(n, np.nan),
    }[kind]
    clock, pos, idx, ts = run_clock(stamps, arrivals)
    assert clock.mode == "arrival"
    assert idx[0] == 0 and strictly_increasing(idx) and strictly_increasing(ts)
    assert np.allclose(np.asarray(ts), arrivals[pos], atol=2e-6)  # stamped on arrival
    assert idx == [p - pos[0] for p in pos]
    if kind in ("minus_one", "zero", "nan"):
        assert pos[0] == 0  # decided at the first frame: no frame held back


def test_stamps_that_break_later_fall_back_without_going_back_in_time():
    rng = np.random.default_rng(5)
    period = 1 / 60
    n = 400
    stamps = 4.5e8 + np.arange(n) * period * 1e3
    stamps[250:] = stamps[250:] - 5000.0  # the stamps jump back (a restarted stream)
    true = 100.0 + np.arange(n) * period
    arrivals = bursty_arrivals(true, period, rng)
    clock, pos, idx, ts = run_clock(stamps, arrivals)
    assert clock.mode == "arrival"
    assert strictly_increasing(idx) and strictly_increasing(ts)
    assert np.allclose(np.asarray(ts)[-50:], arrivals[pos][-50:], atol=2e-6)


def host_clock_frames(seconds: float, rng, fps: float = 60.0):
    """Stamps that are perf_counter times of the frames (ms); arrivals 26 ms or more later, bursty."""
    period = 1 / fps
    true = 5000.0 + np.arange(int(seconds * fps)) * period
    return true * 1e3, bursty_arrivals(true, period, rng) + 0.026, true


def test_host_clock_stamps_are_used_as_they_are():
    stamps, arrivals, _ = host_clock_frames(60.0, np.random.default_rng(6))
    clock, pos, idx, ts = run_clock(stamps, arrivals)
    assert clock.mode == "camera" and clock.source == "host"
    assert np.array_equal(np.asarray(ts), stamps[pos] / 1e3)  # exactly the stamps, from the first frame on
    assert np.max(np.abs(np.diff(ts) - 1 / 60)) < 1e-9
    assert idx[0] == 0 and idx == [p - pos[0] for p in pos]


def test_stamps_on_another_clock_use_the_learned_offset():
    stamps, arrivals, _ = host_clock_frames(30.0, np.random.default_rng(7))
    clock, pos, idx, ts = run_clock(stamps + 1e6, arrivals)  # 1000 s away from perf_counter
    assert clock.mode == "camera" and clock.source == "learned"
    assert np.max(np.abs(np.diff(ts) - 1 / 60)) < 1e-4 and strictly_increasing(ts)


def test_host_clock_stamps_that_start_drifting_switch_to_the_learned_offset_without_a_step():
    rng = np.random.default_rng(8)
    period = 1 / 60
    k = np.arange(int(400 / period))
    stamps = (5000.0 + k * period) * 1e3
    s = k * period
    true = 5000.0 + s + np.where(s > 60.0, (s - 60.0) * 100e-6, 0.0)  # from 60 s on, 100 ppm apart
    arrivals = bursty_arrivals(true, period, rng) + 0.026
    clock, pos, idx, ts = run_clock(stamps, arrivals)
    assert clock.mode == "camera" and clock.source == "learned"
    ts, pos = np.asarray(ts), np.asarray(pos)
    early = s[pos] < 60.0
    assert np.array_equal(ts[early], stamps[pos][early] / 1e3)  # direct times before the drift
    assert np.max(np.abs(np.diff(ts) - np.diff(stamps[pos]) / 1e3)) < 1e-4  # no step over 0.1 ms
    assert strictly_increasing(ts) and strictly_increasing(idx)
    off = (ts - stamps[pos] / 1e3) * 1e3  # ms the times moved off the stamps
    assert off[-1] > 10.0  # and they follow the drift (34 ms by 400 s)


def test_an_arrival_before_its_stamp_switches_to_the_learned_offset_without_a_step():
    stamps, arrivals, _ = host_clock_frames(60.0, np.random.default_rng(9))
    arrivals[1500] = stamps[1500] / 1e3 - 0.005
    arrivals[1501:] = np.maximum(arrivals[1501:], arrivals[1500])
    clock, pos, idx, ts = run_clock(stamps, arrivals)
    assert clock.source == "learned"
    ts, pos = np.asarray(ts), np.asarray(pos)
    before = pos < 1500
    assert np.array_equal(ts[before], stamps[pos][before] / 1e3)
    assert np.max(np.abs(np.diff(ts) - 1 / 60)) < 1e-4 and strictly_increasing(ts)


# --------------------------------------------------------------------------- fake captures
class _Capture:
    """Fake camera running at ``fps``: frame k is taken at ``t0 + k / fps`` (perf_counter)."""

    fps = 100.0
    instances: list = []
    grab_waits = False  # Media Foundation-like: grab() waits; DirectShow-like: retrieve() waits
    stamps = False  # gives CAP_PROP_POS_MSEC sample times
    host_stamps = False  # stamps on the PC's perf_counter clock (else on a clock 1000 s away)
    lose = ()  # camera frame numbers the PC never gets
    dead_apis: set = set()

    def __init__(self, index, api=cv2.CAP_ANY):
        self.index, self.api = index, api
        self.props = {W: 320.0, H: 100.0, cv2.CAP_PROP_FPS: self.fps,
                      cv2.CAP_PROP_FOURCC: float(cv2.VideoWriter_fourcc(*"MJPG"))}
        self.t0 = time.perf_counter() + 0.002
        self.k = -1  # camera frame number of the grabbed frame
        self.delivered: dict[int, tuple[int, float]] = {}  # grab count -> (camera frame, ready time)
        self.n = 0
        self.released = False
        type(self).instances.append(self)

    def isOpened(self):
        return not self.released

    def set(self, prop, value):
        self.props[prop] = float(value)
        return True

    def get(self, prop):
        if prop == POS:
            if not (self.stamps and self.k >= 0):
                return -1.0
            base = self.t0 if self.host_stamps else self.t0 + 1000.0  # seconds
            return (base + self.k / self.fps) * 1000.0
        return self.props.get(prop, 0.0)

    def getBackendName(self):
        return {cv2.CAP_DSHOW: "DSHOW", cv2.CAP_MSMF: "MSMF"}.get(self.api, "ANY")

    def _wait_next(self) -> float:
        if self.api in self.dead_apis:
            time.sleep(0.01)
            return -1.0
        k = self.k + 1
        while k in self.lose:
            k += 1
        ready = self.t0 + k / self.fps
        while time.perf_counter() < ready:
            time.sleep(max(0.0, min(0.002, ready - time.perf_counter())))
        self.k = k
        return time.perf_counter()

    def grab(self):
        if self.grab_waits:
            if self._wait_next() < 0:
                return False
            self.delivered[self.n] = (self.k, time.perf_counter())
        self.n += 1
        return True

    def retrieve(self):
        if not self.grab_waits:
            got = self._wait_next()
            if got < 0:
                return False, None
            self.delivered[self.n - 1] = (self.k, time.perf_counter())
        img = np.zeros((int(self.props[H]), int(self.props[W]), 3), np.uint8)
        img[0, 0] = self.k % 250
        return True, img

    def release(self):
        self.released = True


@pytest.fixture
def capture(monkeypatch):
    class Cap(_Capture):
        instances = []
        dead_apis = set()

    monkeypatch.setattr(cv2, "VideoCapture", Cap)
    return Cap


def _read_for(cam: StereoCamera, seconds: float) -> list:
    frames, end = [], time.perf_counter() + seconds
    while time.perf_counter() < end:
        frames.append(cam.read(timeout=1.0))
    return frames


def test_directshow_like_capture_is_stamped_after_retrieve(capture):
    # grab() returns at once and retrieve() waits for the frame: a stamp taken after grab() is
    # about a frame early (the DirectShow fault). The frame must be stamped after it arrived.
    with StereoCamera(CameraParams(backend="dshow", width=320, height=100, fps=100.0)) as cam:
        frames = _read_for(cam, 0.6)
        info = cam.info
    cap = capture.instances[0]
    assert len(frames) > 20
    for f in frames:
        _, ready = cap.delivered[f.index]
        assert ready <= f.t_host < ready + 0.004, (f.index, f.t_host - ready)
    assert strictly_increasing([f.index for f in frames]) and strictly_increasing([f.t_host for f in frames])
    assert info["frame_times"] == "arrival" and info["frame_period_ms"] == pytest.approx(10.0, rel=0.3)


def test_media_foundation_like_capture_uses_the_camera_clock(capture):
    # stamps on another clock (1000 s away from perf_counter): carried over by the learned offset
    capture.grab_waits, capture.stamps = True, True
    capture.lose = set(range(40, 43)) | {70}  # frames the PC never got
    with StereoCamera(CameraParams(backend="msmf", width=320, height=100, fps=100.0)) as cam:
        frames = _read_for(cam, 1.0)
        info = cam.info
    assert info["frame_times"] == "camera" and info["frame_period_ms"] == pytest.approx(10.0, rel=1e-6)
    assert info["frame_clock"] == "learned"
    ks = [int(f.left[0, 0]) for f in frames]  # camera frame numbers (mod 250)
    idx = [f.index for f in frames]
    assert frames[0].index >= 0 and strictly_increasing(idx) and strictly_increasing([f.t_host for f in frames])
    for a, b, ka, kb in zip(frames, frames[1:], ks, ks[1:]):
        gap = (kb - ka) % 250
        assert b.index - a.index == gap  # the index counts the camera's frames, lost ones too
        assert b.t_host - a.t_host == pytest.approx(gap * 0.010, abs=2e-4)


def test_media_foundation_like_capture_with_host_clock_stamps_uses_them_as_they_are(capture):
    capture.grab_waits, capture.stamps, capture.host_stamps = True, True, True
    capture.lose = {50}
    with StereoCamera(CameraParams(backend="msmf", width=320, height=100, fps=100.0)) as cam:
        frames = _read_for(cam, 0.8)
        info = cam.info
    cap = capture.instances[0]
    assert info["frame_times"] == "camera" and info["frame_clock"] == "host"
    for f in frames:
        k = int(f.left[0, 0])
        assert (f.t_host - cap.t0) * 100.0 == pytest.approx(k + 250 * round(((f.t_host - cap.t0) * 100.0 - k) / 250),
                                                            abs=1e-6)
    steps = np.diff([f.t_host for f in frames]) / np.diff([f.index for f in frames])
    assert np.max(np.abs(steps - 0.010)) < 1e-9 and strictly_increasing([f.index for f in frames])


def test_auto_falls_back_to_directshow_when_media_foundation_gives_no_frame(capture, monkeypatch):
    monkeypatch.setattr(cam_mod.sys, "platform", "win32")
    capture.dead_apis = {cv2.CAP_MSMF}
    with StereoCamera(CameraParams(width=320, height=100, fps=100.0), first_frame_timeout=1.0) as cam:
        assert cam.read().index >= 0
        assert cam.info["backend"] == "DSHOW"
    assert [c.api for c in capture.instances] == [cv2.CAP_MSMF, cv2.CAP_DSHOW]
    assert capture.instances[0].released


def test_two_camera_fallback_still_reads(capture):
    with StereoCamera(CameraParams(backend="dshow", second_index=1, width=320, height=100, fps=100.0)) as cam:
        frames = _read_for(cam, 0.3)
    assert len(capture.instances) == 2 and len(frames) > 5
    assert strictly_increasing([f.t_host for f in frames]) and frames[0].left.shape == (100, 160)


# --------------------------------------------------------------------------- backends and environment
def test_auto_backend_order():
    assert cam_mod._backend_candidates("auto", "win32") == [cv2.CAP_MSMF, cv2.CAP_DSHOW]
    assert cam_mod._backend_candidates("auto", "linux") == [cv2.CAP_V4L2, cv2.CAP_ANY]
    assert cam_mod._backend_candidates("dshow", "win32") == [cv2.CAP_DSHOW]
    assert cam_mod._backend_candidates("msmf", "win32") == [cv2.CAP_MSMF]


def _env_after_import(value: str | None) -> tuple[str, bool]:
    env = {k: v for k, v in os.environ.items() if k != ENV}
    if value is not None:
        env[ENV] = value
    code = ("import os, sys, ohs; print(os.environ.get(%r, '<unset>')); print('cv2' in sys.modules)" % ENV)
    out = subprocess.run([sys.executable, "-c", code], cwd=SOFTWARE, env=env, capture_output=True,
                         text=True, timeout=60, check=True).stdout.split()
    return out[0], out[1] == "True"


def test_hardware_transforms_are_off_by_default_and_a_users_value_is_kept():
    assert _env_after_import(None) == ("0", False)  # set by ``import ohs``, before (and without) cv2
    assert _env_after_import("1") == ("1", False)
    assert os.environ.get(ENV) is not None  # this test process imported ohs before cv2 as well


def test_frame_clock_constants_are_sane():
    assert 0 < cam_mod._FrameClock.SLEW * (1 / 60) < 50e-6  # per-frame correction at 60 fps: tens of us
    assert math.isclose(cam_mod._FrameClock.MAX_STEP_CORRECTION, 50e-6)


def test_wait_judge_picks_the_waiting_call():
    j = cam_mod._WaitJudge()
    t = 0.0
    out = []
    for _i in range(40):  # DirectShow: grab 4 us, retrieve 16 ms
        ta, tg, tr = t, t + 4e-6, t + 0.016
        out.append(j.arrival(ta, tg, tr))
        t += 0.0167
    assert out[-1] == pytest.approx(t - 0.0167 + 0.016) and j.after == "retrieve"
    j = cam_mod._WaitJudge()
    for _i in range(40):  # Media Foundation: grab waits, retrieve converts quickly
        ta, tg, tr = t, t + 0.014, t + 0.0025 + 0.014
        last = j.arrival(ta, tg, tr)
        t += 0.0167
    assert last == pytest.approx(t - 0.0167 + 0.014) and j.after == "grab"
