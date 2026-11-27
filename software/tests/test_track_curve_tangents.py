"""ohs.track.curve_ba.curve_tangents: the direction of a laser curve at each sample, and which samples a crossing
may use (ENGINE_PLAN X8, 2026-11-27).

Where a laser line bends over an edge (a wall's top, a plate's rim) its samples cut the corner, and one sample off
the surface tilts its neighbours. A direction taken from the two neighbours, a third of a millimetre apart, is then
30 degrees and more off, and two nearly parallel lines of neighbouring frames pass for a crossing: their "miss" is
the distance between the lines, a millimetre or two at the true camera positions. As a setting
(``CurveParams.tangent_fit_mm``) the direction is that of a straight line fitted over a short stretch, and a sample
is used only where its curve is straight on both sides.

The setting is OFF by default, and the first test holds that: the cleaner crossings show the lasers' other error
(the projector's dots pulling a line's centre, alike in neighbouring frames), and the adjustment's held-out test
then moves thin standing walls at their true poses (``test_track_curve_ba.py``, the standing walls).

Synthetic curves only: no rendering, no graphics card.
"""
from __future__ import annotations

import numpy as np

from ohs.track.curve_ba import CurveParams, crossing_residuals, curve_tangents, find_crossings

STEP = 0.3  # mm between samples, as a laser line gives at 250 mm
OLD = CurveParams()  # the default: from the two neighbours
NEW = CurveParams(tangent_fit_mm=1.5)  # the setting: a line fitted within 1.5 mm
EYE = np.eye(4)


def _run(a, b, step: float = STEP) -> np.ndarray:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    n = int(round(np.linalg.norm(b - a) / step))
    return a + (b - a) * (np.arange(n + 1) / n)[:, None]


def _angle(t: np.ndarray, d) -> np.ndarray:
    d = np.asarray(d, dtype=float) / np.linalg.norm(d)
    return np.degrees(np.arccos(np.clip(np.abs(t @ d), 0.0, 1.0)))


