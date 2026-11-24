"""The tag pose held, through the real server (ENGINE_PLAN X19; ``[process] align_hold_tags``, off by default).

Two processed scans of the simulated bracket from opposite sides (the top at 20 degrees, the back at 200: they share
little), each with a marker map of six tags in its own world that agree with the true motion (written as a marker-mode
scan's ``marker_map.json``, which the server reads as the tags the scan saw). Off: the alignment and its response are
as before, without the hold's fields. On: the tags' pose is held, the response carries the hold's fields, and a merge
at that pose records the held link in its result's ``held_by_tags`` and the merged scan's report (information, not
one of its warnings).
"""
from __future__ import annotations

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
from ohs.track.export import load_mesh  # noqa: E402
from test_align import _scan  # noqa: E402
from test_server_process_studio import T as TIMEOUT, post, run_job  # noqa: E402

pytestmark = pytest.mark.slow
HOLD_FIELDS = ("held_by_tags", "tag_rms_mm", "tags_used", "shape_moves_mm", "overruled", "tag_pose_sigma_mm")


def _square(c, s=10.0):
    return np.array([[-s, s, 0.0], [s, s, 0.0], [s, -s, 0.0], [-s, -s, 0.0]]) + np.asarray(c, dtype=np.float64)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("align-hold-server")
    scans = [_scan(root, "bracket", 20.0, 0), _scan(root, "bracket", 200.0, 2)]
    T_true = G.invert_se3(scans[0]["world"]) @ scans[1]["world"]  # the back's mesh world into the top's
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        st = app.state.studio
        pid = requests.post(srv.url + "/api/projects", json={"name": "Bracket"}, timeout=TIMEOUT).json()["id"]
        sids = []
        for name, sc in (("Top", scans[0]), ("Back", scans[1])):
            scan, rec_dir = st.projects.add_scan(pid, name, "markerless")
            shutil.copytree(sc["rec"], rec_dir)
            shutil.copytree(sc["out"], st.projects.scan_dir(pid, scan["id"]) / "result")
            n = len(RecordingReader(rec_dir))
            st.projects.update_scan(pid, scan["id"], status="processed", n_frames=n, duration_s=n / 30.0, quality="standard")
            sids.append(scan["id"])
        # six tags round the part, in the top scan's world, and the same tags in the back scan's world
        Va, _, _ = load_mesh(st.projects.scan_dir(pid, sids[0]) / "result" / "mesh.ply")
        c, z = np.median(Va, axis=0), float(np.percentile(Va[:, 2], 2))
        tags_a = {k: _square(c + [120.0 * np.cos(a), 120.0 * np.sin(a), 0.0] - [0.0, 0.0, c[2] - z])
                  for k, a in enumerate(np.linspace(0.0, 2 * np.pi, 6, endpoint=False))}
        Ti = G.invert_se3(T_true)
        tags_b = {k: X @ Ti[:3, :3].T + Ti[:3, 3] for k, X in tags_a.items()}
        for sid, tags in zip(sids, (tags_a, tags_b)):
            (st.projects.scan_dir(pid, sid) / "result" / "marker_map.json").write_text(json.dumps(
                {"corners": {str(k): X.tolist() for k, X in tags.items()}, "n_observations": {str(k): 20 for k in tags}}),
                encoding="utf-8")
        yield {"url": srv.url, "studio": st, "pid": pid, "a": sids[0], "b": sids[1], "T_true": T_true,
               "base": f"{srv.url}/api/projects/{pid}"}


def _align(env):
    return run_job(env, post(env["base"] + "/align", {"target": env["a"], "source": env["b"]}), "align")["result"]


def test_off_the_response_is_as_before(env):
    st = env["studio"]
    assert st.cfg.process.align_hold_tags is False
    res = _align(env)
    assert res["start"] == "tags" and res["accepted"] and res["little_shared"], res["verdict"]
    assert not any(k in res for k in HOLD_FIELDS) and res["tags"]["start"] == "accepted"


def test_on_the_tags_hold_and_a_merge_records_the_held_link(env):
    st = env["studio"]
    st.cfg.process.align_hold_tags = True
    try:
        res = _align(env)
        assert res["held_by_tags"] is True and res["overruled"] is None and len(res["tags_used"]) >= 3, res["verdict"]
        assert res["verdict"].startswith(f"Aligned from the {len(res['tags_used'])} tags both scans saw")
        assert res["tags"]["start"] == "held" and res["tag_pose_sigma_mm"] >= 0 and res["shape_moves_mm"] is not None
        deg, mm = G.pose_error(env["T_true"], np.asarray(res["T"]))
        assert mm < 1e-3 and deg < 1e-4  # the tags' pose (exact here), not where the shapes alone fit
        m = run_job(env, post(env["base"] + "/merge", {"target": env["a"], "source": env["b"], "T": res["T"],
                                                       "name": "Top and back, held"}), "merge")["result"]
        held = m["held_by_tags"]
        assert [(h["a"], h["b"]) for h in held] == [(env["a"], env["b"])] and held[0]["tags_used"] == res["tags_used"]
        assert "by the" in held[0]["words"] and held[0]["words"] not in m["warnings"]  # information, not a warning
        rep = json.loads((st.projects.scan_dir(env["pid"], m["scan_id"]) / "result" / "report.json").read_text(encoding="utf-8"))
        assert rep["held_by_tags"][0]["b"] == env["b"] and rep["held_by_tags"][0]["words"] == held[0]["words"]
        assert held[0]["words"] not in rep["warnings"]
        # a merge at another pose than the held one records nothing
        T2 = np.asarray(res["T"]).copy()
        T2[0, 3] += 0.5
        m2 = run_job(env, post(env["base"] + "/merge", {"target": env["a"], "source": env["b"], "T": T2.tolist(),
                                                        "name": "Top and back, moved"}), "merge")["result"]
        assert "held_by_tags" not in m2
    finally:
        st.cfg.process.align_hold_tags = False
