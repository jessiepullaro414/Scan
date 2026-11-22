"""The thin-parts hint (ENGINE_PLAN X5, step 1) as the server carries it (APP_API.md, "The thin-parts hint").

At the end of Make mesh the server looks at the mesh it made (:func:`ohs.mesh.thin_parts.find_thin_parts`, which
only reads): the whole finding goes into ``result/report.json`` (``thin_parts``), the hint in words into
``ProcessState.made.thin_parts``. A hint only: no setting changes, no job starts, the preset stays. The user can put
it away; it stays away for that scan, also after Make mesh again. A finder that fails costs the scan nothing.

The 10-frame recording here has no thin part to speak of, so where a finding is needed one is handed in.
"""
from __future__ import annotations

import json
import shutil
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

from ohs.app.config import Config  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from ohs.mesh import thin_parts as TP  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402
from recordings import scan_recording  # noqa: E402

T = 30


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("thinparts")
    rec = scan_recording(root / "rec", n_frames=10, image_size=(640, 400), seed=3, depth_every=0)
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        yield {"url": srv.url, "root": root, "studio": app.state.studio, "rec": rec}


def _add_scan(env: dict, name: str) -> tuple[str, str]:
    st = env["studio"]
    pid = requests.post(env["url"] + "/api/projects", json={"name": name}, timeout=T).json()["id"]
    scan, rec_dir = st.projects.add_scan(pid, name, "markerless")
    shutil.copytree(env["rec"], rec_dir)
    n = len(RecordingReader(rec_dir))
    st.projects.update_scan(pid, scan["id"], status="recorded", n_frames=n, duration_s=n / 30.0)
    return pid, scan["id"]


def _make_mesh(env: dict, base: str) -> dict:
    r = requests.post(base + "/process", json={"quality": "preview"}, timeout=T)
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        j = requests.get(f"{env['url']}/api/jobs/{jid}", timeout=T).json()
        if j["state"] not in ("queued", "running"):
            assert j["state"] == "done", j
            return j
        time.sleep(0.1)
    raise AssertionError("Make mesh did not finish in time")


def _walls_found(calls: list):
    """A finder that says it found thin walls (as the standing walls of the scorecard read), and keeps its call."""
    def fake(V, F, **kw):
        calls.append({"n_faces": len(F), **{k: v for k, v in kw.items() if k != "sigma"}})
        return {"version": 1, "voxel_mm": kw["voxel_mm"], "preset": kw.get("preset_id"), "found": True, "suggest": True,
                "why_not": None, "suggest_preset": TP.THIN_PRESET_ID, "suggest_voxel_mm": 0.5,
                "walls": {"found": True, "area_mm2": 1794.0, "share": 0.0051, "reads_mm": 1.84, "reads_range_mm": [1.33, 2.54],
                          "estimate_mm": [0.5, 1.7], "n_regions": 3, "regions": []},
                "plates": {"found": False, "plates": []}, "holes": {"found": False, "count": 0}, "bricks": {"n": 92}}
    return fake


def test_make_mesh_says_what_it_found_and_changes_nothing(env, monkeypatch):
    calls: list = []
    monkeypatch.setattr(TP, "find_thin_parts", _walls_found(calls))
    pid, sid = _add_scan(env, "Standing walls")
    base = f"{env['url']}/api/projects/{pid}/scans/{sid}"
    preset_before = requests.get(base + "/preset", timeout=T).json()
    _make_mesh(env, base)
    st = requests.get(base + "/process", timeout=T).json()
    hint = st["made"]["thin_parts"]
    assert hint["suggest"] is True and hint["found"] == ["walls"] and hint["dismissed"] is False
    assert hint["preset_id"] == TP.THIN_PRESET_ID and hint["voxel_mm"] == calls[0]["voxel_mm"]
    w = hint["words"]
    assert w["title"] == "Thin walls found" and w["action"] == "Make the mesh again with this preset"
    assert "1,794 mm²" in " ".join(w["found"]) and "times as long" in " ".join(w["cost"])
    # the finder had the mesh Make mesh ended with, its detail and the scan's preset
    assert calls[0]["n_faces"] == st["current"]["stats"]["triangles"]
    assert calls[0]["preset_id"] == preset_before["id"]
    res = json.loads((env["studio"].projects.result_dir(pid, sid) / "report.json").read_text(encoding="utf-8"))
    assert res["thin_parts"]["walls"]["area_mm2"] == 1794.0
    # a hint only: the preset and its values are as they were, and no other job was started
    assert requests.get(base + "/preset", timeout=T).json() == preset_before
    jobs = requests.get(env["url"] + "/api/jobs", timeout=T).json()
    assert not [j for j in (jobs if isinstance(jobs, list) else jobs.get("jobs", []))
                if j.get("meta", {}).get("scan_id") == sid and j["state"] in ("queued", "running")]


