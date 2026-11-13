"""The processing studio (APP_API.md, M3 phase 2) through the real server.

Small simulated recordings are copied into scans (no live scan needed):

* a 10-frame sphere scan (640x400): track -> edits -> undo / redo -> selections -> overlays ->
  recipes (edits and all) -> fuse -> straighten (no loops: skipped); the WebSocket ``process``
  messages; the 409 rules (recording, busy, no track, no mesh); a cancelled track; recipes CRUD;
  "Make mesh" running the Default recipe; a scan processed before M3 being taken over;
* ``process_recording(poses=...)``: fusing at the tracked poses gives the tracked mesh again, at
  the true poses the trajectory error is zero;
* a 400 deg orbit around the bracket (as in ``test_track_posegraph.py``) whose trajectory is the
  truth plus synthetic drift: the real ``straighten_trajectory`` closes loops, the scan is fused
  again at the corrected poses, and the trajectory overlay shows both with the loop links.
"""
from __future__ import annotations

import base64
import csv
import dataclasses
import json
import shutil
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

from websockets.sync.client import connect as ws_connect  # noqa: E402

from ohs.app.config import Config  # noqa: E402
from ohs.app.pipeline import PipelineParams  # noqa: E402
from ohs.app.process import ProcessParams, process_recording  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.processing import BUILTIN_RECIPES, read_trajectory  # noqa: E402
from ohs.server.testing import ServerThread, recv_until  # noqa: E402
from ohs.sim.datasets import make_scan_recording  # noqa: E402
from ohs.track.export import load_ply  # noqa: E402

T = 30  # HTTP timeout


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("studio")
    rec = make_scan_recording(root / "rec", n_frames=10, image_size=(640, 400), seed=3, depth_every=0)
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        e = {"srv": srv, "url": srv.url, "root": root, "studio": app.state.studio, "rec": rec}
        e["pid"], e["sid"] = add_scan(e, "Sphere", rec)
        yield e


def add_scan(env: dict, name: str, rec: Path, project: str | None = None) -> tuple[str, str]:
    """A project with one recorded scan whose recording is a copy of ``rec``."""
    st = env["studio"]
    pid = project or requests.post(env["url"] + "/api/projects", json={"name": name}, timeout=T).json()["id"]
    scan, rec_dir = st.projects.add_scan(pid, name, "markerless")
    shutil.copytree(rec, rec_dir)
    n = len(RecordingReader(rec_dir))
    st.projects.update_scan(pid, scan["id"], status="recorded", n_frames=n, duration_s=n / 30.0)
    return pid, scan["id"]


def base_url(env: dict, pid: str | None = None, sid: str | None = None) -> str:
    return f"{env['url']}/api/projects/{pid or env['pid']}/scans/{sid or env['sid']}"


def post(url: str, body: dict | None = None):
    return requests.post(url, json=body, timeout=T)


def get(url: str):
    return requests.get(url, timeout=T)


def err(r, status: int, needle: str = "") -> dict:
    assert r.status_code == status, r.text
    body = r.json()
    assert body["detail"] and body.get("hint"), body  # every error says what to do
    assert needle.lower() in body["detail"].lower(), body
    return body


