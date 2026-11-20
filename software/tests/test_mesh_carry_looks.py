"""Each vertex's count of looks through the mesh edits (``ohs.mesh.carry_looks``; FEATURE_PLAN N1).

As the sigma is carried (``carry_sigma``): removed vertices drop theirs, kept ones keep theirs in
order, smoothing keeps them all, a decimated vertex takes its nearest old vertex's, and the vertices
hole filling adds get 0: no look measured them. The Check's reasons read 0 as "no looks here": such a
vertex is left out of its area's count and of the surface that passes, and an area with no counted
vertex says so instead of failing.
"""
from __future__ import annotations

import numpy as np
import pytest

from mesh_shapes import grid, punch
from ohs import mesh as M
from ohs.mesh import carry_looks, carry_sigma
from ohs.mesh.check import weak_areas
from ohs.mesh.topology import compact


def _plate_with_hole_and_speck():
    V, F = grid(41, 31, 1.0)
    V, F = punch(V, F, [(20.0, 15.0, 0.0)], [3.0])
    V, F, _ = compact(V, F)  # no vertex left without a face
    # a loose speck far off: remove_floaters takes it away
    Vs = np.array([[100.0, 100.0, 0.0], [100.5, 100.0, 0.0], [100.0, 100.5, 0.0]])
    F = np.vstack([F, np.array([[0, 1, 2]]) + len(V)])
    V = np.vstack([V, Vs])
    L = (1.0 + np.arange(len(V)) % 17).astype(np.float32)  # every vertex its own, >= 1
    return V, F, L


def test_each_edit_carries_the_looks_as_the_sigma():
    V, F, L = _plate_with_hole_and_speck()
    assert carry_looks("smooth", V, F, None, V) is None
    # smoothing moves the vertices: each keeps its looks
    V2, F2 = M.smooth(V, F, "taubin", 0.5)
    L2 = carry_looks("smooth", V, F, L, V2)
    assert L2.dtype == np.float32 and np.array_equal(L2, L)
    # loose pieces: the kept vertices keep theirs, in order
    V2, F2, kept = M.remove_floaters(V, F, min_size_mm=5.0, return_kept=True)
    assert len(V2) == len(V) - 3
    assert np.array_equal(carry_looks("remove_floaters", V, F, L, V2, kept), L[:-3])
    # a selection deleted / kept: the vertices of the faces left
    sel = np.arange(0, len(F), 3)
    for op, fn in (("delete_faces", M.delete_faces), ("keep_faces", M.keep_faces)):
        V2, F2 = fn(V, F, sel)
        L2 = carry_looks(op, V, F, L, V2, sel)
        S2 = carry_sigma(op, V, F, L, V2, sel)  # the sigma's rule, on the same numbers
        assert np.array_equal(L2, S2) and len(L2) == len(V2)
        # each kept vertex's looks are its own: found by position
        idx = np.array([np.flatnonzero(np.all(V == p, axis=1))[0] for p in V2])
        assert np.array_equal(L2, L[idx])
    # hole filling adds vertices after the old ones: 0 looks (nothing measured them)
    V2, F2 = M.fill_holes(V, F, 30.0)
    assert len(V2) >= len(V)
    L2 = carry_looks("fill_holes", V, F, L, V2)
    assert np.array_equal(L2[:len(V)], L) and np.all(L2[len(V):] == 0.0)
    # decimation: the looks of the nearest vertex before
    V2, F2 = M.decimate(V, F, ratio=0.5)
    L2 = carry_looks("decimate", V, F, L, V2)
    assert len(L2) == len(V2) and set(np.unique(L2)) <= set(np.unique(L))
    # looks that do not fit the mesh: none
    assert carry_looks("smooth", V, F, L[:-1], V) is None


def test_the_reasons_read_no_looks_as_no_looks_here():
    """A weak patch where hole filling made some vertices (0 looks): the count is over the others; a
    patch where no vertex has looks says so, and no area fails."""
    V, F = grid(101, 61, 1.0)
    S = np.full(len(V), 0.02)
    m = (V[:, 0] >= 40) & (V[:, 0] <= 60) & (V[:, 1] >= 20) & (V[:, 1] <= 40)
    S[m] = 0.2
    L = np.full(len(V), 12.0)
    L[m] = 4.0
    a = weak_areas(V, F, S, tolerance_mm=0.1, looks=L)["areas"][0]
    assert a["reason"] == "few_looks" and a["evidence"]["looks"] == 4.0
    some = L.copy()
    some[np.flatnonzero(m)[::2]] = 0.0  # half of the patch made up: not counted
    some[np.flatnonzero(~m)[::5]] = 0.0  # and some of the surface that passes
    a = weak_areas(V, F, S, tolerance_mm=0.1, looks=some)["areas"][0]
    assert a["reason"] == "few_looks" and a["evidence"]["looks"] == 4.0 and a["evidence"]["looks_passing"] == 12.0
    none = L.copy()
    none[np.unique(F[a["faces"]])] = 0.0  # every vertex of the area (the patch and the ring of faces round it)
    r = weak_areas(V, F, S, tolerance_mm=0.1, looks=none)
    a = r["areas"][0]
    assert a["reason"] == "unknown" and a["evidence"]["looks"] is None
    assert any(u.startswith("few_looks: no point of it has a count of looks") for u in a["unknown_because"])
    assert r["reasons"]["looks"] is True and r["reasons"]["looks_passing"] == 12.0
    # NaN reads the same as 0
    nan = L.copy()
    nan[np.unique(F[a["faces"]])] = np.nan
    assert weak_areas(V, F, S, tolerance_mm=0.1, looks=nan)["areas"][0]["evidence"]["looks"] is None


@pytest.mark.parametrize("op", ["smooth", "fill_holes", "decimate"])
def test_no_looks_stays_no_looks(op):
    V, F, _ = _plate_with_hole_and_speck()
    assert carry_looks(op, V, F, None, V) is None
