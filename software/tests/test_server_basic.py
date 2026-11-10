"""OpenHandScan Studio backend without a device: REST basics, errors, UI serving, guards, units.

A real uvicorn server runs in a background thread (``ohs.server.testing.ServerThread``; httpx and
FastAPI's TestClient are not available). Also unit tests of the protocol framing, the hub's
latest-only slots, decimation / quality colours, the job runner, the tracking states and the
new config keys.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")
requests = pytest.importorskip("requests")

from websockets.sync.client import connect as ws_connect  # noqa: E402

from ohs.app.config import Config, load_config, merge_into, render_template  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.hub import Hub  # noqa: E402
from ohs.server.jobs import JobCancelled, JobRunner  # noqa: E402
from ohs.server.meshing import GREEN, YELLOW, CoverageGrid, decimate, dilate_on_mesh, quality_colors  # noqa: E402
from ohs.server.protocol import (  # noqa: E402
    ApiError,
    mesh_message,
    pack_binary,
    preview_message,
    split_mesh_payload,
    unpack_binary,
)
from ohs.server.session import WeakHintFilter, tracking_state  # noqa: E402
from ohs.server.testing import ServerThread, recv_until  # noqa: E402
from ohs.types import TrackResult  # noqa: E402

TIMEOUT = 10


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("basic")
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        yield {"srv": srv, "url": srv.url, "root": root, "app": app}


def _post(url: str, path: str, body: dict | None = None, **kw):
    return requests.post(url + path, json=body, timeout=TIMEOUT, **kw)


def _get(url: str, path: str, **kw):
    return requests.get(url + path, timeout=TIMEOUT, **kw)


def _assert_error(r, status: int, needle: str = "") -> dict:
    assert r.status_code == status, r.text
    body = r.json()
    assert isinstance(body.get("detail"), str) and body["detail"], body
    assert isinstance(body.get("hint"), str) and body["hint"], body  # every error says what to do
    assert needle.lower() in body["detail"].lower(), body
    return body


# --------------------------------------------------------------------------- REST
def test_health(env):
    h = _get(env["url"], "/api/health").json()
    assert h["app"] == "openhandscan" and h["version"].startswith("0.")
    assert h["engine"]["opencv"] and "open3d" in h["engine"]


def test_projects_crud_and_trash(env):
    url, root = env["url"], env["root"]
    a = _post(url, "/api/projects", {"name": "Intake bracket"}).json()
    time.sleep(1.1)  # creation stamps have 1 s resolution
    b = _post(url, "/api/projects", {"name": "  Kotflügel   links "}).json()
    assert a["name"] == "Intake bracket" and a["scans"] == [] and a["n_scans"] == 0 and a["thumbnail_url"] is None
    assert b["name"] == "Kotflügel links" and b["id"].startswith("kotflugel-links-")
    assert {"id", "name", "created", "modified", "n_scans", "thumbnail_url", "scans"} <= set(a)
    pj = json.loads((root / "projects" / a["id"] / "project.json").read_text(encoding="utf-8"))
    assert {"id", "name", "created", "modified", "scans"} <= set(pj) and pj["ohs_version"]
    listed = _get(url, "/api/projects").json()
    ids = [p["id"] for p in listed]
    assert ids.index(b["id"]) < ids.index(a["id"])  # newest first
    assert "scans" not in listed[0]
    assert _get(url, f"/api/projects/{a['id']}").json()["name"] == "Intake bracket"
    r = requests.patch(url + f"/api/projects/{a['id']}", json={"name": "Bracket v2"}, timeout=TIMEOUT)
    assert r.status_code == 200 and r.json()["name"] == "Bracket v2" and r.json()["id"] == a["id"]
    r = requests.delete(url + f"/api/projects/{a['id']}", timeout=TIMEOUT)
    assert r.status_code == 204 and not r.content
    trashed = list((root / "projects" / ".trash").iterdir())
    assert len(trashed) == 1 and trashed[0].name.startswith(a["id"]) and (trashed[0] / "project.json").exists()
    assert a["id"] not in [p["id"] for p in _get(url, "/api/projects").json()]
    _assert_error(_get(url, f"/api/projects/{a['id']}"), 404, "no project")
    _assert_error(requests.delete(url + f"/api/projects/{a['id']}", timeout=TIMEOUT), 404)
    _assert_error(_post(url, "/api/projects", {"name": "   "}), 400, "empty")
    _assert_error(_post(url, "/api/projects", {}), 422, "invalid request")
    _assert_error(_get(url, "/api/projects/..%2F..%2Fsecret"), 404)


def test_settings(env):
    s = _get(env["url"], "/api/settings").json()
    assert s["paths"]["projects_dir"] == "~/OpenHandScan/projects"
    assert s["app"] == {"host": "127.0.0.1", "port": 8765}
    assert s["effective"]["projects_dir"] == str(env["root"] / "projects")
    assert {"camera", "pico", "emitters", "live", "process", "board", "markers"} <= set(s)
    assert s["effective"]["ui_built"] is False


def test_scan_needs_a_device_and_errors_have_hints(env):
    url = env["url"]
    pid = _post(url, "/api/projects", {"name": "No device"}).json()["id"]
    body = _assert_error(_post(url, "/api/scan/start", {"project_id": pid}), 409, "no device")
    assert "connect" in body["hint"].lower()
    assert _get(url, "/api/scan").json() == {"state": "idle", "project_id": None, "scan_id": None,
                                             "mode": "markerless", "elapsed_s": 0.0, "frames": 0}
    for path in ("/api/scan/stop", "/api/scan/pause", "/api/scan/resume"):
        _assert_error(_post(url, path), 409, "no scan")
    _assert_error(_post(url, "/api/scan/rewind", {"seconds": 2.0}), 409, "no scan")
    _assert_error(_post(url, "/api/scan/start", {"project_id": pid, "mode": "fast"}), 422)
    _assert_error(_post(url, "/api/device/emitters", {"vertical": 50}), 409, "no device")
    _assert_error(_post(url, "/api/device/emitters", {"vertical": 150}), 422)
    _assert_error(_post(url, "/api/device/connect", {"source": "replay"}), 400, "recording")
    _assert_error(_post(url, "/api/device/connect", {"source": "replay", "recording": str(env["root"])}), 404,
                  "not a recording")
    _assert_error(_post(url, "/api/device/connect", {"source": "usb"}), 422)
    dev = _post(url, "/api/device/disconnect").json()
    assert dev["source"] == "none" and dev["connected"] is False and dev["calibration"]["loaded"] is False
    assert _get(url, "/api/device").json()["camera"] is None


def test_unknown_routes_and_files_are_json_404(env):
    url = env["url"]
    _assert_error(_get(url, "/api/nope"), 404, "unknown")
    _assert_error(_post(url, "/api/nope"), 404, "unknown")
    assert "POST" in _assert_error(_get(url, "/api/scan/start"), 405, "not allowed")["hint"]
    _assert_error(_get(url, "/api/jobs/j-missing"), 404, "no job")
    _assert_error(_post(url, "/api/jobs/j-missing/cancel"), 404)
    pid = _post(url, "/api/projects", {"name": "Files"}).json()["id"]
    _assert_error(_get(url, f"/api/projects/{pid}/scans/scan-1/files/mesh.ply"), 404, "no scan")
    _assert_error(_post(url, f"/api/projects/{pid}/scans/scan-1/process", {"quality": "full"}), 404, "no scan")
    assert _get(url, "/api/jobs").json() == [] or isinstance(_get(url, "/api/jobs").json(), list)


def test_ui_page_without_build_then_spa_fallback(env):
    url, static = env["url"], env["root"] / "static"
    r = _get(url, "/")
    assert r.status_code == 200 and "npm run build" in r.text and "software/ui" in r.text
    assert "npm run build" in _get(url, "/projects/abc").text
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>Studio UI</title><div id=root></div>", encoding="utf-8")
    (static / "assets" / "app-1234.js").write_text("console.log('ui')", encoding="utf-8")
    try:
        r = _get(url, "/")
        assert r.status_code == 200 and "Studio UI" in r.text and r.headers["cache-control"] == "no-cache"
        assert "Studio UI" in _get(url, "/projects/abc/scan").text  # SPA fallback
        js = _get(url, "/assets/app-1234.js")
        assert js.status_code == 200 and "javascript" in js.headers["content-type"] and "immutable" in js.headers["cache-control"]
        assert _get(url, "/assets/missing.js").status_code == 404
        api = _get(url, "/api/projects/zzz")
        assert api.status_code == 404 and api.headers["content-type"].startswith("application/json")
        assert _get(url, "/../../etc/passwd").status_code in (200, 404)  # never a file outside static/
        assert "root:" not in _get(url, "/../../etc/passwd").text
    finally:
        for _ in range(100):  # Windows: the server closes the served file a moment after the response
            try:
                (static / "index.html").unlink()
                break
            except PermissionError:
                time.sleep(0.05)


def test_cors_for_the_vite_dev_server(env):
    r = requests.options(env["url"] + "/api/projects", timeout=TIMEOUT, headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "http://localhost:5173"
    r = _get(env["url"], "/api/health", headers={"Origin": "http://127.0.0.1:5173"})
    assert r.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    r = _post(env["url"], "/api/projects", {"name": "From dev"}, headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 200


def test_local_guard_blocks_other_sites(env):
    url = env["url"]
    _assert_error(_post(url, "/api/projects", {"name": "x"}, headers={"Origin": "http://evil.example"}), 403, "cross-origin")
    _assert_error(_post(url, "/api/scan/stop", headers={"Origin": "null"}), 403)
    _assert_error(_get(url, "/api/health", headers={"Host": "rebind.example:8765"}), 403, "host")
    ok = _post(url, "/api/projects", {"name": "Same origin"}, headers={"Origin": url})
    assert ok.status_code == 200
    with pytest.raises(Exception):
        with ws_connect(env["srv"].ws_url, origin="http://evil.example", open_timeout=5) as ws:
            ws.recv(timeout=2)


def test_websocket_hello_device_session_and_status(env):
    with ws_connect(env["srv"].ws_url, max_size=None, open_timeout=5) as ws:
        msgs = recv_until(ws, lambda m, p, raw: m.get("type") == "status", timeout=10)
        types = [m["type"] for m, _, _ in msgs]
        assert types[0] == "hello" and msgs[0][0]["version"] == "0.3.0"
        assert types.index("device") < types.index("status") and "session" in types
        dev = next(m for m, _, _ in msgs if m["type"] == "device")
        assert dev["source"] == "none" and dev["connected"] is False
        st = msgs[-1][0]
        assert st["session"]["state"] == "idle" and st["tracking"]["state"] == "idle" and st["tracking"]["reason"]
        assert {"fitness", "rmse_mm"} <= set(st["tracking"]) and st["fps"] == {"camera": 0.0, "pipeline": 0.0}
        assert st["model"] == {"vertices": 0, "triangles": 0} and st["emitters"] is None and st["distance_mm"] is None
        ws.send(json.dumps({"type": "subscribe", "preview": False, "mesh": False, "max_preview_hz": 5}))
        ws.send("not json")
        t0 = time.monotonic()
        recv_until(ws, lambda m, p, raw: m.get("type") == "status", timeout=5)
        stats = env["app"].state.studio.hub.stats()
        assert any(c["preview"] is False and c["max_preview_hz"] == 5 for c in stats)
        n = len(recv_until(ws, lambda m, p, raw: time.monotonic() - t0 > 1.0, timeout=5))
        assert n >= 5  # ~10 Hz


# --------------------------------------------------------------------------- protocol / hub / meshing units
@pytest.mark.parametrize("extra", ["", "x", "xx", "xxx", "xxxx"])
def test_binary_framing_is_4_byte_aligned(extra):
    data = pack_binary({"type": "t", "pad": extra}, [b"abc", np.arange(3, dtype=np.float32), b""])
    header, payload, off = unpack_binary(data)
    assert header["pad"] == extra and off % 4 == 0 and len(data) % 4 == 0
    assert bytes(payload[:3]) == b"abc" and bytes(payload[3:4]) == b"\0"
    assert np.frombuffer(payload, np.float32, 3, offset=4).tolist() == [0.0, 1.0, 2.0]


def test_mesh_message_round_trip_and_preview():
    V = np.random.default_rng(0).normal(size=(7, 3)) * 100
    N = V / np.linalg.norm(V, axis=1, keepdims=True)
    C = quality_colors(np.arange(7))
    F = np.array([[0, 1, 2], [2, 3, 4], [4, 5, 6]])
    data = mesh_message(5, V, N, C, F)
    header, payload, off = unpack_binary(data)
    assert header == {"type": "mesh", "seq": 5, "vertices": 7, "triangles": 3, "units": "mm", "frame": "world"}
    assert len(payload) == 7 * 12 + 7 * 12 + 7 * 4 + 3 * 12 and off % 4 == 0
    secs = split_mesh_payload(header, payload)
    assert np.allclose(secs["positions"], V.astype(np.float32)) and np.array_equal(secs["indices"], F)
    assert np.array_equal(secs["colors"], C) and secs["colors"].shape == (7, 4)
    empty = mesh_message(0, np.zeros((0, 3)), np.zeros((0, 3)), np.zeros((0, 4)), np.zeros((0, 3)))
    h0, p0, _ = unpack_binary(empty)
    assert h0["vertices"] == h0["triangles"] == 0 and len(p0) == 0
    with pytest.raises(ValueError):
        mesh_message(1, V, N[:3], C, F)
    jp = preview_message("left", b"\xff\xd8jpeg", 640, 400, 9)
    h, p, off = unpack_binary(jp)
    assert h == {"type": "preview", "camera": "left", "format": "jpeg", "w": 640, "h": 400, "seq": 9} and off % 4 == 0
    assert bytes(p[:6]) == b"\xff\xd8jpeg"


class _FakeLoop:
    def __init__(self) -> None:
        self.calls = 0

    def call_soon_threadsafe(self, fn, *args):
        self.calls += 1
        fn(*args)


def test_hub_latest_only_slots_logs_and_preview_rate():
    hub = Hub()
    loop = _FakeLoop()
    c = hub.register(loop)  # type: ignore[arg-type]
    for i in range(5):
        hub.publish_json({"type": "status", "i": i})
        hub.publish_binary("preview:left", b"L%d" % i)
    hub.publish_json({"type": "job", "id": "a", "progress": 0.1})
    hub.publish_json({"type": "job", "id": "b", "progress": 0.2})
    hub.publish_json({"type": "job", "id": "a", "progress": 0.3})
    for i in range(3):
        hub.publish_json({"type": "log", "level": "info", "message": f"m{i}"})
    hub.publish_binary("mesh", b"MESH")
    assert loop.calls == 1  # one wake-up for the whole burst
    batch, delay = hub._take(c, now=100.0)
    texts = [json.loads(x) for x in batch if isinstance(x, str)]
    assert [t["message"] for t in texts if t["type"] == "log"] == ["m0", "m1", "m2"]  # FIFO, all kept
    assert [t["i"] for t in texts if t["type"] == "status"] == [4]  # latest only
    assert sorted((t["id"], t["progress"]) for t in texts if t["type"] == "job") == [("a", 0.3), ("b", 0.2)]
    assert batch[-2:] == [b"L4", b"MESH"] and delay is None  # previews before the mesh, mesh last
    assert c.dropped == 4 + 4 + 1
    hub.subscribe(c, {"max_preview_hz": 10})
    hub.publish_binary("preview:left", b"next")
    batch, delay = hub._take(c, now=100.05)  # 50 ms after the last preview: held back
    assert b"next" not in batch and delay == pytest.approx(0.05, abs=1e-3)
    batch, _ = hub._take(c, now=100.11)
    assert batch == [b"next"]
    hub.subscribe(c, {"preview": False, "mesh": False})
    hub.publish_binary("preview:left", b"no")
    hub.publish_binary("mesh", b"no")
    assert hub._take(c, now=200.0)[0] == [] and not hub.wants("preview") and not hub.wants("mesh")
    hub.unregister(c)
    hub.publish_json({"type": "status"})  # no clients: nothing to do
    assert hub.n_clients == 0


def test_hub_serve_sends_in_order_on_a_real_loop():
    async def main() -> list:
        hub = Hub()
        c = hub.register(asyncio.get_running_loop())
        out: list = []

        async def send(x):
            out.append(x)
            if len(out) >= 3:
                hub.unregister(c)
                c.wake.set()

        task = asyncio.create_task(hub.serve(c, send, send))
        threading.Thread(target=lambda: (hub.publish_json({"type": "session", "s": 1}),
                                         hub.publish_json({"type": "status"}),
                                         hub.publish_binary("mesh", b"M"))).start()
        await asyncio.wait_for(task, 5)
        return out

    out = asyncio.run(main())
    assert json.loads(out[0])["type"] == "session" and json.loads(out[1])["type"] == "status" and out[2] == b"M"


def _grid_mesh(n: int, step: float = 1.0):
    ys, xs = np.mgrid[0:n, 0:n]
    V = np.stack([xs.ravel() * step, ys.ravel() * step, np.zeros(n * n)], axis=1)
    idx = np.arange(n * n).reshape(n, n)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel()
    F = np.concatenate([np.stack([a, b, d], 1), np.stack([a, d, c], 1)])
    N = np.tile([0.0, 0.0, 1.0], (n * n, 1))
    return V, F, N


def test_decimate_vertex_clustering():
    V, F, N = _grid_mesh(201)  # 80k triangles
    q = np.arange(len(V)) % 7
    V2, F2, N2, q2 = decimate(V, F, N, q, max_triangles=20000)
    assert 5000 < len(F2) <= 20000 and len(V2) == len(N2) == len(q2) and F2.max() < len(V2)
    assert np.allclose(np.linalg.norm(N2, axis=1), 1.0) and np.allclose(N2[:, 2], 1.0)
    assert V2[:, :2].min() >= -1e-9 and V2[:, :2].max() <= 200 + 1e-9 and q2.max() == 6
    assert len(np.unique(np.sort(F2, axis=1), axis=0)) == len(F2)  # no duplicate faces
    same = decimate(V, F, N, q, max_triangles=10**6)
    assert same[0] is V and same[1].shape == F.shape


def test_coverage_grid_counts_and_colours():
    g = CoverageGrid(cell_mm=8.0, stride=4)
    K = np.array([[200.0, 0, 80], [0, 200.0, 50], [0, 0, 1]])
    depth = np.full((100, 160), 250.0, np.float32)
    keys = g.frame_keys(depth, K, np.eye(4))
    assert len(keys) > 10 and len(np.unique(keys)) == len(keys)
    for _ in range(5):
        g.add(keys)
    other = g.frame_keys(depth, K, np.array([[1, 0, 0, 400.0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]))
    g.add(other)
    snap = g.snapshot()
    V = np.array([[0.0, 0.0, 250.0], [400.0, 0.0, 250.0], [5000.0, 0.0, 0.0]])
    assert CoverageGrid.lookup(V, snap, 8.0).tolist() == [5, 1, 0]
    g.remove(keys)
    assert CoverageGrid.lookup(V, g.snapshot(), 8.0).tolist() == [4, 1, 0]
    cols = quality_colors(np.array([0, 4, 8, 12, 50]))
    assert cols.dtype == np.uint8 and cols.shape == (5, 4)
    assert cols[0].tolist() == YELLOW.astype(int).tolist() and cols[-1].tolist() == GREEN.astype(int).tolist()
    assert dilate_on_mesh(np.array([0, 0, 7, 0]), np.array([[0, 1, 2]])).tolist() == [7, 7, 7, 0]


def test_tracking_states():
    ok = tracking_state(TrackResult(np.eye(4), True, {"fitness": 0.75, "rmse_mm": 0.3, "gyro_prior": True,
                                                      "degenerate": True}), "markerless")
    assert ok["state"] == "ok" and ok["reason"] is None and ok["fitness"] == 0.75
    weak = tracking_state(TrackResult(np.eye(4), True, {"fitness": 0.3, "rmse_mm": 0.3}), "markerless")
    assert weak["state"] == "weak" and "overlap" in weak["reason"].lower()
    deg = tracking_state(TrackResult(np.eye(4), True, {"fitness": 0.8, "rmse_mm": 0.3, "degenerate": True}), "markerless")
    assert deg["state"] == "weak" and "tags" in deg["reason"].lower()
    # what the surface can't pin down: sliding is a hint even with the gyro, turning only without it
    slide = {"kind": "slide", "axis": [1.0, 0.0, 0.0]}
    turn = {"kind": "turn", "axis": [0.0, 1.0, 0.0]}
    s = tracking_state(TrackResult(np.eye(4), True, {"fitness": 0.8, "rmse_mm": 0.3, "gyro_prior": True,
                                                     "weak": [slide, turn]}), "markerless")
    assert s["state"] == "weak" and s["code"] == "degenerate" and s["weak"] == ["slide", "turn"]
    assert s["reason"].startswith("Flat surface:") and "edge" in s["reason"]
    t = tracking_state(TrackResult(np.eye(4), True, {"fitness": 0.8, "rmse_mm": 0.3, "gyro_prior": True,
                                                     "weak": [turn]}), "markerless")
    assert t["state"] == "ok" and t["weak"] == ["turn"]
    t = tracking_state(TrackResult(np.eye(4), True, {"fitness": 0.8, "rmse_mm": 0.3, "weak": [turn]}), "markerless")
    assert t["state"] == "weak" and t["reason"].startswith("Round or symmetric shape:")
    lost = tracking_state(TrackResult(np.eye(4), False, {"reason": "no_depth"}), "markerless")
    assert lost["state"] == "lost" and lost["code"] == "no_depth" and "200-350 mm" in lost["reason"]
    tags = tracking_state(TrackResult(np.eye(4), True, {"n_tags": 2, "method": "stereo", "rms_mm": 0.2}), "markers")
    assert tags["state"] == "weak" and tags["rmse_mm"] == 0.2
    assert tracking_state(TrackResult(np.eye(4), False, {"reason": "weird"}), "markers")["reason"].startswith("Tracking lost")


def test_weak_hint_needs_a_majority_of_recent_frames():
    f = WeakHintFilter(window=6, need=4)
    slide = {"state": "weak", "code": "degenerate", "reason": "Flat surface: keep an edge in view", "weak": ["slide"]}
    ok = {"state": "ok", "code": None, "reason": None, "weak": []}
    lost = {"state": "lost", "code": "jump", "reason": "Moved too fast", "weak": []}
    shown = [f.apply(t)["state"] for t in [slide, ok, slide, slide, slide, ok, slide, ok, ok, ok, ok]]
    # a lone frame doesn't flicker it on; once 4 of the last 6 agree it stays through a feature-rich frame,
    # and goes once fewer than 4 of the last 6 have it
    assert shown == ["ok", "ok", "ok", "ok", "weak", "weak", "weak", "weak", "ok", "ok", "ok"]
    assert f.apply(lost) == lost  # other states pass through untouched


# --------------------------------------------------------------------------- jobs / config
def test_job_runner_progress_failure_and_cooperative_cancel():
    updates: list[dict] = []
    jr = JobRunner(on_update=updates.append, max_workers=1)
    started = threading.Event()

    def ok(ctx):
        ctx.progress(0.5, "half", force=True)
        return {"answer": 42}

    def bad(ctx):
        raise ApiError(409, "nope", "do something else")

    def slow(ctx):
        started.set()
        for _ in range(500):
            ctx.check()
            time.sleep(0.01)
        return {}

    try:
        j1 = jr.submit("ok", ok)
        assert jr.wait(j1.id, 5).state == "done" and j1.result == {"answer": 42} and j1.progress == 1.0
        assert any(u["id"] == j1.id and u["message"] == "half" for u in updates)
        j2 = jr.submit("bad", bad)
        assert jr.wait(j2.id, 5).state == "failed" and "nope" in j2.error and "do something else" in j2.error
        j3 = jr.submit("slow", slow)
        j4 = jr.submit("queued", ok)  # waits behind j3 (one worker)
        assert started.wait(5)
        assert jr.cancel(j4.id).state == "cancelled"
        jr.cancel(j3.id)
        assert jr.wait(j3.id, 5).state == "cancelled"
        time.sleep(0.1)
        assert j4.state == "cancelled" and j4.result is None
        assert [j.id for j in jr.list()][:2] == [j4.id, j3.id]  # newest first
        d = j1.to_dict()
        assert set(d) == {"id", "kind", "state", "progress", "message", "result", "error", "created"}
        with pytest.raises(ApiError):
            jr.get("j-none")
        with pytest.raises(JobCancelled):
            from ohs.server.jobs import JobContext

            j3.cancel_event.set()
            JobContext(jr, j3).check()
    finally:
        jr.shutdown(timeout=5)


def test_job_cancelled_while_queued_runs_on_done_exactly_once():
    jr = JobRunner(max_workers=2)
    release = threading.Event()
    running = [threading.Event(), threading.Event()]
    done_calls: list[tuple[str, str]] = []

    def block(i):
        def fn(ctx):
            running[i].set()
            release.wait(10)
            return {}
        return fn

    try:
        jr.submit("b0", block(0))
        jr.submit("b1", block(1))
        assert running[0].wait(5) and running[1].wait(5)  # both workers are busy
        ran = threading.Event()
        j3 = jr.submit("queued", lambda ctx: ran.set() or {},
                       on_done=lambda j: done_calls.append((j.id, j.state)))
        assert j3.state == "queued"
        assert jr.cancel(j3.id).state == "cancelled"
        assert jr.wait(j3.id, 5).finished.is_set()
        assert done_calls == [(j3.id, "cancelled")]  # settled at once, not when a worker frees up
        release.set()  # the workers now reach the cancelled job: it must neither run nor call on_done again
        jr.submit("after", lambda ctx: {}).future.result(5)
        assert j3.future.result(5) is None
        assert done_calls == [(j3.id, "cancelled")] and not ran.is_set()
        assert jr.cancel(j3.id).state == "cancelled" and len(done_calls) == 1  # a second cancel is a no-op
    finally:
        release.set()
        jr.shutdown(timeout=5)


def test_config_has_projects_dir_and_app_section(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = Config()
    assert cfg.paths.projects_path == Path("~/OpenHandScan/projects").expanduser()
    assert (cfg.app.host, cfg.app.port) == ("127.0.0.1", 8765)
    text = render_template()
    assert "[app]" in text and 'projects_dir = "~/OpenHandScan/projects"' in text and "port = 8765" in text
    p = tmp_path / "c.toml"
    p.write_text('[app]\nport = 9000\nhost = "0.0.0.0"\n[paths]\nprojects_dir = "D:/scans/projects"\n', encoding="utf-8")
    c2 = load_config(p)
    assert c2.app.port == 9000 and c2.app.host == "0.0.0.0" and c2.paths.projects_path == Path("D:/scans/projects")
    p.write_text("[app]\nport = 70000\n", encoding="utf-8")
    with pytest.raises(ValueError, match="app.port"):
        load_config(p)
    back = Config()
    import tomllib

    assert merge_into(back, tomllib.loads(render_template(comments=False))) == [] and back == Config()
