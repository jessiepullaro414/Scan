""""Make only the thin walls finer" in the app's Make mesh (ENGINE_PLAN X5 step 2, a choice per scan), through the real
server.

* **Off (no choice, ``[process] fine_walls`` false):** Make mesh is as before: no depth is kept by any pass, the
  stages are the five of before, the plan of the percentage has no part for it, ``made`` and the report say nothing of
  it, and the mesh is the one made with the choice stored false.
* **The choice** (``POST .../process {fine_walls}``) is stored in the scan's entry and used by every later Make mesh
  and recipe on everything; absent, the stored one stays; ``[process] fine_walls`` is the default for scans without one.
* **On, on the graphics card's path, at mesh detail 1 mm:** Follow the camera keeps the depth (Straighten's fuses do
  not), the step runs after Straighten with its own part of the percentage, its mesh becomes the base and Refine with
  lasers refines it; the stage, ``made.fine_walls`` and ``report.json`` say what it did; the kept depth is gone after,
  and the hint does not suggest again (``walls_made_finer``). Then ``fine_walls: false`` makes the mesh as before.
* **On, but it cannot or need not act:** no thin walls (nothing changes), no graphics card, another mesh detail,
  Preview quality, too little disk space (the margin is tested on both sides): each says why, and nothing is kept
  where it cannot run.
* **It never costs the scan:** a failure in the step leaves the mesh as fused and says so; a cancel in it leaves the
  scan as it was; the kept depth is removed in every case, and a stale one from a stopped run at the next Follow the
  camera.
* **The hint** offers "Make only the thin walls finer" first, with its measured gain and cost, only on the graphics
  card's path at 1 mm; elsewhere only the preset, as before.

The 10-frame recording (640 x 400) is fused at mesh detail 1 mm here (``[process] voxel_mm``), the step's own; it has
no thin wall, so where walls are needed the engine's wall finding is handed some (the rest runs for real). Run on the
GPU path and with ``OHS_COMPUTE=cpu`` (where the parts that need a graphics card are told one is there, or skipped).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

from ohs.app.config import Config  # noqa: E402
from ohs.mesh import thin_parts as TP  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402
from ohs.track.export import load_ply  # noqa: E402
from recordings import scan_recording  # noqa: E402
from test_server_make_mesh_images import same_mesh  # noqa: E402
from test_server_process_studio import add_scan, base_url, get, post, wait_job  # noqa: E402

FIVE = {"track", "straighten", "fuse", "spacetime", "refine"}


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("finewalls")
    rec = scan_recording(root / "rec", n_frames=10, image_size=(640, 400), seed=3, depth_every=0)
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    cfg.process.voxel_mm = 1.0  # the step's mesh detail (these small recordings fuse at 2 mm by default)
    cfg.process.repeatable_edits = True  # the same mesh twice (as_made_without)
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        st = app.state.studio
        yield {"srv": srv, "url": srv.url, "root": root, "studio": st, "rec": rec, "cfg": st.cfg}


@pytest.fixture(autouse=True)
def settings(env):
    """Each test starts from the module's settings and leaves them so."""
    env["cfg"].process.voxel_mm, env["cfg"].process.fine_walls = 1.0, False
    yield
    env["cfg"].process.voxel_mm, env["cfg"].process.fine_walls = 1.0, False


@pytest.fixture(autouse=True)
def plans(monkeypatch) -> list:
    """Every plan of the percentage a Make mesh makes."""
    import ohs.server.processing as SP

    seen: list = []
    real = SP.make_mesh_plan

    def spy(**kw):
        out = real(**kw)
        seen.append([{"stage": k, "from": round(a, 4), "to": round(b, 4)} for k, (a, b) in out.items()])
        return out

    monkeypatch.setattr(SP, "make_mesh_plan", spy)
    return seen


@pytest.fixture(autouse=True)
def passes(monkeypatch) -> list:
    """Every pass over the frames the server runs: ``(its folder's name, whether it keeps the depth)``."""
    import ohs.app.process as P

    seen: list = []
    real = P.process_recording

    def spy(rec, out_dir, *a, params=None, **kw):
        seen.append((Path(out_dir).name.split(".tmp-")[0], bool(getattr(params, "fine_walls", False))))
        return real(rec, out_dir, *a, params=params, **kw)

    monkeypatch.setattr(P, "process_recording", spy)
    return seen


