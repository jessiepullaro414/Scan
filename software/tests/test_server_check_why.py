"""Why each area of the Check answer is weak and where to scan it from, over the API (FEATURE_PLAN N1),
and each vertex's count of looks through the server's edits.

The scans are made up and taken over as "a scan processed before M3" (a ``result/mesh.ply`` and no
processing state), so the test does not depend on the compute path: a plate of 1 mm triangles with a
weak patch (sigma 0.2 mm), its ``looks``, a hole and a loose speck; a camera path that saw the plate
only from 70 degrees off its normal; the nominal scanner's calibration in the recording; a report with
the depth range and the voxel size.

* ``POST .../check/why`` explains the areas ``POST .../check`` lists, in its order: the reason, the
  pose to scan it from, a plan of at most three poses; the plain answer is unchanged by it; kept per
  mesh version and tolerance; 409 for another version. A scan without its calibration, and a merge
  whose first scan lost its camera path, say so in words instead of guessing.
* ``looks`` follows every edit (removed vertices drop theirs, hole filling adds 0, smoothing and
  refining keep them), undo / redo, a step rebuilt from its edit, and a cache file written before
  this change (no looks) still loads.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

from mesh_shapes import grid, punch  # noqa: E402
from ohs.app.config import Config  # noqa: E402
from ohs.mesh import carry_looks  # noqa: E402
from ohs.mesh.topology import compact, vertex_normals  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402
from ohs.sim.nominal import nominal_calibration  # noqa: E402
from ohs.sim.trajectory import look_at  # noqa: E402
from ohs.track.export import load_ply, save_mesh  # noqa: E402
from ohs.track.posegraph import write_trajectory  # noqa: E402

T = 60  # HTTP timeout
CENTRE = np.array([50.0, 30.0, 0.0])
PATCH = (40.0, 60.0, 20.0, 40.0)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("why")
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        e = {"srv": srv, "url": srv.url, "root": root, "studio": app.state.studio}
        e["pid"] = requests.post(e["url"] + "/api/projects", json={"name": "Why"}, timeout=T).json()["id"]
        yield e


def plate(speck: bool = True):
    """A 100 x 60 mm plate (1 mm grid) with a round hole and a weak patch; a loose speck far off."""
    V, F = grid(101, 61, 1.0)
    V, F = punch(V, F, [(15.0, 15.0, 0.0)], [3.0])
    V, F, _ = compact(V, F)
    if speck:
        F = np.vstack([F, np.array([[0, 1, 2]]) + len(V)])
        V = np.vstack([V, [[200.0, 200.0, 0.0], [200.5, 200.0, 0.0], [200.0, 200.5, 0.0]]])
    S = np.full(len(V), 0.02, np.float32)
    m = (V[:, 0] >= PATCH[0]) & (V[:, 0] <= PATCH[1]) & (V[:, 1] >= PATCH[2]) & (V[:, 1] <= PATCH[3])
    S[m] = 0.2
    L = (3.0 + np.arange(len(V)) % 17).astype(np.float32)  # each vertex its own count, >= 3
    return V, F, S, L


def steep_path(n: int = 12, off_deg: float = 70.0, dist: float = 250.0) -> np.ndarray:
    out = []
    for a in np.radians(np.linspace(-20.0, 20.0, n)):
        th = np.radians(off_deg)
        d = np.array([np.sin(th) * np.cos(a), np.sin(th) * np.sin(a), np.cos(th)])
        out.append(look_at(CENTRE + dist * d, CENTRE, up=(0.0, 0.0, 1.0)))
    return np.stack(out)


def adopted_scan(e: dict, name: str, mesh=None, path: np.ndarray | None = None, calib: bool = True,
                 report: dict | None = None, looks: bool = True) -> str:
    """A scan with a result only (taken over on first use): its mesh, camera path, calibration, report."""
    st = e["studio"]
    scan, rec = st.projects.add_scan(e["pid"], name, "markerless")
    sid = scan["id"]
    V, F, S, L = mesh if mesh is not None else plate()
    res = st.projects.scan_dir(e["pid"], sid) / "result"
    res.mkdir(parents=True)
    rec.mkdir(parents=True, exist_ok=True)
    save_mesh(res / "mesh.ply", V, F, vertex_normals(V, F), quality=S, looks=L if looks else None)
    if path is not None:
        write_trajectory(res / "trajectory.csv", [(i, i / 30.0, True, Tw) for i, Tw in enumerate(path)])
    if calib:
        nominal_calibration((1280, 800)).save(rec / "calibration.json")
    rep = report if report is not None else {"calibration": "recording", "mesh": {"voxel_mm": 1.0},
                                             "params": {"depth": {"min_depth_mm": 150.0, "max_depth_mm": 450.0}}}
    (res / "report.json").write_text(json.dumps(rep), encoding="utf-8")
    st.projects.update_scan(e["pid"], sid, status="processed", n_frames=0 if path is None else len(path))
    return sid


def base_url(e: dict, sid: str) -> str:
    return f"{e['url']}/api/projects/{e['pid']}/scans/{sid}"


def post(url: str, body: dict | None = None):
    return requests.post(url, json=body, timeout=T)


def get(url: str):
    return requests.get(url, timeout=T)


def err(r, status: int, needle: str = "") -> dict:
    assert r.status_code == status, r.text
    body = r.json()
    assert body["detail"] and body.get("hint"), body
    assert needle.lower() in body["detail"].lower(), body
    return body


def wait_job(e: dict, jid: str, timeout: float = 180.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        j = get(f"{e['url']}/api/jobs/{jid}").json()
        if j["state"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {jid} did not finish within {timeout:.0f} s")


def edit(e: dict, sid: str, op: str, params: dict) -> dict:
    r = post(base_url(e, sid) + "/process/edits", {"op": op, "params": params})
    assert r.status_code == 200, r.text
    j = wait_job(e, r.json()["id"])
    assert j["state"] == "done", j
    return j


def served(e: dict, sid: str, tmp: Path) -> dict:
    """The current ``mesh.ply`` as the app downloads it."""
    s = get(base_url(e, sid) + "/process").json()
    r = get(e["url"] + s["current"]["mesh_url"] + f"?v={s['current']['version']}")
    assert r.status_code == 200
    p = tmp / f"m-{time.monotonic_ns()}.ply"
    p.write_bytes(r.content)
    return load_ply(p)


def faces_b64(idx) -> str:
    return base64.b64encode(np.asarray(idx, dtype="<u4").tobytes()).decode("ascii")


# --------------------------------------------------------------------------- looks through the edits
def test_the_looks_follow_every_edit_undo_redo_and_a_rebuilt_step(env, tmp_path):
    V, F, S, L = plate()
    sid = adopted_scan(env, "Looks", (V, F, S, L), steep_path())
    b = base_url(env, sid)
    m0 = served(env, sid, tmp_path)  # the base: the file as written
    assert m0["looks"] is not None and np.array_equal(m0["looks"], L.astype(np.float64))
    steps = [m0]
    # loose pieces: the speck's three vertices go, the rest keep theirs in order
    edit(env, sid, "remove_floaters", {"min_size_mm": 5})
    m = served(env, sid, tmp_path)
    assert len(m["vertices"]) == len(V) - 3 and np.array_equal(m["looks"], m0["looks"][:-3])
    steps.append(m)
    # hole filling: the old vertices keep theirs, the new ones have none (0)
    edit(env, sid, "fill_holes", {"max_hole_mm": 30})
    m = served(env, sid, tmp_path)
    n = len(steps[-1]["vertices"])
    assert len(m["vertices"]) > n and np.array_equal(m["looks"][:n], steps[-1]["looks"]) and np.all(m["looks"][n:] == 0)
    steps.append(m)
    # smoothing moves the vertices, each keeps its looks
    edit(env, sid, "smooth", {"method": "taubin", "strength": 0.5})
    m = served(env, sid, tmp_path)
    assert np.array_equal(m["looks"], steps[-1]["looks"]) and not np.array_equal(m["vertices"], steps[-1]["vertices"])
    steps.append(m)
    # a selection deleted: the vertices of the faces left keep theirs
    sel = np.arange(0, len(m["faces"]), 4)
    edit(env, sid, "delete_faces", {"faces": faces_b64(sel)})
    m = served(env, sid, tmp_path)
    prev = steps[-1]
    want = carry_looks("delete_faces", prev["vertices"], prev["faces"], prev["looks"], m["vertices"], sel)
    assert np.array_equal(m["looks"], want.astype(np.float64))
    steps.append(m)
    # simplifying: each new vertex has the looks of an old one
    edit(env, sid, "decimate", {"ratio": 0.5})
    m = served(env, sid, tmp_path)
    assert len(m["looks"]) == len(m["vertices"]) and set(np.unique(m["looks"])) <= set(np.unique(steps[-1]["looks"]))
    steps.append(m)
    # undo / redo bring each step's own looks back
    for k in range(len(steps) - 2, -1, -1):
        assert post(b + "/process/undo").status_code == 200
        assert np.array_equal(served(env, sid, tmp_path)["looks"], steps[k]["looks"]), k
    for k in range(1, len(steps)):
        assert post(b + "/process/redo").status_code == 200
        assert np.array_equal(served(env, sid, tmp_path)["looks"], steps[k]["looks"]), k
    # a step missing from the cache is rebuilt by replaying its edit, looks and all
    st = get(b + "/process").json()
    cache = env["studio"].projects.scan_dir(env["pid"], sid) / "process" / "cache"
    (cache / f"{st['edits'][2]['id']}.npz").unlink()  # the smoothing's step
    (cache / f"{st['edits'][1]['id']}.npz").unlink()  # and the hole filling's
    for _ in range(3):
        assert post(b + "/process/undo").status_code == 200
    assert np.array_equal(served(env, sid, tmp_path)["looks"], steps[2]["looks"])
    assert post(b + "/process/redo").status_code == 200
    assert np.array_equal(served(env, sid, tmp_path)["looks"], steps[3]["looks"])


def test_a_cache_file_from_before_the_looks_still_loads(env, tmp_path):
    """A cached step written before this change has no looks: it loads, its mesh has none, edits work,
    and the Check and its reasons answer ("few_looks" unknown, in words)."""
    sid = adopted_scan(env, "Old cache", path=steep_path())
    b = base_url(env, sid)
    assert get(b + "/process").status_code == 200  # taken over: the base's cache file has the looks
    cache = env["studio"].projects.scan_dir(env["pid"], sid) / "process" / "cache" / "base.npz"
    with np.load(cache) as z:
        assert "L" in z.files
        old = {k: z[k] for k in z.files if k != "L"}
    np.savez(cache, **old)  # as written before
    edit(env, sid, "smooth", {"method": "taubin", "strength": 0.3})  # replayed from the old base
    assert served(env, sid, tmp_path)["looks"] is None
    assert post(b + "/process/undo").status_code == 200
    m = served(env, sid, tmp_path)
    assert m["looks"] is None and len(m["vertices"]) > 0
    r = post(b + "/check/why", {"tolerance_mm": 0.1})
    assert r.status_code == 200, r.text
    w = r.json()
    assert w["given"]["looks"] is False and w["areas"]
    assert any(u.startswith("few_looks:") for u in w["areas"][0]["unknown_because"])


@pytest.mark.slow
def test_refine_with_lasers_keeps_the_looks(env, tmp_path):
    """"Refine with lasers" moves the vertices onto the laser points: each keeps its count of looks."""
    from ohs.track.export import save_points_ply

    V, F, S, L = plate(speck=False)
    sid = adopted_scan(env, "Refine", (V, F, S, L), steep_path())
    res = env["studio"].projects.scan_dir(env["pid"], sid) / "result"
    save_points_ply(res / "laser_points.ply", V[::3] + np.array([0.0, 0.0, 0.3]))  # the lasers saw it 0.3 mm higher
    b = base_url(env, sid)
    assert get(b + "/process").status_code == 200
    r = post(b + "/process/refine", {})
    assert r.status_code == 200, r.text
    j = wait_job(env, r.json()["id"])
    assert j["state"] == "done", j
    s = get(b + "/process").json()
    assert s["stages"]["refine"]["state"] == "done", s["stages"]["refine"]
    m = served(env, sid, tmp_path)
    assert np.array_equal(m["looks"], L.astype(np.float64)) and not np.allclose(m["vertices"], V)
    stage = load_ply(env["studio"].projects.scan_dir(env["pid"], sid) / "process" / "refine" / "mesh.ply")
    assert np.array_equal(stage["looks"], L.astype(np.float64))


# --------------------------------------------------------------------------- the route
def test_the_reasons_and_poses_of_the_check_answer(env):
    sid = adopted_scan(env, "Steep", path=steep_path())
    b = base_url(env, sid)
    err(post(b + "/check/why", {"tolerance_mm": 0}), 422)
    plain = post(b + "/check", {"tolerance_mm": 0.1})
    assert plain.status_code == 200, plain.text
    plain = plain.json()
    t0 = time.perf_counter()
    r = post(b + "/check/why", {"tolerance_mm": 0.1, "version": plain["version"]})
    first = time.perf_counter() - t0
    assert r.status_code == 200, r.text
    w = r.json()
    assert w["version"] == plain["version"] and w["tolerance_mm"] == 0.1 and w["n_areas"] == plain["n_areas"]
    # the same areas, in the same order
    assert [(a["kind"], a["n_faces"], a["area_mm2"]) for a in w["areas"]] == \
        [(a["kind"], a["n_faces"], a["area_mm2"]) for a in plain["areas"]]
    assert [a["index"] for a in w["areas"]] == list(range(len(plain["areas"])))
    patch = next(a for a in w["areas"] if abs(a["center"][0] - 50) < 2 and abs(a["center"][1] - 30) < 2)
    assert patch["reason"] == "steep" and "steep" in patch["reason_text"] and patch["evidence"]["view_angle_deg"] > 65
    assert patch["normal"] == pytest.approx([0.0, 0.0, 1.0], abs=1e-6)
    v = patch["view"]
    assert v["ok"] and v["pose"]["tilt_deg"] < 59.6 and v["share_seen"] > 0.5 and v["text"].startswith("Hold the scanner")
    assert len(v["pose"]["T_wc"]) == 4 and all(len(v["pose"][k]) == 3 for k in ("position_mm", "look_dir", "up", "target_mm"))
    assert w["given"]["looks"] and w["given"]["views"] and w["given"]["edges"]
    cam = w["camera_path"]
    assert cam["available"] and cam["why"] is None and cam["frames_total"] == cam["frames_used"] == 12
    assert cam["frames_cap"] == 120 and cam["scans"][0]["frames"] == 12
    assert 1 <= len(w["plan"]) <= 3 and all(p["pose"] and 0 <= p["area"] < len(w["areas"]) for p in w["plan"])
    assert w["plan"][0]["area"] == 0 and w["plan"][0]["kind"] == "area"
    # the plain answer is as it was, and the reasons are kept for this version and tolerance
    again = post(b + "/check", {"tolerance_mm": 0.1}).json()
    assert again == plain
    t0 = time.perf_counter()
    assert post(b + "/check/why", {"tolerance_mm": 0.1}).json() == w
    assert time.perf_counter() - t0 < max(0.5, first / 2)
    # another tolerance without asking the plain answer first: its own areas
    w2 = post(b + "/check/why", {"tolerance_mm": 0.3}).json()
    p2 = post(b + "/check", {"tolerance_mm": 0.3}).json()
    assert [(a["n_faces"], a["area_mm2"]) for a in w2["areas"]] == [(a["n_faces"], a["area_mm2"]) for a in p2["areas"]]
    err(post(b + "/check/why", {"tolerance_mm": 0.1, "version": plain["version"] + 1}), 409, "changed")


def test_what_is_not_there_is_said_not_guessed(env):
    # no calibration: the angle and the distance cannot be told, and there is no pose
    sid = adopted_scan(env, "No calibration", path=steep_path(), calib=False)
    w = post(base_url(env, sid) + "/check/why", {"tolerance_mm": 0.1}).json()
    assert w["camera_path"]["available"] is False and "calibration" in w["camera_path"]["why"]
    a = w["areas"][0]
    assert a["reason"] in ("unknown", "few_looks", "not_seen", "edge") and a["reason"] != "steep"
    assert any(u.startswith("steep, far:") and "calibration" in u for u in a["unknown_because"])
    assert a["view"]["ok"] is False and a["view"]["pose"] is None and "calibration" in a["view"]["text"]
    assert w["plan"] == []
    # no camera path at all
    sid = adopted_scan(env, "No path", path=None)
    w = post(base_url(env, sid) + "/check/why", {"tolerance_mm": 0.1}).json()
    assert w["camera_path"]["available"] is False and "camera path" in w["camera_path"]["why"]
    # no voxel size: the edge cannot be told
    sid = adopted_scan(env, "No voxel", path=steep_path(), report={"calibration": "recording",
                                                                    "params": {"depth": {"min_depth_mm": 150.0,
                                                                                         "max_depth_mm": 450.0}}})
    w = post(base_url(env, sid) + "/check/why", {"tolerance_mm": 0.1}).json()
    assert w["given"]["edges"] is False and w["camera_path"]["available"]
    assert any(u.startswith("edge:") for u in w["areas"][0]["unknown_because"])


def test_a_merge_uses_each_scans_camera_path_or_says_which_is_missing(env):
    """A merged scan of two plates: its report lists the scans with their ``T``; each one's own camera
    path, calibration and depth range are used. When the first scan's camera path is gone, none is used,
    and the answer names that scan."""
    V, F, S, L = plate(speck=False)
    a = adopted_scan(env, "Left half", (V, F, S, L), steep_path(8))
    T2 = np.eye(4)
    T2[:3, 3] = [0.0, 0.0, 0.0]
    b2 = adopted_scan(env, "Right half", (V, F, S, L), steep_path(6, off_deg=72.0))
    st = env["studio"]
    m = adopted_scan(env, "Merged", (V, F, S, L), path=None, calib=False, report={
        "merged": True, "mesh": {"voxel_mm": 1.0},
        "scans": [{"name": "Left half", "recording": "gone", "frames": 8, "T": np.eye(4).tolist()},
                  {"name": "Right half", "recording": "gone", "frames": 6, "T": T2.tolist()}]})
    st.projects.update_scan(env["pid"], m, merged_from=[{"id": a, "name": "Left half"}, {"id": b2, "name": "Right half"}])
    w = post(base_url(env, m) + "/check/why", {"tolerance_mm": 0.1}).json()
    cam = w["camera_path"]
    assert cam["available"] and [s["name"] for s in cam["scans"]] == ["Left half", "Right half"]
    assert cam["frames_total"] == 14
    patch = next(x for x in w["areas"] if abs(x["center"][0] - 50) < 2 and abs(x["center"][1] - 30) < 2)
    assert patch["reason"] == "steep" and patch["view"]["ok"]
    # the first scan lost its camera path: nothing is guessed from the second one alone
    (st.projects.scan_dir(env["pid"], a) / "result" / "trajectory.csv").unlink()
    post(base_url(env, m) + "/process/edits", {"op": "smooth", "params": {"method": "taubin", "strength": 0.1}})
    deadline = time.monotonic() + 60
    while get(base_url(env, m) + "/process").json()["cursor"] < 1 and time.monotonic() < deadline:
        time.sleep(0.05)
    w = post(base_url(env, m) + "/check/why", {"tolerance_mm": 0.1}).json()
    assert w["camera_path"]["available"] is False and "Left half" in w["camera_path"]["why"]
    assert all(x["reason"] != "steep" for x in w["areas"])
    # its camera path changed since the merge (a frame more): also said
    write_trajectory(st.projects.scan_dir(env["pid"], a) / "result" / "trajectory.csv",
                     [(i, i / 30.0, True, Tw) for i, Tw in enumerate(steep_path(9))])
    assert post(base_url(env, m) + "/process/undo").status_code == 200
    w = post(base_url(env, m) + "/check/why", {"tolerance_mm": 0.1}).json()
    assert w["camera_path"]["available"] is False and "changed since the merge" in w["camera_path"]["why"]
