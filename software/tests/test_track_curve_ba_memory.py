"""``ohs.track.curve_ba.find_crossings`` in bounded memory (FEATURE_PLAN R5, "Projects larger than memory").

Every pair of laser points within ``radius_mm`` is a crossing candidate. With the scanner held nearly
still the lines of all frames lie on top of each other and the candidates run into the tens of
millions (84M on a simulated still 200-frame scan at 1280x800). Filtering them all at once took
about 150 bytes a candidate (``VALIDATION.md``, "Projects larger than memory"); they are now
filtered in pieces, in order. The guards:

* the crossings are the same as before, every array bit for bit and in the same order: on the
  laser curves of a rendered orbit (the recording of ``test_track_curve_ba``) and on a synthetic
  scan held still, against :func:`_find_crossings_before`, the function as it was;
* on the still scan (about 4M candidates, several pieces), the numpy memory the call allocates at
  its peak (``tracemalloc``: the same on a busy PC as on a quiet one) stays below 24 bytes a
  candidate plus 96 MB (201 MB at its 4.39M candidates). Before: 608 MB (139 bytes a candidate); now
  87 MB (20 bytes, most of it the candidate pairs themselves, 16 bytes each).
"""
from __future__ import annotations

import math
import tracemalloc

import numpy as np
import pytest

from ohs.geometry import invert_se3, so3_exp
from ohs.io.recording import RecordingReader
from ohs.sim.nominal import NOMINAL_TIME_OFFSET, nominal_calibration
from ohs.sim.scene import make_scene
from ohs.sim.trajectory import HandheldTrajectory
from ohs.track.curve_ba import CurveParams, Crossings, find_crossings, frame_curves
from recordings import scan_recording

FIELDS = ("fa", "xa", "ta", "fb", "xb", "tb")


def _find_crossings_before(curves, T, params=None) -> Crossings:
    """``find_crossings`` as it was before 2026-11-20 (every candidate filtered at once): the reference."""
    from scipy.spatial import cKDTree

    p = params or CurveParams()
    Xw, Tw, Xc, Tc, Fr, Cid = [], [], [], [], [], []
    cid = 0
    for f, cs in curves.items():
        if f not in T:
            continue
        R, t = T[f][:3, :3], T[f][:3, 3]
        for _, P, tan in cs:
            Xw.append(P @ R.T + t)
            Tw.append(tan @ R.T)
            Xc.append(P)
            Tc.append(tan)
            Fr.append(np.full(len(P), f))
            Cid.append(np.full(len(P), cid))
            cid += 1
    empty = Crossings(np.zeros(0, dtype=np.int64), np.zeros((0, 3)), np.zeros((0, 3)), np.zeros(0, dtype=np.int64),
                      np.zeros((0, 3)), np.zeros((0, 3)))
    if cid < 2:
        return empty
    Xw_, Tw_, Xc_, Tc_ = (np.concatenate(a) for a in (Xw, Tw, Xc, Tc))
    Fr_, Cid_ = np.concatenate(Fr), np.concatenate(Cid)
    pairs = cKDTree(Xw_).query_pairs(float(p.radius_mm), output_type="ndarray")
    if len(pairs) == 0:
        return empty
    i, j = pairs[:, 0], pairs[:, 1]
    keep = Fr_[i] != Fr_[j]
    i, j = i[keep], j[keep]
    keep = np.linalg.norm(np.cross(Tw_[i], Tw_[j]), axis=1) > math.sin(math.radians(p.min_angle_deg))
    i, j = i[keep], j[keep]
    a, b, ta, tb = Xw_[i], Xw_[j], Tw_[i], Tw_[j]
    w0 = a - b
    A_ = np.einsum("ij,ij->i", ta, ta)
    B_ = np.einsum("ij,ij->i", ta, tb)
    C_ = np.einsum("ij,ij->i", tb, tb)
    D_ = np.einsum("ij,ij->i", ta, w0)
    E_ = np.einsum("ij,ij->i", tb, w0)
    den = np.maximum(A_ * C_ - B_ * B_, 1e-12)
    sa = (B_ * E_ - C_ * D_) / den
    sb = (A_ * E_ - B_ * D_) / den
    dist = np.linalg.norm((a + sa[:, None] * ta) - (b + sb[:, None] * tb), axis=1)
    local = (np.abs(sa) < p.local_mm) & (np.abs(sb) < p.local_mm)
    i, j, sa, sb, dist = i[local], j[local], sa[local], sb[local], dist[local]
    if len(i) == 0:
        return empty
    lo, hi = np.minimum(Cid_[i], Cid_[j]), np.maximum(Cid_[i], Cid_[j])
    key = lo.astype(np.int64) * (cid + 1) + hi
    order = np.lexsort((dist, key))
    first = np.ones(len(order), dtype=bool)
    first[1:] = key[order][1:] != key[order][:-1]
    sel = order[first]
    i, j, sa, sb = i[sel], j[sel], sa[sel], sb[sel]
    return Crossings(Fr_[i].astype(np.int64), Xc_[i] + sa[:, None] * Tc_[i], Tc_[i], Fr_[j].astype(np.int64),
                     Xc_[j] + sb[:, None] * Tc_[j], Tc_[j])


