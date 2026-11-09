"""``ohs.mesh``: floaters, face selections, decimation and smoothing on known shapes.

* Floaters: a sphere with small and medium pieces around it; by size, by triangle count, by
  fraction, keep-the-largest; the largest piece always stays.
* Selections: delete / keep by face index, unused vertices dropped, bad indices refused.
* Decimation (batch quadric collapses): exact counts for a target and a ratio, the sphere stays
  round, holes and an open border keep their size, a box keeps its corners, no non-manifold
  edges appear.
* Smoothing: on a noisy box and a noisy step (a bracket's edge), bilateral with ``keep_edges``
  removes the noise on the faces and keeps the edges sharp, without it the edges round off;
  Taubin smooths and, with ``keep_edges``, rounds the edges less.
"""
from __future__ import annotations

import numpy as np
import pytest

from mesh_shapes import box, box_distance, grid, icosphere, punch, step, step_distance
from ohs.mesh import decimate, delete_faces, keep_faces, mesh_stats, remove_floaters, smooth
from ohs.mesh.topology import vertex_normals


# --------------------------------------------------------------------------- floaters
@pytest.fixture(scope="module")
def with_floaters():
    V, F = icosphere(40.0, 4)  # 5120 triangles
    parts = [(V, F)]
    small, _ = icosphere(1.0, 1)  # 80 triangles, 2 mm across
    _, Fs = icosphere(1.0, 1)
    for k in range(6):
        parts.append((small + [60.0 + 5 * k, 0.0, 0.0], Fs))
    mid_V, mid_F = icosphere(6.0, 2)  # 320 triangles, 12 mm across
    parts.append((mid_V + [0.0, 70.0, 0.0], mid_F))
    Vs, Fs_all, off = [], [], 0
    for a, b in parts:
        Vs.append(a)
        Fs_all.append(b + off)
        off += len(a)
    return np.vstack(Vs), np.vstack(Fs_all)


def test_remove_floaters_by_size_count_fraction_and_largest(with_floaters):
    V, F = with_floaters
    assert mesh_stats(V, F)["components"] == 8
    V1, F1 = remove_floaters(V, F, min_size_mm=5.0)
    assert mesh_stats(V1, F1)["components"] == 2 and len(F1) == 5120 + 320
    assert len(V1) == len(np.unique(F1))  # unused vertices are gone
    _, F2 = remove_floaters(V, F, min_triangles=100)
    assert len(F2) == 5120 + 320
    _, F3 = remove_floaters(V, F, min_fraction=0.1)
    assert len(F3) == 5120
    V4, F4 = remove_floaters(V, F, keep_largest_only=True)
    assert len(F4) == 5120 and mesh_stats(V4, F4)["components"] == 1
    _, F5 = remove_floaters(V, F, min_size_mm=1000.0)  # the scan itself always stays
    assert len(F5) == 5120
    V6, F6 = remove_floaters(V, F)  # no criterion: nothing to do
    assert len(F6) == len(F) and np.array_equal(V6, V)


# --------------------------------------------------------------------------- selections
def test_delete_and_keep_faces():
    V, F = grid(11, 11, 1.0)  # 200 triangles, 121 vertices
    sel = np.arange(0, 200, 2)
    Vd, Fd = delete_faces(V, F, sel)
    assert len(Fd) == 100 and np.array_equal(Vd[Fd], V[F[1::2]])
    Vk, Fk = keep_faces(V, F, sel)
    assert len(Fk) == 100 and np.array_equal(Vk[Fk], V[F[0::2]])
    Vk1, Fk1 = keep_faces(V, F, [5, 5, 5])  # duplicates are fine
    assert len(Fk1) == 1 and len(Vk1) == 3
    Vd2, Fd2 = delete_faces(V, F, [])
    assert len(Fd2) == 200 and len(Vd2) == 121
    with pytest.raises(ValueError, match="out of range"):
        delete_faces(V, F, [200])
    with pytest.raises(ValueError, match="out of range"):
        keep_faces(V, F, [-1])


# --------------------------------------------------------------------------- decimation
def test_decimate_counts_and_shape():
    R = 60.0
    V, F = icosphere(R, 6)
    rng = np.random.default_rng(0)
    dirs = rng.normal(size=(12, 3))
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    V, F = punch(V, F, dirs * R, rng.uniform(4, 9, 12))
    before = mesh_stats(V, F)
    for kw, want in (({"target_triangles": 20_000}, 20_000), ({"ratio": 0.25}, round(0.25 * len(F)))):
        V2, F2 = decimate(V, F, **kw)
        assert abs(len(F2) - want) <= max(10, 0.002 * want), (kw, len(F2))
        st = mesh_stats(V2, F2)
        assert st["non_manifold_edges"] == 0 and st["components"] == 1 and st["holes"] == before["holes"]
        assert st["largest_hole_mm"] == pytest.approx(before["largest_hole_mm"], rel=0.02)
        assert np.abs(np.linalg.norm(V2, axis=1) - R).max() < 0.2
        assert len(V2) == len(np.unique(F2))
    same_V, same_F = decimate(V, F, target_triangles=len(F) + 10)
    assert len(same_F) == len(F)
    with pytest.raises(ValueError):
        decimate(V, F)
    with pytest.raises(ValueError):
        decimate(V, F, target_triangles=100, ratio=0.5)
    with pytest.raises(ValueError):
        decimate(V, F, ratio=1.5)


