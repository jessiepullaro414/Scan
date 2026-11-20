"""A merge makes its model with the presets' meshing values (FEATURE_PLAN P2 and N2, found in review 2026-11-20).

The merge of the Align page (and of a second pass) used ``process_params`` alone, so two scans made with the
"Thin sheet metal and small parts" preset (0.5 mm voxels) were merged at 1 mm. Now the merge takes the first
scan's ("stays in place") acting values that act on meshing, ``voxel_mm`` and ``thin_walls``, as a processing
stage takes them (``ohs.server.processing.preset_meshing``); the merged scan's report and entry say which preset
and values; a scan of the merge that acts with other values is named in the merge's warnings. With General
everywhere the merge is exactly as before: the same parameters, and the same mesh.
"""
from __future__ import annotations

import dataclasses
import json
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
from ohs.track.export import load_ply  # noqa: E402
from test_align import _scan  # noqa: E402
from test_server_process_studio import T as TIMEOUT, post, run_job  # noqa: E402

pytestmark = pytest.mark.slow

THIN = {"id": "thin-sheet-small-parts", "name": "Thin sheet metal and small parts", "hand": {}}


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("merge-preset")
    scans = [_scan(root, "bracket", 20.0, 0), _scan(root, "bracket", 110.0, 1)]
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        st = app.state.studio
        pid = requests.post(srv.url + "/api/projects", json={"name": "Bracket"}, timeout=TIMEOUT).json()["id"]
        ids = {}
        for key, name, sc, preset in (("thin_a", "Top thin", scans[0], THIN), ("thin_b", "Side thin", scans[1], THIN),
                                      ("gen_a", "Top", scans[0], None), ("gen_b", "Side", scans[1], None)):
            scan, rec_dir = st.projects.add_scan(pid, name, "markerless")
            shutil.copytree(sc["rec"], rec_dir)
            shutil.copytree(sc["out"], st.projects.scan_dir(pid, scan["id"]) / "result")
            n = len(RecordingReader(rec_dir))
            fields = {"status": "processed", "n_frames": n, "duration_s": n / 30.0, "quality": "standard"}
            if preset:
                fields["preset"] = preset
            st.projects.update_scan(pid, scan["id"], **fields)
            ids[key] = scan["id"]
        yield {"url": srv.url, "studio": st, "pid": pid, "ids": ids, "scans": scans, "root": root,
               "T": (G.invert_se3(scans[0]["world"]) @ scans[1]["world"]).tolist(), "base": f"{srv.url}/api/projects/{pid}"}


def _merge(env, a, b, name):
    done = run_job(env, post(env["base"] + "/merge", {"target": env["ids"][a], "source": env["ids"][b], "T": env["T"],
                                                     "name": name}), "merge")
    sid = done["result"]["scan_id"]
    rep = json.loads((env["studio"].projects.result_dir(env["pid"], sid) / "report.json").read_text(encoding="utf-8"))
    return done["result"], rep, env["studio"].projects.scan_entry(env["pid"], sid)


def test_two_thin_scans_merge_at_the_thin_presets_voxel(env):
    res, rep, entry = _merge(env, "thin_a", "thin_b", "Both thin")
    print(f"thin + thin: voxel {rep['mesh']['voxel_mm']} mm, {rep['mesh']['n_vertices']} vertices; preset {rep.get('preset')}")
    assert rep["mesh"]["voxel_mm"] == pytest.approx(0.5)
    assert entry["preset"]["id"] == THIN["id"] and rep["preset"]["id"] == THIN["id"]
    assert rep["preset"]["used"]["voxel_mm"] == pytest.approx(0.5) and rep["preset"]["from_scan"] == env["ids"]["thin_a"]
    assert res["warnings"] == []


def test_a_scan_with_other_meshing_values_is_named_in_the_warnings(env):
    res, rep, _ = _merge(env, "thin_a", "gen_b", "Thin and general")
    assert rep["mesh"]["voxel_mm"] == pytest.approx(0.5)  # the first scan's ("stays in place")
    w = [x for x in res["warnings"] if "Side" in x]
    assert len(w) == 1 and "0.5 mm" in w[0] and "Top thin" in w[0], res["warnings"]
    assert rep["warnings"] == res["warnings"]


def test_general_merges_exactly_as_before(env):
    """The parameters are those the merge built before presets, and the mesh is the one they make."""
    from ohs.align import ScanInput, merge_scans
    from ohs.server.studio import process_params

    st = env["studio"]
    res, rep, entry = _merge(env, "gen_a", "gen_b", "General")
    assert res["warnings"] == [] and "preset" not in entry
    before = dataclasses.replace(process_params(st.cfg, "markerless", "standard", 640), poisson=False)
    assert st.aligning.merge_params(env["pid"], env["ids"]["gen_a"], 640, "standard")[0] == before
    sc = env["scans"]
    out = env["root"] / "merge-before"
    merge_scans([ScanInput(sc[0]["rec"], sc[0]["out"] / "trajectory.csv", np.eye(4), "Top"),
                 ScanInput(sc[1]["rec"], sc[1]["out"] / "trajectory.csv", np.asarray(env["T"]), "Side")], out, before)
    old = load_ply(out / "mesh.ply")
    new = load_ply(st.projects.result_dir(env["pid"], res["scan_id"]) / "mesh.ply")
    # the GPU path writes the same mesh in another vertex order from run to run: compare the sorted vertices
    Vo, Vn = old["vertices"], new["vertices"]
    assert Vo.shape == Vn.shape and len(old["faces"]) == len(new["faces"])
    so, sn = Vo[np.lexsort(Vo.T[::-1])], Vn[np.lexsort(Vn.T[::-1])]
    print(f"General: {len(Vo)} vertices before and after; largest difference {np.abs(so - sn).max():.2e} mm")
    assert np.array_equal(so, sn)
