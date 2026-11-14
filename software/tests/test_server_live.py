"""Live scanning through the real server on the simulator source.

A tiny simulator demo (640x400, 12 frames, the seamless back-and-forth loop of
``ohs.server.demo``) is pre-generated into the module's ``demo_dir``, so ``connect sim`` is instant;
one test covers the first-connect path (202 + ``generate_demo`` job) with an even smaller demo.
Covered: the DeviceState of the sim source, WebSocket hello / device / session / status / pose /
preview / mesh messages (binary layout checked against the payload), start / rewind / stop (the
recording, its ``cut`` event, the project entry), a ``process`` job to done and the ``mesh.ply``
download, a client that never reads (backpressure), and the end of a non-looping replay.
"""
from __future__ import annotations

import base64
import csv
import json
import os
import socket
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
from ohs.server.demo import DemoParams, demo_path, demo_ready, generate_demo  # noqa: E402
from ohs.server.protocol import split_mesh_payload  # noqa: E402
from ohs.server.testing import ServerThread, recv_until  # noqa: E402
from ohs.track.export import load_ply  # noqa: E402

T = 10  # HTTP timeout


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("live")
    generate_demo(demo_path(root / "demo"), DemoParams(n_frames=12, image_size=(640, 400)))
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        yield {"srv": srv, "url": srv.url, "root": root, "studio": app.state.studio}


def _post(url, path, body=None):
    return requests.post(url + path, json=body, timeout=60)