def wait_job(env: dict, jid: str, timeout: float = 180.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        j = get(f"{env['url']}/api/jobs/{jid}").json()
        if j["state"] not in ("queued", "running"):
            return j
        time.sleep(0.1)
    raise AssertionError(f"job {jid} did not finish within {timeout:.0f} s")


def run_job(env: dict, r, kind: str) -> dict:
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["kind"] == kind and job["state"] in ("queued", "running")
    done = wait_job(env, job["id"])
    assert done["state"] == "done", done
    return done


def mesh_faces(env: dict, url: str) -> int:
    r = get(env["url"] + url)
    assert r.status_code == 200 and r.content.startswith(b"ply")
    p = env["root"] / f"dl-{time.monotonic_ns()}.ply"
    p.write_bytes(r.content)
    return len(load_ply(p)["faces"])


def mesh_quality(env: dict, url: str) -> np.ndarray | None:
    """Each vertex's sigma in the served PLY (``quality``), None when it has none."""
    r = get(env["url"] + url)
    p = env["root"] / f"dl-{time.monotonic_ns()}.ply"
    p.write_bytes(r.content)
    return load_ply(p)["quality"]


def faces_b64(idx) -> str:
    return base64.b64encode(np.asarray(idx, dtype="<u4").tobytes()).decode("ascii")


# --------------------------------------------------------------------------- engine
def test_process_recording_fuses_at_given_poses(env, tmp_path):
    params = ProcessParams(pipeline=PipelineParams(scale=0.5), export=("ply",))
    tracked = process_recording(env["rec"], tmp_path / "tracked", params=params)
    rows = read_trajectory(tmp_path / "tracked" / "trajectory.csv")
    poses = {i: T for i, _, ok, T in rows if ok}
    fused = process_recording(env["rec"], tmp_path / "fused", params=params, poses=poses)
    assert fused["poses"] == "given" and fused["frames"]["processed"] == len(poses) == fused["frames"]["tracked"]
    assert fused["params"]["tracking"]["model"].startswith("none")
    assert len(load_ply(tmp_path / "fused" / "mesh.ply")["faces"]) == tracked["mesh"]["n_faces"]
    assert [r[0] for r in read_trajectory(tmp_path / "fused" / "trajectory.csv")] == sorted(poses)
    # at the true poses (in the tracked world frame), half of the frames: no trajectory error
    gt = RecordingReader(env["rec"]).ground_truth_poses()
    i0 = min(poses)
    A = poses[i0] @ np.linalg.inv(gt[i0])
    half = {i: A @ gt[i] for i in sorted(poses)[::2]}
    at_truth = process_recording(env["rec"], tmp_path / "truth", params=params, poses=half)
    n_rec = len(RecordingReader(env["rec"]))  # a frame the tracker lost (it can, on a busy CPU) has no pose either
    assert at_truth["frames"]["processed"] == len(half) and at_truth["frames"]["without_pose"] == n_rec - len(half)
    assert at_truth["ground_truth"]["rms_trans_mm"] < 1e-3
    with pytest.raises(ValueError, match="none of the given poses"):
        process_recording(env["rec"], tmp_path / "none", params=params, poses={999: np.eye(4)})


# --------------------------------------------------------------------------- REST
def test_a_fresh_scan_and_the_409_rules(env):
    b = base_url(env)
    s = get(b + "/process").json()
    assert s["project_id"] == env["pid"] and s["scan_id"] == env["sid"]
    assert {k: v["state"] for k, v in s["stages"].items()} == {"track": "none", "straighten": "none", "fuse": "none",
                                                               "spacetime": "none", "refine": "none"}
    assert s["base"] is None and s["current"] is None and s["edits"] == [] and s["cursor"] == 0
    assert s["can_undo"] is False and s["can_redo"] is False and s["busy"] is None and s["job"] is None
    assert {"quality", "tracked_pct"} <= set(s["stages"]["track"]) and {"loops", "correction_mm", "correction_deg"} <= \
        set(s["stages"]["straighten"]) and "voxel_mm" in s["stages"]["fuse"]
    assert {"source", "points", "outliers", "moved_pct", "moved_mm", "residual_before_mm", "residual_after_mm"} <= \
        set(s["stages"]["refine"])
    err(post(b + "/process/straighten", {}), 409, "track")
    err(post(b + "/process/refine", {}), 409, "track")
    err(post(b + "/process/fuse", {"voxel_mm": 2.0}), 409, "track")
    err(post(b + "/process/edits", {"op": "fill_holes", "params": {"max_hole_mm": 5}}), 409, "no mesh")
    err(post(b + "/process/undo"), 409, "no mesh")
    err(post(b + "/process/redo"), 409, "no mesh")
    err(post(b + "/process/recipe", {"recipe_id": "default", "scope": "edits"}), 409, "no mesh")
    err(get(b + "/process/holes.json"), 409, "no mesh")
    err(post(b + "/fit", {"kind": "plane", "face": 0, "point": [0, 0, 250]}), 409, "no mesh")
    err(post(b + "/check", {"tolerance_mm": 0.1}), 409, "no mesh")
    err(get(b + "/process/deviation.json"), 409, "no mesh")
    err(get(b + "/process/deviation.bin"), 409, "no mesh")
    err(get(b + "/process/trajectory.json"), 404, "trajectory")
    err(post(b + "/process/track", {"quality": "draft"}), 422)
    err(post(b + "/process/fuse", {"voxel_mm": 0.01}), 422)
    err(post(b + "/process/edits", {"op": "explode"}), 422)
    err(post(b + "/process/recipe", {"recipe_id": "nope", "scope": "edits"}), 404, "recipe")
    err(get(base_url(env, sid="scan-missing") + "/process"), 404, "no scan")


def test_track_edits_undo_redo_and_the_process_messages(env):
    b = base_url(env)
    with ws_connect(env["srv"].ws_url, max_size=None, open_timeout=5) as ws:
        ws.send(json.dumps({"type": "subscribe", "preview": False, "mesh": False}))
        r = post(b + "/process/track", {"quality": "preview"})
        assert r.status_code == 200, r.text
        job = r.json()
        assert job["kind"] == "process_track" and job["project_id"] == env["pid"] and job["scan_id"] == env["sid"]
        err(post(b + "/process/track", {"quality": "preview"}), 409, "busy")  # one job per scan
        err(post(b + "/process", {"quality": "preview"}), 409, "busy")
        seen: list[dict] = []

        def done(m, p, raw):
            if m.get("type") == "process" and m.get("scan_id") == env["sid"]:
                seen.append(m)
                return m["stages"]["track"]["state"] == "done" and m["job"] is None
            return False

        recv_until(ws, done, timeout=180)
        running = [m for m in seen if m["stages"]["track"]["state"] == "running"]
        assert running and running[0]["busy"] == "Tracking the scan…" and running[0]["job"]["id"] == job["id"]
        assert running[0]["stages"]["track"]["job_id"] == job["id"]
    assert wait_job(env, job["id"])["state"] == "done"
    s = get(b + "/process").json()
    tr = s["stages"]["track"]
    assert tr["quality"] == "preview" and tr["tracked_pct"] >= 80 and tr["summary"].startswith("Tracked") and tr["when"]
    assert s["base"]["stage"] == "track" and s["base"]["triangles"] > 1000 and s["edits"] == [] and s["cursor"] == 0
    cur = s["current"]
    assert cur["version"] >= 1 and cur["mesh_url"] == f"/api/projects/{env['pid']}/scans/{env['sid']}/files/mesh.ply"
    r = get(env["url"] + cur["mesh_url"] + f"?v={cur['version']}")  # the UI adds the version to the URL
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    st0 = cur["stats"]
    assert st0["triangles"] == s["base"]["triangles"] and st0["vertices"] == s["base"]["vertices"]
    assert {"components", "holes", "largest_hole_mm", "border_mm", "boundary_edges", "non_manifold_edges",
            "watertight", "bbox_mm", "area_cm2", "volume_cm3"} <= set(st0)
    assert st0["border_mm"] is not None  # an open scan has an outer border
    assert mesh_faces(env, cur["mesh_url"]) == s["base"]["triangles"]
    # each vertex's sigma: with the confidence reconstruction (a GPU), not with Open3D's
    qs = [mesh_quality(env, cur["mesh_url"])]
    confident = qs[0] is not None
    assert (st0["sigma_mm"] is not None) == confident
    if confident:
        assert len(qs[0]) == st0["vertices"] and np.all(qs[0] > 0) and 0 < st0["sigma_mm"]["median"] < 50.0
    scan = get(f"{env['url']}/api/projects/{env['pid']}").json()["scans"][0]
    assert scan["status"] == "processed" and scan["result"]["report"]["quality"] == "preview"
    res = env["root"] / "projects" / env["pid"] / "scans" / env["sid"] / "result"
    assert {"mesh.ply", "mesh.stl", "report.json", "trajectory.csv", "laser_points.ply"} <= {p.name for p in res.iterdir()}
    # edits
    v0 = cur["version"]
    plan = [("remove_floaters", {"min_size_mm": 5}, "Remove loose pieces smaller than 5 mm"),
            ("fill_holes", {"max_hole_mm": 30}, "Fill holes up to 30 mm"),
            ("smooth", {"method": "bilateral", "strength": 0.3}, "Smooth (bilateral, 30 %, edges kept)"),
            ("decimate", {"ratio": 0.5}, "Simplify to 50 % of the triangles")]
    tris = [s["base"]["triangles"]]
    for k, (op, params, label) in enumerate(plan):
        j = run_job(env, post(b + "/process/edits", {"op": op, "params": params}), "mesh_edit")
        e = j["result"]["edit"]
        assert e["op"] == op and e["label"] == label and e["ms"] >= 0 and e["summary"] == j["result"]["message"]
        s = get(b + "/process").json()
        assert len(s["edits"]) == k + 1 and s["cursor"] == k + 1 and s["current"]["version"] == v0 + k + 1
        assert s["edits"][-1]["triangles_after"] == s["current"]["stats"]["triangles"]
        tris.append(s["edits"][-1]["triangles_after"])
        qs.append(mesh_quality(env, s["current"]["mesh_url"]))
        if confident:  # the sigmas follow every edit; filled holes are "not measured"
            sm = s["current"]["stats"]["sigma_mm"]
            assert len(qs[-1]) == s["current"]["stats"]["vertices"] and sm is not None
            if op == "fill_holes":
                assert (sm["not_measured"] > 0) == (tris[-1] > tris[-2]) and np.any(qs[-1] == 50.0) == (tris[-1] > tris[-2])
        else:
            assert qs[-1] is None
    assert s["edits"][0]["params"] == {"min_size_mm": 5.0}
    assert s["edits"][2]["params"] == {"method": "bilateral", "strength": 0.3, "keep_edges": True}
    assert tris[2] >= tris[1] and abs(tris[4] - tris[3] / 2) <= max(10, 0.01 * tris[3])
    assert s["can_undo"] and not s["can_redo"]
    assert mesh_faces(env, s["current"]["mesh_url"]) == tris[4]
    # undo / redo: instant, and the result files follow; a step missing from the cache is rebuilt
    # by replaying its edit (sigmas too)
    cache = env["root"] / "projects" / env["pid"] / "scans" / env["sid"] / "process" / "cache"
    (cache / f"{s['edits'][2]['id']}.npz").unlink()
    ver = s["current"]["version"]
    t0 = time.perf_counter()
    u = post(b + "/process/undo").json()
    assert time.perf_counter() - t0 < 5.0
    assert u["cursor"] == 3 and u["can_redo"] and u["current"]["version"] == ver + 1
    assert u["current"]["stats"]["triangles"] == tris[3] and mesh_faces(env, u["current"]["mesh_url"]) == tris[3]
    if confident:  # the step's own sigmas come back
        assert np.array_equal(mesh_quality(env, u["current"]["mesh_url"]), qs[3])
    u = post(b + "/process/undo").json()
    assert u["cursor"] == 2 and mesh_faces(env, u["current"]["mesh_url"]) == tris[2]
    r = post(b + "/process/redo").json()
    assert r["cursor"] == 3 and r["current"]["version"] == ver + 3 and mesh_faces(env, r["current"]["mesh_url"]) == tris[3]
    for _ in range(3):
        u = post(b + "/process/undo").json()
    assert u["cursor"] == 0 and not u["can_undo"] and u["current"]["stats"]["triangles"] == tris[0]
    err(post(b + "/process/undo"), 409, "nothing to undo")
    for _ in range(4):
        r = post(b + "/process/redo").json()
    assert r["cursor"] == 4 and not r["can_redo"] and mesh_faces(env, r["current"]["mesh_url"]) == tris[4]
    err(post(b + "/process/redo"), 409, "nothing to redo")
    # a new edit after an undo drops the redo steps
    post(b + "/process/undo")
    run_job(env, post(b + "/process/edits", {"op": "decimate", "params": {"target_triangles": tris[3] // 3}}),
            "mesh_edit")
    s = get(b + "/process").json()
    assert len(s["edits"]) == 4 and s["cursor"] == 4 and not s["can_redo"]
    assert s["edits"][3]["params"] == {"target_triangles": tris[3] // 3}
    assert {p.stem for p in cache.glob("*.npz")} == {"base"} | {e["id"] for e in s["edits"]}


def test_undo_writes_the_files_without_the_main_lock(env, monkeypatch):
    """Undo / redo hold ``Studio._lock`` only for the 409 rules and write the result files under the
    scan's own lock: starting or stopping a scan, the trigger and connect never wait for them. What
    comes meanwhile still sees the undo whole: a second undo, an edit, a project delete."""
    import threading

    from ohs.server.processing import ProcessManager

    pid, sid = add_scan(env, "Undo lock", env["rec"])
    b = base_url(env, pid, sid)
    run_job(env, post(b + "/process/track", {"quality": "preview"}), "process_track")
    n0 = get(b + "/process").json()["current"]["stats"]["triangles"]
    run_job(env, post(b + "/process/edits", {"op": "delete_faces", "params": {"faces": faces_b64(range(10))}}),
            "mesh_edit")
    run_job(env, post(b + "/process/edits", {"op": "keep_faces", "params": {"faces": faces_b64(range(100))}}),
            "mesh_edit")
    v = get(b + "/process").json()["current"]["version"]
    real = ProcessManager._write_result
    gate = {"held": threading.Event(), "go": threading.Event()}
    gate["go"].set()

    def slow(self, *a, **kw):  # the result files of a large mesh: written when the test lets go
        g = gate.copy()
        if not g["go"].is_set():
            g["held"].set()
            g["go"].wait(30)
        return real(self, *a, **kw)

    def in_thread(fn):
        out: dict = {}
        t = threading.Thread(target=lambda: out.update(r=fn()), daemon=True)
        t.start()
        return t, out

    def undo_held(then):
        """An undo that is writing its files when ``then()`` sends another request (in a thread);
        returns both answers."""
        gate.update(held=threading.Event(), go=threading.Event())
        t1, u = in_thread(lambda: post(b + "/process/undo"))
        try:
            assert gate["held"].wait(30)
            t2, o = then()
            time.sleep(0.3)  # the other request arrives while the undo writes
        finally:
            gate["go"].set()
            t1.join(30)
        t2.join(30)
        return u["r"], o["r"]

    monkeypatch.setattr(ProcessManager, "_write_result", slow)
    lock = env["studio"]._lock

    def lock_free_then_undo():
        free = lock.acquire(timeout=2.0)
        if free:
            lock.release()
        assert free, "the undo holds Studio._lock while it writes the result files"
        return in_thread(lambda: post(b + "/process/undo"))

    # 1. the main lock is free while an undo writes; a second undo at once waits for the first
    u1, u2 = undo_held(lock_free_then_undo)
    assert u1.status_code == 200 and u2.status_code == 200, (u1.text, u2.text)
    s = get(b + "/process").json()
    assert s["cursor"] == 0 and s["current"]["version"] == v + 2 and s["current"]["stats"]["triangles"] == n0
    assert mesh_faces(env, s["current"]["mesh_url"]) == n0
    # 2. an edit sent while an undo writes is made from the undone step (n0 - 10 triangles, not 100)
    for _ in range(2):
        assert post(b + "/process/redo").status_code == 200
    u, e = undo_held(lambda: in_thread(lambda: post(b + "/process/edits", {"op": "decimate", "params": {"ratio": 0.5}})))
    assert u.status_code == 200, u.text
    run_job(env, e, "mesh_edit")
    s = get(b + "/process").json()
    assert [x["op"] for x in s["edits"]] == ["delete_faces", "decimate"] and s["cursor"] == 2
    assert abs(s["edits"][1]["triangles_after"] - (n0 - 10) / 2) <= max(10, 0.01 * n0)
    # 3. a project deleted while an undo writes moves to the trash after it (no folder written again)
    u, d = undo_held(lambda: in_thread(lambda: requests.delete(f"{env['url']}/api/projects/{pid}", timeout=T)))
    assert u.status_code == 200 and d.status_code == 204, (u.text, d.text)
    assert not (env["root"] / "projects" / pid).exists()


def test_selections_and_their_checks(env):
    b = base_url(env)
    s = get(b + "/process").json()
    n = s["current"]["stats"]["triangles"]
    ver = s["current"]["version"]
    sel = list(range(0, n, 3))
    j = run_job(env, post(b + "/process/edits", {"op": "delete_faces", "params": {"faces": faces_b64(sel)}, "version": ver}),
                "mesh_edit")
    e = j["result"]["edit"]
    assert e["label"] == f"Delete {len(sel):,} triangles" and e["params"] == {"count": len(sel)}
    assert e["triangles_after"] == n - len(sel)
    s = get(b + "/process").json()
    m = s["current"]["stats"]["triangles"]
    j = run_job(env, post(b + "/process/edits", {"op": "keep_faces", "params": {"faces": faces_b64([5, 1, 1, 7])}}),
                "mesh_edit")
    assert j["result"]["edit"]["triangles_after"] == 3 and j["result"]["edit"]["label"] == "Keep 3 triangles"
    post(b + "/process/undo")
    sel_dir = env["root"] / "projects" / env["pid"] / "scans" / env["sid"] / "process" / "selections"
    assert len(list(sel_dir.glob("*.npy"))) == 2  # kept for replays until the edit is dropped
    err(post(b + "/process/edits", {"op": "delete_faces", "params": {"faces": faces_b64([m])}}), 422, "has triangle")
    err(post(b + "/process/edits", {"op": "delete_faces", "params": {"faces": "not base64!"}}), 422, "base64")
    err(post(b + "/process/edits", {"op": "delete_faces", "params": {"faces": base64.b64encode(b"abc").decode()}}),
        422, "bytes")
    err(post(b + "/process/edits", {"op": "keep_faces", "params": {}}), 422, "missing")
    err(post(b + "/process/edits", {"op": "delete_faces", "params": {"faces": faces_b64([0])}, "version": 1}),
        409, "changed")
    err(post(b + "/process/edits", {"op": "fill_holes", "params": {}}), 422, "max_hole_mm")
    err(post(b + "/process/edits", {"op": "smooth", "params": {"method": "gauss", "strength": 0.5}}), 422, "method")
    err(post(b + "/process/edits", {"op": "decimate", "params": {"ratio": 0.5, "target_triangles": 100}}), 422)
    err(post(b + "/process/edits", {"op": "remove_floaters", "params": {"size": 5}}), 422, "unknown parameter")
    # deleting everything is refused and leaves the mesh as it was
    r = post(b + "/process/edits", {"op": "delete_faces", "params": {"faces": faces_b64(range(m))}})
    failed = wait_job(env, r.json()["id"])
    assert failed["state"] == "failed" and "whole mesh" in failed["error"]
    after = get(b + "/process").json()
    assert after["current"]["version"] == s["current"]["version"] + 2 and after["cursor"] == s["cursor"]


def test_overlays(env):
    b = base_url(env)
    while get(b + "/process").json()["cursor"] > 0:
        post(b + "/process/undo")
    holes = get(b + "/process/holes.json").json()
    assert isinstance(holes, list)
    for h in holes:
        assert h["perimeter_mm"] > 0 and len(h["points"]) == 3 * (h["n_edges"] + 1)
    assert [h["perimeter_mm"] for h in holes] == sorted((h["perimeter_mm"] for h in holes), reverse=True)
    st = get(b + "/process").json()["current"]["stats"]
    assert len(holes) == st["holes"]
    tj = get(b + "/process/trajectory.json").json()
    tracked = get(b + "/process").json()["stages"]["track"]["tracked_pct"]
    assert len(tj["raw"]) == round(10 * tracked / 100) and all(len(p) == 3 for p in tj["raw"])
    assert tj["straightened"] is None and tj["loops"] == []


def test_fit_a_plane_on_the_table(env, tmp_path):
    b = base_url(env)
    cur = get(b + "/process").json()["current"]
    ply = tmp_path / "mesh.ply"
    ply.write_bytes(get(env["url"] + cur["mesh_url"]).content)
    m = load_ply(ply)
    V, F = m["vertices"], m["faces"]
    C = V[F].mean(axis=1)
    far = np.flatnonzero(C[:, 2] >= np.percentile(C[:, 2], 90))  # the table, behind the sphere
    face = int(far[np.argmin(np.linalg.norm(C[far] - C[far].mean(axis=0), axis=1))])
    click = {"face": face, "point": [float(x) for x in C[face]], "version": cur["version"]}
    r = post(b + "/fit", {"kind": "plane", **click})
    assert r.status_code == 200, r.text
    fit = r.json()
    shape = fit["shape"]
    assert fit["kind"] == "plane" and shape["kind"] == "plane" and fit["version"] == cur["version"]
    faces = np.frombuffer(base64.b64decode(fit["faces"]), dtype="<u4")
    assert len(faces) == fit["n_faces"] > 100 and face in faces and np.all(np.diff(faces.astype(np.int64)) > 0)
    # this short, coarse scan is noisy (face normals about 10° off the table, 0.4 mm scatter): the
    # check is that the fit agrees with its own region, not precision (that is step 5's)
    Nf = np.cross(V[F[faces, 1]] - V[F[faces, 0]], V[F[faces, 2]] - V[F[faces, 0]])
    mean_n = Nf.sum(axis=0) / np.linalg.norm(Nf.sum(axis=0))
    assert abs(float(np.dot(mean_n, shape["normal"]))) > np.cos(np.radians(10))
    assert shape["rms_mm"] < 1.0 and shape["flatness_mm"] >= shape["rms_mm"] and shape["normal_sigma_deg"] > 0
    # the tolerance follows the scatter (its sigma, when there is one, claims ~3 mm)
    assert 0 < fit["tolerance_mm"] < 3.0 and isinstance(fit["warnings"], list)
    assert fit["mean_sigma_mm"] is None or fit["mean_sigma_mm"] > 0
    # clear answers for what does not fit
    err(post(b + "/fit", {"kind": "cone", **click}), 422, "unknown kind")
    err(post(b + "/fit", {**click, "kind": "plane", "face": len(F) + 5}), 422, "not on the mesh")
    err(post(b + "/fit", {**click, "kind": "plane", "version": cur["version"] - 1}), 409, "changed")
    # a shape the scene does not have: there is no hole in a table with a sphere on it. (A cylinder
    # was asked for here before; on this short, noisy scan the engine's answer to that depends on the
    # noise: MAINTENANCE_PLAN.md, F1.)
    err(post(b + "/fit", {"kind": "hole", **click}), 422)
    assert post(b + "/fit", {"kind": "plane", **{**click, "point": [0, 0]}}).status_code == 422


def test_the_deviation_map_follows_the_edits(env, tmp_path):
    b = base_url(env)
    s = get(b + "/process").json()
    assert s["cursor"] == 0 and s["can_redo"]  # as test_overlays left it

    def both():
        info = get(b + "/process/deviation.json").json()
        r = get(b + "/process/deviation.bin")
        assert r.status_code == 200 and r.headers["x-ohs-mesh-version"] == str(info["version"])
        return info, np.frombuffer(r.content, dtype="<f4")

    info, off = both()  # no edits: the mesh is its own reference
    assert info["edits"] == 0 and info["version"] == s["current"]["version"] and info["reference"]["stage"] == "track"
    assert len(off) == info["vertices"] == s["current"]["stats"]["vertices"] and not off.any()
    assert info["stats"]["max_mm"] == 0.0 and info["stats"]["new"] == 0 and info["stats"]["moved"] == 0.0
    for _ in range(3):  # remove floaters, fill holes, smooth
        s = post(b + "/process/redo").json()
    info, off = both()
    assert info["edits"] == 3 and info["version"] == s["current"]["version"]
    assert info["reference"]["triangles"] == s["base"]["triangles"]
    ply = tmp_path / "mesh.ply"
    ply.write_bytes(get(env["url"] + s["current"]["mesh_url"]).content)
    m = load_ply(ply)
    assert len(off) == len(m["vertices"]) == info["vertices"]  # one value per vertex of mesh.ply
    st = info["stats"]
    assert st["new"] == int(np.isnan(off).sum()) and st["n"] == len(off) - st["new"]
    filled = m.get("filled")
    if filled is not None and np.any(filled):  # the new vertices are those no measured face uses
        measured = np.zeros(len(off), dtype=bool)
        measured[m["faces"][~filled.astype(bool)]] = True
        assert np.array_equal(np.isnan(off), ~measured)
    # smoothing moved the surface a little, both ways, and nowhere far
    assert 0 < st["p50_mm"] <= st["p95_mm"] <= st["max_mm"] < 10.0 and st["moved"] > 0.1
    assert np.nanmin(off) < 0 < np.nanmax(off) and st["max_mm"] == pytest.approx(float(np.nanmax(np.abs(off))), abs=1e-3)
    assert get(b + "/process/deviation.json").json() == info  # kept for this version
    for _ in range(3):
        s = post(b + "/process/undo").json()
    assert s["cursor"] == 0 and not both()[1].any()


def test_check_lists_the_areas_that_miss_a_tolerance(env):
    b = base_url(env)
    s = get(b + "/process").json()
    n_faces = s["current"]["stats"]["triangles"]
    loose, tight = (post(b + "/check", {"tolerance_mm": t}) for t in (20.0, 0.05))
    assert loose.status_code == 200 and tight.status_code == 200, loose.text + tight.text
    loose, tight = loose.json(), tight.json()
    for r, tol in ((loose, 20.0), (tight, 0.05)):
        assert r["version"] == s["current"]["version"] and r["tolerance_mm"] == tol and r["k"] == 2.0
        assert r["area_mm2"] > 0 and r["min_area_mm2"] >= 4.0 and r["n_areas"] >= len(r["areas"]) <= 50
        sizes = [a["area_mm2"] for a in r["areas"]]
        assert sizes == sorted(sizes, reverse=True)
        for a in r["areas"]:
            faces = np.frombuffer(base64.b64decode(a["faces"]), dtype="<u4")
            assert len(faces) == a["n_faces"] > 0 and int(faces.max()) < n_faces and np.all(np.diff(faces.astype(np.int64)) > 0)
            assert a["kind"] in ("uncertain", "not_measured") and len(a["center"]) == 3
            assert a["area_mm2"] >= r["min_area_mm2"] and a["radius_mm"] > 0 and 0 < a["share"] <= 1
    if tight["has_sigma"]:  # the confidence reconstruction (a GPU): a tighter tolerance passes less
        assert tight["share_ok"] < loose["share_ok"] and tight["areas"] and tight["areas"][0]["kind"] == "uncertain"
        for r in (loose, tight):
            assert r["share_ok"] + r["share_uncertain"] + r["share_not_measured"] == pytest.approx(1.0, abs=1e-6)
    else:  # Open3D's mesh has no sigma: nothing to check a tolerance against
        assert tight["share_ok"] is None and tight["share_uncertain"] is None and tight["areas"] == loose["areas"]
    err(post(b + "/check", {"tolerance_mm": 0}), 422)
    err(post(b + "/check", {}), 422)


def test_recipes_crud(env):
    url = env["url"] + "/api/recipes"
    rs = get(url).json()
    assert [r["id"] for r in rs[:4]] == ["default", "body-panel", "bracket", "quick-preview"]
    assert all(r["builtin"] and r["name"] and r["description"] for r in rs[:4])
    default = rs[0]
    assert default["settings"] == {"quality": "standard", "straighten": "auto", "refine": "auto", "voxel_mm": None}
    assert [o["op"] for o in default["ops"]] == ["remove_floaters", "fill_holes"]  # no smoothing, no simplification
    assert default["ops"][1]["params"]["max_hole_mm"] == 5.0
    assert rs[3]["settings"]["quality"] == "preview" and rs[3]["ops"][-1] == {"op": "decimate",
                                                                              "params": {"target_triangles": 200000}}
    body = {"name": "Coarse look", "description": "fast", "settings": {"quality": "preview", "straighten": "off"},
            "ops": [{"op": "remove_floaters", "params": {"keep_largest_only": True}},
                    {"op": "decimate", "params": {"ratio": 0.5}}]}
    r = post(url, body)
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["id"].startswith("coarse-look-") and rec["builtin"] is False and rec["name"] == "Coarse look"
    assert rec["settings"] == {"quality": "preview", "straighten": "off", "refine": "auto", "voxel_mm": None}
    assert rec["ops"][0] == {"op": "remove_floaters", "params": {"keep_largest_only": True}}
    assert (env["root"] / "data" / "recipes" / f"{rec['id']}.json").is_file()
    assert [x["id"] for x in get(url).json()][4:] == [rec["id"]]
    # from a scan: its active edits, without the selections
    r2 = post(url, {"name": "From the sphere", "from_scan": {"project_id": env["pid"], "scan_id": env["sid"]}})
    assert r2.status_code == 200, r2.text
    fs = r2.json()
    assert fs["settings"]["quality"] == "preview" and fs["settings"]["straighten"] == "off"
    assert fs["ops"] == []  # the sphere's edits are all undone (cursor 0)
    err(post(url, {"name": "Bad", "settings": {}, "ops": [{"op": "delete_faces", "params": {}}]}), 422)
    err(post(url, {"name": "Bad", "settings": {}, "ops": [{"op": "fill_holes", "params": {}}]}), 422, "max_hole_mm")
    err(post(url, {"name": "Bad"}), 422, "from_scan")
    err(post(url, {"name": "   ", "settings": {}, "ops": []}), 400, "empty")
    err(requests.delete(f"{url}/default", timeout=T), 409, "built-in")
    assert requests.delete(f"{url}/{fs['id']}", timeout=T).status_code == 204
    err(requests.delete(f"{url}/{fs['id']}", timeout=T), 404, "no recipe")
    env["user_recipe"] = rec["id"]


def test_recipes_apply_edits_and_all(env):
    b = base_url(env)
    before = get(b + "/process").json()
    j = run_job(env, post(b + "/process/recipe", {"recipe_id": "bracket", "scope": "edits"}), "process_recipe")
    assert j["result"]["message"].startswith("Bracket / mechanical applied")
    s = get(b + "/process").json()
    assert [e["op"] for e in s["edits"]] == ["remove_floaters", "fill_holes", "smooth"] and s["cursor"] == 3
    assert s["edits"][2]["params"] == {"method": "bilateral", "strength": 0.25, "keep_edges": True, "within_sigma": True}
    assert s["base"] == before["base"] and s["stages"]["track"] == before["stages"]["track"]
    # all: the stages with the recipe's settings, then its ops
    rid = env["user_recipe"]
    j = run_job(env, post(b + "/process/recipe", {"recipe_id": rid, "scope": "all"}), "process_recipe")
    s = get(b + "/process").json()
    assert s["stages"]["track"]["state"] == "done" and s["stages"]["track"]["job_id"] == j["id"]
    assert s["stages"]["straighten"]["state"] == "none"  # off in this recipe
    assert [e["op"] for e in s["edits"]] == ["remove_floaters", "decimate"] and s["cursor"] == 2
    assert s["current"]["stats"]["components"] == 1


def test_fuse_and_straighten_stages(env):
    b = base_url(env)
    before = get(b + "/process").json()
    j = run_job(env, post(b + "/process/fuse", {"voxel_mm": 3.0}), "process_fuse")
    s = get(b + "/process").json()
    fu = s["stages"]["fuse"]
    assert fu["state"] == "done" and fu["voxel_mm"] == 3.0 and fu["job_id"] == j["id"] and "3 mm" in fu["summary"]
    assert s["base"]["stage"] == "fuse" and s["edits"] == [] and s["cursor"] == 0  # a new base clears the edits
    assert s["base"]["triangles"] < before["base"]["triangles"]  # coarser voxels than the 2 mm of preview
    assert s["current"]["version"] > before["current"]["version"]
    # straighten on a short scan with no revisit: skipped, nothing changes
    j = run_job(env, post(b + "/process/straighten", {}), "process_straighten")
    s2 = get(b + "/process").json()
    stt = s2["stages"]["straighten"]
    assert stt["state"] == "skipped" and stt["summary"] and j["result"]["message"] == stt["summary"]
    assert s2["base"] == s["base"] and s2["current"]["version"] == s["current"]["version"]
    assert s2["stages"]["fuse"]["state"] == "done"


def test_cancel_a_track_and_409_while_recording(env):
    b = base_url(env)
    before = get(b + "/process").json()
    job = post(b + "/process/track", {"quality": "preview"}).json()
    err(post(b + "/process/undo"), 409, "busy")
    err(post(b + "/process/edits", {"op": "fill_holes", "params": {"max_hole_mm": 5}}), 409, "busy")
    st = get(b + "/process").json()
    assert st["job"] is not None and st["job"]["id"] == job["id"] and st["busy"] == "Tracking the scan…"
    requests.post(f"{env['url']}/api/jobs/{job['id']}/cancel", timeout=T)
    assert wait_job(env, job["id"])["state"] == "cancelled"
    after = get(b + "/process").json()
    assert after["stages"]["track"] == before["stages"]["track"]  # back to what it was
    assert after["base"] == before["base"] and after["current"]["version"] == before["current"]["version"]
    assert get(f"{env['url']}/api/projects/{env['pid']}").json()["scans"][0]["status"] == "processed"

    class Recording:  # stands in for a running ScanSession of this scan
        active = True
        state = "scanning"
        project_id, scan_id = env["pid"], env["sid"]

        def state_dict(self):
            return {"state": "scanning", "project_id": self.project_id, "scan_id": self.scan_id,
                    "mode": "markerless", "elapsed_s": 1.0, "frames": 1}

        def live_stats(self):
            return {"tracking": {"state": "ok", "reason": None, "code": None, "fitness": None, "rmse_mm": None},
                    "model": {"vertices": 0, "triangles": 0}}

    studio = env["studio"]
    studio.session = Recording()
    try:
        err(post(b + "/process/track", {"quality": "preview"}), 409, "recording")
        err(post(b + "/process/edits", {"op": "fill_holes", "params": {"max_hole_mm": 5}}), 409, "recording")
        err(post(b + "/process/undo"), 409, "recording")
        err(post(b + "/process", {"quality": "preview"}), 409, "recording")
    finally:
        studio.session = None


def test_a_stage_job_cancelled_while_queued_settles_the_scan(env):
    import threading

    pid, sid = add_scan(env, "Cancelled in the queue", env["rec"])
    jobs = env["studio"].jobs
    release = threading.Event()
    started = [threading.Event(), threading.Event()]
    blockers = [jobs.submit("test_block", lambda ctx, e=e: e.set() or release.wait(30) and {}) for e in started]
    try:
        assert all(e.wait(10) for e in started)  # both workers are busy
        job = post(base_url(env, pid, sid) + "/process/track", {"quality": "preview"}).json()
        assert job["state"] == "queued"
        assert get(f"{env['url']}/api/projects/{pid}").json()["scans"][0]["status"] == "processing"
        requests.post(f"{env['url']}/api/jobs/{job['id']}/cancel", timeout=T)
        assert wait_job(env, job["id"], 10)["state"] == "cancelled"
        # settled by the cancel itself, while both workers are still busy
        assert get(f"{env['url']}/api/projects/{pid}").json()["scans"][0]["status"] == "recorded"
    finally:
        release.set()
        for b in blockers:
            jobs.wait(b.id, 10)
    assert get(f"{env['url']}/api/projects/{pid}").json()["scans"][0]["status"] == "recorded"


def test_make_mesh_runs_the_default_recipe(env):
    pid, sid = add_scan(env, "Make mesh", env["rec"])
    b = base_url(env, pid, sid)
    r = post(b + "/process", {"quality": "preview"})
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["kind"] == "process_scan" and job["quality"] == "preview"
    err(post(b + "/process", {"quality": "preview"}), 409, "already")
    assert get(f"{env['url']}/api/projects/{pid}").json()["scans"][0]["status"] == "processing"
    done = wait_job(env, job["id"])
    assert done["state"] == "done" and done["result"]["message"] == "Mesh ready", done
    scan = done["result"]["scan"]
    assert scan["status"] == "processed" and scan["quality"] == "preview"
    assert scan["result"]["report"]["quality"] == "preview" and scan["result"]["laser_points_url"]
    s = get(b + "/process").json()
    assert s["stages"]["track"]["state"] == "done" and s["stages"]["track"]["quality"] == "preview"
    assert s["stages"]["straighten"]["state"] in ("skipped", "done")  # auto
    assert [e["op"] for e in s["edits"]] == ["remove_floaters", "fill_holes"] and s["cursor"] == 2
    assert s["edits"][1]["params"] == {"max_hole_mm": 5.0}
    assert mesh_faces(env, scan["result"]["mesh_url"]) == s["current"]["stats"]["triangles"]
    rep = get(f"{b}/files/report.json").json()
    assert rep["quality"] == "preview" and rep["frames"]["processed"] == 10
    assert get(f"{b}/files/mesh.stl").status_code == 200 and get(f"{b}/files/trajectory.csv").status_code == 200
    scan_dir = env["root"] / "projects" / pid / "scans" / sid
    assert not list(scan_dir.glob("result.tmp-*")) and not list((scan_dir / "process").glob("*.tmp-*"))


def test_refine_with_lasers(env):
    from ohs.mesh import vertex_normals
    from ohs.track.export import save_mesh

    pid, sid = add_scan(env, "Refine", env["rec"])
    b = base_url(env, pid, sid)
    err(post(b + "/process/refine"), 409, "track")
    run_job(env, post(b + "/process/track", {"quality": "preview"}), "process_track")
    s = get(b + "/process").json()
    assert s["stages"]["refine"]["state"] == "none" and s["base"]["stage"] == "track"
    # a dense surface 0.5 mm off its laser lines (as a depth bias would leave it) is pulled back onto them
    track = env["root"] / "projects" / pid / "scans" / sid / "process" / "track"
    d = load_ply(track / "mesh.ply")
    V, F = d["vertices"], d["faces"]
    save_mesh(track / "mesh.ply", V + 0.5 * vertex_normals(V, F), F, quality=d["quality"])
    j = run_job(env, post(b + "/process/refine"), "process_refine")
    s = get(b + "/process").json()
    rf = s["stages"]["refine"]
    assert rf["state"] == "done" and rf["source"] == "track" and j["result"]["message"] == rf["summary"]
    assert rf["summary"].startswith("Pulled") and rf["points"] > 1000 and rf["moved_pct"] > 1
    assert rf["residual_before_mm"] > 0.35 and rf["residual_after_mm"] < 0.6 * rf["residual_before_mm"]
    assert rf["moved_mm"] > 0.3
    assert s["base"]["stage"] == "refine" and s["edits"] == [] and s["current"]["stats"]["triangles"] == len(F)
    res = env["root"] / "projects" / pid / "scans" / sid / "result"
    ref = json.loads((res / "report.json").read_text(encoding="utf-8"))["refine"]
    # the injected offset is gone on average (the coarse preview surface also gets some real corrections)
    assert abs(ref["residual_before"]["mean"] + 0.5) < 0.15 and abs(ref["residual_after"]["mean"]) < 0.1
    assert (load_ply(res / "mesh.ply")["quality"] is None) == (d["quality"] is None)  # the sigmas stay
    # again: from the same source mesh, not from its own result
    run_job(env, post(b + "/process/refine"), "process_refine")
    rf2 = get(b + "/process").json()["stages"]["refine"]
    assert rf2["state"] == "done" and rf2["source"] == "track" and abs(rf2["residual_before_mm"] - rf["residual_before_mm"]) < 1e-6
    # a new fuse makes a new base: refine starts over
    run_job(env, post(b + "/process/fuse", {"voxel_mm": 2.0}), "process_fuse")
    s = get(b + "/process").json()
    assert s["stages"]["refine"]["state"] == "none" and s["base"]["stage"] == "fuse"
    # a scan recorded without the lasers: nothing to refine with
    rec = make_scan_recording(env["root"] / "rec-nolines", n_frames=6, image_size=(640, 400), seed=5, depth_every=0,
                              lines=False)
    pid2, sid2 = add_scan(env, "No lasers", rec)
    b2 = base_url(env, pid2, sid2)
    run_job(env, post(b2 + "/process/track", {"quality": "preview"}), "process_track")
    run_job(env, post(b2 + "/process/refine"), "process_refine")
    rf = get(b2 + "/process").json()["stages"]["refine"]
    assert rf["state"] == "skipped" and "no laser points" in rf["summary"]


def test_refine_from_the_images(env, monkeypatch):
    import ohs.track.spacetime as stm

    pid, sid = add_scan(env, "Spacetime", env["rec"])
    b = base_url(env, pid, sid)
    err(post(b + "/process/spacetime"), 409, "track")
    run_job(env, post(b + "/process/track", {"quality": "full"}), "process_track")
    # the 10 frames are fewer than a patch needs by default: nothing changes
    j = run_job(env, post(b + "/process/spacetime"), "process_spacetime")
    s = get(b + "/process").json()
    stt = s["stages"]["spacetime"]
    assert stt["state"] == "skipped" and stt["source"] == "track" and stt["frames"] == 10 and stt["kept"] == 0
    assert "left as it is" in stt["summary"] and j["result"]["message"] == stt["summary"]
    assert s["base"]["stage"] == "track"
    # with fewer views required, the surface is measured against the frames
    defaults = stm.SpacetimeParams
    light = {"half_px": 4.0, "levels": ((0.4, 0.1), (0.1, 0.025))}  # this checks the stage, not the search: quick on a CPU
    monkeypatch.setattr(stm, "SpacetimeParams", lambda: dataclasses.replace(defaults(), min_views=6, **light))
    run_job(env, post(b + "/process/edits", {"op": "remove_floaters", "params": {"min_size_mm": 5}}), "mesh_edit")
    j = run_job(env, post(b + "/process/spacetime"), "process_spacetime")
    s = get(b + "/process").json()
    stt = s["stages"]["spacetime"]
    assert stt["state"] == "done" and stt["source"] == "track" and stt["kept"] >= 50, stt
    assert stt["summary"].startswith("Measured") and stt["covered_pct"] > 1 and stt["device"] in ("gpu", "cpu")
    assert s["base"]["stage"] == "spacetime" and s["edits"] == []  # a new base: the edits start over
    res = env["root"] / "projects" / pid / "scans" / sid / "result"
    rep = json.loads((res / "report.json").read_text(encoding="utf-8"))["spacetime"]
    assert rep["source"] == "track" and rep["kept"] == stt["kept"] and "patch_results" not in rep
    track_q = load_ply(env["root"] / "projects" / pid / "scans" / sid / "process" / "track" / "mesh.ply")["quality"]
    assert (load_ply(res / "mesh.ply")["quality"] is None) == (track_q is None)  # each vertex keeps its sigma
    # the lasers refine the new surface; spacetime again starts from the mesh it refined
    run_job(env, post(b + "/process/refine"), "process_refine")
    assert get(b + "/process").json()["stages"]["refine"]["source"] == "spacetime"
    run_job(env, post(b + "/process/spacetime"), "process_spacetime")
    s = get(b + "/process").json()
    assert s["stages"]["spacetime"]["source"] == "track" and s["stages"]["refine"]["state"] == "none"
    # a new fuse makes a new base: both refinements start over
    run_job(env, post(b + "/process/fuse", {"voxel_mm": 2.0}), "process_fuse")
    s = get(b + "/process").json()
    assert s["stages"]["spacetime"]["state"] == "none" and s["base"]["stage"] == "fuse"


def test_measurement_safe_edits(env):
    pid, sid = add_scan(env, "Safe edits", env["rec"])
    b = base_url(env, pid, sid)
    run_job(env, post(b + "/process/track", {"quality": "preview"}), "process_track")
    err(post(b + "/process/edits", {"op": "decimate", "params": {}}), 422, "max_error_mm")
    err(post(b + "/process/edits", {"op": "smooth", "params": {"method": "taubin", "strength": 0.3,
                                                                "within_sigma": "yes"}}), 422, "within_sigma")
    err(post(b + "/process/edits", {"op": "fill_holes", "params": {"max_hole_mm": 5, "watertight": 1}}), 422,
        "watertight")
    s0 = get(b + "/process").json()["current"]["stats"]
    assert s0["filled_cm2"] == 0.0
    # filled faces are flagged in the PLY and counted
    j = run_job(env, post(b + "/process/edits", {"op": "fill_holes", "params": {"max_hole_mm": 60}}), "mesh_edit")
    s = get(b + "/process").json()
    p = env["root"] / f"dl-{time.monotonic_ns()}.ply"
    p.write_bytes(get(env["url"] + s["current"]["mesh_url"]).content)
    d = load_ply(p)
    new = s["current"]["stats"]["triangles"] - s0["triangles"]
    assert new > 0 and d["filled"] is not None and int(d["filled"].sum()) == new
    assert s["current"]["stats"]["filled_cm2"] > 0
    # the flags follow the next edits; undo brings back the step's own
    run_job(env, post(b + "/process/edits", {"op": "smooth", "params": {"method": "bilateral", "strength": 0.3,
                                                                          "within_sigma": True}}), "mesh_edit")
    s = get(b + "/process").json()
    assert s["edits"][-1]["label"].endswith("within 2σ)") and s["current"]["stats"]["filled_cm2"] > 0
    j = run_job(env, post(b + "/process/edits", {"op": "decimate", "params": {"max_error_mm": 0.05}}), "mesh_edit")
    s = get(b + "/process").json()
    e = s["edits"][-1]
    assert e["label"] == "Simplify to within 0.05 mm" and "moved at most" in e["summary"]
    assert e["triangles_after"] < s["edits"][-2]["triangles_after"] and s["current"]["stats"]["filled_cm2"] > 0
    u = post(b + "/process/undo").json()
    u = post(b + "/process/undo").json()
    u = post(b + "/process/undo").json()
    assert u["cursor"] == 0 and u["current"]["stats"]["filled_cm2"] == 0.0
    p.write_bytes(get(env["url"] + u["current"]["mesh_url"]).content)
    assert load_ply(p)["filled"] is None
    # watertight: an open scan's outer edge is far over any reasonable limit, and the summary says so
    j = run_job(env, post(b + "/process/edits", {"op": "fill_holes", "params": {"max_hole_mm": 60, "watertight": True}}),
                "mesh_edit")
    e = get(b + "/process").json()["edits"][-1]
    assert e["label"] == "Make watertight: close every opening up to 60 mm" and "not watertight yet" in e["summary"]


def test_quality_levels_and_the_old_name(env):
    """standard / best / preview, and "full" (the old name) read as standard: requests, stored state, recipes."""
    pid, sid = add_scan(env, "Levels", env["rec"])
    b = base_url(env, pid, sid)
    err(post(b + "/process/track", {"quality": "ultra"}), 422)
    j = run_job(env, post(b + "/process/track", {"quality": "best"}), "process_track")
    tr = get(b + "/process").json()["stages"]["track"]
    assert tr["quality"] == "best" and tr["summary"].endswith("at best quality") and j["quality"] == "best"
    j = run_job(env, post(b + "/process/track", {"quality": "full"}), "process_track")
    tr = get(b + "/process").json()["stages"]["track"]
    assert tr["quality"] == "standard" and tr["summary"].endswith("at standard quality") and j["quality"] == "standard"
    j = run_job(env, post(b + "/process/track"), "process_track")  # the default
    assert j["quality"] == "standard"
    scan_dir = env["root"] / "projects" / pid / "scans" / sid
    rep = json.loads((scan_dir / "process" / "track" / "report.json").read_text(encoding="utf-8"))
    assert rep["quality"] == "standard" and rep["frames"]["step"] == 1 and rep["frames"]["max_frame_rate"] == 30.0
    assert rep["frames"]["recorded_fps"] == pytest.approx(30.0, abs=0.5)  # a 30 fps recording: every frame
    # state written by an older engine says "full": it reads as "standard"
    state_path = scan_dir / "process" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["stages"]["track"]["quality"] = "full"
    state["stages"]["track"]["summary"] = state["stages"]["track"]["summary"].replace("standard quality", "full quality")
    assert state["stages"]["track"]["summary"].endswith(" at full quality")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    env["studio"].projects.update_scan(pid, sid, quality="full")
    env["studio"].processing.forget(pid)
    tr = get(b + "/process").json()["stages"]["track"]
    assert tr["quality"] == "standard" and tr["summary"].endswith(" at standard quality")
    scan = get(f"{env['url']}/api/projects/{pid}").json()["scans"][0]
    assert scan["quality"] == "standard"
    # Fuse after it runs at standard quality on the track's frames
    run_job(env, post(b + "/process/fuse", {"voxel_mm": 2.0}), "process_fuse")
    fused = json.loads((scan_dir / "process" / "fuse" / "report.json").read_text(encoding="utf-8"))
    assert fused["quality"] == "standard" and fused["frames"]["processed"] == rep["frames"]["tracked"]
    # a recipe saved with "full" lists as "standard"; new recipes can say best or full
    rdir = env["root"] / "data" / "recipes"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "old-full.json").write_text(json.dumps({"id": "old-full", "name": "Old full", "description": "",
                                                    "settings": {"quality": "full", "straighten": "auto",
                                                                 "voxel_mm": None}, "ops": []}), encoding="utf-8")
    old = [r for r in get(env["url"] + "/api/recipes").json() if r["id"] == "old-full"]
    assert old and old[0]["settings"]["quality"] == "standard"
    for q, want in (("best", "best"), ("full", "standard")):
        r = post(env["url"] + "/api/recipes", {"name": f"Level {q}", "settings": {"quality": q}, "ops": []})
        assert r.status_code == 200, r.text
        assert r.json()["settings"]["quality"] == want
    err(post(env["url"] + "/api/recipes", {"name": "Bad level", "settings": {"quality": "ultra"}, "ops": []}), 422)


def test_a_scan_processed_before_m3_is_taken_over(env):
    pid, sid = add_scan(env, "Old result", env["rec"])
    scan_dir = env["root"] / "projects" / pid / "scans" / sid
    report = process_recording(scan_dir / "recording", scan_dir / "result",
                               params=ProcessParams(pipeline=PipelineParams(scale=0.5), export=("ply", "stl")))
    env["studio"].projects.update_scan(pid, sid, status="processed", quality="preview")
    s = get(base_url(env, pid, sid) + "/process").json()
    assert s["stages"]["track"]["state"] == "done" and s["stages"]["track"]["quality"] == "preview"
    assert s["base"]["stage"] == "track" and s["base"]["triangles"] == report["mesh"]["n_faces"]
    assert s["current"]["version"] == 1 and (scan_dir / "process" / "track" / "trajectory.csv").is_file()
    run_job(env, post(base_url(env, pid, sid) + "/process/edits", {"op": "fill_holes", "params": {"max_hole_mm": 5}}),
            "mesh_edit")


# --------------------------------------------------------------------------- straighten with loops
ORBIT_FRAMES, ORBIT_FPS, ORBIT_DEG = 60, 5.0, 400.0


@pytest.fixture(scope="module")
def orbit(tmp_path_factory) -> Path:
    """A 400 deg hand-held orbit around the bracket (the last 40 deg pass over the start again)."""
    from ohs.sim.nominal import NOMINAL_TIME_OFFSET, nominal_calibration
    from ohs.sim.scene import make_scene
    from ohs.sim.trajectory import HandheldTrajectory

    calib = nominal_calibration((640, 400), realistic=True)
    sc = make_scene("bracket", 3)
    dur = ORBIT_FRAMES / ORBIT_FPS
    traj = HandheldTrajectory(target=sc.target, t0=100.0 + NOMINAL_TIME_OFFSET, duration=dur, seed=3,
                              baseline=calib.stereo.baseline, distance=(200.0, 280.0), elevation_deg=(35.0, 65.0),
                              sweep_deg_s=ORBIT_DEG / dur)
    return make_scan_recording(tmp_path_factory.mktemp("orbit") / "rec", scene="bracket", n_frames=ORBIT_FRAMES,
                               fps=ORBIT_FPS, image_size=(640, 400), realistic=True, trajectory=traj, depth_every=0,
                               seed=3)


def test_straighten_closes_loops_and_fuses_again(env, orbit):
    pytest.importorskip("ohs.track.posegraph")
    from ohs.app.process import _write_trajectory
    from ohs.geometry import invert_se3
    from ohs.sim.trajectory import add_drift

    pid, sid = add_scan(env, "Orbit", orbit)
    b = base_url(env, pid, sid)
    run_job(env, post(b + "/process/track", {"quality": "preview"}), "process_track")
    reader = RecordingReader(orbit)
    gt = reader.ground_truth_poses()
    idx = sorted(gt)
    Tt = np.stack([invert_se3(gt[idx[0]]) @ gt[i] for i in idx])
    times = reader.times()
    proc = env["root"] / "projects" / pid / "scans" / sid / "process"
    track_csv = proc / "track" / "trajectory.csv"
    run_job(env, post(b + "/process/edits", {"op": "fill_holes", "params": {"max_hole_mm": 10}}), "mesh_edit")
    # 1. the true poses: loops close, but the passes already line up, so nothing changes (no new fuse,
    # the edits stay) and the summary is the engine's verdict
    _write_trajectory(track_csv, [(i, float(times[k]), True, Tt[k]) for k, i in enumerate(idx)])
    before = get(b + "/process").json()
    j = run_job(env, post(b + "/process/straighten", {}), "process_straighten")
    s = get(b + "/process").json()
    stt = s["stages"]["straighten"]
    rep = json.loads((proc / "straighten.json").read_text(encoding="utf-8"))
    assert rep["changed"] is False and stt["state"] == "skipped" and stt["summary"] == rep["verdict"], stt
    assert stt["summary"].startswith("Closed") and "unchanged" in stt["summary"] and stt["loops"] >= 1
    assert j["result"]["message"] == stt["summary"]
    assert s["base"] == before["base"] and s["edits"] == before["edits"] and s["cursor"] == 1
    assert s["current"]["version"] == before["current"]["version"] and not (proc / "straighten").exists()
    assert get(b + "/process/trajectory.json").json()["straightened"] is None
    # 2. the truth plus a drift that loop closure can see: straightened and fused again
    drifted = add_drift(Tt, 0.1, 0.3, 2)
    _write_trajectory(track_csv, [(i, float(times[k]), True, drifted[k]) for k, i in enumerate(idx)])
    v0 = get(b + "/process").json()["current"]["version"]
    j = run_job(env, post(b + "/process/straighten", {}), "process_straighten")
    s = get(b + "/process").json()
    stt = s["stages"]["straighten"]
    assert stt["state"] == "done", stt
    assert stt["loops"] >= 1 and stt["correction_mm"] > 0.3 and stt["summary"].startswith("Closed")
    assert j["result"]["message"] == stt["summary"]
    assert s["base"]["stage"] == "straighten" and s["edits"] == [] and s["current"]["version"] > v0
    rep = json.loads((proc / "straighten.json").read_text(encoding="utf-8"))
    g = rep["ground_truth"]
    print("METRICS " + json.dumps({"test": "studio_straighten_orbit", "loops": stt["loops"],
                                   "correction_mm": round(stt["correction_mm"], 3), "summary": stt["summary"],
                                   "truth_rms_mm": [round(g["before"]["rms_trans_mm"], 3),
                                                    round(g["after"]["rms_trans_mm"], 3)]}))
    assert g["after"]["rms_trans_mm"] < g["before"]["rms_trans_mm"]
    # the fuse at the corrected poses used exactly those poses
    fused = {i: T for i, _, _, T in read_trajectory(proc / "straighten" / "trajectory.csv")}
    assert set(fused) == set(idx)
    tj = get(b + "/process/trajectory.json").json()
    assert len(tj["raw"]) == len(tj["straightened"]) == len(idx) and len(tj["loops"]) == stt["loops"]
    assert all(0 <= a < b2 < len(idx) for a, b2 in tj["loops"])
    moved = np.linalg.norm(np.asarray(tj["raw"]) - np.asarray(tj["straightened"]), axis=1)
    assert moved.max() == pytest.approx(stt["correction_mm"], abs=0.05)
    # fuse at 3 mm now uses the straightened poses
    run_job(env, post(b + "/process/fuse", {"voxel_mm": 3.0}), "process_fuse")
    fz = {i: T for i, _, _, T in read_trajectory(proc / "fuse" / "trajectory.csv")}
    assert all(np.allclose(fz[i], fused[i], atol=1e-6) for i in idx)
    with open(proc / "fuse" / "trajectory.csv", newline="", encoding="utf-8") as fh:
        assert sum(1 for _ in csv.DictReader(fh)) == len(idx)


# --------------------------------------------------------------------------- the job's frame counter (plan F4)
def test_make_mesh_reports_its_frames_and_rate(env):
    """While a frame pass runs, the job has ``frames`` (stage, done, total, fps, elapsed_s); after the
    last frame its message says what happens instead of a frame count; the finished job has none."""
    pid, sid = add_scan(env, "Frames", env["rec"])
    with ws_connect(env["srv"].ws_url, max_size=None, open_timeout=5) as ws:
        ws.send(json.dumps({"type": "subscribe", "preview": False, "mesh": False}))
        r = post(base_url(env, pid, sid) + "/process", {"quality": "preview"})
        assert r.status_code == 200, r.text
        jid = r.json()["id"]
        out = recv_until(ws, lambda m, p, raw: m.get("type") == "job" and m.get("id") == jid
                         and m["state"] not in ("queued", "running"), timeout=180)
    msgs = [m for m, _, _ in out if m.get("type") == "job" and m.get("id") == jid]
    final = msgs[-1]
    assert final["state"] == "done" and "frames" not in final, final
    with_frames = [m for m in msgs if m.get("frames")]
    assert with_frames, [m["message"] for m in msgs]
    n = json.loads((env["root"] / "projects" / pid / "scans" / sid / "result" / "report.json")
                   .read_text(encoding="utf-8"))["frames"]["processed"]
    for m in with_frames:
        f = m["frames"]
        assert set(f) == {"stage", "done", "total", "fps", "elapsed_s"}, f
        assert f["stage"] in ("track", "straighten") and 1 <= f["done"] <= f["total"] and f["elapsed_s"] >= 0
        assert f["fps"] is None or f["fps"] > 0
        if f["stage"] == "track":
            assert f["total"] == n
    track = [m for m in with_frames if m["frames"]["stage"] == "track"]
    dones = [m["frames"]["done"] for m in track]
    assert dones == sorted(dones) and dones[-1] == n  # the last frame always goes out (forced)
    last = track[-1]
    assert last["frames"]["fps"] > 0 and last["message"] == f"Tracking: all {n} frames done, making the mesh"
    # the pass is over: the next update has no frame counter
    after = msgs[msgs.index(last) + 1:]
    assert after and "frames" not in after[0] and get(f"{env['url']}/api/jobs/{jid}").json().get("frames") is None