"""UX_PLAN R5: every stage's calibration status reads the compute path the scan was made on.

Straighten and Build the surface fuse at given poses, so their own report tracks nothing; read on its own params,
a scan made on the graphics card's path said it was "processed without a graphics card". Through the real server,
on whichever path this run has (the default: the graphics card when there is one; ``OHS_COMPUTE=cpu``: the CPU
path): the fuse stage's ``report.json`` carries the track's path (``health_path``) and its status says what the
track's says about the path, and the result's report (which the viewer and the HTML report read) too.
"""
from __future__ import annotations

import shutil
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("open3d")
requests = pytest.importorskip("requests")

from ohs.app.config import Config  # noqa: E402
from ohs.io.recording import RecordingReader  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402
from recordings import scan_recording  # noqa: E402

T = 30


def wait_done(url: str, jid: str, timeout: float = 300.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        j = requests.get(f"{url}/api/jobs/{jid}", timeout=T).json()
        if j["state"] not in ("queued", "running"):
            assert j["state"] == "done", j
            return j
        time.sleep(0.1)
    raise AssertionError("the job did not finish")


def test_a_stage_at_given_poses_keeps_the_tracks_path(tmp_path):
    rec = scan_recording(tmp_path / "rec", n_frames=10, image_size=(640, 400), seed=3, depth_every=0)
    cfg = Config()
    cfg.paths.calibration = str(tmp_path / "no-calibration.json")
    app = create_app(cfg, projects_dir=tmp_path / "projects", demo_dir=tmp_path / "demo", static_dir=tmp_path / "static",
                     data_dir=tmp_path / "data", config_path=tmp_path / "config.toml")
    with ServerThread(app) as srv:
        st = app.state.studio
        pid = requests.post(srv.url + "/api/projects", json={"name": "Path"}, timeout=T).json()["id"]
        scan, rec_dir = st.projects.add_scan(pid, "Sphere", "markerless")
        shutil.copytree(rec, rec_dir)
        st.projects.update_scan(pid, scan["id"], status="recorded", n_frames=len(RecordingReader(rec_dir)))
        b = f"{srv.url}/api/projects/{pid}/scans/{scan['id']}"
        wait_done(srv.url, requests.post(b + "/process/track", json={"quality": "standard"}, timeout=T).json()["id"])
        wait_done(srv.url, requests.post(b + "/process/fuse", json={"voxel_mm": 3.0}, timeout=T).json()["id"])
        sd = st.projects.scan_dir(pid, scan["id"])
        import json

        track = json.loads((sd / "process" / "track" / "report.json").read_text(encoding="utf-8"))
        fuse = json.loads((sd / "process" / "fuse" / "report.json").read_text(encoding="utf-8"))
        result = requests.get(b + "/files/report.json", timeout=T).json()
    model = track["params"]["compute"]["tracking_model"]
    assert fuse["params"]["compute"]["tracking_model"] is None  # at given poses: nothing tracked
    for rep in (fuse, result):
        note = rep["health_status"]["note"] or ""
        assert ("graphics card" in note) == ("graphics card" in (track["health_status"]["note"] or "")), (model, note)
        if model == "gpu":
            assert "without a graphics card" not in note
    assert fuse["health_path"]["tracking_model"] == model and fuse["health_path"]["from"] == "track"
