"""IMU-camera calibration on a synthetic smooth rotation trajectory (area C)."""
import numpy as np
import pytest

from ohs import geometry as G
from ohs.calib.imu import calibrate_imu_camera, find_static_segment

TRUE_R_CI = G.so3_exp([0.4, -0.25, 1.2])  # ~76 deg, about a skewed axis
TRUE_OFFSET = 0.032
TRUE_BIAS = np.array([0.012, -0.018, 0.009])


def _exp(w):
    return np.stack([G.so3_exp(v) for v in w])


def _log(R):
    return np.stack([G.so3_log(r) for r in R])


def make_data(seed=0, duration=20.0, static_s=1.5, axes=(1, 1, 1), cam_noise_deg=0.03, gyro_noise=0.002):
    """Camera orientations at 30 Hz (+ pose noise, timestamp jitter) and raw gyro at 416 Hz."""
    rng = np.random.default_rng(seed)
    nf = 5
    freqs = rng.uniform(0.2, 1.2, (nf, 3))
    amps = rng.uniform(0.06, 0.22, (nf, 3)) * np.asarray(axes, float)
    phases = rng.uniform(0, 2 * np.pi, (nf, 3))
    theta0 = np.array([0.2, -0.1, 0.05])

    def theta(t):
        tt = np.clip(t - static_s, 0.0, None)[:, None]
        x = np.clip(tt / 1.0, 0.0, 1.0) if static_s > 0 else np.ones_like(tt)
        ramp = 10 * x**3 - 15 * x**4 + 6 * x**5  # smooth start of the motion
        s = sum(amps[k] * (np.sin(2 * np.pi * freqs[k] * tt + phases[k]) - np.sin(phases[k])) for k in range(nf))
        return theta0 + ramp * s

    def R_wc(t):
        return _exp(theta(t))

    def omega_c(t, h=1e-4):  # body-frame angular velocity of the camera
        Ra, Rb = R_wc(t - h / 2), R_wc(t + h / 2)
        return _log(np.swapaxes(Ra, 1, 2) @ Rb) / h

    # IMU: physical times tau, host timestamps = tau - offset (camera time = IMU host time + offset)
    tau = np.arange(0.0, duration, 1 / 416.0)
    imu_t = tau - TRUE_OFFSET + rng.normal(0, 2e-4, len(tau))
    gyro = omega_c(tau) @ TRUE_R_CI + TRUE_BIAS + rng.normal(0, gyro_noise, (len(tau), 3))  # R_ci^T w_c
    # camera: exposure times on the camera clock, with timestamp jitter and board-pose noise
    t_cam = np.arange(0.15, duration - 0.15, 1 / 30.0)
    cam_t = t_cam + rng.normal(0, 3e-4, len(t_cam))
    noise = _exp(rng.normal(0, np.radians(cam_noise_deg), (len(t_cam), 3)))
    cam_R = R_wc(t_cam) @ noise
    return cam_t, cam_R, imu_t, gyro


def check(calib):
    assert G.rotation_angle_deg(calib.R_ci.T @ TRUE_R_CI) < 0.5
    assert abs(calib.time_offset - TRUE_OFFSET) < 0.002
    assert np.linalg.norm(calib.gyro_bias - TRUE_BIAS) < 0.002


def test_imu_calibration_with_static_segment():
    cam_t, cam_R, imu_t, gyro = make_data(seed=1, static_s=1.5)
    seg = find_static_segment(imu_t, gyro)
    assert seg is not None and imu_t[seg[1] - 1] - imu_t[seg[0]] > 1.0
    calib, rep = calibrate_imu_camera(cam_t, cam_R, imu_t, gyro)
    check(calib)
    assert rep["bias_source"] == "static" and rep["ok"] and rep["verdict"] == "good"
    assert rep["xcorr_peak"] > 0.9 and abs(rep["time_offset_xcorr_s"] - TRUE_OFFSET) < 0.005
    assert rep["residual_rms_deg"] < 0.1


def test_imu_calibration_joint_bias_without_static_segment():
    cam_t, cam_R, imu_t, gyro = make_data(seed=2, static_s=0.0)
    assert find_static_segment(imu_t, gyro) is None
    # frames without a board pose are simply missing: drop a 1 s block
    keep = (cam_t < 8.0) | (cam_t > 9.0)
    calib, rep = calibrate_imu_camera(cam_t[keep], cam_R[keep], imu_t, gyro)
    check(calib)
    assert rep["bias_source"] == "joint" and rep["ok"]


def test_imu_calibration_robust_to_bad_board_poses():
    cam_t, cam_R, imu_t, gyro = make_data(seed=3, static_s=0.0)
    rng = np.random.default_rng(30)
    bad = np.flatnonzero(rng.random(len(cam_t)) < 0.05)  # e.g. flipped planar-PnP solutions
    for i in bad:
        axis = rng.normal(size=3)
        cam_R[i] = cam_R[i] @ G.so3_exp(axis / np.linalg.norm(axis) * np.radians(25.0))
    calib, rep = calibrate_imu_camera(cam_t, cam_R, imu_t, gyro)
    check(calib)
    assert rep["n_frames_rejected"] >= 0.9 * len(bad) and rep["ok"]


def test_imu_calibration_reports_single_axis_motion():
    cam_t, cam_R, imu_t, gyro = make_data(seed=3, static_s=1.0, axes=(0, 0, 1))
    calib, rep = calibrate_imu_camera(cam_t, cam_R, imu_t, gyro)
    assert not rep["ok"] and "one axis" in rep["verdict"]
    assert abs(calib.time_offset - TRUE_OFFSET) < 0.005  # the offset is still observable


def test_imu_calibration_rejects_too_little_data():
    cam_t, cam_R, imu_t, gyro = make_data(seed=4, duration=2.0)
    with pytest.raises(ValueError):
        calibrate_imu_camera(cam_t[:10], cam_R[:10], imu_t, gyro)
