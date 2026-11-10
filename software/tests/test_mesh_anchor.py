"""ohs.mesh.anchor: pulling the dense surface onto the laser points ("Refine with lasers").

A 120 x 120 mm sheet (1 mm grid) and laser "sweeps" across its middle (lines 4 mm apart, a point
every 0.2 mm, 0.05 mm noise), as a scan leaves them:

* a dense surface 0.3 mm off is pulled onto the lasers where they are, and left where they aren't;
* without an offset nothing moves: the lasers' own scatter is below what gets corrected;
* glints far off the surface are outliers and change nothing.
"""
from __future__ import annotations

import numpy as np

from mesh_shapes import grid
from ohs.mesh import AnchorParams, anchor_to_points


def sweeps(rng, noise=0.05, z=0.0):
    ys = np.arange(30.0, 91.0, 4.0)
    xs = np.arange(30.0, 90.0, 0.2)
    X, Y = np.meshgrid(xs, ys)
    P = np.stack([X.ravel(), Y.ravel(), np.full(X.size, z)], axis=1)
    P[:, 2] += rng.normal(0.0, noise, len(P))
    return P


def test_a_dense_surface_off_the_lasers_is_pulled_onto_them():
    rng = np.random.default_rng(0)
    V, F = grid(121, 121, 1.0, z=lambda x, y: np.full_like(x, 0.3))
    V2, delta, rep = anchor_to_points(V, F, sweeps(rng))
    inner = (np.abs(V[:, 0] - 60) < 25) & (np.abs(V[:, 1] - 60) < 25)
    assert np.abs(V2[inner, 2]).max() < 0.05  # the 0.3 mm offset is gone where the lasers swept
    far = (np.abs(V[:, 0] - 60) > 45) | (np.abs(V[:, 1] - 60) > 45)  # beyond 3 smoothing lengths of any line
    assert np.all(V2[far] == V[far])
    assert rep["residual_before"]["median"] > 0.25 and rep["residual_after"]["median"] < 0.06
    assert rep["outliers"] < 0.01 * rep["points"] and 0.2 < rep["moved_share"] < 0.6
    assert abs(rep["moved"]["median"] - 0.3) < 0.03 and np.allclose(V2[:, :2], V[:, :2])


def test_nothing_moves_when_the_surface_already_agrees():
    rng = np.random.default_rng(1)
    V, F = grid(121, 121, 1.0)
    V2, delta, rep = anchor_to_points(V, F, sweeps(rng))
    assert np.array_equal(V2, V) and rep["moved_share"] == 0.0
    assert rep["residual_before"]["median"] < 0.05


def test_glints_are_outliers():
    rng = np.random.default_rng(2)
    V, F = grid(121, 121, 1.0)
    P = sweeps(rng)
    glints = P[rng.choice(len(P), len(P) // 20, replace=False)] + [0.0, 0.0, 1.5]  # a mirrored line 1.5 mm off
    far = P[:100] + [0.0, 0.0, 6.0]  # beyond the gate
    V2, _, rep = anchor_to_points(V, F, np.vstack([P, glints, far]), AnchorParams())
    assert np.abs(V2[:, 2]).max() < 0.02
    assert rep["outliers"] >= len(glints) + len(far) - 50
