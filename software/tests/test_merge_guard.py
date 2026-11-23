"""The fusion's gap guard and a merge's clip (FEATURE_PLAN X1, "the merge").

The gap guard (``ohs.track.recon_tsdf``, "The gap guard") drops depth within a few pixels of a patch
without depth, because the stereo matcher carries a surface into a patch it cannot match (a hole, a
dark background). A merge of a moved part leaves out the bare table of every frame on purpose: that
depth was measured, and is not such a patch. Measured on the clipped frame, the guard ate the rim of
everything the merge kept, and all of a small thin part (the bracket's side scan: a raised area 14 mm
across went whole). So the guard and the distance to missing depth are worked out on the frame as the
camera saw it (``ConfidenceTsdf.integrate(seen=...)``, the pipeline's ``_fuse``), and only the kept
depth is fused.

Synthetic frames: a camera 300 mm above a table, looking straight down; on it a raised island 14 mm and
one 30 mm across (what a table map keeps), the rest of the table left out by the clip; beside them a
real hole in the depth, inside a stretch of table that is kept.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

pytest.importorskip("open3d")

from ohs.track.recon_tsdf import ConfidenceTsdf, gap_guard_mask  # noqa: E402

W, H = 640, 400
F_PX = 520.0
K = np.array([[F_PX, 0.0, W / 2], [0.0, F_PX, H / 2], [0.0, 0.0, 1.0]])
Z = 300.37  # the table, mm from the camera (off the voxel grid, where a flat surface fuses cleanly)
GUARD_PX = 8.0
ISLANDS = ((200.0, 200.0, 7.0), (440.0, 200.0, 15.0))  # pixel centre, radius in mm: 14 and 30 mm across
HOLE = (60, 120, 300, 360)  # rows, columns of a real hole (no depth as the camera saw it)
KEPT = (30, 150, 270, 390)  # a stretch of table round it that the map keeps


def _frames():
    v, u = np.mgrid[0:H, 0:W].astype(np.float64)
    seen = np.full((H, W), Z, np.float32)
    island = np.zeros((H, W), bool)
    for cu, cv, r_mm in ISLANDS:
        mask = np.hypot(u - cu, v - cv) <= r_mm * F_PX / Z
        island |= mask
        seen[mask] = Z - 0.3  # a thin part 0.3 mm thick
    hole = np.zeros((H, W), bool)
    hole[HOLE[0]:HOLE[1], HOLE[2]:HOLE[3]] = True
    seen[hole] = 0.0
    keep = island.copy()
    keep[KEPT[0]:KEPT[1], KEPT[2]:KEPT[3]] = True
    clipped = np.where(keep, seen, np.float32(0.0)).astype(np.float32)
    return seen, clipped, island, hole, (u, v)


def _model():
    return ConfidenceTsdf(K, (W, H), voxel_mm=1.0, trunc_mm=4.0, gap_guard_px=GUARD_PX)


def _fuse(depth, seen=None, n=5):
    m = _model()
    for _ in range(n):
        if seen is None:
            m.integrate(depth, None, np.eye(4))
        else:
            m.integrate(depth, None, np.eye(4), seen=seen)
    P, _ = m.extract_points()
    return m, P


def _pixels(P):
    """Where the points are in the image, and whether they lie on the islands' tops (not the table)."""
    u = F_PX * P[:, 0] / P[:, 2] + W / 2
    v = F_PX * P[:, 1] / P[:, 2] + H / 2
    return u, v


def _island_cover(P):
    """Of each island, the share of its top (1 mm cells) that holds a fused point."""
    u, v = _pixels(P)
    out = []
    for cu, cv, r_mm in ISLANDS:
        r_px = r_mm * F_PX / Z
        cells = set()
        on = np.hypot(u - cu, v - cv) <= r_px
        mm = Z / F_PX
        for a, b in zip(np.floor((u[on] - cu) * mm), np.floor((v[on] - cv) * mm)):
            cells.add((int(a), int(b)))
        want = {(a, b) for a in range(-int(r_mm), int(r_mm) + 1) for b in range(-int(r_mm), int(r_mm) + 1)
                if math.hypot(a + 0.5, b + 0.5) <= r_mm - 0.5}
        out.append(len(want & cells) / len(want))
    return out


def test_an_island_the_merge_keeps_is_not_eaten_by_the_guard():
    seen, clipped, island, hole, _ = _frames()
    # the clipped frame alone: the guard takes the table left out for patches without depth
    drop = gap_guard_mask(clipped, GUARD_PX)
    assert drop[island].mean() > 0.4  # what the merge suffered: the guard's ring on the islands
    # measured on the frame as the camera saw it, the guard leaves the islands alone
    assert not gap_guard_mask(seen, GUARD_PX)[island].any()
    _, P_old = _fuse(clipped)
    _, P_new = _fuse(clipped, seen)
    old, new = _island_cover(P_old), _island_cover(P_new)
    assert old[0] < 0.2 and old[1] < 0.6, old  # today: the 14 mm island all but gone, the 30 mm one eaten round
    assert new[0] > 0.9 and new[1] > 0.9, new


