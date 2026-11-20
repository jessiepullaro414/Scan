"""What a scan keeps on disk (FEATURE_PLAN.md R2; APP_API.md "What a scan keeps").

The guards, on real simulated scans made through the server (a 10-frame sphere at 640x400):

* **Engineering** (the recording and the result) frees exactly the bytes its plan reported (counted
  here independently with ``Path.rglob``), removes the stage folders and the undo history and
  nothing else, and Make mesh then gives the same mesh as before, bit for bit (as a set of
  vertices: the file may list them in another order);
* **Standard** (the result only) asks for a second yes; afterwards edits, undo of the new edits,
  fits, the Check answer, the kept features, the laser map, the path and the downloads answer as
  before, and Make mesh, the stages and Merge answer with a refusal in plain words;
* a scan that is recording, being processed, or has a job queued is not moved; a failure half
  way leaves a scan that still opens, and freeing again finishes the job; a merged scan (no
  recording of its own) can keep its result only; "Refine with lasers" no longer needs a recording;
* the project-wide plan and move.

Run on the GPU path and with ``OHS_COMPUTE=cpu``.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

from ohs.app.config import Config  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402
from ohs.track.export import load_ply  # noqa: E402
from recordings import scan_recording  # noqa: E402

T = 60  # HTTP timeout
JOB_TIMEOUT = 300.0
RESULT_FILES = ("mesh.ply", "mesh.stl", "laser_points.ply", "report.json", "trajectory.csv")


# --------------------------------------------------------------------------- helpers
def post(url: str, body: dict | None = None):
    return requests.post(url, json=body, timeout=T)


def get(url: str):
    return requests.get(url, timeout=T)


def err(r, status: int, needle: str = "") -> dict:
    assert r.status_code == status, r.text
    body = r.json()
    assert body["detail"] and body.get("hint"), body  # every refusal says what to do
    assert needle.lower() in (body["detail"] + " " + body["hint"]).lower(), body
    assert "traceback" not in r.text.lower() and "errno" not in r.text.lower(), body
    return body


def size_of(d: Path) -> int:
    """Bytes of every file under ``d``, counted independently of the server (0 when missing)."""
    return sum(p.stat().st_size for p in d.rglob("*") if p.is_file()) if d.exists() else 0


def wait_job(env: dict, jid: str, timeout: float = JOB_TIMEOUT) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        j = get(f"{env['url']}/api/jobs/{jid}").json()
        if j["state"] not in ("queued", "running"):
            return j
        time.sleep(0.1)
    raise AssertionError(f"job {jid} did not finish within {timeout:.0f} s")


def run_job(env: dict, r, kind: str) -> dict:
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["kind"] == kind, job
    done = wait_job(env, job["id"])
    assert done["state"] == "done", done
    return done


def scan_url(env: dict, pid: str, sid: str) -> str:
    return f"{env['url']}/api/projects/{pid}/scans/{sid}"


def scan_dir(env: dict, pid: str, sid: str) -> Path:
    return env["studio"].projects.scan_dir(pid, sid)


def new_project(env: dict, name: str) -> str:
    return post(env["url"] + "/api/projects", {"name": name}).json()["id"]


def add_recorded(env: dict, pid: str, name: str) -> str:
    """A recorded scan (not processed) whose recording is a copy of the module's recording."""
    st = env["studio"]
    scan, rec_dir = st.projects.add_scan(pid, name, "markerless")
    shutil.copytree(env["rec"], rec_dir)
    n = len(RecordingReader(rec_dir))
    st.projects.update_scan(pid, scan["id"], status="recorded", n_frames=n, duration_s=n / 30.0)
    return scan["id"]


def clone(env: dict, pid: str, name: str) -> str:
    """A processed scan: a copy of the template scan's folder (recording, process, result) and entry."""
    st = env["studio"]
    tp, ts = env["template"]
    scan, _ = st.projects.add_scan(pid, name, "markerless")
    shutil.copytree(scan_dir(env, tp, ts), scan_dir(env, pid, scan["id"]), dirs_exist_ok=True)
    e = st.projects.scan_entry(tp, ts)
    st.projects.update_scan(pid, scan["id"], **{k: e[k] for k in ("status", "n_frames", "duration_s", "quality", "report")
                                                 if k in e})
    return scan["id"]


