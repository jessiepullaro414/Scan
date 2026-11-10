"""Frames picked by motion (maintenance plan F2 step 2): :func:`ohs.app.process.pick_frames_by_motion`.

Synthetic gyro arrays only (no rendering): a 60 fps recording, a 416 Hz gyro, a time offset between
the IMU and the camera clock and a gyro bias, as in a real recording. The pipeline side (report,
fallbacks, cuts, the second pass at given poses) is in test_app_pipeline.py and
test_server_process_cuts.py.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from ohs.app.process import pick_frames_by_motion

FPS = 60.0
OFFSET = 0.0123  # camera time = IMU host time + OFFSET
BIAS = np.array([0.004, -0.003, 0.002])  # rad/s, IMU frame


def _frames(n: int, t0: float = 100.0) -> tuple[list[int], np.ndarray]:
    return list(range(n)), t0 + np.arange(n) / FPS


def _gyro(rate_deg_s, t0: float = 99.5, t1: float = 101.5, hz: float = 416.0, axis=(0.3, -0.5, 0.8)) -> tuple:
    """IMU host times and raw gyro (rad/s, with BIAS) for a rotation at ``rate_deg_s(t_camera)`` about ``axis``."""
    t_cam = np.arange(t0, t1, 1.0 / hz)
    a = np.asarray(axis, dtype=np.float64) / np.linalg.norm(axis)
    rate = np.radians(np.broadcast_to(rate_deg_s(t_cam) if callable(rate_deg_s) else rate_deg_s, t_cam.shape))
    return t_cam - OFFSET, rate[:, None] * a[None] + BIAS


def _pick(pos, ft, t, g, **kw):
    kw = {"time_offset": OFFSET, "gyro_bias": BIAS, **kw}
    return pick_frames_by_motion(pos, ft, t, g, **kw)


def test_rotation_picks_by_angle_and_the_cap_bounds_the_gap():
    pos, ft = _frames(20)
    t, g = _gyro(30.0)  # 0.5 deg per recorded frame
    chosen, why = _pick(pos, ft, t, g, threshold_deg=0.99, max_gap=4)
    assert chosen == list(range(0, 20, 2)) and why == ["first"] + ["rotation"] * 9
    chosen, why = _pick(pos, ft, t, g, threshold_deg=1.49, max_gap=4)
    assert chosen == list(range(0, 20, 3)) and set(why[1:]) == {"rotation"}
    # the cap comes first when the angle would need more frames than it allows
    chosen, why = _pick(pos, ft, t, g, threshold_deg=10.0, max_gap=4)
    assert chosen == list(range(0, 20, 4)) and set(why[1:]) == {"cap"}
    # a threshold below the motion of one frame keeps every frame
    chosen, _ = _pick(pos, ft, t, g, threshold_deg=0.01, max_gap=4)
    assert chosen == pos


def test_still_scanner_is_picked_by_the_cap_only_and_the_bias_is_removed():
    pos, ft = _frames(13)
    t, g = _gyro(0.0)  # only the bias: 0.31 deg/s, 0.021 deg over 4 frames
    chosen, why = _pick(pos, ft, t, g, threshold_deg=0.02, max_gap=4)
    assert chosen == [0, 4, 8, 12] and why == ["first", "cap", "cap", "cap"]
    # without subtracting the bias the same samples would turn by 0.02 deg within 4 frames
    chosen, why = _pick(pos, ft, t, g, threshold_deg=0.02, max_gap=4, gyro_bias=None)
    assert "rotation" in why


def test_net_rotation_not_the_path():
    """Turning back and forth by 0.3 deg within a gap is no new view: the angle is the net rotation."""
    pos, ft = _frames(9)
    period = 4 / FPS
    # 0.3 deg out by frame 2 and back by frame 4: 0.6 deg of path, no net turn
    t, g = _gyro(lambda tc: 0.3 * math.pi / period * np.sin(2 * math.pi * (tc - 100.0) / period))
    chosen, why = _pick(pos, ft, t, g, threshold_deg=0.5, max_gap=4)
    assert chosen == [0, 4, 8] and set(why[1:]) == {"cap"}


def test_step_is_the_smallest_gap_and_the_cap_counts_recorded_frames():
    pos, ft = _frames(30)
    pos, ft = pos[::2], ft[::2]  # frame_step 2: the candidates
    t, g = _gyro(30.0)  # 1 deg per candidate
    chosen, _ = _pick(pos, ft, t, g, threshold_deg=0.5, max_gap=4)
    assert chosen == pos  # at least every 2nd frame
    chosen, why = _pick(pos, ft, t, g, threshold_deg=50.0, max_gap=5)
    assert chosen == list(range(0, 30, 6)) and set(why[1:]) == {"cap"}  # first candidate with a gap >= 5
    chosen, _ = _pick(pos[1:], ft[1:], t, g, threshold_deg=50.0, max_gap=4)  # start = 2
    assert chosen == list(range(2, 30, 4))


def test_restart_after_a_cut():
    pos, ft = _frames(20)
    t, g = _gyro(30.0)
    keep = [p for p in pos if not 5 <= p <= 9]  # frames 5..9 cut
    chosen, why = _pick(keep, ft[keep], t, g, threshold_deg=10.0, max_gap=4, restart={10})
    assert chosen == [0, 4, 10, 14, 18] and why == ["first", "cap", "restart", "cap", "cap"]
    # rotation does not carry over a cut: the angle starts again at the restart
    chosen, why = _pick(keep, ft[keep], t, g, threshold_deg=1.4, max_gap=8, restart={10})
    assert chosen == [0, 3, 10, 13, 16, 19] and why == ["first", "rotation", "restart"] + ["rotation"] * 3


def test_no_gyro_keeps_every_candidate():
    pos, ft = _frames(12)
    t, g = _gyro(0.0)
    # no samples at all
    chosen, why = _pick(pos, ft, t[:0], g[:0], threshold_deg=5.0, max_gap=4)
    assert chosen == pos and why == ["first"] + ["no_gyro"] * 11
    # a 0.1 s hole in the IMU stream over frames 4..9: those frames are all processed
    hole = (t + OFFSET > ft[3] + 0.002) & (t + OFFSET < ft[3] + 0.102)
    chosen, why = _pick(pos, ft, t[~hole], g[~hole], threshold_deg=5.0, max_gap=4)
    assert chosen == [0, 4, 5, 6, 7, 8, 9, 10] and why == ["first"] + ["no_gyro"] * 7  # frame 11: gyro again
    # samples that end before the frames do
    end = t + OFFSET < ft[4] + 0.001
    chosen, why = _pick(pos, ft, t[end], g[end], threshold_deg=5.0, max_gap=4)
    assert chosen == [0, 4, 5, 6, 7, 8, 9, 10, 11] and why == ["first", "cap"] + ["no_gyro"] * 7


def test_bad_arguments():
    pos, ft = _frames(3)
    t, g = _gyro(0.0)
    with pytest.raises(ValueError):
        _pick(pos, ft, t, g, threshold_deg=0.0, max_gap=4)
    with pytest.raises(ValueError):
        _pick(pos, ft, t, g, threshold_deg=1.0, max_gap=0)
    with pytest.raises(ValueError):
        _pick(pos, ft[:2], t, g, threshold_deg=1.0, max_gap=4)
    assert _pick([], [], t, g, threshold_deg=1.0, max_gap=4) == ([], [])
