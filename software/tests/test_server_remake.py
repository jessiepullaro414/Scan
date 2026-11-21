"""UX_PLAN R2: "Make mesh again" builds beside the scan's result and takes its place only once it has finished.

On a real small scan (a 10-frame simulated sphere) that was processed and then edited:

* a Make mesh cancelled half way (after its track stage made a new base) leaves every file of ``process/`` and
  ``result/`` byte for byte as it was, the processing state and the scan entry too, and its job says
  ``mesh_kept: true``;
* a failed one the same;
* the recipe on everything (``process/recipe``, scope ``all``) the same;
* an app that stopped on the way: a run that had not finished is removed at the next start, a swap that stopped
  half way is undone, and a swap whose new folders were all moved in is finished;
* a Make mesh that finishes replaces the result as before, with a higher mesh version and no folder left over.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

from ohs.app.config import Config  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.jobs import JobCancelled  # noqa: E402
from ohs.server.processing import REMAKE_PREFIX, ProcessManager  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402
from recordings import scan_recording  # noqa: E402

T = 30


def _app(root: Path, data: str = "data"):
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    return create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                      data_dir=root / data, config_path=root / "config.toml")


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("remake")
    rec = scan_recording(root / "rec", n_frames=10, image_size=(640, 400), seed=3, depth_every=0)
    app = _app(root)
    with ServerThread(app) as srv:
        st = app.state.studio
        pid = requests.post(srv.url + "/api/projects", json={"name": "Remake"}, timeout=T).json()["id"]
        scan, rec_dir = st.projects.add_scan(pid, "Sphere", "markerless")
        shutil.copytree(rec, rec_dir)
        n = len(RecordingReader(rec_dir))
        st.projects.update_scan(pid, scan["id"], status="recorded", n_frames=n, duration_s=n / 30.0)
        e = {"srv": srv, "url": srv.url, "root": root, "studio": st, "pid": pid, "sid": scan["id"],
             "scan_dir": st.projects.scan_dir(pid, scan["id"])}
        b = base(e)
        wait_done(e, requests.post(b + "/process", json={"quality": "preview"}, timeout=T).json()["id"])
        # an edit on top of Make mesh's own: it must survive a cancelled or failed Make mesh again
        job = requests.post(b + "/process/edits", json={"op": "smooth", "params": {"method": "taubin", "strength": 0.3}},
                            timeout=T).json()
        assert wait_job(e, job["id"])["state"] == "done"
        yield e


def base(env: dict) -> str:
    return f"{env['url']}/api/projects/{env['pid']}/scans/{env['sid']}"


def wait_job(env: dict, jid: str, timeout: float = 240.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        j = requests.get(f"{env['url']}/api/jobs/{jid}", timeout=T).json()
        if j["state"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {jid} did not finish within {timeout:.0f} s")


def wait_done(env: dict, jid: str) -> dict:
    j = wait_job(env, jid)
    assert j["state"] == "done", j
    return j


def snapshot(env: dict) -> dict:
    """Every file of the scan's ``process/`` and ``result/`` (bytes), the processing state as the API gives it and
    the scan's entry in ``project.json``."""
    sd = env["scan_dir"]
    files = {str(p.relative_to(sd)): p.read_bytes() for d in ("process", "result") for p in sorted((sd / d).rglob("*"))
             if p.is_file()}
    state = requests.get(base(env) + "/process", timeout=T).json()
    state = {k: v for k, v in state.items() if k not in ("job", "busy", "making")}
    entry = env["studio"].projects.scan_entry(env["pid"], env["sid"])
    return {"files": files, "state": state, "entry": entry}


def assert_same(before: dict, after: dict) -> None:
    assert sorted(after["files"]) == sorted(before["files"]), "the files of the result changed"
    changed = [k for k in before["files"] if before["files"][k] != after["files"][k]]
    assert not changed, f"files changed: {changed}"
    assert after["state"] == before["state"]
    assert after["entry"] == before["entry"]


def leftovers(env: dict) -> list[str]:
    return [p.name for p in env["scan_dir"].iterdir() if p.name.startswith(REMAKE_PREFIX)]


def held_in_refine(monkeypatch, studio):
    """Make "Refine with lasers" of the next run wait for a cancel: by then Follow the camera (and Straighten) have
    made the run's new base, which is when a run on the scan itself had replaced the old mesh."""
    reached = threading.Event()

    def slow(self, ctx, sp, prog):
        reached.set()
        deadline = time.monotonic() + 60
        while not ctx.cancelled and time.monotonic() < deadline:
            time.sleep(0.02)
        ctx.check()
        raise AssertionError("not cancelled")

    monkeypatch.setattr(ProcessManager, "_do_refine", slow)
    return reached


