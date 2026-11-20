"""The velocity guard of the Open3D model (MAINTENANCE_PLAN F14; ``ohs.track.markerless``, "The velocity guard").

On a scene the depth barely holds sideways (a dark part on a bench: the part is a few percent of the image,
the rest a plane), one frame can be pushed far off, and the constant-velocity prediction then carries that
error on, frame after frame: the simulated speckled bracket, tracked on the CPU path with the CPU matcher
averaging the pair upside down, slid 15-60 mm a frame over its last 14 frames and ended 222 mm off.
``FusionParams.velocity_max_accel`` bounds what a frame carries into the next prediction: its velocity
may differ from the last one by at most that acceleration times the frame time, and the first velocity
after the start or a lost frame is at most ``VELOCITY_MAX_SPEED``. The pose itself is what ICP found.
The GPU model ignores the setting.
"""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs.geometry import make_se3  # noqa: E402
from ohs.track.markerless import VELOCITY_MAX_ACCEL, VELOCITY_MAX_SPEED, FusionParams, MarkerlessTracker  # noqa: E402
from test_track_markerless import FPS, H, K, W, arc_trajectory, imu_predictor, render_depth  # noqa: E402

DT = 1.0 / 30.0


def _steps(tracker, xs_mm):
    """Accept poses moving along x by ``xs_mm`` (cumulative positions) a frame apart; the carried steps."""
    tracker._accept(np.eye(4), 0.0, None)
    out, acted = [], []
    for k, x in enumerate(xs_mm, start=1):
        tracker._accept(make_se3(np.eye(3), np.array([x, 0.0, 0.0])), k * DT, True)
        out.append(float(tracker._last_step[1][0]))
        acted.append(tracker._guard_mm)
    return out, acted


def test_the_guard_bounds_the_velocity_carried_into_the_next_prediction():
    """A first frame pushed 18.7 mm (the dark bracket's, where the scanner moved 3 mm), then the same again."""
    tr = MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, model_backend="open3d",
                                                   velocity_max_accel=VELOCITY_MAX_ACCEL))
    steps, acted = _steps(tr, [18.7, 37.4])
    first = VELOCITY_MAX_SPEED * DT  # 10 mm at 30 frames a second
    assert steps[0] == pytest.approx(first) and acted[0] == pytest.approx(18.7 - first)
    second = first + VELOCITY_MAX_ACCEL * 1000.0 * DT * DT  # + 2.2 mm
    assert steps[1] == pytest.approx(second) and acted[1] == pytest.approx(18.7 - second)
    # without the guard (the default), and on the GPU model, the steps are carried as they are
    off = MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, model_backend="open3d"))
    assert _steps(off, [18.7, 37.4]) == ([pytest.approx(18.7), pytest.approx(18.7)], [0.0, 0.0])
    try:
        gpu = MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, model_backend="gpu",
                                                        velocity_max_accel=VELOCITY_MAX_ACCEL))
    except RuntimeError:
        return  # no GPU here
    assert _steps(gpu, [18.7, 37.4]) == ([pytest.approx(18.7), pytest.approx(18.7)], [0.0, 0.0])


def test_the_guard_leaves_a_hand_held_motion_alone():
    """4 mm a frame from the start (the simulator's fastest), changing by 0.5 mm a frame (0.45 m/s^2)."""
    tr = MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, model_backend="open3d",
                                                   velocity_max_accel=VELOCITY_MAX_ACCEL))
    v = [4.0, 4.5, 4.0, 3.5, 4.0]
    steps, acted = _steps(tr, list(np.cumsum(v)))
    assert steps == [pytest.approx(x) for x in v] and acted == [0.0] * len(v)
    # after a lost frame the first velocity is bounded again, not compared with the old one
    tr._accept(np.eye(4), 1.0, False)
    assert tr._v_w is None


def test_a_scan_the_guard_does_not_act_on_is_the_same_bit_for_bit():
    """The sphere-on-plane arc of ``test_track_markerless`` (its first 12 frames, smooth): the same poses with and
    without the guard, and no frame says the guard acted."""
    poses = arc_trajectory(36)[:12]
    runs = []
    for accel in (None, VELOCITY_MAX_ACCEL):
        rng = np.random.default_rng(0)
        gp = imu_predictor(poses, rng)
        tr = MarkerlessTracker(K, (W, H), FusionParams(voxel_mm=2.0, model_backend="open3d", velocity_max_accel=accel))
        res = []
        for k, T in enumerate(poses):
            t_prev, t = (k - 1) / FPS, k / FPS
            R_prior = gp.relative_rotation(t_prev, t) if k and gp.covers(t_prev, t) else None
            res.append(tr.track(render_depth(T, rng), None, t, R_prior))
        runs.append(res)
    assert all(r.ok for r in runs[1]) and not any("velocity_guard_mm" in r.info for r in runs[1])
    assert all(np.array_equal(a.T_wc, b.T_wc) for a, b in zip(*runs))
