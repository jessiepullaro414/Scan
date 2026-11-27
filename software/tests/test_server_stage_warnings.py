"""Nothing goes missing in silence (ENGINE_PLAN R2-5): the stages that fuse again say what their own fuse warned.

* **Straighten and Build the surface** (``fuse``) each fuse again at given poses; that fuse's report can carry a
  warning of its own (the mesh model's pool was full, the matcher fell back to the processor). The stage's entry
  carries ``warnings``: the stage's OWN sentences, in the app's words. The camera path's warning, which every later
  stage's report takes from the track (``_carry_path_check``), is shown under "Follow the camera" only, and
  Straighten's laser-crossings sentence, already at the end of its summary, is not said a second time.
* **"Make only the thin walls finer"** (``fine_walls``): when the finer model's pool refused surface
  (``mesh_capacity.full``) the stage, ``made.fine_walls``, the stage's report, the base's report and the scan's
  summary say so, in a sentence of its own (:func:`ohs.app.process.fine_walls_pool_warning`).
* **Without warnings** the stages are as before, with ``warnings: []``; a state saved before the field loads with [].

The recordings are too short for the engine's checks to warn and too small to fill a pool, so the checks and the
pool's status are replaced by ones that warn (the rest runs for real, on the processor path or the graphics card's).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

from ohs.app.config import Config  # noqa: E402
from ohs.app.process import fine_walls_pool_warning, pool_full_warning  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.projects import user_words  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402
from recordings import scan_recording  # noqa: E402
from test_server_process_studio import add_scan, base_url, get, post, run_job, wait_job  # noqa: E402

PATH = ("The camera path jumps by 37 mm near frame 4, more than a hand moves in 0.8 s: tracking may have slid "
        "there. Scan it again more slowly, with more of the part's edges in view.")
#: the mesh model's pool, full from frame 6 (as ConfidenceTsdf.capacity_status with first_refused_frame)
CAP = {"full": True, "bricks": 1000, "mb": 12.0, "max_bricks": 1000, "max_mb": 12.0, "bricks_refused": 40,
       "requests_refused": 50, "calls_refused": 3, "first_refused_call": 6, "voxel_mm": 2.0, "first_refused_frame": 6}
POOL = pool_full_warning(CAP)
#: the finer model's pool for the thin walls, full from frame 7
FCAP = {**CAP, "bricks": 5000, "mb": 60.0, "bricks_refused": 1200, "voxel_mm": 0.5, "first_refused_frame": 7}
FINE = fine_walls_pool_warning(FCAP)


def _warn(rows, cpu_path=False):
    return {"version": 1, "window_s": 0.8, "limit_mm": 4.0, "frames": 10, "jump_mm": 37.2, "frame": 4,
            "state": "warn", "warning": PATH}


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("stagewarn")
    rec = scan_recording(root / "rec", n_frames=10, image_size=(640, 400), seed=3, depth_every=0)
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        st = app.state.studio
        yield {"srv": srv, "url": srv.url, "root": root, "studio": st, "rec": rec, "cfg": st.cfg}


@pytest.fixture(autouse=True)
def settings(env):
    env["cfg"].process.voxel_mm, env["cfg"].process.fine_walls = None, False
    yield
    env["cfg"].process.voxel_mm, env["cfg"].process.fine_walls = None, False


@pytest.fixture()
def full_on(monkeypatch) -> set:
    """The passes (by their folder's name: ``track``, ``straighten``, ``fuse``) whose mesh model's pool is made full:
    their report gets ``mesh_capacity`` and the sentence, as ``process_recording`` writes them."""
    import ohs.app.process as P

    names: set = set()
    real = P.process_recording

    def spy(rec, out_dir, *a, **kw):
        report = real(rec, out_dir, *a, **kw)
        if Path(out_dir).name.split(".tmp-")[0] in names:
            report["mesh_capacity"] = dict(CAP)
            report["warnings"] = [*report.get("warnings", []), POOL]
        return report

    monkeypatch.setattr(P, "process_recording", spy)
    return names


def scan_dir(env: dict, pid: str, sid: str) -> Path:
    return env["root"] / "projects" / pid / "scans" / sid


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def card(env: dict, pid: str, sid: str) -> dict:
    return [s for s in get(f"{env['url']}/api/projects/{pid}").json()["scans"] if s["id"] == sid][0]


# --------------------------------------------------------------------------- the sentences
def test_the_pool_sentences():
    # the mesh model's sentence is as before, word for word
    assert POOL == ("The mesh model ran out of room after frame 6: surface seen after that was not added (40 of 1,040 "
                    "blocks of surface left out; the model holds 1,000 blocks, 12 MB). The mesh is incomplete there. "
                    "Scan a smaller area at a time, or make the mesh at a coarser mesh detail.")
    # the thin walls' own: the finer model, not the whole mesh; nothing removed; no advice of a coarser mesh detail
    assert "finer model for the thin walls ran out of room after frame 7" in FINE
    assert "1,200 of 6,200 blocks" in FINE and "5,000 blocks, 60 MB" in FINE
    assert "coarser mesh detail" not in FINE and "The mesh is incomplete" not in FINE
    assert "while the first kept frames were fused" in fine_walls_pool_warning({**FCAP, "first_refused_frame": None})


def test_the_new_field_and_a_state_saved_before_it(tmp_path):
    import ohs.server.processing as SP

    stages = SP._empty_state("p", "s")["stages"]
    for name in ("track", "straighten", "fuse", SP.FINE_WALLS):
        assert stages[name]["warnings"] == [], name
    old = SP._empty_state("p", "s")
    for name in ("straighten", "fuse", SP.FINE_WALLS):
        del old["stages"][name]["warnings"]
    old["stages"]["fuse"].update(state="done", summary="Built the surface", voxel_mm=2.0)
    sp = SP.ScanProcess("p", "s", tmp_path)
    sp.dir.mkdir(parents=True)
    sp.state_path.write_text(json.dumps(old), encoding="utf-8")
    assert sp.load()
    assert sp.state["stages"]["fuse"]["state"] == "done" and sp.state["stages"]["fuse"]["warnings"] == []
    assert sp.state["stages"]["straighten"]["warnings"] == [] and sp.state["stages"][SP.FINE_WALLS]["warnings"] == []


# --------------------------------------------------------------------------- Build the surface
def test_build_the_surface_says_its_own_warning_and_not_the_camera_paths(env, monkeypatch, full_on):
    monkeypatch.setattr("ohs.app.process.check_path", _warn)
    pid, sid = add_scan(env, "Fuse full", env["rec"])
    b = base_url(env, pid, sid)
    run_job(env, post(b + "/process/track", {"quality": "preview"}), "process_track")
    full_on.add("fuse")
    run_job(env, post(b + "/process/fuse", {}), "process_fuse")
    st = get(b + "/process").json()["stages"]
    assert st["fuse"]["state"] == "done" and st["fuse"]["warnings"] == [user_words(POOL)], st["fuse"]
    assert st["track"]["warnings"] == [user_words(PATH)]  # the path's warning stays under Follow the camera only
    for path in (scan_dir(env, pid, sid) / "process" / "fuse" / "report.json",
                 scan_dir(env, pid, sid) / "result" / "report.json"):
        assert read(path)["warnings"] == [PATH, POOL], path  # the report keeps both, as before
    assert card(env, pid, sid)["result"]["report"]["warnings"] == [user_words(PATH), user_words(POOL)]
    # again, with room: nothing of the earlier run is left on the stage
    full_on.clear()
    run_job(env, post(b + "/process/fuse", {}), "process_fuse")
    assert get(b + "/process").json()["stages"]["fuse"]["warnings"] == []


def test_a_fuse_that_fills_its_pool_as_the_tracks_did_says_so_under_its_own_stage_too(env, full_on):
    """Build the surface at the same poses and mesh detail fills its pool exactly as Follow the camera's fuse did: the
    same sentence, word for word. It is that fuse's own warning, so its stage says it too: a stage's own warnings are
    told by where they came from, not by their words."""
    full_on.update({"track", "fuse"})
    pid, sid = add_scan(env, "Both full", env["rec"])
    b = base_url(env, pid, sid)
    run_job(env, post(b + "/process/track", {"quality": "preview"}), "process_track")
    run_job(env, post(b + "/process/fuse", {}), "process_fuse")
    st = get(b + "/process").json()["stages"]
    assert st["track"]["warnings"] == [user_words(POOL)]
    assert st["fuse"]["state"] == "done" and st["fuse"]["warnings"] == [user_words(POOL)], st["fuse"]
    # the report says it once
    assert read(scan_dir(env, pid, sid) / "process" / "fuse" / "report.json")["warnings"] == [POOL]


def test_without_warnings_the_stages_say_nothing(env):
    pid, sid = add_scan(env, "Quiet", env["rec"])
    b = base_url(env, pid, sid)
    run_job(env, post(b + "/process/track", {"quality": "preview"}), "process_track")
    run_job(env, post(b + "/process/fuse", {}), "process_fuse")
    run_job(env, post(b + "/process/straighten", {}), "process_straighten")
    st = get(b + "/process").json()["stages"]
    assert st["fuse"]["state"] == "done" and st["fuse"]["warnings"] == []
    assert st["straighten"]["state"] == "skipped" and st["straighten"]["warnings"] == []


# --------------------------------------------------------------------------- Straighten
def test_straighten_says_its_own_fuses_warning_once(env, monkeypatch, full_on):
    """Straighten fused again at the poses the laser crossings moved, and that fuse's pool was full: the stage says
    the pool's sentence; the crossings' sentence stays at the end of its summary only, the path's under the track."""
    import ohs.track.curve_ba as CB
    from ohs import geometry as G

    words = CB.acted_warning(0.3)

    def shifted(rec_path, calib, trajectory, *, reference=None, params=None, progress=None, cancelled=None):
        poses = {int(i): np.asarray(T, float) @ G.make_se3(np.eye(3), [0.3, 0.0, 0.0]) for i, _, ok, T in trajectory if ok}
        return poses, {"changed": True, "reason": "adjusted", "crossings": 999, "frames": len(poses),
                       "holdout": {"ratio": 0.5}, "miss_mm": {"before": 0.2, "after": 0.1}, "moved_mm": 0.3,
                       "moved_deg": 0.0, "verdict": "moved", "warning": words}

    monkeypatch.setattr(CB, "laser_curve_adjustment", shifted)
    monkeypatch.setattr("ohs.app.process.check_path", _warn)
    pid, sid = add_scan(env, "Straighten full", env["rec"])
    b = base_url(env, pid, sid)
    run_job(env, post(b + "/process/track", {"quality": "preview"}), "process_track")
    full_on.add("straighten")
    run_job(env, post(b + "/process/straighten", {}), "process_straighten")
    stt = get(b + "/process").json()["stages"]["straighten"]
    assert stt["state"] == "done" and stt["summary"].endswith(words), stt
    assert stt["warnings"] == [user_words(POOL)], stt
    res = read(scan_dir(env, pid, sid) / "result" / "report.json")
    assert res["warnings"] == [PATH, POOL, words]
    assert user_words(POOL) in card(env, pid, sid)["result"]["report"]["warnings"]


# --------------------------------------------------------------------------- Make only the thin walls finer
def _fine_walls(monkeypatch, *, acted: bool, cap: dict | None) -> None:
    """The graphics card's step is said to be there; the engine's step returns ``cap`` as its finer model's pool (and,
    when ``acted``, the base's mesh as the one it made finer, so that its stage becomes the base)."""
    import ohs.app.fine_walls as FW
    import ohs.server.processing as SP

    monkeypatch.setattr(SP, "images_on_gpu", lambda: True)

    def fake(stage_dir, spool_dir, *a, out_dir=None, **kw):
        rep = {"version": 1, "fine_voxel_mm": 0.5, "acted": acted, "voxel_mm": 1.0, "share": 0.03 if acted else None,
               "walls": {"found": True, "n_regions": 2}, "frames": {"kept": 10, "fused": 10},
               "mesh_capacity": None if cap is None else dict(cap)}
        if acted:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(stage_dir) / "mesh.ply", Path(out_dir) / "mesh.ply")
        else:
            rep["why"] = "no_finer_surface"
        return rep

    monkeypatch.setattr(FW, "make_fine_walls", fake)


