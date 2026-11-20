"""A second pass that joins the first (FEATURE_PLAN N2): did the areas the user asked for get better?

The Check answer of the first scan lists areas that miss a tolerance; the user scans some of them
again, and the pass is aligned and merged with the first scan (:mod:`ohs.server.passes`). The merged
mesh is in the first scan's world, made again from the raw frames of both, so its vertices are not
the first mesh's: an area is found on it by where it lies.

* :func:`area_samples`: what is kept of the asked areas when the pass starts: each face's centre,
  normal and area, and which area it belongs to (the first mesh may be edited afterwards).
* :func:`area_progress`: for each asked area, the share of it (by area) that passes the tolerance
  on the merged mesh, with the Check's own rule (:func:`ohs.mesh.check.weak_areas`: a face passes
  when it is measured, not filled, and ``k`` times the mean sigma of its vertices is at most the
  tolerance).

**Finding a face of the first mesh on the merged one** (the only rule here with a size in mm): of the
faces of the merged mesh whose centres lie within :data:`MATCH_MM` and whose normals are within
:data:`MATCH_ANGLE_DEG` of the first face's, the one whose plane passes nearest to the first face's
centre. Both tests keep a thin part right: the two sides of a plate a fraction of a millimetre thick
face opposite ways, and a plate lying on its table, facing the same way as the table, lies in
another plane than the table 0.3 mm below it, even where the table's face centres are nearer than
the plate's own (``tests/test_guide_passes.py``: a 0.3 mm plate on its table, and seen from both
sides, with the merged mesh at another resolution). Two layers closer than the surfaces' own noise
(their sigma) cannot be told apart this way; neither can the scan tell them apart. Where the merged
plate does not reach as far as the first one did, the surface under it can stand for it, up to
:data:`MATCH_MM` from its rim. A face with no such neighbour counts as "not found" (the merge
did not keep that surface, or the two scans' surfaces lie more than :data:`MATCH_MM` apart); it is
reported, never hidden. Nothing is removed from any mesh: this only reads.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np

from ohs.mesh.topology import as_mesh, face_areas, rows

__all__ = ["MATCH_MM", "MATCH_ANGLE_DEG", "PASSES_SHARE", "area_samples", "area_progress", "common_surface", "face_pass",
           "match_faces"]

#: how far a face of the merged mesh may lie from a face of the first mesh to stand for it: the
#: merged model is made again from the same frames, so the same surface lies within a voxel (1 mm at
#: 1280x800, 2 mm at 640x400) plus the alignment's error (about 1 mm at 640x400, VALIDATION.md)
MATCH_MM = 2.5
#: and how far its normal may turn (the two sides of a thin plate face 180 degrees apart)
MATCH_ANGLE_DEG = 60.0
#: an asked area "passes now" when this share of it passes (the rest is "partly")
PASSES_SHARE = 0.9
KNN = 24  # faces looked at per point (a 2 mm voxel mesh has about 8 face centres within 2.5 mm per surface)
TIE = 0.05  # how much the distance between face centres counts beside the distance to the plane


def _face_normals(V: np.ndarray, F: np.ndarray) -> np.ndarray:
    a, b, c = rows(V, F[:, 0]), rows(V, F[:, 1]), rows(V, F[:, 2])
    n = np.cross(b - a, c - a)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    return np.divide(n, ln, out=np.zeros_like(n), where=ln > 0)


def area_samples(V: np.ndarray, F: np.ndarray, faces: Sequence[np.ndarray]) -> dict:
    """The faces of each asked area (``faces[i]``: indices into ``F``) as ``{"points" (m,3), "normals"
    (m,3), "weights" (m,) mm^2, "area" (m,) int}``: the face centres, unit normals and areas."""
    V, F = as_mesh(V, F)
    idx, lab = [], []
    for i, f in enumerate(faces):
        f = np.asarray(f, dtype=np.int64).reshape(-1)
        if len(f) and (f.min() < 0 or f.max() >= len(F)):
            raise ValueError(f"area {i} refers to faces this mesh does not have")
        idx.append(f)
        lab.append(np.full(len(f), i, dtype=np.int32))
    sel = np.concatenate(idx) if idx else np.zeros(0, dtype=np.int64)
    Fs = F[sel]
    P = (rows(V, Fs[:, 0]) + rows(V, Fs[:, 1]) + rows(V, Fs[:, 2])) / 3.0 if len(Fs) else np.zeros((0, 3))
    return {"points": P.astype(np.float64), "normals": _face_normals(V, Fs) if len(Fs) else np.zeros((0, 3)),
            "weights": face_areas(V, Fs) if len(Fs) else np.zeros(0),
            "area": np.concatenate(lab) if lab else np.zeros(0, dtype=np.int32)}


def face_pass(F: np.ndarray, sigma: np.ndarray | None, filled: np.ndarray | None, tolerance_mm: float,
              k: float = 2.0) -> np.ndarray:
    """Per face: passes the tolerance, as the Check decides it (``weak_areas``'s face states)."""
    from ohs.mesh.check import _face_states

    fl = np.zeros(len(F), dtype=bool) if filled is None else np.asarray(filled, dtype=bool).reshape(-1)
    if len(fl) != len(F):
        fl = np.zeros(len(F), dtype=bool)
    S = None if sigma is None else np.asarray(sigma, dtype=np.float64).reshape(-1)
    if S is None:
        return np.zeros(len(F), dtype=bool)  # no sigma: nothing can be said to pass
    _, not_measured, uncertain = _face_states(F, S, fl, float(tolerance_mm), float(k))
    return ~(not_measured | uncertain)


def match_faces(P: np.ndarray, Nf: np.ndarray, V: np.ndarray, F: np.ndarray, max_mm: float = MATCH_MM,
                max_angle_deg: float = MATCH_ANGLE_DEG, chunk: int = 100_000) -> tuple[np.ndarray, np.ndarray]:
    """The rule of the module docstring for points ``P`` with unit normals ``Nf`` on the mesh ``(V, F)``:
    ``(found (m,) bool, the face each found point stands on (m,) int)``. In chunks, so a mesh of a million
    faces does not need gigabytes."""
    from scipy.spatial import cKDTree

    P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
    Nf = np.asarray(Nf, dtype=np.float64).reshape(-1, 3)
    found = np.zeros(len(P), dtype=bool)
    pick = np.zeros(len(P), dtype=np.int64)
    if not len(F) or not len(P):
        return found, pick
    C = (rows(V, F[:, 0]) + rows(V, F[:, 1]) + rows(V, F[:, 2])) / 3.0
    Nm = _face_normals(V, F)
    tree = cKDTree(C)
    cos_min = math.cos(math.radians(max_angle_deg))
    kq = min(KNN, len(C))
    for a in range(0, len(P), chunk):
        Pc, Nc = P[a:a + chunk], Nf[a:a + chunk]
        # the faces within reach that face the same way; of those, the one whose plane passes nearest to the
        # point (the same surface lies in it; a layer of the part a fraction of a millimetre away does not),
        # nearer face centres first among equals
        d, j = tree.query(Pc, k=kq, distance_upper_bound=float(max_mm))
        d = d.reshape(len(Pc), kq)
        j = j.reshape(len(Pc), kq)
        inside = np.isfinite(d)
        jj = np.where(inside, j, 0)
        same_way = inside & (np.einsum("pkc,pc->pk", Nm[jj], Nc) >= cos_min)
        off = np.abs(np.einsum("pkc,pkc->pk", Nm[jj], Pc[:, None, :] - C[jj]))  # from the point to each face's plane
        key = np.where(same_way, off + TIE * np.where(inside, d, 0.0), np.inf)
        best = np.argmin(key, axis=1)
        found[a:a + chunk] = same_way[np.arange(len(Pc)), best]
        pick[a:a + chunk] = jj[np.arange(len(Pc)), best]
    return found, pick


def common_surface(first: tuple, merged: tuple, tolerance_mm: float, k: float = 2.0, max_mm: float = MATCH_MM,
                   max_angle_deg: float = MATCH_ANGLE_DEG) -> dict:
    """Before and after on the surface both meshes hold: ``first`` and ``merged`` are ``(V, F, sigma, filled)``.
    Every face of the first mesh is looked for on the merged one (:func:`match_faces`). Returns (shares of the
    first mesh's surface area): ``found_share`` (on the merged mesh), ``missing_share`` / ``missing_mm2`` (not
    on it), and on the found surface ``before_ok`` (passes on the first mesh) and ``after_ok`` (passes on the
    merged mesh); the last two None without sigma. A difference of the whole meshes' shares that comes from
    surface one of them does not hold is not in these numbers."""
    V1, F1 = as_mesh(first[0], first[1])
    V2, F2 = as_mesh(merged[0], merged[1])
    s1 = area_samples(V1, F1, [np.arange(len(F1))])
    W = s1["weights"]
    tot = float(W.sum())
    found, pick = match_faces(s1["points"], s1["normals"], V2, F2, max_mm, max_angle_deg)
    ok1 = face_pass(F1, first[2], first[3], tolerance_mm, k)
    ok2 = face_pass(F2, merged[2], merged[3], tolerance_mm, k)
    wf = float(W[found].sum())
    out = {"area_mm2": tot, "found_share": wf / tot if tot > 0 else 0.0,
           "missing_share": 1.0 - wf / tot if tot > 0 else 0.0, "missing_mm2": tot - wf,
           "before_ok": None, "after_ok": None}
    if wf > 0 and first[2] is not None:
        out["before_ok"] = float(W[found & ok1].sum()) / wf
    if wf > 0 and merged[2] is not None:
        out["after_ok"] = float(W[found & ok2[pick]].sum()) / wf
    return out


def area_progress(samples: dict, V: np.ndarray, F: np.ndarray, sigma: np.ndarray | None, filled: np.ndarray | None,
                  tolerance_mm: float, k: float = 2.0, max_mm: float = MATCH_MM,
                  max_angle_deg: float = MATCH_ANGLE_DEG) -> list[dict]:
    """For each asked area of ``samples`` (:func:`area_samples`), on the mesh ``(V, F)`` with its sigma and
    filled flags: ``{"area", "share_ok", "found_share", "state"}``. ``share_ok``: of the area, the share
    that passes on this mesh (None without sigma); ``found_share``: the share found on this mesh at all
    (module docstring); ``state``: ``"passes"`` (at least :data:`PASSES_SHARE` passes), ``"partly"``,
    ``"weak"`` (none passes), ``"not_found"`` (none of it is on this mesh) or ``"unknown"`` (no sigma)."""
    V, F = as_mesh(V, F)
    lab = np.asarray(samples["area"], dtype=np.int64)
    P = np.asarray(samples["points"], dtype=np.float64).reshape(-1, 3)
    Nf = np.asarray(samples["normals"], dtype=np.float64).reshape(-1, 3)
    W = np.asarray(samples["weights"], dtype=np.float64).reshape(-1)
    n_areas = int(lab.max()) + 1 if len(lab) else 0
    ok_face = face_pass(F, sigma, filled, tolerance_mm, k)
    found, pick = match_faces(P, Nf, V, F, max_mm, max_angle_deg)
    passes = found & ok_face[pick] if len(F) else np.zeros(len(P), dtype=bool)
    out = []
    has_sigma = sigma is not None
    for a in range(n_areas):
        m = lab == a
        w = W[m]
        tot = float(w.sum())
        if tot <= 0:
            out.append({"area": a, "share_ok": None, "found_share": 0.0, "state": "not_found"})
            continue
        fs = float(w[found[m]].sum()) / tot
        so = float(w[passes[m]].sum()) / tot if has_sigma else None
        if fs <= 0:
            state = "not_found"
        elif so is None:
            state = "unknown"
        elif so >= PASSES_SHARE:
            state = "passes"
        elif so > 0:
            state = "partly"
        else:
            state = "weak"
        out.append({"area": a, "share_ok": so, "found_share": fs, "state": state})
    return out
