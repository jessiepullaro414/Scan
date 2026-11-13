"""M2 calibration through the real server: the setup wizard's calibration steps on the simulator.

The simulator's live virtual scanner (:class:`ohs.sim.board_camera.SimBoardCamera`) is injected
through ``sim_camera_factory`` with ``realtime=False`` (its virtual clock runs as fast as frames
render, a few times real time; every capture rule works in frame time) and shorter dwells. A tiny
scan demo (640x400) is pre-generated so ``connect sim`` is instant.

* ``test_wizard_end_to_end_on_the_simulator`` (slow, ~80 s): stereo -> solve -> save, lasers, imu,
  check, each result checked against the virtual scanner's true calibration (``sim_truth``; run
  with ``-s`` to see the ``SIM_TRUTH`` line), and the device's real calibration path never touched.
* the device target: a virtual scanner plays the camera; the measured print scale goes into the
  calibration; saving backs up the old file, drops its lasers and IMU and the camera reloads it.
* rules: replay refused, lasers without stereo, scan vs calibration (409 both ways), solve with
  too few views, a failed solve back to capturing (views kept), continue, delete a view (the
  recording follows), manual captures, auto on/off, thumbnails, cancel (views stay on disk).
* WebSocket: ``calib``, ``calib_session``, ``setup`` and the ``calib_left`` / ``calib_right``
  previews arrive.
* camera path: a fake camera with no board in view and the real firmware logic on a fake Pico:
  manual captures are refused with the plain reason, the lasers profile switches the lasers on and
  cancel switches them off.
"""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

from websockets.sync.client import connect as ws_connect  # noqa: E402

from ohs.app.config import Config  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.demo import DemoParams, demo_path, generate_demo  # noqa: E402
from ohs.server.testing import ServerThread, recv_until  # noqa: E402

T = 30  # HTTP timeout
SIM_KW = {"realtime": False, "dwell_s": 1.0, "move_s": 0.4}


def sim_factory(script, cfg):
    """The board tours at 5 fps (each rendered frame covers 0.2 s of the virtual clock), the IMU at 10 fps."""
    from ohs.sim.board_camera import SimBoardCamera

    return SimBoardCamera(script, spec=cfg.board.spec(), paper=cfg.board.paper, fps=10.0 if script == "imu" else 5.0,
                          **SIM_KW)


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("calib_demo")
    generate_demo(demo_path(d), DemoParams(n_frames=12, image_size=(640, 400)))
    return d


def _app(root: Path, demo_dir: Path, **kw):
    cfg = Config()
    cfg.paths.calibration = str(root / "device" / "calibration.json")
    cfg.paths.scans_dir = str(root / "scans")
    cfg.paths.printables_dir = str(root / "printables")
    return create_app(cfg, projects_dir=root / "projects", demo_dir=demo_dir, static_dir=root / "static",
                      data_dir=root / "data", config_path=root / "config.toml", **kw)


@pytest.fixture
def env(tmp_path, demo_dir):
    app = _app(tmp_path, demo_dir, sim_camera_factory=sim_factory)
    with ServerThread(app) as srv:
        yield {"srv": srv, "url": srv.url, "root": tmp_path, "studio": app.state.studio, "demo": demo_dir}


def _get(url, path):
    return requests.get(url + path, timeout=T)


def _post(url, path, body=None):
    return requests.post(url + path, json=body, timeout=120)


def _ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def _refused(r, status: int, needle: str = "") -> dict:
    assert r.status_code == status, r.text
    body = r.json()
    assert body["detail"] and body.get("hint"), body  # every refusal says what to do
    assert needle.lower() in body["detail"].lower(), body
    return body


