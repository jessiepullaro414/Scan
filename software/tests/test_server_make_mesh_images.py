"""``[process] refine_from_images``: "Refine from the images" inside Make mesh (ENGINE_PLAN X6, a setting that is off
by default), through the real server.

* **Off (the default):** Make mesh runs as before: the stage "Refine from the images" stays ``none``, the plan of the
  percentage has no part for it, and the report says nothing of it.
* **On, with a graphics card:** Make mesh runs the stage between Straighten and Refine with lasers: the stage is
  ``done`` from the track's mesh, the lasers refine its surface (their source is ``spacetime``), the plan has its part
  in that order, and the result's report says what was refined and what was left (patches kept and rejected, the share
  covered, the vertices moved and left, and what the guard left as it was).
* **On, without a graphics card** (``OHS_COMPUTE=cpu``): the stage is ``skipped`` in plain words, the mesh is the one
  Make mesh makes with the setting off, and the log says so.
* **Not at Preview quality.** The plan of the percentage is unchanged when the setting is off.
* **A graphics driver reset during the search** (its self-check fails): the mesh edit fails in plain words and
  Make mesh says so in the stage; nothing is written, the mesh stays as it was.

The 10-frame recording is fewer views than a patch needs by default, so the stage's parameters are made lighter here
(this checks the plumbing, not the search). Run on the GPU path and with ``OHS_COMPUTE=cpu``.
"""
from __future__ import annotations

import dataclasses
import json
import logging

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

from ohs.app.config import Config  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402
from ohs.track.export import load_ply  # noqa: E402
from recordings import scan_recording  # noqa: E402
from test_server_process_studio import add_scan, base_url, get, post, wait_job  # noqa: E402


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("images")
    rec = scan_recording(root / "rec", n_frames=10, image_size=(640, 400), seed=3, depth_every=0)
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        st = app.state.studio
        yield {"srv": srv, "url": srv.url, "root": root, "studio": st, "rec": rec, "cfg": st.cfg}
        st.cfg.process.refine_from_images = False


@pytest.fixture
def light(monkeypatch):
    """Make mesh's parameters of the stage (its guard too), lighter: 6 textured views a patch (the recording has 10
    frames)."""
    import ohs.server.processing as SP

    p = dataclasses.replace(SP.make_mesh_spacetime_params(), min_views=6, half_px=4.0, levels=((0.4, 0.1), (0.1, 0.025)))
    assert p.guarded
    monkeypatch.setattr(SP, "make_mesh_spacetime_params", lambda: p)
    return p


@pytest.fixture(autouse=True)
def plans(monkeypatch) -> list:
    """Every plan of the percentage a Make mesh makes (:func:`ohs.server.processing.make_mesh_plan`; the run beside
    the result keeps it only while it runs)."""
    import ohs.server.processing as SP

    seen: list = []
    real = SP.make_mesh_plan

    def spy(**kw):
        out = real(**kw)
        seen.append([{"stage": k, "from": round(a, 4), "to": round(b, 4)} for k, (a, b) in out.items()])
        return out

    monkeypatch.setattr(SP, "make_mesh_plan", spy)
    return seen


def make_mesh(env: dict, name: str, plans: list, quality: str = "standard") -> tuple[str, str, dict, list]:
    """Make mesh on a new scan; returns its ids, the process state and the plan of the percentage it ran with."""
    pid, sid = add_scan(env, name, env["rec"])
    b = base_url(env, pid, sid)
    plans.clear()
    r = post(b + "/process", {"quality": quality})
    assert r.status_code == 200, r.text
    done = wait_job(env, r.json()["id"], timeout=900.0)
    assert done["state"] == "done", done
    assert len(plans) == 1, plans
    return pid, sid, get(b + "/process").json(), plans[0]


