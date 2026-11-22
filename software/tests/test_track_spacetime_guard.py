"""The guard on "Refine from the images" (``SpacetimeParams.guard_*``, :func:`ohs.track.spacetime.guard_patches`;
ENGINE_PLAN X6): Make mesh's ``[process] refine_from_images`` turns it on, the stage itself runs without it.

* **Off by default:** ``SpacetimeParams()`` is unguarded, and an unguarded run's mesh and report are what they were
  (no ``guard`` in the report, the vertices moved by every kept patch).
* **Not smooth:** a patch within its fit of a 90 degree crease is left out; on a gently curved sheet none is.
* **Free edge:** a patch within ``guard_edge_mm`` of the mesh's outline is left out, the others are used.
* **Thin walls** (measured limit: ``guard_thin_mm`` = 2 mm): on two-sided plates 0.5, 1.0 and 1.9 mm thick every patch
  is left out, on a 3 mm plate none is; and a 0.5 mm plate whose images would pull its top 0.2 mm in keeps its
  thickness.
* **Disagreement:** a patch whose refined surface lies more than ``guard_move_mm`` from the fused one is left out, and
  no vertex moves farther than that; the report counts what was used and what was left, by reason.

The image search is replaced by given offsets (this checks the guard, not the search).
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from mesh_shapes import grid
from ohs.mesh.topology import vertex_normals
from ohs.track import spacetime as ST
from ohs.track.spacetime import SpacetimeParams, guard_patches, make_patches, project_onto_patches

HALF = 2.0
BASE = replace(SpacetimeParams(), half_mm=HALF, spacing_mm=1.5, samples=15)


def plate(t: float, n: int = 41) -> tuple[np.ndarray, np.ndarray]:
    """A plate ``t`` mm thick: its top (normals +z) at z = t and its bottom (normals -z) at z = 0, 40 mm square."""
    Vt, Ft = grid(n, n, 1.0, z=lambda x, y: np.full_like(x, t))
    Vb, Fb = grid(n, n, 1.0)
    return np.concatenate([Vt, Vb]), np.concatenate([Ft, Fb[:, ::-1] + len(Vt)])


def patches_of(V, F):
    N = vertex_normals(V, F)
    return N, make_patches(V, F, N, BASE.spacing_mm, BASE.half_mm, fit=BASE.fit)


def fake_search(offset_of):
    """A stand-in for the image search: every patch kept, its refined surface ``offset_of(patch centres)`` (mm) from
    the fused mesh along its normal."""
    def search(patches, *a, **k):
        n = len(patches)
        return {"offset": offset_of(patches.P) - patches.geo[:, 12], "views": np.full(n, 20), "cost": np.full(n, 0.1),
                "curvature": np.full(n, 5.0), "kept": np.ones(n, dtype=bool), "reason": np.full(n, "", dtype=object),
                "device": "cpu"}
    return search


def refine(monkeypatch, V, F, params, offset_of):
    monkeypatch.setattr(ST, "search_offsets", fake_search(offset_of))
    return ST.refine_from_images(V, F, [], [], np.zeros((0, 4, 4)), np.eye(3), 60.0, params)


def test_the_guard_is_off_by_default_and_the_stage_is_as_before(monkeypatch):
    assert SpacetimeParams().guarded is False
    assert all(getattr(SpacetimeParams(), f) is None for f in ("guard_smooth_deg", "guard_edge_mm", "guard_thin_mm",
                                                               "guard_move_mm"))
    rng = np.random.default_rng(3)
    V, F = grid(61, 61, 1.0)
    V[:, 2] += rng.normal(0.0, 0.03, len(V))
    V2, _, rep = refine(monkeypatch, V, F, BASE, lambda P: np.full(len(P), 0.5))
    assert "guard" not in rep and "used" not in rep["patch_results"] and "guard" not in rep["ms"]
    # the same move as every kept patch gives, by hand
    N, pa = patches_of(V, F)
    move, _ = project_onto_patches(V, N, pa, np.full(len(pa), 0.5) - pa.geo[:, 12], HALF, BASE.spacing_mm, BASE.gate_mm)
    assert np.array_equal(V2, V + move) and np.abs(move).max() > 0.4


def test_patches_by_a_crease_are_left_out_and_a_gentle_curve_is_not():
    V, F = grid(61, 41, 1.0, z=lambda x, y: np.abs(x - 30.0))  # two faces at 90 degrees
    N, pa = patches_of(V, F)
    p = replace(BASE, guard_smooth_deg=30.0)
    rough = guard_patches(V, F, N, pa, np.zeros(len(pa)), p)["rough"]
    # distance along the sheet from the crease
    d = np.abs(pa.P[:, 0] - 30.0)
    reach = BASE.fit * HALF
    assert rough[d < 0.5 * reach].all() and not rough[d > reach + 1.0].any()
    V, F = grid(61, 61, 1.0, z=lambda x, y: 0.004 * (x - 30.0) ** 2)  # 0.6 degrees per mm
    N, pa = patches_of(V, F)
    assert not guard_patches(V, F, N, pa, np.zeros(len(pa)), p)["rough"].any()
    off = replace(BASE, guard_edge_mm=3.0)  # the smoothness part off: nothing rough
    assert not guard_patches(V, F, N, pa, np.zeros(len(pa)), off)["rough"].any()


def test_patches_near_a_free_edge_are_left_out():
    V, F = grid(61, 61, 1.0)
    N, pa = patches_of(V, F)
    edge = guard_patches(V, F, N, pa, np.zeros(len(pa)), replace(BASE, guard_edge_mm=3.0))["edge"]
    d = np.min(np.stack([pa.P[:, 0], 60.0 - pa.P[:, 0], pa.P[:, 1], 60.0 - pa.P[:, 1]]), axis=0)
    assert edge[d < 3.0 - 1e-6].all() and not edge[d > 3.0 + 1e-6].any() and (~edge).sum() > 300


@pytest.mark.parametrize("t, thin", [(0.5, True), (1.0, True), (1.9, True), (3.0, False)])
def test_thin_walls_are_left_out_up_to_the_limit(t, thin):
    V, F = plate(t)
    N, pa = patches_of(V, F)
    assert len(pa) > 400
    p = replace(BASE, guard_thin_mm=2.0)
    why = guard_patches(V, F, N, pa, np.zeros(len(pa)), p)
    assert why["thin"].all() if thin else not why["thin"].any()
    assert not guard_patches(V, F, N, pa, np.zeros(len(pa)), BASE)["thin"].any()  # off: nothing


def test_a_thin_plate_keeps_its_thickness(monkeypatch):
    V, F = plate(0.5)
    top = V[:, 2] > 0.25
    # the images would pull the top 0.2 mm in, towards the bottom
    pull = lambda P: np.where(P[:, 2] > 0.25, -0.2, 0.0)  # noqa: E731
    V0, _, _ = refine(monkeypatch, V, F, BASE, pull)
    assert np.median(V0[top, 2]) < 0.35  # unguarded it thins
    V2, _, rep = refine(monkeypatch, V, F, replace(BASE, **{
        "guard_smooth_deg": 30.0, "guard_edge_mm": 3.0, "guard_thin_mm": 2.0, "guard_move_mm": 0.3}), pull)
    assert np.array_equal(V2, V)
    g = rep["guard"]
    assert g["used"] == 0 and sum(g["patches_left"].values()) == rep["kept"]


def test_where_the_images_disagree_with_the_mesh_it_stays(monkeypatch):
    rng = np.random.default_rng(5)
    V, F = grid(61, 61, 1.0)
    V[:, 2] += rng.normal(0.0, 0.02, len(V))
    # the right half's refined surface 0.5 mm off, the left half's 0.1 mm
    off = lambda P: np.where(P[:, 0] > 30.0, 0.5, 0.1)  # noqa: E731
    p = replace(BASE, guard_move_mm=0.3)
    V2, _, rep = refine(monkeypatch, V, F, p, off)
    move = np.linalg.norm(V2 - V, axis=1)
    assert move.max() <= 0.3
    assert np.median(move[(V[:, 0] > 10) & (V[:, 0] < 25) & (V[:, 1] > 10) & (V[:, 1] < 50)]) > 0.05  # the left moves
    assert np.all(move[V[:, 0] > 36.0] == 0.0)  # the right stays
    g = rep["guard"]
    assert g["patches_left"]["far"] > 100 and g["used"] == rep["kept"] - sum(g["patches_left"].values())
    assert g["limits"] == {"smooth_deg": None, "edge_mm": None, "thin_mm": None, "move_mm": 0.3}
    assert np.array_equal(rep["patch_results"]["used"], rep["patch_results"]["kept"] & ~(np.abs(
        rep["patch_results"]["offset"]) > 0.3))
    assert "guard" in rep["ms"]


def test_make_mesh_runs_it_guarded_and_the_stage_does_not():
    from ohs.server.processing import MAKE_MESH_SPACETIME_GUARD, make_mesh_spacetime_params, spacetime_changes

    p = make_mesh_spacetime_params()
    assert p.guarded and {k: getattr(p, k) for k in MAKE_MESH_SPACETIME_GUARD} == MAKE_MESH_SPACETIME_GUARD
    assert replace(p, **{k: None for k in MAKE_MESH_SPACETIME_GUARD}) == SpacetimeParams()  # nothing else changes
    # with the guard, the patches it used decide whether the mesh changes
    rep = {"kept": 10_000, "covered_share": 0.5}
    assert spacetime_changes(rep) and not spacetime_changes({**rep, "guard": {"used": 3}})
