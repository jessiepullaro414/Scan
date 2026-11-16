"""The faster helpers of the line extraction (several lines per family) give exactly the output
of the ones they replace, which are kept in ``laser_reference.py``."""
import zlib
from dataclasses import replace

import numpy as np
import pytest

import laser_reference as ref
import ohs.vision.laser as laser
from ohs.vision.laser import LaserParams, extract_laser_lines
from test_vision_laser import PAIR_SCENES, render_pairs

FIELDS = ("vertical", "horizontal", "vertical_strength", "horizontal_strength", "vertical_segment", "horizontal_segment")
HELPERS = ("_row_candidates", "_near_other_lines")


def same(a: np.ndarray, b: np.ndarray) -> bool:
    return a.shape == b.shape and a.dtype == b.dtype and a.tobytes() == b.tobytes()


def test_the_reference_is_not_the_code_under_test():
    for name in HELPERS:
        assert getattr(laser, name).__module__ == "ohs.vision.laser" and getattr(ref, name).__module__ == "laser_reference"


# --------------------------------------------------------------------------- ridge candidates of a row
def _responses(rng):
    """Ridge-response-like images: smooth noise with ridges, flat tops and equal values."""
    for k in range(40):
        h, w = int(rng.integers(3, 70)), int(rng.integers(8, 160))
        kind = k % 4
        if kind == 0:  # plain noise
            r = rng.normal(0.0, 10.0, (h, w))
        elif kind == 1:  # whole numbers: many equal neighbours
            r = rng.integers(0, 6, (h, w)).astype(float) * 10.0
        elif kind == 2:  # ridges of different widths on noise, some saturated (flat tops)
            x = np.arange(w)[None, :]
            r = rng.normal(0.0, 3.0, (h, w))
            for _ in range(int(rng.integers(1, 5))):
                c0 = rng.uniform(0, w) + rng.uniform(-0.5, 0.5) * np.arange(h)[:, None]
                r += rng.uniform(20, 200) * np.exp(-0.5 * ((x - c0) / rng.uniform(0.7, 4.0)) ** 2)
            r = np.minimum(r, rng.uniform(60, 250))
        else:  # mostly under the threshold
            r = rng.normal(-50.0, 5.0, (h, w))
            r[rng.integers(0, h, 12), rng.integers(0, w, 12)] = rng.uniform(15.0, 90.0, 12)
        yield np.ascontiguousarray(r.astype(np.float32))


def test_row_candidates_same_as_before():
    rng = np.random.default_rng(1)
    n_cases = n_found = 0
    for resp in _responses(rng):
        views = [resp, resp[:, 2:-2], resp[:, ::-1]]  # the extraction passes a slice, not a whole array
        for view in views:
            for thr in (12.0, 24.3, float(np.percentile(resp, 60)), float(resp.max()), 1e9):
                for n, radius in ((1, 2), (2, 2), (2, 5), (3, 3), (2, 40)):
                    a = laser._row_candidates(view, thr, n, radius)
                    b = ref._row_candidates(view, thr, n, radius)
                    assert same(a[0], b[0]) and same(a[1], b[1]), (view.shape, thr, n, radius)
                    n_cases += 1
                    n_found += len(a[0])
    assert n_cases == 40 * 3 * 5 * 5 and n_found > 20000


# --------------------------------------------------------------------------- near the other family's lines
def _polyline(rng, n, gap_chance):
    t = np.sort(rng.choice(np.arange(0, 3 * n), n, replace=False)).astype(float)
    c = rng.uniform(50, 350) + rng.uniform(-1, 1) * (t - t.mean()) + rng.normal(0.0, 0.3, n)
    slope = np.gradient(c, t)
    seg = np.cumsum(rng.random(n) < gap_chance)
    return t, c, slope, seg


