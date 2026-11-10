"""ohs.track.curve_ba: the laser lines' crossings line up the poses (research plan 7.3, poses only).

A 400 deg orbit around the sphere on the table (640x400, 60 frames, dots and lasers), rendered once:

* at the true poses the crossings meet within the lasers' own scatter;
* true poses come back unchanged (the held-out crossings would meet worse);
* random-walk drift is reduced, the first frame kept;
* a scan with too few crossings is left as it is.
"""
from __future__ import annotations

import numpy as np
import pytest

from ohs.geometry import invert_se3
from ohs.io.recording import RecordingReader
from ohs.sim.datasets import make_scan_recording
from ohs.sim.nominal import NOMINAL_TIME_OFFSET, nominal_calibration
from ohs.sim.scene import make_scene
from ohs.sim.trajectory import HandheldTrajectory, add_drift
from ohs.track.curve_ba import CurveParams, crossing_residuals, find_crossings, frame_curves, laser_curve_adjustment


@pytest.fixture(scope="module")
def orbit(tmp_path_factory):
    calib = nominal_calibration((640, 400), realistic=True)
    sc = make_scene("sphere_on_plane", 7)
    t0 = 100.0 + NOMINAL_TIME_OFFSET
    traj = HandheldTrajectory(target=sc.target, t0=t0, duration=12.0, seed=7, baseline=calib.stereo.baseline,
                              distance=(220.0, 300.0), elevation_deg=(35.0, 65.0), sweep_deg_s=400.0 / 12.0)
    return make_scan_recording(tmp_path_factory.mktemp("cba") / "rec", scene="sphere_on_plane", n_frames=60, fps=5.0,
                               image_size=(640, 400), realistic=True, trajectory=traj, depth_every=0, seed=7)


@pytest.fixture(scope="module")
def truth(orbit):
    reader = RecordingReader(orbit)
    gt = reader.ground_truth_poses()
    idx = sorted(gt)
    return idx, np.stack([invert_se3(gt[idx[0]]) @ gt[i] for i in idx]), reader.times()


def rows(idx, T, times):
    return [(int(i), float(times[k]), True, T[k]) for k, i in enumerate(idx)]


def test_the_curves_meet_at_the_true_poses(orbit, truth):
    idx, T, _ = truth
    reader = RecordingReader(orbit)
    curves = frame_curves(reader, reader.calibration(), idx)
    assert all(len(cs) == 2 for cs in curves.values() if cs)  # both lines in the frames that see them
    Tt = {f: T[k] for k, f in enumerate(idx)}
    cr = find_crossings(curves, Tt)
    r = crossing_residuals(Tt, cr)[0]
    assert len(cr) > 800
    assert np.median(np.abs(r)) < 0.25 and abs(np.mean(r)) < 0.06  # the lasers' own scatter at 640x400
    # each crossing joins two different frames, and its points are on their curves (frame C, in front)
    assert np.all(cr.fa != cr.fb) and np.all(cr.xa[:, 2] > 50) and np.all(cr.xb[:, 2] > 50)
    assert np.allclose(np.linalg.norm(cr.ta, axis=1), 1.0) and np.allclose(np.linalg.norm(cr.tb, axis=1), 1.0)


def test_true_poses_are_left_alone(orbit, truth):
    idx, T, times = truth
    poses, rep = laser_curve_adjustment(orbit, None, rows(idx, T, times))
    assert rep["reason"] == "not_better" and rep["changed"] is False, rep["verdict"]
    assert rep["holdout"]["ratio"] > CurveParams().holdout_max_ratio  # fitting the scatter: the other half meet worse
    assert all(np.array_equal(poses[i], T[k]) for k, i in enumerate(idx))
    assert "unchanged" in rep["verdict"]


def test_drift_is_reduced(orbit, truth):
    idx, T, times = truth
    D = add_drift(T, 0.2, 0.6, 3)
    poses, rep = laser_curve_adjustment(orbit, None, rows(idx, D, times))
    g = rep["ground_truth"]
    assert rep["reason"] == "adjusted" and rep["changed"] is True, rep["verdict"]
    assert rep["holdout"]["ratio"] < 0.7 and rep["miss_mm"]["after"] < 0.6 * rep["miss_mm"]["before"]
    assert g["after"]["rms_trans_mm"] < 0.8 * g["before"]["rms_trans_mm"]
    assert np.array_equal(poses[idx[0]], D[0]) and set(poses) == set(idx)  # the first frame defines the world
    assert rep["moved_mm"] > 0.5 and rep["verdict"].startswith("Lined the scan up on")


def test_few_crossings_change_nothing(orbit, truth):
    idx, T, times = truth
    D = add_drift(T, 0.2, 0.6, 3)
    poses, rep = laser_curve_adjustment(orbit, None, rows(idx, D, times), params=CurveParams(max_frames=4))
    assert rep["reason"] == "few_crossings" and rep["changed"] is False and rep["frames"] == 4
    assert all(np.array_equal(poses[i], D[k]) for k, i in enumerate(idx))