def test_a_cancelled_make_mesh_again_leaves_the_scan_as_it_was(env, monkeypatch):
    before = snapshot(env)
    assert before["state"]["cursor"] == 3 and before["state"]["edits"][-1]["op"] == "smooth"
    reached = held_in_refine(monkeypatch, env["studio"])
    job = requests.post(base(env) + "/process", json={"quality": "preview"}, timeout=T).json()
    assert reached.wait(120), "the run did not get to Refine with lasers"
    s = requests.get(base(env) + "/process", timeout=T).json()
    # while it runs: the scan's own stages, edits and mesh stay; the new run's stages are beside them
    assert s["stages"] == before["state"]["stages"] and s["current"] == before["state"]["current"]
    assert s["making"]["stages"]["track"]["state"] == "done" and s["job"]["id"] == job["id"]
    assert s["making"]["stages"]["straighten"]["state"] in ("done", "skipped")
    # R18: the run's parts and their spans of the percentage, each stage's time, the time left at this pace
    assert [x["stage"] for x in s["making"]["plan"]] == ["track", "straighten", "refine", "edits"]
    assert s["making"]["plan"][0]["from"] == 0.0 and s["making"]["plan"][-1]["to"] == 1.0
    assert isinstance(s["making"]["stages"]["track"]["seconds"], float) and s["making"]["stages"]["track"]["started"]
    assert s["making"]["elapsed_s"] > 0 and (s["making"]["left_s"] is None or s["making"]["left_s"] >= 0)
    requests.post(f"{env['url']}/api/jobs/{job['id']}/cancel", timeout=T)
    j = wait_job(env, job["id"])
    assert j["state"] == "cancelled" and j["mesh_kept"] is True
    assert_same(before, snapshot(env))
    assert not leftovers(env)
    assert requests.get(base(env) + "/process", timeout=T).json()["making"] is None


def test_the_mesh_before_any_edit_of_the_history(env, tmp_path):
    """UX_PLAN R23: what an edit took can be shown at any later time: ``.../process/edits/{id}/before.ply`` is the
    mesh the edit was applied to, for every edit of the history (not only the last, and also an undone one)."""
    from ohs.track.export import load_ply

    s = requests.get(base(env) + "/process", timeout=T).json()
    counts = [s["base"]["triangles"]] + [e["triangles_after"] for e in s["edits"]]
    assert len(s["edits"]) == 3 and s["edits"][0]["op"] == "remove_floaters"
    for k, e in enumerate(s["edits"]):
        r = requests.get(base(env) + f"/process/edits/{e['id']}/before.ply", timeout=T)
        assert r.status_code == 200 and r.content.startswith(b"ply")
        assert r.headers["X-OHS-Step"] == str(k) and r.headers["X-OHS-Mesh-Version"] == str(s["current"]["version"])
        p = tmp_path / f"before-{k}.ply"
        p.write_bytes(r.content)
        assert len(load_ply(p)["faces"]) == counts[k]
        r = requests.get(base(env) + f"/process/edits/{e['id']}/after.ply", timeout=T)
        assert r.status_code == 200 and r.headers["X-OHS-Step"] == str(k + 1)
        p.write_bytes(r.content)
        assert len(load_ply(p)["faces"]) == counts[k + 1]
    r = requests.get(base(env) + "/process/edits/e-nothere/before.ply", timeout=T)
    assert r.status_code == 404 and r.json()["detail"] == "This edit is no longer in the scan's history." and r.json()["hint"]


def test_a_failed_make_mesh_again_leaves_the_scan_as_it_was(env, monkeypatch):
    before = snapshot(env)

    def boom(self, ctx, sp, ops, prog):
        raise RuntimeError("an engine error after the stages")

    monkeypatch.setattr(ProcessManager, "_apply_ops", boom)
    job = requests.post(base(env) + "/process", json={"quality": "preview"}, timeout=T).json()
    j = wait_job(env, job["id"])
    assert j["state"] == "failed" and j["mesh_kept"] is True
    assert_same(before, snapshot(env))
    assert not leftovers(env)
    # the recipe on everything is built beside the result the same way
    job = requests.post(base(env) + "/process/recipe", json={"recipe_id": "default", "scope": "all"}, timeout=T).json()
    j = wait_job(env, job["id"])
    assert j["state"] == "failed" and j["mesh_kept"] is True
    assert_same(before, snapshot(env))
    assert not leftovers(env)


def test_a_cancelled_recipe_on_everything_leaves_the_scan_as_it_was(env, monkeypatch):
    before = snapshot(env)
    reached = held_in_refine(monkeypatch, env["studio"])
    job = requests.post(base(env) + "/process/recipe", json={"recipe_id": "default", "scope": "all"}, timeout=T).json()
    assert reached.wait(120)
    requests.post(f"{env['url']}/api/jobs/{job['id']}/cancel", timeout=T)
    j = wait_job(env, job["id"])
    assert j["state"] == "cancelled" and j["mesh_kept"] is True
    assert_same(before, snapshot(env))
    assert not leftovers(env)