def on_gpu() -> bool:
    from ohs.server.processing import images_on_gpu

    return images_on_gpu()


def card(monkeypatch) -> None:
    """The step's own test of the graphics card says there is one (on the CPU path the pass then keeps nothing and the
    step says why: the plumbing around it runs as on the graphics card's path)."""
    import ohs.server.processing as SP

    monkeypatch.setattr(SP, "images_on_gpu", lambda: True)


def walls_found(monkeypatch) -> None:
    """The engine's wall finding (the step's and the hint's) says it found three walls around the mesh's middle."""
    def fake(V, F, **kw):
        V = np.asarray(V, dtype=np.float64)
        c = np.median(V, axis=0)
        wv = np.flatnonzero(np.linalg.norm(V - c, axis=1) < 12.0)
        return ({"found": True, "area_mm2": 1794.0, "share": 0.05, "reads_mm": 1.84, "reads_range_mm": [1.33, 2.54],
                 "n_regions": 3, "regions": []}, wv)

    monkeypatch.setattr(TP, "find_thin_walls", fake)


def as_made_without(a: dict, b: dict) -> bool:
    """The mesh ``b`` is the one Make mesh makes without the step (``a``): the same triangles at the same places. (The
    module's settings break the edits' ties by position, ``[process] repeatable_edits``: at mesh detail 1 mm the
    vertices come out of the model in an order of their own each run, and Fill holes would otherwise split a hole
    differently, on the code before this step too; checked 2026-11-22.)"""
    return same_mesh(a, b)


def scan_dir(env: dict, pid: str, sid: str) -> Path:
    return env["root"] / "projects" / pid / "scans" / sid


def kept(env: dict, pid: str, sid: str) -> list[Path]:
    return sorted(scan_dir(env, pid, sid).rglob("fused_depth"))


def result_report(env: dict, pid: str, sid: str) -> dict:
    return json.loads((scan_dir(env, pid, sid) / "result" / "report.json").read_text("utf-8"))


def result_mesh(env: dict, pid: str, sid: str) -> dict:
    return load_ply(scan_dir(env, pid, sid) / "result" / "mesh.ply")


def make(env: dict, plans: list, passes: list, pid: str | None = None, sid: str | None = None, *, name: str = "Scan",
         state: str = "done", **body) -> tuple[str, str, dict, list | None, dict]:
    """Make mesh (on a new scan unless one is given) with ``body`` (quality standard unless said); returns the ids, the
    process state, the plan it ran with and the job."""
    if pid is None:
        pid, sid = add_scan(env, name, env["rec"])
    b = base_url(env, pid, sid)
    plans.clear()
    passes.clear()
    r = post(b + "/process", {"quality": "standard", **body})
    assert r.status_code == 200, r.text
    done = wait_job(env, r.json()["id"], timeout=900.0)
    assert done["state"] == state, done
    return pid, sid, get(b + "/process").json(), (plans[0] if plans else None), done


def entry(env: dict, pid: str, sid: str) -> dict:
    return env["studio"].projects.scan_entry(pid, sid)


# --------------------------------------------------------------------------- off: as before
def test_off_by_default_make_mesh_keeps_no_depth_and_makes_the_same_mesh(env, plans, passes):
    from ohs.server.processing import MAKE_MESH_SHARES, make_mesh_plan

    assert Config().process.fine_walls is False
    for kw in ({"straighten": True, "refine": True, "edits": True}, {"straighten": False, "refine": False, "edits": True}):
        plan = make_mesh_plan(**kw)
        assert "fine_walls" not in plan and list(plan) == [k for k in MAKE_MESH_SHARES if kw.get(k, True)]
    pid, sid, s, plan, _ = make(env, plans, passes, name="Off")
    assert set(s["stages"]) == FIVE and (s["making"] is None)
    assert [p["stage"] for p in plan] == ["track", "straighten", "refine", "edits"]
    assert passes and not any(k for _, k in passes)  # no pass kept the depth
    assert "fine_walls" not in (s["made"] or {}) and "fine_walls" not in result_report(env, pid, sid)
    assert "fine_walls" not in entry(env, pid, sid) and kept(env, pid, sid) == []
    off = result_mesh(env, pid, sid)
    # the choice stored false: the same as no choice, part for part
    _, _, s2, plan2, _ = make(env, plans, passes, pid, sid, fine_walls=False)
    assert entry(env, pid, sid)["fine_walls"] is False and set(s2["stages"]) == FIVE
    assert [p["stage"] for p in plan2] == ["track", "straighten", "refine", "edits"]
    assert not any(k for _, k in passes) and "fine_walls" not in (s2["made"] or {}) and kept(env, pid, sid) == []
    assert as_made_without(off, result_mesh(env, pid, sid))