def same_mesh(a: dict, b: dict) -> bool:
    """The same triangles at the same places (the GPU path's mesh lists its vertices in an order of its own)."""
    def tris(m):
        T = np.asarray(m["vertices"], dtype=float)[np.asarray(m["faces"], dtype=np.int64)]  # (M, 3 corners, xyz)
        k = np.lexsort((T[..., 2], T[..., 1], T[..., 0]), axis=-1)  # each triangle's corners in one order
        R = np.take_along_axis(T, k[..., None], axis=1).reshape(len(T), 9)
        return R[np.lexsort(R.T[::-1])]  # the triangles in one order

    return len(a["vertices"]) == len(b["vertices"]) and np.array_equal(tris(a), tris(b))


def result_report(env: dict, pid: str, sid: str) -> dict:
    return json.loads((env["root"] / "projects" / pid / "scans" / sid / "result" / "report.json").read_text("utf-8"))


def result_mesh(env: dict, pid: str, sid: str) -> dict:
    return load_ply(env["root"] / "projects" / pid / "scans" / sid / "result" / "mesh.ply")


def test_the_setting_is_off_by_default_and_make_mesh_is_as_before(env, plans):
    from ohs.server.processing import MAKE_MESH_SHARES, make_mesh_plan

    assert Config().process.refine_from_images is False
    assert env["cfg"].process.refine_from_images is False
    # the plan without the setting is the one before it, part for part
    for kw in ({"straighten": True, "refine": True, "edits": True}, {"straighten": False, "refine": False, "edits": True}):
        plan = make_mesh_plan(**kw)
        assert "spacetime" not in plan and list(plan) == [k for k in MAKE_MESH_SHARES if kw.get(k, True)]
    pid, sid, s, plan = make_mesh(env, "Off", plans)
    assert s["stages"]["spacetime"]["state"] == "none"
    assert [p["stage"] for p in plan] == ["track", "straighten", "refine", "edits"]
    assert "spacetime" not in result_report(env, pid, sid)


def test_on_make_mesh_refines_from_the_images_before_the_lasers(env, light, plans):
    from ohs.server.processing import images_on_gpu

    if not images_on_gpu():
        pytest.skip("needs a graphics card (the CPU path is tested below)")
    env["cfg"].process.refine_from_images = True
    try:
        pid, sid, s, plan = make_mesh(env, "On", plans)
    finally:
        env["cfg"].process.refine_from_images = False
    stt = s["stages"]["spacetime"]
    assert stt["state"] == "done" and stt["source"] == "track" and stt["device"] == "gpu", stt
    assert stt["kept"] >= 50 and stt["summary"].startswith("Measured")
    assert [p["stage"] for p in plan] == ["track", "straighten", "spacetime", "refine", "edits"]
    assert plan[-1]["to"] == 1.0 and all(a["to"] == b["from"] for a, b in zip(plan, plan[1:]))
    assert s["stages"]["refine"]["source"] == "spacetime"  # the lasers refine the refined surface
    rep = result_report(env, pid, sid)["spacetime"]
    assert rep["source"] == "track" and rep["kept"] == stt["kept"] and "patch_results" not in rep
    # what was refined and what was left, for the app to say
    assert rep["patches"] == rep["kept"] + sum(rep["rejected"].values())
    assert 0.0 < rep["covered_share"] <= 1.0 and 0.0 < rep["moved_share"] <= 1.0
    # Make mesh's guard: the patches it used and those it left, by reason, and the points it left as they were
    g = rep["guard"]
    assert g["used"] + sum(g["patches_left"].values()) == rep["kept"] and g["used"] >= 50
    assert set(g["patches_left"]) == {"far", "thin", "edge", "rough"} and g["vertices_left"] >= 0
    assert g["limits"]["move_mm"] == 0.3 and stt["guard"] == g and "left as they were" in stt["summary"]
    assert rep["moved"]["max"] <= 0.3 + 1e-9  # no point moved farther than the guard's limit


