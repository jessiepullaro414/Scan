"""The compiled row loop of the line linking (``ohs/vision/laser_link.py``, needs numba) gives
exactly the labels of the Python loop it stands in for (``ohs.vision.laser._link_rows``)."""
import zlib
from dataclasses import replace

import numpy as np
import pytest

pytest.importorskip("numba")

import ohs.vision.laser as laser  # noqa: E402
from ohs.vision import laser_link  # noqa: E402
from ohs.vision.laser import LaserParams, extract_laser_lines  # noqa: E402
from test_vision_laser import PAIR_SCENES, render_pairs  # noqa: E402

FIELDS = ("vertical", "horizontal", "vertical_strength", "horizontal_strength", "vertical_segment", "horizontal_segment")


def same(a: np.ndarray, b: np.ndarray) -> bool:
    return a.shape == b.shape and a.dtype == b.dtype and a.tobytes() == b.tobytes()


def args(p: LaserParams) -> tuple:
    return float(p.family_sep), int(p.max_gap) + 1, float(p.max_step), 8, laser._nms_radius(p)


def test_compiled_code_rounds_a_multiply_add_like_python():
    """A product and a sum fused into one rounding would move a prediction by its last bit."""
    a = b = 1.0 + 2.0 ** -30
    c = -(1.0 + 2.0 ** -29)
    assert a * b + c == 0.0  # two roundings; one rounding would give 2 ** -60
    assert laser_link.fused_multiply_add_probe(a, b, c) == 0.0


def candidates(rng, n_rows, n_lines, per_row):
    """Candidates of ``n_lines`` curved lines that cross, merge and have gaps, with stray ones:
    ``(t, c)`` sorted by row, then column, at most ``per_row`` per row."""
    rows = np.arange(n_rows)
    lines = []
    for _ in range(n_lines):
        c0, slope = rng.uniform(40, 360), rng.uniform(-0.9, 0.9)
        curv = rng.uniform(-1, 1) / 2000.0
        mid = rng.uniform(0.2, 0.8) * n_rows
        c = c0 + slope * (rows - mid) + curv * (rows - mid) ** 2 + rng.normal(0.0, rng.choice([0.05, 0.2, 0.6]), n_rows)
        there = rng.random(n_rows) > rng.choice([0.0, 0.03, 0.2])
        a = int(rng.integers(0, n_rows))
        there[a : a + int(rng.integers(0, 30))] = False
        lines.append((c, there))
    t_out, c_out = [], []
    for r in rows:
        cs = [c[r] for c, there in lines if there[r]]
        cs += list(rng.uniform(0, 400, int(rng.integers(0, 3)) if rng.random() < 0.3 else 0))
        if rng.random() < 0.05 and cs:  # two candidates of one ridge after re-centring
            cs.append(cs[0] + rng.uniform(0.0, 3.0))
        if rng.random() < 0.02:  # a gap in the rows
            continue
        cs = sorted(rng.permutation(cs)[:per_row])
        t_out += [r] * len(cs)
        c_out += cs
    return np.array(t_out, np.int64), np.array(c_out, np.float64)


