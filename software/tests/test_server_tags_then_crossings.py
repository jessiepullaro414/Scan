"""On a scan the tags hold, Straighten does not run the laser crossings after them (ENGINE_PLAN X8, the wall question).

Measured on a short tagged scan of standing walls (``VALIDATION.md``, "The thin wall lost with the tags on: the laser
crossings, not the tags"): the tags alone took the camera path from 1.39 to 0.28 mm off the truth and the 0.5 mm wall
was there; the laser crossings, run after them, passed their own check at 0.847 against 0.85, moved the path to
0.66 mm off the truth and pulled the wall's two sides through each other: the wall was gone from the mesh, without a
warning. A thin part is never removed in silence, so:

* with tags that held the scan (``rep["tags"]["acted"]``) the crossings are not run; ``straighten.json`` says so
  (``laser_curves.reason == "tags_held"``) and the stage's mesh is the fuse at the tags' poses;
* without tags, or with tags that did not act, the crossings run exactly as before.

The recording is too short for tags or loops, so the loops' step is replaced by one that reports what the tags did;
the fuse runs for real (on the processor path or the graphics card's).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

from ohs.app.config import Config  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402
from recordings import scan_recording  # noqa: E402
from test_server_process_studio import add_scan, base_url, get, post, run_job  # noqa: E402

TAGS_VERDICT = "No loops found. 5 tags held the scan: the middle of the scan moved 0.3 mm into place."


def test_the_rule_as_a_function():
    from ohs.server.processing import TAGS_HELD_CROSSINGS, crossings_after_tags

    held = crossings_after_tags({"tags": {"acted": True, "used": [0, 1, 3, 4, 5]}})
    assert held == {"changed": False, "reason": "tags_held", "verdict": TAGS_HELD_CROSSINGS, "warning": None}
    assert "tags held the scan" in TAGS_HELD_CROSSINGS and "laser crossings" in TAGS_HELD_CROSSINGS
    # tags that were looked for and did not act, a report from before the tags, no report: the crossings run
    for rep in ({"tags": {"acted": False, "reason": "not_better"}}, {"tags": {"enabled": False, "acted": False}},
                {"tags": None}, {"loops_accepted": 3}, {}, None):
        assert crossings_after_tags(rep) is None, rep


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("tagscross")
    rec = scan_recording(root / "rec", n_frames=10, image_size=(640, 400), seed=3, depth_every=0)
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        st = app.state.studio
        yield {"srv": srv, "url": srv.url, "root": root, "studio": st, "rec": rec, "cfg": st.cfg}


def _replace_steps(monkeypatch, *, tags_acted: bool) -> list:
    """The loops' step reports tags that held the scan (poses moved by 0.3 mm) or tags that did not act (poses as
    tracked); the laser crossings' step notes each call and, when called, moves the poses and says it acted."""
    import ohs.track.curve_ba as CB
    import ohs.track.posegraph as PG
    from ohs import geometry as G

    calls: list = []

    def loops(rec_path, calib, trajectory, **kw):
        rows = [(int(i), np.asarray(T, float)) for i, _, ok, T in trajectory if ok]
        if tags_acted:
            poses = {i: T @ G.make_se3(np.eye(3), [0.3, 0.0, 0.0]) for i, T in rows}
            return poses, {"reason": "no_candidates", "changed": True, "loops_accepted": 0, "loops": [],
                           "correction_mm": 0.3, "correction_deg": 0.0, "verdict": TAGS_VERDICT,
                           "tags": {"enabled": True, "seen": 6, "acted": True, "reason": "anchored",
                                    "used": [0, 1, 3, 4, 5], "moved_mm": 0.3}}
        return dict(rows), {"reason": "no_candidates", "changed": False, "loops_accepted": 0, "loops": [],
                            "correction_mm": 0.0, "correction_deg": 0.0,
                            "verdict": "No loops found: to let Studio straighten a scan, pass over an earlier part again.",
                            "tags": {"enabled": True, "seen": 6, "acted": False, "reason": "not_better", "used": [0, 1]}}

    def crossings(rec_path, calib, trajectory, *, reference=None, params=None, progress=None, cancelled=None):
        calls.append(len(trajectory))
        poses = {int(i): np.asarray(T, float) @ G.make_se3(np.eye(3), [0.0, 0.4, 0.0]) for i, _, ok, T in trajectory if ok}
        return poses, {"changed": True, "reason": "adjusted", "crossings": 999, "frames": len(poses),
                       "holdout": {"ratio": 0.5}, "miss_mm": {"before": 0.2, "after": 0.1}, "moved_mm": 0.4,
                       "moved_deg": 0.0, "verdict": "moved", "warning": CB.acted_warning(0.4)}

    monkeypatch.setattr(PG, "straighten_trajectory", loops)
    monkeypatch.setattr(CB, "laser_curve_adjustment", crossings)
    return calls


