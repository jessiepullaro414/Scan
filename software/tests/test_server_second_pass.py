"""A second pass that joins the first, through the real server (FEATURE_PLAN N2; APP_API.md "A second pass").

The routes are called as the app's clicks call them: the simulated bracket is replayed as the device
(``tests/second_pass_sim.py``); the first scan is started, stopped by the replay's end, made into a
mesh and checked; its reasons give the places to scan the weak areas from. The second pass is
rendered along one of those places (the largest area on the part itself, not on the table) and
started with ``continues``; after its Stop the server makes it into a mesh, aligns it to the first
scan and merges the two, as jobs. The merged scan's Check share must be higher than the first
scan's.

On the way: a cancelled step stops the chain and keeps the pass; an alignment that is not accepted
merges nothing and says so; ``POST .../pass/run`` runs what is left. The first scan is never changed,
and a scan that continues nothing is started and saved as before (no ``continues`` anywhere).

The shares need the mesh's sigma, which only the GPU path's model has; on the CPU path the chain is
run and checked, the share comparison is skipped (``VALIDATION.md``, "A second pass that joins the first").
"""
from __future__ import annotations

import hashlib
import json
import time

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

from ohs import compute  # noqa: E402
from ohs.app.config import Config  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402

pytestmark = pytest.mark.slow

GPU = compute.get_device() is not None
SIZE = (1280, 800)  # at 640x400 the tracked pass and first scan are 2-3 mm apart and the aligner refuses them
TOL = 1.0
T = 60  # HTTP timeout
CHAIN_S = 1500.0  # the whole chain (process, align, merge) at most; a busy PC is slow


def _wait_job(url, jid, timeout=900.0):
    from second_pass_sim import wait

    return wait(lambda: (lambda j: j if j["state"] not in ("queued", "running") else None)(
        requests.get(f"{url}/api/jobs/{jid}", timeout=T).json()), timeout, f"job {jid}", 0.25)


def _pass_view(env, sid):
    r = requests.get(f"{env['base']}/scans/{sid}/pass", timeout=T)
    assert r.status_code == 200, r.text
    return r.json()


def _wait_chain(env, sid, states, timeout=CHAIN_S):
    from second_pass_sim import wait

    return wait(lambda: (lambda v: v if v["chain"]["state"] in states else None)(_pass_view(env, sid)), timeout,
                f"the chain to reach {states}", 0.5)


def _err(r, status, needle=""):
    assert r.status_code == status, r.text
    body = r.json()
    assert body["detail"] and body.get("hint"), body
    assert needle.lower() in body["detail"].lower(), body
    return body


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    from second_pass_sim import first_recording, scan_through_server

    root = tmp_path_factory.mktemp("second-pass")
    rec1 = first_recording(root / "recs", SIZE)
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        url = srv.url
        st = app.state.studio
        pid = requests.post(url + "/api/projects", json={"name": "Bracket"}, timeout=T).json()["id"]
        first = scan_through_server(url, rec1, {"project_id": pid, "name": "First"})
        assert first["status"] == 200, first
        sid = first["scan_id"]
        r = requests.post(f"{url}/api/projects/{pid}/scans/{sid}/process", json={}, timeout=T)
        assert r.status_code == 200, r.text
        assert _wait_job(url, r.json()["id"])["state"] == "done"
        yield {"url": url, "studio": st, "pid": pid, "first": sid, "rec1": rec1, "root": root,
               "base": f"{url}/api/projects/{pid}", "start": first["start"]}


def test_a_scan_that_continues_nothing_is_started_and_saved_as_before(env):
    st, pid, sid = env["studio"], env["pid"], env["first"]
    assert "continues" not in env["start"]  # the start's answer: the session as before
    assert "continues" not in st.projects.scan_entry(pid, sid)
    proj = requests.get(env["base"], timeout=T).json()
    assert "continues" not in next(s for s in proj["scans"] if s["id"] == sid)
    meta = json.loads((st.projects.recording_dir(pid, sid) / "meta.json").read_text(encoding="utf-8"))
    assert "continues" not in meta
    assert not (st.projects.scan_dir(pid, sid) / "pass_areas.npz").exists()
    _err(requests.get(f"{env['base']}/scans/{sid}/pass", timeout=T), 409, "is not a second pass")
    _err(requests.post(f"{env['base']}/scans/{sid}/pass/run", timeout=T), 409, "is not a second pass")
    _err(requests.post(f"{env['base']}/scans/{sid}/pass/compare", json={}, timeout=T), 409, "is not a second pass")


