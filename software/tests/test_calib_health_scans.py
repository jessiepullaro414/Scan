"""Calibration health, part 3 (FEATURE_PLAN H3), end to end: scans processed with H2's slightly
wrong calibrations, and the status they give.

* ``process_recording`` writes ``report["health_status"]``: what that scan alone says. With H2's
  perturbed calibrations (``tools/calib_health_study.py``) on a 90-frame full-resolution bracket
  scan it names the right part; with the true calibration it stays Valid on the bracket and on the
  hard scenes of H2 (a dark part, a speckled one standing in for a shiny one, a 0.5 mm plate, 90
  frames each), and so does the joined status over them. On the CPU path only the cameras are
  judged; on a short scan (12 frames) too.
* Through the server: the Track stage keeps each scan's signs in ``setup.json`` and the setup
  state's ``calibration_health`` counts the scans made with its calibration.

Run on the GPU path and with ``OHS_COMPUTE=cpu``. Each processing runs in a child process with a
hard time limit (:data:`PROCESS_TIMEOUT_S`): while this was built the CPU path's tracking crashed
natively now and then and once spun for hours (``MAINTENANCE_PLAN.md``, F6, mended 2026-11-19), and
anything like it must fail one test with a message, not end or hang the whole run. On the CPU path
only the cameras are
judged (by the row error, measured on the rectified images: the same on both paths, H2), so there
the tests process only what that path can judge.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("open3d")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import calib_health_study as study  # noqa: E402

from ohs.calib import health  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from recordings import cached_recording, scan_recording  # noqa: E402

pytestmark = pytest.mark.slow
SOFTWARE = Path(__file__).resolve().parents[1]
CPU = os.environ.get("OHS_COMPUTE", "").strip().lower() == "cpu"
PROCESS_TIMEOUT_S = 900  # one 90-frame full-resolution scan: about 20 s on the GPU, 60-100 s on the CPU here
_CHILD = "; ".join([
    "import sys",
    "from ohs.app.process import ProcessParams, process_recording",
    "from ohs.types import ScannerCalibration",
    "rec, out, cal = sys.argv[1:4]",
    "process_recording(rec, out, calib=ScannerCalibration.load(cal), "
    "params=ProcessParams(export=('ply',), row_error_every=10))",
])


@pytest.fixture(scope="module")
def short_scan(tmp_path_factory) -> Path:
    # the recording of test_calib_health_study.py (the same arguments: one render, shared by the cache)
    return scan_recording(tmp_path_factory.mktemp("h3") / "rec", scene="bracket", n_frames=12, image_size=(1280, 800),
                          realistic=True, seed=7, depth_every=0)


def _recording(tmp_path_factory, name: str, **change) -> Path:
    """One of H2's recordings (``study.RECORDINGS``), from the test cache."""
    spec = dict(study.RECORDINGS[name], **change)
    return cached_recording(tmp_path_factory.mktemp("h3rec") / "rec", study.make_recording, spec=spec)


@pytest.fixture(scope="module")
def bracket(tmp_path_factory) -> Path:
    # H2's bracket with its own seed and 90 frames (MIN_FRAMES: the laser and IMU signs need that many)
    return _recording(tmp_path_factory, "bracket-s0", seed=7, frames=health.MIN_FRAMES)


@pytest.fixture(scope="module")
def bracket_true(bracket, tmp_path_factory) -> dict:
    """The bracket processed with its true calibration (the report)."""
    return _process(bracket, tmp_path_factory.mktemp("h3true") / "true", RecordingReader(bracket).calibration())