def _fake_run(env: dict, name: str) -> Path:
    """A run's folder as a finished Make mesh leaves it just before the swap: a copy of the scan's ``process/`` and
    ``result/`` with a mark, so the test can tell the new from the old."""
    sd = env["scan_dir"]
    staging = sd / f"{REMAKE_PREFIX}{name}"
    for d in ("process", "result"):
        shutil.copytree(sd / d, staging / d)
        (staging / d / "NEW").write_text("new", encoding="utf-8")
    return staging


def test_an_app_stopped_mid_run_keeps_the_old_result(env):
    """Each moment an app can stop at: before the new result was whole, and half way through the swap; the next start
    puts the scan back as it was and removes the leftovers."""
    sd = env["scan_dir"]
    before = snapshot(env)
    # 1. stopped while the run was building: a half-made folder, no swap.json; checked through a real start
    half = sd / f"{REMAKE_PREFIX}j-half"
    (half / "process" / "track.tmp-j-half").mkdir(parents=True)
    (half / "process" / "track.tmp-j-half" / "mesh.ply").write_bytes(b"ply half")
    with ServerThread(_app(env["root"], "data-restart")):
        pass
    assert not half.exists()
    assert_same(before, snapshot(env))
    # 2. stopped half way through the swap: the old process/ was moved aside, the old result/ not yet
    staging = _fake_run(env, "j-swap1")
    (staging / "swap.json").write_text(json.dumps({"fields": {"quality": "best"}}), encoding="utf-8")
    (staging / "old").mkdir()
    (sd / "process").rename(staging / "old" / "process")
    assert env["studio"].processing.recover_remakes() == 1
    assert not staging.exists() and not (sd / "process" / "NEW").exists()
    assert_same(before, snapshot(env))
    # 3. the new process/ was moved in, the new result/ not yet: undone too
    staging = _fake_run(env, "j-swap2")
    (staging / "swap.json").write_text(json.dumps({"fields": {"quality": "best"}}), encoding="utf-8")
    (staging / "old").mkdir()
    (sd / "process").rename(staging / "old" / "process")
    (sd / "result").rename(staging / "old" / "result")
    (staging / "process").rename(sd / "process")
    assert env["studio"].processing.recover_remakes() == 1
    assert not staging.exists() and not (sd / "process" / "NEW").exists() and not (sd / "result" / "NEW").exists()
    assert_same(before, snapshot(env))


def test_an_app_stopped_after_the_swap_keeps_the_new_result(env):
    sd = env["scan_dir"]
    keep = {d: sd.parent / f"keep-{d}" for d in ("process", "result")}
    for d, p in keep.items():
        shutil.copytree(sd / d, p)
    entry = env["studio"].projects.scan_entry(env["pid"], env["sid"])
    try:
        staging = _fake_run(env, "j-swap3")
        (staging / "swap.json").write_text(json.dumps({"fields": {"quality": "best"}}), encoding="utf-8")
        (staging / "old").mkdir()
        for d in ("process", "result"):
            (sd / d).rename(staging / "old" / d)
        for d in ("process", "result"):
            (staging / d).rename(sd / d)
        assert env["studio"].processing.recover_remakes() == 1
        assert not staging.exists() and (sd / "process" / "NEW").is_file() and (sd / "result" / "NEW").is_file()
        assert env["studio"].projects.scan_entry(env["pid"], env["sid"])["quality"] == "best"
    finally:  # back to the scan the other tests know
        for d, p in keep.items():
            shutil.rmtree(sd / d)
            p.rename(sd / d)
        env["studio"].projects.update_scan(env["pid"], env["sid"], quality=entry.get("quality"))


def test_a_swap_that_cannot_finish_puts_the_old_result_back(env, monkeypatch):
    """A file of the result held open (Windows) past the patience: the swap is undone, the job fails in plain words."""
    import ohs.server.processing as P

    before = snapshot(env)
    real = P._rename
    calls = []

    def held(src, dst):
        calls.append(src.name)
        if len(calls) == 2:  # the old result/ cannot be moved aside
            raise PermissionError(13, "held open")
        real(src, dst)

    monkeypatch.setattr(P, "_rename", held)
    job = requests.post(base(env) + "/process", json={"quality": "preview"}, timeout=T).json()
    j = wait_job(env, job["id"])
    assert j["state"] == "failed" and j["mesh_kept"] is True and "open in another program" in j["error"], j
    monkeypatch.setattr(P, "_rename", real)
    assert_same(before, snapshot(env))
    assert not leftovers(env)