def test_link_rows_compiled_same_as_python():
    rng = np.random.default_rng(2)
    n_points = n_dropped = n_tracks = n_many = 0
    for k in range(200):
        p = replace(LaserParams() if k % 3 else LaserParams().scaled(0.5), lines_per_family=2 + k % 2)
        t, c = candidates(rng, int(rng.integers(30, 500)), int(rng.integers(1, 6)), p.lines_per_family + k % 3)
        if k % 10 == 0:
            t, c = t[: k // 10], c[: k // 10]  # none, one, a few candidates
        a, b = laser_link.link_rows(t, c, *args(p)), laser._link_rows(t, c, *args(p))
        assert same(a, b), k
        n_points += len(a)
        n_dropped += int((a < 0).sum())
        n_tracks += len(np.unique(a[a >= 0]))
        n_many += int(len(t) > 0 and np.bincount(t).max() > 3)
    # the cases reach the merging of two lines (dropped candidates), many tracks and crowded rows
    assert n_points > 60000 and n_dropped > 1500 and n_tracks > 3000 and n_many > 20


def test_link_rows_many_pairs_in_a_row():
    """More candidate-track pairs in one row than the compiled loop's first buffer holds (64)."""
    rng = np.random.default_rng(5)
    p = replace(LaserParams(), lines_per_family=12, max_step=40.0)
    t = np.repeat(np.arange(40), 12).astype(np.int64)
    c = np.sort(rng.uniform(0.0, 300.0, (40, 12)), axis=1).ravel()
    a, b = laser_link.link_rows(t, c, *args(p)), laser._link_rows(t, c, *args(p))
    assert same(a, b) and len(np.unique(a)) > 12


def both(img, p, monkeypatch):
    """The extraction with the compiled loop and with the Python loop."""
    assert laser.compiled_link_rows() is laser_link.link_rows
    fast = extract_laser_lines(img, p)
    with monkeypatch.context() as m:
        m.setattr(laser, "_LINK_COMPILED", False)
        assert laser.compiled_link_rows() is None
        slow = extract_laser_lines(img, p)
    return fast, slow


@pytest.mark.parametrize("scale", [1.0, 0.5])
@pytest.mark.parametrize("scene", sorted(PAIR_SCENES))
def test_two_lines_per_family_compiled_same_as_python(scene, scale, monkeypatch):
    vls, hls = ([ln.scaled(scale) for ln in fam] for fam in PAIR_SCENES[scene])
    for seed in range(3):
        rng = np.random.default_rng(zlib.crc32(scene.encode()) + 7 + int(scale * 10) + 1000 * seed)
        img = render_pairs(vls, hls, rng, scale)
        base = LaserParams() if scale == 1.0 else LaserParams().scaled(scale)
        fast, slow = both(img, replace(base, lines_per_family=2), monkeypatch)
        assert len(fast.vertical) > 300 * scale and len(fast.horizontal) > 300 * scale
        for f in FIELDS:
            assert same(getattr(fast, f), getattr(slow, f)), (seed, f)


@pytest.mark.parametrize("layout", ["4", "4-cross250"])
def test_rendered_four_laser_frames_compiled_same_as_python(layout, monkeypatch):
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
                    fast, slow = both(im, p, monkeypatch)
                    n += len(fast)
                    for f in FIELDS:
                        assert same(getattr(fast, f), getattr(slow, f)), (eye, scale, f)
    assert n > 4000


def test_the_switch_and_the_fallback(monkeypatch):
    """``OHS_NUMBA=0`` (read at the first use) and a numba that cannot be imported both leave the
    Python loop in charge."""
    import builtins

    monkeypatch.setattr(laser, "_LINK_COMPILED", None)
    monkeypatch.setenv("OHS_NUMBA", "0")
    assert laser.compiled_link_rows() is None and laser._LINK_COMPILED is False
    monkeypatch.setattr(laser, "_LINK_COMPILED", None)
    monkeypatch.delenv("OHS_NUMBA")
    real_import = builtins.__import__

    def no_numba(name, *a, **k):
        if name.startswith("ohs.vision.laser_link") or name == "numba":
            raise ImportError("no numba here")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_numba)
    assert laser.compiled_link_rows() is None
    monkeypatch.setattr(builtins, "__import__", real_import)
    monkeypatch.setattr(laser, "_LINK_COMPILED", None)
    assert laser.compiled_link_rows() is laser_link.link_rows
    t = np.array([0, 0, 1, 1, 2, 2], np.int64)
    c = np.array([10.0, 50.0, 10.2, 50.1, 10.4, 50.3])
    p = replace(LaserParams(), lines_per_family=2, min_segment=2)
    assert laser._link_tracks(t, c, p).tolist() == [0, 1, 0, 1, 0, 1]