def test_a_pass_is_refused_in_plain_words_before_anything_is_made(env):
    st, pid, sid = env["studio"], env["pid"], env["first"]
    n0 = len(st.projects.get(pid)["scans"])
    c = requests.post(f"{env['base']}/scans/{sid}/check", json={"tolerance_mm": TOL}, timeout=T).json()
    start = lambda cont: requests.post(env["url"] + "/api/scan/start", json={"project_id": pid, "continues": cont}, timeout=T)  # noqa: E731
    _err(start({"scan_id": "scan-19990101-000000", "tolerance_mm": TOL}), 404, "no such scan")
    bare, _ = st.projects.add_scan(pid, "No mesh", "markerless")
    st.projects.update_scan(pid, bare["id"], status="recorded")
    _err(start({"scan_id": bare["id"], "tolerance_mm": TOL}), 409, "has no mesh yet")
    _err(start({"scan_id": sid, "tolerance_mm": 0}), 422, "tolerance")
    _err(start({"scan_id": sid, "tolerance_mm": TOL, "areas": [c["n_areas"] + 100]}), 422, "not in the Scan check")
    _err(start({"scan_id": sid, "tolerance_mm": TOL, "areas": [0, 0]}), 422, "twice")
    _err(start({"scan_id": sid, "tolerance_mm": TOL, "version": c["version"] + 1, "areas": [0]}), 409, "changed since")
    _err(start({"scan_id": sid, "tolerance_mm": TOL, "poses": [{"pose": {"target_mm": [0, 0]}}]}), 422, "place 1 cannot be read")
    _err(start({"scan_id": sid, "tolerance_mm": TOL, "same_setup": "yes"}), 422, "whether the part was moved")
    # nothing was made by any of them (the bare scan is the test's own)
    assert len(st.projects.get(pid)["scans"]) == n0 + 1
    st.projects.remove_scan(pid, bare["id"])


def _part_pose(env, why):
    """The largest area on the part (not the table) that has a place to scan it from: its index and pose,
    with the place in the simulator's world."""
    from ohs.sim.scene import make_scene
    from second_pass_sim import scan_world

    st, pid, sid = env["studio"], env["pid"], env["first"]
    Tw = scan_world(env["rec1"], st.projects.result_dir(pid, sid) / "trajectory.csv")
    sc = make_scene("bracket", 0)
    for a in why["areas"]:  # largest first
        cw = Tw[:3, :3] @ np.asarray(a["center"]) + Tw[:3, 3]
        on_part = cw[2] > 2.0 and np.linalg.norm(cw[:2] - np.asarray(sc.target)[:2]) < 0.8 * sc.radius
        if a["view"]["ok"] and on_part:
            p = a["view"]["pose"]
            print(f"asked area {a['index'] + 1}: centre {np.round(cw, 1)} in the simulator's world")
            return a, Tw[:3, :3] @ np.asarray(p["target_mm"]) + Tw[:3, 3], -(Tw[:3, :3] @ np.asarray(p["look_dir"]))
    raise AssertionError(f"no area on the part with a pose: {[(a['reason'], a['view']['ok']) for a in why['areas']]}")