def test_decimate_keeps_corners_and_borders():
    Vb, Fb = box(20.0, 32)
    V2, F2 = decimate(Vb, Fb, 200)
    assert len(F2) <= 210 and np.abs(box_distance(V2, 10.0)).max() < 1e-6 and mesh_stats(V2, F2)["watertight"]
    for corner in np.array(np.meshgrid([-10, 10], [-10, 10], [-10, 10])).T.reshape(-1, 3):
        assert np.linalg.norm(V2 - corner, axis=1).min() < 1e-6  # all 8 corners are still there
    Vg, Fg = grid(201, 41, 0.5, z=lambda x, y: 2 * np.sin(x / 10))
    V3, F3 = decimate(Vg, Fg, 2000)
    s0, s3 = mesh_stats(Vg, Fg), mesh_stats(V3, F3)
    assert abs(len(F3) - 2000) <= 10 and s3["border_mm"] == pytest.approx(s0["border_mm"], rel=1e-3)
    assert s3["bbox_mm"] == pytest.approx(s0["bbox_mm"], abs=0.02)


# --------------------------------------------------------------------------- smoothing
@pytest.fixture(scope="module")
def noisy_box():
    V, F = box(20.0, 64)
    rng = np.random.default_rng(1)
    return V, F, V + vertex_normals(V, F) * rng.normal(0, 0.05, len(V))[:, None]


def _box_errors(V0, X):
    d = box_distance(X, 10.0)
    second = np.sort(np.abs(V0), axis=1)[:, 1]
    flat = second < 8.0  # 2 mm or more from any edge
    edge = second > 9.0  # within 1 mm of an edge
    corner = np.abs(V0).min(axis=1) > 10 - 1e-6
    rms = lambda m: float(np.sqrt(np.mean(d[m] ** 2)))  # noqa: E731
    return rms(flat), rms(edge), float(np.abs(d[corner]).max())


def test_bilateral_keeps_the_edges_of_a_box(noisy_box):
    V, F, Vn = noisy_box
    flat0, edge0, _ = _box_errors(V, Vn)
    Xk, Fk = smooth(Vn, F, "bilateral", 0.5, keep_edges=True)
    assert np.array_equal(Fk, F)
    flat, edge, corner = _box_errors(V, Xk)
    assert flat < flat0 / 3 and edge < edge0 / 2 and corner < 0.1
    Xr, _ = smooth(Vn, F, "bilateral", 0.5, keep_edges=False)
    flat_r, edge_r, corner_r = _box_errors(V, Xr)
    assert flat_r < flat0 / 3  # the faces are just as smooth ...
    assert edge_r > 2.5 * edge and corner_r > 0.12  # ... but the edges and corners are rounded
    X1, _ = smooth(Vn, F, "bilateral", 1.0, keep_edges=True)
    assert _box_errors(V, X1)[0] < flat  # stronger is smoother
    X0, _ = smooth(Vn, F, "bilateral", 0.0)
    assert np.array_equal(X0, Vn)


def test_taubin_smooths_and_keep_edges_rounds_less(noisy_box):
    V, F, Vn = noisy_box
    flat0, edge0, _ = _box_errors(V, Vn)
    Xt, _ = smooth(Vn, F, "taubin", 0.5, keep_edges=False)
    flat, edge, _ = _box_errors(V, Xt)
    assert flat < flat0 / 2.5
    Xk, _ = smooth(Vn, F, "taubin", 0.5, keep_edges=True)
    flat_k, edge_k, _ = _box_errors(V, Xk)
    assert flat_k < flat0 / 2.5 and edge_k < 0.75 * edge
    with pytest.raises(ValueError):
        smooth(Vn, F, "gaussian", 0.5)
    with pytest.raises(ValueError):
        smooth(Vn, F, "taubin", 1.5)


def test_bilateral_keeps_a_bracket_step():
    V, F = step()
    rng = np.random.default_rng(3)
    Vn = V + vertex_normals(V, F) * rng.normal(0, 0.04, len(V))[:, None]
    inner = (V[:, 1] > 2) & (V[:, 1] < 18) & (V[:, 0] > 2) & (V[:, 0] < 38)
    near = inner & (np.abs(V[:, 0] - 20) < 1.5)
    far = inner & (np.abs(V[:, 0] - 20) > 4)
    rms = lambda X, m: float(np.sqrt(np.mean(step_distance(X[m]) ** 2)))  # noqa: E731
    Xk, _ = smooth(Vn, F, "bilateral", 0.6, keep_edges=True)
    Xr, _ = smooth(Vn, F, "bilateral", 0.6, keep_edges=False)
    assert rms(Xk, far) < rms(Vn, far) / 3 and rms(Xr, far) < rms(Vn, far) / 3
    assert rms(Xk, near) < rms(Vn, near)  # the step stays sharp and gets cleaner
    assert rms(Xr, near) > 2.5 * rms(Xk, near)  # without keep_edges it becomes a ramp
    assert step_distance(Xk[near]).max() < 0.12
