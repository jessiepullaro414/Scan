"""Mesh statistics for the stats panel (APP_API.md ``MeshStats``).

:func:`mesh_stats` returns, in mm / cm^2 / cm^3:

* ``triangles``, ``vertices``, ``components`` (connected pieces with faces);
* ``holes``: boundary loops that are not outer borders (see :mod:`ohs.mesh.topology`: each open
  piece's longest loop is its border, nearly closed pieces have none), ``largest_hole_mm`` (its
  perimeter) and ``border_mm`` (the longest outer border);
* ``boundary_edges`` (one face), ``non_manifold_edges`` (three or more faces), ``watertight``
  (neither, and at least one face);
* ``bbox_mm`` (size along x, y, z), ``area_cm2`` and ``volume_cm3`` (only when watertight:
  the absolute signed volume of the closed surface).
"""
from __future__ import annotations

import numpy as np

from ohs.mesh.holes import find_holes
from ohs.mesh.topology import as_mesh, rows

__all__ = ["mesh_stats", "empty_stats"]


def empty_stats() -> dict:
    return {"triangles": 0, "vertices": 0, "components": 0, "holes": 0, "largest_hole_mm": None, "border_mm": None,
            "boundary_edges": 0, "non_manifold_edges": 0, "watertight": False, "bbox_mm": [0.0, 0.0, 0.0],
            "area_cm2": 0.0, "volume_cm3": None}


def mesh_stats(V: np.ndarray, F: np.ndarray) -> dict:
    """``MeshStats`` of a mesh (see the module docstring)."""
    V, F = as_mesh(V, F)
    out = empty_stats()
    out["vertices"] = int(len(V))
    if len(F) == 0:
        return out
    loops, is_border, info = find_holes(V, F)
    et = info["et"]
    boundary = int(np.count_nonzero(et.counts == 1))
    nonman = int(np.count_nonzero(et.counts > 2))
    holes = loops.perimeter[~is_border]
    borders = loops.perimeter[is_border]
    used = np.zeros(len(V), dtype=bool)
    used[F.reshape(-1)] = True
    P = V[used]
    watertight = boundary == 0 and nonman == 0
    volume = None
    if watertight:
        v0, v1, v2 = rows(V, F[:, 0]), rows(V, F[:, 1]), rows(V, F[:, 2])
        volume = round(abs(float(np.einsum("ij,ij->i", v0, np.cross(v1, v2)).sum()) / 6.0) / 1000.0, 3)
    out.update({
        "triangles": int(len(F)),
        "components": int(info["n_components"]),
        "holes": int(len(holes)),
        "largest_hole_mm": round(float(holes.max()), 2) if len(holes) else None,
        "border_mm": round(float(borders.max()), 2) if len(borders) else None,
        "boundary_edges": boundary,
        "non_manifold_edges": nonman,
        "watertight": bool(watertight),
        "bbox_mm": [round(float(x), 2) for x in (P.max(axis=0) - P.min(axis=0))],
        "area_cm2": round(float(info["areas"].sum()) / 100.0, 2),
        "volume_cm3": volume,
    })
    return out
