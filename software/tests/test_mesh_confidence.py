"""ohs.mesh.confidence: the confidence in numbers, and each vertex's sigma and each face's filled
flag through the mesh edits.

* ``sigma_stats`` gives shares of the surface (area-weighted), not of the vertices;
* ``carry_sigma``: smoothing keeps each vertex's sigma, filled holes are "not measured", deleting
  or keeping faces keeps the sigmas of the vertices left, floaters and decimation take the
  nearest vertex's;
* ``carry_filled``: the faces fill_holes adds are filled, and the flags follow them; they reach
  a PLY file (face property ``filled``) and back.
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


def test_carry_filled_marks_new_faces_and_follows_them():
    from ohs.mesh import carry_filled, filled_area

    V, F = grid(61, 61, 1.0)
    V, F = punch(V, F, [(30.0, 30.0, 0.0)], [6.0])
    assert carry_filled("smooth", V, F, None, V, F) is None  # nothing filled yet
    V2, F2 = fill_holes(V, F, 100.0)
    fl = carry_filled("fill_holes", V, F, None, V2, F2)
    assert fl is not None and not fl[: len(F)].any() and fl[len(F):].all()
    hole = np.pi * 6.0 ** 2
    assert 0.7 * hole < filled_area(V2, F2, fl) < 1.3 * hole
    # smoothing keeps them; deleting faces keeps the flags of the faces left
    assert np.array_equal(carry_filled("smooth", V2, F2, fl, V2, F2), fl)
    sel = np.arange(100)
    V3, F3 = delete_faces(V2, F2, sel)
    fl3 = carry_filled("delete_faces", V2, F2, fl, V3, F3, sel)
    assert np.array_equal(fl3, fl[100:])
    # decimation: each new face takes the flag of the old face nearest its centre
    V4, F4 = decimate(V2, F2, ratio=0.3)
    fl4 = carry_filled("decimate", V2, F2, fl, V4, F4)
    c = V4[F4].mean(axis=1)
    inside = np.hypot(c[:, 0] - 30, c[:, 1] - 30)
    assert fl4[inside < 4.0].all() and not fl4[inside > 9.0].any()


def test_filled_flags_round_trip_through_ply(tmp_path):
    from ohs.track.export import load_ply, save_mesh

    V = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]])
    F = np.array([[0, 1, 2], [1, 3, 2]])
    save_mesh(tmp_path / "m.ply", V, F, quality=np.ones(4), filled=[False, True])
    d = load_ply(tmp_path / "m.ply")
    assert d["filled"].tolist() == [False, True] and d["faces"].tolist() == F.tolist()
    assert np.allclose(d["quality"], 1.0)
    save_mesh(tmp_path / "plain.ply", V, F)
    assert load_ply(tmp_path / "plain.ply")["filled"] is None
    # ASCII, as other programs write it
    (tmp_path / "a.ply").write_text("ply\nformat ascii 1.0\nelement vertex 4\nproperty float x\nproperty float y\n"
                                    "property float z\nelement face 2\nproperty list uchar int vertex_indices\n"
                                    "property uchar filled\nend_header\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n3 0 1 2 1\n3 1 3 2 0\n")
    d = load_ply(tmp_path / "a.ply")
    assert d["filled"].tolist() == [True, False] and d["faces"].tolist() == F.tolist()
