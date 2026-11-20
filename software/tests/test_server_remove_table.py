""""Remove the table" in the processing studio (FEATURE_PLAN X1) through the real server.

The test scan of ``test_server_process_studio.py`` (a 10-frame sphere on a table, tracked at
preview quality): the preview route answers the faces the edit would remove; the edit removes
exactly those, with its label, keeps each vertex's sigma and each face's filled flag, and undo /
redo (also a step rebuilt from its saved selection) bring the meshes back bit for bit. On the mesh
without its table the engine refuses, in its words, and nothing changes. The op is accepted in a
user's recipe (a refusal there skips the step) and is in no built-in recipe.
"""
from __future__ import annotations

import base64

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

from ohs.app.config import Config  # noqa: E402
from ohs.mesh.topology import compact  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.processing import BUILTIN_RECIPES, MESH_OPS, RECIPE_OPS  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402
from ohs.track.export import load_ply  # noqa: E402
from recordings import scan_recording  # noqa: E402
from test_server_process_studio import T, add_scan, base_url, err, get, post, run_job, wait_job  # noqa: E402


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("table")
    rec = scan_recording(root / "rec", n_frames=10, image_size=(640, 400), seed=3, depth_every=0)
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        e = {"srv": srv, "url": srv.url, "root": root, "studio": app.state.studio, "rec": rec}
        e["pid"], e["sid"] = add_scan(e, "Sphere on a table", rec)
        run_job(e, post(base_url(e) + "/process/track", {"quality": "preview"}), "process_track")
        yield e


def mesh(env: dict, s: dict) -> dict:
    """The current ``result/mesh.ply`` as arrays (vertices, faces, quality, filled)."""
    p = env["root"] / f"dl-{s['current']['version']}-{np.random.default_rng().integers(1 << 30)}.ply"
    p.write_bytes(get(env["url"] + s["current"]["mesh_url"]).content)
    return load_ply(p)


def same(a: dict, b: dict) -> bool:
    for k in ("vertices", "faces", "quality", "filled"):
        if (a[k] is None) != (b[k] is None) or (a[k] is not None and not np.array_equal(a[k], b[k])):
            return False
    return True


def faces_of(answer: dict) -> np.ndarray:
    return np.frombuffer(base64.b64decode(answer["faces"]), dtype="<u4").astype(np.int64)


def test_the_op_is_known_and_in_no_built_in_recipe():
    assert "remove_table" in MESH_OPS and "remove_table" in RECIPE_OPS
    assert all(o["op"] != "remove_table" for r in BUILTIN_RECIPES for o in r["ops"])


