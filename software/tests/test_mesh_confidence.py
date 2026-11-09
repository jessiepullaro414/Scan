"""ohs.mesh.confidence: the confidence in numbers, and each vertex's sigma through the mesh edits.

* ``sigma_stats`` gives shares of the surface (area-weighted), not of the vertices;
* ``carry_sigma``: smoothing keeps each vertex's sigma, filled holes are "not measured", deleting
  or keeping faces keeps the sigmas of the vertices left, floaters and decimation take the
  nearest vertex's.
"""
from __future__ import annotations

import numpy as np
import pytest

from mesh_shapes import grid, punch
from ohs.mesh import (SIGMA_NONE_MM, carry_sigma, decimate, delete_faces, fill_holes, keep_faces, remove_floaters,
                      sigma_stats, smooth)


def sig(x):
    return 0.015 + 0.01 * x


def sheet():
    """A 40 x 40 mm sheet (1 mm grid) whose sigma grows along x: 0.015 mm at x = 0 to 0.415 mm."""
    V, F = grid(41, 41, 1.0)
    return V, F, sig(V[:, 0]).astype(np.float32)


def test_sigma_stats_are_shares_of_the_surface():
    V, F, S = sheet()
    st = sigma_stats(S, V, F)
    assert st["median"] == pytest.approx(sig(20), abs=0.006) and st["p95"] == pytest.approx(sig(38), abs=0.006)
    # sigma <= 0.1 for x <= 8: the border column counts half (half the area)
    assert st["within_0_1"] == pytest.approx(8.5 / 40, abs=1e-3) and st["within_0_3"] == pytest.approx(28.5 / 40, abs=1e-3)
    assert st["not_measured"] == 0.0
    # a big triangle measured well and a small one badly: by area 100 : 1, by vertices 1 : 1
    V = np.array([[0, 0, 0], [20, 0, 0], [0, 10, 0], [50, 0, 0], [51, 0, 0], [50, 2, 0]], dtype=float)
    F = np.array([[0, 1, 2], [3, 4, 5]])
    S = np.array([0.05] * 3 + [1.0] * 3, dtype=np.float32)
    st = sigma_stats(S, V, F)
    assert st["within_0_1"] == pytest.approx(100 / 101, abs=1e-4) and st["median"] == pytest.approx(0.05)
    assert sigma_stats(S)["within_0_1"] == 0.5
    S[3:] = SIGMA_NONE_MM
    assert sigma_stats(S, V, F)["not_measured"] == pytest.approx(1 / 101, abs=1e-4)
    assert sigma_stats(None) is None and sigma_stats(np.zeros(0)) is None


def test_carry_sigma_follows_the_vertices_through_each_edit():
    V, F, S = sheet()
    V, F = punch(V, F, [(20.0, 20.0, 0.0)], [3.0])
    assert carry_sigma("smooth", V, F, None, V) is None
    assert carry_sigma("smooth", V, F, S[:-1], V) is None  # doesn't fit the vertices
    # smoothing moves the vertices: each keeps its sigma
    V2, _ = smooth(V, F, "taubin", 0.5)
    assert np.array_equal(carry_sigma("smooth", V, F, S, V2), S)
    # filling adds vertices after the old ones: nothing measured them
    V2, F2 = fill_holes(V, F, 40.0)
    S2 = carry_sigma("fill_holes", V, F, S, V2)
    assert len(V2) > len(V) and np.array_equal(S2[: len(V)], S) and np.all(S2[len(V):] == SIGMA_NONE_MM)
    # deleting / keeping faces keeps the vertices of the faces left, in order: the same sigma at the same place
    sel = np.flatnonzero(V[F].mean(axis=1)[:, 0] < 10.0)
    for op, fn in (("delete_faces", delete_faces), ("keep_faces", keep_faces)):
        V2, _ = fn(V, F, sel)
        S2 = carry_sigma(op, V, F, S, V2, sel)
        assert len(S2) == len(V2) and np.allclose(S2, sig(V2[:, 0]), atol=1e-6), op
    # floaters and decimation: the nearest vertex's sigma (the same here: sigma follows x exactly)
    Vf = np.vstack([V, V[:3] + [0.0, 0.0, 30.0]])
    Ff = np.vstack([F, [[len(V), len(V) + 1, len(V) + 2]]])
    Sf = np.r_[S, np.full(3, 9.0, np.float32)]
    V2, _ = remove_floaters(Vf, Ff, min_size_mm=5.0)
    S2 = carry_sigma("remove_floaters", Vf, Ff, Sf, V2)
    assert len(V2) < len(Vf) and np.allclose(S2, sig(V2[:, 0]), atol=1e-6)
    V2, _ = decimate(V, F, ratio=0.25)
    S2 = carry_sigma("decimate", V, F, S, V2)
    assert np.abs(S2 - sig(V2[:, 0])).max() <= 0.01 + 1e-6  # within a vertex spacing (1 mm)
