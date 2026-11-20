"""Presets in the app (FEATURE_PLAN P2; APP_API.md "Presets"), through the real server.

The guards, on a 12-frame 640x400 sphere recording:

* **General is today:** Make mesh with General gives the track stage's mesh of a scan made before presets,
  bit for bit as content (each vertex's values and every face, ``test_server_keep_thin.mesh_content``),
  and both equal the engine run directly with today's settings (``process_params``); every stage gets the
  very settings it got before presets (the same ``ProcessParams``);
* **a preset's values reach the pipeline:** "Thin sheet metal and small parts" makes the mesh at 0.5 mm
  voxels, in every stage of Make mesh; a user's preset's quality, thin walls and recipe act too;
* **a value changed by hand wins** over the preset, is recorded as changed (the scan's entry and the
  stage's ``report.json``), and set back to the preset's value it is no change;
* a scan made before presets, and one whose preset file is gone, open and process as General, and say so;
* the scan's start writes the preset and every value it resolved to into the recording's ``meta.json``;
* the routes: list, save and delete the user's presets, refusals in plain words.

Run on the GPU path and with ``OHS_COMPUTE=cpu``.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import shutil
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

from ohs.app.config import Config  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.demo import DemoParams, demo_path, generate_demo  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402
from recordings import scan_recording  # noqa: E402
from test_server_keep import err, get, post, run_job, scan_dir, scan_url  # noqa: E402
from test_server_keep_thin import mesh_content, same_mesh  # noqa: E402

THIN = "thin-sheet-small-parts"


# --------------------------------------------------------------------------- helpers
def put(url: str, body: dict | None = None):
    return requests.put(url, json=body, timeout=60)


def delete(url: str):
    return requests.delete(url, timeout=60)


def new_project(env: dict, name: str) -> str:
    return post(env["url"] + "/api/projects", {"name": name}).json()["id"]


def add_recorded(env: dict, pid: str, name: str) -> str:
    """A recorded scan as one made before presets: its entry has no preset."""
    st = env["studio"]
    scan, rec_dir = st.projects.add_scan(pid, name, "markerless")
    shutil.copytree(env["rec"], rec_dir)
    st.projects.update_scan(pid, scan["id"], status="recorded", n_frames=len(RecordingReader(rec_dir)), duration_s=0.4)
    return scan["id"]


def preset_of(env: dict, pid: str, sid: str) -> dict:
    r = get(scan_url(env, pid, sid) + "/preset")
    assert r.status_code == 200, r.text
    return r.json()


def track_report(env: dict, pid: str, sid: str) -> dict:
    return json.loads((scan_dir(env, pid, sid) / "process" / "track" / "report.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("presets")
    rec = scan_recording(root / "rec", n_frames=12, fps=30.0, image_size=(640, 400), seed=3, depth_every=0)
    generate_demo(demo_path(root / "demo"), DemoParams(n_frames=12, image_size=(640, 400)))
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        yield {"srv": srv, "url": srv.url, "root": root, "studio": app.state.studio, "rec": rec, "cfg": cfg}


@pytest.fixture
def calls(monkeypatch):
    """Every ``process_recording`` a stage runs, with the settings it was given (the real one runs)."""
    import ohs.app.process as P

    real = P.process_recording
    seen: list[dict] = []

    def spy(rec, out, calib=None, params=None, progress=None, poses=None, **kw):
        seen.append({"params": copy.deepcopy(params), "given_poses": poses is not None})
        return real(rec, out, calib, params=params, progress=progress, poses=poses, **kw)

    monkeypatch.setattr(P, "process_recording", spy)
    return seen


def todays_params(env: dict, quality: str = "standard", width: int = 640):
    """What a stage got before presets: ``process_params`` of the config, the base keeping every piece, PLY."""
    from ohs.server.studio import process_params

    p = process_params(env["cfg"], "markerless", quality, width)
    return dataclasses.replace(p, clean_min_component=0.0, export=("ply",))


# --------------------------------------------------------------------------- General is today
def test_general_is_todays_mesh_bit_for_bit(env, calls, tmp_path):
    pid = new_project(env, "General")
    before = add_recorded(env, pid, "Made before presets")
    general = add_recorded(env, pid, "General")
    v = preset_of(env, pid, before)
    assert v["id"] == "general" and v["name"] == "General" and v["before_presets"] is True
    assert "before presets" in v["note"] and "General" in v["note"]
    r = put(scan_url(env, pid, general) + "/preset", {"preset_id": "general"})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "general" and r.json()["before_presets"] is False and r.json()["changed"] == []
    for sid in (before, general):
        calls.clear()
        run_job(env, post(scan_url(env, pid, sid) + "/process", {"quality": "standard"}), "process_scan")
        assert calls, "no stage ran"
        for c in calls:  # every stage (track, straighten's fuses) got exactly today's settings
            assert c["params"] == todays_params(env), c
    # the engine run directly with today's settings: the same mesh, every value bit for bit
    from ohs.app.process import process_recording

    direct = tmp_path / "direct"
    process_recording(env["rec"], direct, params=todays_params(env))
    a = scan_dir(env, pid, before) / "process" / "track" / "mesh.ply"
    b = scan_dir(env, pid, general) / "process" / "track" / "mesh.ply"
    same_mesh(a, b)
    same_mesh(a, direct / "mesh.ply")
    # the report says which preset made it, and every value it was made with
    rep = track_report(env, pid, general)["preset"]
    assert rep["id"] == "general" and rep["changed"] == [] and rep["hand"] == {}
    assert rep["used"] == {"mode": "markerless", "quality": "standard", "voxel_mm": 2.0, "thin_walls": "auto",
                           "recipe": "default", "check_tolerance_mm": 0.3}
    old = track_report(env, pid, before)["preset"]
    assert old["id"] == "general" and "before presets" in old["note"]
    res = json.loads((scan_dir(env, pid, general) / "result" / "report.json").read_text(encoding="utf-8"))
    assert res["preset"]["id"] == "general"


# --------------------------------------------------------------------------- a preset's values reach the pipeline
def test_thin_preset_makes_the_mesh_at_half_a_millimetre(env, calls):
    pid = new_project(env, "Thin")
    sid = add_recorded(env, pid, "Thin")
    gen_sid = add_recorded(env, pid, "General")
    r = put(scan_url(env, pid, sid) + "/preset", {"preset_id": THIN})
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["name"] == "Thin sheet metal and small parts" and v["preset_values"] == {"voxel_mm": 0.5}
    assert v["values"]["voxel_mm"] == 0.5 and v["changed"] == [] and v["general"]["voxel_mm"] == 2.0
    run_job(env, post(scan_url(env, pid, sid) + "/process", {"quality": "standard"}), "process_scan")
    assert calls and all(c["params"].pipeline.fusion.voxel_mm == 0.5 for c in calls)
    # nothing else moved: the settings are today's but for the voxel
    want = todays_params(env)
    for c in calls:
        p = c["params"]
        assert dataclasses.replace(p, pipeline=dataclasses.replace(p.pipeline, fusion=None)) == want
    rep = track_report(env, pid, sid)
    assert rep["mesh"]["voxel_mm"] == 0.5 and rep["preset"]["id"] == THIN and rep["preset"]["used"]["voxel_mm"] == 0.5
    state = get(scan_url(env, pid, sid) + "/process").json()
    assert state["stages"]["track"]["voxel_mm"] == 0.5
    # finer than General's mesh of the same recording
    run_job(env, post(scan_url(env, pid, gen_sid) + "/process", {"quality": "standard"}), "process_scan")
    n_thin = len(mesh_content(scan_dir(env, pid, sid) / "process" / "track" / "mesh.ply")[0])
    n_gen = len(mesh_content(scan_dir(env, pid, gen_sid) / "process" / "track" / "mesh.ply")[0])
    assert n_thin > 3 * n_gen
    # Fuse again without a voxel: the scan's own (the track's) voxel, as before presets
    calls.clear()
    run_job(env, post(scan_url(env, pid, sid) + "/process/fuse", {}), "process_fuse")
    assert [c["params"].pipeline.fusion.voxel_mm for c in calls] == [0.5]


def test_a_value_changed_by_hand_wins_and_is_recorded(env, calls):
    pid = new_project(env, "Hand")
    sid = add_recorded(env, pid, "Hand")
    b = scan_url(env, pid, sid)
    assert put(b + "/preset", {"preset_id": THIN}).status_code == 200
    v = put(b + "/preset", {"values": {"voxel_mm": 0.75, "thin_walls": "on"}}).json()
    assert v["id"] == THIN and v["hand"] == {"voxel_mm": 0.75, "thin_walls": "on"}
    assert v["changed"] == ["voxel_mm", "thin_walls"] and v["values"]["voxel_mm"] == 0.75
    entry = env["studio"].projects.scan_entry(pid, sid)
    assert entry["preset"] == {"id": THIN, "name": "Thin sheet metal and small parts",
                               "hand": {"voxel_mm": 0.75, "thin_walls": "on"}}
    run_job(env, post(b + "/process/track", {"quality": "preview"}), "process_track")
    (c,) = calls
    assert c["params"].pipeline.fusion.voxel_mm == 0.75 and c["params"].pipeline.thin_walls == "on"
    rep = track_report(env, pid, sid)
    assert rep["mesh"]["voxel_mm"] == 0.75 and rep["params"]["reconstruction"]["thin_walls"] == "on"
    assert rep["preset"]["changed"] == ["voxel_mm", "thin_walls"] and rep["preset"]["preset_values"] == {"voxel_mm": 0.5}
    assert rep["preset"]["used"]["voxel_mm"] == 0.75 and rep["preset"]["used"]["thin_walls"] == "on"
    assert rep["preset"]["used"]["quality"] == "preview"
    # back to the preset's value: no change by hand any more; the preset's own value is no change either
    v = put(b + "/preset", {"values": {"voxel_mm": None, "thin_walls": None}}).json()
    assert v["hand"] == {} and v["changed"] == [] and v["values"]["voxel_mm"] == 0.5
    v = put(b + "/preset", {"values": {"voxel_mm": 0.5}}).json()
    assert v["hand"] == {} and v["changed"] == []
    # another preset: its own values, the hand changes start over
    put(b + "/preset", {"values": {"voxel_mm": 1.5}})
    v = put(b + "/preset", {"preset_id": "general"}).json()
    assert v["id"] == "general" and v["hand"] == {} and v["values"]["voxel_mm"] is None
    # refusals, in plain words
    err(put(b + "/preset", {"values": {"mode": "markers"}}), 422, "tracking mode")
    err(put(b + "/preset", {"values": {"voxel_mm": 0.1}}), 422, "at least 0.25 mm")
    err(put(b + "/preset", {"values": {"depth_range": [100, 600]}}), 422, "depth range")
    err(put(b + "/preset", {"preset_id": "nope-0000"}), 404, "no preset")
    err(put(scan_url(env, pid, "scan-nope") + "/preset", {"preset_id": THIN}), 404, "no such scan")


# --------------------------------------------------------------------------- the user's presets
def test_the_users_presets_routes_and_a_preset_that_is_gone(env, calls):
    url = env["url"]
    listed = get(url + "/api/presets").json()
    assert [p["id"] for p in listed][:2] == ["general", THIN] and listed[1]["card"]["why"]
    r = post(url + "/api/presets", {"name": "Quick look", "description": "Preview, thin walls on, the bracket recipe",
                                    "values": {"quality": "preview", "thin_walls": "on", "recipe": "bracket",
                                               "check_tolerance_mm": 0.5}})
    assert r.status_code == 200, r.text
    mine = r.json()
    assert mine["builtin"] is False and mine["values"] == {"quality": "preview", "thin_walls": "on", "recipe": "bracket",
                                                           "check_tolerance_mm": 0.5}
    assert (env["root"] / "data" / "presets" / f"{mine['id']}.json").is_file()
    assert mine["id"] in [p["id"] for p in get(url + "/api/presets").json()]
    err(post(url + "/api/presets", {"name": "Odd", "values": {"colour": "red"}}), 422, "no value called \u201ccolour\u201d")
    err(post(url + "/api/presets", {"name": "Odd", "values": {"voxel_mm": 50}}), 422, "at most 10 mm")
    err(post(url + "/api/presets", {"name": "Odd", "values": {"recipe": "nope-0000"}}), 422, "no recipe")
    err(post(url + "/api/presets", {"name": "General", "values": {}}), 409, "built-in")
    err(delete(url + f"/api/presets/{THIN}"), 409, "built-in")
    err(delete(url + "/api/presets/nope-0000"), 404, "no preset")

    # its values act: Make mesh without a quality takes the preset's, its recipe runs, thin walls on
    pid = new_project(env, "Mine")
    sid = add_recorded(env, pid, "Mine")
    b = scan_url(env, pid, sid)
    assert put(b + "/preset", {"preset_id": mine["id"]}).json()["values"]["recipe"] == "bracket"
    run_job(env, post(b + "/process"), "process_scan")
    assert calls and all(c["params"].pipeline.thin_walls == "on" and c["params"].pipeline.scale == 0.5 for c in calls)
    state = get(b + "/process").json()
    assert state["stages"]["track"]["quality"] == "preview"
    assert any(e["op"] == "smooth" for e in state["edits"])  # the bracket recipe's light smoothing
    assert track_report(env, pid, sid)["preset"]["used"]["recipe"] == "bracket"
    # a quality asked for by hand still wins (the Make mesh buttons are a choice)
    calls.clear()
    run_job(env, post(b + "/process/track", {"quality": "standard"}), "process_track")
    assert calls[0]["params"].pipeline.scale == 1.0

    # the preset's file goes: the scan opens and processes as General, and says so; its own hand change stays
    put(b + "/preset", {"values": {"voxel_mm": 1.5}})
    assert delete(url + f"/api/presets/{mine['id']}").status_code == 204
    v = preset_of(env, pid, sid)
    assert v["id"] == mine["id"] and v["name"] == "Quick look" and v["missing"] is True
    assert "no longer in the app" in v["note"] and v["preset_values"] == {}
    assert v["values"]["recipe"] is None and v["values"]["voxel_mm"] == 1.5
    calls.clear()
    run_job(env, post(b + "/process"), "process_scan")
    want = todays_params(env)
    for c in calls:
        p = c["params"]
        assert p.pipeline.fusion.voxel_mm == 1.5
        assert dataclasses.replace(p, pipeline=dataclasses.replace(p.pipeline, fusion=None)) == want
    state = get(b + "/process").json()
    assert not any(e["op"] == "smooth" for e in state["edits"])  # the Default recipe, General's
    assert "no longer in the app" in track_report(env, pid, sid)["preset"]["note"]


# --------------------------------------------------------------------------- the scan's start
def test_the_start_writes_the_preset_into_the_recording(env):
    url = env["url"]
    r = post(url + "/api/device/connect", {"source": "sim"})
    assert r.status_code == 200, r.text
    try:
        pid = new_project(env, "Start")
        err(post(url + "/api/scan/start", {"project_id": pid, "preset": "nope-0000"}), 404, "no preset")
        err(post(url + "/api/scan/start", {"project_id": pid, "preset": THIN, "preset_values": {"voxel_mm": 99}}),
            422, "at most 10 mm")
        assert get(url + f"/api/projects/{pid}").json()["scans"] == []  # a refused start leaves no scan
        r = post(url + "/api/scan/start", {"project_id": pid, "mode": "markerless", "preset": THIN,
                                           "preset_values": {"thin_walls": "on"}})
        assert r.status_code == 200, r.text
        deadline = time.monotonic() + 30
        while get(url + "/api/scan").json()["frames"] < 3 and time.monotonic() < deadline:
            time.sleep(0.1)
    finally:
        if get(url + "/api/scan").json()["state"] != "idle":
            post(url + "/api/scan/stop")
        post(url + "/api/device/disconnect")
    scan = get(url + f"/api/projects/{pid}").json()["scans"][0]
    assert scan["preset"] == {"id": THIN, "name": "Thin sheet metal and small parts", "hand": {"thin_walls": "on"}}
    meta = json.loads((scan_dir(env, pid, scan["id"]) / "recording" / "meta.json").read_text(encoding="utf-8"))
    p = meta["preset"]
    assert p["id"] == THIN and p["changed"] == ["thin_walls"] and p["preset_values"] == {"voxel_mm": 0.5}
    assert p["values"] == {"mode": "markerless", "quality": None, "voxel_mm": 0.5, "thin_walls": "on", "recipe": None,
                           "check_tolerance_mm": None}
    v = preset_of(env, pid, scan["id"])
    assert v["changed"] == ["thin_walls"] and v["values"]["voxel_mm"] == 0.5


# --------------------------------------------------------------------------- the report
def test_the_report_names_the_preset():
    from ohs.report.html import build_html

    data = {"project": {"name": "P"}, "scan": {"name": "S"}, "app_version": "x", "date": "2026-11-20",
            "process": {"mode": "markerless", "quality": "standard",
                        "preset": {"id": THIN, "name": "Thin sheet metal and small parts", "changed": ["voxel_mm"],
                                   "preset_values": {"voxel_mm": 0.5}, "hand": {"voxel_mm": 0.75},
                                   "used": {"voxel_mm": 0.75}, "note": None}}}
    html = build_html(data)
    assert "Thin sheet metal and small parts" in html and "changed by hand" in html and "0.75 mm" in html
    old = build_html({**data, "process": {"mode": "markerless", "quality": "standard"}})
    assert "Preset" not in old  # a scan made before presets: the report as it was