def test_preview_then_remove_the_table_undo_and_redo(env):
    b = base_url(env)
    # filled faces first, so that their flags have something to follow
    run_job(env, post(b + "/process/edits", {"op": "fill_holes", "params": {"max_hole_mm": 30}}), "mesh_edit")
    s0 = get(b + "/process").json()
    m0 = mesh(env, s0)
    n0 = s0["current"]["stats"]["triangles"]
    assert len(m0["faces"]) == n0
    # the preview: the faces it would remove, and what it found; nothing changes
    r = post(b + "/process/remove-table/preview", {"version": s0["current"]["version"]})
    assert r.status_code == 200, r.text
    pv = r.json()
    sel = faces_of(pv)
    assert pv["version"] == s0["current"]["version"] and pv["n_faces"] == len(sel) and pv["margin_mm"] == 1.0
    assert np.all(np.diff(sel) > 0) and 0.4 * n0 < len(sel) < n0 and int(sel.max()) < n0
    A = np.linalg.norm(np.cross(m0["vertices"][m0["faces"][:, 1]] - m0["vertices"][m0["faces"][:, 0]],
                                m0["vertices"][m0["faces"][:, 2]] - m0["vertices"][m0["faces"][:, 0]]), axis=1) / 2
    assert pv["area_mm2"] == pytest.approx(float(A[sel].sum()), rel=1e-3)
    assert pv["normal"] and pv["share"] > 0.3 and pv["rule"] and pv["summary"].startswith("Found the table")
    assert isinstance(pv["warnings"], list) and pv["lowest_mm"] is not None and pv["triangles"] == n0
    assert isinstance(pv["raised"], list) and all(x["n_faces"] > 0 and (x["faces"] is None) == (x["verdict"] == "uneven")
                                                  for x in pv["raised"])  # drawn areas come with their faces
    assert get(b + "/process").json()["current"]["version"] == s0["current"]["version"]
    # the same again (kept for this version and margin)
    assert post(b + "/process/remove-table/preview", {}).json()["faces"] == pv["faces"]
    # the edit removes exactly those faces
    j = run_job(env, post(b + "/process/edits", {"op": "remove_table", "params": {}}), "mesh_edit")
    e = j["result"]["edit"]
    assert e["op"] == "remove_table" and e["label"] == f"Remove the table ({len(sel):,} faces)"
    assert e["params"] == {"margin_mm": 1.0} and e["triangles_after"] == n0 - len(sel)
    assert e["summary"].startswith("Removed the table") and e["summary"] == j["result"]["message"]
    s1 = get(b + "/process").json()
    assert s1["cursor"] == s0["cursor"] + 1 and s1["edits"][-1]["label"] == e["label"]
    m1 = mesh(env, s1)
    keep = np.ones(n0, dtype=bool)
    keep[sel] = False
    V2, F2, vidx = compact(m0["vertices"].astype(np.float64), m0["faces"][keep])
    assert np.array_equal(m1["faces"], F2) and np.array_equal(m1["vertices"], V2.astype(m1["vertices"].dtype))
    # each vertex's sigma and each face's filled flag go with it
    if m0["quality"] is not None:
        assert np.array_equal(m1["quality"], m0["quality"][vidx])
    else:
        assert m1["quality"] is None
    f0 = m0["filled"] if m0["filled"] is not None else np.zeros(n0, dtype=bool)
    assert (m1["filled"] if m1["filled"] is not None else np.zeros(len(F2), dtype=bool)).tolist() == f0[keep].tolist()
    # undo: the table is back, bit for bit; redo: gone again
    u = post(b + "/process/undo").json()
    assert u["cursor"] == s0["cursor"] and same(mesh(env, u), m0)
    rd = post(b + "/process/redo").json()
    assert rd["cursor"] == s1["cursor"] and same(mesh(env, rd), m1)
    # a step missing from the cache is rebuilt from the selection the edit kept
    proc = env["root"] / "projects" / env["pid"] / "scans" / env["sid"] / "process"
    assert (proc / "selections" / f"{e['id']}.npy").is_file()
    (proc / "cache" / f"{e['id']}.npz").unlink()
    post(b + "/process/undo")
    rd = post(b + "/process/redo").json()
    assert same(mesh(env, rd), m1)
    env["table_gone"] = rd


def test_without_its_table_the_scan_is_refused_and_nothing_changes(env):
    b = base_url(env)
    s = env.get("table_gone") or pytest.skip("needs the edit above")
    before = get(b + "/process").json()
    body = err(post(b + "/process/remove-table/preview", {}), 422, "no table found")
    assert "select" in body["hint"]
    r = post(b + "/process/edits", {"op": "remove_table", "params": {"margin_mm": 1.5}})
    failed = wait_job(env, r.json()["id"])
    assert failed["state"] == "failed" and "No table found" in failed["error"], failed
    after = get(b + "/process").json()
    assert after["current"]["version"] == before["current"]["version"] == s["current"]["version"]
    assert after["edits"] == before["edits"] and after["cursor"] == before["cursor"]


def test_the_parameters_are_checked(env):
    b = base_url(env)
    v = get(b + "/process").json()["current"]["version"]
    err(post(b + "/process/edits", {"op": "remove_table", "params": {"margin_mm": -1}}), 422, "the margin must be")
    err(post(b + "/process/edits", {"op": "remove_table", "params": {"margin_mm": 50}}), 422, "the margin must be")
    err(post(b + "/process/edits", {"op": "remove_table", "params": {"margin_mm": "1"}}), 422, "the margin must be")
    err(post(b + "/process/edits", {"op": "remove_table", "params": {"height": 1}}), 422, "a setting this edit does not have")
    err(post(b + "/process/remove-table/preview", {"margin_mm": 21}), 422, "the margin must be")
    err(post(b + "/process/remove-table/preview", {"version": v - 1}), 409, "changed")
    err(post(base_url(env, sid="scan-missing") + "/process/remove-table/preview", {}), 404)


