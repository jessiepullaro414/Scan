"""Simulator IMU synthesis: gyro integration, specific force, clocks and the time-offset convention."""
import numpy as np
import pytest

from ohs.geometry import integrate_gyro, make_se3, rotation_angle_deg, so3_exp
from ohs.sim.imu import ImuSimParams, body_rates, synthesize_imu, synthesize_imu_arrays
from ohs.sim.nominal import nominal_calibration
from ohs.sim.trajectory import HandheldTrajectory, StaticTrajectory
from ohs.types import ImuSample

CAL = nominal_calibration()
R_CI = CAL.imu.R_ci
TO = CAL.imu.time_offset


def _traj(seed=3):
    return HandheldTrajectory(target=[0.0, 0.0, 30.0], t0=10.0, duration=3.0, seed=seed, sweep_deg_s=35.0)


@pytest.mark.parametrize("noisy", [False, True])
def test_gyro_integrates_to_true_relative_rotation(noisy):
    traj = _traj()
    params = ImuSimParams(gyro_bias=CAL.imu.gyro_bias) if noisy else ImuSimParams.ideal()
    a = synthesize_imu_arrays(traj, 9.5, 13.5, R_CI, TO, params, seed=1)
    for s0 in (10.0, 10.9, 11.7):  # IMU host times
        R_i = integrate_gyro(a["t_host"], a["gyro"], s0, s0 + 1.0, bias=CAL.imu.gyro_bias if noisy else None)
        R_c = R_CI @ R_i @ R_CI.T
        # IMU host s <-> camera time s + time_offset
        T0, T1 = traj.pose(s0 + TO), traj.pose(s0 + 1.0 + TO)
        R_true = T0[:3, :3].T @ T1[:3, :3]
        assert rotation_angle_deg(R_true) > 10.0
        assert rotation_angle_deg(R_c.T @ R_true) < 0.2


def test_specific_force_at_rest_is_gravity():
    T = make_se3(so3_exp(np.array([0.3, -1.1, 0.4])), [10.0, 20.0, 300.0])
    a = synthesize_imu_arrays(StaticTrajectory(T), 0.0, 1.0, R_CI, TO, ImuSimParams.ideal())
    expected = R_CI.T @ T[:3, :3].T @ np.array([0.0, 0.0, 9.81])
    assert np.allclose(a["accel"], expected, atol=1e-9)
    assert np.allclose(np.linalg.norm(a["accel"], axis=1), 9.81)
    assert np.abs(a["gyro"]).max() < 1e-12
    # realistic sensor at rest: mean = truth + bias, white noise of the configured size
    p = ImuSimParams()
    b = synthesize_imu_arrays(StaticTrajectory(T), 0.0, 10.0, R_CI, TO, p, seed=4)
    assert np.allclose(b["accel"].mean(0), expected + p.accel_bias, atol=3e-3)
    assert np.allclose(b["gyro"].mean(0), p.gyro_bias, atol=3e-4)
    assert b["gyro"].std(0) == pytest.approx(np.full(3, p.gyro_noise), rel=0.25)
    assert b["accel"].std(0) == pytest.approx(np.full(3, p.accel_noise), rel=0.25)
    # quantised to the +-1000 dps LSB
    q = b["gyro"] / p.gyro_lsb
    assert np.allclose(q, np.round(q), atol=1e-6)


def test_lever_arm_centripetal_acceleration():
    """Pure rotation about the camera centre: the IMU 31 mm behind sees w^2 r (+ gravity)."""
    w = 2.0  # rad/s about camera y

    class Spin:
        def poses(self, t):
            t = np.atleast_1d(t)
            out = np.zeros((len(t), 4, 4))
            out[:, :3, :3] = [so3_exp(np.array([0.0, w * ti, 0.0])) for ti in t]
            out[:, 3, 3] = 1.0
            return out

        def pose(self, t):
            return self.poses(np.array([t]))[0]

    p = ImuSimParams.ideal(gravity=np.zeros(3))
    a = synthesize_imu_arrays(Spin(), 0.0, 0.5, R_CI, 0.0, p)
    f_c = a["accel"] @ R_CI.T  # back to the camera frame
    lever = p.lever_arm_mm / 1000.0
    expected = -(w**2) * np.array([lever[0], 0.0, lever[2]])  # centripetal, towards the axis
    assert np.allclose(f_c, expected, atol=1e-5)
    assert np.allclose(a["gyro"] @ R_CI.T, [0.0, w, 0.0], atol=1e-7)


def test_device_clock_offset_and_drift():
    p = ImuSimParams.ideal()
    a = synthesize_imu_arrays(StaticTrajectory(np.eye(4)), 100.0, 110.0, R_CI, TO, p)
    td, th = a["t_device"], a["t_host"]
    assert np.allclose(np.diff(td), 1 / 416, atol=1.1e-6)  # evenly spaced in device time, 1 us ticks
    slope, offset = np.polyfit(th, td, 1)
    assert slope == pytest.approx(1 + 20e-6, abs=2e-7)
    assert offset == pytest.approx(p.device_offset_s, abs=1e-4)
    assert len(td) == pytest.approx(10.0 * 416 * (1 + 20e-6), abs=1.5)
    assert th[0] >= 100.0 and th[-1] <= 110.0 and (np.diff(th) > 0).all()
    # jittered host stamps by default (clock-sync residual), still monotonic
    b = synthesize_imu_arrays(StaticTrajectory(np.eye(4)), 100.0, 101.0, R_CI, TO, ImuSimParams(), seed=2)
    resid = b["t_host"] - (b["t_device"] - p.device_offset_s) / (1 + 20e-6)
    assert 5e-6 < resid.std() < 1e-4


def test_time_offset_convention():
    """IMU host time s observes the motion at camera time s + time_offset."""
    traj = _traj(seed=5)
    a = synthesize_imu_arrays(traj, 10.0, 13.0, R_CI, TO, ImuSimParams.ideal())
    speed_imu = np.linalg.norm(a["gyro"], axis=1)
    tc = np.arange(10.3, 12.6, 0.002)
    speed_cam = np.linalg.norm(body_rates(traj, tc), axis=1)
    lags = np.arange(-0.08, 0.08, 0.0005)
    cost = [np.mean(np.abs(np.interp(tc - lag, a["t_host"], speed_imu) - speed_cam)) for lag in lags]
    assert lags[int(np.argmin(cost))] == pytest.approx(TO, abs=1e-3)


def test_synthesize_imu_returns_samples():
    s = synthesize_imu(_traj(), 10.0, 10.1, R_CI, TO, seed=0)
    assert all(isinstance(x, ImuSample) for x in s) and 40 <= len(s) <= 43
    assert s[0].gyro.shape == (3,) and s[0].accel.shape == (3,) and s[0].t_device > s[0].t_host
