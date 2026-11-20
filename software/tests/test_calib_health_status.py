"""Calibration health, part 3 (FEATURE_PLAN H3): the status, as a plain function of plain data.

``ohs.calib.health.status`` joins the launch check, the last 2-minute board check and the last
scans' health blocks into Valid / Check recommended / Recalibration required, with the part, one
sentence and the wizard step to do next; ``scan_status`` says the same of one scan from its own
block. Everything here is hand-made blocks with the numbers H2 measured (``VALIDATION.md``, "Which
signs show a stale calibration"), so no scan is processed: the rules one by one.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ohs.calib import health  # noqa: E402

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
LINES = ("horizontal", "vertical")

# the true calibration on H2's seven scenes: (row error px, vertical - mesh mm, horizontal - mesh mm, gyro deg)
TRUE_SCENES = {
    "bracket-s0": (0.062, 0.004, -0.010, 0.0047), "bracket-s1": (0.063, 0.007, -0.003, 0.0059),
    "bracket-dark": (0.066, 0.003, 0.007, 0.0050), "bracket-speckle": (0.126, 0.015, 0.016, 0.0099),
    "plate-0.5mm": (0.052, 0.002, -0.002, 0.0025), "sphere": (0.047, -0.000, -0.002, 0.0023),
    "panel": (0.046, 0.005, -0.003, 0.0074),
}
# H2's runs (bracket s0 / s1 / speckled, GPU path): fault -> (signs, the part it is)
FAULTS = {
    "camera_pitch 0.02 s0": ((0.2525, 0.0229, -0.1272, 0.0045), "cameras"),
    "camera_pitch 0.05 s0": ((0.6316, 0.0538, -0.1953, 0.0049), "cameras"),
    "camera_pitch 0.05 s1 (horizontal line lost)": ((0.6484, 0.0421, None, 0.0077), "cameras"),
    "camera_pitch 0.1 s0 (tracking lost)": ((1.3003, -0.2775, None, None), "cameras"),
    "camera_yaw 0.01 s0": ((0.0635, -0.1101, -0.1491, 0.0049), "cameras"),
    "camera_yaw 0.02 speckled": ((0.1305, -0.2676, -0.3504, 0.0096), "cameras"),
    "baseline 0.1 % s0": ((0.0618, 0.184, 0.1664, 0.0049), "cameras"),
    "baseline 0.1 % s1": ((0.0634, 0.2073, 0.1947, 0.0059), "cameras"),
    "laser_tilt vertical 0.02 s0": ((0.0618, 0.12, -0.0095, 0.0047), "lasers"),
    "laser_tilt horizontal 0.02 s1": ((0.0634, 0.0067, 0.2699, 0.0059), "lasers"),
    "laser_offset vertical 0.1 s0": ((0.0618, -0.1592, -0.0095, 0.0047), "lasers"),
    "laser_offset horizontal 0.05 s0": ((0.0618, 0.0035, -0.1311, 0.0047), "lasers"),
    "laser_offset horizontal 0.1 speckled": ((0.1256, 0.0154, -0.2832, 0.0099), "lasers"),
    "imu_rotation 2 s0": ((0.0618, 0.0044, -0.0107, 0.0261), "imu"),
    "imu_rotation 5 s1": ((0.0634, 0.0055, -0.0045, 0.0698), "imu"),
    "imu_time 20 s0": ((0.0618, 0.0014, -0.0091, 0.021), "imu"),
}


def block(row=0.062, v=0.004, h=-0.010, gyro=0.0047, cid="cal-1") -> dict:
    """A health block as ``ohs.calib.scan_health`` writes it, with only what the status reads filled
    in (a laser line given as None has no entry: it lost all its points)."""
    per_line = {}
    for name, val in (("vertical", v), ("horizontal", h)):
        if val is not None:
            per_line[name] = {"points": 900, "used": 800, "stray": 3, "median_mm": val, "mean_mm": val, "abs_p95_mm": 0.3}
    return {"version": 1, "calibration": {"id": cid, "created": None, "temperature_c": None, "steps": None,
                                          "source": "recording"},
            "row_error": {"every": 30, "frames": 4, "matches": 1500, "median_px": row, "p90_px": None,
                          "signed_px": None, "per_frame": []},
            "laser_mesh": {"all": {}, "per_line": per_line},
            "tracking": {"frames": 120, "rmse_mm": {"n": 119, "median": 0.36},
                         "gyro_jump_deg": {"n": 0} if gyro is None else {"n": 119, "median": gyro, "p95": gyro * 2}},
            "straighten": None, "ms": 1.0}


def item(signs=(0.062, 0.004, -0.010, 0.0047), days_ago=1.0, model="gpu", lines=LINES) -> dict:
    row, v, h, g = signs
    return {"health": block(row, v, h, g), "tracking_model": model, "lines": list(lines),
            "when": (NOW - timedelta(days=days_ago)).isoformat(), "scan": None}


def run(*scans, **kw) -> dict:
    kw.setdefault("saved", (NOW - timedelta(days=2)).isoformat())
    return health.status(scans=list(scans), now=NOW, **kw)


# --------------------------------------------------------------------------- one scan, read
@pytest.mark.parametrize("scene", sorted(TRUE_SCENES))
def test_the_true_calibration_reads_valid_on_every_scene(scene):
    signs = TRUE_SCENES[scene]
    r = health.read_scan(block(*signs), tracking_model="gpu", lines=LINES)
    assert {p: f["level"] for p, f in r["parts"].items()} == {"cameras": 0, "lasers": 0, "imu": 0}
    s = health.scan_status(block(*signs), tracking_model="gpu", lines=LINES)
    assert s["state"] == "valid" and s["part"] is None and s["action"] is None and s["reasons"] == []
    assert s["parts"] == {"cameras": "valid", "lasers": "valid", "imu": "valid"} and s["note"] is None
    st = run(item(signs, 3), item(signs, 2), item(signs, 1))
    assert st["state"] == "valid" and st["reason"].startswith("Nothing looks off: the last 3 scans"), st["reason"]


@pytest.mark.parametrize("name", sorted(FAULTS))
def test_each_fault_names_its_part(name):
    signs, part = FAULTS[name]
    s = health.scan_status(block(*signs), tracking_model="gpu", lines=LINES)
    assert s["state"] in ("check", "recalibrate") and s["part"] == part, (name, s["reason"])
    assert s["action"]["step"] in {"check", "stereo", "lasers", "imu"}
    # over three scans: two of them seeing it move the status, and name the same part
    st = run(item(signs, 3), item(signs, 2), item(TRUE_SCENES["bracket-s0"], 1))
    assert st["state"] == s["state"] and st["part"] == part, (name, st["reason"])
    assert st["reason"].startswith("2 of the last 3 scans"), st["reason"]


def test_the_row_error_is_read_first():
    """A pitch moves the horizontal line as well (-0.13 mm at 0.02 deg): it is the cameras."""
    r = health.read_scan(block(0.2525, 0.0229, -0.1272, 0.0045), tracking_model="gpu", lines=LINES)
    assert r["parts"]["cameras"]["kind"] == "rows" and r["parts"]["cameras"]["level"] == 1
    assert r["parts"]["lasers"] is None  # explained by the cameras: not judged in this scan
    s = health.scan_status(block(0.2525, 0.0229, -0.1272, 0.0045), tracking_model="gpu", lines=LINES)
    assert s["state"] == "check" and s["part"] == "cameras" and s["action"]["step"] == "check"
    assert s["then"]["step"] == "stereo" and "0.25 px" in s["reason"]
    # past 0.3 px: recalibrate the cameras
    s = health.scan_status(block(0.6316, 0.0538, -0.1953, 0.0049), tracking_model="gpu", lines=LINES)
    assert (s["state"], s["part"], s["action"]["step"]) == ("recalibrate", "cameras", "stereo")


def test_both_lines_alike_point_at_the_cameras_not_the_lasers():
    for signs in (FAULTS["camera_yaw 0.01 s0"][0], FAULTS["baseline 0.1 % s0"][0]):
        r = health.read_scan(block(*signs), tracking_model="gpu", lines=LINES)
        assert r["parts"]["cameras"]["kind"] == "both_lines" and r["parts"]["cameras"]["level"] == 2
        assert r["parts"]["lasers"] is None
        s = health.scan_status(block(*signs), tracking_model="gpu", lines=LINES)
        assert "not at the lasers" in s["reason"] and s["action"]["step"] == "stereo"
    # both lines moved, but opposite ways: not the yaw or the baseline; the lasers, the cameras next
    r = health.read_scan(block(0.062, 0.2, -0.2), tracking_model="gpu", lines=LINES)
    assert r["parts"]["lasers"]["kind"] == "lines" and r["parts"]["cameras"]["kind"] == "rows"
    # the same way but one far more than the other (over BOTH_LINES_RATIO): the lasers
    r = health.read_scan(block(0.062, 0.06, 0.5), tracking_model="gpu", lines=LINES)
    assert r["parts"]["lasers"]["kind"] == "lines" and r["parts"]["lasers"]["level"] == 2


def test_one_line_moved_is_its_laser_and_the_words_do_not_promise_a_mend():
    """A camera roll reads exactly as a vertical-laser fault (H2): the words say to redo the lasers
    first and the cameras next, never that the lasers will mend it."""
    roll = (0.1182, 0.1784, -0.017, 0.0047)  # camera roll 0.05 deg, bracket s0
    for signs, line in ((roll, "vertical"), (FAULTS["laser_tilt vertical 0.02 s0"][0], "vertical"),
                        (FAULTS["laser_offset horizontal 0.05 s0"][0], "horizontal")):
        s = health.scan_status(block(*signs), tracking_model="gpu", lines=LINES)
        assert (s["state"], s["part"]) == ("recalibrate", "lasers"), s["reason"]
        assert f"the {line} laser line" in s["reason"] and "while the cameras look in line" in s["reason"]
        assert s["action"]["step"] == "lasers" and s["then"]["step"] == "stereo"
        assert s["then"]["text"].startswith("If later scans still show it, redo the camera calibration")
        for word in ("will fix", "will mend", "fixes", "mends"):
            assert word not in (s["reason"] + s["action"]["text"] + s["then"]["text"]).lower()
    s = health.scan_status(block(*roll), tracking_model="gpu", lines=LINES)
    assert "a small turn of one camera looks just the same" in s["then"]["text"]


def test_a_line_without_points_is_a_sign():
    # the rows in line, the horizontal line gone: its laser (check), what to try first and next
    r = health.read_scan(block(0.062, 0.004, None), tracking_model="gpu", lines=LINES)
    assert r["parts"]["lasers"]["kind"] == "line_missing" and r["parts"]["lasers"]["level"] == 1
    assert r["signs"]["laser:horizontal"]["missing"] is True
    s = health.scan_status(block(0.062, 0.004, None), tracking_model="gpu", lines=LINES)
    assert (s["state"], s["part"], s["action"]["step"]) == ("check", "lasers", "lasers")
    assert "no usable points from the horizontal laser line" in s["reason"] and s["then"]["step"] == "stereo"
    # the rows far off as well: the cameras (a pitch of 0.03-0.05 deg loses that line)
    s = health.scan_status(block(0.6484, 0.0421, None, 0.0077), tracking_model="gpu", lines=LINES)
    assert (s["state"], s["part"]) == ("recalibrate", "cameras")
    # no line at all: the scan had no laser data, which says nothing about the lasers
    r = health.read_scan(block(0.062, None, None), tracking_model="gpu", lines=LINES)
    assert r["parts"]["lasers"] is None
    # a line the scan did not have (lasers off: no lines expected) is no sign either
    r = health.read_scan(block(0.062, 0.004, None), tracking_model="gpu", lines=[])
    assert r["parts"]["lasers"] is None
    # by default the lines expected are those in the block
    r = health.read_scan(block(0.062, 0.004, None), tracking_model="gpu")
    assert r["parts"]["lasers"]["level"] == 0


def test_the_cpu_path_judges_the_cameras_only():
    """On the CPU path without its 2026-11-20 settings (here: nothing says it had them, as in a scan from
    before) the lasers and the IMU are not judged: neither stale nor fine. With them they are
    (``test_calib_health_cpu_path.py``)."""
    true_cpu = (0.0618, -0.0189, 0.0801, 0.0362)  # H2, CPU path, true calibration: gyro over "recalibrate"
    for signs in (true_cpu, (0.0618, -0.1764, 0.0827, 0.0354)):  # ... and the vertical laser 0.1 mm off
        r = health.read_scan(block(*signs), tracking_model="open3d", lines=LINES)
        assert r["parts"]["lasers"] is None and r["parts"]["imu"] is None and r["parts"]["cameras"]["level"] == 0
        assert r["signs"]["gyro_jump"]["state"] is None and r["signs"]["laser:horizontal"]["state"] is None
        s = health.scan_status(block(*signs), tracking_model="open3d", lines=LINES)
        assert s["state"] == "valid" and s["parts"] == {"cameras": "valid", "lasers": None, "imu": None}
        assert s["not_judged"] == ["lasers", "imu"]
        assert "by an earlier version of the software" in s["note"] and "lasers and the IMU" in s["note"]
        assert "cameras" in s["reason"] and "lasers" not in s["reason"]
    st = run(*(item(true_cpu, d, model="open3d") for d in (3, 2, 1)))
    assert st["state"] == "valid" and st["parts"]["lasers"] is None and st["parts"]["imu"] is None
    assert "by an earlier version of the software" in st["note"]
    assert st["reason"].startswith("Nothing looks off for the cameras:"), st["reason"]
    # the row error still holds there: a pitch is caught
    st = run(*(item((0.2525, 0.0686, 0.2232, 0.0374), d, model="open3d") for d in (3, 2, 1)))
    assert (st["state"], st["part"]) == ("check", "cameras")
    # no compute path known (an old report): the row error only, too
    assert health.read_scan(block(*true_cpu), tracking_model=None, lines=LINES)["parts"]["imu"] is None


def test_mixed_paths_judge_the_lasers_from_the_gpu_scans_only():
    bad_v = FAULTS["laser_offset vertical 0.1 s0"][0]
    st = run(item(bad_v, 3), item(bad_v, 2, model="open3d"), item(bad_v, 1))
    assert (st["state"], st["part"]) == ("recalibrate", "lasers") and st["note"] is None
    assert st["reasons"][0]["reason"].startswith("The last 2 scans"), st["reasons"][0]
    st = run(item(bad_v, 3, model="open3d"), item(bad_v, 2, model="open3d"), item(bad_v, 1))
    assert st["state"] == "valid" and st["parts"]["lasers"] is None  # one GPU scan: not enough to judge


def test_a_short_scan_judges_the_cameras_only():
    """Measured: 12-frame pieces of the speckled bracket read the horizontal line 0.04-0.11 mm off the
    mesh with the true calibration (90 frames: 0.016); the laser and IMU limits were measured on
    90-120-frame scans. So under MIN_FRAMES processed frames only the row error is judged."""
    assert health.MIN_FRAMES == 90
    speckle_12 = (0.1263, 0.0117, 0.1065, 0.0122)  # bracket-speckle frames 0-12, true calibration
    r = health.read_scan(block(*speckle_12), tracking_model="gpu", lines=LINES, frames=12)
    assert r["parts"]["lasers"] is None and r["parts"]["imu"] is None and r["parts"]["cameras"]["level"] == 0
    s = health.scan_status(block(*speckle_12), tracking_model="gpu", lines=LINES, frames=12)
    assert s["state"] == "valid" and s["not_judged"] == ["lasers", "imu"]
    assert "12 frames" in s["note"] and "at least 90" in s["note"]
    # long enough: judged
    s = health.scan_status(block(*speckle_12), tracking_model="gpu", lines=LINES, frames=90)
    assert (s["state"], s["part"]) == ("recalibrate", "lasers")
    # the cameras are still judged on a short scan
    s = health.scan_status(block(0.6316, 0.05, -0.2, 0.005), tracking_model="gpu", lines=LINES, frames=12)
    assert (s["state"], s["part"]) == ("recalibrate", "cameras")
    # in the joined status: short scans do not count for the lasers
    it = item(speckle_12)
    st = run(*(dict(it, frames=12, when=(NOW - timedelta(days=d)).isoformat()) for d in (3, 2, 1)))
    assert st["state"] == "valid" and st["parts"]["lasers"] is None and "at least 90" in st["note"]


# --------------------------------------------------------------------------- the last scans must agree
def test_one_scan_alone_does_not_move_the_status():
    bad = FAULTS["laser_offset vertical 0.1 s0"][0]
    good = TRUE_SCENES["bracket-s0"]
    assert run(item(bad, 1))["state"] == "valid"  # one scan so far
    assert run(item(good, 3), item(good, 2), item(bad, 1))["state"] == "valid"  # the newest is an outlier
    assert run(item(bad, 3), item(good, 2), item(good, 1))["state"] == "valid"
    st = run(item(bad, 3), item(good, 2), item(bad, 1))
    assert (st["state"], st["part"]) == ("recalibrate", "lasers")
    # only the last SCANS_WINDOW count: two bad scans before three good ones are forgotten
    assert run(item(bad, 6), item(bad, 5), item(good, 3), item(good, 2), item(good, 1))["state"] == "valid"
    assert run(item(bad, 1))["scans"] == {"used": 1, "window": 3, "agree": 2}
    # but the scan's own report says what it saw
    assert health.scan_status(block(*bad), tracking_model="gpu", lines=LINES)["state"] == "recalibrate"


def test_the_level_is_the_one_two_scans_reach():
    rec = FAULTS["camera_pitch 0.05 s0"][0]  # recalibrate
    chk = FAULTS["camera_pitch 0.02 s0"][0]  # check
    good = TRUE_SCENES["bracket-s0"]
    assert run(item(rec, 3), item(chk, 2), item(good, 1))["state"] == "check"
    assert run(item(rec, 3), item(rec, 2), item(chk, 1))["state"] == "recalibrate"
    st = run(item(chk, 3), item(rec, 2), item(good, 1))
    assert st["state"] == "check" and "0.63 px" in st["reason"]  # the words of the newest scan that agrees


def test_scans_are_ordered_by_time_not_by_list_order():
    bad = FAULTS["imu_rotation 5 s1"][0]
    good = TRUE_SCENES["bracket-s1"]
    st = run(item(good, 1), item(bad, 4), item(good, 3), item(bad, 2), item(bad, 5))
    assert st["state"] == "valid"  # newest three: good (1), bad (2), good (3)


# --------------------------------------------------------------------------- the launch check and the board check
def test_a_launch_drift_after_the_last_save():
    lc = {"state": "drift", "dv_px": 0.82, "matches": 300, "message": "x", "when": (NOW - timedelta(hours=1)).isoformat()}
    st = run(launch_check=lc)
    assert (st["state"], st["part"]) == ("recalibrate", "cameras")
    assert "0.82 px" in st["reason"] and "0.6 px" in st["reason"]
    assert st["action"]["step"] == "check" and st["then"]["step"] == "stereo"
    # a drift from before the last save judged another calibration
    old = dict(lc, when=(NOW - timedelta(days=3)).isoformat())
    assert run(launch_check=old)["state"] == "valid"
    # the 2-minute check it asked for passed after it
    passed = {"when": (NOW - timedelta(minutes=10)).isoformat(), "ok": True, "verdict": "The check passed."}
    assert run(launch_check=lc, last_check=passed)["state"] == "valid"
    # ok counts as evidence; unknown and running say nothing
    ok = dict(lc, state="ok", dv_px=0.09)
    st = run(launch_check=ok)
    assert st["state"] == "valid" and "the launch check (today)" in st["reason"]
    assert st["parts"]["cameras"] == "valid" and st["parts"]["lasers"] is None
    for state in ("unknown", "running"):
        st = run(launch_check=dict(lc, state=state, dv_px=None))
        assert st["state"] == "valid" and st["parts"]["cameras"] is None


def test_the_launch_value_from_0_3_px_recommends_a_check():
    """Measured: on 3 idle frames at half resolution the launch check reads up to 0.148 px with a
    right calibration (one triplet, speckled bracket), so the scans' 0.15 px cannot judge it; from
    0.3 px (a pitch of about 0.02 deg, 0.26-0.31 px on every scene) it recommends the 2-minute check.
    Its own 0.6 px alert is unchanged: "drift" stays "Recalibration required"."""
    assert health.LAUNCH_CHECK_PX == health.LIMITS["row_error"].recalibrate == 0.3
    lc = {"state": "ok", "dv_px": 0.148, "matches": 300, "message": "x", "when": (NOW - timedelta(hours=1)).isoformat()}
    assert run(launch_check=lc)["state"] == "valid"
    st = run(launch_check=dict(lc, dv_px=0.41))
    assert (st["state"], st["part"], st["action"]["step"], st["then"]["step"]) == ("check", "cameras", "check", "stereo")
    assert "0.41 px" in st["reason"] and "under 0.3 px" in st["reason"]
    # a 2-minute check after it that found the rows in order answers it
    passed = {"when": (NOW - timedelta(minutes=10)).isoformat(), "ok": True, "verdict": "ok"}
    assert run(launch_check=dict(lc, dv_px=0.41), last_check=passed)["state"] == "valid"


def test_a_failed_board_check_names_its_part():
    when = (NOW - timedelta(hours=2)).isoformat()
    st = run(last_check={"when": when, "ok": False, "verdict": "The vertical laser is 0.8 mm off its plane.",
                         "parts": ["lasers"]})
    assert (st["state"], st["part"], st["action"]["step"]) == ("recalibrate", "lasers", "lasers")
    assert st["reason"] == ("The last 2-minute check did not pass (today): The vertical laser is 0.8 mm off its "
                            "plane.")
    st = run(last_check={"when": when, "ok": False, "verdict": "x", "parts": ["imu", "cameras"]})
    assert (st["part"], st["action"]["step"]) == ("cameras", "stereo")
    # a check that failed on the lasers only found the rows in order: it answers a launch drift before it
    lc = {"state": "drift", "dv_px": 0.82, "when": (NOW - timedelta(hours=3)).isoformat()}
    st = run(launch_check=lc, last_check={"when": when, "ok": False, "verdict": "x", "parts": ["lasers"]})
    assert [r["source"] for r in st["reasons"]] == ["board_check"] and st["part"] == "lasers"
    st = run(launch_check=lc, last_check={"when": when, "ok": False, "verdict": "x", "parts": ["cameras"]})
    assert [r["source"] for r in st["reasons"]] == ["board_check", "launch_check"]
    # a check from before the parts were kept: the part is not known
    st = run(last_check={"when": when, "ok": False, "verdict": "the rows disagree"})
    assert st["state"] == "recalibrate" and st["part"] is None and st["action"]["step"] == "check"
    # a failed check from before the last save does not count
    old = (NOW - timedelta(days=3)).isoformat()
    assert run(last_check={"when": old, "ok": False, "verdict": "x", "parts": ["lasers"]})["state"] == "valid"


def test_the_age_of_the_last_board_check():
    days = health.BOARD_CHECK_MAX_AGE_DAYS
    assert days == 14
    saved = (NOW - timedelta(days=60)).isoformat()
    st = run(saved=saved, last_check={"when": (NOW - timedelta(days=days + 1)).isoformat(), "ok": True, "verdict": "ok"})
    assert (st["state"], st["part"], st["action"]["step"]) == ("check", None, "check")
    assert st["reason"].startswith(f"The last 2-minute check was {days + 1} days ago")
    st = run(saved=saved, last_check={"when": (NOW - timedelta(days=days - 1)).isoformat(), "ok": True, "verdict": "ok"})
    assert st["state"] == "valid" and "the 2-minute check" in st["reason"]
    # never checked: from the last calibration step
    st = run(saved=(NOW - timedelta(days=days + 5)).isoformat())
    assert st["state"] == "check" and "since it was saved" in st["reason"]
    assert run(saved=(NOW - timedelta(days=days - 5)).isoformat())["state"] == "valid"
    # a fresh calibration step counts as fresh, whatever the last check
    st = run(saved=(NOW - timedelta(days=1)).isoformat(),
             last_check={"when": (NOW - timedelta(days=90)).isoformat(), "ok": True, "verdict": "ok"})
    assert st["state"] == "valid"
    # nothing known at all: no age to judge
    assert health.status(now=NOW)["state"] == "valid"


def test_a_passed_board_check_answers_the_row_error_check_before_it():
    chk = FAULTS["camera_pitch 0.02 s0"][0]
    passed = {"when": (NOW - timedelta(days=1, hours=12)).isoformat(), "ok": True, "verdict": "ok"}
    # two scans at "check" before the check passed: answered
    st = run(item(chk, 3), item(chk, 2), last_check=passed)
    assert st["state"] == "valid", st["reason"]
    # one before, one after: one is not enough
    assert run(item(chk, 3), item(chk, 1), last_check=passed)["state"] == "valid"
    # two after it: they count again
    assert run(item(chk, 1.2), item(chk, 1), last_check=passed)["state"] == "check"
    # a "recalibrate" is not answered by a board check, and neither is a laser sign it cannot see
    rec = FAULTS["camera_pitch 0.05 s0"][0]
    assert run(item(rec, 3), item(rec, 2), last_check=passed)["state"] == "recalibrate"
    lz = FAULTS["laser_offset horizontal 0.05 s0"][0]
    assert run(item(lz, 3), item(lz, 2), last_check=passed)["part"] == "lasers"


# --------------------------------------------------------------------------- joining
def test_the_worst_wins_and_every_reason_is_listed():
    bad_l = FAULTS["laser_offset vertical 0.1 s0"][0]
    bad_imu = FAULTS["imu_rotation 2 s0"][0]
    both = (0.0618, -0.1592, -0.0095, 0.0261)
    st = run(item(both, 2), item(both, 1), saved=(NOW - timedelta(days=30)).isoformat())
    assert (st["state"], st["part"]) == ("recalibrate", "lasers")
    assert [(r["state"], r["part"], r["source"]) for r in st["reasons"]] == [
        ("recalibrate", "lasers", "scans"), ("check", "imu", "scans"), ("check", None, "board_check_age")]
    assert st["parts"] == {"cameras": "valid", "lasers": "recalibrate", "imu": "check"}
    # on a tie the cameras come first
    lc = {"state": "drift", "dv_px": 0.9, "matches": 99, "message": "x", "when": NOW.isoformat()}
    st = run(item(bad_l, 2), item(bad_l, 1), launch_check=lc)
    assert st["part"] == "cameras" and [r["part"] for r in st["reasons"]] == ["cameras", "lasers"]
    assert run(item(bad_imu, 2), item(bad_imu, 1))["part"] == "imu"


def test_the_shape_a_page_can_show_without_logic():
    from ohs.server.setup import STEP_IDS

    keys = {"version", "state", "label", "part", "part_label", "reason", "action", "then", "reasons", "parts",
            "not_judged", "note", "provisional", "source", "scans"}
    chk = FAULTS["camera_pitch 0.02 s0"][0]
    for st in (run(), run(item(chk, 2), item(chk, 1)), run(launch_check={"state": "drift", "dv_px": 1.0,
                                                                       "when": NOW.isoformat()})):
        assert set(st) == keys
        assert st["label"] == health.STATE_LABELS[st["state"]] and st["state"] in health.STATES
        assert st["reason"].endswith(".") and "\n" not in st["reason"]
        for a in (st["action"], st["then"]):
            assert a is None or (set(a) == {"step", "text"} and a["step"] in STEP_IDS and a["text"])
        assert st["provisional"] is True
        json.dumps(st)  # plain data
    assert set(health.STEPS) <= set(STEP_IDS)
    s = health.scan_status(block(*chk), tracking_model="gpu", lines=LINES)
    assert set(s) == (keys - {"scans"}) | {"signs"} and s["source"] == "scan"
    assert s["signs"]["row_error"] == {"value": 0.2525, "unit": "px", "check": 0.15, "recalibrate": 0.3,
                                       "state": "check"}
    assert set(s["signs"]) == {"row_error", "laser:vertical", "laser:horizontal", "gyro_jump"}
    # a report's own words, examples: Valid, Check recommended, Recalibration required
    assert health.scan_status(block(), tracking_model="gpu", lines=LINES)["reason"] == (
        "This scan shows nothing off for the cameras, the lasers and the IMU.")
    assert s["reason"] == ("This scan shows the two cameras slightly out of line: matching points sit 0.25 px apart "
                           "in height (want under 0.15 px).")


def test_no_block_or_a_failed_one():
    s = health.scan_status(None, tracking_model="gpu", lines=LINES)
    assert s["state"] == "valid" and s["parts"] == {"cameras": None, "lasers": None, "imu": None}
    assert s["reason"] == "This scan has too little to judge the calibration by."
    assert health.scan_item({"health": {"version": 1, "error": "boom"}}) is None
    assert health.trim_block(None) is None


# --------------------------------------------------------------------------- what is kept of a scan
def test_scan_item_keeps_what_the_status_reads():
    rep = {"health": block(0.2525, 0.0229, -0.1272, 0.0045), "lasers": {"planes": ["horizontal", "vertical"]},
           "params": {"pipeline": {"use_lasers": True}, "compute": {"tracking_model": "gpu"}}}
    it = health.scan_item(rep, when=NOW.isoformat(), scan={"project_id": "p", "scan_id": "s"}, calibration="cal-1")
    assert it["tracking_model"] == "gpu" and it["lines"] == ["horizontal", "vertical"] and it["calibration"] == "cal-1"
    assert len(json.dumps(it)) < 600
    assert (health.scan_status(it["health"], tracking_model="gpu", lines=it["lines"])["reasons"]
            == health.scan_status(rep["health"], tracking_model="gpu", lines=it["lines"])["reasons"])
    rep["params"]["pipeline"]["use_lasers"] = False
    assert health.lines_of_report(rep) == []


def test_calibration_key():
    import dataclasses

    from ohs.sim.nominal import nominal_calibration
    from ohs.types import ScannerCalibration

    c = nominal_calibration((640, 400), realistic=True)
    assert health.calibration_key(None) is None
    assert health.calibration_key(dataclasses.replace(c, id="abc123")) == "abc123"
    k = health.calibration_key(c)
    assert k.startswith("content-")
    # a copy read back (a recording's copy) is the same calibration
    again = ScannerCalibration.from_dict(json.loads(json.dumps(c.to_dict())))
    assert health.calibration_key(again) == k
    from tools import calib_health_study as study  # noqa: PLC0415

    assert health.calibration_key(study.perturb(c, "laser_offset:vertical", 0.05)) != k
