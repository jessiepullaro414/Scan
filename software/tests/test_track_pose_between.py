"""``ohs.track.pose_between``: a laser frame's pose placed between the dot frames beside it in time (frames in turn).

On the simulator's true hand paths (no rendering) with the dots and the laser lines in turn at 60 frames a second
(30 dot frames a second), the placed poses against the true ones, measured 2026-11-27 (fins, bracket, sphere, thin
plate; three seeds each, 4 s): straight between two, position median 0.017-0.027 mm (nine in ten within 0.026-0.041),
rotation median 0.008 deg; the cubic through four, position median 0.008-0.012 mm (nine in ten within 0.014-0.018),
rotation median 0.0045 deg. At 50 dot frames a second the straight line 0.006-0.010 mm, the cubic 0.0013-0.0019 mm.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from ohs import geometry as G
from ohs.track.pose_between import PlacingParams, place_laser_frames, pose_between

SOFTWARE = Path(__file__).resolve().parents[1]
if str(SOFTWARE) not in sys.path:
    sys.path.insert(0, str(SOFTWARE))


def _T(rv, t) -> np.ndarray:
    return G.make_se3(G.so3_exp(np.asarray(rv, dtype=float)), np.asarray(t, dtype=float))


def test_straight_is_the_line_and_the_even_turn():
    A = _T([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    B = _T([0.0, 0.0, 0.2], [10.0, -4.0, 2.0])
    M = pose_between(1.5, [1.0, 2.0], [A, B])
    assert np.allclose(M[:3, 3], [5.0, -2.0, 1.0])
    assert np.allclose(M[:3, :3], G.so3_exp([0.0, 0.0, 0.1]))
    # at a dot frame's own time it is that frame's pose; a quarter of the way: a quarter of the turn
    assert np.allclose(pose_between(1.0, [1.0, 2.0], [A, B]), A)
    assert np.allclose(pose_between(1.25, [1.0, 2.0], [A, B])[:3, :3], G.so3_exp([0.0, 0.0, 0.05]))


def test_the_cubic_goes_through_its_four_poses_and_follows_a_bend():
    ts = np.array([0.0, 1.0, 2.0, 3.0])
    path = [_T([0.0, 0.01 * t * t, 0.02 * t], [t, 0.5 * t * t, 0.1 * t ** 3]) for t in ts]
    for k in range(4):
        assert np.allclose(pose_between(ts[k], ts, path, base=1), path[k], atol=1e-9)
    # a cubic in position is followed exactly between the middle two; the straight line cuts the corner
    want = np.array([1.5, 0.5 * 1.5 ** 2, 0.1 * 1.5 ** 3])
    assert np.allclose(pose_between(1.5, ts, path, base=1)[:3, 3], want)
    assert np.linalg.norm(pose_between(1.5, ts[1:3], path[1:3])[:3, 3] - want) > 0.1


def _dots(n: int, dt: float = 1 / 30, ok=None):
    ok = [True] * n if ok is None else ok
    return [(2 * k, 100.0 + k * dt, bool(ok[k]), _T([0, 0, 0.01 * k], [k, 0, 0])) for k in range(n)]


def test_what_is_left_out_and_why():
    dots = _dots(8, ok=[True, True, True, False, True, True, True, True])
    dt = 1 / 30
    laser = [(2 * k + 1, 100.0 + (k + 0.5) * dt) for k in range(-1, 8)]  # one before the first, one after the last
    got = {p.index: p for p in place_laser_frames(laser, dots, breaks={6}, max_gap_s=1.5 * dt)}
    assert got[-1].reason == "at_the_ends" and got[15].reason == "at_the_ends"
    assert got[5].reason == "untracked" and got[7].reason == "untracked"  # dot frame 6 (position 3) was lost
    assert got[11].reason == "cut"  # a cut between dot positions 5 and 6
    assert got[1].method == "straight"  # no second dot frame before it
    assert got[3].method == "straight"  # dot position 3 (after it) is lost: no cubic over it
    assert got[13].method == "straight"  # no second dot frame after it
    assert got[9].method == "straight"  # position 3 is lost and position 6 lies across the cut
    for p in got.values():
        assert (p.T_wc is None) == (p.reason is not None)
    # a gap longer than max_gap_s: left out
    far = [(0, 100.0, True, np.eye(4)), (2, 100.2, True, np.eye(4))]
    assert place_laser_frames([(1, 100.1)], far, max_gap_s=0.1)[0].reason == "gap"
    assert place_laser_frames([(1, 100.1)], far)[0].method == "straight"


def test_the_cubic_needs_evenly_spaced_neighbours():
    T = [_T([0, 0, 0.01 * k], [k, 0, 0]) for k in range(4)]
    even = [(0, 0.0, True, T[0]), (2, 1.0, True, T[1]), (4, 2.0, True, T[2]), (6, 3.0, True, T[3])]
    assert place_laser_frames([(3, 1.5)], even)[0].method == "cubic"
    assert place_laser_frames([(3, 1.5)], even)[0].neighbours == (0, 2, 4, 6)
    uneven = [(0, -2.0, True, T[0])] + even[1:]
    assert place_laser_frames([(3, 1.5)], uneven)[0].method == "straight"  # 3 s against 1 s: more than twice
    assert place_laser_frames([(3, 1.5)], uneven, params=PlacingParams(even_ratio=3.0))[0].method == "cubic"
    assert place_laser_frames([(3, 1.5)], even, params=PlacingParams(cubic=False))[0].method == "straight"
    with pytest.raises(ValueError):
        place_laser_frames([(3, 1.5)], even[::-1])


def _true_path(scene: str, seed: int, fps: float):
    from ohs.sim.nominal import nominal_calibration  # noqa: PLC0415
    from ohs.sim.scene import make_scene  # noqa: PLC0415
    from ohs.sim.trajectory import HandheldTrajectory  # noqa: PLC0415

    if scene == "fins":
        from tools.preset_study import fins_scene  # noqa: PLC0415

        sc = fins_scene(seed)
    else:
        sc = make_scene(scene, seed)
    calib = nominal_calibration((640, 400))
    traj = HandheldTrajectory(target=sc.target, t0=100.0, duration=4.0, seed=seed, baseline=calib.stereo.baseline,
                              **sc.meta["trajectory"])
    t = 100.0 + np.arange(int(4.0 * fps)) / fps
    return t, [np.asarray(T, dtype=np.float64) for T in traj.poses(t)]


@pytest.mark.parametrize("scene", ["fins", "bracket"])
def test_placed_poses_against_the_true_ones(scene):
    """Even frames dots, odd frames lasers, at 60 frames a second, the dot frames at their true poses: the placed
    poses are within a few hundredths of a millimetre of the true ones, and the cubic is not worse than the line."""
    t, Ts = _true_path(scene, 1, 60.0)
    dots = [(k, t[k], True, Ts[k]) for k in range(0, len(t), 2)]
    laser = [(k, t[k]) for k in range(1, len(t), 2)]
    err = {}
    for cubic in (False, True):
        placed = place_laser_frames(laser, dots, params=PlacingParams(cubic=cubic), max_gap_s=2.5 / 30)
        assert sum(p.T_wc is not None for p in placed) == len(laser) - 1  # all but the last (no dot frame after it)
        if cubic:
            assert sum(p.method == "cubic" for p in placed) == len(laser) - 3  # the ends straight
        e = np.array([G.pose_error(p.T_wc, Ts[p.index]) for p in placed if p.T_wc is not None])
        err[cubic] = {"deg_med": np.median(e[:, 0]), "deg_p90": np.percentile(e[:, 0], 90),
                      "mm_med": np.median(e[:, 1]), "mm_p90": np.percentile(e[:, 1], 90), "mm_max": e[:, 1].max()}
    line, cub = err[False], err[True]
    assert line["mm_med"] < 0.04 and line["mm_p90"] < 0.06 and line["deg_p90"] < 0.02, line
    assert cub["mm_med"] < 0.02 and cub["mm_p90"] < 0.03 and cub["deg_p90"] < 0.012, cub
    for k in ("mm_med", "mm_p90", "deg_med", "deg_p90"):
        assert cub[k] <= line[k], (k, cub, line)