def test_without_a_graphics_card_make_mesh_leaves_the_mesh_and_says_so(env, light, plans, monkeypatch, caplog):
    import ohs.server.processing as SP

    pid0, sid0, s0, _ = make_mesh(env, "Off for CPU", plans)
    monkeypatch.setattr(SP, "images_on_gpu", lambda: False)  # the stage's own test of the device, nothing else's
    env["cfg"].process.refine_from_images = True
    try:
        with caplog.at_level(logging.INFO, logger="ohs.server.processing"):
            pid, sid, s, plan = make_mesh(env, "On without a GPU", plans)
    finally:
        env["cfg"].process.refine_from_images = False
    stt = s["stages"]["spacetime"]
    assert stt["state"] == "skipped" and "graphics card" in stt["summary"] and "left the mesh as it is" in stt["summary"]
    assert s["stages"]["refine"]["source"] != "spacetime" and s["base"]["stage"] == s0["base"]["stage"]
    assert any("refine from the images skipped" in r.getMessage() for r in caplog.records)
    assert "spacetime" not in result_report(env, pid, sid)
    a, b = result_mesh(env, pid0, sid0), result_mesh(env, pid, sid)
    assert same_mesh(a, b)


def test_not_at_preview_quality(env, light, plans):
    env["cfg"].process.refine_from_images = True
    try:
        pid, sid, s, plan = make_mesh(env, "Preview", plans, quality="preview")
    finally:
        env["cfg"].process.refine_from_images = False
    assert s["stages"]["spacetime"]["state"] == "none"
    assert "spacetime" not in [p["stage"] for p in plan]


def reset_during_the_search(monkeypatch) -> None:
    """The search's self-check finds the first GPU launch changed (``ohs.track.spacetime._Cost.check``): what a
    graphics driver reset part way looks like. On the CPU path the check is the same hook."""
    import ohs.track.spacetime as stm

    def check(self):
        raise stm.GpuResetError(stm.GPU_RESET_TEXT)

    monkeypatch.setattr(stm._Cost, "check", check)


def test_a_driver_reset_in_the_mesh_edit_leaves_the_mesh_and_says_so(env, light, plans, monkeypatch):
    import ohs.track.spacetime as stm

    pid, sid, s0, _ = make_mesh(env, "Reset in the edit", plans)
    b = base_url(env, pid, sid)
    res = env["root"] / "projects" / pid / "scans" / sid / "result" / "mesh.ply"
    before = res.read_bytes()
    monkeypatch.setattr(stm, "SpacetimeParams", lambda: light)  # the stage's parameters, lighter
    reset_during_the_search(monkeypatch)
    r = post(b + "/process/spacetime")
    assert r.status_code == 200, r.text
    done = wait_job(env, r.json()["id"], timeout=900.0)
    assert done["state"] == "failed" and "graphics driver seems to have been reset" in json.dumps(done), done
    s = get(b + "/process").json()
    stt = s["stages"]["spacetime"]
    assert stt["state"] == "failed" and "mesh was left unchanged" in stt["summary"], stt
    assert s["base"]["stage"] == s0["base"]["stage"] and res.read_bytes() == before
    assert not (env["root"] / "projects" / pid / "scans" / sid / "process" / "spacetime").exists()


def test_a_driver_reset_in_make_mesh_leaves_the_mesh_and_says_so(env, light, plans, monkeypatch):
    from ohs.server.processing import images_on_gpu

    if not images_on_gpu():
        pytest.skip("Make mesh runs the stage on a graphics card only")
    pid0, sid0, _, _ = make_mesh(env, "Reset off", plans)
    reset_during_the_search(monkeypatch)
    env["cfg"].process.refine_from_images = True
    try:
        pid, sid, s, _ = make_mesh(env, "Reset on", plans)
    finally:
        env["cfg"].process.refine_from_images = False
    stt = s["stages"]["spacetime"]
    assert stt["state"] == "failed" and "graphics driver seems to have been reset" in stt["summary"], stt
    assert s["stages"]["refine"]["source"] != "spacetime" and "spacetime" not in result_report(env, pid, sid)
    assert same_mesh(result_mesh(env, pid0, sid0), result_mesh(env, pid, sid))
