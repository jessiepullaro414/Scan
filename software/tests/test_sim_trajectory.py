"""Simulator trajectories: look-at geometry, ranges, smoothness, continuous-time API."""
import numpy as np
import pytest

from ohs.geometry import so3_log
from ohs.sim.imu import body_rates
from ohs.sim.nominal import nominal_calibration
from ohs.sim.trajectory import HandheldTrajectory, StaticTrajectory, look_at, sample_frames, so3_exp_batch


def test_look_at_frame():
    T = look_at([0.0, -200.0, 150.0], [0.0, 0.0, 0.0])
    R = T[:3, :3]
    assert np.allclose(R.T @ R, np.eye(3)) and np.linalg.det(R) == pytest.approx(1.0)
    z = R[:, 2]
    assert np.allclose(z, -np.array([0.0, -200.0, 150.0]) / 250.0)
    assert R[:, 1] @ np.array([0.0, 0.0, 1.0]) < 0  # image down points away from world up
    assert abs(R[:, 0] @ np.array([0.0, 0.0, 1.0])) < 1e-12  # no roll: x stays horizontal
    with pytest.raises(ValueError):
        look_at([0.0, 0.0, 100.0], [0.0, 0.0, 0.0])  # straight down along "up" is singular


def test_handheld_trajectory_ranges_and_aim():
    target = np.array([10.0, -5.0, 30.0])
    traj = HandheldTrajectory(target=target, t0=5.0, duration=4.0, seed=7, distance=(200.0, 320.0))
    t = np.linspace(5.0, 9.0, 400)
    T = traj.poses(t)
    R = T[:, :3, :3]
    assert np.allclose(np.einsum("nij,nik->njk", R, R), np.eye(3), atol=1e-12)
    assert np.allclose(np.linalg.det(R), 1.0)
    centre = T[:, :3, 3] + 30.0 * R[:, :, 0]  # scanner centre, 30 mm right of the left camera
    dist = np.linalg.norm(centre - target, axis=1)
    assert dist.min() > 200.0 - 12 and dist.max() < 320.0 + 12 and 150 < dist.min() and dist.max() < 400
    # the target stays near the middle of the left image
    K = nominal_calibration().stereo.K_rect
    Pc = np.einsum("nji,nj->ni", R, target - T[:, :3, 3])
    u = K[0, 0] * Pc[:, 0] / Pc[:, 2] + K[0, 2]
    v = K[1, 1] * Pc[:, 1] / Pc[:, 2] + K[1, 2]
    assert (Pc[:, 2] > 0).all() and (np.abs(u - 640) < 250).all() and (np.abs(v - 400) < 150).all()
    # continuous-time API: scalar and vector evaluation agree, also outside [t0, t0 + duration]
    assert np.allclose(traj.pose(6.123), traj.poses(np.array([6.123]))[0])
    assert np.isfinite(traj.poses(np.array([0.0, 20.0]))).all()


def test_handheld_motion_is_smooth_and_hand_like():
    traj = HandheldTrajectory(target=[0.0, 0.0, 30.0], t0=0.0, duration=3.0, seed=1)
    t = np.linspace(0.0, 3.0, 3001)
    w = body_rates(traj, t)
    speed = np.linalg.norm(w, axis=1)
    assert 0.1 < speed.mean() < 1.5 and speed.max() < 3.0  # rad/s
    p = traj.poses(t)[:, :3, 3]
    v = np.linalg.norm(np.diff(p, axis=0), axis=1) / np.diff(t)
    assert 10 < v.mean() < 300 and v.max() < 500  # mm/s
    # frame-to-frame motion at 30 fps is ICP-friendly
    times, poses = sample_frames(traj, 90, 30.0, t0=0.0)
    assert poses.shape == (90, 4, 4) and np.allclose(np.diff(times), 1 / 30)
    rel = [np.degrees(np.linalg.norm(so3_log(poses[i, :3, :3].T @ poses[i + 1, :3, :3]))) for i in range(89)]
    assert max(rel) < 3.0
    # tremor makes the gyro signal non-trivial at high frequency
    spec = np.abs(np.fft.rfft(w[:, 0] - w[:, 0].mean()))
    f = np.fft.rfftfreq(len(t), t[1] - t[0])
    assert spec[(f > 5) & (f < 12)].max() > 0.5 * spec[(f > 12) & (f < 30)].max()
    with pytest.raises(ValueError):
        HandheldTrajectory(target=[0, 0, 0], elevation_deg=(60.0, 89.0))


def test_static_trajectory_and_so3_exp_batch():
    T = np.eye(4)
    T[:3, 3] = [1.0, 2.0, 3.0]
    st = StaticTrajectory(T)
    assert np.allclose(st.poses(np.arange(5.0)), T) and np.allclose(st.pose(3.0), T)
    w = np.array([[0.0, 0.0, 0.0], [1e-10, 0, 0], [0.3, -0.2, 1.4]])
    R = so3_exp_batch(w)
    for wi, Ri in zip(w, R):
        assert np.allclose(so3_log(Ri), wi, atol=1e-12)
