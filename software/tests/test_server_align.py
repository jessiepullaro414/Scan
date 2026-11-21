"""Aligning and merging two scans through the real server (APP_API.md, "M4 first slice").

A project holds two processed scans of the simulated bracket taken from two sides (their results
made at the true poses, so the true motion between them is known) and one scan without a mesh.
"""
from __future__ import annotations

import shutil

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

from ohs import geometry as G  # noqa: E402
from ohs.app.config import Config  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402
from ohs.track.export import load_mesh  # noqa: E402
from test_align import N_FRAMES, _scan  # noqa: E402
from test_server_process_studio import T as TIMEOUT, err, get, post, run_job  # noqa: E402

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("align-server")
    scans = [_scan(root, "bracket", 20.0, 0), _scan(root, "bracket", 110.0, 1), _scan(root, "bracket", 200.0, 2)]
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        st = app.state.studio
        pid = requests.post(srv.url + "/api/projects", json={"name": "Bracket"}, timeout=TIMEOUT).json()["id"]
        sids = []
        for name, sc in (("Top", scans[0]), ("Side", scans[1]), ("No mesh", scans[0]), ("Back", scans[2])):
            scan, rec_dir = st.projects.add_scan(pid, name, "markerless")
            shutil.copytree(sc["rec"], rec_dir)
            n = len(RecordingReader(rec_dir))
            if name == "No mesh":
                st.projects.update_scan(pid, scan["id"], status="recorded", n_frames=n, duration_s=n / 30.0)
            else:
                shutil.copytree(sc["out"], st.projects.scan_dir(pid, scan["id"]) / "result")
                st.projects.update_scan(pid, scan["id"], status="processed", n_frames=n, duration_s=n / 30.0, quality="standard")
            sids.append(scan["id"])
        yield {"srv": srv, "url": srv.url, "studio": st, "pid": pid, "a": sids[0], "b": sids[1], "bare": sids[2],
               "back": sids[3], "T_true": G.invert_se3(scans[0]["world"]) @ scans[1]["world"],
               "T_back": G.invert_se3(scans[0]["world"]) @ scans[2]["world"], "base": f"{srv.url}/api/projects/{pid}"}


def _off(env, T) -> tuple[float, float]:
    e = G.pose_error(env["T_true"], np.asarray(T))
    return e[1], e[0]


def test_align_two_scans_from_their_shape(env):
    done = run_job(env, post(env["base"] + "/align", {"target": env["a"], "source": env["b"], "same_setup": True}), "align")
    res = done["result"]
    assert res["accepted"] is True and res["reason"] is None and res["start"] == "features"
    assert res["verdict"].startswith("Aligned: ") and done["message"] == res["verdict"]
    assert res["project_id"] == env["pid"] and res["target"] == env["a"] and res["source"] == env["b"]
    assert res["support"] == {"target": True, "source": True} and res["used_support"] is True and res["pairs_rms_mm"] is None
    assert res["agree"] >= 3 and 0.15 < res["overlap"] <= 1.0 and res["dof"] == 6
    mm, deg = _off(env, res["T"])
    assert mm < 2.5 and deg < 0.6, (mm, deg)
    env["T"] = res["T"]
    # the part moved: the same call says so, and the table is not used
    done = run_job(env, post(env["base"] + "/align", {"target": env["a"], "source": env["b"], "same_setup": False}), "align")
    assert done["result"]["accepted"] and done["result"]["used_support"] is False


def test_align_from_picked_points(env):
    rng = np.random.default_rng(0)
    T = env["T_true"]
    target = np.array([[0.0, 0.0, 250.0], [30.0, 10.0, 240.0], [-20.0, 25.0, 260.0], [10.0, -30.0, 255.0]])
    source = (target - T[:3, 3]) @ T[:3, :3] + rng.normal(0.0, 1.0, target.shape)
    pairs = [{"source": s.tolist(), "target": t.tolist()} for s, t in zip(source, target)]
    done = run_job(env, post(env["base"] + "/align", {"target": env["a"], "source": env["b"], "pairs": pairs}), "align")
    res = done["result"]
    assert res["start"] == "given" and res["candidates"] == 1 and res["accepted"], res["verdict"]
    assert 0.0 < res["pairs_rms_mm"] < 3.0 and "from the start you gave" in res["verdict"]
    mm, deg = _off(env, res["T"])
    assert mm < 2.5 and deg < 0.6
    # too few points, points in one line: said at once, with what to do
    err(post(env["base"] + "/align", {"target": env["a"], "source": env["b"], "pairs": pairs[:2]}), 422, "too few")
    line = [{"source": [float(k), 0.0, 0.0], "target": [float(k), 0.0, 0.0]} for k in (0, 10, 20)]
    err(post(env["base"] + "/align", {"target": env["a"], "source": env["b"], "pairs": line}), 422, "cannot place the scans")


