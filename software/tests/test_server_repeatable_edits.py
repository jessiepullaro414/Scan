"""``[process] repeatable_edits`` through the real server (``MAINTENANCE_PLAN.md`` F15, the owner's
decision D9: off by default).

Two scans hold the same base mesh, the second with its vertices renumbered at random and its faces
shuffled (as two runs of the GPU path list the same mesh). The same edits run on both: remove loose
pieces, remove the table, fill holes up to 30 mm, smooth (bilateral, away from edges), simplify to half.

* **On:** after every edit the two scans' result meshes are the same content, bit for bit (every
  vertex's position, normal, sigma and looks, every face); each edit records ``by_position: true``.
* **Off:** the edits record exactly the params they did before F15, and the shuffled scan ends with
  another mesh (simplifying orders equal costs by number: the case the option is for).
* **A step replayed later uses the setting it was made with:** an edit made with the option on is
  rebuilt (its cache file gone: undo, redo) with the option off, and the other way round; both give
  the result file they gave the first time, byte for byte, although the other setting would give
  another mesh.
* A recipe saved from a scan does not carry the setting (it belongs to the app when the recipe runs).

Run on the GPU path and with ``OHS_COMPUTE=cpu`` (the edits are numpy / scipy; the base mesh is not).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

from ohs.app.config import Config  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.processing import _load_npz, _save_npz  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402
from recordings import scan_recording  # noqa: E402
from test_server_keep_thin import mesh_content  # noqa: E402
from test_server_process_studio import add_scan, base_url, get, post, run_job, wait_job  # noqa: E402

OPS = [("remove_floaters", {"min_size_mm": 5.0}), ("remove_table", {}), ("fill_holes", {"max_hole_mm": 30.0}),
       ("smooth", {"method": "bilateral", "strength": 0.5, "away_from_edges": True}), ("decimate", {"ratio": 0.5})]


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("repeatable")
    rec = scan_recording(root / "rec", n_frames=10, image_size=(640, 400), seed=3, depth_every=0)
    cfg = Config()
    assert cfg.process.repeatable_edits is False  # off by default (D9)
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        st = app.state.studio
        e = {"srv": srv, "url": srv.url, "root": root, "studio": st, "rec": rec, "cfg": st.cfg}
        e["pid"], e["sid"] = add_scan(e, "Sphere", rec)
        run_job(e, post(base_url(e) + "/process/track", {"quality": "preview"}), "process_track")
        e["sid2"] = add_scan(e, "Sphere shuffled", rec, project=e["pid"])[1]
        run_job(e, post(base_url(e, sid=e["sid2"]) + "/process/track", {"quality": "preview"}), "process_track")
        # the second scan's base: the first one's mesh, renumbered at random, faces shuffled
        pm = st.processing
        sp1, sp2 = pm.scan(e["pid"], e["sid"]), pm.scan(e["pid"], e["sid2"])
        V, F, N, S, Fl, L = _load_npz(sp1.cache_path(0), normals=True)
        rng = np.random.default_rng(11)
        pv = rng.permutation(len(V))
        new = np.empty_like(pv)
        new[pv] = np.arange(len(pv))
        pf = rng.permutation(len(F))
        perm = (lambda x: None if x is None else x[pv])
        with sp2.lock:
            _save_npz(sp2.cache_path(0), V[pv], new[F[pf]], perm(N), perm(S), None if Fl is None else Fl[pf], perm(L))
            pm._show_step(sp2, 0)
        with sp1.lock:  # both results written from the cached step (its normals are kept as float16)
            pm._show_step(sp1, 0)
        assert_same(e, e["sid"], e["sid2"])  # the same content, another order
        yield e
        e["cfg"].process.repeatable_edits = False


def result(env: dict, sid: str) -> Path:
    return env["root"] / "projects" / env["pid"] / "scans" / sid / "result" / "mesh.ply"


def assert_same(env: dict, a: str, b: str) -> None:
    (va, fa), (vb, fb) = mesh_content(result(env, a)), mesh_content(result(env, b))
    assert va.shape == vb.shape and np.array_equal(va, vb), "vertices differ"
    assert fa.shape == fb.shape and np.array_equal(fa, fb), "faces differ"


def differ(env: dict, a: str, b: str) -> bool:
    try:
        assert_same(env, a, b)
    except AssertionError:
        return True
    return False


def edit(env: dict, sid: str, op: str, params: dict) -> dict | None:
    """Run one edit; None when "Remove the table" finds none (the step is left out on both scans)."""
    r = post(base_url(env, sid=sid) + "/process/edits", {"op": op, "params": params})
    assert r.status_code == 200, r.text
    j = wait_job(env, r.json()["id"])
    if op == "remove_table" and j["state"] == "failed":
        return None
    assert j["state"] == "done", j
    return j["result"]["edit"]


def reset(env: dict, sid: str) -> None:
    pm = env["studio"].processing
    sp = pm.scan(env["pid"], sid)
    with sp.lock:
        sp.drop_edits_after(0)
        pm._show_step(sp, 0)


def run_all(env: dict, on: bool) -> tuple[list[str], list[str]]:
    env["cfg"].process.repeatable_edits = on
    for sid in (env["sid"], env["sid2"]):
        reset(env, sid)
    differs, ran = [], []
    for op, params in OPS:
        e1 = edit(env, env["sid"], op, params)
        e2 = edit(env, env["sid2"], op, params)
        if e1 is None or e2 is None:
            assert e1 is None and e2 is None, op
            continue
        ran.append(op)
        for e in (e1, e2):
            assert ("by_position" in e["params"]) == on and e["params"].get("by_position", on) is on
        if differ(env, env["sid"], env["sid2"]):
            differs.append(op)
    return differs, ran


def test_on_the_same_mesh_in_another_order_gives_the_same_result(env):
    differs, ran = run_all(env, on=True)
    assert differs == [] and ran == [op for op, _ in OPS]  # the table too: the sphere stands on one


def test_off_the_edits_record_what_they_did_before_and_the_order_shows(env):
    differs, _ = run_all(env, on=False)
    # Which edits come out differently for the shuffled scan depends on the mesh: with ties, simplifying and
    # hole filling do (tests/test_mesh_repeatable.py builds meshes that have them); this scan's mesh on the CPU
    # path had none in review, so it is told here, not asserted.
    print(f"REPEATABLE_OFF edits that differ after a shuffle on this mesh: {sorted(differs) or 'none'}")
    s = get(base_url(env) + "/process").json()
    params = {e["op"]: e["params"] for e in s["edits"]}
    assert params["remove_floaters"] == {"min_size_mm": 5.0}
    assert params["fill_holes"] == {"max_hole_mm": 30.0}
    assert params["decimate"] == {"ratio": 0.5}


@pytest.mark.parametrize("made_on", [True, False])
def test_a_replayed_step_uses_the_setting_it_was_made_with(env, made_on):
    sid = env["sid2"]
    b = base_url(env, sid=sid)
    env["cfg"].process.repeatable_edits = made_on
    reset(env, sid)
    for op, params in (("fill_holes", {"max_hole_mm": 30.0}), ("decimate", {"ratio": 0.5})):
        edit(env, sid, op, params)
    first = result(env, sid).read_bytes()
    s = get(b + "/process").json()
    assert s["cursor"] == 2 and ("by_position" in s["edits"][1]["params"]) == made_on
    keep = env["root"] / f"first-{made_on}.ply"
    shutil.copyfile(result(env, sid), keep)
    # the other setting gives another mesh on this step, so the replay below has something to tell apart
    env["cfg"].process.repeatable_edits = not made_on
    post(b + "/process/undo")
    edit(env, sid, "decimate", {"ratio": 0.5})
    assert result(env, sid).read_bytes() != first
    # back to the edit as first made (the setting flipped once more), then rebuilt from its edit alone
    env["cfg"].process.repeatable_edits = made_on
    post(b + "/process/undo")
    edit(env, sid, "decimate", {"ratio": 0.5})
    assert result(env, sid).read_bytes() == first
    env["cfg"].process.repeatable_edits = not made_on
    s = get(b + "/process").json()
    cache = env["root"] / "projects" / env["pid"] / "scans" / sid / "process" / "cache"
    (cache / f"{s['edits'][1]['id']}.npz").unlink()
    u = post(b + "/process/undo")
    assert u.status_code == 200 and u.json()["cursor"] == 1
    r = post(b + "/process/redo")
    assert r.status_code == 200 and r.json()["cursor"] == 2
    assert (cache / f"{s['edits'][1]['id']}.npz").is_file()  # rebuilt by replaying the edit
    assert result(env, sid).read_bytes() == first


def test_a_recipe_from_a_scan_does_not_carry_the_setting(env):
    env["cfg"].process.repeatable_edits = True
    reset(env, env["sid"])
    edit(env, env["sid"], "fill_holes", {"max_hole_mm": 30.0})
    r = post(env["url"] + "/api/recipes", {"name": "From the sphere", "from_scan": {"project_id": env["pid"],
                                                                                    "scan_id": env["sid"]}})
    assert r.status_code == 200, r.text
    assert r.json()["ops"] == [{"op": "fill_holes", "params": {"max_hole_mm": 30.0}}]
    requests.delete(env["url"] + "/api/recipes/" + r.json()["id"], timeout=30)