def _scan_dir(env: dict, pid: str, sid: str) -> Path:
    return env["root"] / "projects" / pid / "scans" / sid


def _moved(env: dict, pid: str, sid: str) -> np.ndarray:
    """How far each camera of Straighten's stage is from the tracked one (mm), as (dx, dy, dz) in the camera's frame."""
    from ohs.track.posegraph import read_trajectory

    d = _scan_dir(env, pid, sid) / "process"
    a = {int(i): T for i, _, ok, T in read_trajectory(d / "track" / "trajectory.csv") if ok}
    b = {int(i): T for i, _, ok, T in read_trajectory(d / "straighten" / "trajectory.csv") if ok}
    return np.array([(np.linalg.inv(a[i]) @ b[i])[:3, 3] for i in sorted(a)])


def test_tags_that_held_the_scan_keep_the_laser_crossings_out(env, monkeypatch):
    calls = _replace_steps(monkeypatch, tags_acted=True)
    pid, sid = add_scan(env, "Tags hold", env["rec"])
    b = base_url(env, pid, sid)
    run_job(env, post(b + "/process/track", {"quality": "preview"}), "process_track")
    run_job(env, post(b + "/process/straighten", {}), "process_straighten")
    assert calls == [], "the laser crossings ran on a scan the tags hold"
    stt = get(b + "/process").json()["stages"]["straighten"]
    assert stt["state"] == "done" and stt["tags"] == 5 and stt["crossings"] is None, stt
    assert stt["summary"] == TAGS_VERDICT and "laser" not in stt["summary"], stt["summary"]
    rep = json.loads((_scan_dir(env, pid, sid) / "process" / "straighten.json").read_text(encoding="utf-8"))
    assert rep["laser_curves"]["reason"] == "tags_held" and rep["laser_curves"]["changed"] is False
    assert rep["laser_curves"]["warning"] is None
    # the stage's poses are the tags' own (0.3 mm along x), not the crossings' (0.4 mm along y)
    moved = _moved(env, pid, sid)
    assert np.allclose(moved, [0.3, 0.0, 0.0], atol=1e-3), moved[:3]
    res = json.loads((_scan_dir(env, pid, sid) / "result" / "report.json").read_text(encoding="utf-8"))
    assert not any("laser lines meet" in w for w in res.get("warnings", [])), res.get("warnings")


def test_without_tags_that_acted_the_laser_crossings_run_as_before(env, monkeypatch):
    calls = _replace_steps(monkeypatch, tags_acted=False)
    pid, sid = add_scan(env, "Tags do not act", env["rec"])
    b = base_url(env, pid, sid)
    run_job(env, post(b + "/process/track", {"quality": "preview"}), "process_track")
    run_job(env, post(b + "/process/straighten", {}), "process_straighten")
    assert len(calls) == 1, calls
    stt = get(b + "/process").json()["stages"]["straighten"]
    assert stt["state"] == "done" and stt["crossings"] == 999 and stt["tags"] is None, stt
    assert "laser lines cross" in stt["summary"], stt["summary"]
    rep = json.loads((_scan_dir(env, pid, sid) / "process" / "straighten.json").read_text(encoding="utf-8"))
    assert rep["laser_curves"]["reason"] == "adjusted" and rep["laser_curves"]["changed"] is True
    assert np.allclose(_moved(env, pid, sid), [0.0, 0.4, 0.0], atol=1e-3)


def test_the_scorecards_chain_asks_the_same_rule():
    """``tools/scorecard.py`` runs Straighten as the server does: its copy of the step must ask the server's rule."""
    import inspect

    import tools.scorecard as SC

    src = inspect.getsource(SC._app_straighten)
    assert "crossings_after_tags(rep)" in src
