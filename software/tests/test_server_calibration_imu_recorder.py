"""The motion sensor calibration step and its recorder: a recording that stopped at its budget (the disk could not
keep up) or lost a frame to a write error is never solved in silence.

The camera path of tests/test_server_calibration.py (a fake camera, the real firmware logic on a fake Pico, a
nominal calibration), a recorder budget of a few frames and a slow or failing disk:

* the recorder stops: the step stops recording at once and tells the user in the recorder's own sentence;
* too short for the motion sensor calibration: Finish & calibrate is refused, saying what happened and what to do;
* long enough (the minimum made small here): it is worked out, and the result says what happened;
* a frame that could not be written: Finish & calibrate is refused (the recording has a gap).
"""
from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

import cv2  # noqa: E402

from ohs.app import live  # noqa: E402
from ohs.server import calibration as C  # noqa: E402
from ohs.server.demo import DemoParams, demo_path, generate_demo  # noqa: E402
from ohs.server.testing import LiveReader, ServerThread, recv_until  # noqa: E402
from test_server_calibration import _app, _get, _ok, _post, _refused, _wait, _wait_job  # noqa: E402

SIZE = (320, 200)
PAIR = 2 * SIZE[0] * SIZE[1]


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("imu_rec_demo")
    generate_demo(demo_path(d), DemoParams(n_frames=12, image_size=(640, 400)))
    return d


@pytest.fixture
def device(tmp_path, demo_dir):
    from test_app_live import FakeCamera, SimPico

    from ohs.sim.nominal import nominal_calibration

    (tmp_path / "device").mkdir()
    nominal_calibration(SIZE).save(tmp_path / "device" / "calibration.json")
    cam = FakeCamera(size=SIZE, fps=30.0)
    sp = SimPico()
    app = _app(tmp_path, demo_dir, camera_factory=lambda _c: cam, pico_factory=lambda _c: sp.pico())
    try:
        with ServerThread(app) as srv, LiveReader(srv.ws_url) as ws:
            _ok(_post(srv.url, "/api/device/connect", {"source": "camera"}))
            yield {"url": srv.url, "ws": ws}
    finally:
        sp.close()


def _slow_disk(monkeypatch, seconds: float) -> None:
    lock = threading.Lock()
    real = cv2.imwrite

    def slow(*a, **k):
        with lock:
            time.sleep(seconds)
            return real(*a, **k)

    monkeypatch.setattr(cv2, "imwrite", slow)


def _record_until_the_recorder_stops(device) -> dict:
    url, ws = device["url"], device["ws"]
    s = _ok(_post(url, "/api/calibration/start", {"kind": "imu"}))
    assert s["state"] == "armed"
    _ok(_post(url, "/api/calibration/imu/start"))
    msgs = recv_until(ws, lambda m, p, raw: m["type"] == "log" and "Recording stopped" in m["message"], timeout=20)
    notice = msgs[-1][0]
    s = _wait(lambda: (x := _get(url, "/api/calibration").json())["state"] == "capturing" and x, 15,
              "the step to stop recording by itself")
    return {"notice": notice, "session": s}


def test_a_recorder_stop_stops_the_step_and_a_short_recording_is_not_solved(device, monkeypatch):
    monkeypatch.setattr(live, "RECORD_MAX_BYTES", 4 * PAIR)
    _slow_disk(monkeypatch, 0.3)
    got = _record_until_the_recorder_stops(device)
    text = got["notice"]["message"]
    assert got["notice"]["level"] == "error"  # shown as a toast
    assert text.startswith("The motion sensor recording stopped: Recording stopped: the disk could not keep up (")
    body = _refused(_post(device["url"], "/api/calibration/solve"), 409, "Recording stopped: the disk could not keep up")
    assert "needs at least 22 s" in body["detail"]
    assert body["hint"] == "Free some disk space or use a faster disk, then record this step again."


def test_a_long_enough_stopped_recording_is_solved_and_says_so(device, monkeypatch):
    monkeypatch.setattr(live, "RECORD_MAX_BYTES", 4 * PAIR)
    monkeypatch.setattr(C, "IMU_MIN_S", 0.0)  # what was recorded counts as long enough
    _slow_disk(monkeypatch, 0.3)
    _record_until_the_recorder_stops(device)
    job = _ok(_post(device["url"], "/api/calibration/solve"))
    _wait_job(device["url"], job["id"])
    res = _get(device["url"], "/api/calibration").json()["result"]
    assert res is not None
    first = res["advice"][0]
    assert first["text"].startswith("Recording stopped: the disk could not keep up (") and first["action"] == "start_over"
    assert "The motion sensor calibration used the " in first["text"]
    assert "free some disk space or use a faster disk" in first["text"]


def test_a_failed_frame_write_is_not_solved(device, monkeypatch):
    real = cv2.imwrite
    calls = {"n": 0}
    lock = threading.Lock()

    def failing_once(*a, **k):
        with lock:
            calls["n"] += 1
            if calls["n"] == 2:
                return False
        return real(*a, **k)

    monkeypatch.setattr(cv2, "imwrite", failing_once)
    monkeypatch.setattr(C, "IMU_MIN_S", 0.5)
    url = device["url"]
    _ok(_post(url, "/api/calibration/start", {"kind": "imu"}))
    _ok(_post(url, "/api/calibration/imu/start"))
    _wait(lambda: _post(url, "/api/calibration/imu/stop").status_code == 200, 20, "the stop", every=0.3)
    body = _refused(_post(url, "/api/calibration/solve"), 409, "could not be written to the disk")
    assert body["hint"] == "Free some disk space or use a faster disk, then record this step again."


def test_a_healthy_recording_is_solved_as_before(device, monkeypatch):
    monkeypatch.setattr(C, "IMU_MIN_S", 0.5)
    url = device["url"]
    _ok(_post(url, "/api/calibration/start", {"kind": "imu"}))
    _ok(_post(url, "/api/calibration/imu/start"))
    _wait(lambda: _post(url, "/api/calibration/imu/stop").status_code == 200, 20, "the stop", every=0.3)
    job = _ok(_post(url, "/api/calibration/solve"))
    _wait_job(url, job["id"])
    res = _get(url, "/api/calibration").json()["result"]
    assert res is not None and not any("disk" in a["text"] for a in res["advice"])
