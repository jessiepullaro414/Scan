"""Keep only the frames Standard quality uses (FEATURE_PLAN.md R3; APP_API.md "What a scan keeps").

"Recording and result" with ``thin: true``: the frames Standard quality skips (every 2nd of a 60 frames a
second recording) are removed from disk; the frames list, ``meta.json`` and the scan say what the
recording holds now; the motion sensor, the events and the calibration stay as they were.

The guards, on a 12-frame 60 frames a second sphere at 640x400 made through the server:

* **the mesh is the same:** Make mesh and Track at Standard quality on the thinned recording give the
  mesh of the full recording with every value bit for bit (each vertex's position, normal, sigma and
  looks, and every face); the file may list the vertices in another order, as two runs of the same full
  recording on the GPU path do. The camera path and the laser points are the same files, byte for byte;
* **the promises of R2:** the plan changes nothing on disk, "Frees X" is the true net drop, it asks once
  more (it cannot be undone), a busy scan is not touched, a failure half way leaves a scan that opens and
  can be finished;
* Best quality still runs on the thinned recording (from the 30 frames a second that stay), the plan
  says so beforehand;
* what cannot be thinned is refused in plain words (a 30 frames a second recording, settings under
  which Standard would pick other frames, a scan that keeps its result only).

Run on the GPU path and with ``OHS_COMPUTE=cpu``.
"""
from __future__ import annotations

import csv
import json
import shutil
import threading
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
from test_server_keep import err, get, post, run_job, scan_dir, scan_url, size_of, wait_job  # noqa: E402

N60 = 12  # frames of the 60 frames a second recording