def _process(rec: Path, out: Path, calib) -> dict:
    """``process_recording`` in a child process with a hard time limit; its report."""
    out.mkdir(parents=True, exist_ok=True)
    cal = out.with_name(out.name + "-calibration.json")
    calib.save(cal)
    try:
        r = subprocess.run([sys.executable, "-c", _CHILD, str(rec), str(out), str(cal)], cwd=SOFTWARE,
                           capture_output=True, text=True, timeout=PROCESS_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        pytest.fail(f"processing {rec.name} did not finish in {PROCESS_TIMEOUT_S} s and was stopped "
                    "(see MAINTENANCE_PLAN.md, F6 / F13 on the CPU path)")
    if r.returncode != 0:
        pytest.fail(f"processing {rec.name} ended with exit code {r.returncode} (a native crash is F6 in "
                    f"MAINTENANCE_PLAN.md): {r.stderr[-3000:]}")
    return json.loads((out / "report.json").read_text(encoding="utf-8"))


def _item(rep: dict, k: int) -> dict:
    """The scan as the setup state keeps it, ``k`` minutes old."""
    it = health.scan_item(rep, when=datetime.fromtimestamp(time.time() - 60 * k, timezone.utc).isoformat())
    assert it is not None
    return it


def test_the_status_names_the_part_of_each_perturbed_calibration(bracket, bracket_true, tmp_path):
    true = RecordingReader(bracket).calibration()
    base = bracket_true
    gpu = base["params"]["compute"]["tracking_model"] == "gpu"
    s = base["health_status"]
    assert s["source"] == "scan" and s["state"] == "valid", (s["reason"], s["signs"])
    if gpu:
        assert s["parts"] == {"cameras": "valid", "lasers": "valid", "imu": "valid"} and s["note"] is None
    else:  # the CPU path: the lasers and the IMU are not judged, neither stale nor fine
        assert s["parts"] == {"cameras": "valid", "lasers": None, "imu": None}
        assert "cannot be judged on this computer" in s["note"]
    faults = [("camera_pitch", 0.03, "cameras"), ("laser_offset:vertical", 0.2, "lasers"), ("imu_rotation", 3.0, "imu")]
    for fault, size, part in faults:
        if not gpu and part != "cameras":
            continue  # not judged on the CPU path (test_calib_health_status.py checks that rule on H2's CPU runs)
        rep = _process(bracket, tmp_path / fault.replace(":", "-"), study.perturb(true, fault, size))
        s = rep["health_status"]
        joined = health.status(scans=[_item(rep, 2), _item(rep, 1), _item(base, 0)])
        assert s["state"] != "valid" and s["part"] == part, (fault, s["reason"], s["signs"])
        assert joined["state"] == s["state"] and joined["part"] == part, (fault, joined["reason"])
        assert joined["reason"].startswith("2 of the last 3 scans"), joined["reason"]


def test_a_short_scan_judges_the_cameras_only(short_scan, tmp_path):
    true = RecordingReader(short_scan).calibration()
    s = _process(short_scan, tmp_path / "true", true)["health_status"]
    assert s["state"] == "valid" and s["parts"] == {"cameras": "valid", "lasers": None, "imu": None}, s
    assert "12 frames" in s["note"] or "without a GPU" in s["note"]
    s = _process(short_scan, tmp_path / "pitch", study.perturb(true, "camera_pitch", 0.03))["health_status"]
    assert s["state"] != "valid" and s["part"] == "cameras", (s["reason"], s["signs"])


@pytest.mark.parametrize("name", ["bracket-dark", "bracket-speckle", "plate-0.5mm"])
def test_the_true_calibration_stays_valid_on_the_hard_scenes(name, bracket_true, tmp_path_factory):
    """H2's hard scenes as H2 rendered them (90 frames): the scan's own status and the joined status
    (this scene's scan with two of the bracket) stay Valid. GPU path only: on the CPU path only the
    row error is judged, which does not depend on the path (H2: 0.062 px on both)."""
    if CPU:
        pytest.skip("the CPU path judges the row error only, the same on both paths (H2); see the module docstring")
    rec = _recording(tmp_path_factory, name)
    out = tmp_path_factory.mktemp("h3out")
    rep = _process(rec, out / "scan", RecordingReader(rec).calibration())
    s = rep["health_status"]
    assert s["state"] == "valid", (name, s["reason"], s["signs"])
    joined = health.status(scans=[_item(rep, 0), _item(rep, 1), _item(bracket_true, 2)])
    assert joined["state"] == "valid", (name, joined["reason"])


def test_the_track_stage_feeds_the_setup_state(tmp_path):
    """A scan tracked in the app keeps its signs in ``setup.json``; the setup state counts it when
    it was made with the target's calibration (here a file without an id: matched by its numbers)."""
    pytest.importorskip("fastapi")
    requests = pytest.importorskip("requests")
    from ohs.app.config import Config
    from ohs.server.app import create_app
    from ohs.server.testing import ServerThread

    rec = scan_recording(tmp_path / "rec", n_frames=10, image_size=(640, 400), seed=3, depth_every=0)
    cfg = Config()
    cfg.paths.calibration = str(tmp_path / "device" / "calibration.json")
    (tmp_path / "device").mkdir()
    RecordingReader(rec).calibration().save(cfg.paths.calibration)
    app = create_app(cfg, projects_dir=tmp_path / "projects", demo_dir=tmp_path / "demo", static_dir=tmp_path / "static",
                     data_dir=tmp_path / "data", config_path=tmp_path / "config.toml")
    with ServerThread(app) as srv:
        st, url = app.state.studio, srv.url
        pid = requests.post(url + "/api/projects", json={"name": "Health"}, timeout=30).json()["id"]
        for k in range(2):
            scan, rec_dir = st.projects.add_scan(pid, f"Scan {k}", "markerless")
            shutil.copytree(rec, rec_dir)
            st.projects.update_scan(pid, scan["id"], status="recorded", n_frames=10, duration_s=0.3)
            r = requests.post(f"{url}/api/projects/{pid}/scans/{scan['id']}/process/track", json={"quality": "preview"},
                              timeout=30)
            assert r.status_code == 200, r.text
            jid = r.json()["id"]
            deadline = time.monotonic() + 180
            while (j := requests.get(f"{url}/api/jobs/{jid}", timeout=30).json())["state"] in ("queued", "running"):
                assert time.monotonic() < deadline
                time.sleep(0.1)
            assert j["state"] == "done", j
            rep = json.loads((st.projects.result_dir(pid, scan["id"]) / "report.json").read_text(encoding="utf-8"))
            assert rep["health_status"]["source"] == "scan"
        kept = json.loads((tmp_path / "data" / "setup.json").read_text(encoding="utf-8"))["scan_health"]
        assert len(kept) == 2 and {e["scan"]["project_id"] for e in kept} == {pid}
        assert kept[0]["calibration"] == health.calibration_key(RecordingReader(rec).calibration())
        assert kept[0]["frames"] == 10
        h = requests.get(url + "/api/setup", timeout=30).json()["calibration_health"]
        assert h["scans"]["used"] == 2 and h["state"] in health.STATES, h