def test_a_dismissed_hint_stays_away_for_the_scan(env, monkeypatch):
    monkeypatch.setattr(TP, "find_thin_parts", _walls_found([]))
    pid, sid = _add_scan(env, "Sheet")
    base = f"{env['url']}/api/projects/{pid}/scans/{sid}"
    _make_mesh(env, base)
    r = requests.post(base + "/process/hints/thin-parts/dismiss", timeout=T)
    assert r.status_code == 200, r.text
    assert r.json()["made"]["thin_parts"]["dismissed"] is True
    assert requests.get(base + "/process", timeout=T).json()["made"]["thin_parts"]["dismissed"] is True
    assert requests.post(base + "/process/hints/thin-parts/dismiss", timeout=T).status_code == 200  # twice: the same
    assert env["studio"].projects.scan_entry(pid, sid)["hints_dismissed"] == ["thin_parts"]
    _make_mesh(env, base)  # made again: the finding is new, the user's answer stays
    hint = requests.get(base + "/process", timeout=T).json()["made"]["thin_parts"]
    assert hint["suggest"] is True and hint["dismissed"] is True


def test_the_real_finder_runs_reads_only_and_a_failing_one_costs_nothing(env, monkeypatch):
    pid, sid = _add_scan(env, "Sphere")
    base = f"{env['url']}/api/projects/{pid}/scans/{sid}"
    real = TP.find_thin_parts
    seen: dict = {}

    def watched(V, F, **kw):  # the result's mesh as it was before the finder ran
        seen["bytes"] = next(env["studio"].projects.scan_dir(pid, sid).glob(".remake-*/result/mesh.ply")).read_bytes()
        return real(V, F, **kw)

    monkeypatch.setattr(TP, "find_thin_parts", watched)
    _make_mesh(env, base)
    hint = requests.get(base + "/process", timeout=T).json()["made"]["thin_parts"]
    res = json.loads((env["studio"].projects.result_dir(pid, sid) / "report.json").read_text(encoding="utf-8"))
    f = res["thin_parts"]
    assert f["version"] == TP.VERSION and f["voxel_mm"] == res["mesh"]["voxel_mm"]
    assert hint["suggest"] is f["suggest"] and (hint["words"] is None) is (not f["suggest"])
    # the finder only reads: the mesh the scan got is the one there before it ran, byte for byte
    assert (env["studio"].projects.result_dir(pid, sid) / "mesh.ply").read_bytes() == seen["bytes"]

    def broken(*a, **k):
        raise RuntimeError("no")

    monkeypatch.setattr(TP, "find_thin_parts", broken)
    _make_mesh(env, base)
    st = requests.get(base + "/process", timeout=T).json()
    assert st["made"] is not None and "thin_parts" not in st["made"] and st["current"] is not None


def test_dismiss_for_an_unknown_scan_is_404(env):
    pid, _ = _add_scan(env, "Other")
    r = requests.post(f"{env['url']}/api/projects/{pid}/scans/nosuchscan/process/hints/thin-parts/dismiss", timeout=T)
    assert r.status_code == 404