def test_the_plan_gives_the_step_its_part_after_straighten_only_when_it_runs():
    from ohs.server.processing import FINE_WALLS_SHARE, make_mesh_plan

    plan = make_mesh_plan(straighten=True, refine=True, edits=True, fine_walls=True)
    assert list(plan) == ["track", "straighten", "fine_walls", "refine", "edits"]
    assert plan["edits"][1] == 1.0 and all(plan[a][1] == plan[b][0] for a, b in zip(list(plan), list(plan)[1:]))
    a, b = plan["fine_walls"]
    assert b - a == pytest.approx(FINE_WALLS_SHARE / (1.0 + FINE_WALLS_SHARE), abs=1e-9)
    both = make_mesh_plan(straighten=False, refine=True, edits=False, spacetime=True, fine_walls=True)
    assert list(both) == ["track", "fine_walls", "spacetime", "refine"]


# --------------------------------------------------------------------------- the choice
def test_the_choice_is_stored_and_used_by_later_make_mesh_and_the_setting_is_the_default(env, plans, passes,
                                                                                         monkeypatch):
    card(monkeypatch)
    pid, sid, s, plan, _ = make(env, plans, passes, name="Chosen", fine_walls=True)
    assert entry(env, pid, sid)["fine_walls"] is True
    assert ("track", True) in passes and all(not k for n, k in passes if n != "track")  # Straighten's fuses keep none
    assert "fine_walls" in s["stages"] and s["made"]["fine_walls"]["chosen"] is True
    # a later Make mesh without the field: the stored choice
    _, _, s2, _, _ = make(env, plans, passes, pid, sid)
    assert ("track", True) in passes and entry(env, pid, sid)["fine_walls"] is True
    # a recipe on everything too
    passes.clear()
    r = post(base_url(env, pid, sid) + "/process/recipe", {"recipe_id": "default", "scope": "all"})
    assert r.status_code == 200, r.text
    assert wait_job(env, r.json()["id"], timeout=900.0)["state"] == "done"
    assert ("track", True) in passes and "fine_walls" in get(base_url(env, pid, sid) + "/process").json()["stages"]
    assert kept(env, pid, sid) == []
    # [process] fine_walls: the default for a scan without a choice; a scan's own false wins over it
    env["cfg"].process.fine_walls = True
    pid2, sid2, s3, _, _ = make(env, plans, passes, name="By the setting")
    assert ("track", True) in passes and "fine_walls" not in entry(env, pid2, sid2)
    _, _, s4, _, _ = make(env, plans, passes, pid2, sid2, fine_walls=False)
    assert not any(k for _, k in passes) and set(s4["stages"]) == FIVE and "fine_walls" not in (s4["made"] or {})