def test_a_swap_that_cannot_be_undone_keeps_the_old_result_for_the_next_start(env, monkeypatch):
    """Worse: the old result/ cannot be moved aside, and then the old process/ cannot be moved back either. The run's
    folder, which holds it, is not removed; the next start puts it back."""
    import ohs.server.processing as P

    before = snapshot(env)
    real = P._rename
    calls = []

    def held(src, dst):
        calls.append(src.name)
        if len(calls) >= 2:
            raise PermissionError(13, "held open")
        real(src, dst)

    monkeypatch.setattr(P, "_rename", held)
    job = requests.post(base(env) + "/process", json={"quality": "preview"}, timeout=T).json()
    j = wait_job(env, job["id"])
    monkeypatch.setattr(P, "_rename", real)
    assert j["state"] == "failed" and j["mesh_kept"] is True, j
    left = leftovers(env)
    assert len(left) == 1 and (env["scan_dir"] / left[0] / "old" / "process" / "state.json").is_file()
    assert env["studio"].processing.recover_remakes() == 1
    assert not leftovers(env)
    assert_same(before, snapshot(env))


def test_a_finished_make_mesh_again_takes_the_old_ones_place(env):
    before = snapshot(env)
    job = requests.post(base(env) + "/process", json={"quality": "preview"}, timeout=T).json()
    j = wait_done(env, job["id"])
    assert j["mesh_kept"] is False and j["result"]["message"] == "Mesh ready"
    s = requests.get(base(env) + "/process", timeout=T).json()
    assert s["current"]["version"] > before["state"]["current"]["version"]
    assert [e["op"] for e in s["edits"]] == ["remove_floaters", "fill_holes"] and s["cursor"] == 2  # the recipe's own
    assert s["stages"]["track"]["job_id"] == job["id"] and s["making"] is None
    assert env["studio"].projects.scan_entry(env["pid"], env["sid"])["status"] == "processed"
    assert not leftovers(env)
    sd = env["scan_dir"]
    assert (sd / "result" / "mesh.ply").is_file() and (sd / "process" / "state.json").is_file()
    assert requests.get(base(env) + "/files/mesh.ply", timeout=T).status_code == 200


def test_a_cancel_after_the_point_of_no_return_is_ignored():
    from ohs.server.jobs import JobRunner

    runner = JobRunner()
    go = threading.Event()

    def fn(ctx):
        ctx.commit()
        go.wait(10)
        return {"message": "Done"}

    job = runner.submit("test", fn)
    deadline = time.monotonic() + 10
    while not job.committed and time.monotonic() < deadline:
        time.sleep(0.01)
    runner.cancel(job.id)
    go.set()
    assert runner.wait(job.id, 10).state == "done"

    def fn2(ctx):
        go2.wait(10)
        ctx.commit()
        return {}

    go2 = threading.Event()
    job2 = runner.submit("test", fn2)
    runner.cancel(job2.id)
    go2.set()
    assert runner.wait(job2.id, 10).state == "cancelled"
    runner.shutdown(5)
    assert JobCancelled


def test_the_plan_of_a_make_mesh():
    from ohs.server.processing import MAKE_MESH_SHARES, make_mesh_plan

    full = make_mesh_plan(straighten=True, refine=True, edits=True)
    assert list(full) == ["track", "straighten", "refine", "edits"] and full["edits"][1] == 1.0
    assert full["track"][1] == pytest.approx(MAKE_MESH_SHARES["track"] / sum(MAKE_MESH_SHARES.values()))
    assert all(full[a][1] == full[b][0] for a, b in zip(list(full), list(full)[1:]))
    quick = make_mesh_plan(straighten=False, refine=False, edits=True)
    assert list(quick) == ["track", "edits"] and quick["track"][0] == 0.0 and quick["edits"][1] == 1.0


def test_recovery_never_removes_a_folder_that_still_holds_the_old_result(tmp_path):
    """Found in review: after undoing a half-done swap the run's folder is removed. Should an old folder still be in
    it (a state the swap never makes, but a disk can: a file manager, a restore, a second copy), it must stay."""
    from ohs.server.processing import recover_remake

    scan, staging = tmp_path / "scan", tmp_path / "scan" / (REMAKE_PREFIX + "x")
    for d, text in ((scan / "process", "someone else's"), (staging / "process", "new"), (staging / "result", "new"),
                    (staging / "old" / "process", "old")):
        d.mkdir(parents=True)
        (d / "f.txt").write_text(text, encoding="utf-8")
    (staging / "swap.json").write_text(json.dumps({"fields": {}}), encoding="utf-8")
    assert recover_remake(scan, staging) is None
    assert (staging / "old" / "process" / "f.txt").read_text(encoding="utf-8") == "old"  # not removed
    assert (scan / "process" / "f.txt").read_text(encoding="utf-8") == "someone else's"  # not touched
