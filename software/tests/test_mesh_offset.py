"""ohs.mesh.offset: how far an edited mesh lies from the measured one, on shapes with a known move."""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("open3d")

from mesh_shapes import grid, icosphere, punch  # noqa: E402
from ohs.mesh import fill_holes, offset_stats, surface_offset  # noqa: E402

R = 40.0


def test_a_grown_and_a_shrunk_sphere():
    V, F = icosphere(R, 5)
    out = surface_offset(V * (R + 0.2) / R, V, F)
    assert out.dtype == np.float32 and out.shape == (len(V),)
    assert np.all(np.abs(out - 0.2) < 0.03)  # outward: along the reference's normals
    inn = surface_offset(V * (R - 0.2) / R, V, F)
    assert np.all(np.abs(inn + 0.2) < 0.03)
    assert np.all(np.abs(surface_offset(V, V, F)) < 1e-4)  # unchanged: zero


def test_a_dent_in_a_plate_and_the_statistics():
    V, F = grid(81, 61, 0.5)
    n = np.cross(V[F[0, 1]] - V[F[0, 0]], V[F[0, 2]] - V[F[0, 0]])
    up = float(np.sign(n[2]))  # the sign follows the reference's face normals
    c = V.mean(axis=0)
    r = np.linalg.norm(V[:, :2] - c[:2], axis=1)
    dz = -0.5 * np.exp(-(r / 4.0) ** 2)
    V2 = V.copy()
    V2[:, 2] += dz
    out = surface_offset(V2, V, F)
    assert np.allclose(out, up * dz, atol=1e-4)
    st = offset_stats(out)
    assert st["n"] == len(V) and st["new"] == 0
    assert st["max_mm"] == pytest.approx(0.5, abs=1e-3) and st["p95_mm"] < 0.2 and 0 < st["rms_mm"] < 0.2
    assert st["moved"] == pytest.approx(float(np.mean(np.abs(dz) > 0.01)), abs=1e-3)
    assert st["mean_mm"] == pytest.approx(up * float(dz.mean()), abs=1e-4)


def test_a_filled_hole_is_new_surface_and_the_rest_did_not_move():
    V, F = grid(81, 61, 0.5)
    c = V.mean(axis=0)
    Vh, Fh = punch(V, F, [c], [6.0])
    V2, F2 = fill_holes(Vh, Fh, 60.0)
    assert len(F2) > len(Fh)
    filled = np.arange(len(F2)) >= len(Fh)  # fill_holes appends its faces
    out = surface_offset(V2, Vh, Fh, F=F2, filled=filled)
    new = np.isnan(out)
    measured = np.zeros(len(V2), dtype=bool)
    measured[F2[~filled]] = True
    assert np.array_equal(new, ~measured)  # only vertices that no measured face uses
    assert np.all(np.abs(out[~new]) < 1e-4)
    st = offset_stats(out)
    assert st["new"] == int(new.sum()) and st["n"] == int((~new).sum()) and st["max_mm"] < 1e-3 and st["moved"] == 0.0
    # without the flags every vertex gets a distance (the patch lies in the plate's plane: about 0)
    assert not np.isnan(surface_offset(V2, Vh, Fh)).any()


def test_edge_cases():
    V, F = grid(5, 5, 1.0)
    assert np.isnan(surface_offset(V, V, F[:0])).all()  # no reference surface
    assert surface_offset(V[:0], V, F).shape == (0,)
    assert offset_stats(np.full(4, np.nan, dtype=np.float32)) == {"n": 0, "new": 4, "rms_mm": None, "mean_mm": None,
                                                                 "p50_mm": None, "p95_mm": None, "p99_mm": None,
                                                                 "max_mm": None, "moved": None}
    with pytest.raises(ValueError):
        surface_offset(V, V, F, F=F, filled=np.zeros(3, dtype=bool))