# --------------------------------------------------------------------------- on, acting
def test_on_the_graphics_card_the_thin_walls_are_made_finer_and_the_kept_depth_is_removed(env, plans, passes,
                                                                                         monkeypatch):
    import ohs.app.fine_walls as FW

    if not on_gpu():
        pytest.skip("the depth is kept on the graphics card's path only (the CPU path is tested below)")
    walls_found(monkeypatch)
    seen: dict = {}
    real = FW.make_fine_walls

    def watched(stage_dir, spool_dir, *a, **kw):
        seen["spool"] = Path(spool_dir).is_dir()
        seen["mb"] = FW.spool_size_mb(spool_dir)
        seen["frames"] = len((FW.read_spool(spool_dir) or {}).get("entries") or [])
        seen["stage"] = Path(stage_dir).name
        return real(stage_dir, spool_dir, *a, **kw)

    monkeypatch.setattr(FW, "make_fine_walls", watched)
    pid0, sid0, _, _, _ = make(env, plans, passes, name="Plain")
    pid, sid, s, plan, _ = make(env, plans, passes, name="Finer", fine_walls=True)
    track = json.loads((scan_dir(env, pid, sid) / "process" / "track" / "report.json").read_text("utf-8"))
    # Follow the camera kept the depth of every frame it processed, about 2 MB a frame at 640 x 400
    assert seen["spool"] and seen["frames"] == track["frames"]["processed"] == 10
    assert seen["mb"] == pytest.approx(10 * 640 * 400 * 8 / 2 ** 20, rel=1e-6)
    assert track["fine_walls_depth"]["kept"] is True and seen["stage"] in ("track", "straighten")
    assert [p["stage"] for p in plan] == ["track", "straighten", "fine_walls", "refine", "edits"]
    fw = s["stages"]["fine_walls"]
    assert fw["state"] == "done" and fw["source"] == seen["stage"] and fw["regions"] == 3 and fw["share_pct"] > 0
    assert fw["kept_mb"] == pytest.approx(seen["mb"], abs=0.1) and "made finer" in fw["summary"]
    # its mesh is the base Refine with lasers worked on
    rf = s["stages"]["refine"]
    assert (rf["source"] == "fine_walls") if rf["state"] == "done" else (s["base"]["stage"] == "fine_walls"), (rf, s["base"])
    made = s["made"]["fine_walls"]
    assert made["acted"] is True and made["why"] is None and made["regions"] == 3 and 0 < made["share"] <= 1
    assert made["seconds"] > 0 and made["kept_mb"] > 0 and made["action"] == "Make the mesh again without this"
    assert "made finer" in made["line"] and "report" not in made and s["made"]["stages"]["fine_walls"] > 0
    rep = result_report(env, pid, sid)["fine_walls"]
    assert rep["acted"] and rep["triangles_after"] > rep["triangles_before"] and rep["frames"]["fused"] >= 1
    assert len(result_mesh(env, pid, sid)["faces"]) > len(result_mesh(env, pid0, sid0)["faces"])
    assert kept(env, pid, sid) == []  # the kept depth is gone
    # the hint: not suggested again for walls made finer, and no thickness estimate
    hint = s["made"]["thin_parts"]
    assert hint["suggest"] is False and hint["why_not"] == "walls_made_finer" and hint["words"] is None
    assert result_report(env, pid, sid)["thin_parts"]["why_not"] == "walls_made_finer"
    # "Make the mesh again without this": as a mesh made without it
    _, _, s2, plan2, _ = make(env, plans, passes, pid, sid, fine_walls=False)
    assert set(s2["stages"]) == FIVE and "fine_walls" not in (s2["made"] or {}) and not any(k for _, k in passes)
    assert [p["stage"] for p in plan2] == ["track", "straighten", "refine", "edits"]
    assert as_made_without(result_mesh(env, pid0, sid0), result_mesh(env, pid, sid)) and kept(env, pid, sid) == []


def test_on_without_thin_walls_nothing_changes_and_it_says_so(env, plans, passes):
    if not on_gpu():
        pytest.skip("without a graphics card the step does not run (tested below)")
    pid0, sid0, s0, _, _ = make(env, plans, passes, name="Plain sphere")
    pid, sid, s, plan, _ = make(env, plans, passes, name="Sphere, on", fine_walls=True)
    assert "fine_walls" in [p["stage"] for p in plan] and ("track", True) in passes
    fw = s["stages"]["fine_walls"]
    assert fw["state"] == "skipped" and "No thin walls were found" in fw["summary"] and fw["why"] == "no_thin_walls"
    made = s["made"]["fine_walls"]
    assert made["acted"] is False and made["why"] == "no_thin_walls" and "No thin walls were found" in made["line"]
    assert "action" not in made and s["base"]["stage"] == s0["base"]["stage"]
    assert as_made_without(result_mesh(env, pid0, sid0), result_mesh(env, pid, sid)) and kept(env, pid, sid) == []
    assert result_report(env, pid, sid)["fine_walls"]["why"] == "no_thin_walls"


# --------------------------------------------------------------------------- on, but it cannot run
def test_without_a_graphics_card_it_is_skipped_and_nothing_is_kept(env, plans, passes, monkeypatch):
    import ohs.server.processing as SP

    pid0, sid0, _, _, _ = make(env, plans, passes, name="Plain, no card")
    monkeypatch.setattr(SP, "images_on_gpu", lambda: False)
    pid, sid, s, plan, _ = make(env, plans, passes, name="On, no card", fine_walls=True)
    assert not any(k for _, k in passes) and "fine_walls" not in [p["stage"] for p in plan]
    fw = s["stages"]["fine_walls"]
    assert fw["state"] == "skipped" and "graphics card" in fw["summary"] and s["made"]["fine_walls"]["why"] == "no_graphics_card"
    assert as_made_without(result_mesh(env, pid0, sid0), result_mesh(env, pid, sid)) and kept(env, pid, sid) == []


