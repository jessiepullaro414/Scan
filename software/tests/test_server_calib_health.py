"""Calibration health, part 3 (FEATURE_PLAN H3) in the server: the status in the setup state.

``GET /api/setup`` gains ``calibration_health`` (``ohs.calib.health.status``): the launch check, the
last 2-minute check (now with the parts it found off) and the health of the last scans made with
this calibration, which the Track stage keeps in ``setup.json`` (``scan_health``). Nothing here
processes a scan: the scans' reports are hand-made with the numbers H2 measured.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
requests = pytest.importorskip("requests")

from ohs.app.config import Config  # noqa: E402
from ohs.server.app import create_app  # noqa: E402
from ohs.server.protocol import now_iso  # noqa: E402
from ohs.server.testing import ServerThread  # noqa: E402
from test_calib_health_status import FAULTS, TRUE_SCENES, block  # noqa: E402

OLD_KEYS = {"target", "first_run", "dismissed", "steps", "calibration", "selftest", "print_check", "board",
            "launch_check", "last_check"}


def _app(root: Path):
    cfg = Config()
    cfg.paths.calibration = str(root / "device" / "calibration.json")
    cfg.paths.scans_dir = str(root / "scans")
    cfg.paths.printables_dir = str(root / "printables")
    (root / "demo").mkdir(exist_ok=True)
    return create_app(cfg, projects_dir=root / "projects", demo_dir=root / "demo", static_dir=root / "static",
                      data_dir=root / "data", config_path=root / "config.toml")


def _calibrate(root: Path, cid: str | None = "cal-1"):
    from ohs.sim.nominal import nominal_calibration

    c = dataclasses.replace(nominal_calibration((640, 400), realistic=True), id=cid, created=now_iso() if cid else None)
    p = root / "device" / "calibration.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    c.save(p)
    return c


def _report(signs, cid="cal-1", model="gpu") -> dict:
    return {"health": block(*signs, cid=cid), "lasers": {"planes": ["horizontal", "vertical"]},
            "params": {"pipeline": {"use_lasers": True}, "compute": {"tracking_model": model}}}


@pytest.fixture
def env(tmp_path):
    app = _app(tmp_path)
    with ServerThread(app) as srv:
        yield {"url": srv.url, "root": tmp_path, "studio": app.state.studio}


def _setup(url) -> dict:
    r = requests.get(url + "/api/setup", timeout=30)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_setup_state_gains_the_status_and_keeps_its_keys(env):
    url, root, st = env["url"], env["root"], env["studio"]
    s = _setup(url)
    assert set(s) == OLD_KEYS | {"calibration_health"} and s["calibration_health"] is None  # no calibration yet
    _calibrate(root)
    s = _setup(url)
    h = s["calibration_health"]
    assert h["state"] == "valid" and h["label"] == "Valid" and h["part"] is None and h["action"] is None
    assert h["reason"].startswith("Nothing speaks against the calibration yet")
    assert h["scans"] == {"used": 0, "window": 3, "agree": 2}
    # the old keys mean what they meant
    assert s["launch_check"] is None and s["last_check"] is None and s["calibration"]["exists"] is True
    # two scans see the vertical laser off: Recalibration required, the lasers, redo step "lasers"
    bad = FAULTS["laser_offset vertical 0.1 s0"][0]
    st.setup.record_scan_health("p1", "scan-a", _report(bad))
    assert _setup(url)["calibration_health"]["state"] == "valid"  # one scan alone does not move it
    st.setup.record_scan_health("p1", "scan-b", _report(bad))
    h = _setup(url)["calibration_health"]
    assert (h["state"], h["part"], h["action"]["step"], h["then"]["step"]) == ("recalibrate", "lasers", "lasers", "stereo")
    assert h["reason"].startswith("The last 2 scans show the vertical laser line 0.16 mm off the surface")
    # a scan processed again replaces its entry; a scan made with another calibration does not count
    st.setup.record_scan_health("p1", "scan-b", _report(TRUE_SCENES["bracket-s0"]))
    st.setup.record_scan_health("p1", "scan-c", _report(bad, cid="another"))
    assert _setup(url)["calibration_health"]["state"] == "valid"
    data = json.loads((root / "data" / "setup.json").read_text(encoding="utf-8"))
    kept = data["scan_health"]
    assert [e["scan"]["scan_id"] for e in kept] == ["scan-c", "scan-b", "scan-a"]
    assert {e["calibration"] for e in kept} == {"cal-1", "another"} and len(json.dumps(kept)) < 3000
    assert data["targets"]["device"]["launch_source"] is None


def test_the_launch_check_and_the_board_check_in_the_status(env):
    url, root, st = env["url"], env["root"], env["studio"]
    _calibrate(root)
    lc = {"state": "drift", "dv_px": 0.83, "matches": 400, "message": "x", "when": now_iso()}
    st.setup.record_launch_check("camera", lc)
    s = _setup(url)
    assert s["launch_check"] == lc  # as it was
    h = s["calibration_health"]
    assert (h["state"], h["part"], h["action"]["step"]) == ("recalibrate", "cameras", "check")
    # a replay's launch check judged the replay's own calibration
    st.setup.record_launch_check("replay", lc)
    assert _setup(url)["calibration_health"]["state"] == "valid"
    st.setup.record_launch_check("camera", lc)
    # the 2-minute check fails on the lasers: it keeps the parts, and the status names them
    st.setup.record_last_check("device", False, "The vertical laser is 0.8 mm off its plane.", parts=["lasers"])
    s = _setup(url)
    assert s["last_check"]["parts"] == ["lasers"] and s["last_check"]["ok"] is False
    # its rows passed (only the lasers failed): that answers the launch drift it was run to confirm
    h = s["calibration_health"]
    assert [(r["state"], r["part"], r["source"]) for r in h["reasons"]] == [("recalibrate", "lasers", "board_check")]
    assert h["action"]["step"] == "lasers"
    # it passes: the launch drift it was asked to confirm is answered
    st.setup.record_last_check("device", True, "The check passed.", parts=[])
    h = _setup(url)["calibration_health"]
    assert h["state"] == "valid" and "the 2-minute check (today)" in h["reason"]
    # without parts, the key is not written (as before H3)
    st.setup.record_last_check("device", True, "The check passed.")
    assert "parts" not in _setup(url)["last_check"]


def test_a_calibration_without_an_id_matches_its_scans_by_its_numbers(env):
    from ohs.calib import health
    from ohs.io.recording import RecordingWriter

    url, root, st = env["url"], env["root"], env["studio"]
    c = _calibrate(root, cid=None)
    rec = root / "rec"
    with RecordingWriter(rec) as w:
        w.write_calibration(c)
    bad = FAULTS["imu_rotation 5 s1"][0]
    for sid in ("s1", "s2"):
        st.setup.record_scan_health("p", sid, _report(bad, cid=None), recording=rec)
    kept = json.loads((root / "data" / "setup.json").read_text(encoding="utf-8"))["scan_health"]
    assert {e["calibration"] for e in kept} == {health.calibration_key(c)}
    h = _setup(url)["calibration_health"]
    assert (h["state"], h["part"]) == ("recalibrate", "imu")


def test_an_old_setup_file_loads(tmp_path):
    from ohs.server.setup import SetupStore

    p = tmp_path / "setup.json"
    p.write_text(json.dumps({"format_version": 1, "targets": {"device": {"dismissed": True, "launch_check": None}},
                             "printables": None}), encoding="utf-8")
    s = SetupStore(p)
    assert s.scan_health() == [] and s.target("device")["dismissed"] is True
    assert s.target("device")["launch_source"] is None


def test_check_failed_parts():
    from ohs.server.calib_results import check_failed_parts

    rep = {"stereo": {"ok": False}, "lasers": {"lasers": {"vertical": {"ok": False}, "horizontal": {"ok": True}}},
           "imu": {"ok": False}}
    assert check_failed_parts(rep) == ["cameras", "lasers", "imu"]
    assert check_failed_parts(rep, sim=True) == ["cameras", "lasers"]  # the simulator's check ignores the IMU
    assert check_failed_parts({"stereo": {"ok": True}, "lasers": {"verdict": "no planes"}, "imu": {"ok": None}}) == []
    assert check_failed_parts({}) == []
