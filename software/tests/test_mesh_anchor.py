"""ohs.mesh.anchor: pulling the dense surface onto the laser points ("Refine with lasers").

A 120 x 120 mm sheet (1 mm grid) and laser "sweeps" across its middle (lines 4 mm apart, a point
every 0.2 mm, 0.05 mm noise), as a scan leaves them:

* a dense surface 0.3 mm off is pulled onto the lasers where they are, and left where they aren't;
* without an offset nothing moves: the lasers' own scatter is below what gets corrected;
* glints far off the surface are outliers and change nothing;
* the laser agreement map: each vertex's distance to the laser points near it, none where no laser
  point came near.
"""
from __future__ import annotations

import numpy as np

from mesh_shapes import grid
from ohs.mesh import AnchorParams, anchor_to_points, laser_agreement
from ohs.mesh.topology import vertex_normals


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


def test_laser_agreement_map():
    """A sheet 0.3 mm off its laser lines: the vertices under the lines read 0.3 mm (signed along
    their normal: the lasers see the surface further in), the rest have no value."""
    rng = np.random.default_rng(3)
    V, F = grid(121, 121, 1.0, z=lambda x, y: np.full_like(x, 0.3))
    up = float(np.sign(vertex_normals(V, F)[:, 2].mean()))  # the sheet's normal: +z or -z
    P = sweeps(rng)
    val, rep = laser_agreement(V, F, P)
    assert val.shape == (len(V),) and val.dtype == np.float32
    has = np.isfinite(val)
    on_line = (V[:, 1] >= 30) & (V[:, 1] <= 90) & (V[:, 1] % 4 == 2) & (V[:, 0] >= 30) & (V[:, 0] < 90)
    assert has[on_line].all() and not has[(V[:, 1] % 4 == 0) | (V[:, 0] < 29) | (V[:, 0] > 91)].any()
    assert np.allclose(val[has], -0.3 * up, atol=0.12) and abs(float(val[has].mean()) + 0.3 * up) < 0.01
    assert rep["points"] == {"total": len(P), "on_surface": len(P), "used": len(P), "stray": 0}
    st = rep["stats"]
    assert st["n"] == int(has.sum()) and abs(st["mean_mm"] + 0.3 * up) < 0.01 and abs(st["p50_mm"] - 0.3) < 0.01
    assert st["within_0_1"] == 0.0 and 0.05 < st["covered"] < 0.09  # 16 lines of 60 mm, a 1 mm strip each, of 120 x 120
    assert rep["gate_mm"] == 3.0

    # a surface that agrees; glints beyond the gate are stray, points past the sheet's edge are not on it
    V, F = grid(121, 121, 1.0)
    glints = P[:200] + [0.0, 0.0, 5.0]
    off_mesh = P[:100] - [80.0, 0.0, 0.0]
    val, rep = laser_agreement(V, F, np.vstack([P, glints, off_mesh]))
    has = np.isfinite(val)
    assert rep["points"] == {"total": len(P) + 300, "on_surface": len(P) + 200, "used": len(P), "stray": 200}
    assert np.abs(val[has]).max() < 0.15 and rep["stats"]["within_0_1"] > 0.99 and rep["stats"]["within_0_3"] == 1.0
    assert abs(rep["stats"]["mean_mm"]) < 0.01 and rep["stats"]["max_mm"] < 0.15

    # nothing to compare with
    for pts in (np.zeros((0, 3)), glints):
        val, rep = laser_agreement(V, F, pts)
        assert np.isnan(val).all() and rep["stats"]["n"] == 0 and rep["stats"]["covered"] in (None, 0.0)
        assert rep["stats"]["mean_mm"] is None and rep["stats"]["within_0_1"] is None
