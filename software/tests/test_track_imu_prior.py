"""Tests for ohs.track.imu_prior.GyroPredictor (synthetic gyro, non-identity R_ci, offset, bias)."""
import numpy as np
import pytest

from ohs import geometry as G
from ohs.track.imu_prior import GyroPredictor
from ohs.types import ImuCalibration, ImuSample

RATE = 416.0
R_CI = G.so3_exp(np.array([0.3, -0.8, 1.9]))  # far from identity
BIAS = np.array([0.021, -0.013, 0.017])  # rad/s, IMU frame
OFFSET = 0.0237  # s: camera time = IMU host time + offset


def _calib() -> ImuCalibration:
    return ImuCalibration(R_ci=R_CI, time_offset=OFFSET, gyro_bias=BIAS)


def _orientation(t: float) -> np.ndarray:
    """Smooth, non-trivial camera orientation R_wc(t) with rates up to ~2 rad/s."""
    phi = np.array([0.6 * np.sin(1.3 * t), 0.5 * np.sin(2.1 * t + 0.5), 0.9 * np.sin(0.9 * t + 1.0)])
    return G.so3_exp(phi)


def _omega_c(t: float, h: float = 1e-5) -> np.ndarray:
    """Body (camera-frame) angular velocity of :func:`_orientation` by central differences."""
    return G.so3_log(_orientation(t - h).T @ _orientation(t + h)) / (2 * h)


def _imu_samples(t_cam: np.ndarray, omega_c: np.ndarray, noise: float, seed: int = 0) -> list[ImuSample]:
    rng = np.random.default_rng(seed)
    w_i = omega_c @ R_CI + BIAS + rng.normal(scale=noise, size=omega_c.shape)  # R_ci^T w_c + bias
    return [ImuSample(float(t - OFFSET), float(t - OFFSET + 5.0), w, np.array([0, 0, 9.81]))
            for t, w in zip(t_cam, w_i)]


def test_constant_rate():
    w_c = np.array([0.4, -0.7, 1.3])
    t_cam = np.arange(0.0, 2.0, 1.0 / RATE)
    gp = GyroPredictor(_calib())
    gp.add(_imu_samples(t_cam, np.tile(w_c, (len(t_cam), 1)), noise=0.0))
    for t0, t1 in [(0.5, 0.5 + 1 / 30), (0.2, 1.4), (1.7, 0.3)]:
        assert gp.covers(t0, t1)
        R = gp.relative_rotation(t0, t1)
        truth = G.so3_exp(w_c * (t1 - t0))
        assert G.rotation_angle_deg(R.T @ truth) < 1e-6
    # bias and R_ci matter: a predictor without calibration is clearly wrong
    raw = GyroPredictor()
    raw.add(_imu_samples(t_cam, np.tile(w_c, (len(t_cam), 1)), noise=0.0))
    assert G.rotation_angle_deg(raw.relative_rotation(0.2, 1.4).T @ G.so3_exp(w_c * 1.2)) > 1.0


def test_varying_rate_with_offset_bias_and_noise():
    t_cam = np.arange(0.0, 3.0, 1.0 / RATE) + 0.0011  # IMU clock not aligned with the frames
    omega = np.array([_omega_c(t) for t in t_cam])
    assert np.abs(omega).max() > 1.0  # the motion is not trivially slow
    samples = _imu_samples(t_cam, omega, noise=0.003, seed=1)
    gp = GyroPredictor(_calib())
    # arrive in chunks, like Pico.read_imu() batches
    for k in range(0, len(samples), 37):
        gp.add(samples[k:k + 37])
    frames = np.arange(0.2, 2.8, 1 / 30)
    errs = []
    for t0, t1 in zip(frames[:-1], frames[1:]):
        R = gp.relative_rotation(t0, t1)
        errs.append(G.rotation_angle_deg(R.T @ (_orientation(t0).T @ _orientation(t1))))
    for t0, t1 in [(0.3, 0.8), (1.0, 2.5), (2.6, 0.4)]:  # long intervals and reversed order
        R = gp.relative_rotation(t0, t1)
        errs.append(G.rotation_angle_deg(R.T @ (_orientation(t0).T @ _orientation(t1))))
    assert max(errs) < 0.1, max(errs)

    # the time offset matters: ignoring it gives a clearly larger error on long intervals
    no_offset = GyroPredictor(ImuCalibration(R_ci=R_CI, time_offset=0.0, gyro_bias=BIAS))
    no_offset.add(samples)
    bad = max(G.rotation_angle_deg(no_offset.relative_rotation(a, b).T @ (_orientation(a).T @ _orientation(b)))
              for a, b in [(0.3, 0.8), (1.0, 2.5)])
    assert bad > 0.3


def test_add_arrays_matches_add_and_out_of_order():
    t_cam = np.arange(0.0, 1.0, 1.0 / RATE)
    omega = np.array([_omega_c(t) for t in t_cam])
    samples = _imu_samples(t_cam, omega, noise=0.0)
    a = GyroPredictor(_calib())
    a.add(samples)
    b = GyroPredictor(_calib())
    perm = np.random.default_rng(3).permutation(len(samples))
    half = len(perm) // 2
    for idx in (perm[:half], perm[half:], perm[:10]):  # shuffled, with duplicates
        b.add_arrays(np.array([samples[i].t_host for i in idx]), np.array([samples[i].gyro for i in idx]))
    assert len(b) == len(a)
    assert np.allclose(a.relative_rotation(0.1, 0.9), b.relative_rotation(0.1, 0.9), atol=1e-12)


def test_covers_gaps_and_bounded_buffer():
    gp = GyroPredictor(_calib(), max_duration_s=2.0, max_gap_s=0.02)
    assert not gp.covers(0.0, 0.1)
    with pytest.raises(ValueError):
        gp.relative_rotation(0.0, 0.1)
    t_host = np.arange(0.0, 1.0, 1.0 / RATE)
    gp.add_arrays(t_host, np.zeros((len(t_host), 3)))
    lo, hi = gp.time_range
    assert lo == pytest.approx(OFFSET) and hi == pytest.approx(t_host[-1] + OFFSET)
    assert gp.covers(0.2, 0.9)
    assert not gp.covers(0.0, 0.5)  # starts before the first (offset-shifted) sample
    assert not gp.covers(0.5, 1.2)  # extends past the newest sample
    # a 50 ms hole (dropped packets)
    t2 = np.arange(1.05, 1.5, 1.0 / RATE)
    gp.add_arrays(t2, np.zeros((len(t2), 3)))
    assert not gp.covers(0.9 + OFFSET, 1.2 + OFFSET)
    assert gp.covers(1.1 + OFFSET, 1.4 + OFFSET)
    # bounded: after 10 s of data only ~2 s remain
    t3 = np.arange(1.5, 11.5, 1.0 / RATE)
    gp.add_arrays(t3, np.zeros((len(t3), 3)))
    lo, hi = gp.time_range
    assert hi - lo <= 2.0 + 1e-9 and len(gp) <= int(2.0 * RATE) + 2
    capped = GyroPredictor(max_samples=100)
    capped.add_arrays(t3, np.zeros((len(t3), 3)))
    assert len(capped) == 100 and capped.time_range[1] == pytest.approx(t3[-1])
