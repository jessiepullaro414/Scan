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
    assert at_truth["frames"]["processed"] == len(half) and at_truth["frames"]["without_pose"] == len(poses) - len(half)
    assert at_truth["ground_truth"]["rms_trans_mm"] < 1e-3
    with pytest.raises(ValueError, match="none of the given poses"):
        process_recording(env["rec"], tmp_path / "none", params=params, poses={999: np.eye(4)})


# --------------------------------------------------------------------------- REST
def test_a_fresh_scan_and_the_409_rules(env):
    b = base_url(env)
    s = get(b + "/process").json()
    assert s["project_id"] == env["pid"] and s["scan_id"] == env["sid"]
    assert {k: v["state"] for k, v in s["stages"].items()} == {"track": "none", "straighten": "none", "fuse": "none",
                                                               "refine": "none"}
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


def test_recipes_crud(env):
    url = env["url"] + "/api/recipes"
    rs = get(url).json()
    assert [r["id"] for r in rs[:4]] == ["default", "body-panel", "bracket", "quick-preview"]
    assert all(r["builtin"] and r["name"] and r["description"] for r in rs[:4])
    default = rs[0]
    assert default["settings"] == {"quality": "full", "straighten": "auto", "refine": "auto", "voxel_mm": None}
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
    assert s["edits"][2]["params"] == {"method": "bilateral", "strength": 0.25, "keep_edges": True}
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