def assert_same(new: Crossings, old: Crossings) -> None:
    for k in FIELDS:
        a, b = getattr(new, k), getattr(old, k)
        assert a.dtype == b.dtype and a.shape == b.shape, k
        assert a.tobytes() == b.tobytes(), f"{k} differs"


def still_scan(n_frames: int, step_mm: float = 0.7, length_mm: float = 300.0, seed: int = 0):
    """Two laser lines a frame on a table 250 mm away, the scanner held nearly still (2 mm, 0.6 deg)."""
    rng = np.random.default_rng(seed)
    s = np.arange(-length_mm / 2, length_mm / 2, step_mm)
    curves, T = {}, {}
    for f in range(n_frames):
        Tf = np.eye(4)
        Tf[:3, :3] = so3_exp(rng.normal(0, 0.01, 3))
        Tf[:3, 3] = rng.normal(0, 2.0, 3)
        T[f] = Tf
        curves[f] = [(name, s[:, None] * d[None] + np.array([0.0, 0.0, 250.0]) + rng.normal(0, 0.05, (len(s), 3)),
                      np.tile(d, (len(s), 1)))
                     for name, d in (("horizontal", np.array([1.0, 0.0, 0.0])), ("vertical", np.array([0.0, 1.0, 0.0])))]
    return curves, T


def candidates(curves, T, radius: float) -> int:
    from scipy.spatial import cKDTree

    X = np.concatenate([P @ T[f][:3, :3].T + T[f][:3, 3] for f, cs in curves.items() for _, P, _ in cs])
    tree = cKDTree(X)
    return (int(tree.count_neighbors(tree, radius)) - len(X)) // 2


def numpy_peak(fn, *args):
    """``(result, peak bytes)`` of what the call allocates (numpy reports to tracemalloc)."""
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        base = tracemalloc.get_traced_memory()[0]
        out = fn(*args)
        return out, tracemalloc.get_traced_memory()[1] - base
    finally:
        tracemalloc.stop()


@pytest.fixture(scope="module")
def still():
    curves, T = still_scan(80)
    return curves, T, candidates(curves, T, CurveParams().radius_mm)


def test_the_still_scan_has_many_candidates(still):
    _, _, n = still
    assert n > 3_500_000  # several pieces


def test_same_crossings_as_before_on_a_still_scan(still):
    curves, T, _ = still
    new, old = find_crossings(curves, T), _find_crossings_before(curves, T)
    assert len(new) > 1000
    assert_same(new, old)
    for p in (CurveParams(radius_mm=1.0), CurveParams(min_angle_deg=60.0, local_mm=0.5)):
        assert_same(find_crossings(curves, T, p), _find_crossings_before(curves, T, p))


def test_same_crossings_as_before_on_a_rendered_orbit(tmp_path_factory):
    calib = nominal_calibration((640, 400), realistic=True)
    sc = make_scene("sphere_on_plane", 7)
    traj = HandheldTrajectory(target=sc.target, t0=100.0 + NOMINAL_TIME_OFFSET, duration=12.0, seed=7,
                              baseline=calib.stereo.baseline, distance=(220.0, 300.0), elevation_deg=(35.0, 65.0),
                              sweep_deg_s=400.0 / 12.0)  # the recording of test_track_curve_ba (the cache's)
    rec = scan_recording(tmp_path_factory.mktemp("cbam") / "rec", scene="sphere_on_plane", n_frames=60, fps=5.0,
                         image_size=(640, 400), realistic=True, trajectory=traj, depth_every=0, seed=7)
    reader = RecordingReader(rec)
    gt = reader.ground_truth_poses()
    idx = sorted(gt)
    curves = frame_curves(reader, reader.calibration(), idx)
    Tt = {f: invert_se3(gt[idx[0]]) @ gt[f] for f in idx}
    new = find_crossings(curves, Tt)
    assert len(new) > 800
    assert_same(new, _find_crossings_before(curves, Tt))
    # nothing at all, and curves of one frame only: the empty answer as before
    assert_same(find_crossings({}, Tt), _find_crossings_before({}, Tt))
    one = {idx[0]: curves[idx[0]]}
    assert_same(find_crossings(one, Tt), _find_crossings_before(one, Tt))


def test_memory_is_bounded_by_the_candidates(still):
    curves, T, n = still
    new, peak = numpy_peak(find_crossings, curves, T)
    bound = 24 * n + 96e6
    print(f"{n:,} candidates: find_crossings peaks at {peak / 1e6:.0f} MB ({peak / n:.0f} bytes a candidate); "
          f"bound {bound / 1e6:.0f} MB")
    assert peak < bound
    assert len(new) > 1000
