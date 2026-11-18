"""Calibration health, part 2 (FEATURE_PLAN H2): the slightly wrong calibrations of
``tools/calib_health_study.py``, and the limits of ``ohs/calib/health.py`` they were measured for.

* Each perturbation changes the true calibration by exactly what was asked and nothing else: the
  cameras' relative angle (about the axis that shifts rows, the one that changes depth, the optical
  axis), the baseline, one laser plane's tilt (about its emitter) or offset, the IMU's rotation or
  its time offset. A camera fault keeps the laser planes and the IMU where they are on the left
  camera (they did not move): only the rectified frame they are written in turns with the new
  rectification. A perturbed calibration has no ``id``: it is not the calibration it came from.
* End to end, on one short full-resolution recording: one fault of each part (cameras, lasers,
  IMU) moves the sign kept for that part past its "check" limit, and the true calibration stays
  below every limit on the same recording. Run on the GPU path and with ``OHS_COMPUTE=cpu``.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import calib_health_study as study  # noqa: E402

from ohs.geometry import so3_log  # noqa: E402
from ohs.sim.nominal import nominal_calibration, nominal_emitters  # noqa: E402

TOL = 1e-9


@pytest.fixture(scope="module")
def true_calib():
    import dataclasses

    c = nominal_calibration((1280, 800), realistic=True)
    return dataclasses.replace(c, id="0123456789ab", created="2026-11-18T08:00:00+00:00", temperature_c=25.0,
                               steps={"stereo": {"created": "2026-11-18T08:00:00+00:00", "temperature_c": 25.0}})


def _angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    return math.degrees(math.acos(float(np.clip(np.dot(a, b) / np.linalg.norm(a) / np.linalg.norm(b), -1.0, 1.0))))


def _raw_planes(c) -> dict:
    """Each laser plane on the left camera's raw frame (what does not move when the cameras' angle does)."""
    return {k: (c.stereo.R1.T @ p.normal, p.d) for k, p in c.lasers.items()}


def _same_stereo(a, b) -> None:
    for k in ("K1", "D1", "K2", "D2", "R", "T", "R1", "R2", "P1", "P2", "Q"):
        assert np.array_equal(getattr(a.stereo, k), getattr(b.stereo, k)), k
    assert a.stereo.image_size == b.stereo.image_size


def _no_identity(c) -> None:
    assert c.id is None and c.created is None and c.temperature_c is None and c.steps is None


# --------------------------------------------------------------------------- the cameras
@pytest.mark.parametrize("axis", ["pitch", "yaw", "roll"])
@pytest.mark.parametrize("deg", [0.01, 0.1, -0.05])
def test_rotate_cameras_turns_the_right_camera_by_exactly_that(true_calib, axis, deg):
    c = study.rotate_cameras(true_calib, deg, axis)
    w = so3_log(c.stereo.R @ true_calib.stereo.R.T)  # the turn, in the right camera's frame
    assert np.allclose(w, np.radians(deg) * np.asarray(study.CAMERA_AXES[axis], float), atol=TOL, rtol=0)
    for k in ("K1", "D1", "K2", "D2", "T"):  # nothing else of the pair changed
        assert np.array_equal(getattr(c.stereo, k), getattr(true_calib.stereo, k)), k
    # the rectification is made anew from the turned pair (so the rows follow the old, wrong angle)
    assert not np.allclose(c.stereo.R1, true_calib.stereo.R1, atol=1e-7)
    # the laser planes and the IMU stay where they are on the left camera
    raw0, raw1 = _raw_planes(true_calib), _raw_planes(c)
    assert set(raw0) == set(raw1)
    for k in raw0:
        assert np.allclose(raw0[k][0], raw1[k][0], atol=TOL) and abs(raw0[k][1] - raw1[k][1]) < TOL, k
    assert np.allclose(c.stereo.R1.T @ c.imu.R_ci, true_calib.stereo.R1.T @ true_calib.imu.R_ci, atol=TOL)
    assert c.imu.time_offset == true_calib.imu.time_offset and np.array_equal(c.imu.gyro_bias, true_calib.imu.gyro_bias)
    _no_identity(c)


def test_the_axes_are_the_ones_named(true_calib):
    """Pitch (about the baseline) shifts the rows; yaw (about the up axis) shifts the disparity, so
    the depth; roll (about the optical axis) turns the rows. Checked on a point ahead as the true
    cameras see it, rectified with the turned calibration: its row error and its disparity."""
    import cv2

    t = true_calib.stereo

    def seen(Xc, K, D):  # pixel of a camera of the true pair
        return cv2.projectPoints(Xc.reshape(1, 3), np.zeros(3), np.zeros(3), K, D)[0]

    def dv_disp(stereo, X):
        left = cv2.undistortPoints(seen(X, t.K1, t.D1), stereo.K1, stereo.D1, R=stereo.R1, P=stereo.P1).reshape(2)
        right = cv2.undistortPoints(seen(t.R @ X + t.T, t.K2, t.D2), stereo.K2, stereo.D2, R=stereo.R2,
                                    P=stereo.P2).reshape(2)
        # the row error, and the depth the disparity gives (a new rectification has its own focal length)
        return left[1] - right[1], stereo.P1[0, 0] * stereo.baseline / (left[0] - right[0])

    X = t.R1.T @ np.array([30.0, 20.0, 250.0])  # a point 250 mm ahead, in the left raw frame
    dv0, z0 = dv_disp(t, X)
    assert abs(dv0) < 1e-6 and abs(z0 - 250.0) < 1e-6
    moved = {a: np.subtract(dv_disp(study.rotate_cameras(true_calib, 0.1, a).stereo, X), (dv0, z0))
             for a in ("pitch", "yaw", "roll")}
    f_rad = t.P1[0, 0] * math.radians(0.1)  # about 1.26 px of rows
    z_rad = 250.0**2 * math.radians(0.1) / t.baseline  # about 1.8 mm of depth at 250 mm
    assert abs(abs(moved["pitch"][0]) - f_rad) < 0.2 * f_rad and abs(moved["pitch"][1]) < 0.2 * z_rad  # rows
    assert abs(abs(moved["yaw"][1]) - z_rad) < 0.2 * z_rad and abs(moved["yaw"][0]) < 0.2 * f_rad  # depth
    assert abs(moved["roll"][0]) < 0.2 * f_rad and abs(moved["roll"][1]) < 0.2 * z_rad  # little near the centre


@pytest.mark.parametrize("fraction", [0.001, 0.01, -0.005])
def test_scale_baseline(true_calib, fraction):
    c = study.scale_baseline(true_calib, fraction)
    assert np.allclose(c.stereo.T, true_calib.stereo.T * (1.0 + fraction), atol=TOL, rtol=0)
    assert abs(c.stereo.baseline / true_calib.stereo.baseline - (1.0 + fraction)) < 1e-9
    assert np.array_equal(c.stereo.R, true_calib.stereo.R)
    assert np.allclose(c.stereo.R1, true_calib.stereo.R1, atol=1e-12)  # same direction: same rectification
    for k, p in c.lasers.items():
        assert np.allclose(p.normal, true_calib.lasers[k].normal, atol=1e-12) and abs(p.d - true_calib.lasers[k].d) < TOL
    _no_identity(c)


# --------------------------------------------------------------------------- the lasers
@pytest.mark.parametrize("name", ["vertical", "horizontal"])
@pytest.mark.parametrize("axis", ["aim", "roll"])
@pytest.mark.parametrize("deg", [0.02, 0.2, -0.1])
def test_tilt_laser_turns_one_plane_about_its_emitter(true_calib, name, axis, deg):
    c = study.tilt_laser(true_calib, name, deg, axis=axis)
    p0, p1 = true_calib.lasers[name], c.lasers[name]
    assert abs(_angle_deg(p0.normal, p1.normal) - abs(deg)) < 1e-7
    o = nominal_emitters(baseline=true_calib.stereo.baseline)[name]["origin"]
    assert abs(p0.distance(o[None])[0]) < 1e-9 and abs(p1.distance(o[None])[0]) < 1e-9  # the emitter stays on it
    a = study.laser_axis(p0, axis)  # the turn is about a line in the sheet
    assert abs(a @ p0.normal) < TOL and abs(a @ p1.normal) < 1e-9
    if axis == "aim":  # the aim turns about the line across the image (perpendicular to the optical axis)
        assert abs(a[2]) < TOL
    for k in true_calib.lasers:
        if k != name:
            assert np.array_equal(c.lasers[k].normal, true_calib.lasers[k].normal) and c.lasers[k].d == true_calib.lasers[k].d
    _same_stereo(c, true_calib)
    _no_identity(c)


@pytest.mark.parametrize("name", ["vertical", "horizontal"])
@pytest.mark.parametrize("mm", [0.1, 1.0, -0.3])
def test_shift_laser_moves_one_plane_along_its_normal(true_calib, name, mm):
    c = study.shift_laser(true_calib, name, mm)
    p0, p1 = true_calib.lasers[name], c.lasers[name]
    assert np.allclose(p0.normal, p1.normal, atol=TOL)
    X0 = -p0.d * p0.normal  # on the old plane
    assert abs(p1.distance(X0[None])[0] + mm) < TOL  # the new plane is mm farther out (+ = away from the camera)
    assert abs(-p1.d - (-p0.d + mm)) < TOL
    other = next(k for k in true_calib.lasers if k != name)
    assert c.lasers[other].d == true_calib.lasers[other].d
    _same_stereo(c, true_calib)
    _no_identity(c)


def test_an_unknown_laser_is_refused(true_calib):
    with pytest.raises(KeyError):
        study.shift_laser(true_calib, "diagonal", 0.1)


# --------------------------------------------------------------------------- the IMU
@pytest.mark.parametrize("deg", [0.5, 2.0, -1.0])
def test_rotate_imu(true_calib, deg):
    c = study.rotate_imu(true_calib, deg)
    w = so3_log(c.imu.R_ci @ true_calib.imu.R_ci.T)  # in the camera frame
    assert np.allclose(w, np.radians(deg) * np.asarray(study.IMU_AXIS, float), atol=TOL, rtol=0)
    assert c.imu.time_offset == true_calib.imu.time_offset
    _same_stereo(c, true_calib)
    _no_identity(c)


@pytest.mark.parametrize("seconds", [0.002, 0.02, -0.005])
def test_shift_imu_time(true_calib, seconds):
    c = study.shift_imu_time(true_calib, seconds)
    assert abs(c.imu.time_offset - true_calib.imu.time_offset - seconds) < 1e-12
    assert np.array_equal(c.imu.R_ci, true_calib.imu.R_ci)
    _same_stereo(c, true_calib)
    _no_identity(c)


# --------------------------------------------------------------------------- the faults by name
def test_perturb_by_name_uses_the_table_units(true_calib):
    """``perturb(calib, fault, size)`` in the units of the study's table (degrees, % of the
    baseline, mm, ms), and every fault names the part it belongs to."""
    def same(a, b) -> bool:
        return (all(np.array_equal(getattr(a.stereo, k), getattr(b.stereo, k)) for k in ("R", "T", "R1", "P2"))
                and all(np.array_equal(a.lasers[k].normal, b.lasers[k].normal) and a.lasers[k].d == b.lasers[k].d
                        for k in a.lasers)
                and np.array_equal(a.imu.R_ci, b.imu.R_ci) and a.imu.time_offset == b.imu.time_offset)

    assert same(study.perturb(true_calib, "camera_pitch", 0.05), study.rotate_cameras(true_calib, 0.05, "pitch"))
    assert same(study.perturb(true_calib, "camera_yaw", 0.05), study.rotate_cameras(true_calib, 0.05, "yaw"))
    assert same(study.perturb(true_calib, "baseline", 0.5), study.scale_baseline(true_calib, 0.005))
    assert same(study.perturb(true_calib, "laser_tilt:horizontal", 0.1), study.tilt_laser(true_calib, "horizontal", 0.1))
    assert same(study.perturb(true_calib, "laser_offset:vertical", 0.2), study.shift_laser(true_calib, "vertical", 0.2))
    assert same(study.perturb(true_calib, "imu_rotation", 1.0), study.rotate_imu(true_calib, 1.0))
    assert same(study.perturb(true_calib, "imu_time", 5.0), study.shift_imu_time(true_calib, 0.005))
    assert same(study.perturb(true_calib, "none", 0.0), true_calib)
    parts = {study.fault_part(f) for f in ("camera_pitch", "camera_yaw", "camera_roll", "baseline",
                                           "laser_tilt:vertical", "laser_offset:horizontal", "imu_rotation", "imu_time")}
    assert parts == {"cameras", "lasers", "imu"}
    with pytest.raises(ValueError):
        study.perturb(true_calib, "camera_wobble", 0.1)


# --------------------------------------------------------------------------- the limits
def test_the_limits_are_well_formed():
    from ohs.calib import health

    assert {lim.part for lim in health.LIMITS.values()} == {"cameras", "lasers", "imu"}
    for name, lim in health.LIMITS.items():
        assert 0 < lim.check < lim.recalibrate, name
        assert lim.part in health.PARTS and lim.unit and lim.path, name
    # each laser family has its own limit (the two lines scatter differently against the stereo depth)
    fams = {lim.family for lim in health.LIMITS.values() if lim.part == "lasers"}
    assert fams == {"vertical", "horizontal"}
    assert health.laser_family("vertical_2") == "vertical" and health.laser_family("horizontal") == "horizontal"
    assert set(health.DROPPED) >= {"laser_depth", "tracking_rmse", "temperature", "straighten"}
    # the CPU path's tracking puts a true calibration over the laser and IMU limits: the cameras' sign only
    assert set(health.CPU_PATH) == {"row_error"}


@pytest.fixture(scope="module")
def short_scan(tmp_path_factory) -> Path:
    from recordings import scan_recording

    pytest.importorskip("open3d")
    return scan_recording(tmp_path_factory.mktemp("h2") / "rec", scene="bracket", n_frames=12, image_size=(1280, 800),
                          realistic=True, seed=7, depth_every=0)


def _signs(rec: Path, out: Path, calib) -> dict[str, dict[str, float | None]]:
    """The kept signs that hold on the path the scan was processed on (``health.CPU_PATH`` on the CPU)."""
    from ohs.app.process import ProcessParams, process_recording
    from ohs.calib import health

    rep = process_recording(rec, out, calib=calib, params=ProcessParams(export=("ply",), row_error_every=3))
    gpu = rep["params"]["compute"]["tracking_model"] == "gpu"
    return {name: health.values(rep["health"], lim) for name, lim in health.LIMITS.items()
            if gpu or name in health.CPU_PATH}


def test_one_fault_of_each_part_passes_its_check_limit_and_the_truth_does_not(short_scan, tmp_path):
    """On one short full-resolution bracket scan (12 frames): the true calibration stays below every
    "check" limit; the cameras turned 0.03 deg (rows), the vertical laser 0.2 mm off and the IMU
    turned 3 deg each pass the "check" limit of their own part's sign. A laser or IMU fault moves no
    other part's sign; a camera fault may move the laser lines' too (the study: a pitch moves the
    horizontal line's gap), which is why the status must read the cameras' sign first (H3)."""
    from ohs.calib import health
    from ohs.io.recording import RecordingReader

    true = RecordingReader(short_scan).calibration()
    got = _signs(short_scan, tmp_path / "true", true)
    for name, vals in got.items():
        assert vals and all(v is not None and abs(v) < health.LIMITS[name].check for v in vals.values()), (name, vals)
    kept = {health.LIMITS[n].part for n in got}  # on the CPU path the cameras' sign only (health.CPU_PATH)
    assert "cameras" in kept
    for fault, size, part in (("camera_pitch", 0.03, "cameras"), ("laser_offset:vertical", 0.2, "lasers"),
                              ("imu_rotation", 3.0, "imu")):
        if part not in kept:
            continue
        got = _signs(short_scan, tmp_path / fault.replace(":", "-"), study.perturb(true, fault, size))
        over = {name for name, vals in got.items()
                if any(v is not None and abs(v) >= health.LIMITS[name].check for v in vals.values())}
        parts = {health.LIMITS[n].part for n in over}
        assert part in parts, (fault, over, got)
        if part != "cameras":
            assert parts == {part}, (fault, over, got)
        if fault.startswith("laser_"):  # the line that moved, not the other one
            assert {health.LIMITS[n].family for n in over} == {"vertical"}, (fault, over, got)
