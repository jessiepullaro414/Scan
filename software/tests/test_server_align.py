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
    scans = [_scan(root, "bracket", 20.0, 0), _scan(root, "bracket", 110.0, 1)]
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        st = app.state.studio
        pid = requests.post(srv.url + "/api/projects", json={"name": "Bracket"}, timeout=TIMEOUT).json()["id"]
        sids = []
        for name, sc in (("Top", scans[0]), ("Side", scans[1]), ("No mesh", scans[0])):
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
               "T_true": G.invert_se3(scans[0]["world"]) @ scans[1]["world"], "base": f"{srv.url}/api/projects/{pid}"}


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
    err(post(env["base"] + "/align", {"target": env["a"], "source": env["b"], "pairs": line}), 422, "do not give a pose")


def test_align_and_merge_say_what_is_missing(env):
    err(post(env["base"] + "/align", {"target": env["a"], "source": env["a"]}), 409, "to itself")
    err(post(env["base"] + "/align", {"target": env["a"], "source": env["bare"]}), 409, "has no mesh yet")
    err(post(env["base"] + "/align", {"target": "scan-19990101-000000", "source": env["b"]}), 404)
    eye = np.eye(4).tolist()
    err(post(env["base"] + "/merge", {"target": env["a"], "source": env["bare"], "T": eye}), 409, "has no mesh yet")
    bad = np.eye(4)
    bad[0, 0] = 2.0
    err(post(env["base"] + "/merge", {"target": env["a"], "source": env["b"], "T": bad.tolist()}), 422, "not a rigid motion")
    err(post(env["base"] + "/merge", {"target": env["a"], "source": env["b"], "T": [[1.0, 0.0], [0.0, 1.0]]}), 422, "4x4")


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