def test_two_passes_through_the_route_give_a_merged_scan_whose_check_share_is_higher(env, monkeypatch):
    import ohs.align as align_mod
    from second_pass_sim import pass_recording, scan_through_server

    st, pid, sid, url = env["studio"], env["pid"], env["first"], env["url"]
    first_mesh = st.projects.result_dir(pid, sid) / "mesh.ply"
    before_hash, before_entry = _hash(first_mesh), st.projects.scan_entry(pid, sid)
    c1 = requests.post(f"{env['base']}/scans/{sid}/check", json={"tolerance_mm": TOL}, timeout=T).json()
    why = requests.post(f"{env['base']}/scans/{sid}/check/why", json={"tolerance_mm": TOL, "version": c1["version"]},
                        timeout=600).json()
    if GPU:
        area, target_w, dir_w = _part_pose(env, why)
        print(f"first scan: {c1['share_ok']:.3f} of the surface within ± {TOL} mm, {c1['n_areas']} areas; asked area "
              f"{area['index'] + 1} ({area['reason']}, {area['area_mm2']:.0f} mm2)")
        rec2 = pass_recording(env["root"] / "recs", target_w, dir_w, area["view"]["pose"]["distance_mm"], SIZE)
        cont = {"scan_id": sid, "tolerance_mm": TOL, "version": c1["version"], "areas": [area["index"]],
                "poses": [{"area": area["index"], "kind": "area", "text": area["view"]["text"], "pose": area["view"]["pose"]}]}
    else:
        # the CPU path's model has no sigma: the Check lists no area and gives no place to scan from. The pass is
        # then the one the GPU path's runs chose (the part's foot on the side the first scan did not see, from
        # about 76 degrees up), asked with no area: the chain itself is what is checked here
        from ohs.sim.scene import make_scene

        assert c1["share_ok"] is None and not why["areas"]
        area = None
        target_w = np.asarray(make_scene("bracket", 0).target) + np.array([-2.0, 34.0, -15.0])
        rec2 = pass_recording(env["root"] / "recs", target_w, [0.085, -0.227, 0.97], 254.0, SIZE)
        cont = {"scan_id": sid, "tolerance_mm": TOL, "version": c1["version"], "areas": []}
    n_scans = len(st.projects.get(pid)["scans"])

    # the user's first click on Stop comes in the middle of the first step: cancel it
    jobs_seen: list = []
    real_after_stop = st.passes.after_stop

    def after_stop_then_cancel(p, s):
        real_after_stop(p, s)
        jid = st.projects.scan_entry(p, s)["continues"]["chain"]["jobs"]["process"]
        jobs_seen.append(jid)
        st.jobs.cancel(jid)

    monkeypatch.setattr(st.passes, "after_stop", after_stop_then_cancel)
    second = scan_through_server(url, rec2, {"project_id": pid, "name": "Pass", "continues": cont})
    assert second["status"] == 200, second
    assert second["start"]["continues"] == {"scan_id": sid, "scan_name": "First"}
    pas = second["scan_id"]
    v = _wait_chain(env, pas, ("cancelled", "failed"), 120)
    assert v["chain"]["state"] == "cancelled" and "kept as its own scan" in v["chain"]["message"], v["chain"]
    monkeypatch.setattr(st.passes, "after_stop", real_after_stop)

    # the link, as the entry, the recording and the route keep it
    entry = st.projects.scan_entry(pid, pas)
    link = entry["continues"]
    assert link["scan_id"] == sid and link["scan_name"] == "First" and link["tolerance_mm"] == TOL
    assert [a["index"] for a in link["areas"]] == ([area["index"]] if area else []) and link["same_setup"] is True
    assert (link["poses"][0]["text"] == area["view"]["text"] if area else link["poses"] == []) and link["merge_problem"] is None
    assert link["preset_from_first"] is True and link["preset"] == "General"
    # what was sent for the setup is said in words (a switch sent the wrong way round would show here)
    assert link["same_setup"] is True and link["setup_words"].startswith("The part was not moved"), link.get("setup_words")
    meta = json.loads((st.projects.recording_dir(pid, pas) / "meta.json").read_text(encoding="utf-8"))
    assert meta["continues"]["scan_id"] == sid and meta["continues"]["areas"] == link["areas"]
    assert v["first"]["name"] == "First" and v["note"]
    assert (len(v["areas_faces"]) == 1) if area else (v["areas_faces"] is None)
    _err(requests.post(f"{env['base']}/scans/{pas}/pass/compare", json={}, timeout=T), 409, "not merged")

    # Continue, with an alignment that is not accepted: nothing is merged, and the chain says so
    real_align = align_mod.align_with_tags

    def refused(*a, **kw):
        res, tags = real_align(*a, **kw)
        res.accepted, res.verdict = False, "Not aligned: (made up by the test) not sure."
        return res, tags

    monkeypatch.setattr(align_mod, "align_with_tags", refused)
    r = requests.post(f"{env['base']}/scans/{pas}/pass/run", timeout=T)
    assert r.status_code == 200, r.text
    _err(requests.post(f"{env['base']}/scans/{pas}/pass/run", timeout=T), 409, "running already")
    v = _wait_chain(env, pas, ("not_aligned", "failed", "merged", "cancelled"))
    assert v["chain"]["state"] == "not_aligned", v["chain"]
    assert "Nothing was merged" in v["chain"]["message"] and "Align page" in v["chain"]["message"]
    assert len(st.projects.get(pid)["scans"]) == n_scans + 1  # the pass only
    assert st.projects.scan_entry(pid, pas)["status"] == "processed"
    monkeypatch.setattr(align_mod, "align_with_tags", real_align)

    # Continue again: aligned (the real rule) and merged
    t0 = time.monotonic()
    r = requests.post(f"{env['base']}/scans/{pas}/pass/run", timeout=T)
    assert r.status_code == 200, r.text
    v = _wait_chain(env, pas, ("not_aligned", "not_merged", "failed", "merged", "cancelled"))
    chain = v["chain"]
    print(f"chain: {chain['state']}: {chain['message']} (align {chain.get('align')}); {time.monotonic() - t0:.0f} s")
    assert chain["state"] == "merged", chain
    assert set(chain["jobs"]) == {"process", "align", "merge"}
    for step in ("process", "align", "merge"):
        assert requests.get(f"{url}/api/jobs/{chain['jobs'][step]}", timeout=T).json()["state"] == "done"
    mid = chain["merged_scan_id"]
    merged = st.projects.scan_entry(pid, mid)
    assert [m["id"] for m in merged["merged_from"]] == [sid, pas] and merged["name"] == "First + Pass"
    _err(requests.post(f"{env['base']}/scans/{pas}/pass/run", timeout=T), 409, "merged already")

    # before and after, at the same tolerance
    cmp_ = requests.post(f"{env['base']}/scans/{pas}/pass/compare", json={}, timeout=600).json()
    f, m = cmp_["first"], cmp_["merged"]
    print(f"before and after at ± {TOL} mm: first {f['share_ok']} ({f['n_areas']} areas), merged {m['share_ok']} "
          f"({m['n_areas']} areas); asked area: {cmp_['areas']}")
    assert cmp_["tolerance_mm"] == TOL and f["id"] == sid and m["id"] == mid and "does not yet" in cmp_["note"]
    # on the surface both hold: the part was not moved, so (nearly) all of the first scan's surface is in the merge
    com, miss = cmp_["common"], cmp_["missing"]
    print(f"on the surface both hold ({com['found_share']:.3f} of the first scan's): {com['before_ok']} -> {com['after_ok']}; "
          f"missing {miss}")
    assert com["found_share"] > 0.95 and miss["share"] == pytest.approx(1.0 - com["found_share"]) and miss["why"] is None
    if GPU:
        assert len(cmp_["areas"]) == 1 and cmp_["areas"][0]["index"] == area["index"]
        assert cmp_["areas"][0]["found_share"] > 0.5
        assert m["share_ok"] > f["share_ok"]
        assert com["after_ok"] > com["before_ok"]
        assert cmp_["areas"][0]["share_ok"] > 0.0  # the asked area got better (it passed nowhere before)
    else:  # the CPU path's model has no sigma: nothing to compare (the chain itself is what is checked there)
        assert f["share_ok"] is None and m["share_ok"] is None

    # the first scan is unchanged
    assert _hash(first_mesh) == before_hash
    after_entry = st.projects.scan_entry(pid, sid)
    assert after_entry == before_entry, (before_entry, after_entry)