# --------------------------------------------------------------------------- helpers
def mesh_content(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """The mesh as content, bit for bit and independent of the order the file lists it in: every vertex's
    values (position, normal, sigma, looks) as raw bits, sorted; every face as its three vertices (same
    orientation, smallest first), sorted. A face names its vertices by their values, not by their place:
    a mesh can hold two vertices with the same values in every bit (seen in review: 1 pair in 70,495), and
    which of the two a file lists first is chance."""
    d = load_ply(path)
    V = np.asarray(d["vertices"], dtype=np.float64)
    cols = [V] + [np.asarray(d[k], dtype=np.float64).reshape(len(V), -1) for k in ("normals", "quality", "looks")
                  if d.get(k) is not None]
    bits = np.ascontiguousarray(np.hstack(cols)).view(np.uint64)
    order = np.lexsort(bits.T[::-1])
    _, inv = np.unique(bits, axis=0, return_inverse=True)  # the same number for vertices with the same values
    F = inv.reshape(-1)[np.asarray(d["faces"], dtype=np.int64)]
    r = np.argmin(F, axis=1)
    F = np.stack([F[np.arange(len(F)), (r + i) % 3] for i in range(3)], axis=1)
    return bits[order], F[np.lexsort(F.T[::-1])]


def same_mesh(a: Path, b: Path) -> None:
    (va, fa), (vb, fb) = mesh_content(a), mesh_content(b)
    assert va.shape == vb.shape and np.array_equal(va, vb), f"vertices differ: {a} {b}"
    assert fa.shape == fb.shape and np.array_equal(fa, fb), f"faces differ: {a} {b}"
    assert len(fa) > 100


def frames_listed(rec: Path) -> list[tuple[int, str]]:
    with open(rec / "frames.csv", newline="", encoding="utf-8") as fh:
        return [(int(r["index"]), r["file"]) for r in csv.DictReader(fh)]


def snapshot(sd: Path) -> list[tuple[str, int]]:
    return sorted((p.relative_to(sd).as_posix(), p.stat().st_size) for p in sd.rglob("*") if p.is_file())


def new_project(env: dict, name: str) -> str:
    return post(env["url"] + "/api/projects", {"name": name}).json()["id"]


def add_recorded(env: dict, pid: str, name: str, rec: Path) -> str:
    st = env["studio"]
    scan, rec_dir = st.projects.add_scan(pid, name, "markerless")
    shutil.copytree(rec, rec_dir)
    n = len(RecordingReader(rec_dir))
    st.projects.update_scan(pid, scan["id"], status="recorded", n_frames=n, duration_s=N60 / 60.0)
    return scan["id"]


def entry(env: dict, pid: str, sid: str) -> dict:
    return next(s for s in get(f"{env['url']}/api/projects/{pid}").json()["scans"] if s["id"] == sid)


def made(env: dict, pid: str, sid: str, tmp: Path, name: str) -> Path:
    """Make mesh at Standard, and keep a copy of what it wrote (the result and the track stage)."""
    run_job(env, post(scan_url(env, pid, sid) + "/process", {"quality": "standard"}), "process_scan")
    out = tmp / name
    sd = scan_dir(env, pid, sid)
    shutil.copytree(sd / "result", out / "result")
    shutil.copytree(sd / "process" / "track", out / "track")
    return out


# --------------------------------------------------------------------------- the server
@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("thin")
    rec60 = scan_recording(root / "rec60", n_frames=N60, fps=60.0, image_size=(640, 400), seed=3, depth_every=0)
    rec30 = scan_recording(root / "rec30", n_frames=6, image_size=(640, 400), seed=3, depth_every=0)
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        yield {"srv": srv, "url": srv.url, "root": root, "studio": app.state.studio, "rec60": rec60, "rec30": rec30,
               "cfg": cfg}


# --------------------------------------------------------------------------- the mesh is the same
def test_thinning_keeps_the_standard_mesh_bit_for_bit(env, tmp_path):
    pid = new_project(env, "Thin")
    sid = add_recorded(env, pid, "A", env["rec60"])
    b, sd = scan_url(env, pid, sid), scan_dir(env, pid, sid)
    rec = sd / "recording"
    before = made(env, pid, sid, tmp_path, "before")
    kept_bytes = {n: (rec / n).read_bytes() for n in ("imu.csv", "events.csv", "calibration.json")}
    gt_bytes = (rec / "ground_truth" / "poses.csv").read_bytes()

    # the plan: what goes, what it frees, and that it asks once more; it writes nothing
    snap, row = snapshot(sd), size_of(sd)
    r = get(b + "/keep?mode=engineering&thin=true")
    assert r.status_code == 200, r.text
    plan = r.json()
    assert snapshot(sd) == snap
    assert plan["ok"] is True and plan["thin"] is True and plan["to"] == "engineering" and plan["confirm"] is True
    th = plan["thinned"]
    assert th["frames_before"] == N60 and th["frames"] == N60 // 2 and th["step"] == 2  # what it would hold, and held
    assert th["fps_before"] == pytest.approx(60.0, abs=0.5) and th["fps"] == pytest.approx(30.0, abs=0.5)
    parts = {p["part"]: p for p in plan["remove"]}
    skipped = [f for k, (_, f) in enumerate(frames_listed(rec)) if k % 2 == 1]
    assert parts["frames"]["files"] == len(skipped)
    assert parts["frames"]["bytes"] == sum((rec / "frames" / f).stat().st_size for f in skipped)
    assert {"stages", "undo"} & set(parts)  # and what "recording and result" removes
    assert "best" in plan["loses"] and "recording" in plan["keeps"]
    writes = {w["what"]: w["bytes"] for w in plan["writes"]}
    assert writes["frames_list"] < 0 and writes["meta"] > 0  # the shorter frames list, the note in meta.json
    assert plan["frees_bytes"] == sum(p["bytes"] for p in plan["remove"]) - sum(writes.values()) > 0
    plain = get(b + "/keep?mode=engineering").json()
    assert plain["thin"] is False and "frames" not in {p["part"] for p in plain["remove"]} and plain["confirm"] is False
    assert plan["frees_bytes"] > plain["frees_bytes"]

    # asked once more: nothing happens without the second yes
    err(post(b + "/keep", {"mode": "engineering", "thin": True}), 409, "cannot be undone")
    assert snapshot(sd) == snap
    r = post(b + "/keep", {"mode": "engineering", "thin": True, "confirm": True})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["freed_bytes"] == plan["frees_bytes"] == row - size_of(sd)  # the true net drop

    # what the recording holds now: the frames Standard uses, with their own indices and times
    listed = frames_listed(rec)
    assert [i for i, _ in listed] == list(range(0, N60, 2))
    assert sorted(p.name for p in (rec / "frames").iterdir()) == sorted(f for _, f in listed)
    full_times = RecordingReader(env["rec60"]).times()
    assert np.array_equal(RecordingReader(rec).times(), full_times[::2])
    assert {n: (rec / n).read_bytes() for n in kept_bytes} == kept_bytes  # motion sensor, events, calibration
    assert (rec / "ground_truth" / "poses.csv").read_bytes() == gt_bytes
    meta = json.loads((rec / "meta.json").read_text(encoding="utf-8"))
    assert meta["n_frames"] == N60 // 2
    assert meta["thinned"]["step"] == 2 and meta["thinned"]["frames_before"] == N60
    assert meta["thinned"]["fps"] == pytest.approx(30.0, abs=0.5) and meta["thinned"]["fps_before"] == pytest.approx(60.0, abs=0.5)
    scan = entry(env, pid, sid)
    assert scan["keep"] == "engineering" and scan["has_recording"] is True and scan["n_frames"] == N60 // 2
    assert scan["thinned"]["fps"] == pytest.approx(30.0, abs=0.5) and scan["thinned"]["frames_before"] == N60
    assert scan["freed"][-1]["bytes"] == out["freed_bytes"] and scan["freed"][-1]["thin"] is True

    # Make mesh at Standard: the same mesh, the same path and laser points
    after = made(env, pid, sid, tmp_path, "after")
    same_mesh(before / "result" / "mesh.ply", after / "result" / "mesh.ply")
    same_mesh(before / "track" / "mesh.ply", after / "track" / "mesh.ply")
    for n in ("trajectory.csv", "laser_points.ply"):
        assert (before / "result" / n).read_bytes() == (after / "result" / n).read_bytes(), n
    rep = json.loads((after / "result" / "report.json").read_text(encoding="utf-8"))
    rep0 = json.loads((before / "result" / "report.json").read_text(encoding="utf-8"))
    assert rep["frames"]["processed"] == rep0["frames"]["processed"] == N60 // 2
    assert rep["frames"]["recorded_fps"] == pytest.approx(30.0, abs=0.5) and rep["frames"]["step"] == 1

    # Track on its own at Standard: the same stage mesh
    run_job(env, post(b + "/process/track", {"quality": "standard"}), "process_track")
    same_mesh(before / "track" / "mesh.ply", sd / "process" / "track" / "mesh.ply")

    # Best quality still runs, from the frames that stay (the plan said so); it now uses the Standard frames
    run_job(env, post(b + "/process", {"quality": "best"}), "process_scan")
    best = json.loads((sd / "result" / "report.json").read_text(encoding="utf-8"))
    assert best["frames"]["processed"] == N60 // 2 and best["frames"]["max_frame_rate"] == 0.0

    # nothing left to thin; no way back to every frame; the result only still works afterwards
    again = get(b + "/keep?mode=engineering&thin=true").json()
    assert "frames" not in {p["part"] for p in again["remove"]} and again["confirm"] is False
    err(post(b + "/keep", {"mode": "full"}), 409, "cannot be brought back")
    r = post(b + "/keep", {"mode": "standard", "confirm": True})
    assert r.status_code == 200, r.text
    p = get(b + "/keep?mode=engineering&thin=true").json()
    assert p["ok"] is False and "cannot keep" in p["refusal"]["detail"]


# --------------------------------------------------------------------------- refusals
def test_what_cannot_be_thinned_is_refused_in_plain_words(env):
    pid = new_project(env, "Refused")
    s30 = add_recorded(env, pid, "Thirty", env["rec30"])
    b30 = scan_url(env, pid, s30)
    p = get(b30 + "/keep?mode=engineering&thin=true").json()
    assert p["ok"] is False and "every one of them" in p["refusal"]["detail"]  # 30 a second: nothing to thin
    err(get(b30 + "/keep?mode=standard&thin=true"), 422, "recording and the result")
    err(post(b30 + "/keep", {"mode": "full", "thin": True}), 422, "recording and the result")

    s60 = add_recorded(env, pid, "Sixty", env["rec60"])
    b60, sd = scan_url(env, pid, s60), scan_dir(env, pid, s60)
    cfg = env["studio"].cfg
    snap = snapshot(sd)
    for field, value, needle in (("frame_motion_deg", 2.0, "by motion"), ("frame_step", 2, "every 2nd frame"),
                                 ("standard_frame_rate", 0.0, "every frame")):
        old = getattr(cfg.process, field)
        setattr(cfg.process, field, value)
        try:
            p = get(b60 + "/keep?mode=engineering&thin=true").json()
            assert p["ok"] is False and needle in p["refusal"]["detail"], (field, p["refusal"])
            assert "same mesh" in p["refusal"]["detail"]
            err(post(b60 + "/keep", {"mode": "engineering", "thin": True, "confirm": True}), 409, needle)
        finally:
            setattr(cfg.process, field, old)
    assert snapshot(sd) == snap


# --------------------------------------------------------------------------- busy, and a failure half way
def test_a_busy_scan_is_not_thinned(env):
    pid = new_project(env, "Busy")
    sid = add_recorded(env, pid, "B", env["rec60"])
    b, sd, st = scan_url(env, pid, sid), scan_dir(env, pid, sid), env["studio"]
    release = threading.Event()
    hold = st.jobs.submit("test_hold", lambda ctx: release.wait(60) and {}, meta={"project_id": pid, "scan_id": sid})
    try:
        snap = snapshot(sd)
        p = get(b + "/keep?mode=engineering&thin=true").json()
        assert p["ok"] is False and "busy" in p["refusal"]["detail"]
        err(post(b + "/keep", {"mode": "engineering", "thin": True, "confirm": True}), 409, "busy")
        assert snapshot(sd) == snap
    finally:
        release.set()
        wait_job(env, hold.id)
    st.projects.update_scan(pid, sid, status="recording")
    err(post(b + "/keep", {"mode": "engineering", "thin": True, "confirm": True}), 409, "busy")
    st.projects.update_scan(pid, sid, status="recorded")


def test_a_failure_half_way_leaves_a_scan_that_opens_and_can_be_finished(env, monkeypatch, tmp_path):
    import ohs.server.keep as keep

    pid = new_project(env, "Half way")
    sid = add_recorded(env, pid, "H", env["rec60"])
    b, sd = scan_url(env, pid, sid), scan_dir(env, pid, sid)
    before = made(env, pid, sid, tmp_path, "before")
    rec = sd / "recording"
    held = frames_listed(rec)[3][1]  # a frame Standard skips, held open by another program
    real = keep._unlink

    def stuck(path: Path) -> None:
        if Path(path).name == held:
            raise PermissionError(13, "The process cannot access the file because it is being used by another process",
                                  str(path))
        real(path)

    monkeypatch.setattr(keep, "_unlink", stuck)
    monkeypatch.setattr(keep, "RETRY_S", 0.0)
    size0 = size_of(sd)
    body = err(post(b + "/keep", {"mode": "engineering", "thin": True, "confirm": True}), 409, "still opens")
    assert "could not be removed" in body["detail"]
    assert 0 < size0 - size_of(sd)
    # the recording reads as thinned and is valid; the frame left behind is not listed
    assert [i for i, _ in frames_listed(rec)] == list(range(0, N60, 2))
    assert (rec / "frames" / held).is_file()
    assert entry(env, pid, sid)["thinned"]["frames_before"] == N60
    assert get(b + "/files/mesh.ply").status_code == 200
    # freeing again finishes the job and says what is left exactly
    monkeypatch.setattr(keep, "_unlink", real)
    plan = get(b + "/keep?mode=engineering&thin=true").json()
    assert plan["ok"] is True and {p["part"]: p["files"] for p in plan["remove"]}["frames"] == 1
    size1 = size_of(sd)
    r = post(b + "/keep", {"mode": "engineering", "thin": True, "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["freed_bytes"] == plan["frees_bytes"] == size1 - size_of(sd)
    assert not (rec / "frames" / held).exists()
    meta = json.loads((rec / "meta.json").read_text(encoding="utf-8"))
    assert meta["thinned"]["frames_before"] == N60  # the first move's note, not overwritten by the second
    # and the scan's mesh can be made again: the same mesh
    after = made(env, pid, sid, tmp_path, "after")
    same_mesh(before / "result" / "mesh.ply", after / "result" / "mesh.ply")


def test_a_frames_list_that_cannot_be_written_leaves_every_frame(env, monkeypatch):
    """The shorter frames list cannot be written (a full disk, a file held open): no frame goes, and the scan
    does not claim to be thinned."""
    import ohs.server.keep as keep

    pid = new_project(env, "List held")
    sid = add_recorded(env, pid, "L", env["rec60"])
    b, sd = scan_url(env, pid, sid), scan_dir(env, pid, sid)
    rec = sd / "recording"
    real = keep._write_bytes

    def held(path: Path, data: bytes) -> None:
        if Path(path).name == "frames.csv":
            raise PermissionError(13, "The process cannot access the file because it is being used by another process",
                                  str(path))
        real(path, data)

    monkeypatch.setattr(keep, "_write_bytes", held)
    csv0, frames0 = (rec / "frames.csv").read_bytes(), sorted(p.name for p in (rec / "frames").iterdir())
    err(post(b + "/keep", {"mode": "engineering", "thin": True, "confirm": True}), 409, "still opens")
    assert (rec / "frames.csv").read_bytes() == csv0 and sorted(p.name for p in (rec / "frames").iterdir()) == frames0
    assert not [p for p in rec.iterdir() if ".tmp-" in p.name]  # no half-written file left
    scan = entry(env, pid, sid)
    assert "thinned" not in scan and scan["n_frames"] == N60 and scan["has_recording"] is True
    monkeypatch.setattr(keep, "_write_bytes", real)
    r = post(b + "/keep", {"mode": "engineering", "thin": True, "confirm": True})  # and it can be done afterwards
    assert r.status_code == 200, r.text
    assert [i for i, _ in frames_listed(rec)] == list(range(0, N60, 2))


# --------------------------------------------------------------------------- a whole project
def test_a_whole_project_thinned(env):
    pid = new_project(env, "Whole project")
    a = add_recorded(env, pid, "A", env["rec60"])
    c = add_recorded(env, pid, "C", env["rec30"])
    purl = f"{env['url']}/api/projects/{pid}"
    plan = get(purl + "/keep?mode=engineering&thin=true").json()
    by = {p["scan_id"]: p for p in plan["scans"]}
    assert by[a]["ok"] is True and by[c]["ok"] is False and plan["confirm"] is True and plan["thin"] is True
    assert plan["frees_bytes"] == by[a]["frees_bytes"] and plan["n_scans"] == 1
    size0 = size_of(env["studio"].projects.project_dir(pid) / "scans")
    err(post(purl + "/keep", {"mode": "engineering", "thin": True}), 409, "cannot be undone")
    r = post(purl + "/keep", {"mode": "engineering", "thin": True, "confirm": True})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["freed_bytes"] == plan["frees_bytes"] == size0 - size_of(env["studio"].projects.project_dir(pid) / "scans")
    assert entry(env, pid, a)["thinned"]["step"] == 2 and "thinned" not in entry(env, pid, c)