def test_a_real_hole_beside_them_is_still_guarded():
    """The hole was not seen by the camera: the guard keeps the fused depth off its rim, exactly as for
    a frame nothing was clipped from."""
    seen, clipped, island, hole, (u, v) = _frames()
    _, P_new = _fuse(clipped, seen)
    _, P_full = _fuse(seen)
    pu, pv = _pixels(P_new)
    du = np.maximum(np.maximum(HOLE[2] - pu, pu - (HOLE[3] - 1)), 0)
    dv = np.maximum(np.maximum(HOLE[0] - pv, pv - (HOLE[1] - 1)), 0)
    dist = np.hypot(du, dv)  # px from the hole
    inside_kept = (pv > KEPT[0] + 2) & (pv < KEPT[1] - 2) & (pu > KEPT[2] + 2) & (pu < KEPT[3] - 2)
    assert not (inside_kept & (dist < GUARD_PX - 2.0)).any()  # the rim the guard drops
    assert (inside_kept & (dist > GUARD_PX + 3.0)).sum() > 100  # and the table beyond it is there
    # the same points round the hole as the unclipped frame gives
    def near_hole(P):
        a, b = _pixels(P)
        sel = (b > KEPT[0] + 12) & (b < KEPT[1] - 12) & (a > KEPT[2] + 12) & (a < KEPT[3] - 12)
        Q = P[sel]
        return Q[np.lexsort(Q.T[::-1])]

    assert np.array_equal(near_hole(P_new), near_hole(P_full))


def test_a_frame_nothing_was_clipped_from_is_fused_as_before():
    """``seen`` the same as the depth, or not given: bit for bit the same model, on the device the
    tests run on (the GPU, or with OHS_COMPUTE=cpu the numpy one)."""
    seen, _, _, _, _ = _frames()
    m0, _ = _fuse(seen)
    m1, _ = _fuse(seen, seen)
    a = m0.extract_mesh(with_sigma=True)
    b = m1.extract_mesh(with_sigma=True)

    def canon(V, F, N, S):
        o = np.lexsort(V.T[::-1])
        inv = np.empty_like(o)
        inv[o] = np.arange(len(o))
        Fc = np.sort(inv[F], axis=1)
        return V[o], Fc[np.lexsort(Fc.T[::-1])], N[o], S[o]

    assert len(a[0]) > 1000
    assert all(np.array_equal(x, y) for x, y in zip(canon(*a), canon(*b)))


def test_the_pipeline_fuses_a_clipped_frame_with_the_guard_on_what_was_seen(monkeypatch):
    """``FramePipeline._fuse`` hands the model the frame as seen with the clipped depth (the confidence
    model), or guards on it itself (Open3D's model); a frame without a clip is fused as before."""
    from types import SimpleNamespace

    from ohs.app.pipeline import FramePipeline

    seen, clipped, island, _, _ = _frames()
    calls = []

    class Model(ConfidenceTsdf):
        def integrate(self, depth_mm, sigma_mm, T_wc, t=None, seen=None):  # noqa: D401
            calls.append((depth_mm, seen))

    stub = SimpleNamespace(markerless=Model.__new__(Model), _guard_in_fuse=False, has_confidence=True,
                           fusion_params=SimpleNamespace(depth_min_mm=120.0, depth_max_mm=500.0), gap_guard_px=GUARD_PX,
                           fused_spool=None)  # no fused depth is kept (ENGINE_PLAN X5 step 2): as a pipeline starts
    FramePipeline._fuse(stub, clipped, np.eye(4), 0.0, {}, None, seen=seen)
    FramePipeline._fuse(stub, seen, np.eye(4), 0.0, {}, None)
    assert calls[0][0] is clipped and calls[0][1] is seen
    assert calls[1][0] is seen and calls[1][1] is None
    # Open3D's model: the pipeline guards, on what was seen
    got = []
    stub = SimpleNamespace(markerless=SimpleNamespace(integrate=lambda d, s, T, t=None: got.append(d)), _guard_in_fuse=True,
                           has_confidence=False, fusion_params=SimpleNamespace(depth_min_mm=120.0, depth_max_mm=500.0),
                           gap_guard_px=GUARD_PX, fused_spool=None)
    FramePipeline._fuse(stub, clipped, np.eye(4), 0.0, {}, None, seen=seen)
    assert (got[0][island] > 0).all()
    FramePipeline._fuse(stub, clipped, np.eye(4), 0.0, {}, None)  # without what was seen: as before, the islands eaten
    assert (got[1][island] > 0).mean() < 0.6