def _make(env: dict, name: str) -> tuple[str, str, dict]:
    env["cfg"].process.voxel_mm = 1.0  # the step's mesh detail
    pid, sid = add_scan(env, name, env["rec"])
    b = base_url(env, pid, sid)
    r = post(b + "/process", {"quality": "standard", "fine_walls": True})
    assert r.status_code == 200, r.text
    done = wait_job(env, r.json()["id"], timeout=900.0)
    assert done["state"] == "done", done
    return pid, sid, get(b + "/process").json()


@pytest.mark.parametrize("acted", [True, False])
def test_the_thin_walls_say_when_their_finer_model_ran_out_of_room(env, monkeypatch, acted):
    _fine_walls(monkeypatch, acted=acted, cap=FCAP)
    pid, sid, s = _make(env, f"Thin walls full {acted}")
    said = user_words(FINE)
    fw = s["stages"]["fine_walls"]
    assert fw["state"] == ("done" if acted else "skipped") and fw["warnings"] == [said], fw
    made = s["made"]["fine_walls"]
    assert made["acted"] is acted and made["warnings"] == [said], made
    res = read(scan_dir(env, pid, sid) / "result" / "report.json")
    assert res["warnings"].count(FINE) == 1 and res["fine_walls"]["warnings"] == [FINE], res.get("warnings")
    assert res["fine_walls"]["mesh_capacity"] == FCAP
    if acted:  # the stage's own report, which the base's and Refine with lasers' copy
        assert read(scan_dir(env, pid, sid) / "process" / "fine_walls" / "report.json")["warnings"].count(FINE) == 1
    assert said in card(env, pid, sid)["result"]["report"]["warnings"]
    # the other stages do not say it
    assert all(said not in (s["stages"][n].get("warnings") or []) for n in ("track", "straighten", "fuse", "refine"))


@pytest.mark.parametrize("cap", [None, {**FCAP, "full": False, "bricks_refused": 0, "first_refused_frame": None}])
def test_the_thin_walls_say_nothing_when_their_finer_model_had_room(env, monkeypatch, cap):
    _fine_walls(monkeypatch, acted=True, cap=cap)
    pid, sid, s = _make(env, f"Thin walls room {cap is None}")
    fw = s["stages"]["fine_walls"]
    assert fw["state"] == "done" and fw["warnings"] == [], fw
    assert s["made"]["fine_walls"]["warnings"] == []
    res = read(scan_dir(env, pid, sid) / "result" / "report.json")
    assert not any("finer model" in w for w in res.get("warnings") or [])
    assert res["fine_walls"]["warnings"] == []