def test_align_from_the_tags_both_scans_saw(env, tmp_path):
    """Two scans of the sphere on its tagged table, 150 degrees apart: from the shape alone they
    cannot be aligned (a sphere turns into itself); the server finds the tags in both recordings
    and aligns from them, for a pair and for several scans; ``use_tags: false`` turns that off."""
    from test_align_tags import _scan as sphere_scan

    a, b = sphere_scan(tmp_path, 0.0, 0), sphere_scan(tmp_path, 150.0, 1)
    st = env["studio"]
    pid = requests.post(env["url"] + "/api/projects", json={"name": "Sphere"}, timeout=TIMEOUT).json()["id"]
    ids = []
    for name, sc in (("Front", a), ("Back", b)):
        scan, rec_dir = st.projects.add_scan(pid, name, "markerless")
        shutil.copytree(sc["rec"], rec_dir)
        shutil.copytree(sc["out"], st.projects.scan_dir(pid, scan["id"]) / "result")
        n = len(RecordingReader(rec_dir))
        st.projects.update_scan(pid, scan["id"], status="processed", n_frames=n, duration_s=n / 30.0, quality="standard")
        ids.append(scan["id"])
    base = f"{env['url']}/api/projects/{pid}"
    T_true = G.invert_se3(a["world"]) @ b["world"]
    res = run_job(env, post(base + "/align", {"target": ids[0], "source": ids[1]}), "align")["result"]
    assert res["accepted"] and res["start"] == "tags" and "from the tags both scans saw" in res["verdict"], res["verdict"]
    assert len(res["tags"]["used"]) >= 2 and res["tags"]["start"] == "accepted"
    deg, mm = G.pose_error(T_true, np.asarray(res["T"]))
    print(f"from the tags {res['tags']}: {mm:.2f} mm, {deg:.3f} deg off")
    assert mm < 2.0 and deg < 0.3
    res = run_job(env, post(base + "/align", {"target": ids[0], "source": ids[1], "use_tags": False}), "align")["result"]
    assert res["tags"] is None and res["start"] == "features" and not res["accepted"]  # a sphere fits itself turned
    res = run_job(env, post(base + "/align_all", {"scans": ids}), "align_all")["result"]
    assert res["accepted"] and res["pairs"][0]["start"] == "tags" and len(res["tags"][ids[0]]) >= 3, res["verdict"]
    deg, mm = G.pose_error(T_true, np.asarray(res["T"][ids[1]]))
    assert mm < 2.0 and deg < 0.3


def test_an_alignment_the_engine_is_not_sure_of_carries_its_poses(env, tmp_path):
    """MAINTENANCE_PLAN F11: two scans of the sphere on its table from the shape alone. A sphere fits
    itself turned round, so the job says "not sure" (not a guess presented as an answer), why in one
    word and in words, and carries the poses it had in mind for the page to show; for several scans
    the pair carries them too and the verdict says the engine was not sure."""
    from test_align_tags import _scan as sphere_scan

    a, b = sphere_scan(tmp_path, 0.0, 0), sphere_scan(tmp_path, 150.0, 1)
    st = env["studio"]
    pid = requests.post(env["url"] + "/api/projects", json={"name": "Sphere, not sure"}, timeout=TIMEOUT).json()["id"]
    ids = []
    for name, sc in (("Front", a), ("Back", b)):
        scan, rec_dir = st.projects.add_scan(pid, name, "markerless")
        shutil.copytree(sc["out"], st.projects.scan_dir(pid, scan["id"]) / "result")
        st.projects.update_scan(pid, scan["id"], status="processed", n_frames=1, duration_s=1.0, quality="standard")
        ids.append(scan["id"])
    base = f"{env['url']}/api/projects/{pid}"
    done = run_job(env, post(base + "/align", {"target": ids[0], "source": ids[1], "use_tags": False}), "align")
    res = done["result"]
    assert res["accepted"] is False and res["sure"] is False and res["doubt"] in ("rival", "turned", "one_guess"), res
    assert res["verdict"].startswith("Not aligned: not sure") and done["message"] == res["verdict"]
    poses = res["poses"]
    assert 2 <= len(poses) <= 4 and poses[0]["chosen"] and not any(q["chosen"] for q in poses[1:])
    assert np.allclose(poses[0]["T"], res["T"]) and all(np.asarray(q["T"]).shape == (4, 4) for q in poses)
    assert all(set(q) >= {"pick", "overlap", "reached", "from", "apart_mm"} for q in poses)
    res = run_job(env, post(base + "/align_all", {"scans": ids, "use_tags": False}), "align_all")["result"]
    pair = res["pairs"][0]
    assert not res["accepted"] and pair["sure"] is False and len(pair["poses"]) >= 2
    assert "not sure of" in res["verdict"] and "Pick matching points" in res["verdict"], res["verdict"]


