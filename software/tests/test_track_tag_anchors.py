"""Tags as anchors in Straighten (``ohs.track.tag_anchors``, ENGINE_PLAN X8 step 2), on synthetic views (no rendering).

A three-pass sweep along a 1.3 m panel (the large-part study's path, ``tools/large_part_study.py``), every 4th frame
a keyframe; 20 tags on the table: along both long sides, 300 mm apart, and across both short ends (every pass sees
those at its turns; a tag that only one pass sees cannot hold the passes together, and the middle pass sees no side
tag). Each keyframe "sees" the tags wholly in its image at 150-450 mm, their corners triangulated with the stereo
pair's scatter (``tag_sigma_px`` of a corner's image position). The tracked poses are the true ones with a slide along
the panel added (pass 2 slides 8 mm over its length, then the rest of the scan stays shifted), as the study measured.
Judged at the point 265 mm in front of each keyframe, where it measures the surface. Checked: the slide is mended; true
poses come back unchanged; a moved tag, a misread id and a tag seen too rarely are left out and said; one tag alone
does not hold a scan; the size of a tag need not be known.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from ohs.geometry import so3_exp
from ohs.sim.trajectory import SweepTrajectory
from ohs.track import tag_anchors as ta
from ohs.track.posegraph import StraightenParams, odometry_edges, tag_odometry_params

F_PX, B_MM, W_PX, H_PX = 720.0, 60.0, 1280, 800
EVERY = 4  # frames per keyframe
FPS, DURATION = 10.0, 56.5


def _tags(side_mm: float = 25.0) -> dict[int, np.ndarray]:
    """Tag id -> world corners (4,3) on the table (z = 0): along both long sides, 300 mm apart, and across both ends."""
    places = [(x, y) for y in (-230.0, 230.0) for x in (-600.0, -300.0, 0.0, 300.0, 600.0)]
    places += [(x, y) for x in (-700.0, 700.0) for y in (-120.0, -60.0, 0.0, 60.0, 120.0)]
    out = {}
    for m, (x, y) in enumerate(places):
        R = so3_exp(np.array([0.0, 0.0, 0.3 * m]))
        out[m] = (ta.tag_square(side_mm) @ R.T) + np.array([x, y, 0.0])
    return out


def _path() -> np.ndarray:
    traj = SweepTrajectory(target=np.array([0.0, 0.0, 30.0]), t0=0.0, duration=DURATION, passes=3, length_mm=1200.0,
                           spacing_mm=130.0, distance=(240.0, 290.0), elevation_deg=(60.0, 75.0), seed=0)
    return traj.poses(np.arange(0.0, DURATION, EVERY / FPS))


def _slide(T: np.ndarray, mm: float = 8.0) -> np.ndarray:
    """Pass 2 slides ``mm`` along x over its length; the rest of the scan stays shifted (a steady wrong velocity)."""
    n = len(T)
    out = T.copy()
    for k in range(n):
        out[k, 0, 3] += mm * float(np.clip((k / n - 1 / 3) * 3.0, 0.0, 1.0))
    return out


def _views(T_true: np.ndarray, tags: dict[int, np.ndarray], sigma_px: float, seed: int = 0,
           sigma_view_px: float | None = None) -> list[ta.TagView]:
    """What each keyframe sees, with the scatter measured on the study's seed 3 (the defaults' values)."""
    sigma_view_px = StraightenParams().tag_sigma_view_px if sigma_view_px is None else sigma_view_px
    rng = np.random.default_rng(seed)
    out = []
    for k, T in enumerate(T_true):
        for m, Xw in tags.items():
            X = (Xw - T[:3, 3]) @ T[:3, :3]
            Z = X[:, 2]
            if np.any(Z < 150) or np.any(Z > 450):
                continue
            u = F_PX * X[:, 0] / Z + W_PX / 2
            v = F_PX * X[:, 1] / Z + H_PX / 2
            if np.any(u < 20) or np.any(u > W_PX - 20) or np.any(v < 20) or np.any(v > H_PX - 20):
                continue
            s_xy = Z / F_PX * sigma_px
            s_z = math.sqrt(2.0) * Z * Z / (F_PX * B_MM) * sigma_view_px
            out.append(ta.TagView(k, m, X + rng.normal(size=(4, 3)) * np.stack([s_xy, s_xy, s_z], axis=1)))
    return out


def _odometry(T: np.ndarray, p: StraightenParams) -> list:
    """The tracked motion between keyframes with the tags' step's drift model (as Straighten builds it)."""
    return [(e.k, e.X, e.info) for e in odometry_edges(T, np.full(len(T) - 1, EVERY), None, tag_odometry_params(p))]


def _err(T: np.ndarray, T_true: np.ndarray) -> np.ndarray:
    """Each keyframe's error (mm) at the point 265 mm in front of it (where it measures the surface), the scan put on
    the truth by a rigid best fit: what changes the mesh's shape and size. (A turn of the whole scan about its first
    keyframe, which the fit may leave, changes neither.)"""
    P = T[:, :3, 3] + 265.0 * T[:, :3, 2]
    Q = T_true[:, :3, 3] + 265.0 * T_true[:, :3, 2]
    cp, cq = P.mean(axis=0), Q.mean(axis=0)
    U, _, Vt = np.linalg.svd((P - cp).T @ (Q - cq))
    R = Vt.T @ np.diag([1.0, 1.0, np.sign(np.linalg.det(Vt.T @ U.T))]) @ U.T
    return np.linalg.norm((P - cp) @ R.T + cq - Q, axis=1)


def _stretch_pct(T: np.ndarray, T_true: np.ndarray) -> float:
    """How much longer the scan is along the panel than the truth (%): the slope of the along-panel error over x."""
    P = T[:, :3, 3] + 265.0 * T[:, :3, 2]
    Q = T_true[:, :3, 3] + 265.0 * T_true[:, :3, 2]
    cp, cq = P.mean(axis=0), Q.mean(axis=0)
    U, _, Vt = np.linalg.svd((P - cp).T @ (Q - cq))
    R = Vt.T @ np.diag([1.0, 1.0, np.sign(np.linalg.det(Vt.T @ U.T))]) @ U.T
    A = (P - cp) @ R.T + cq
    return 100.0 * float(np.polyfit(Q[:, 0], A[:, 0] - Q[:, 0], 1)[0])


def _fit(T_trk: np.ndarray, views: list, p: StraightenParams) -> ta.TagFit:
    return ta.anchor_keyframes(T_trk, views, _odometry(T_trk, p), p, F_PX, B_MM)


@pytest.fixture(scope="module")
def scene():
    return _path(), _tags()


def test_a_slide_along_the_panel_is_mended(scene):
    T_true, tags = scene
    p = StraightenParams(tags=True)
    T_trk = _slide(T_true)
    views = _views(T_true, tags, p.tag_sigma_px)
    assert sum(1 for k in range(len(T_true)) if len({v.tag for v in views if v.k == k}) >= 2) > 0.2 * len(T_true)
    fit = _fit(T_trk, views, p)
    assert fit.reason == "anchored", fit
    assert sorted(fit.used) == sorted(tags) and not fit.left_out and fit.dropped_views == 0
    before, after = _err(T_trk, T_true), _err(fit.T, T_true)
    assert before.max() > 3.5  # the slide, its rigid part apart (8 mm at the end of the scan)
    assert after.max() < 0.7 and np.median(after) < 0.3, (after.max(), np.median(after))
    assert abs(_stretch_pct(T_trk, T_true)) > 0.1 and abs(_stretch_pct(fit.T, T_true)) < 0.02  # the length is right
    assert all(s["ratio"] < 0.85 for s in fit.holdout["splits"])
    assert all(abs(s - 25.0) < 0.1 for s in fit.side_mm.values())  # measured by the stereo pair, not given


def test_true_poses_come_back_unchanged(scene):
    T_true, tags = scene
    p = StraightenParams(tags=True)
    for seed in (1, 4, 5):
        fit = _fit(T_true, _views(T_true, tags, p.tag_sigma_px, seed=seed), p)
        assert fit.T is None and fit.reason in ("agree", "not_better"), (seed, fit.reason, fit.holdout)


def test_a_tag_moved_during_the_scan_is_left_out(scene):
    T_true, tags = scene
    p = StraightenParams(tags=True)
    moved = []
    for v in _views(T_true, tags, p.tag_sigma_px, seed=2):  # tag 11 (an end's, every pass sees it) knocked 5 mm
        if v.tag == 11 and v.k >= len(T_true) // 3:
            v = ta.TagView(v.k, v.tag, v.X + T_true[v.k][:3, :3].T @ np.array([5.0, 0.0, 0.0]))
        moved.append(v)
    T_trk = _slide(T_true)
    fit = _fit(T_trk, moved, p)
    assert 11 in fit.left_out and "moved" in fit.left_out[11], fit.left_out
    assert fit.reason == "anchored" and _err(fit.T, T_true).max() < 0.7


def test_a_misread_id_and_a_rare_tag_are_left_out(scene):
    T_true, tags = scene
    p = StraightenParams(tags=True)
    views = _views(T_true, tags, p.tag_sigma_px, seed=3)
    w = views[40]
    wrong = ta.TagView(w.k, 3 if w.tag != 3 else 7, w.X.copy())  # this view read as another tag's id
    ks = sorted({v.k for v in views})
    rare = [ta.TagView(ks[5], 42, views[0].X + 50.0), ta.TagView(ks[9], 42, views[0].X + 50.0)]  # seen in 2 frames
    T_trk = _slide(T_true)
    fit = _fit(T_trk, views + [wrong] + rare, p)
    assert fit.left_out.get(42, "").startswith("seen in only 2")
    assert fit.dropped_views >= 1 and set(fit.left_out) == {42}
    assert fit.reason == "anchored" and _err(fit.T, T_true).max() < 0.7


def test_one_tag_alone_does_not_hold_the_scan(scene):
    T_true, tags = scene
    p = StraightenParams(tags=True)
    fit = _fit(_slide(T_true), [v for v in _views(T_true, tags, p.tag_sigma_px) if v.tag == 11], p)
    assert fit.T is None and fit.reason == "too_few"


def test_size_free_triangulation_keeps_squares_of_any_size_and_drops_skewed_ones():
    K = np.array([[F_PX, 0, 640.0], [0, F_PX, 400.0], [0, 0, 1]])

    def stereo(X: np.ndarray) -> tuple[dict, dict]:
        left = {1: np.stack([F_PX * X[:, 0] / X[:, 2] + 640, F_PX * X[:, 1] / X[:, 2] + 400], axis=1)}
        right = {1: np.stack([F_PX * (X[:, 0] - B_MM) / X[:, 2] + 640, F_PX * X[:, 1] / X[:, 2] + 400], axis=1)}
        return left, right

    for side in (15.0, 25.0, 40.0):
        X = ta.tag_square(side) + np.array([10.0, -5.0, 300.0])
        assert np.allclose(ta.triangulate_size_free(K, B_MM, *stereo(X), 0.06)[1], X, atol=1e-6)
    skew = ta.tag_square(25.0) * np.array([1.3, 1.0, 1.0]) + np.array([0.0, 0.0, 300.0])
    assert ta.triangulate_size_free(K, B_MM, *stereo(skew), 0.06) == {}
