"""Short GPU launches in "Refine from the images" (``ohs.track.spacetime._GpuCost``, 2026-11-22): the texture test and
the cost run in batches of patches sized by their work, each launch waited for, so that none runs long (a launch of
3 s timed the Radeon driver out, and the screen and the PC with it); and a self-check that makes a driver reset loud.

* the batches cover every item once, in order; the first is :data:`BATCH_FIRST_WORK`, later ones what the pace
  measured does in :data:`BATCH_TARGET_S`, none more than the cap (one item at least), whatever the pace says;
* on the GPU, batched results equal one launch bit for bit, for two forced batch sizes, and no launch is over a
  forced tiny cap;
* the self-check (the first cost launch run again) passes on a sane run and raises :class:`GpuResetError`, in plain
  words, when the second run differs.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from ohs.compute import get_device
from ohs.track import spacetime as ST
from ohs.track.spacetime import BATCH_FIRST_WORK, BATCH_MAX_WORK, BATCH_TARGET_S, GpuResetError, Patches

gpu = pytest.mark.skipif(get_device() is None, reason="no OpenCL GPU")


def batches(work, *, rate=None, forced=None, cap=None, kernel="cost"):
    eng = SimpleNamespace(batch_work=forced, max_work=cap, _rate={} if rate is None else {kernel: rate})
    return list(ST._GpuCost._batches(eng, np.asarray(work, dtype=float), kernel))


def test_batches_cover_everything_once_and_stay_under_the_cap():
    w = np.full(10_000, 1e5)
    for kw in ({}, {"rate": 1e9}, {"rate": 1e14}, {"forced": 3e6}, {"forced": 1.0}, {"cap": 2.5e5}):
        b = batches(w, **kw)
        assert b[0][0] == 0 and b[-1][1] == len(w) and all(x[1] == y[0] for x, y in zip(b, b[1:]))
        cap = min(kw.get("cap") or BATCH_MAX_WORK["cost"], kw.get("forced") or np.inf)
        assert all(w[a:z].sum() <= max(cap, w[a]) for a, z in b)
    for k in ("cost", "texture"):  # each kernel its own first size, pace and cap
        assert batches(w, kernel=k)[0] == (0, int(BATCH_FIRST_WORK[k] // 1e5))
        assert batches(w, rate=1e8, kernel=k)[0] == (0, int(min(1e8 * BATCH_TARGET_S, BATCH_MAX_WORK[k]) // 1e5))
        assert batches(w, rate=1e14, kernel=k)[0] == (0, int(BATCH_MAX_WORK[k] // 1e5))  # a fast pace: the cap
    assert BATCH_MAX_WORK["texture"] < BATCH_MAX_WORK["cost"]  # the texture test is slower a sample
    big = np.array([5e8, 1.0, 1.0])  # one item over the cap is a launch of its own
    assert batches(big) == [(0, 1), (1, 3)]


def scene(seed=0, n_frames=6, size=(320, 240)):
    """Patches on a plane 300 mm in front of cameras looking at it, random dot images: cost inputs, not a scan."""
    rng = np.random.default_rng(seed)
    xs, ys = np.meshgrid(np.linspace(-40, 40, 23), np.linspace(-30, 30, 17))
    P = np.stack([xs.ravel(), ys.ravel(), np.full(xs.size, 300.0)], axis=1)
    geo = np.zeros((len(P), 18))
    geo[:, 0:3], geo[:, 3:6], geo[:, 6:9], geo[:, 9:12] = P, [1, 0, 0], [0, 1, 0], [0, 0, -1]
    geo[:, 12:18] = rng.normal(0.0, 0.01, (len(P), 6))
    pa = Patches(np.arange(len(P)), geo)
    K = np.array([[300.0, 0, size[0] / 2], [0, 300.0, size[1] / 2], [0, 0, 1]])
    T = np.repeat(np.eye(4)[None], n_frames, axis=0)
    T[:, 0, 3] = np.linspace(-10, 10, n_frames)
    left = [rng.integers(0, 255, size[::-1], dtype=np.uint8) for _ in range(n_frames)]
    right = [np.roll(x, -60, axis=1) for x in left]  # 60 px disparity at 300 mm (f B / z = 300 * 60 / 300)
    vis = rng.random((len(P), n_frames)) < 0.8
    p = ST.SpacetimeParams(half_mm=2.0, samples=9, spacing_mm=1.5)
    deltas = np.tile(np.linspace(-0.4, 0.4, 9), (len(P), 1))
    return pa, left, right, T, K, 60.0, vis, p, deltas


def run(forced=None, cap=None):
    pa, left, right, T, K, B, vis, p, deltas = scene()
    eng = ST._Cost(pa, left, right, T, K, B, p, "gpu")
    g = eng.gpu
    g.batch_work, g.max_work = forced, (np.inf if forced == np.inf else cap)
    eng.use(np.arange(len(pa)), vis)
    tex = eng.texture()
    eng.use(np.arange(len(pa)), vis & tex)
    C, W = eng.cost(deltas)
    launches = list(g.launches)
    eng.check()
    eng.close()
    return tex, C, W, launches


@gpu
def test_batched_equals_one_launch_bit_for_bit():
    tex0, C0, W0, L0 = run(forced=np.inf)  # one launch each
    assert [x["kernel"] for x in L0] == ["texture", "cost"]
    assert tex0.sum() > 100 and np.isfinite(C0).all() and W0.max() > 0
    for forced in (2e4, 3.3e5):
        tex, C, W, L = run(forced=forced)
        assert len(L) > 4
        assert np.array_equal(tex, tex0) and np.array_equal(C, C0) and np.array_equal(W, W0)


@gpu
def test_no_launch_is_over_the_cap():
    cap = 5e4
    _, _, _, L = run(cap=cap)
    assert len(L) > 10 and {x["kernel"] for x in L} == {"texture", "cost"}
    assert max(x["work"] for x in L) <= cap  # every patch's work is far under it


@gpu
def test_the_self_check_raises_when_the_first_launch_changes(monkeypatch):
    run()  # a sane run passes its check
    real = ST._GpuCost.rerun_first

    def changed(self):
        C, W = real(self)
        C = C.copy()
        C[0] += 1e-3  # what a reset part way does: another answer
        return C, W

    monkeypatch.setattr(ST._GpuCost, "rerun_first", changed)
    with pytest.raises(GpuResetError, match="graphics driver seems to have been reset.*mesh was left unchanged"):
        run()