def test_align_and_merge_say_what_is_missing(env):
    err(post(env["base"] + "/align", {"target": env["a"], "source": env["a"]}), 409, "with itself")
    err(post(env["base"] + "/align", {"target": env["a"], "source": env["bare"]}), 409, "has no mesh yet")
    err(post(env["base"] + "/align", {"target": "scan-19990101-000000", "source": env["b"]}), 404)
    eye = np.eye(4).tolist()
    err(post(env["base"] + "/merge", {"target": env["a"], "source": env["bare"], "T": eye}), 409, "has no mesh yet")
    bad = np.eye(4)
    bad[0, 0] = 2.0
    err(post(env["base"] + "/merge", {"target": env["a"], "source": env["b"], "T": bad.tolist()}), 422, "would bend or stretch")
    err(post(env["base"] + "/merge", {"target": env["a"], "source": env["b"], "T": [[1.0, 0.0], [0.0, 1.0]]}), 422, "cannot be read")
    # found in review (UX_PLAN R21): a typed name another scan of the project has is refused, as a rename is
    r = post(env["base"] + "/merge", {"target": env["a"], "source": env["b"], "T": eye, "name": " side "})
    err(r, 409, "already called")
    assert r.json()["suggestion"] == "side (2)" and "“side (2)”" in r.json()["hint"]


def test_merge_adds_a_scan_to_the_project(env):
    T = env.get("T", env["T_true"].tolist())
    st, pid = env["studio"], env["pid"]
    before = {s["id"] for s in get(env["base"]).json()["scans"]}
    done = run_job(env, post(env["base"] + "/merge", {"target": env["a"], "source": env["b"], "T": T, "name": "Both sides"}),
                   "merge")
    res = done["result"]
    sid = res["scan_id"]
    assert sid not in before and res["merged_from"] == [env["a"], env["b"]] and done["message"] == "Merged into \"Both sides\""
    scan = st.projects.get_scan(pid, sid)
    assert scan["name"] == "Both sides" and scan["status"] == "processed" and scan["n_frames"] == 2 * N_FRAMES
    assert [m["name"] for m in scan["merged_from"]] == ["Top", "Side"] and scan["quality"] == "standard"
    assert scan["result"]["report"]["mesh"]["n_vertices"] > 10000
    r = get(env["url"] + scan["result"]["mesh_url"])
    assert r.status_code == 200 and len(r.content) > 100000
    assert get(env["url"] + scan["result"]["stl_url"]).status_code == 200
    assert not (st.projects.project_dir(pid) / ".merge").exists()  # nothing left of the work folder
    V, _, _ = load_mesh(st.projects.scan_dir(pid, sid) / "result" / "mesh.ply")
    # the merged scan has no recording: it cannot be merged again, and says so
    err(post(env["base"] + "/merge", {"target": sid, "source": env["b"], "T": np.eye(4).tolist()}), 409, "has no recording")
    # the part was moved: both tables stay out, and the model is the part alone
    done = run_job(env, post(env["base"] + "/merge", {"target": env["a"], "source": env["b"], "T": T, "same_setup": False}),
                   "merge")
    scan2 = st.projects.get_scan(pid, done["result"]["scan_id"])
    assert scan2["name"] == "Top + Side"
    assert 500 < scan2["result"]["report"]["mesh"]["n_vertices"] < 0.3 * len(V)


