"""The calibration status on the CPU path (MAINTENANCE_PLAN F14; ``ohs.calib.health``, "The GPU path, and the CPU
path with its 2026-11-20 settings").

A scan processed without a GPU has its laser and IMU signs judged when its own report shows the CPU path's
settings of 2026-11-20 at full resolution (the depth fix ``params.depth.flip_average``, the ray cast's weight
threshold 8, the velocity guard), and only its cameras otherwise: an older scan, a half-resolution one, or
one with a setting turned off. The numbers are those the CPU path measured on the simulated bracket with
those settings (true calibration, H2's 0.2 mm laser fault and 3 deg IMU fault), and H2's CPU-path numbers
of before F13/F14 for a scan without them. The limits are the GPU path's, unchanged.
"""
from __future__ import annotations

import copy
from datetime import timedelta

import pytest

from ohs.calib import health
from test_calib_health_status import LINES, NOW, block, run

# CPU path with the 2026-11-20 settings, the 90-frame bracket: (row px, vertical mm, horizontal mm, gyro deg)
TRUE_CPU = (0.0642, 0.0128, -0.0063, 0.0055)
LASER_FAULT_CPU = (0.0642, -0.3565, -0.0063, 0.0055)  # vertical laser offset 0.2 mm
IMU_FAULT_CPU = (0.0642, 0.0198, -0.0062, 0.0335)  # IMU turned 3 deg
# H2's CPU path before F13/F14 (no such settings), true calibration: the gyro over "recalibrate"
OLD_CPU = (0.0618, -0.0189, 0.0801, 0.0362)


def report(signs, *, width=1280, flip=True, weight=8.0, guard=2.0, model="open3d", keys=True) -> dict:
    """A processing report with what the status reads (``keys=False``: one from before the settings)."""
    depth = {"block_size": 7, "flip_average": flip} if keys else {"block_size": 7}
    fusion = {"voxel_mm": 1.0, "raycast_weight_threshold": weight, "velocity_max_accel": guard}
    if not keys:
        fusion = {"voxel_mm": 1.0, "raycast_weight_threshold": 3.0}
    return {"health": block(*signs), "image_size": [width, width * 5 // 8], "lasers": {"planes": list(LINES)},
            "frames": {"processed": 90},
            "params": {"pipeline": {"use_lasers": True}, "depth": depth, "fusion": fusion,
                       "compute": {"tracking_model": model}}}


def status_of(rep) -> dict:
    return health.scan_status(rep["health"], tracking_model=rep["params"]["compute"]["tracking_model"],
                              lines=health.lines_of_report(rep), frames=rep["frames"]["processed"],
                              cpu_path=health.cpu_path_case(rep))


def test_what_a_report_says_of_its_path():
    assert health.cpu_path_case(report(TRUE_CPU, model="gpu")) is None
    assert health.cpu_path_case(report(TRUE_CPU)) == "full"
    assert health.cpu_path_case(report(TRUE_CPU, keys=False)) == "older"
    assert health.cpu_path_case(report(TRUE_CPU, width=640, flip=False, weight=3.0)) == "half"
    assert health.cpu_path_case(report(TRUE_CPU, width=640)) == "half"  # even with the settings set by hand
    for off in ({"flip": False}, {"weight": 3.0}, {"guard": 0.0}, {"guard": None}):
        assert health.cpu_path_case(report(TRUE_CPU, **off)) == "off", off
    assert health.cpu_path_case({}) == "older"


def test_with_the_settings_a_right_calibration_reads_valid_for_all_three_parts():
    s = status_of(report(TRUE_CPU))
    assert s["state"] == "valid" and s["parts"] == {"cameras": "valid", "lasers": "valid", "imu": "valid"}, s
    assert s["note"] is None and s["not_judged"] == []


@pytest.mark.parametrize("signs, part", [(LASER_FAULT_CPU, "lasers"), (IMU_FAULT_CPU, "imu")])
def test_with_the_settings_the_faults_name_their_part(signs, part):
    s = status_of(report(signs))
    assert (s["state"], s["part"]) == ("recalibrate", part), (s["reason"], s["signs"])


@pytest.mark.parametrize("case, kw, words", [
    ("older", {"keys": False}, "by an earlier version of the software"),
    ("half", {"width": 640, "flip": False, "weight": 3.0}, "at half resolution"),
    ("off", {"guard": 0.0}, "with some of the settings for computers without a graphics card turned off"),
])
def test_without_them_only_the_cameras_are_judged_and_the_note_says_why(case, kw, words):
    for signs in (TRUE_CPU, LASER_FAULT_CPU, OLD_CPU):  # a fault-like reading there moves nothing either
        rep = report(signs, **kw)
        assert health.cpu_path_case(rep) == case
        s = status_of(rep)
        assert s["state"] == "valid" and s["parts"] == {"cameras": "valid", "lasers": None, "imu": None}, (case, s)
        assert s["not_judged"] == ["lasers", "imu"]
        assert s["note"].startswith("This scan was processed without a graphics card ") and words in s["note"], s["note"]
        assert "only the cameras could be judged" in s["note"] and "—" not in s["note"]
        assert "cameras" in s["reason"] and "lasers" not in s["reason"]


def _item(rep, days_ago):
    return health.scan_item(rep, when=(NOW - timedelta(days=days_ago)).isoformat())


def test_the_last_scans_are_read_under_the_rule_they_were_made_with():
    """The setup state keeps the last scans (``scan_item``). Scans with the settings are judged in full; a
    scan kept before ``cpu_path`` existed (no such key) stays read for the cameras only, so recomputing the
    status at the app's launch never turns it to Recalibration required, or to judged, by a rule it did not
    run under."""
    full = [_item(report(LASER_FAULT_CPU), d) for d in (3, 2, 1)]
    assert all(it["cpu_path"] == "full" for it in full)
    st = run(*full)
    assert (st["state"], st["part"]) == ("recalibrate", "lasers") and st["note"] is None
    kept = []
    for it in full:  # the same scans as kept before today: no "cpu_path"
        it = copy.deepcopy(it)
        it.pop("cpu_path")
        kept.append(it)
    st = run(*kept)
    assert st["state"] == "valid" and st["parts"]["lasers"] is None and st["parts"]["imu"] is None
    assert "These scans were processed without a graphics card by an earlier version" in st["note"]
    assert run(*(_item(report(OLD_CPU, keys=False), d) for d in (3, 2, 1)))["parts"]["imu"] is None
    # mixed reasons: said as such
    mixed = run(_item(report(TRUE_CPU, keys=False), 3), _item(report(TRUE_CPU, width=640), 2),
                _item(report(TRUE_CPU, guard=0.0), 1))
    assert mixed["parts"]["lasers"] is None and "in ways that let only the cameras be judged" in mixed["note"]
    # a scan with the settings next to two without: one scan alone does not judge the lasers
    st = run(_item(report(LASER_FAULT_CPU), 1), *kept[:2])
    assert st["state"] == "valid" and st["parts"]["lasers"] is None and st["note"] is None


def test_the_gpu_path_reads_as_before():
    gpu = report(TRUE_CPU, model="gpu", keys=False)
    assert health.cpu_path_case(gpu) is None and _item(gpu, 1)["cpu_path"] is None
    s = status_of(gpu)
    assert s["parts"] == {"cameras": "valid", "lasers": "valid", "imu": "valid"} and s["note"] is None
    assert s == health.scan_status(gpu["health"], tracking_model="gpu", lines=list(LINES), frames=90)