def test_near_other_lines_same_as_before():
    rng = np.random.default_rng(3)
    n_near = n_far = 0
    for k in range(150):
        vertical = bool(k % 2)
        # the other family: one to three lines, each split into segments (contiguous runs sorted by t)
        ot, oc, osl, oseg = [], [], [], []
        for line in range(int(rng.integers(1, 4))):
            t, c, s, seg = _polyline(rng, int(rng.integers(2, 150)), rng.choice([0.0, 0.02, 0.2]))
            ot.append(t), oc.append(c), osl.append(s), oseg.append(seg + (oseg[-1][-1] + 1 if oseg else 0))
        ot, oc, osl, oseg = (np.concatenate(x) for x in (ot, oc, osl, oseg))
        other = np.stack([oc, ot], 1) if vertical else np.stack([ot, oc], 1)
        od = laser._unit(osl, np.ones_like(osl)) if vertical else laser._unit(np.ones_like(osl), osl)
        # the points: a line crossing them, points scattered around, and points on the other lines
        m = int(rng.integers(1, 300))
        pts = np.stack([rng.uniform(-20, 470, m), rng.uniform(-20, 470, m)], 1)
        pts[: m // 3] = other[rng.integers(0, len(other), m // 3)] + rng.normal(0.0, 8.0, (m // 3, 2))
        ang = rng.uniform(0, np.pi, m)
        dirs = np.stack([np.cos(ang), np.sin(ang)], 1)
        for radius in (3.0, 7.5, 15.0, 40.0):
            a = laser._near_other_lines(pts, dirs, other, od, osl, oseg, vertical, radius)
            b = ref._near_other_lines(pts, dirs, other, od, osl, oseg, vertical, radius)
            assert same(a, b), (k, radius)
            n_near += int(a.sum())
            n_far += int((~a).sum())
    assert n_near > 5000 and n_far > 5000
    empty = np.zeros((0, 2))
    assert len(laser._near_other_lines(empty, empty, other, od, osl, oseg, True, 15.0)) == 0
    assert not laser._near_other_lines(pts, dirs, empty, empty, np.zeros(0), np.zeros(0, int), True, 15.0).any()


# --------------------------------------------------------------------------- the whole extraction
def _extract_both(img, p, monkeypatch):
    new = extract_laser_lines(img, p)
    with monkeypatch.context() as m:
        for name in HELPERS:
            m.setattr(laser, name, getattr(ref, name))
        old = extract_laser_lines(img, p)
    return new, old


@pytest.mark.parametrize("scale", [1.0, 0.5])
@pytest.mark.parametrize("scene", sorted(PAIR_SCENES))
def test_two_lines_per_family_same_as_before(scene, scale, monkeypatch):
    vls, hls = ([ln.scaled(scale) for ln in fam] for fam in PAIR_SCENES[scene])
    for seed in range(3):
        rng = np.random.default_rng(zlib.crc32(scene.encode()) + 7 + int(scale * 10) + 1000 * seed)
        img = render_pairs(vls, hls, rng, scale)
        base = LaserParams() if scale == 1.0 else LaserParams().scaled(scale)
        new, old = _extract_both(img, replace(base, lines_per_family=2), monkeypatch)
        assert len(new.vertical) > 300 * scale and len(new.horizontal) > 300 * scale
        for f in FIELDS:
            assert same(getattr(new, f), getattr(old, f)), (seed, f)


@pytest.mark.parametrize("layout", ["4", "4-cross250"])
def test_rendered_four_laser_frames_same_as_before(layout, monkeypatch):
    """Simulator frames (the build's aim, and the aim where each family's lines meet in view), both
    cameras, at full and half size."""
    import cv2

    from ohs.sim.nominal import nominal_calibration
    from ohs.sim.render import Renderer, RenderParams
    from ohs.sim.scene import sphere_on_plane
    from ohs.sim.trajectory import look_at

    cal = nominal_calibration(layout=layout)
    n = 0
    with Renderer(cal, None, sphere_on_plane(tags=False), RenderParams(seed=3)) as r:
        for eye in ([40.0, -150.0, 220.0], [-60.0, -120.0, 260.0]):
            T = look_at(eye, [0.0, 0.0, 30.0], up=(0.0, 0.0, 1.0))
            left, right, _ = r.render(T, rectified=True)
            for img in (left, right):
                for scale in (1.0, 0.5):
                    im = img if scale == 1.0 else cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                    p = replace(LaserParams() if scale == 1.0 else LaserParams().scaled(scale), lines_per_family=2)
                    new, old = _extract_both(im, p, monkeypatch)
                    n += len(new)
                    for f in FIELDS:
                        assert same(getattr(new, f), getattr(old, f)), (eye, scale, f)
    assert n > 4000


# --------------------------------------------------------------------------- the four-laser point assignment
def test_nanmedian_rows_same_as_numpy():
    """``_nanmedian_rows`` (the dense depth's samples around each laser point) gives numpy's
    ``nanmedian(axis=0)`` bit for bit: ties, missing samples, one row, many columns."""
    import warnings

    rng = np.random.default_rng(5)
    n_cols = 0
    for t in range(1500):
        r, n = int(rng.integers(1, 9)), int(rng.integers(1, 900))
        x = rng.normal(300.0, 50.0, (r, n))
        if t % 3 == 0:
            x = np.round(x)  # equal numbers
        x[rng.random((r, n)) < rng.uniform(0.0, 0.8)] = np.nan
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # numpy warns about all-NaN columns
            want = np.nanmedian(x, axis=0)
        got = laser._nanmedian_rows(x)
        assert got.dtype == want.dtype and got.tobytes() == want.tobytes(), t
        n_cols += n
    assert n_cols > 500_000


def test_stereo_residual_with_a_tree_already_built():
    from scipy.spatial import cKDTree

    rng = np.random.default_rng(6)
    K = np.array([[900.0, 0.0, 640.0], [0.0, 900.0, 400.0], [0.0, 0.0, 1.0]])
    P = np.column_stack([rng.uniform(-80, 80, 500), rng.uniform(-50, 50, 500), rng.uniform(150, 450, 500)])
    P[::17] = np.nan
    right = rng.uniform([0, 0], [1280, 800], (20000, 2))
    a = laser.stereo_residual(P, right, K, 60.0, 3.0)
    b = laser.stereo_residual(P, right, K, 60.0, 3.0, cKDTree(right))
    assert a.tobytes() == b.tobytes() and np.isfinite(a).sum() > 50