def vertex_set(path: Path) -> np.ndarray:
    """The mesh's vertices as a sorted set of rows (the file may list them in another order)."""
    V = np.asarray(load_ply(path)["vertices"])
    return V[np.lexsort(V.T[::-1])]


def stage_dirs(sd: Path) -> list[str]:
    proc = sd / "process"
    return sorted(q.name for q in proc.iterdir() if q.is_dir() and q.name not in ("cache", "selections")) \
        if proc.is_dir() else []


def edit(env: dict, pid: str, sid: str, op: str, params: dict) -> dict:
    return run_job(env, post(scan_url(env, pid, sid) + "/process/edits", {"op": op, "params": params}), "mesh_edit")


def answers(env: dict, pid: str, sid: str) -> dict:
    """What the mesh-reading routes answer now (status and body), to compare before and after."""
    b = scan_url(env, pid, sid)
    s = get(b + "/process").json()
    v = s["current"]["version"]
    d = load_ply(scan_dir(env, pid, sid) / "result" / "mesh.ply")
    V, F = np.asarray(d["vertices"]), np.asarray(d["faces"])
    out: dict = {"version": v}
    for k in (0, len(F) // 3, len(F) // 2):  # a fit at three places (whatever the answer, it must not change)
        c = V[F[k]].mean(axis=0).tolist()
        r = post(b + "/fit", {"kind": "plane", "face": int(k), "point": c, "version": v})
        out[f"fit{k}"] = (r.status_code, r.json())
    r = post(b + "/check", {"tolerance_mm": 0.1})
    out["check"] = (r.status_code, r.json())
    r = get(b + "/process/lasers.json")
    out["lasers"] = (r.status_code, r.json())
    out["features"] = get(b + "/features").json()
    out["files"] = {n: get(f"{b}/files/{n}").content for n in RESULT_FILES}
    return out


# --------------------------------------------------------------------------- the server
@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("keep")
    rec = scan_recording(root / "rec", n_frames=10, image_size=(640, 400), seed=3, depth_every=0)
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        e = {"srv": srv, "url": srv.url, "root": root, "studio": app.state.studio, "rec": rec}
        pid = new_project(e, "Template")
        sid = add_recorded(e, pid, "Template")
        run_job(e, post(scan_url(e, pid, sid) + "/process", {"quality": "preview"}), "process_scan")
        e["template"] = (pid, sid)
        yield e


# --------------------------------------------------------------------------- Engineering
def test_engineering_frees_what_it_says_and_make_mesh_gives_the_same_mesh(env):
    pid = new_project(env, "Engineering")
    sid = clone(env, pid, "A")
    b, sd = scan_url(env, pid, sid), scan_dir(env, pid, sid)
    tp, ts = env["template"]
    made = vertex_set(scan_dir(env, tp, ts) / "result" / "mesh.ply")
    n_faces = len(load_ply(scan_dir(env, tp, ts) / "result" / "mesh.ply")["faces"])
    edit(env, pid, sid, "smooth", {"method": "taubin", "strength": 0.2})  # an undo step (and its cache file)
    assert get(b + "/process").json()["can_undo"] is True
    scan =next(s for s in get(f"{env['url']}/api/projects/{pid}").json()["scans"] if s["id"] == sid)
    assert scan["keep"] == "full" and scan["has_recording"] is True  # new scans keep everything (the default)

    disk = next(s for s in get(f"{env['url']}/api/projects/{pid}/disk").json()["scans"] if s["id"] == sid)
    r = get(b + "/keep?mode=engineering")
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["ok"] is True and plan["refusal"] is None and plan["keep"] == "full" and plan["to"] == "engineering"
    assert plan["confirm"] is False  # only Standard asks twice
    parts = {p["part"]: p["bytes"] for p in plan["remove"]}
    # R1's numbers, gross per part: the whole undo cache goes (result/mesh.ply holds the current mesh, bit for
    # bit, and serves as the base new edits start from); state.json is rewritten (its change in `writes`)
    assert set(parts) == {"stages", "undo"} and parts["stages"] == disk["stages_bytes"] and parts["undo"] == disk["undo"]
    writes = {w["what"]: w["bytes"] for w in plan["writes"]}
    assert set(writes) == {"state"} and writes["state"] < 0  # the history leaves state.json too
    state_before = (sd / "process" / "state.json").stat().st_size
    assert plan["frees_bytes"] == sum(parts.values()) - sum(writes.values()) > 0
    assert {"undo", "stages"} <= set(plan["loses"]) and "remake" not in plan["loses"]
    assert {"recording", "result"} <= set(plan["keeps"])

    rec_before, res_before = size_of(sd / "recording"), {n: (sd / "result" / n).read_bytes() for n in RESULT_FILES}
    before = size_of(sd)
    r = post(b + "/keep", {"mode": "engineering"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["freed_bytes"] == plan["frees_bytes"] == before - size_of(sd)  # what it said, exactly
    assert writes["state"] == (sd / "process" / "state.json").stat().st_size - state_before
    assert disk["bytes"] - out["freed_bytes"] == size_of(sd)  # the row's number minus the promise is what is left
    assert out["scan"]["keep"] == "engineering" and out["scan"]["freed"][-1]["bytes"] == out["freed_bytes"]
    assert stage_dirs(sd) == [] and not (sd / "process" / "selections").exists()
    assert not (sd / "process" / "cache").exists() or not any((sd / "process" / "cache").iterdir())
    assert size_of(sd / "recording") == rec_before  # the recording is all there
    assert {n: (sd / "result" / n).read_bytes() for n in RESULT_FILES} == res_before  # and the result
    entry = json.loads((sd.parent.parent / "project.json").read_text(encoding="utf-8"))["scans"]
    assert next(e for e in entry if e["id"] == sid)["keep"] == "engineering"  # recorded in project.json
    s = get(b + "/process").json()
    assert s["edits"] == [] and s["can_undo"] is False and s["current"] is not None  # the mesh as it was, no history

    # a stage on its own says why it cannot run, and what would be needed
    for stage in ("straighten", "fuse", "refine", "spacetime"):
        err(post(b + f"/process/{stage}", {}), 409, "removed to free space")
    err(post(b + "/keep", {"mode": "engineering"}), 409, "nothing to free")
    err(post(b + "/keep", {"mode": "full"}), 409, "cannot be brought back")

    # Make mesh from the recording: the same mesh, bit for bit
    run_job(env, post(b + "/process", {"quality": "preview"}), "process_scan")
    again = sd / "result" / "mesh.ply"
    assert len(load_ply(again)["faces"]) == n_faces
    np.testing.assert_array_equal(vertex_set(again), made)
    # the stage folders are back; the scan still says what was removed, the disk says what is there now
    assert stage_dirs(sd) != []
    assert next(s for s in get(f"{env['url']}/api/projects/{pid}").json()["scans"] if s["id"] == sid)["keep"] == "engineering"
    plan2 = get(b + "/keep?mode=engineering").json()
    assert plan2["ok"] is True and plan2["frees_bytes"] > 0  # and can be freed again


# --------------------------------------------------------------------------- Standard
def test_standard_keeps_the_result_and_refuses_what_needs_the_recording(env):
    pid = new_project(env, "Standard")
    sid = clone(env, pid, "B")
    other = clone(env, pid, "B2")  # a scan to merge with
    b, sd = scan_url(env, pid, sid), scan_dir(env, pid, sid)
    edit(env, pid, sid, "fill_holes", {"max_hole_mm": 3.0})
    m = post(b + "/features/measurements", {"a": [0, 0, 0], "b": [10, 0, 0], "name": "Ten"})
    assert m.status_code == 200, m.text
    before_answers = answers(env, pid, sid)
    assert before_answers["features"]["measurements"][0]["stale"] is False
    assert any(before_answers[k][0] == 200 for k in before_answers if k.startswith("fit"))  # a fit that works
    assert before_answers["check"][0] == 200 and before_answers["lasers"][0] == 200

    plan = get(b + "/keep?mode=standard").json()
    assert plan["ok"] is True and plan["confirm"] is True
    assert {"remake", "merge", "refine", "frames", "undo"} <= set(plan["loses"])
    assert {"result", "laser_points", "trajectory", "report", "calibration", "features"} <= set(plan["keeps"])
    assert "recording" not in plan["keeps"]
    parts = {p["part"]: p["bytes"] for p in plan["remove"]}
    kept_rec = sum((sd / "recording" / n).stat().st_size for n in ("calibration.json", "meta.json")
                   if (sd / "recording" / n).is_file())
    assert parts["recording"] == size_of(sd / "recording") - kept_rec

    size0 = size_of(sd)
    err(post(b + "/keep", {"mode": "standard"}), 409, "cannot be undone")  # the second yes is required
    assert size_of(sd) == size0  # nothing was touched
    r = post(b + "/keep", {"mode": "standard", "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["freed_bytes"] == plan["frees_bytes"] == size0 - size_of(sd)
    assert sorted(p.name for p in (sd / "recording").iterdir()) == ["calibration.json", "meta.json"]
    assert stage_dirs(sd) == []
    scan = next(s for s in get(f"{env['url']}/api/projects/{pid}").json()["scans"] if s["id"] == sid)
    assert scan["keep"] == "standard" and scan["has_recording"] is False and scan["result"] is not None

    # everything that reads the result answers as before
    after = answers(env, pid, sid)
    assert after == before_answers
    assert get(b + "/process/trajectory.json").status_code == 200  # the path, from the result
    assert get(b + "/process/deviation.json").status_code == 200
    rep = post(b + "/report/report.html", {"tolerance_mm": 0.2})
    assert rep.status_code == 200 and b"<html" in rep.content.lower()
    assert post(b + "/report/features.csv", {}).status_code == 200
    # edits and their undo still work; the earlier history is gone and undo says so
    kept_mesh = load_ply(sd / "result" / "mesh.ply")
    edit(env, pid, sid, "smooth", {"method": "taubin", "strength": 0.2})
    assert get(b + "/process").json()["can_undo"] is True
    r = post(b + "/process/undo")
    assert r.status_code == 200, r.text
    assert r.json()["can_undo"] is False
    back = load_ply(sd / "result" / "mesh.ply")  # back to the kept mesh, bit for bit
    assert np.array_equal(back["vertices"], kept_mesh["vertices"]) and np.array_equal(back["faces"], kept_mesh["faces"])
    err(post(b + "/process/undo"), 409, "removed to free space")

    # what needs the recording says so, in plain words
    err(post(b + "/process", {"quality": "preview"}), 409, "recording was removed")
    for stage in ("track", "straighten", "fuse", "spacetime"):
        err(post(b + f"/process/{stage}", {}), 409, "recording was removed")
    err(post(b + "/process/refine", {}), 409, "removed to free space")
    err(post(b + "/process/recipe", {"recipe_id": "default", "scope": "all"}), 409, "recording was removed")
    err(post(f"{env['url']}/api/projects/{pid}/merge", {"target": other, "source": sid, "T": np.eye(4).tolist()}),
        409, "its recording was removed")
    err(post(b + "/keep", {"mode": "engineering"}), 409, "cannot keep")
    err(post(b + "/keep", {"mode": "full"}), 409, "cannot be brought back")


# --------------------------------------------------------------------------- the plan writes nothing
def snapshot(sd: Path) -> list[tuple[str, int]]:
    """Every file under the scan folder with its size: what a read-only request must leave as it was."""
    return sorted((p.relative_to(sd).as_posix(), p.stat().st_size) for p in sd.rglob("*") if p.is_file())


def row_bytes(env: dict, pid: str, sid: str) -> int:
    """What the project page shows on the scan's row (R1)."""
    return next(s for s in get(f"{env['url']}/api/projects/{pid}/disk").json()["scans"] if s["id"] == sid)["bytes"]


def test_a_plan_changes_nothing_for_a_scan_made_before_the_stages(env):
    """A scan with a recording and a result but no ``process/`` folder (made before the processing studio):
    asking for the plan must not take it over (no stage copy, no cache file); the promise is against the
    number on its row."""
    st = env["studio"]
    pid = new_project(env, "Before the stages")
    tp, ts = env["template"]
    scan, rec_dir = st.projects.add_scan(pid, "Old", "markerless")
    sid = scan["id"]
    sd = scan_dir(env, pid, sid)
    shutil.copytree(scan_dir(env, tp, ts) / "recording", rec_dir)
    shutil.copytree(scan_dir(env, tp, ts) / "result", sd / "result")
    e = st.projects.scan_entry(tp, ts)
    st.projects.update_scan(pid, sid, status="processed", n_frames=e["n_frames"], duration_s=e["duration_s"])
    b = scan_url(env, pid, sid)
    snap, row = snapshot(sd), row_bytes(env, pid, sid)
    assert row == size_of(sd)
    eng = get(b + "/keep?mode=engineering").json()
    std = get(b + "/keep?mode=standard").json()
    get(f"{env['url']}/api/projects/{pid}/keep?mode=standard")
    assert snapshot(sd) == snap and not (sd / "process").exists()  # nothing written, nothing taken over
    assert row_bytes(env, pid, sid) == row  # the row stays true after a dialog closed without a move
    assert eng["ok"] is False and "nothing to free" in eng["refusal"]["detail"]  # no stages, nothing to free
    assert std["ok"] is True and std["writes"] == []
    r = post(b + "/keep", {"mode": "standard", "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["freed_bytes"] == std["frees_bytes"] == row - size_of(sd) > 0
    assert not (sd / "process").exists()  # the move wrote nothing either
    assert post(b + "/check", {"tolerance_mm": 0.1}).status_code == 200  # and the scan opens


def test_a_plan_changes_nothing_when_the_current_step_is_not_cached(env):
    """Stages and edits, but the current step's cache file is gone (pruned, or removed by hand): the plan
    must not rebuild it, and the move needs none (``result/mesh.ply`` is the current mesh)."""
    pid = new_project(env, "No cache file")
    sid = clone(env, pid, "E")
    b, sd = scan_url(env, pid, sid), scan_dir(env, pid, sid)
    edit(env, pid, sid, "fill_holes", {"max_hole_mm": 3.0})
    before_answers = answers(env, pid, sid)
    st = json.loads((sd / "process" / "state.json").read_text(encoding="utf-8"))
    (sd / "process" / "cache" / f"{st['edits'][st['cursor'] - 1]['id']}.npz").unlink()
    env["studio"].processing.forget(pid)  # as after a restart: nothing of it in memory either
    snap, row = snapshot(sd), row_bytes(env, pid, sid)
    plan = get(b + "/keep?mode=engineering").json()
    assert snapshot(sd) == snap and row_bytes(env, pid, sid) == row  # the plan wrote nothing
    assert plan["ok"] is True and plan["frees_bytes"] > 0
    r = post(b + "/keep", {"mode": "engineering"})
    assert r.status_code == 200, r.text
    assert r.json()["freed_bytes"] == plan["frees_bytes"] == row - size_of(sd)
    assert answers(env, pid, sid) == before_answers  # the mesh, its fits and checks: as before
    # a new edit, and undo back to the kept mesh, bit for bit
    kept = load_ply(sd / "result" / "mesh.ply")
    edit(env, pid, sid, "smooth", {"method": "taubin", "strength": 0.2})
    assert post(b + "/process/undo").status_code == 200
    back = load_ply(sd / "result" / "mesh.ply")
    assert np.array_equal(back["vertices"], kept["vertices"]) and np.array_equal(back["faces"], kept["faces"])
    assert np.array_equal(back["quality"], kept["quality"])


def test_the_current_steps_cache_file_stays_when_the_result_differs(env):
    """When ``result/mesh.ply`` does not hold the current step bit for bit (here: changed by hand), the
    step's cache file stays as the base, the plan says so, and the promise still holds."""
    from ohs.track.export import save_mesh

    pid = new_project(env, "Result differs")
    sid = clone(env, pid, "F")
    b, sd = scan_url(env, pid, sid), scan_dir(env, pid, sid)
    d = load_ply(sd / "result" / "mesh.ply")
    save_mesh(sd / "result" / "mesh.ply", d["vertices"] + 1e-3, d["faces"], d["normals"], quality=d["quality"])
    env["studio"].processing.forget(pid)
    snap, row = snapshot(sd), row_bytes(env, pid, sid)
    plan = get(b + "/keep?mode=engineering").json()
    assert snapshot(sd) == snap
    st = json.loads((sd / "process" / "state.json").read_text(encoding="utf-8"))
    name = f"{st['edits'][st['cursor'] - 1]['id']}.npz" if st["cursor"] else "base.npz"
    cache = sd / "process" / "cache" / name
    assert plan["ok"] and plan["kept_step_bytes"] == cache.stat().st_size
    r = post(b + "/keep", {"mode": "engineering"})
    assert r.status_code == 200, r.text
    assert r.json()["freed_bytes"] == plan["frees_bytes"] == row - size_of(sd)
    assert sorted(p.name for p in (sd / "process" / "cache").iterdir()) == [name]
    assert post(b + "/check", {"tolerance_mm": 0.1}).status_code == 200


# --------------------------------------------------------------------------- busy scans
def test_a_busy_scan_is_not_moved(env):
    pid = new_project(env, "Busy")
    sid = clone(env, pid, "C")
    b, st = scan_url(env, pid, sid), env["studio"]
    assert get(b + "/keep?mode=engineering").json()["ok"] is True
    release = threading.Event()

    def blocker(ctx):
        release.wait(60)
        return {}

    hold = st.jobs.submit("test_hold", blocker, meta={"project_id": pid, "scan_id": sid})
    queued = st.jobs.submit("process_track", lambda ctx: {}, meta={"project_id": pid, "scan_id": sid})
    try:
        plan = get(b + "/keep?mode=engineering").json()
        assert plan["ok"] is False and "busy" in plan["refusal"]["detail"]
        size0 = size_of(scan_dir(env, pid, sid))
        err(post(b + "/keep", {"mode": "engineering"}), 409, "busy")
        release.set()
        wait_job(env, hold.id)
        # the second job may still be queued or running: still busy until it is done
        if get(f"{env['url']}/api/jobs/{queued.id}").json()["state"] in ("queued", "running"):
            err(post(b + "/keep", {"mode": "engineering"}), 409, "busy")
        wait_job(env, queued.id)
        assert size_of(scan_dir(env, pid, sid)) == size0
    finally:
        release.set()
    st.projects.update_scan(pid, sid, status="processing")  # e.g. left by a crash: not moved either
    err(post(b + "/keep", {"mode": "engineering"}), 409, "busy")
    st.projects.update_scan(pid, sid, status="processed")
    r = post(b + "/keep", {"mode": "engineering"})
    assert r.status_code == 200, r.text
    rec_only = scan_url(env, pid, add_recorded(env, pid, "Recorded only"))
    err(post(rec_only + "/keep", {"mode": "standard", "confirm": True}), 409, "no mesh")  # nothing would be left
    err(post(rec_only + "/keep", {"mode": "engineering"}), 409, "nothing to free")  # no stages, no history
    err(post(b + "/keep", {"mode": "sometimes"}), 422, "keep the result only")
    err(get(b + "/keep?mode=sometimes"), 422, "keep the result only")
    err(get(f"{env['url']}/api/projects/{pid}/scans/scan-missing/keep?mode=engineering"), 404, "no such scan")


# --------------------------------------------------------------------------- failures
def test_a_failure_half_way_leaves_a_scan_that_opens(env, monkeypatch):
    import ohs.server.keep as keep

    pid = new_project(env, "Half way")
    sid = clone(env, pid, "D")
    b, sd = scan_url(env, pid, sid), scan_dir(env, pid, sid)
    edit(env, pid, sid, "smooth", {"method": "taubin", "strength": 0.2})
    real = keep._unlink

    def stuck(path: Path) -> None:
        if "track" in Path(path).parts:  # a file of the track stage is held open by another program
            raise PermissionError(13, "The process cannot access the file because it is being used by another process",
                                  str(path))
        real(path)

    monkeypatch.setattr(keep, "_unlink", stuck)
    monkeypatch.setattr(keep, "RETRY_S", 0.0)
    size0 = size_of(sd)
    body = err(post(b + "/keep", {"mode": "engineering"}), 409, "still opens")
    assert "could not be removed" in body["detail"]
    freed = size0 - size_of(sd)
    assert 0 < freed
    # the scan opens and everything that reads it works
    proj = get(f"{env['url']}/api/projects/{pid}").json()
    scan = next(s for s in proj["scans"] if s["id"] == sid)
    assert scan["keep"] == "engineering" and scan["freed"][-1]["bytes"] == freed
    s = get(b + "/process").json()
    assert s["current"] is not None and s["edits"] == []
    assert get(b + "/files/mesh.ply").status_code == 200
    assert post(b + "/check", {"tolerance_mm": 0.1}).status_code == 200
    edit(env, pid, sid, "fill_holes", {"max_hole_mm": 2.0})
    # freeing again finishes the job, and says what is left exactly
    monkeypatch.setattr(keep, "_unlink", real)
    plan = get(b + "/keep?mode=engineering").json()
    assert plan["ok"] is True and {p["part"] for p in plan["remove"]} >= {"stages"}
    size1 = size_of(sd)
    r = post(b + "/keep", {"mode": "engineering"})
    assert r.status_code == 200, r.text
    assert r.json()["freed_bytes"] == plan["frees_bytes"] == size1 - size_of(sd)
    assert stage_dirs(sd) == []


# --------------------------------------------------------------------------- a merged scan
def test_a_merged_scan_keeps_its_result_only(env):
    st = env["studio"]
    pid = new_project(env, "Merged")
    tp, ts = env["template"]
    scan, _ = st.projects.add_scan(pid, "A + B", "markerless")
    sid = scan["id"]
    sd = scan_dir(env, pid, sid)
    (sd / "result").mkdir(parents=True)
    for n in ("mesh.ply", "mesh.stl", "laser_points.ply", "report.json"):  # what a merge writes (no trajectory)
        shutil.copy2(scan_dir(env, tp, ts) / "result" / n, sd / "result" / n)
    st.projects.update_scan(pid, sid, status="processed", n_frames=10, merged_from=[{"id": "x", "name": "A"},
                                                                                     {"id": "y", "name": "B"}])
    b = scan_url(env, pid, sid)
    s = get(b + "/process").json()  # taken over: the result becomes the track stage
    assert s["base"]["stage"] == "track"
    # found on the way (FEATURE_PLAN R2): Refine with lasers reads only the stage's laser points
    j = run_job(env, post(b + "/process/refine", {}), "process_refine")
    assert get(b + "/process").json()["stages"]["refine"]["state"] in ("done", "skipped"), j

    plan = get(b + "/keep?mode=engineering").json()
    assert plan["ok"] is False and "no recording of its own" in plan["refusal"]["detail"]
    plan = get(b + "/keep?mode=standard").json()
    assert plan["ok"] is True and "remake" not in plan["loses"] and "refine" in plan["loses"]
    size0 = size_of(sd)
    r = post(b + "/keep", {"mode": "standard", "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["freed_bytes"] == plan["frees_bytes"] == size0 - size_of(sd) > 0
    assert stage_dirs(sd) == []
    assert post(b + "/check", {"tolerance_mm": 0.1}).status_code == 200
    err(post(b + "/process/refine", {}), 409, "removed to free space")


# --------------------------------------------------------------------------- a whole project
def test_a_whole_project(env):
    st = env["studio"]
    pid = new_project(env, "Whole project")
    x, y = clone(env, pid, "X"), clone(env, pid, "Y")
    z = add_recorded(env, pid, "Z")  # recorded, no mesh yet
    w = clone(env, pid, "W")
    st.projects.update_scan(pid, w, status="processing")  # busy: skipped, with the reason
    purl = f"{env['url']}/api/projects/{pid}"
    r = get(purl + "/keep?mode=engineering")
    assert r.status_code == 200, r.text
    plan = r.json()
    by = {p["scan_id"]: p for p in plan["scans"]}
    assert set(by) == {x, y, z, w} and by[w]["ok"] is False and "busy" in by[w]["refusal"]["detail"]
    assert plan["frees_bytes"] == sum(p["frees_bytes"] for p in plan["scans"] if p["ok"]) > 0
    assert plan["n_scans"] == sum(1 for p in plan["scans"] if p["ok"] and p["frees_bytes"] > 0)
    pdir = st.projects.project_dir(pid)
    size0 = size_of(pdir / "scans")
    r = post(purl + "/keep", {"mode": "engineering"})
    assert r.status_code == 200, r.text
    out = r.json()
    res = {q["scan_id"]: q for q in out["results"]}
    assert res[w]["ok"] is False and "busy" in res[w]["error"]["detail"]
    assert res[x]["ok"] and res[y]["ok"]
    assert out["freed_bytes"] == plan["frees_bytes"] == size0 - size_of(pdir / "scans")
    keeps = {s["id"]: s["keep"] for s in get(purl).json()["scans"]}
    assert keeps[x] == keeps[y] == "engineering" and keeps[w] == "full"
    assert z in keeps
    err(post(purl + "/keep", {"mode": "standard"}), 409, "cannot be undone")
    err(get(f"{env['url']}/api/projects/no-such-0000/keep?mode=engineering"), 404, "project")
