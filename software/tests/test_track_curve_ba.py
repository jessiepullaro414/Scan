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
from recordings import scan_recording
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
    return scan_recording(tmp_path_factory.mktemp("cba") / "rec", scene="sphere_on_plane", n_frames=60, fps=5.0,
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


def test_four_laser_curves_use_the_farther_camera(tmp_path, monkeypatch):
    """Four lasers: curves of all four planes, vertical_2 measured from the right camera (about
    twice as far from it as from the left one), so its points lie closer to the true surface."""
    from dataclasses import replace

    import ohs.app.pipeline as pipeline
    from ohs import geometry as G
    from test_e2e_scan import surface_distance

    rec = scan_recording(tmp_path / "rec4", scene="sphere_on_plane", n_frames=6, image_size=(640, 400), realistic=True,
                         depth_every=0, seed=7, lasers=4)
    reader = RecordingReader(rec)
    gt = reader.ground_truth_poses()
    idx = sorted(gt)

    def errors():
        out: dict[str, list] = {}
        for f, cs in frame_curves(reader, reader.calibration(), idx).items():
            for name, P, _ in cs:
                out.setdefault(name, []).append(surface_distance("sphere_on_plane", G.transform_points(gt[f], P))[0])
        return {k: np.concatenate(v) for k, v in out.items()}

    far = errors()
    assert set(far) == {"vertical", "horizontal", "vertical_2", "horizontal_2"}
    orig = pipeline.default_laser_params
    monkeypatch.setattr(pipeline, "default_laser_params", lambda s: replace(orig(s), triangulate_from="left"))
    near = errors()
    for name in ("vertical", "horizontal", "horizontal_2"):
        assert np.array_equal(far[name], near[name])
    print("vertical_2 curve points, median error left / farther camera:", np.median(near["vertical_2"]), np.median(far["vertical_2"]))
    assert np.median(far["vertical_2"]) < 0.8 * np.median(near["vertical_2"])


def test_true_poses_are_left_alone(orbit, truth):
    idx, T, times = truth
    poses, rep = laser_curve_adjustment(orbit, None, rows(idx, T, times))
    assert rep["reason"] == "not_better" and rep["changed"] is False, rep["verdict"]
    # fitting the scatter: the held-out frames' crossings meet worse (the default since 2026-11-21: whole frames)
    assert rep["holdout"]["by"] == "frames" and rep["holdout"]["ratio"] > CurveParams().holdout_max_ratio
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


def test_it_says_when_it_acted(orbit, truth):
    """ENGINE_PLAN X0a: its held-out test can be fooled (thin standing walls), so when it changes the poses its report
    has a sentence for the user (``warning``), with how far it moved the camera path; when it changes nothing, None."""
    idx, T, times = truth
    _, rep = laser_curve_adjustment(orbit, None, rows(idx, add_drift(T, 0.2, 0.6, 3), times))
    assert rep["changed"] is True and isinstance(rep["warning"], str), rep
    assert rep["warning"].startswith(f"Straighten moved the camera path by up to {rep['moved_mm']:.1f} mm")
    assert "thin walls" in rep["warning"] and "GPU" not in rep["warning"] and "trajectory" not in rep["warning"]
    _, rep = laser_curve_adjustment(orbit, None, rows(idx, T, times))
    assert rep["changed"] is False and rep["warning"] is None
    _, rep = laser_curve_adjustment(orbit, None, rows(idx, T, times), params=CurveParams(enabled=False))
    assert rep["reason"] == "off" and rep["warning"] is None


def _walls(out, seed: int, n_frames: int):
    """Three thin walls (0.5, 1 and 2 mm) standing on a base, seen from both sides (the scorecard's standing walls,
    ``tools/preset_study.py`` fins_scene) at 640x400."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ohs.sim.datasets import make_scan_recording
    from tools.preset_study import fins_scene

    make_scan_recording(out, scene=fins_scene(seed), n_frames=n_frames, fps=30.0, image_size=(640, 400), realistic=True,
                        depth_every=0, seed=seed, overwrite=True)


@pytest.mark.slow
def test_the_standing_walls_true_poses_are_left_alone(tmp_path):
    """ENGINE_PLAN X0a and E-D2: on thin standing walls the crossings of one frame share that frame's errors, so half
    the crossings at random "met better" when the other half was fitted even at the TRUE poses (ratio 0.68 here), and
    the adjustment moved true poses by millimetres: a thin part thinned or thickened. The default since 2026-11-21
    holds out whole frames (``holdout="frames"``); it is not fooled, and true poses stay as they are. The old test,
    still a setting (``holdout="crossings"``), is fooled here, and then says so (``warning``)."""
    from recordings import cached_recording

    rec = cached_recording(tmp_path / "walls", _walls, seed=0, n_frames=120)
    reader = RecordingReader(rec)
    gt = reader.ground_truth_poses()
    idx = sorted(gt)
    T = np.stack([invert_se3(gt[idx[0]]) @ gt[i] for i in idx])
    poses, rep = laser_curve_adjustment(rec, None, rows(idx, T, reader.times()))  # the default: whole frames
    print("standing walls, true poses, the default (frames held out):", rep["holdout"], rep["verdict"])
    assert rep["crossings"] >= CurveParams().min_crossings  # enough crossings that the check decides
    assert rep["holdout"]["by"] == "frames" and len(rep["holdout"]["ratios"]) == 2, rep["holdout"]
    assert rep["reason"] == "not_better" and rep["changed"] is False, rep["verdict"]
    assert rep["holdout"]["ratio"] > CurveParams().holdout_max_ratio and rep["warning"] is None
    assert all(np.array_equal(poses[i], T[k]) for k, i in enumerate(idx))
    poses, rep = laser_curve_adjustment(rec, None, rows(idx, T, reader.times()), params=CurveParams(holdout="crossings"))
    print("standing walls, true poses, half the crossings held out (the old test):", rep["holdout"], rep["verdict"])
    assert rep["reason"] == "adjusted" and rep["holdout"]["ratio"] < CurveParams().holdout_max_ratio, rep["holdout"]
    assert not all(np.array_equal(poses[i], T[k]) for k, i in enumerate(idx))  # it moves the true poses ...
    assert isinstance(rep["warning"], str) and "thin walls" in rep["warning"]  # ... and says that it acted


def test_the_genuine_drift_is_mended_with_whole_frames_held_out(orbit, truth):
    """ENGINE_PLAN X0a and E-D2: with whole frames held out (the default since 2026-11-21: every second frame, each
    half once) the drift the adjustment is for still passes the test and is mended."""
    idx, T, times = truth
    D = add_drift(T, 0.2, 0.6, 3)
    assert CurveParams().holdout == "frames" and CurveParams().holdout_every == 2 and CurveParams().enabled is True
    poses, rep = laser_curve_adjustment(orbit, None, rows(idx, D, times))
    h = rep["holdout"]
    assert h["by"] == "frames" and len(h["ratios"]) == 2 and h["ratio"] == max(h["ratios"]), h
    assert h["ratio"] == pytest.approx(0.612, abs=0.002) and min(h["ratios"]) == pytest.approx(0.572, abs=0.002), h
    assert rep["reason"] == "adjusted" and h["ratio"] < 0.7, rep["verdict"]
    assert rep["ground_truth"]["after"]["rms_trans_mm"] < 0.8 * rep["ground_truth"]["before"]["rms_trans_mm"]
    assert isinstance(rep["warning"], str) and "thin walls" in rep["warning"]  # when it acts, the scan says so


def test_switched_off_changes_nothing(orbit, truth):
    idx, T, times = truth
    D = add_drift(T, 0.2, 0.6, 3)
    poses, rep = laser_curve_adjustment(orbit, None, rows(idx, D, times), params=CurveParams(enabled=False))
    assert rep["reason"] == "off" and rep["changed"] is False and "switched off" in rep["verdict"]
    assert all(np.array_equal(poses[i], D[k]) for k, i in enumerate(idx))


def test_the_crossings_split_still_works_as_a_setting(orbit, truth):
    """The test of before 2026-11-21 (half the crossings at random, ``holdout="crossings"``) is a setting since the
    owner's decision (ENGINE_PLAN E-D2), with its numbers; an unknown form is refused."""
    idx, T, times = truth
    D = add_drift(T, 0.2, 0.6, 3)
    _, rep = laser_curve_adjustment(orbit, None, rows(idx, D, times), params=CurveParams(holdout="crossings"))
    assert rep["reason"] == "adjusted" and set(rep["holdout"]) == {"before_mm", "after_mm", "ratio"}
    assert rep["holdout"]["ratio"] == pytest.approx(0.498, abs=0.002)
    _, rep = laser_curve_adjustment(orbit, None, rows(idx, T, times), params=CurveParams(holdout="crossings"))
    assert rep["reason"] == "not_better" and rep["changed"] is False  # true poses: refused here too
    with pytest.raises(ValueError):
        laser_curve_adjustment(orbit, None, rows(idx, D, times), params=CurveParams(holdout="blocks"))


def test_few_crossings_change_nothing(orbit, truth):
    idx, T, times = truth
    D = add_drift(T, 0.2, 0.6, 3)
    poses, rep = laser_curve_adjustment(orbit, None, rows(idx, D, times), params=CurveParams(max_frames=4))
    assert rep["reason"] == "few_crossings" and rep["changed"] is False and rep["frames"] == 4
    assert all(np.array_equal(poses[i], D[k]) for k, i in enumerate(idx))


def test_the_points_only_path_gives_the_fine_clouds_points(orbit):
    """The depth term reads each frame's points and nothing else: ``fine_points`` gives exactly
    the points of the full cloud's fine level, without the coarse level, the search trees and
    the cache (P5)."""
    from ohs.track.posegraph import KeyframeClouds, StraightenParams

    reader = RecordingReader(orbit)
    full = KeyframeClouds(reader, reader.calibration(), StraightenParams())
    lean = KeyframeClouds(reader, reader.calibration(), StraightenParams())
    for pos in (0, 17, 59):
        P = lean.fine_points(pos)
        assert P.dtype == np.float64 and len(P) > 5000 and np.array_equal(P, full.get(pos).fine.pts)
    assert lean._cache == {} and set(full._cache) == {0, 17, 59}
    assert np.array_equal(full.fine_points(17), full.get(17).fine.pts)  # a cloud made already is used
    # too little depth: None, as get() says
    few = KeyframeClouds(reader, reader.calibration(), StraightenParams(min_cloud_points=10 ** 7))
    assert few.fine_points(0) is None and few.get(0) is None
