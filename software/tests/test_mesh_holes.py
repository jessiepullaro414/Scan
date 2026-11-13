"""``ohs.mesh``: statistics, hole outlines and the smooth hole fill on known shapes.

* A sphere (radius 40 mm, ~1.9 mm edges) with five punched holes: all five are holes (a nearly
  closed piece has no outer border); filling up to a size fills exactly the smaller ones; filling
  all of them makes it watertight again with the volume back within 0.1 %, and the new vertices
  lie within 0.1 mm of the sphere, where a flat cap would sit up to 2.5 mm inside it.
* A hole with more edges than the triangulation's dynamic programme takes (ear clipping, then the
  same refinement and fairing) on a sphere: within 0.8 mm of it (0.2 mm on average), where a flat
  fill is 8.8 mm off.
* A flat strip with two holes: its outer border is never filled, the holes are, and the fill is
  flat.
* Box and sphere statistics (volume, area, size), bowtie loops, an empty mesh.
* A lone triangle that hangs on the mesh by one vertex is not a hole: its outline gets no fill
  (which would be the same triangle again, back to back).
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from mesh_shapes import box, grid, icosphere, punch
from ohs.mesh import fill_holes, hole_outlines, mesh_stats
from ohs.mesh.holes import FAIR_BOUND, MAX_DP_EDGES
from ohs.mesh.topology import boundary_loops, edge_table

R = 40.0
DIRS = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [-1, 0, 0], [0, -1, 0.3]], float)
DIRS /= np.linalg.norm(DIRS, axis=1, keepdims=True)
RADII = [2.0, 4.0, 6.0, 9.0, 14.0]


@pytest.fixture(scope="module")
def sphere():
    return icosphere(R, 5)


@pytest.fixture(scope="module")
def holey(sphere):
    return punch(*sphere, DIRS * R, RADII)


def test_stats_of_closed_shapes(sphere):
    V, F = sphere
    st = mesh_stats(V, F)
    assert st["triangles"] == 20480 and st["vertices"] == 10242 and st["components"] == 1
    assert st["watertight"] and st["holes"] == 0 and st["boundary_edges"] == 0 and st["non_manifold_edges"] == 0
    assert st["largest_hole_mm"] is None and st["border_mm"] is None
    assert st["volume_cm3"] == pytest.approx(4 / 3 * math.pi * R ** 3 / 1000, rel=2e-3)
    assert st["area_cm2"] == pytest.approx(4 * math.pi * R ** 2 / 100, rel=2e-3)
    assert st["bbox_mm"] == pytest.approx([80.0, 80.0, 80.0], abs=0.05)
    Vb, Fb = box(20.0, 8)
    sb = mesh_stats(Vb, Fb)
    assert sb["watertight"] and sb["volume_cm3"] == pytest.approx(8.0) and sb["area_cm2"] == pytest.approx(24.0)
    assert sb["bbox_mm"] == [20.0, 20.0, 20.0] and sb["components"] == 1
    # two pieces
    V2 = np.vstack([V, Vb + 100.0])
    F2 = np.vstack([F, Fb + len(V)])
    assert mesh_stats(V2, F2)["components"] == 2
    empty = mesh_stats(np.zeros((0, 3)), np.zeros((0, 3), int))
    assert empty["triangles"] == 0 and empty["watertight"] is False and empty["volume_cm3"] is None


def test_holes_of_a_nearly_closed_piece_are_all_holes(holey):
    V, F = holey
    st = mesh_stats(V, F)
    assert st["holes"] == 5 and st["border_mm"] is None and not st["watertight"] and st["volume_cm3"] is None
    outs = hole_outlines(V, F)
    per = [h["perimeter_mm"] for h in outs]
    assert per == sorted(per, reverse=True) and len(per) == 5
    assert st["largest_hole_mm"] == pytest.approx(per[0], abs=0.01)
    # the perimeter of a punched hole is a little more than its circle (the rim is jagged, the
    # more so for the 2 mm hole on ~1.9 mm triangles)
    for p, r in zip(sorted(per), RADII):
        assert 2 * math.pi * r * 0.9 < p < 2 * math.pi * r * 1.6
    for h in outs:
        pts = np.asarray(h["points"]).reshape(-1, 3)
        assert len(pts) == h["n_edges"] + 1 and np.allclose(pts[0], pts[-1])
        seg = np.linalg.norm(np.diff(pts, axis=0), axis=1).sum()  # points are rounded to 0.01 mm
        assert seg == pytest.approx(h["perimeter_mm"], rel=0.01)
        assert np.allclose(np.linalg.norm(pts, axis=1), R, atol=0.01)


def test_fill_up_to_a_size_fills_exactly_the_smaller_holes(holey):
    V, F = holey
    per = sorted(h["perimeter_mm"] for h in hole_outlines(V, F))
    limit = (per[2] + per[3]) / 2  # between the 3rd and 4th hole
    V2, F2 = fill_holes(V, F, limit)
    st = mesh_stats(V2, F2)
    assert st["holes"] == 2 and st["largest_hole_mm"] == pytest.approx(per[4], abs=0.01)
    assert sorted(h["perimeter_mm"] for h in hole_outlines(V2, F2)) == pytest.approx(per[3:], abs=0.01)
    assert np.array_equal(V2[: len(V)], V) and np.array_equal(F2[: len(F)], F)  # nothing else moved
    assert st["non_manifold_edges"] == 0
    # nothing is filled below the smallest hole, and a zero size changes nothing
    assert len(fill_holes(V, F, per[0] * 0.9)[1]) == len(F)
    assert len(fill_holes(V, F, 0.0)[1]) == len(F)


def test_filling_every_hole_restores_the_sphere(sphere, holey):
    V, F = holey
    V2, F2 = fill_holes(V, F, 1000.0)
    st = mesh_stats(V2, F2)
    assert st["watertight"] and st["holes"] == 0 and st["components"] == 1
    assert st["volume_cm3"] == pytest.approx(mesh_stats(*sphere)["volume_cm3"], rel=1e-3)
    new = V2[len(V):]
    assert len(new) > 200  # the big holes got interior vertices (refined, not a fan)
    err = np.abs(np.linalg.norm(new, axis=1) - R)
    # a flat cap over the 14 mm hole would sit R - sqrt(R^2 - 14^2) = 2.5 mm inside the sphere
    assert err.max() < 0.12 and err.mean() < 0.03
    # the new triangles are oriented like the sphere (outward)
    c = V2[F2[len(F):]].mean(axis=1)
    n = np.cross(V2[F2[len(F):, 1]] - V2[F2[len(F):, 0]], V2[F2[len(F):, 2]] - V2[F2[len(F):, 0]])
    assert np.all(np.einsum("ij,ij->i", n, c) > 0)
    # and they are about as big as the triangles around them (refined to the local density)
    def edge_len(VV, FF):
        return np.linalg.norm(VV[FF[:, 0]] - VV[FF[:, 1]], axis=1)
    assert np.median(edge_len(V2, F2[len(F):])) == pytest.approx(np.median(edge_len(V, F)), rel=0.35)


def test_a_large_hole_is_ear_clipped_then_refined_and_faired():
    # more edges than the dynamic programme takes: ear clipping, then the same refinement and fairing
    V, F = icosphere(R, 6)  # ~0.95 mm edges
    Vh, Fh = punch(V, F, [[0.0, 0.0, R]], [25.0])
    out = hole_outlines(Vh, Fh)
    assert len(out) == 1 and out[0]["n_edges"] > MAX_DP_EDGES
    V2, F2 = fill_holes(Vh, Fh, 1000.0)
    st = mesh_stats(V2, F2)
    assert st["watertight"] and st["non_manifold_edges"] == 0
    err = np.abs(np.linalg.norm(V2[len(Vh):], axis=1) - R)
    # the cap is 8.8 mm high: a flat fill would be that far off. A thin-plate fill is a little flatter
    # than a sphere over such a wide gap (0.54 mm at most, 0.18 mm on average when written).
    assert err.max() < 0.8 and err.mean() < 0.3


def test_a_twisted_hole_is_filled_inside_its_outline():
    # A band of a simulated scan around a hole whose edge climbs around a ball sitting in it (a
    # user's Scan 4, 2026-11-09). Its loop triangulates with folds; on folds the smooth fill was
    # nearly singular and threw the patch 5 m away. Now a patch must stay near its outline and
    # falls back to a soap film when the smooth solution doesn't.
    with np.load(Path(__file__).parent / "data" / "twisted_hole.npz") as z:
        V, F = z["V"].astype(np.float64), z["F"].astype(np.int64)
    out = hole_outlines(V, F)
    assert len(out) == 1 and out[0]["n_edges"] > MAX_DP_EDGES
    V2, F2 = fill_holes(V, F, 1000.0)
    assert len(V2) - len(V) > 1000 and mesh_stats(V2, F2)["holes"] == 0
    rim = np.asarray(out[0]["points"], dtype=np.float64).reshape(-1, 3)
    c = rim.mean(axis=0)
    assert np.linalg.norm(V2[len(V):] - c, axis=1).max() <= FAIR_BOUND * np.linalg.norm(rim - c, axis=1).max() + 0.5


def test_a_strip_keeps_its_outer_border():
    V, F = grid(101, 21, 1.0)
    c = V[F].mean(axis=1)
    keep = (np.hypot(c[:, 0] - 30, c[:, 1] - 10) > 3) & (np.hypot(c[:, 0] - 70, c[:, 1] - 10) > 2)
    F = F[keep]
    st = mesh_stats(V, F)
    assert st["holes"] == 2 and st["border_mm"] == pytest.approx(240.0) and st["components"] == 1
    assert len(hole_outlines(V, F)) == 2  # the border is not a hole
    V2, F2 = fill_holes(V, F, 1e6)
    s2 = mesh_stats(V2, F2)
    assert s2["holes"] == 0 and s2["border_mm"] == pytest.approx(240.0) and s2["boundary_edges"] == 240
    assert s2["area_cm2"] == pytest.approx(20.0, abs=1e-6)  # exactly the full strip again
    assert np.abs(V2[len(V):, 2]).max() < 1e-9  # a flat surface gets a flat fill
    # an open curved sheet: its border survives a fill that would take any hole
    Vc, Fc = grid(41, 41, 1.0, z=lambda x, y: 0.01 * ((x - 20) ** 2 + (y - 20) ** 2))
    s3 = mesh_stats(*fill_holes(Vc, Fc, 1e6))
    assert s3["border_mm"] is not None and s3["boundary_edges"] == 160


def test_loops_are_simple_at_a_bowtie_vertex():
    # two triangles sharing only vertex 0: two separate loops through it, each of 3 edges
    V = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0]], float)
    F = np.array([[0, 1, 2], [0, 3, 4]])
    loops = boundary_loops(V, F, edge_table(F, len(V)))
    assert len(loops) == 2 and all(loops.closed) and sorted(loops.n_edges().tolist()) == [3, 3]
    st = mesh_stats(V, F)
    assert st["components"] == 1 and st["boundary_edges"] == 6


def test_watertight_closes_the_outer_border_within_the_limit():
    # a sphere with a wide cap cut off (a part that sat on the table): the cut is too big for a hole
    # of this piece, so it is the outer border, left open unless watertight
    V, F = icosphere(R, 5)
    Vh, Fh = punch(V, F, [[0.0, 0.0, -R]], [1.2 * R])
    st0 = mesh_stats(Vh, Fh)
    assert st0["holes"] == 0 and st0["border_mm"] > 200
    V2, F2 = fill_holes(Vh, Fh, 1000.0)
    assert len(F2) == len(Fh)
    V3, F3 = fill_holes(Vh, Fh, 1000.0, include_borders=True)
    st = mesh_stats(V3, F3)
    assert st["watertight"] and st["holes"] == 0 and st["border_mm"] is None
    assert len(fill_holes(Vh, Fh, 0.9 * st0["border_mm"], include_borders=True)[1]) == len(Fh)  # the size limit holds


def test_a_hole_whose_vertices_coincide_is_not_listed():
    # a sphere with one triangle left out, its three corners then moved to one spot: the boundary
    # loop has three edges of length 0 (perimeter 0). It is no hole to show or to measure. A hole
    # with a real perimeter next to it is listed as before.
    V, F = icosphere(R, 3)
    V = V.copy()
    V[F[100]] = V[F[100]].mean(axis=0)
    F = np.delete(F, 100, axis=0)
    assert hole_outlines(V, F) == []
    Vh, Fh = punch(V, F, [[0.0, 0.0, -R]], [6.0])
    out = hole_outlines(Vh, Fh)
    assert len(out) == 1 and out[0]["perimeter_mm"] > 10.0


def _doubled(F: np.ndarray) -> int:
    """Faces that use the same three vertices as another face."""
    _, inv, cnt = np.unique(np.sort(F, axis=1), axis=0, return_inverse=True, return_counts=True)
    return int((cnt[inv.ravel()] > 1).sum())


def test_a_lone_triangle_hanging_by_a_vertex_gets_no_twin():
    """A fused scan leaves single triangles that touch the surface at one vertex only. Their three
    edges are open, so their outline looks like a three-edge hole; filling it added the same
    triangle reversed, back to back. The outline of one face is not a hole. A real three-edge hole
    (three faces around it) is still filled, and so is a hole whose rim carries such a triangle."""
    V, F = grid(21, 21, 1.0)
    idx = lambda x, y: y * 21 + x  # noqa: E731
    # 1: a lone triangle standing on an inner vertex of the sheet
    V1 = np.vstack([V, [[10.3, 10.0, 0.8], [10.0, 10.3, 0.8]]])
    F1 = np.vstack([F, [[idx(10, 10), len(V), len(V) + 1]]])
    V2, F2 = fill_holes(V1, F1, 5.0)
    assert len(F2) == len(F1) and np.array_equal(F2, F1) and len(V2) == len(V1) and _doubled(F2) == 0
    # 2: a real hole of three edges (one face taken out) is filled with one face
    gone = int(np.flatnonzero((F == idx(5, 5)).any(axis=1))[0])
    F3 = np.delete(F, gone, axis=0)
    V4, F4 = fill_holes(V, F3, 5.0)
    assert len(F4) == len(F) and sorted(F4[-1].tolist()) == sorted(F[gone].tolist()) and _doubled(F4) == 0
    # 3: a hole (a 2 x 2 block of cells taken out) with a lone triangle on a vertex of its rim: the
    # hole is filled, the triangle is left as it is
    cx = V[F].mean(axis=1)
    hole = (np.abs(cx[:, 0] - 14.0) < 1.0) & (np.abs(cx[:, 1] - 6.0) < 1.0)
    V5 = np.vstack([V, [[13.3, 5.0, 0.8], [13.0, 5.3, 0.8]]])
    F5 = np.vstack([F[~hole], [[idx(13, 5), len(V), len(V) + 1]]])
    assert hole.sum() == 8
    V6, F6 = fill_holes(V5, F5, 20.0)
    assert _doubled(F6) == 0 and len(F6) > len(F5) and np.array_equal(F6[: len(F5)], F5)
    lp = boundary_loops(V6, F6, edge_table(F6, len(V6)))
    assert sorted(lp.n_edges().tolist()) == [3, 80]  # the lone triangle's outline and the sheet's border: no hole left
