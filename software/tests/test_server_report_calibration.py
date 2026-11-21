"""UX_PLAN R6 and R8: the report's Calibration part describes the calibration the scan was made with, and a merged
scan's report says what it was merged from.

* a simulator scan (its recording's ``meta.json`` says ``source: sim``) is described by its recording's own
  calibration (the demo's), not by Setup's target for the simulator (which has nothing in this app): no "Not
  calibrated" laser lines, no "No calibration for the … laser lines" warning;
* a scan of the scanner whose calibration is no longer Setup's says only what its own file holds; once it is Setup's
  file still, Setup's last calibration check and the laser lines the build lacks come beside it;
* a merged scan says "Merged from", the merge's notes and warnings, and a sentence of its own for its calibration
  status (not "processed before the app did that").

Real server, real report page, the 10-frame sphere scan.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

from ohs.app.config import Config  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402
from recordings import scan_recording  # noqa: E402
from test_report_html import Page  # noqa: E402

T = 60


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("repcal")
    rec = scan_recording(root / "rec", n_frames=10, image_size=(640, 400), seed=3, depth_every=0)
    cfg = Config()
    cfg.paths.calibration = str(root / "no-calibration.json")
    app = create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                     data_dir=root / "data", config_path=root / "config.toml")
    with ServerThread(app) as srv:
        st = app.state.studio
        pid = requests.post(srv.url + "/api/projects", json={"name": "Calibration"}, timeout=T).json()["id"]
        scan, rec_dir = st.projects.add_scan(pid, "Sphere", "markerless")
        shutil.copytree(rec, rec_dir)
        st.projects.update_scan(pid, scan["id"], status="recorded", n_frames=len(RecordingReader(rec_dir)))
        e = {"url": srv.url, "studio": st, "pid": pid, "sid": scan["id"], "root": root, "rec": rec_dir}
        job = requests.post(base(e) + "/process", json={"quality": "preview"}, timeout=T).json()
        deadline = time.monotonic() + 240
        while requests.get(f"{srv.url}/api/jobs/{job['id']}", timeout=T).json()["state"] in ("queued", "running"):
            assert time.monotonic() < deadline
            time.sleep(0.1)
        yield e


def base(e: dict, sid: str | None = None) -> str:
    return f"{e['url']}/api/projects/{e['pid']}/scans/{sid or e['sid']}"


def calibration_text(e: dict, sid: str | None = None) -> tuple[str, str]:
    r = requests.post(base(e, sid) + "/report/report.html", json={}, timeout=T)
    assert r.status_code == 200, r.text
    p = Page(r.content.decode("utf-8"))
    return p.text("calibration"), p.text("warnings") if "warnings" in p.order else ""


def test_a_simulator_scan_shows_the_demos_calibration(env):
    calib = RecordingReader(env["rec"]).calibration()
    assert calib is not None and calib.lasers  # the demo's calibration has its laser lines
    assert env["studio"].setup.state("sim")["calibration"]["exists"] is False  # Setup's own target has nothing
    cal, warnings = calibration_text(env)
    assert "simulator's own calibration" in cal, cal
    assert "Not calibrated" not in cal and "No calibration for the" not in warnings, (cal, warnings)
    lines = sorted(calib.lasers)
    assert ("Calibrated: the " + ", ".join(lines) + " laser line") in cal
    assert "baseline" not in cal and "apart" in cal  # the cameras' distance in words


def test_a_scanner_scan_shows_its_own_file_and_setups_check_only_while_it_is_setups(env):
    meta_p = env["rec"] / "meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    st = env["studio"]
    dev_path = Path(st.setup.calibration_path("device"))
    try:
        meta_p.write_text(json.dumps({**meta, "source": "camera", "device": "camera"}), encoding="utf-8")
        cal, _ = calibration_text(env)
        assert "scanner's calibration, as it was when this scan was recorded" in cal and "Calibration check" not in cal
        # the same file is Setup's scanner calibration: Setup's check comes beside it
        dev_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(env["rec"] / "calibration.json", dev_path)
        st.setup.store.update("device", last_check={"when": "2026-11-21T08:00:00", "ok": True,
                                                    "verdict": "The check passed: the board reads 0.1 % large."})
        cal, _ = calibration_text(env)
        assert "Calibration checkThe check passed: the board reads 0.1 % large." in cal, cal
        assert "passed: The check passed" not in cal and "Last board check" not in cal
    finally:
        meta_p.write_text(json.dumps(meta), encoding="utf-8")
        dev_path.unlink(missing_ok=True)


def test_a_merged_scan_says_what_it_was_merged_from(env):
    st, pid = env["studio"], env["pid"]
    scan, _ = st.projects.add_scan(pid, "Sphere + Sphere", "markerless")
    sid = scan["id"]
    res = st.projects.scan_dir(pid, sid) / "result"
    shutil.copytree(st.projects.scan_dir(pid, env["sid"]) / "result", res)
    rep = json.loads((res / "report.json").read_text(encoding="utf-8"))
    for k in ("health", "health_status", "health_path"):
        rep.pop(k, None)
    rep.update(merged=True, warnings=["\"Sphere\": a thin part may be missing near the table."],
               scans=[{"name": "Sphere", "table": {"rule": "map", "summary": "Its table is left out (12,000 mm² of its mesh).",
                                                   "warnings": []}}, {"name": "Sphere again", "table": None}])
    rep.setdefault("mesh", {})["cleaning"] = {"summary": "No loose bit was left out as too small (under 1 % of the "
                                                         "triangles each)."}
    (res / "report.json").write_text(json.dumps(rep), encoding="utf-8")
    st.projects.update_scan(pid, sid, status="processed", merged_from=[{"id": env["sid"], "name": "Sphere"},
                                                                       {"id": "scan-x", "name": "Sphere again"}])
    r = requests.post(base(env, sid) + "/report/report.html", json={}, timeout=T)
    assert r.status_code == 200, r.text
    p = Page(r.content.decode("utf-8"))
    made, cal, warn = p.text("made"), p.text("calibration"), p.text("warnings")
    assert "Merged fromSphere, Sphere again" in made, made
    assert "Sphere: its table is left out (12,000 mm² of its mesh)." in made
    assert "No loose bit was left out as too small" in made
    assert "a thin part may be missing near the table" in warn
    assert "processed before the app did that" not in cal and "merge has no recording of its own" in cal, cal