def test_a_user_recipe_may_remove_the_table(env, monkeypatch):
    url = env["url"] + "/api/recipes"
    r = post(url, {"name": "Without the table", "settings": {"quality": "preview", "straighten": "off"},
                   "ops": [{"op": "remove_table", "params": {"margin_mm": 1.5}},
                           {"op": "remove_floaters", "params": {"min_size_mm": 5}}]})
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["ops"][0] == {"op": "remove_table", "params": {"margin_mm": 1.5}}
    assert all(o["op"] != "remove_table" for x in get(url).json() if x["builtin"] for o in x["ops"])
    err(post(url, {"name": "Bad", "settings": {}, "ops": [{"op": "remove_table", "params": {"margin_mm": 99}}]}), 422)
    b = base_url(env)
    j = run_job(env, post(b + "/process/recipe", {"recipe_id": rec["id"], "scope": "edits"}), "process_recipe")
    s = get(b + "/process").json()
    assert [e["op"] for e in s["edits"]] == ["remove_table", "remove_floaters"], j
    assert s["edits"][0]["label"].startswith("Remove the table (") and s["edits"][0]["params"] == {"margin_mm": 1.5}
    # saved from the scan, the step comes along
    fs = post(url, {"name": "From the scan", "from_scan": {"project_id": env["pid"], "scan_id": env["sid"]}}).json()
    assert fs["ops"][0] == {"op": "remove_table", "params": {"margin_mm": 1.5}}
    # in a recipe a refusal skips the step, in the engine's words, and the rest runs
    import ohs.mesh.support as sup

    def refuse(*a, **k):
        raise sup.SupportRefused("No table found: a test. Nothing was removed.")

    monkeypatch.setattr(sup, "find_support", refuse)
    j = run_job(env, post(b + "/process/recipe", {"recipe_id": rec["id"], "scope": "edits"}), "process_recipe")
    assert "Remove the table: skipped. No table found: a test" in j["result"]["message"], j["result"]["message"]
    assert [e["op"] for e in get(b + "/process").json()["edits"]] == ["remove_floaters"]
    requests.delete(f"{url}/{rec['id']}", timeout=T)
    requests.delete(f"{url}/{fs['id']}", timeout=T)


def test_keep_this_travels_with_the_edit(env):
    """A scan whose mesh is a box on a table with a 0.2 mm disc beside it (a part processed before:
    its result is taken over as the base). The preview draws the disc as a raised area that goes, with
    its faces; "Keep this" (a point on it) keeps exactly that area through the edit, undo and redo."""
    from ohs.track.export import save_mesh
    from test_mesh_support import DISC_AT, _wide_scene, raise_disc

    pid, sid = add_scan(env, "Shim beside a box", env["rec"])
    V, F, _ = _wide_scene()
    V = raise_disc(V, [0, 0, 1], 0.0, DISC_AT, 15.0, 0.2)
    res = env["root"] / "projects" / pid / "scans" / sid / "result"
    res.mkdir(parents=True, exist_ok=True)
    save_mesh(res / "mesh.ply", V, F)
    env["studio"].projects.update_scan(pid, sid, status="processed", quality="preview")
    b = base_url(env, pid, sid)
    s0 = get(b + "/process").json()
    m0 = mesh(env, s0)
    pv = post(b + "/process/remove-table/preview", {}).json()
    disc = [x for x in pv["raised"] if np.linalg.norm(np.asarray(x["centre"])[:2] - DISC_AT[:2]) < 5.0]
    assert disc and disc[0]["verdict"] == "warned" and disc[0]["faces"], pv["raised"]
    area = faces_of(disc[0])
    assert np.isin(area, faces_of(pv)).all()  # it would go with the table ...
    assert pv["thin_limit_mm"] > 0 and pv["limit"]
    point = [float(x) for x in np.asarray(disc[0]["centre"])]
    kp = post(b + "/process/remove-table/preview", {"keep": [point]}).json()
    assert not np.isin(area, faces_of(kp)).any()  # ... unless kept
    assert set(faces_of(pv)) - set(faces_of(kp)) == set(area.tolist())  # that area, and nothing else
    err(post(b + "/process/remove-table/preview", {"keep": [[1, 2]]}), 422, "keep")
    j = run_job(env, post(b + "/process/edits", {"op": "remove_table", "params": {"keep": [point]}}), "mesh_edit")
    e = j["result"]["edit"]
    assert e["label"] == f"Remove the table ({len(faces_of(kp)):,} faces, 1 area kept)"
    assert e["params"] == {"margin_mm": 1.0, "keep": [[round(x, 2) for x in point]]}
    s1 = get(b + "/process").json()
    m1 = mesh(env, s1)
    keep = np.ones(len(m0["faces"]), dtype=bool)
    keep[faces_of(kp)] = False
    _, F2, _ = compact(m0["vertices"].astype(np.float64), m0["faces"][keep])
    assert np.array_equal(m1["faces"], F2)
    u = post(b + "/process/undo").json()
    assert same(mesh(env, u), m0)
    proc = env["root"] / "projects" / pid / "scans" / sid / "process"
    (proc / "cache" / f"{e['id']}.npz").unlink()  # rebuilt from the faces the edit removed
    rd = post(b + "/process/redo").json()
    assert same(mesh(env, rd), m1)