def test_a_merge_of_a_moved_part_says_what_it_left_out(env):
    """The part was moved (``same_setup: false``): each scan's table is found on its own mesh and left
    out, place by place; the job's result says for each scan what was left out and carries the
    warnings for the page, and the merged scan's report.json holds the same."""
    import json

    T = env.get("T", env["T_true"].tolist())
    done = run_job(env, post(env["base"] + "/merge", {"target": env["a"], "source": env["b"], "T": T, "same_setup": False,
                                                     "name": "Moved"}), "merge")
    res = done["result"]
    assert [t["id"] for t in res["tables"]] == [env["a"], env["b"]] and [t["name"] for t in res["tables"]] == ["Top", "Side"]
    assert all(t["rule"] == "map" and isinstance(t["summary"], str) and t["summary"] for t in res["tables"]), res["tables"]
    assert isinstance(res["warnings"], list) and all(isinstance(w, str) for w in res["warnings"])
    rep = json.loads((env["studio"].projects.scan_dir(env["pid"], res["scan_id"]) / "result" / "report.json").read_text(encoding="utf-8"))
    assert [s["table"]["rule"] for s in rep["scans"]] == ["map", "map"] and rep["warnings"] == res["warnings"]
    for s in rep["scans"]:
        assert s["table"]["table_area_mm2"] > 1e4 and s["table"]["left_in_mesh"]["faces"] >= 0
    # the part stayed: nothing is left out and nothing is said
    done = run_job(env, post(env["base"] + "/merge", {"target": env["a"], "source": env["b"], "T": T, "name": "Stayed"}), "merge")
    assert done["result"]["warnings"] == [] and done["result"]["tables"] == []
    # ... but what its cleaning took is said all the same (UX_PLAN R7), in the result and in report.json
    rep = json.loads((env["studio"].projects.scan_dir(env["pid"], done["result"]["scan_id"]) / "result" / "report.json")
                     .read_text(encoding="utf-8"))
    cl = rep["mesh"]["cleaning"]
    assert done["result"]["cleaning"] == cl["summary"] and "left out as too small" in cl["summary"], done["result"]
    assert cl["thin_pieces_kept"] == 0 and "thin part" not in cl["summary"]
    gone = rep["mesh"]["n_faces_before_cleaning"] - rep["mesh"]["n_faces"]
    assert (cl["pieces_removed"] == 0 or gone > 0) and cl["summary"].startswith("No loose bit") == (cl["pieces_removed"] == 0)


def test_align_and_merge_several_scans(env):
    """Top, side and back at once: each placed in the top scan's world, then merged into one scan."""
    scans = [env["a"], env["b"], env["back"]]
    done = run_job(env, post(env["base"] + "/align_all", {"scans": scans}), "align_all")
    res = done["result"]
    assert res["accepted"] is True and res["placed"] == scans and res["unplaced"] == [] and done["message"] == res["verdict"]
    assert res["verdict"].startswith("All 3 scans placed through 2 links") and res["scans"] == scans
    assert np.allclose(res["T"][env["a"]], np.eye(4)) and len(res["pairs"]) == 3
    assert {(q["a"], q["b"]) for q in res["pairs"]} == {(scans[0], scans[1]), (scans[0], scans[2]), (scans[1], scans[2])}
    assert sum(q["used"] == "chain" for q in res["pairs"]) == 2 and res["support"] == {sid: True for sid in scans}
    for sid, T_true in ((env["b"], env["T_true"]), (env["back"], env["T_back"])):
        e = G.pose_error(T_true, np.asarray(res["T"][sid]))
        assert e[1] < 3.0 and e[0] < 1.0, (sid, e)
    # what is wrong with a request is said at once
    err(post(env["base"] + "/align_all", {"scans": scans[:1]}), 422, "at least two")
    err(post(env["base"] + "/align_all", {"scans": [env["a"], env["a"]]}), 409, "twice")
    err(post(env["base"] + "/align_all", {"scans": [env["a"], env["bare"]]}), 409, "has no mesh yet")
    bad = [{"a": env["a"], "b": "scan-19990101-000000", "pairs": [{"source": [0, 0, 0], "target": [0, 0, 0]}] * 3}]
    err(post(env["base"] + "/align_all", {"scans": scans, "picked": bad}), 422, "not in the list")
    # merge all three at the found poses
    body = {"scans": [{"id": sid, "T": res["T"][sid]} for sid in scans]}
    done = run_job(env, post(env["base"] + "/merge", body), "merge")
    scan = env["studio"].projects.get_scan(env["pid"], done["result"]["scan_id"])
    assert scan["name"] == "Top + Side + Back" and scan["n_frames"] == 3 * N_FRAMES
    assert [m["name"] for m in scan["merged_from"]] == ["Top", "Side", "Back"] and done["result"]["merged_from"] == scans
    assert done["scans"] == scans  # the job's meta
    # one form or the other
    err(post(env["base"] + "/merge", {**body, "target": env["a"]}), 422, "two different ways of merging")
    err(post(env["base"] + "/merge", {"target": env["a"], "source": env["b"]}), 422, "did not say which scans")
    err(post(env["base"] + "/merge", {"scans": body["scans"][:1]}), 422, "at least two")