def _all(P: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return np.ones(len(P), dtype=bool), np.zeros(len(P), dtype=np.int64)


def _over_an_edge(cut: float = 0.5) -> np.ndarray:
    """A line along +x on a flat top (z = 250) that goes over an edge at x = 10 and down the side (+z is away from the
    camera), its samples within ``cut`` of the edge on the chord across the corner."""
    top = _run([0.0, 0.0, 250.0], [10.0 - cut, 0.0, 250.0])
    chord = _run([10.0 - cut, 0.0, 250.0], [10.0, 0.0, 250.0 + cut], step=STEP)[1:-1]
    side = _run([10.0, 0.0, 250.0 + cut], [10.0, 0.0, 260.0])
    return np.concatenate([top, chord, side])


def test_the_fitted_line_is_a_setting_and_off_by_default():
    """Off until the held-out test can tell the dots' pull on a laser line from drift (the module's docstring): with
    it on, a walls recording at its TRUE poses is moved by millimetres."""
    assert OLD.tangent_fit_mm == 0.0
    assert NEW.tangent_side_mm == 0.75 and NEW.tangent_tol_mm == 0.1
    assert CurveParams().min_crossings == 150 and CurveParams().holdout == "frames"  # the rest as it was


def test_a_straight_curve_keeps_its_direction_and_loses_only_its_ends():
    P = _run([0.0, 0.0, 250.0], [12.0, 6.0, 251.0])
    ok, seg = _all(P)
    tan, good = curve_tangents(P, ok, seg, NEW)
    assert good.sum() >= len(P) - 8  # all but the samples within the side's reach of an end
    assert not good[:2].any() and not good[-2:].any()
    assert np.all(_angle(tan[good], P[-1] - P[0]) < 0.01)
    assert np.allclose(np.linalg.norm(tan[good], axis=1), 1.0)
    assert np.all(tan[good] @ (P[-1] - P[0]) > 0)  # along the samples' order, as before


def test_the_samples_at_a_bend_are_not_used():
    """Over an edge: with the two neighbours, the samples on the corner point 45 degrees off both legs and were used;
    now no sample within the side's reach of the bend is used, and every used one points along its own leg."""
    P = _over_an_edge()
    ok, seg = _all(P)
    told, gold = curve_tangents(P, ok, seg, OLD)
    off_both = np.minimum(_angle(told, [1, 0, 0]), _angle(told, [0, 0, 1]))
    assert (gold & (off_both > 30.0)).any()  # the old rule: a direction 30 degrees and more off both legs, used
    tan, good = curve_tangents(P, ok, seg, NEW)
    off_both = np.minimum(_angle(tan, [1, 0, 0]), _angle(tan, [0, 0, 1]))
    assert good.sum() > 40 and np.all(off_both[good] < 1.0)
    corner = np.array([10.0, 0.0, 250.0])
    assert np.linalg.norm(P[good] - corner, axis=1).min() > 0.7


def test_one_sample_off_the_surface_does_not_tilt_its_neighbours():
    P = _run([0.0, 0.0, 250.0], [15.0, 0.0, 250.0])
    k = len(P) // 2
    P[k, 2] += 0.4  # one sample 0.4 mm off, as beside a shadow's edge
    ok, seg = _all(P)
    told, gold = curve_tangents(P, ok, seg, OLD)
    assert gold[k - 1] and gold[k + 1] and _angle(told[[k - 1, k + 1]], [1, 0, 0]).min() > 30.0
    tan, good = curve_tangents(P, ok, seg, NEW)
    assert not good[k - 3:k + 4].any()  # the stretch with the sample in reach is not straight: not used
    assert good.sum() > 20 and np.all(_angle(tan[good], [1, 0, 0]) < 0.01)


def test_a_gap_ends_the_stretch():
    P = _run([0.0, 0.0, 250.0], [20.0, 0.0, 250.0])
    ok, seg = _all(P)
    ok[30] = False  # a sample that failed its checks
    tan, good = curve_tangents(P, ok, seg, NEW)
    assert not good[28:33].any() and good[20] and good[40]
    ok, seg = _all(P)
    seg[40:] = 1  # another polyline from sample 40 on
    tan, good = curve_tangents(P, ok, seg, NEW)
    assert not good[38:42].any() and good[30] and good[50]
    Q = P.copy()
    Q[40:, 2] += 1.5  # a step in depth: the next sample more than 1 mm away
    ok, seg = _all(Q)
    tan, good = curve_tangents(Q, ok, seg, NEW)
    assert not good[38:42].any() and good[30] and good[50]


def test_nearly_parallel_lines_beside_an_edge_are_no_crossing():
    """The false crossing itself: the same laser line in two neighbouring frames, 2 mm apart on a flat top, one of them
    going over the edge. With the two neighbours its corner samples pointed 45 degrees off the other line, the pair
    counted as a crossing, and it "missed" by the 2 mm between the lines at the TRUE poses. Now there is none."""
    A = _over_an_edge()
    B = _run([0.0, 2.0, 250.0], [8.8, 2.0, 250.0])  # the other frame's line, on the top, ending before the edge

    def crossings(p: CurveParams):
        curves = {}
        for f, P in ((0, A), (1, B)):
            tan, good = curve_tangents(P, *_all(P), p)
            curves[f] = [("h", P[good], tan[good])]
        T = {0: EYE, 1: EYE}
        cr = find_crossings(curves, T, p)
        return cr, crossing_residuals(T, cr)[0]

    cr, r = crossings(OLD)
    assert len(cr) == 1 and abs(abs(r[0]) - 2.0) < 0.1  # the old rule: one "crossing" that misses by the lines' distance
    cr, r = crossings(NEW)
    assert len(cr) == 0


def test_a_real_crossing_is_kept_and_its_directions_are_steadier():
    rng = np.random.default_rng(3)
    da, db = np.array([1.0, 0.0, 0.0]), np.array([0.5, np.sqrt(0.75), 0.0])  # 60 degrees apart on a flat surface
    A = _run([-8.0, 0.0, 250.0], [8.0, 0.0, 250.0]) + rng.normal(0.0, 0.03, (54, 3))
    B = _run(-8.0 * db + [0, 0, 250.0], 8.0 * db + [0, 0, 250.0]) + rng.normal(0.0, 0.03, (54, 3))
    worst = {}
    for label, p in (("old", OLD), ("new", NEW)):
        curves = {}
        for f, (P, d) in enumerate(((A, da), (B, db))):
            tan, good = curve_tangents(P, *_all(P), p)
            curves[f] = [("h", P[good], tan[good])]
            worst[label, f] = float(_angle(tan[good], d).max())
        T = {0: EYE, 1: EYE}
        cr = find_crossings(curves, T, p)
        r = crossing_residuals(T, cr)[0]
        assert len(cr) == 1 and abs(r[0]) < 0.1, (label, len(cr), r)
    assert max(worst["new", 0], worst["new", 1]) < 2.5  # a line through a dozen samples
    assert min(worst["old", 0], worst["old", 1]) > 2 * max(worst["new", 0], worst["new", 1])  # two samples 0.6 mm apart


def test_samples_farther_apart_get_a_longer_stretch():
    """A smaller image or a far, slanted surface: the samples are farther apart and less exact in proportion, so the
    three lengths grow with them and the fit keeps its samples (0.8 mm apart: twice the lengths)."""
    P = _run([0.0, 0.0, 400.0], [40.0, 0.0, 400.0], step=0.8)
    tan, good = curve_tangents(P, *_all(P), NEW)
    assert good.sum() >= len(P) - 6 and not good[:2].any() and not good[-2:].any()  # 1.5 mm from an end, not 0.75
    assert np.all(_angle(tan[good], [1, 0, 0]) < 0.01)
    P[25, 2] += 0.15  # within the grown tolerance (0.2 mm): the stretch still counts as straight
    tan, good = curve_tangents(P, *_all(P), NEW)
    assert good[22:29].all() and np.all(_angle(tan[good], [1, 0, 0]) < 3.0)
    P[25, 2] += 0.35  # 0.5 mm off: not straight
    tan, good = curve_tangents(P, *_all(P), NEW)
    assert not good[23:28].any()


def test_the_default_is_the_two_neighbours():
    P = _over_an_edge()
    ok, seg = _all(P)
    ok[5] = False
    tan, good = curve_tangents(P, ok, seg, OLD)
    want = np.zeros(len(P), dtype=bool)
    want[1:-1] = ok[1:-1] & ok[2:] & ok[:-2]
    assert np.array_equal(good, want)
    d = P[2:] - P[:-2]
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    assert np.allclose(tan[1:-1][want[1:-1]], d[want[1:-1]])


def test_too_short_a_curve_gives_nothing():
    P = _run([0.0, 0.0, 250.0], [0.9, 0.0, 250.0])  # four samples
    tan, good = curve_tangents(P, *_all(P), NEW)
    assert not good.any()
    tan, good = curve_tangents(P[:2], np.ones(2, bool), np.zeros(2, np.int64), NEW)
    assert tan.shape == (2, 3) and not good.any()