@pytest.mark.parametrize("case", ["detail", "preview"])
def test_outside_the_measured_mesh_detail_or_at_preview_it_is_skipped_and_nothing_is_kept(env, plans, passes,
                                                                                         monkeypatch, case):
    card(monkeypatch)
    if case == "detail":
        env["cfg"].process.voxel_mm = 2.0
        pid, sid, s, plan, _ = make(env, plans, passes, name="2 mm", fine_walls=True)
        why, words = "mesh_detail", ("mesh detail 1 mm only", "made at 2 mm")
    else:
        pid, sid, s, plan, _ = make(env, plans, passes, name="Preview", fine_walls=True, quality="preview")
        why, words = "preview", ("Preview quality",)
    assert not any(k for _, k in passes) and "fine_walls" not in [p["stage"] for p in plan]
    made = s["made"]["fine_walls"]
    assert made["why"] == why and all(w in made["line"] for w in words), made
    assert s["stages"]["fine_walls"]["state"] == "skipped" and kept(env, pid, sid) == []


def test_without_enough_disk_space_the_depth_is_not_kept_and_it_says_how_much_is_needed(env, plans, passes,
                                                                                      monkeypatch):
    import ohs.server.processing as SP
    from ohs.app.fine_walls import kept_depth_estimate

    card(monkeypatch)
    pid, sid = add_scan(env, "Little space", env["rec"])
    rec = scan_dir(env, pid, sid) / "recording"
    need = kept_depth_estimate(rec, SP.stage_params(env["cfg"], "markerless", "standard", 640))["bytes"] / 2 ** 20
    assert need == pytest.approx(10 * 640 * 400 * 8 / 2 ** 20)
    free = {"mb": need + SP.FINE_WALLS_DISK_MARGIN_MB - 1.0}
    monkeypatch.setattr(SP, "free_disk_mb", lambda path: free["mb"])
    _, _, s, plan, _ = make(env, plans, passes, pid, sid, fine_walls=True)
    assert not any(k for _, k in passes) and "fine_walls" not in [p["stage"] for p in plan]
    made = s["made"]["fine_walls"]
    assert made["why"] == "disk" and made["need_mb"] == pytest.approx(need, abs=0.1)
    assert "of free disk space" in made["line"] and "2.0 GB more" in made["line"] and "Free some space" in made["line"]
    assert s["stages"]["fine_walls"]["state"] == "skipped" and kept(env, pid, sid) == []
    free["mb"] = need + SP.FINE_WALLS_DISK_MARGIN_MB  # just enough: kept
    _, _, s2, plan2, _ = make(env, plans, passes, pid, sid)
    assert ("track", True) in passes and "fine_walls" in [p["stage"] for p in plan2]
    assert s2["made"]["fine_walls"]["why"] != "disk" and kept(env, pid, sid) == []


# --------------------------------------------------------------------------- it never costs the scan
def test_a_failure_in_the_step_leaves_the_mesh_as_fused_and_says_so(env, plans, passes, monkeypatch):
    import ohs.app.fine_walls as FW

    pid0, sid0, _, _, _ = make(env, plans, passes, name="Plain, for the failure")
    card(monkeypatch)

    def broken(*a, **k):
        raise RuntimeError("a program error with words the page must not show")

    monkeypatch.setattr(FW, "make_fine_walls", broken)
    pid, sid, s, _, _ = make(env, plans, passes, name="Failing", fine_walls=True)
    fw = s["stages"]["fine_walls"]
    assert fw["state"] == "failed" and "could not finish" in fw["summary"] and "program error" not in fw["summary"]
    made = s["made"]["fine_walls"]
    assert made["why"] == "failed" and made["acted"] is False and "program error" not in json.dumps(s)
    assert as_made_without(result_mesh(env, pid0, sid0), result_mesh(env, pid, sid)) and kept(env, pid, sid) == []