def _wait(cond, timeout=60.0, what="condition", every=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = cond()
        if v:
            return v
        time.sleep(every)
    raise AssertionError(f"timed out waiting for {what}")


def _wait_job(url, jid, timeout=240.0) -> dict:
    return _wait(lambda: (j := _get(url, f"/api/jobs/{jid}").json())["state"] not in ("queued", "running") and j,
                 timeout, f"job {jid}", every=0.25)


def _views(url, n: int, timeout: float = 90.0, stall_s: float = 15.0) -> dict:
    """The session once it has ``n`` views (or no new view for ``stall_s``, with at least the minimum)."""
    t_change, last = time.monotonic(), -1
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = _get(url, "/api/calibration").json()
        k = len(s["views"])
        if k != last:
            t_change, last = time.monotonic(), k
        if k >= n or (k >= s["min_views"] > 0 and time.monotonic() - t_change > stall_s):
            return s
        time.sleep(0.25)
    raise AssertionError(f"only {last} views after {timeout:.0f} s")


def _solve(url) -> tuple[dict, dict]:
    job = _ok(_post(url, "/api/calibration/solve"))
    assert job["kind"] == "calibrate" and job["state"] in ("queued", "running")
    assert _get(url, "/api/calibration").json()["state"] in ("solving", "result", "capturing")
    j = _wait_job(url, job["id"])
    return j, _get(url, "/api/calibration").json()


def _truth(result: dict, part: str) -> dict:
    vals = result["sim_truth_values"]
    assert result["sim_truth"] and vals and part in vals, result
    return vals[part]


def _connect_sim(url) -> dict:
    dev = _ok(_post(url, "/api/device/connect", {"source": "sim"}))
    assert dev["source"] == "sim" and dev["sim_scene"] == "demo"
    return dev


# --------------------------------------------------------------------------- end to end
@pytest.mark.slow
def test_wizard_end_to_end_on_the_simulator(env):
    url, root = env["url"], env["root"]
    device_calib = root / "device" / "calibration.json"
    sandbox = root / "data" / "sim" / "calibration.json"
    _connect_sim(url)
    setup = _ok(_get(url, "/api/setup"))
    assert setup["target"] == "sim" and setup["first_run"] is True and setup["calibration"]["path"] == str(sandbox)
    assert [s["id"] for s in setup["steps"]] == ["connect", "selftest", "print", "stereo", "lasers", "imu", "check"]
    steps = {s["id"]: s for s in setup["steps"]}
    assert steps["connect"]["state"] == "done" and steps["print"]["state"] == "optional"
    assert steps["stereo"]["state"] == "todo" and setup["print_check"] is None
    timings: dict = {}
    truths: dict = {}

    # ---- stereo
    t0 = time.monotonic()
    s = _ok(_post(url, "/api/calibration/start", {"kind": "stereo"}))
    assert s["state"] == "capturing" and s["kind"] == "stereo" and s["target"] == "sim" and s["auto"] is True
    assert s["target_views"] == [15, 25] and s["min_views"] == 4 and "15–25" in s["guidance"]
    assert _get(url, "/api/device").json()["sim_scene"] == "calib_stereo"
    s = _views(url, 15, stall_s=8.0)
    assert len(s["views"]) >= 10, s["views"]
    v = s["views"][0]
    assert v["auto"] and v["corners_left"] >= 30 and v["corners_right"] >= 30 and v["thumb_url"].endswith("/thumb.jpg")
    rec = Path(s["recording"])
    assert rec.parent == root / "data" / "sim" / "calibration" and rec.name.startswith("stereo-")
    timings["stereo_capture"] = time.monotonic() - t0
    t0 = time.monotonic()
    job, s = _solve(url)
    timings["stereo_solve"] = time.monotonic() - t0
    assert job["state"] == "done", job
    r = s["result"]
    assert s["state"] == "result" and r["kind"] == "stereo" and r["grade"] in ("excellent", "good"), r
    assert r["ok"] and r["can_save"] and r["headline"] and r["verdict"] and r["report_url"] == "/api/calibration/report.json"
    labels = [m["label"] for m in r["metrics"]]
    assert "Reprojection error" in labels and "Row error after rectification" in labels and "Baseline" in labels
    assert {p["index"] for p in r["per_view"]} <= {v["index"] for v in s["views"]} and r["per_view"]
    e = truths["stereo"] = _truth(r, "stereo")
    truths["stereo_views"] = len(s["views"])
    assert abs(e["baseline_err_mm"]) < 0.3 and e["row_err_p95_px"] < 0.3 and abs(e["fx_err_px"]) < 3.0, e
    rep = _ok(_get(url, "/api/calibration/report.json"))
    assert rep["rms"] == pytest.approx(float(r["metrics"][0]["value"].split()[0]), abs=0.006)
    assert (rec / "calib_report_stereo.json").is_file()
    saved = _ok(_post(url, "/api/calibration/save"))
    assert saved["session"]["state"] == "saved" and saved["session"]["saved"]["path"] == str(sandbox)
    assert sandbox.is_file() and not device_calib.exists()
    steps = {x["id"]: x for x in saved["setup"]["steps"]}
    assert steps["stereo"]["state"] == "done" and "views" in steps["stereo"]["summary"]
    assert saved["setup"]["first_run"] is False and saved["setup"]["calibration"]["parts"]["stereo"]["session"]
    assert _get(url, "/api/device").json()["sim_scene"] == "demo"

    # ---- lasers
    t0 = time.monotonic()
    s = _ok(_post(url, "/api/calibration/start", {"kind": "lasers"}))
    assert s["kind"] == "lasers" and "Simulated line lasers on" == s["emitters"]
    s = _views(url, 8, stall_s=8.0)
    assert len(s["views"]) >= 5, s["views"]
    assert all(v["lines"]["vertical"] >= 15 and v["lines"]["horizontal"] >= 15 for v in s["views"])
    assert (Path(s["recording"]) / "calibration.json").is_file()
    timings["lasers_capture"] = time.monotonic() - t0
    t0 = time.monotonic()
    job, s = _solve(url)
    timings["lasers_solve"] = time.monotonic() - t0
    r = s["result"]
    assert job["state"] == "done" and r["grade"] in ("excellent", "good"), r
    e = truths["lasers"] = _truth(r, "lasers")
    truths["lasers_views"] = len(s["views"])
    for name in ("vertical", "horizontal"):
        assert e[name]["angle_deg"] < 0.2 and abs(e[name]["d_err_mm"]) < 0.5, (name, e)
    saved = _ok(_post(url, "/api/calibration/save"))["setup"]
    assert saved["calibration"]["lasers"] == ["horizontal", "vertical"]
    assert {x["id"]: x["state"] for x in saved["steps"]}["lasers"] == "done"

    # ---- imu
    t0 = time.monotonic()
    s = _ok(_post(url, "/api/calibration/start", {"kind": "imu"}))
    assert s["state"] == "armed" and s["views"] == [] and s["target_views"] == [0, 0]
    _refused(_post(url, "/api/calibration/capture"), 409, "records continuously")
    _refused(_post(url, "/api/calibration/solve"), 409, "cannot calibrate now")
    s = _ok(_post(url, "/api/calibration/imu/start"))
    assert s["state"] == "recording" and s["recording"]
    first = _post(url, "/api/calibration/imu/stop")
    _refused(first, 409, "keep rotating")
    _wait(lambda: _post(url, "/api/calibration/imu/stop").status_code == 200, 120, "the IMU minimum", every=0.5)
    s = _get(url, "/api/calibration").json()
    assert s["state"] == "capturing"
    timings["imu_record"] = time.monotonic() - t0
    t0 = time.monotonic()
    job, s = _solve(url)
    timings["imu_solve"] = time.monotonic() - t0
    r = s["result"]
    assert job["state"] == "done" and r["grade"] in ("excellent", "good", "usable"), r
    e = truths["imu"] = _truth(r, "imu")
    truths["imu_grade"] = r["grade"]
    assert e["R_ci_err_deg"] < 0.5 and abs(e["time_offset_err_ms"]) < 3.0 and e["gyro_bias_err_mrad_s"] < 1.5, e
    if not r["ok"]:
        _refused(_post(url, "/api/calibration/save"), 409, "needs force")
    saved = _ok(_post(url, "/api/calibration/save", {"force": not r["ok"]}))["setup"]
    assert saved["calibration"]["imu"] is True

    # ---- check
    t0 = time.monotonic()
    s = _ok(_post(url, "/api/calibration/start", {"kind": "check"}))
    s = _views(url, 4, stall_s=10.0)
    timings["check_capture"] = time.monotonic() - t0
    job, s = _solve(url)
    r = s["result"]
    assert job["state"] == "done" and r["kind"] == "check" and r["ok"] and r["can_save"] is False, r
    _refused(_post(url, "/api/calibration/save"), 409, "not saved")
    setup = _ok(_get(url, "/api/setup"))
    assert setup["last_check"]["ok"] is True
    steps = {x["id"]: x["state"] for x in setup["steps"]}
    assert steps == {"connect": "done", "selftest": "todo", "print": "optional", "stereo": "done", "lasers": "done",
                     "imu": "done", "check": "done"}, steps
    assert _ok(_post(url, "/api/calibration/cancel"))["state"] == "idle"
    assert _get(url, "/api/device").json()["sim_scene"] == "demo"

    # the sandbox holds everything; the device's calibration was never written
    from ohs.types import ScannerCalibration

    final = ScannerCalibration.load(sandbox)
    assert final.lasers and final.imu is not None and (sandbox.parent / "calibration.json.bak").is_file()
    assert not device_calib.exists() and not (root / "scans").exists()
    truths["check"] = r["sim_truth_values"]
    print("SIM_TRUTH " + json.dumps({"truth": truths, "timings_s": {k: round(v, 1) for k, v in timings.items()}},
                                    default=float))


# --------------------------------------------------------------------------- rules
def test_calibration_rules_on_the_simulator(env, monkeypatch):
    url, root = env["url"], env["root"]
    _refused(_post(url, "/api/calibration/start", {"kind": "stereo"}), 409, "no scanner is connected")
    _refused(_post(url, "/api/calibration/start", {"kind": "nope"}), 422)
    assert _ok(_get(url, "/api/calibration"))["state"] == "idle"
    _refused(_post(url, "/api/calibration/capture"), 409, "no calibration is running")
    _refused(_get(url, "/api/calibration/report.json"), 404, "no calibration")
    # a replayed recording is refused
    rec = demo_path(env["demo"])
    _ok(_post(url, "/api/device/connect", {"source": "replay", "recording": str(rec), "loop": True}))
    _refused(_post(url, "/api/calibration/start", {"kind": "stereo"}), 409, "replayed recording")
    _connect_sim(url)
    _refused(_post(url, "/api/calibration/start", {"kind": "lasers"}), 409, "stereo calibration first")
    _refused(_post(url, "/api/calibration/start", {"kind": "imu"}), 409, "stereo calibration first")
    s = _ok(_post(url, "/api/calibration/start", {"kind": "stereo"}))
    _refused(_post(url, "/api/calibration/start", {"kind": "stereo"}), 409, "already running")
    pid = _ok(_post(url, "/api/projects", {"name": "Blocked"}))["id"]
    _refused(_post(url, "/api/scan/start", {"project_id": pid}), 409, "calibration is running")
    _refused(_post(url, "/api/device/connect", {"source": "sim"}), 409, "calibration is running")
    _refused(_post(url, "/api/settings/camera", {"index": 1}), 409, "calibration")
    _refused(_post(url, "/api/setup/selftest"), 409, "calibration")
    _refused(_post(url, "/api/calibration/imu/start"), 409, "only the imu step")
    # manual captures with auto-capture off
    s = _ok(_post(url, "/api/calibration/auto", {"enabled": False}))
    assert s["auto"] is False and s["views"] == []
    _wait(lambda: _get(url, "/api/calibration").json() and _post(url, "/api/calibration/capture").status_code == 200,
          30, "a first manual capture", every=0.3)
    while len(_get(url, "/api/calibration").json()["views"]) < 3:
        r = _post(url, "/api/calibration/capture")
        if r.status_code != 200:
            _refused(r, 409, "captured already")
        time.sleep(0.3)
    s = _get(url, "/api/calibration").json()
    assert all(v["auto"] is False for v in s["views"]) and [v["index"] for v in s["views"]][:3] == [0, 1, 2]
    _refused(_post(url, "/api/calibration/solve"), 409, "at least 4")
    # thumbnails and delete: the recording follows
    folder = Path(s["recording"])
    jpg = _get(url, "/api/calibration/views/1/thumb.jpg")
    assert jpg.status_code == 200 and jpg.headers["content-type"] == "image/jpeg" and jpg.content[:2] == b"\xff\xd8"
    s = _ok(requests.delete(url + "/api/calibration/views/1", timeout=T))
    assert [v["index"] for v in s["views"]] == [0, 2]
    with open(folder / "frames.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [int(r["index"]) for r in rows] == [0, 2] and not (folder / "frames" / "000001.png").exists()
    assert json.loads((folder / "meta.json").read_text(encoding="utf-8"))["n_frames"] == 2
    _refused(_get(url, "/api/calibration/views/1/thumb.jpg"), 404, "no view")
    _refused(requests.delete(url + "/api/calibration/views/99", timeout=T), 404, "no view")
    while len(_get(url, "/api/calibration").json()["views"]) < 4:
        _post(url, "/api/calibration/capture")
        time.sleep(0.3)
    # a failed solve returns to capturing with the views kept and says why
    from ohs.app import capture

    def failing_flow(*a, **kw):
        raise ValueError("only 3 stereo views with >= 8 common corners, need at least 4")

    monkeypatch.setattr(capture, "run_flow", failing_flow)
    n = len(_get(url, "/api/calibration").json()["views"])
    job, s = _solve(url)
    assert job["state"] == "failed" and "at least 4" in job["error"], job
    r = s["result"]
    assert s["state"] == "capturing" and len(s["views"]) == n and r["grade"] == "failed" and r["can_save"] is False
    assert "at least 4" in r["verdict"] and r["advice"] and r["advice"][0]["action"] == "add_views"
    _refused(_post(url, "/api/calibration/save"), 409, "no calibration result")
    s = _ok(_post(url, "/api/calibration/continue"))
    assert s["state"] == "capturing" and s["result"] is None and len(s["views"]) == n
    # cancel: idle, the views stay on disk, the simulator back to its demo
    s = _ok(_post(url, "/api/calibration/cancel"))
    assert s["state"] == "idle" and s["kind"] is None and s["target"] == "sim"
    assert (folder / "frames.csv").is_file() and len(list((folder / "frames").glob("*.png"))) == n
    assert _get(url, "/api/device").json()["sim_scene"] == "demo"
    assert _ok(_post(url, "/api/calibration/cancel"))["state"] == "idle"  # again: nothing to do
    # and a running scan blocks a calibration
    assert _ok(_post(url, "/api/scan/start", {"project_id": pid}))["state"] == "scanning"
    _refused(_post(url, "/api/calibration/start", {"kind": "stereo"}), 409, "scan is running")
    _ok(_post(url, "/api/scan/stop"))
    assert not (root / "device").exists()


def test_websocket_calibration_messages(env):
    url = env["url"]
    _connect_sim(url)
    with ws_connect(env["srv"].ws_url, max_size=None, open_timeout=5) as ws:
        msgs = recv_until(ws, lambda m, p, raw: m.get("type") == "status", timeout=10)
        seeded = {m["type"]: m for m, _, _ in msgs}
        assert seeded["hello"]["version"] == "0.3.0"
        assert seeded["calib_session"]["state"] == "idle" and seeded["setup"]["target"] == "sim"
        ws.send(json.dumps({"type": "subscribe", "preview": True, "mesh": False, "max_preview_hz": 15}))
        _ok(_post(url, "/api/calibration/start", {"kind": "stereo"}))
        seen: dict = {}

        def until(m, p, raw):
            if m["type"] == "preview":
                seen.setdefault(m["camera"], (m, p))
            elif m["type"] == "calib" and m.get("left"):
                seen.setdefault("calib", m)
            elif m["type"] == "calib_session" and m["state"] == "capturing":
                seen.setdefault("calib_session", m)
            elif m["type"] == "device" and m.get("sim_scene") == "calib_stereo":
                seen.setdefault("device", m)
            return all(k in seen for k in ("calib", "calib_session", "calib_left", "calib_right", "device"))

        recv_until(ws, until, timeout=30)
        c = seen["calib"]
        assert c["kind"] == "stereo" and c["state"] == "capturing" and c["image_size"] == [1280, 800]
        assert len(c["left"]["corners"]) % 2 == 0 and c["common"] >= 8 and c["board_found"] is True
        assert c["coverage"]["cols"] == c["coverage"]["rows"] == 8 and len(c["coverage"]["left"]) == 64
        assert set(c["diversity"]) == {"flat", "tilt_left", "tilt_right", "tilt_up", "tilt_down", "near", "mid", "far"}
        assert 0.0 <= c["steady"] <= 1.0 and c["hint"]["text"] and c["target_views"] == [15, 25] and c["imu"] is None
        assert c["lines"] is None and len(c["outline"]) == 8 and c["auto"] is True
        for cam in ("calib_left", "calib_right"):
            h, payload = seen[cam]
            assert h["format"] == "jpeg" and (h["w"], h["h"]) == (640, 400) and bytes(payload[:2]) == b"\xff\xd8"
        ws.send(json.dumps({"type": "subscribe", "preview": False}))
        _ok(_post(url, "/api/calibration/cancel"))
        recv_until(ws, lambda m, p, raw: m["type"] == "calib_session" and m["state"] == "idle", timeout=15)
        t0 = time.monotonic()
        late = recv_until(ws, lambda m, p, raw: time.monotonic() - t0 > 1.0, timeout=5)
        assert not any(m["type"] == "preview" for m, _, _ in late)  # previews follow the subscription


def test_calibration_with_the_camera(tmp_path, demo_dir):
    """No board in view: captures are refused in plain words; the lasers follow the session."""
    from test_app_live import FakeCamera, SimPico

    from ohs.sim.nominal import nominal_calibration

    size = (320, 200)
    (tmp_path / "device").mkdir()
    nominal_calibration(size).save(tmp_path / "device" / "calibration.json")
    cam = FakeCamera(size=size, fps=30.0)
    sp = SimPico()
    app = _app(tmp_path, demo_dir, camera_factory=lambda _c: cam, pico_factory=lambda _c: sp.pico())
    try:
        with ServerThread(app) as srv, ws_connect(srv.ws_url, max_size=None, open_timeout=5) as ws:
            url = srv.url
            dev = _ok(_post(url, "/api/device/connect", {"source": "camera"}))
            assert dev["source"] == "camera" and dev["pico"]["connected"] and dev["sim_scene"] is None
            s = _ok(_post(url, "/api/calibration/start", {"kind": "stereo"}))
            assert s["target"] == "device" and s["emitters"] == "Emitters off: ambient light only"
            body = _wait(lambda: (r := _post(url, "/api/calibration/capture")).status_code == 409 and
                         "cameras" in r.json()["detail"] and r.json(), 20, "the refusal")
            assert body["detail"] == "Not captured: both cameras must see the board (0 common corners, need 8)"
            assert "both cameras" in body["hint"]
            sp.press(0.08)  # the trigger captures too, and says why it cannot
            msgs = recv_until(ws, lambda m, p, raw: m["type"] == "log" and "Not captured" in m["message"], timeout=10)
            assert msgs[-1][0]["level"] == "warn" and "both cameras" in msgs[-1][0]["message"]
            sp.press(1.2)  # a long press solves, here: refused for lack of views
            msgs = recv_until(ws, lambda m, p, raw: m["type"] == "log" and "at least 4" in m["message"], timeout=10)
            _ok(_post(url, "/api/calibration/cancel"))
            s = _ok(_post(url, "/api/calibration/start", {"kind": "lasers"}))
            assert s["emitters"].startswith("Line lasers on") and sp.emits()[-1] == "EMIT 100 100 0"
            body = _wait(lambda: (r := _post(url, "/api/calibration/capture")).status_code == 409 and
                         "not found" in r.json()["detail"] and r.json(), 20, "the refusal")
            assert body["detail"].startswith("Not captured: the board was not found")
            _refused(_post(url, "/api/calibration/solve"), 409, "at least 3")
            _ok(_post(url, "/api/calibration/cancel"))
            assert sp.emits()[-1] == "EMIT 0 0 0" and all(d == 0 for d in sp.duties())
            s = _ok(_post(url, "/api/calibration/start", {"kind": "imu"}))
            assert s["state"] == "armed" and s["emitters"] == "Emitters off: ambient light only"
            sp.press(0.08)  # a short press starts the IMU recording
            s = _wait(lambda: (x := _get(url, "/api/calibration").json())["state"] == "recording" and x, 10, "recording")
            folder = Path(s["recording"])
            assert folder.parent == tmp_path / "scans" / "calibration" and folder.name.startswith("imu-")
            _refused(_post(url, "/api/calibration/imu/stop"), 409, "keep rotating")
            sp.press(0.08)  # ... and too early, the trigger says so
            recv_until(ws, lambda m, p, raw: m["type"] == "log" and "Keep rotating" in m["message"], timeout=10)
            assert _get(url, "/api/calibration").json()["state"] == "recording"
            time.sleep(0.5)
            _ok(_post(url, "/api/calibration/cancel"))
            assert (folder / "frames.csv").is_file() and (folder / "calibration.json").is_file()
            with open(folder / "frames.csv", newline="", encoding="utf-8") as fh:
                assert sum(1 for _ in csv.DictReader(fh)) > 3  # the partial recording is kept
            assert (folder / "imu.csv").stat().st_size > 100
            assert _get(url, "/api/device").json()["connected"]
    finally:
        sp.close()


def _no_pico(_cfg):
    raise ConnectionError("no Pico found (USB VID 0x2E8A)")


def test_device_lost_cancels_the_calibration(tmp_path, demo_dir):
    from test_app_live import FakeCamera

    class Dying(FakeCamera):
        def read(self, timeout: float = 1.0):
            if self.n_read >= 45:
                raise RuntimeError("camera 0 delivered no frame for 3.0 s")
            return super().read(timeout)

    app = _app(tmp_path, demo_dir, camera_factory=lambda _c: Dying(size=(320, 200), fps=30.0), pico_factory=_no_pico)
    with ServerThread(app) as srv, ws_connect(srv.ws_url, max_size=None, open_timeout=5) as ws:
        url = srv.url
        _ok(_post(url, "/api/device/connect", {"source": "camera"}))
        assert _ok(_post(url, "/api/calibration/start", {"kind": "stereo"}))["state"] == "capturing"
        seen = set()

        def cancelled(m, p, raw):
            if m["type"] == "calib_session":
                seen.add(m["state"])
            return m["type"] == "calib_session" and m["state"] == "idle" and "capturing" in seen

        msgs = recv_until(ws, cancelled, timeout=20)
        logs = [m["message"] for m, _, _ in msgs if m["type"] == "log"]
        assert any("calibration was cancelled because the device stopped" in s for s in logs), logs
        dev = _get(url, "/api/device").json()
        assert dev["connected"] is False and "camera" in (dev["error"] or "")


def test_device_target_stereo_is_saved_and_the_camera_reloads_it(tmp_path, demo_dir):
    """The device target: a virtual scanner plays the camera; the measured print scale is used;
    the save backs up the old file, drops its lasers and IMU, and the connection reloads it."""
    from ohs.sim.board_camera import SimBoardCamera
    from ohs.sim.nominal import nominal_calibration
    from ohs.types import ScannerCalibration

    path = tmp_path / "device" / "calibration.json"
    path.parent.mkdir()
    nominal_calibration((1280, 800)).save(path)  # an old calibration, with laser planes and the IMU
    app = _app(tmp_path, demo_dir, camera_factory=lambda _c: SimBoardCamera("stereo", fps=5.0, **SIM_KW),
               pico_factory=_no_pico)
    with ServerThread(app) as srv:
        url = srv.url
        dev = _ok(_post(url, "/api/device/connect", {"source": "camera"}))
        assert dev["calibration"]["loaded"] and dev["calibration"]["lasers"] == ["horizontal", "vertical"]
        pc = _ok(_post(url, "/api/setup/print-check", {"measured_mm": 99.0, "apply": True}))
        assert pc["scale"] == pytest.approx(0.99)
        s = _ok(_post(url, "/api/calibration/start", {"kind": "stereo"}))
        assert s["target"] == "device" and "Emitters off" in s["emitters"]
        s = _views(url, 15, stall_s=8.0)
        rec = Path(s["recording"])
        assert rec.parent == tmp_path / "scans" / "calibration" and rec.name.startswith("stereo-")
        meta = json.loads((rec / "meta.json").read_text(encoding="utf-8"))
        assert meta["board"]["square_mm"] == pytest.approx(19.8) and meta["board_scale"] == pytest.approx(0.99)
        assert meta["purpose"] == "calib_stereo" and meta["n_frames"] == len(s["views"])
        job, s = _solve(url)
        r = s["result"]
        assert job["state"] == "done" and r["ok"] and r["sim_truth"] is None, r
        rep = _ok(_get(url, "/api/calibration/report.json"))
        assert "19.80 mm" in rep["board"] and rep["baseline_mm"] == pytest.approx(0.99 * 60.0, abs=0.3)
        saved = _ok(_post(url, "/api/calibration/save"))
        info = saved["session"]["saved"]
        assert info["path"] == str(path) and info["backup"] == str(path.with_name("calibration.json.bak"))
        assert any("laser planes and the IMU calibration were removed" in n for n in info["notes"]), info["notes"]
        new = ScannerCalibration.load(path)
        assert not new.lasers and new.imu is None and new.stereo.baseline == pytest.approx(59.4, abs=0.3)
        assert ScannerCalibration.load(path.with_name("calibration.json.bak")).lasers  # the old one, kept
        dev = _get(url, "/api/device").json()  # the connection reloaded it
        assert dev["calibration"]["loaded"] and dev["calibration"]["lasers"] == [] and dev["calibration"]["imu"] is False
        assert dev["calibration"]["baseline_mm"] == pytest.approx(59.4, abs=0.3)
        st = {x["id"]: x["state"] for x in saved["setup"]["steps"]}
        assert saved["setup"]["target"] == "device" and st["stereo"] == "done" and st["lasers"] == "todo"
        assert saved["setup"]["calibration"]["parts"]["stereo"]["session"] == saved["session"]["id"]


# --------------------------------------------------------------------------- units
def test_capture_rules_units():
    from ohs.server import calib_guide as g

    img = np.zeros((800, 1280), np.uint8)
    assert "no camera image" in g.usable_reason(None)
    ids = np.arange(20, dtype=np.int32)
    uv = np.column_stack([np.linspace(100, 600, 20), np.linspace(100, 400, 20)]).astype(np.float32)
    d = g.Detection("stereo", 1, 10.0, (1280, 800), img, img, uv, ids, uv[:6], ids[:6])
    assert g.usable_reason(d) == "both cameras must see the board (6 common corners, need 8)"
    d.corners_r, d.ids_r = uv, ids
    assert g.usable_reason(d) is None
    tr = g.SteadyTracker()
    steps = []
    for t, shift in ((10.0, 0.0), (10.25, 0.1), (10.5, 0.2), (10.6, 2.5), (11.0, 2.5)):
        steps.append(tr.update(g.Detection("stereo", 0, t, (1280, 800), img, img, uv + shift, ids, uv + shift, ids)))
    assert steps == pytest.approx([0.0, 0.5, 1.0, 0.0, 0.8])
    lasers = g.Detection("lasers", 2, 0.0, (1280, 800), img, T_cb=np.eye(4), margin={"vertical": 40, "horizontal": 3})
    assert "the horizontal laser line misses the white page margin (3 points, need 15)" == g.usable_reason(lasers)

    class V:
        def __init__(self, c, dist, face, tilt=20.0, index=0):
            self.center_uv, self.distance_mm, self.face, self.tilt_deg, self.index = np.array(c), dist, face, tilt, index

    face = np.array([0.0, 0.0, -1.0])
    tilted = np.array([np.sin(np.radians(9.0)), 0.0, -np.cos(np.radians(9.0))])
    views = [V((640, 400), 300.0, face, index=4)]
    probe = g.Detection("stereo", 3, 0.0, (1280, 800), img, center_uv=np.array([700.0, 420.0]), distance_mm=310.0,
                        face=face)
    new, close = g.novelty(probe, views)
    assert not new and close.index == 4
    for change in ({"center_uv": np.array([640.0 + 0.13 * 1280, 400.0])}, {"distance_mm": 334.0}, {"face": tilted}):
        p = g.Detection("stereo", 3, 0.0, (1280, 800), img, center_uv=np.array([640.0, 400.0]), distance_mm=300.0,
                        face=face)
        for k, v in change.items():
            setattr(p, k, v)
        assert g.novelty(p, views)[0], change
    assert g.tilt_bin(face, 5.0) == "flat"
    assert g.tilt_bin(np.array([-0.5, 0.0, -0.866]), 30.0) == "left"
    assert g.tilt_bin(np.array([0.0, -0.5, -0.866]), 30.0) == "up"
    assert g.distance_bin("stereo", 250.0) == "near" and g.distance_bin("lasers", 300.0) == "mid"
    cov = g.coverage([type("W", (), {"corners_l": uv, "corners_r": None})()], (1280, 800), stereo=False)
    assert sum(cov["left"]) == len(np.unique(np.floor(uv[:, 1] / 100) * 8 + np.floor(uv[:, 0] / 160)))
    assert cov["right"] is None and 0 < cov["left_pct"] < 100
    h = g.next_hint("stereo", None, [], (1280, 800), (15, 25))
    assert h["text"] == "Move the board to the middle of the picture" and h["region"] == [0.3333, 0.3333, 0.6667, 0.6667]
    centre = [V((640, 400), 300.0, face)]
    assert "top-left" in g.next_hint("stereo", None, centre, (1280, 800), (15, 25))["text"]
    everywhere = [V(((c + 0.5) * 1280 / 3, (r + 0.5) * 800 / 3), 300.0, face, 5.0) for r in range(3) for c in range(3)]
    h = g.next_hint("stereo", None, everywhere, (1280, 800), (15, 25))
    assert h["tilt"] == "left" and h["region"] is None
    h = g.next_hint("lasers", lasers, [], (1280, 800), (6, 10))
    assert "horizontal laser line" in h["text"]


def test_results_in_plain_words():
    from ohs.server import calib_results as cr
    from ohs.types import StereoCalibration

    K = np.array([[720.0, 0, 639.5], [0, 720.0, 399.5], [0, 0, 1]])
    good = StereoCalibration.build((1280, 800), K, np.zeros(5), K, np.zeros(5), np.eye(3), np.array([-60.0, 0, 0]))
    errors = [{"pair": k, "left": 0.2, "right": 0.22} for k in range(15)] + [{"pair": 15, "left": 0.9, "right": 0.7}]
    rep = {"rms": 0.21, "coverage_left": 0.8, "coverage_right": 0.75, "views_used": 16, "views_total": 17,
           "views_rejected": [{"pair": 16, "camera": "left", "reason": "view rms 3.1 px > 1.0 px"}], "max_tilt_deg": 38.0,
           "baseline_mm": 60.0, "rectification_check": {"mean_abs_dv_px": 0.08, "scale_error_pct": 0.05},
           "frame_indices": [k * 2 for k in range(17)], "per_view_errors": errors}
    r = cr.build_result("stereo", good, rep, view_ids=[k * 2 for k in range(17)])
    assert r["grade"] == "excellent" and r["ok"] and r["can_save"] and r["headline"] == "Excellent stereo calibration"
    bad = {p["index"]: p for p in r["per_view"] if p["bad"]}
    assert set(bad) == {30, 32} and bad[32]["error_px"] is None and bad[30]["error_px"] == 0.9
    assert r["advice"][0]["action"] == "remove_bad_views" and "2 worst views" in r["advice"][0]["text"]
    assert all(m["help"] and m["value"] for m in r["metrics"]) and r["sim_truth"] is None
    swapped = StereoCalibration.build((1280, 800), K, np.zeros(5), K, np.zeros(5), np.eye(3), np.array([60.0, 0, 0]))
    r = cr.build_result("stereo", swapped, rep, view_ids=range(17))
    assert r["grade"] == "failed" and "swapped" in r["headline"] and r["can_save"] is False
    assert "swap left/right" in r["verdict"] and r["advice"][0]["action"] == "start_over"
    poor = dict(rep, rms=1.4, coverage_left=0.3, views_used=6, max_tilt_deg=10.0, views_rejected=[])
    r = cr.build_result("stereo", good, poor, view_ids=range(17))
    assert r["grade"] == "poor" and not r["ok"] and r["can_save"]
    assert {a["action"] for a in r["advice"]} >= {"add_views"} and "left picture" in r["advice"][0]["text"] + r["advice"][1]["text"]
    lasers = {"lasers": {"vertical": {"ok": True, "n_observations": 7, "n_inliers": 900, "normal_std_deg": 0.01, "d_std_mm": 0.02},
                         "horizontal": {"ok": False, "reason": "the horizontal line was found on the board in only 1 "
                                                               "observation(s); need >= 3 with different board poses"}},
              "residuals": {"vertical": {"median_abs_mm": 0.03, "p95_abs_mm": 0.1}}, "region_used": {"vertical": "margin"},
              "frames": 7, "frames_with_board": 7, "per_frame": [{"index": k, "board": True, "reproj_rms_px": 0.2}
                                                                 for k in range(7)]}
    r = cr.build_result("lasers", {"vertical": object()}, lasers, view_ids=range(7))
    assert r["grade"] == "poor" and "Only the vertical laser" in r["verdict"] and r["can_save"]
    assert any("horizontal line crosses the white margin" in a["text"] for a in r["advice"])
    none = dict(lasers, lasers={"vertical": {"ok": False, "reason": "x"}, "horizontal": {"ok": False, "reason": "y"}},
                residuals={})
    r = cr.build_result("lasers", {}, none, view_ids=range(7))
    assert r["grade"] == "failed" and r["can_save"] is False and "Neither laser line" in r["verdict"]
    imu = {"residual_rms_deg": 0.05, "time_offset_s": 0.03, "gyro_bias": [0.004, -0.003, 0.002], "xcorr_peak": 0.95,
           "outlier_fraction": 0.0, "excitation_rad_s": [0.6, 0.5, 0.4], "frames": 200, "frames_with_pose": 195, "ok": True,
           "R_ci_angle_deg": 90.4}
    r = cr.build_result("imu", None, imu)
    assert r["grade"] == "excellent" and "30.0 ms" in r["verdict"] and r["per_view"] == []
    chk = {"ok": False, "verdict": "stereo: vertical disparity 0.81 px is too large (want < 0.3 px)",
           "stereo": {"ok": False, "mean_abs_dv_px": 0.81, "scale_error_pct": 0.1},
           "lasers": {"ok": None, "verdict": "no laser planes in the calibration"}, "imu": {"ok": None, "verdict": "no IMU"}}
    r = cr.build_result("check", None, chk)
    assert r["grade"] == "poor" and r["can_save"] is False and r["advice"][0]["action"] == "recalibrate_stereo"
    # the simulator's check moves the board, so the engine's still-board IMU check must not decide it
    sim_chk = {"ok": False, "verdict": "imu: gyro and board rotations disagree by 2.00 deg per frame",
               "stereo": {"ok": True, "mean_abs_dv_px": 0.05, "scale_error_pct": 0.02},
               "lasers": {"ok": True, "lasers": {"vertical": {"ok": True, "median_abs_mm": 0.05}}},
               "imu": {"ok": False, "median_residual_deg": 2.0, "verdict": "recalibrate the IMU"}}
    r = cr.build_result("check", None, sim_chk, sim=True)
    assert r["ok"] and r["grade"] == "excellent" and not any("IMU" in a["text"] for a in r["advice"])
    assert any(m["label"] == "IMU" and m["value"] == "not checked" and "simulator" in m["help"] for m in r["metrics"])
    assert cr.build_result("check", None, sim_chk)["ok"] is False  # on the scanner it counts
    f = cr.failed_result("stereo", cr.plain_error(ValueError("only 3 stereo views with >= 8 common corners, need at least 4")))
    assert f["verdict"] == "Only 3 stereo views with at least 8 common corners, need at least 4." and f["grade"] == "failed"