def _wait(cond, timeout=30.0, what="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = cond()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def _connect_sim(url):
    r = _post(url, "/api/device/connect", {"source": "sim"})
    assert r.status_code == 200, r.text
    return r.json()


def _check_mesh(header, payload, raw):
    V, Tn = header["vertices"], header["triangles"]
    assert header["type"] == "mesh" and header["units"] == "mm" and header["frame"] == "world"
    assert isinstance(header["seq"], int)
    (hlen,) = np.frombuffer(bytes(raw[:4]), "<u4")
    assert (4 + int(hlen)) % 4 == 0 and len(payload) == len(raw) - 4 - int(hlen)
    assert len(payload) == V * 12 + V * 12 + V * 4 + Tn * 12  # positions, normals, colors, indices
    s = split_mesh_payload(header, payload)
    if V:
        assert np.isfinite(s["positions"]).all() and s["indices"].max() < V
        assert (s["colors"][:, 3] == 255).all()
        n = np.linalg.norm(s["normals"], axis=1)
        assert np.median(n) == pytest.approx(1.0, abs=1e-3)
    return s


# --------------------------------------------------------------------------- tests
def test_sim_device_previews_and_status(env):
    url = env["url"]
    dev = _connect_sim(url)
    assert dev["source"] == "sim" and dev["connected"] and dev["busy"] is None and dev["pico"] is None
    assert dev["camera"]["connected"] and dev["camera"]["info"]["backend"] == "replay"
    cal = dev["calibration"]
    assert cal["loaded"] and cal["image_size"] == [640, 400] and cal["imu"] and cal["lasers"] == ["horizontal", "vertical"]
    assert cal["baseline_mm"] == pytest.approx(60.0, abs=0.5) and cal["path"].endswith("calibration.json")
    assert dev["replay"]["loop"] is True and Path(dev["replay"]["path"]).name == "sim-demo"
    with ws_connect(env["srv"].ws_url, max_size=None, open_timeout=5) as ws:
        msgs = recv_until(ws, lambda m, p, raw: m.get("type") == "hello", timeout=5)
        assert msgs[0][0]["version"] == "0.3.0"
        seen: dict = {}

        def done(m, p, raw):
            if m["type"] == "status":
                if m["distance_mm"] is not None and m["fps"]["camera"] > 5:
                    seen["status"] = (m, p)
            else:
                seen.setdefault(m["type"] + (":" + m.get("camera", "") if m["type"] == "preview" else ""), (m, p))
            return all(k in seen for k in ("device", "session", "preview:left", "preview:depth", "status"))

        recv_until(ws, done, timeout=20)
        assert seen["device"][0]["connected"] and seen["session"][0]["state"] == "idle"
        for cam in ("left", "depth"):
            h, payload = seen[f"preview:{cam}"]
            assert h["format"] == "jpeg" and (h["w"], h["h"]) == (320, 200) and isinstance(h["seq"], int)
            assert bytes(payload[:2]) == b"\xff\xd8"  # JPEG
        st = seen["status"][0]
        assert 150 < st["distance_mm"] < 400 and st["fps"]["camera"] > 5 and st["tracking"]["state"] == "idle"
        assert st["record_backlog"] == 0  # frames waiting to be written: none while not recording
        assert st["gpu_fallback"] is None  # no GPU stage failed (and nothing is scanning)


@pytest.mark.slow
def test_scan_rewind_stop_and_process(env):
    url, root = env["url"], env["root"]
    if not requests.get(url + "/api/device", timeout=T).json()["connected"]:
        _connect_sim(url)
    pid = _post(url, "/api/projects", {"name": "Sim sphere"}).json()["id"]
    with ws_connect(env["srv"].ws_url, max_size=None, open_timeout=5) as ws:
        ws.send(json.dumps({"type": "subscribe", "preview": True, "mesh": True, "max_preview_hz": 15}))
        r = _post(url, "/api/scan/start", {"project_id": pid, "mode": "markerless", "name": "Sim scan"})
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["state"] == "scanning" and s["project_id"] == pid and s["scan_id"] and s["mode"] == "markerless"
        _assert = _post(url, "/api/scan/start", {"project_id": pid})
        assert _assert.status_code == 409 and _assert.json()["hint"]
        t0 = time.monotonic()
        got: dict = {}

        def until_mesh(m, p, raw):
            got.setdefault(m["type"], []).append(m)
            if m["type"] == "mesh":
                _check_mesh(m, p, raw)
                return m["vertices"] > 0 and m["triangles"] > 0 and time.monotonic() - t0 > 2.0
            return False

        recv_until(ws, until_mesh, timeout=40)
        poses = got["pose"]
        assert len(poses[-1]["T_wc"]) == 16 and isinstance(poses[-1]["ok"], bool)
        assert sum(p["ok"] for p in poses) >= 0.8 * len(poses)
        sts = [m for m in got["status"] if m["session"]["state"] == "scanning"]
        last = sts[-1]
        assert last["session"]["frames"] > 0 and last["session"]["elapsed_s"] > 0
        assert last["tracking"]["state"] in ("ok", "weak") and last["fps"]["pipeline"] > 0
        assert last["laser_points"] > 0 and last["model"]["triangles"] > 0 and last["distance_mm"] > 100
        assert all(isinstance(m["record_backlog"], int) and m["record_backlog"] >= 0 for m in sts)
        assert all(m["gpu_fallback"] is None for m in sts)
        assert any(m["type"] == "session" and m["state"] == "scanning" for m in got["session"])
        rw = _post(url, "/api/scan/rewind", {"seconds": 0.5})
        assert rw.status_code == 200, rw.text
        info = rw.json()["rewind"]
        assert rw.json()["state"] == "scanning" and info["dropped_frames"] > 0 and info["rebuild_s"] < 3.0
        assert info["t1"] - info["t0"] == pytest.approx(0.5)
        recv_until(ws, lambda m, p, raw: m["type"] == "mesh", timeout=10)  # a fresh model after the rewind
        time.sleep(0.5)
        r = _post(url, "/api/scan/stop")
        assert r.status_code == 200, r.text
        scan = r.json()["scan"]
        assert scan["status"] == "recorded" and scan["n_frames"] > 30 and scan["duration_s"] > 1.0
        assert scan["name"] == "Sim scan" and scan["result"] is None and scan["thumbnail_url"]
        recv_until(ws, lambda m, p, raw: m["type"] == "session" and m["state"] == "idle", timeout=10)
    assert requests.get(url + "/api/scan", timeout=T).json()["state"] == "idle"
    # the recording
    rec = root / "projects" / pid / "scans" / scan["id"] / "recording"
    with open(rec / "frames.csv", newline="", encoding="utf-8") as fh:
        assert sum(1 for _ in csv.DictReader(fh)) == scan["n_frames"]
    with open(rec / "events.csv", newline="", encoding="utf-8") as fh:
        cuts = [row for row in csv.DictReader(fh) if row["kind"] == "cut"]
    assert len(cuts) == 1
    c0, c1 = (float(x) for x in cuts[0]["value"].split(","))
    assert c1 - c0 == pytest.approx(0.5, abs=1e-5) and float(cuts[0]["t_host"]) == pytest.approx(c1)
    assert (rec / "calibration.json").is_file() and (rec / "imu.csv").stat().st_size > 1000
    meta = json.loads((rec / "meta.json").read_text(encoding="utf-8"))
    assert meta["purpose"] == "scan" and meta["mode"] == "markerless" and meta["n_frames"] == scan["n_frames"]
    assert meta["source"] == "sim" and np.allclose(meta["cuts"], [[c0, c1]], atol=1e-5)
    assert requests.get(url + scan["thumbnail_url"], timeout=T).content[:2] == b"\xff\xd8"
    proj = requests.get(url + f"/api/projects/{pid}", timeout=T).json()
    assert proj["n_scans"] == 1 and proj["scans"][0]["status"] == "recorded" and proj["thumbnail_url"]
    # process (preview quality) to done, then download the mesh
    with ws_connect(env["srv"].ws_url, max_size=None, open_timeout=5) as ws:
        job = _post(url, f"/api/projects/{pid}/scans/{scan['id']}/process", {"quality": "preview"}).json()
        assert job["kind"] == "process_scan" and job["state"] in ("queued", "running") and 0 <= job["progress"] <= 1
        again = _post(url, f"/api/projects/{pid}/scans/{scan['id']}/process", {"quality": "preview"})
        assert again.status_code == 409 and "already" in again.json()["detail"]
        assert requests.get(url + f"/api/projects/{pid}", timeout=T).json()["scans"][0]["status"] == "processing"
        msgs = recv_until(ws, lambda m, p, raw: m["type"] == "job" and m["id"] == job["id"] and m["state"] == "done",
                          timeout=120)
        progress = [m["progress"] for m, _, _ in msgs if m["type"] == "job" and m["id"] == job["id"]]
        assert progress == sorted(progress) and progress[-1] == 1.0
    j = requests.get(url + f"/api/jobs/{job['id']}", timeout=T).json()
    assert j["state"] == "done" and j["error"] is None and j["result"]["scan"]["status"] == "processed"
    res = j["result"]["scan"]["result"]
    assert res["mesh_url"].endswith("/files/mesh.ply") and res["stl_url"].endswith("/files/mesh.stl")
    assert res["laser_points_url"].endswith("/files/laser_points.ply")
    rep = res["report"]
    assert rep["frames"]["cut"] > 0 and rep["frames"]["tracked_fraction"] > 0.8 and rep["quality"] == "preview"
    r = requests.get(url + res["mesh_url"], timeout=T)
    assert r.status_code == 200 and r.content.startswith(b"ply")
    out = root / "mesh.ply"
    out.write_bytes(r.content)
    m = load_ply(out)
    assert len(m["faces"]) > 1000 and len(m["vertices"]) > 500
    assert requests.get(url + res["stl_url"], timeout=T).status_code == 200
    rj = requests.get(url + f"/api/projects/{pid}/scans/{scan['id']}/files/report.json", timeout=T).json()
    assert rj["frames"]["cut"] == rep["frames"]["cut"] and np.allclose(rj["cuts"], [[c0, c1]], atol=1e-5)
    assert not list((root / "projects" / pid / "scans" / scan["id"]).glob("result.tmp-*"))
    delete = requests.delete(url + f"/api/projects/{pid}", timeout=T)
    assert delete.status_code == 204


def test_gpu_fallback_in_the_status_and_one_notification(env, monkeypatch):
    from ohs.app.pipeline import FramePipeline

    url = env["url"]
    if not requests.get(url + "/api/device", timeout=T).json()["connected"]:
        _connect_sim(url)
    pid = _post(url, "/api/projects", {"name": "GPU fallback"}).json()["id"]
    real = FramePipeline.process
    calls = []

    def failing(self, frame):  # a GPU stage fails on the third frame, as the pipeline records it
        res = real(self, frame)
        calls.append(1)
        if len(calls) >= 3:
            self.gpu_fallbacks.setdefault("depth", "out of memory")
        return res

    monkeypatch.setattr(FramePipeline, "process", failing)
    with ws_connect(env["srv"].ws_url, max_size=None, open_timeout=5) as ws:
        assert _post(url, "/api/scan/start", {"project_id": pid, "mode": "markerless"}).status_code == 200
        logs: list = []
        last: dict = {}

        def seen(m, p, raw):
            if m["type"] == "log" and "graphics card" in m["message"]:
                logs.append(m)
            if m["type"] == "status":
                last.update(m)
            return time.monotonic() > t_end and last.get("gpu_fallback") is not None

        t_end = time.monotonic() + 2.0
        recv_until(ws, seen, timeout=40)
        assert last["gpu_fallback"] == {"depth": "out of memory"}  # kept while the scan runs
        assert len(logs) == 1 and logs[0]["level"] == "warn"  # one notification, however many frames follow
        assert "(depth)" in logs[0]["message"] and "processor" in logs[0]["message"]
        assert _post(url, "/api/scan/stop").status_code == 200
        recv_until(ws, lambda m, p, raw: m["type"] == "status" and m["session"]["state"] == "idle"
                   and m["gpu_fallback"] is None, timeout=10)  # null again when idle


def _raw_ws_never_reading(host: str, port: int) -> socket.socket:
    """A WebSocket client that completes the handshake and then never reads another byte."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    s.connect((host, port))
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET /api/ws/live HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(1)
    assert b" 101 " in buf.split(b"\r\n", 1)[0], buf
    return s


def test_a_client_that_never_reads_does_not_stall_the_scan(env):
    url, studio = env["url"], env["studio"]
    if not requests.get(url + "/api/device", timeout=T).json()["connected"]:
        _connect_sim(url)
    pid = _post(url, "/api/projects", {"name": "Backpressure"}).json()["id"]
    slow = _raw_ws_never_reading(env["srv"].host, env["srv"].port)
    try:
        assert _post(url, "/api/scan/start", {"project_id": pid}).status_code == 200
        with ws_connect(env["srv"].ws_url, max_size=None, open_timeout=5) as ws:
            t0 = time.monotonic()
            frames0 = requests.get(url + "/api/scan", timeout=T).json()["frames"]
            msgs = recv_until(ws, lambda m, p, raw: time.monotonic() - t0 > 4.0, timeout=20)
            frames1 = requests.get(url + "/api/scan", timeout=T).json()["frames"]
        kinds = [m["type"] for m, _, _ in msgs]
        assert kinds.count("status") >= 25  # ~10 Hz for the reading client
        assert kinds.count("preview") >= 10 and kinds.count("pose") >= 10
        assert frames1 - frames0 >= 60  # the camera runs at 30 fps and every frame is recorded
        last = [m for m, _, _ in msgs if m["type"] == "status"][-1]
        assert last["fps"]["pipeline"] > 2 and last["tracking"]["state"] in ("ok", "weak")
        stats = studio.hub.stats()
        slow_stats = [c for c in stats if c["dropped"] > 50]
        assert slow_stats, stats  # the slow client's slots were overwritten instead of queueing up
        assert all(c["pending"] <= 12 for c in stats)  # bounded: one message per key (+ a few logs)
    finally:
        slow.close()
        r = _post(url, "/api/scan/stop")
        assert r.status_code == 200, r.text


def test_first_sim_connect_generates_the_demo(tmp_path):
    cfg = Config()
    cfg.paths.calibration = str(tmp_path / "no-calibration.json")
    app = create_app(cfg, projects_dir=tmp_path / "projects", demo_dir=tmp_path / "demo", static_dir=tmp_path / "static",
                     data_dir=tmp_path / "data", config_path=tmp_path / "config.toml")
    studio = app.state.studio
    studio.devices.demo_params = DemoParams(n_frames=6, image_size=(320, 200))
    with ServerThread(app) as srv, ws_connect(srv.ws_url, max_size=None, open_timeout=5) as ws:
        r = requests.post(srv.url + "/api/device/connect", json={"source": "sim"}, timeout=T)
        assert r.status_code == 202, r.text
        job = r.json()["job"]
        assert job["kind"] == "generate_demo" and job["state"] in ("queued", "running", "done")
        dev = requests.get(srv.url + "/api/device", timeout=T).json()
        assert dev["busy"] == "Generating simulator demo…" or dev["connected"]
        seen = {"connected": False, "done": False}

        def both(m, p, raw):
            if m["type"] == "device" and m["connected"]:
                seen["connected"] = True
            if m["type"] == "job" and m["kind"] == "generate_demo" and m["state"] == "done":
                seen["done"] = True
            return seen["connected"] and seen["done"]

        msgs = recv_until(ws, both, timeout=60)
        assert any(m["type"] == "device" and m["busy"] for m, _, _ in msgs)
        dev = requests.get(srv.url + "/api/device", timeout=T).json()
        assert dev["source"] == "sim" and dev["busy"] is None and dev["calibration"]["image_size"] == [320, 200]
        assert demo_ready(demo_path(tmp_path / "demo"))
        assert requests.get(srv.url + f"/api/jobs/{job['id']}", timeout=T).json()["state"] == "done"
        requests.post(srv.url + "/api/device/disconnect", timeout=T)
        t0 = time.monotonic()
        r = requests.post(srv.url + "/api/device/connect", json={"source": "sim"}, timeout=T)
        assert r.status_code == 200 and r.json()["connected"] and time.monotonic() - t0 < 2.0  # cached now
    assert not list((tmp_path / "demo").glob("*.partial-*"))


def test_replay_without_loop_ends_and_disconnects(env):
    url = env["url"]
    rec = demo_path(env["root"] / "demo")
    with ws_connect(env["srv"].ws_url, max_size=None, open_timeout=5) as ws:
        r = _post(url, "/api/device/connect", {"source": "replay", "recording": str(rec), "loop": False})
        assert r.status_code == 200, r.text
        dev = r.json()
        assert dev["source"] == "replay" and dev["replay"] == {"path": str(rec), "loop": False}
        assert dev["calibration"]["loaded"] and dev["imu_stream"] is True
        msgs = recv_until(ws, lambda m, p, raw: m["type"] == "device" and m["source"] == "none", timeout=20)
        logs = [m["message"] for m, _, _ in msgs if m["type"] == "log"]
        assert any("ended" in s for s in logs), logs
    assert requests.get(url + "/api/device", timeout=T).json()["connected"] is False
