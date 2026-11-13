"""``ohs.mesh.weak_areas``: the areas that need another pass, on meshes with a known sigma pattern.

* A 100 x 60 mm plate (1 mm grid, sigma 0.02) with a large patch of sigma 0.2 and a small one of
  0.08: at 0.1 mm both are found as separate "uncertain" areas, with the right size, centre,
  order and sigma numbers, and the share that passes is the exact area share; at 0.5 mm nothing
  fails.
* Areas under the minimum are not listed but count in the shares; the cap on the list keeps the
  largest.
* Filled faces (a filled hole) make a "not_measured" area; so do vertices with NaN, zero,
  negative or ``SIGMA_NONE_MM`` sigma.
* Patches that touch at one vertex only stay two areas (edge adjacency).
* No sigma at all (a mesh without confidence), a closed sphere, bad inputs.
* A 2-million-triangle mesh (about a second).
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from mesh_shapes import grid, icosphere, punch
from ohs.mesh import SIGMA_NONE_MM, fill_holes, weak_areas
from ohs.mesh.confidence import filled_area

NX, NY = 101, 61  # vertices of the plate, 1 mm apart: 100 x 60 mm, 12000 faces of 0.5 mm^2
FACE = 0.5


def _plate():
    V, F = grid(NX, NY, 1.0)
    return V, F, np.full(len(V), 0.02)


def _patch(V, x0, x1, y0, y1):
    """Vertex mask of a rectangle of the plate (inclusive)."""
    return (V[:, 0] >= x0) & (V[:, 0] <= x1) & (V[:, 1] >= y0) & (V[:, 1] <= y1)


@pytest.fixture(scope="module")
def two_patches():
    """Plate with a big patch (sigma 0.2, 20 x 20 vertices) and a small one (0.08, 5 x 5)."""
    V, F, S = _plate()
    big = _patch(V, 10, 29, 10, 29)
    small = _patch(V, 60, 64, 20, 24)
    S[big] = 0.2
    S[small] = 0.08
    return V, F, S, big, small


def test_two_patches_found_separately(two_patches):
    V, F, S, big, small = two_patches
    r = weak_areas(V, F, S, tolerance_mm=0.1)
    assert r["has_sigma"] and r["n_areas"] == 2 and len(r["areas"]) == 2
    # expected faces by counting patch vertices per face: the big patch (2 * 0.2 > 0.1) fails with one
    # vertex in it; the small one (2 * 0.08 = 0.16, but 2 * (0.08 + 2 * 0.02) / 3 = 0.08) needs two
    n_big = big[F].sum(axis=1)
    n_small = small[F].sum(axis=1)
    want_big = np.flatnonzero(n_big >= 1)
    want_small = np.flatnonzero(n_small >= 2)
    a, b = r["areas"]
    assert [a["kind"], b["kind"]] == ["uncertain", "uncertain"]
    assert a["n_faces"] == len(want_big) and b["n_faces"] == len(want_small)
    assert a["area_mm2"] == pytest.approx(len(want_big) * FACE) and b["area_mm2"] == pytest.approx(len(want_small) * FACE)
    assert a["area_mm2"] > b["area_mm2"]  # largest first
    np.testing.assert_array_equal(a["faces"], want_big)
    np.testing.assert_array_equal(b["faces"], want_small)
    assert a["center"] == pytest.approx([19.5, 19.5, 0.0], abs=0.5)
    assert b["center"] == pytest.approx([62.0, 22.0, 0.0], abs=0.5)
    assert a["extent_mm"] == pytest.approx(21.0, abs=1.0) and b["extent_mm"] == pytest.approx(6.0, abs=1.0)
    assert a["extent_mm"] / 2 <= a["radius_mm"] <= a["extent_mm"]
    assert a["sigma_worst_mm"] == pytest.approx(0.2) and a["sigma_median_mm"] == pytest.approx(0.2)
    assert b["sigma_worst_mm"] == pytest.approx(0.08) and b["sigma_median_mm"] == pytest.approx(0.08)
    total = len(F) * FACE
    assert r["area_mm2"] == pytest.approx(total)
    assert a["share"] == pytest.approx(a["area_mm2"] / total)
    bad = (len(want_big) + len(want_small)) * FACE
    assert r["share_ok"] == pytest.approx(1 - bad / total)
    assert r["share_uncertain"] == pytest.approx(bad / total) and r["share_not_measured"] == 0.0
    assert r["tolerance_mm"] == 0.1 and r["k"] == 2.0


def test_looser_tolerance_nothing_fails(two_patches):
    V, F, S, _, _ = two_patches
    r = weak_areas(V, F, S, tolerance_mm=0.5)  # 2 * 0.2 = 0.4 < 0.5 everywhere
    assert r["n_areas"] == 0 and r["areas"] == []
    assert r["share_ok"] == pytest.approx(1.0) and r["share_uncertain"] == 0.0


def test_k_scales_the_test(two_patches):
    V, F, S, _, small = two_patches
    r = weak_areas(V, F, S, tolerance_mm=0.1, k=1.0)  # only the 0.2 patch has sigma above 0.1
    assert r["n_areas"] == 1 and r["areas"][0]["sigma_worst_mm"] == pytest.approx(0.2)


def test_area_below_minimum_is_not_listed_but_counts(two_patches):
    V, F, S, _, _ = two_patches
    full = weak_areas(V, F, S)
    small_area = full["areas"][1]["area_mm2"]
    big_area = full["areas"][0]["area_mm2"]
    r = weak_areas(V, F, S, min_area_mm2=(small_area + big_area) / 2)
    assert r["n_areas"] == 1 and len(r["areas"]) == 1 and r["areas"][0]["area_mm2"] == pytest.approx(big_area)
    assert r["share_ok"] == pytest.approx(full["share_ok"])  # the small one still counts in the shares
    assert r["min_area_mm2"] == pytest.approx((small_area + big_area) / 2)


def test_default_minimum_leaves_out_a_speck():
    V, F, S = _plate()
    S[30 * NX + 50] = 0.5  # one noisy vertex: its 6 faces are 3 mm^2
    r = weak_areas(V, F, S)
    assert r["areas"] == [] and r["n_areas"] == 0
    assert r["share_ok"] == pytest.approx(1 - 6 * FACE / (len(F) * FACE))
    assert weak_areas(V, F, S, min_area_mm2=0)["n_areas"] == 1


def test_default_minimum_follows_the_face_size():
    V, F, S = _plate()
    assert weak_areas(V, F, S)["min_area_mm2"] == pytest.approx(5.0)  # 10 median faces of 0.5 mm^2
    V2, F2 = grid(21, 21, 4.0)  # coarse: 8 mm^2 faces
    assert weak_areas(V2, F2, np.full(len(V2), 0.02))["min_area_mm2"] == pytest.approx(80.0)
    V3, F3 = grid(21, 21, 0.1)  # fine: the floor of 4 mm^2
    assert weak_areas(V3, F3, np.full(len(V3), 0.02))["min_area_mm2"] == pytest.approx(4.0)


def test_filled_hole_is_a_not_measured_area():
    V, F, S = _plate()
    V, F = punch(V, F, [(50.0, 30.0, 0.0)], [6.0])
    S = np.full(len(V), 0.02)
    V2, F2 = fill_holes(V, F, 200.0)
    assert len(F2) > len(F)
    filled = np.arange(len(F2)) >= len(F)
    S2 = np.concatenate([S, np.full(len(V2) - len(V), SIGMA_NONE_MM)])
    want = filled_area(V2, F2, filled)
    # the flags alone are enough (every sigma is fine)
    r = weak_areas(V2, F2, np.full(len(V2), 0.02), filled)
    a = r["areas"][0]
    assert r["n_areas"] == 1 and a["kind"] == "not_measured"
    assert a["area_mm2"] == pytest.approx(want)
    np.testing.assert_array_equal(a["faces"], np.flatnonzero(filled))
    assert r["share_not_measured"] == pytest.approx(want / r["area_mm2"])
    assert r["share_ok"] == pytest.approx(1 - want / r["area_mm2"])
    assert a["center"] == pytest.approx([50.0, 30.0, 0.0], abs=0.3)
    assert a["extent_mm"] == pytest.approx(12.0, abs=2.5)
    # the fill's new vertices carry SIGMA_NONE: the same area, and nothing else fails
    r2 = weak_areas(V2, F2, S2, filled)
    assert r2["n_areas"] == 1 and r2["areas"][0]["kind"] == "not_measured"
    np.testing.assert_array_equal(r2["areas"][0]["faces"], np.flatnonzero(filled))


@pytest.mark.parametrize("bad", [np.nan, SIGMA_NONE_MM, 99.0, 0.0, -1.0])
def test_vertices_without_sigma_are_not_measured(bad):
    V, F, S = _plate()
    patch = _patch(V, 20, 29, 20, 29)
    S[patch] = bad
    r = weak_areas(V, F, S)
    assert r["n_areas"] == 1 and r["areas"][0]["kind"] == "not_measured"
    want = np.flatnonzero(patch[F].any(axis=1))  # every face with a vertex of the patch
    np.testing.assert_array_equal(r["areas"][0]["faces"], want)
    assert r["share_not_measured"] == pytest.approx(len(want) / len(F)) and r["share_uncertain"] == 0.0
    assert r["areas"][0]["sigma_worst_mm"] == pytest.approx(0.02)  # only the measured ring vertices count


def test_area_with_no_measured_vertex_has_no_sigma_numbers():
    V, F, S = _plate()
    S[:] = np.nan
    r = weak_areas(V, F, S)
    a = r["areas"][0]
    assert a["kind"] == "not_measured" and a["sigma_median_mm"] is None and a["sigma_worst_mm"] is None
    assert r["share_ok"] == 0.0 and a["n_faces"] == len(F)


def test_kind_follows_the_larger_part_of_an_area():
    V, F, S = _plate()
    S[_patch(V, 20, 39, 20, 39)] = 0.3  # uncertain
    S[_patch(V, 40, 41, 20, 39)] = np.nan  # a thin not measured strip joined to it
    r = weak_areas(V, F, S)
    assert r["n_areas"] == 1 and r["areas"][0]["kind"] == "uncertain"
    S[_patch(V, 20, 39, 20, 39)] = np.nan
    S[_patch(V, 40, 41, 20, 39)] = 0.3
    assert weak_areas(V, F, S)["areas"][0]["kind"] == "not_measured"


def _faces_with(F, verts):
    """Index of the face made of exactly these vertices."""
    want = sorted(verts)
    return next(i for i, f in enumerate(F.tolist()) if sorted(f) == want)


def test_patches_touching_at_a_vertex_stay_two_areas():
    V, F = grid(6, 6, 1.0)
    S = np.full(len(V), 0.02)
    v = lambda i, j: j * 6 + i  # noqa: E731
    f1 = _faces_with(F, [v(0, 0), v(1, 0), v(1, 1)])
    f2 = _faces_with(F, [v(1, 1), v(2, 2), v(1, 2)])  # shares only the vertex (1, 1) with f1
    filled = np.zeros(len(F), bool)
    filled[[f1, f2]] = True
    r = weak_areas(V, F, S, filled, min_area_mm2=0)
    assert r["n_areas"] == 2 and [a["n_faces"] for a in r["areas"]] == [1, 1]
    # a face sharing an edge with the first joins them into one two-face area, the other stays alone
    f3 = _faces_with(F, [v(0, 0), v(1, 1), v(0, 1)])
    filled[f3] = True
    r = weak_areas(V, F, S, filled, min_area_mm2=0)
    assert r["n_areas"] == 2 and [a["n_faces"] for a in r["areas"]] == [2, 1]
    np.testing.assert_array_equal(r["areas"][0]["faces"], sorted([f1, f3]))


def test_no_sigma_reports_only_filled_faces():
    V, F, _ = _plate()
    r = weak_areas(V, F, None)
    assert r["has_sigma"] is False and r["areas"] == [] and r["n_areas"] == 0
    assert r["share_ok"] is None and r["share_uncertain"] is None and r["share_not_measured"] == 0.0
    filled = _patch(V, 20, 29, 20, 29)[F].all(axis=1)  # whole cells only
    r = weak_areas(V, F, None, filled)
    assert r["has_sigma"] is False and r["share_ok"] is None
    assert r["n_areas"] == 1 and r["areas"][0]["kind"] == "not_measured"
    assert r["areas"][0]["sigma_median_mm"] is None and r["areas"][0]["sigma_worst_mm"] is None
    assert r["areas"][0]["n_faces"] == int(filled.sum())
    assert r["share_not_measured"] == pytest.approx(filled.sum() / len(F))


def test_max_areas_keeps_the_largest():
    V, F, S = _plate()
    sizes = [3, 5, 7, 9]  # square patches (vertices per side), spaced out
    for n, x in zip(sizes, (5, 25, 45, 70)):
        S[_patch(V, x, x + n - 1, 20, 20 + n - 1)] = 0.3
    full = weak_areas(V, F, S)
    assert full["n_areas"] == 4 and len(full["areas"]) == 4
    sizes_found = [a["area_mm2"] for a in full["areas"]]
    assert sizes_found == sorted(sizes_found, reverse=True)
    r = weak_areas(V, F, S, max_areas=2)
    assert r["n_areas"] == 4 and [a["area_mm2"] for a in r["areas"]] == sizes_found[:2]
    assert r["share_ok"] == pytest.approx(full["share_ok"])
    assert weak_areas(V, F, S, max_areas=0)["areas"] == []


def test_closed_sphere_cap():
    V, F = icosphere(40.0, 4)
    S = np.full(len(V), 0.02)
    S[V[:, 2] > 30.0] = 0.3
    r = weak_areas(V, F, S)
    assert r["n_areas"] == 1
    a = r["areas"][0]
    assert a["kind"] == "uncertain" and a["center"][2] > 30.0
    assert a["center"][0] == pytest.approx(0.0, abs=1.0) and a["center"][1] == pytest.approx(0.0, abs=1.0)
    assert 0.0 < a["share"] < 0.2 and r["share_ok"] == pytest.approx(1 - a["share"])
    assert a["radius_mm"] < 30.0 and a["radius_mm"] <= a["extent_mm"]


def test_empty_mesh_and_inputs_are_left_alone(two_patches):
    r = weak_areas(np.zeros((0, 3)), np.zeros((0, 3), np.int64), np.zeros(0))
    assert r["areas"] == [] and r["share_ok"] is None and r["area_mm2"] == 0.0
    V, F, S, _, _ = two_patches
    V0, F0, S0 = V.copy(), F.copy(), S.copy()
    weak_areas(V, F, S)
    assert (V == V0).all() and (F == F0).all() and (S == S0).all()


def test_bad_input_raises_plain_errors(two_patches):
    V, F, S, _, _ = two_patches
    with pytest.raises(ValueError, match="sigma values for"):
        weak_areas(V, F, S[:-1])
    with pytest.raises(ValueError, match="filled flags for"):
        weak_areas(V, F, S, np.zeros(len(F) - 1, bool))
    for tol in (0.0, -0.1, float("nan")):
        with pytest.raises(ValueError, match="tolerance"):
            weak_areas(V, F, S, tolerance_mm=tol)
    with pytest.raises(ValueError, match="k must"):
        weak_areas(V, F, S, k=0)
    with pytest.raises(ValueError, match="minimum area"):
        weak_areas(V, F, S, min_area_mm2=-1)
    with pytest.raises(ValueError, match="max_areas"):
        weak_areas(V, F, S, max_areas=-1)
    with pytest.raises(ValueError, match="face indices"):
        weak_areas(V, F + len(V), S)


def test_two_million_triangles_in_seconds():
    V, F = grid(1001, 1001, 0.1)  # 2.0 M faces
    assert len(F) == 2_000_000
    S = np.full(len(V), 0.02, np.float32)
    S[_patch(V, 10, 30, 10, 30)] = 0.3
    S[_patch(V, 60, 90, 50, 80)] = np.nan
    S[::997] = 0.3  # scattered noisy vertices: many small areas
    t0 = time.perf_counter()
    r = weak_areas(V, F, S)
    dt = time.perf_counter() - t0
    print(f"weak_areas on {len(F)} faces: {dt:.2f} s, {r['n_areas']} areas listed above {r['min_area_mm2']} mm2")
    assert r["n_areas"] >= 2 and r["areas"][0]["kind"] == "not_measured"
    assert dt < 10.0