def test_a_cancel_in_the_step_leaves_the_scan_as_it_was_and_no_depth(env, plans, passes, monkeypatch):
    import ohs.app.fine_walls as FW

    card(monkeypatch)
    pid, sid = add_scan(env, "Cancelled", env["rec"])
    job: dict = {}
    got = threading.Event()

    def held(stage_dir, spool_dir, *a, cancelled=None, **k):
        got.set()
        deadline = time.monotonic() + 60
        while not job.get("id") and time.monotonic() < deadline:
            time.sleep(0.05)
        requests.post(f"{env['url']}/api/jobs/{job['id']}/cancel", timeout=30)
        while not cancelled() and time.monotonic() < deadline:
            time.sleep(0.05)
        raise InterruptedError("cancelled")

    monkeypatch.setattr(FW, "make_fine_walls", held)
    r = post(base_url(env, pid, sid) + "/process", {"quality": "standard", "fine_walls": True})
    assert r.status_code == 200, r.text
    job["id"] = r.json()["id"]
    done = wait_job(env, job["id"], timeout=900.0)
    assert got.is_set() and done["state"] == "cancelled", done
    assert kept(env, pid, sid) == [] and not (scan_dir(env, pid, sid) / "result" / "mesh.ply").exists()
    assert not list(scan_dir(env, pid, sid).glob(".remake-*"))


def test_kept_depth_left_by_a_stopped_run_is_removed_at_the_next_follow_the_camera(env, plans, passes):
    """A run stopped on the way (a power cut) may leave kept depth in the stage folder or in a pass's folder
    (``track.tmp-<job>``, which no later pass replaces): both go at the next Follow the camera of the scan, alone or in
    Make mesh. (An interrupted Make mesh's own folder, ``.remake-*``, goes at the next start, as before.)"""
    pid, sid, _, _, _ = make(env, plans, passes, name="Stale")

    def stale() -> list[Path]:
        out = []
        for d in (scan_dir(env, pid, sid) / "process" / "track" / "fused_depth",
                  scan_dir(env, pid, sid) / "process" / "track.tmp-j-stopped" / "fused_depth"):
            d.mkdir(parents=True, exist_ok=True)
            (d / "000000.bin").write_bytes(b"\0" * 1024)
            out.append(d)
        return out

    ds = stale()
    r = post(base_url(env, pid, sid) + "/process/track", {})  # Follow the camera alone
    assert r.status_code == 200, r.text
    assert wait_job(env, r.json()["id"], timeout=900.0)["state"] == "done"
    assert not any(d.exists() for d in ds) and kept(env, pid, sid) == []
    ds = stale()
    make(env, plans, passes, pid, sid)  # Make mesh without the choice
    assert not any(d.exists() for d in ds) and kept(env, pid, sid) == []


# --------------------------------------------------------------------------- the hint
def test_the_hint_offers_making_only_the_thin_walls_finer_first_where_it_can_run(env, plans, passes, monkeypatch):
    real = TP.find_thin_parts

    def found(V, F, **kw):
        f = real(V, F, **kw)
        f.update(found=True, suggest=not kw.get("walls_made_finer"), why_not=None,
                 walls={"found": True, "area_mm2": 1794.0, "share": 0.0051, "reads_mm": 1.84,
                        "reads_range_mm": [1.33, 2.54], "estimate_mm": [0.5, 1.7], "n_regions": 3, "regions": []})
        return f

    monkeypatch.setattr(TP, "find_thin_parts", found)
    pid, sid, s, _, _ = make(env, plans, passes, name="Hint")
    w = s["made"]["thin_parts"]["words"]
    assert w["action"] == "Make the mesh again with this preset" and w["preset_id"] == TP.THIN_PRESET_ID  # unchanged
    if on_gpu():
        fw = w["fine"]
        assert fw["action"] == "Make only the thin walls finer"
        text = " ".join([fw["intro"], *fw["gain"], *fw["cost"]])
        secs = s["made"]["seconds"]
        assert "0.04 to 0.52 mm nearer" in text and "about 1.2 to 1.3 times" in text
        assert (f"This mesh took {secs:.0f} s" in text) if secs < 90 else ("This mesh took" in text)
        assert "10 frames" in text and fw["disk_mb"] == pytest.approx(10 * 640 * 400 * 8 / 2 ** 20, abs=0.1)
    else:
        assert w["fine"] is None  # without a graphics card only the preset, as before
    env["cfg"].process.voxel_mm = 2.0  # outside the measured mesh detail: only the preset
    _, _, s2, _, _ = make(env, plans, passes, pid, sid)
    assert s2["made"]["thin_parts"]["words"]["fine"] is None
    assert s2["made"]["thin_parts"]["words"]["action"] == "Make the mesh again with this preset"
