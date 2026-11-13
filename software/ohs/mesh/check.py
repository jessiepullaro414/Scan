"""Which parts of a mesh need another pass: the areas whose confidence misses a tolerance.

:func:`weak_areas` takes the mesh, each vertex's sigma (:mod:`ohs.mesh.confidence`, mm) and the
faces hole filling made up, and answers "this share of the surface is good to the tolerance, these
areas are not", largest first. It is pure numpy / scipy, a few seconds on millions of triangles.

* A face **fails** when it is *filled* (kind ``not_measured``: the surface was made up, nothing
  measured it), when one of its vertices has no usable sigma (NaN, zero or negative, or
  :data:`~ohs.mesh.confidence.SIGMA_NONE_MM` and above: also ``not_measured``), or when ``k`` times
  the mean sigma of its three vertices exceeds the tolerance (``uncertain``: with ``k = 2`` the
  scan's own 2-sigma band there is wider than the part may be off). The sigma is statistical: a
  calibration or scale error is not in it.
* Failing faces that share an **edge** form an area (two patches touching at one vertex stay two).
  An area is ``not_measured`` when most of its surface is, else ``uncertain``.
* Areas under a minimum size are left out of the list (single noisy faces would bury the real
  ones) but still count in the shares.
"""
from __future__ import annotations

import numpy as np

from ohs.mesh.confidence import SIGMA_NONE_MM
from ohs.mesh.topology import as_mesh, edge_table, face_areas, rows, sorted_unique

__all__ = ["weak_areas", "MIN_AREA_FLOOR_MM2", "MIN_AREA_FACES"]

#: the smallest area worth listing, whatever the mesh (mm^2): a 2 x 2 mm spot
MIN_AREA_FLOOR_MM2 = 4.0
#: ... and never fewer than this many median faces, so a coarse mesh does not list single faces
MIN_AREA_FACES = 10


def _result(has_sigma: bool, tolerance_mm: float, k: float, min_area: float, total: float, ok, unc, nm,
            n_areas: int, areas: list) -> dict:
    return {"has_sigma": has_sigma, "tolerance_mm": float(tolerance_mm), "k": float(k),
            "min_area_mm2": float(min_area), "area_mm2": float(total), "share_ok": ok, "share_uncertain": unc,
            "share_not_measured": nm, "n_areas": int(n_areas), "areas": areas}


def _face_groups(F_fail: np.ndarray, n_vertices: int) -> tuple[int, np.ndarray]:
    """Connected groups of the faces ``F_fail`` over shared edges: ``(count, label (m,))``."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    m = len(F_fail)
    et = edge_table(F_fail, n_vertices)
    pos = np.arange(len(et.he_order))
    group = et.he_edge[et.he_order]
    later = pos != et.edge_start[group]  # every half-edge of an edge but its first one
    first_face = et.he_order[et.edge_start[group[later]]] // 3
    other_face = et.he_order[later] // 3
    A = coo_matrix((np.ones(len(first_face), dtype=np.int8), (first_face, other_face)), shape=(m, m)).tocsr()
    return connected_components(A, directed=False)


def weak_areas(V: np.ndarray, F: np.ndarray, sigma: np.ndarray | None, filled: np.ndarray | None = None,
               tolerance_mm: float = 0.1, k: float = 2.0, min_area_mm2: float | None = None,
               max_areas: int = 50) -> dict:
    """The areas of ``(V, F)`` that miss ``tolerance_mm`` (see the module docstring).

    ``sigma`` is one value per vertex (mm); None for a mesh without confidence: then only the
    ``filled`` faces (one flag per face) fail and the result says ``has_sigma`` False.

    ``min_area_mm2`` defaults to the larger of :data:`MIN_AREA_FLOOR_MM2` (4 mm^2) and
    :data:`MIN_AREA_FACES` (10) times the mesh's median face area: a fixed size alone would list
    single faces of a coarse mesh, a face count alone would hide real spots on a fine one.

    Returns a dict: ``has_sigma``, ``tolerance_mm``, ``k``, ``min_area_mm2`` (as used), ``area_mm2``
    (whole mesh), ``share_ok`` / ``share_uncertain`` / ``share_not_measured`` (of the surface area;
    the first two are None without sigma, and all three None for an empty mesh), ``n_areas`` (all
    above the minimum) and ``areas``: at most ``max_areas``, largest first, each with ``kind``,
    ``area_mm2``, ``share`` (of the mesh), ``center`` (area-weighted), ``extent_mm`` (largest side
    of its bounding box), ``radius_mm`` (farthest vertex from the centre), ``n_faces``,
    ``sigma_median_mm`` / ``sigma_worst_mm`` (over its measured vertices, None when there are
    none) and ``faces`` (sorted int64 indices into ``F``).
    """
    V, F = as_mesh(V, F)
    nf = len(F)
    if not np.isfinite(tolerance_mm) or tolerance_mm <= 0:
        raise ValueError(f"the tolerance must be above 0 mm, got {tolerance_mm}")
    if not np.isfinite(k) or k <= 0:
        raise ValueError(f"k must be above 0, got {k}")
    if min_area_mm2 is not None and (not np.isfinite(min_area_mm2) or min_area_mm2 < 0):
        raise ValueError(f"the minimum area must be 0 mm^2 or more, got {min_area_mm2}")
    if max_areas < 0:
        raise ValueError(f"max_areas must be 0 or more, got {max_areas}")
    S = None
    if sigma is not None:
        S = np.asarray(sigma, dtype=np.float64).reshape(-1)
        if len(S) != len(V):
            raise ValueError(f"{len(S)} sigma values for {len(V)} vertices")
    fl = np.zeros(nf, dtype=bool)
    if filled is not None:
        fl = np.asarray(filled, dtype=bool).reshape(-1)
        if len(fl) != nf:
            raise ValueError(f"{len(fl)} filled flags for {nf} faces")

    area = face_areas(V, F)
    total = float(area.sum())
    if min_area_mm2 is None:
        med = float(np.median(area)) if nf else 0.0
        min_area_mm2 = max(MIN_AREA_FLOOR_MM2, MIN_AREA_FACES * med)
    if nf == 0 or total <= 0:
        return _result(S is not None, tolerance_mm, k, min_area_mm2, total, None, None, None, 0, [])

    # per face: not measured / uncertain
    measured_v = None
    if S is None:
        not_measured = fl
        uncertain = np.zeros(nf, dtype=bool)
    else:
        measured_v = np.isfinite(S) & (S > 0) & (S < SIGMA_NONE_MM)
        ok_v = [np.take(measured_v, F[:, j]) for j in range(3)]
        not_measured = fl | ~(ok_v[0] & ok_v[1] & ok_v[2])
        mean = (np.take(S, F[:, 0]) + np.take(S, F[:, 1]) + np.take(S, F[:, 2])) / 3.0
        uncertain = ~not_measured & (k * mean > tolerance_mm)
    fail = not_measured | uncertain
    nm_share = float(area[not_measured].sum()) / total
    if S is None:
        unc_share, ok_share = None, None
    else:
        unc_share = float(area[uncertain].sum()) / total
        ok_share = max(0.0, 1.0 - nm_share - unc_share)
    if not fail.any():
        return _result(S is not None, tolerance_mm, k, min_area_mm2, total, ok_share, unc_share, nm_share, 0, [])

    # areas: failing faces joined over shared edges
    fidx = np.flatnonzero(fail)
    n_grp, label = _face_groups(F[fidx], len(V))
    fa = area[fidx]
    g_area = np.bincount(label, weights=fa, minlength=n_grp)
    g_nm = np.bincount(label, weights=np.where(not_measured[fidx], fa, 0.0), minlength=n_grp)
    listed = np.flatnonzero(g_area >= min_area_mm2)
    listed = listed[np.argsort(-g_area[listed], kind="stable")]
    n_areas = len(listed)
    listed = listed[:max_areas]
    order = np.argsort(label, kind="stable")  # faces grouped by area, each group in face order
    start = np.concatenate([[0], np.cumsum(np.bincount(label, minlength=n_grp))])

    areas = []
    for g in listed.tolist():
        faces = fidx[order[start[g]:start[g + 1]]]
        Fg = F[faces]
        ag = area[faces]
        cen_f = (rows(V, Fg[:, 0]) + rows(V, Fg[:, 1]) + rows(V, Fg[:, 2])) / 3.0
        center = (cen_f * ag[:, None]).sum(axis=0) / ag.sum()
        verts = sorted_unique(Fg)
        P = rows(V, verts)
        radius = float(np.linalg.norm(P - center, axis=1).max())
        extent = float((P.max(axis=0) - P.min(axis=0)).max())
        med = worst = None
        if S is not None:
            good = S[verts[measured_v[verts]]]
            if len(good):
                med, worst = float(np.median(good)), float(good.max())
        areas.append({"kind": "not_measured" if 2.0 * g_nm[g] > g_area[g] else "uncertain",
                      "area_mm2": float(g_area[g]), "share": float(g_area[g] / total),
                      "center": [float(x) for x in center], "extent_mm": extent, "radius_mm": radius,
                      "n_faces": int(len(faces)), "sigma_median_mm": med, "sigma_worst_mm": worst, "faces": faces})
    return _result(S is not None, tolerance_mm, k, min_area_mm2, total, ok_share, unc_share, nm_share, n_areas, areas)
