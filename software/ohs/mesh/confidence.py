"""Each vertex's confidence: its standard deviation in mm (*sigma*), as
:class:`ohs.track.recon_tsdf.ConfidenceTsdf` measures it and the PLY ``quality`` property
carries it (``S``, float32 (N,), one per vertex of ``V``).

* :data:`SIGMA_NONE_MM` (50 mm) marks a vertex nothing measured (a filled hole) and caps the rest.
* :func:`sigma_stats` sums a mesh's confidence up for the stats panel and the report.
* :func:`carry_sigma` follows the vertices through a mesh edit (:mod:`ohs.mesh`).
* :func:`carry_filled` follows each face's *filled* flag (made by hole filling, not measured:
  measuring tools skip such faces) through the same edits; :func:`filled_area` sums it up.
"""
from __future__ import annotations

import numpy as np

from ohs.mesh.topology import as_mesh, rows

__all__ = ["SIGMA_NONE_MM", "sigma_stats", "carry_sigma", "carry_filled", "filled_area"]

_FACE_OPS = ("delete_faces", "keep_faces", "remove_floaters")  # edits that leave a subset of the faces, in order

#: the sigma (mm) of a vertex nothing measured, and the cap of the others ("no confidence")
SIGMA_NONE_MM = 50.0


def sigma_stats(S: np.ndarray | None, V: np.ndarray | None = None, F: np.ndarray | None = None) -> dict | None:
    """``median`` and ``p95`` sigma (mm), and the shares ``within_0_1`` / ``within_0_3`` (sigma at
    most 0.1 / 0.3 mm) and ``not_measured`` (:data:`SIGMA_NONE_MM`). None without sigmas.

    With the faces, each vertex counts for a third of its triangles' area, so the numbers are
    shares of the surface (decimation thins the vertices unevenly); without, each vertex counts
    the same."""
    if S is None or not len(S):
        return None
    s = np.asarray(S, dtype=np.float64).reshape(-1)
    w = np.ones(len(s))
    if V is not None and F is not None and len(F) and len(V) == len(s):
        V, F = as_mesh(V, F)
        v0 = rows(V, F[:, 0])
        a = 0.5 * np.linalg.norm(np.cross(rows(V, F[:, 1]) - v0, rows(V, F[:, 2]) - v0), axis=1)
        wa = np.bincount(F.reshape(-1), weights=np.repeat(a / 3.0, 3), minlength=len(s))
        if wa.sum() > 0:
            w = wa
    order = np.argsort(s, kind="stable")
    cw = np.cumsum(w[order])
    total = float(cw[-1])

    def quantile(p: float) -> float:
        return float(s[order[min(int(np.searchsorted(cw, p * total)), len(s) - 1)]])

    def share(mask: np.ndarray) -> float:
        return round(float(w[mask].sum()) / total, 4)

    return {"median": round(quantile(0.5), 4), "p95": round(quantile(0.95), 4), "within_0_1": share(s <= 0.1),
            "within_0_3": share(s <= 0.3), "not_measured": share(s >= SIGMA_NONE_MM)}


def carry_sigma(op: str, V: np.ndarray, F: np.ndarray, S: np.ndarray | None, V2: np.ndarray,
                faces: np.ndarray | None = None) -> np.ndarray | None:
    """The sigmas of ``V2``, the vertices after the :mod:`ohs.mesh` operation ``op`` on ``(V, F)``.

    * ``smooth`` moves the vertices: each keeps its sigma;
    * ``fill_holes`` adds vertices after the old ones: nothing measured them (:data:`SIGMA_NONE_MM`);
    * ``delete_faces`` / ``keep_faces`` (given their ``faces``) keep the vertices of the faces
      left, in order; so does ``remove_floaters`` given the faces it kept (its ``return_kept``);
    * the rest (``decimate``, and ``remove_floaters`` without its faces) take the sigma of the
      nearest vertex before.

    None without sigmas (or when ``S`` doesn't fit ``V``)."""
    if S is None:
        return None
    S = np.asarray(S, dtype=np.float32).reshape(-1)
    if len(S) != len(V):
        return None
    n2 = len(V2)
    if op == "smooth" and n2 == len(V):
        return S.copy()
    if op == "fill_holes" and n2 >= len(V):
        return np.concatenate([S, np.full(n2 - len(V), SIGMA_NONE_MM, np.float32)])
    if op in _FACE_OPS and faces is not None:
        F = np.asarray(F, dtype=np.int64).reshape(-1, 3)
        if op == "remove_floaters" and len(faces) == len(F) and n2 == len(V):
            return S.copy()  # nothing was removed: the vertices are as they were
        keep = np.full(len(F), op == "delete_faces")
        keep[np.asarray(faces, dtype=np.int64).reshape(-1)] = op != "delete_faces"
        used = np.zeros(len(V), dtype=bool)
        used[F[keep].reshape(-1)] = True
        idx = np.flatnonzero(used)
        if len(idx) == n2:
            return S[idx]
    if n2 == 0:
        return np.zeros(0, np.float32)
    from scipy.spatial import cKDTree  # noqa: PLC0415 (only for these edits)

    _, idx = cKDTree(np.asarray(V, dtype=np.float64)).query(np.asarray(V2, dtype=np.float64), workers=-1)
    return S[idx]


def carry_filled(op: str, V: np.ndarray, F: np.ndarray, filled: np.ndarray | None, V2: np.ndarray,
                 F2: np.ndarray, faces: np.ndarray | None = None) -> np.ndarray | None:
    """Each face's *filled* flag after the :mod:`ohs.mesh` operation ``op`` turned ``(V, F)`` into
    ``(V2, F2)``; None when no face is filled.

    * ``fill_holes`` adds its faces after the old ones: those are filled;
    * ``smooth`` keeps the faces;
    * ``delete_faces`` / ``keep_faces`` (given their ``faces``) keep the faces left, in order; so
      does ``remove_floaters`` given the faces it kept;
    * the rest (``decimate``, and ``remove_floaters`` without its faces) take the flag of the old
      face nearest to each new face's centre."""
    n2 = len(F2)
    old = None if filled is None else np.asarray(filled, dtype=bool).reshape(-1)
    if old is not None and len(old) != len(F):
        old = None
    if op == "fill_holes" and n2 >= len(F):
        out = np.concatenate([old if old is not None else np.zeros(len(F), bool), np.ones(n2 - len(F), bool)])
        return out if out.any() else None
    if old is None or not old.any():
        return None
    if op == "smooth" and n2 == len(F):
        return old.copy()
    if op in _FACE_OPS and faces is not None:
        keep = np.full(len(F), op == "delete_faces")
        keep[np.asarray(faces, dtype=np.int64).reshape(-1)] = op != "delete_faces"
        if int(keep.sum()) == n2:
            out = old[keep]
            return out if out.any() else None
    if n2 == 0:
        return None
    from scipy.spatial import cKDTree  # noqa: PLC0415

    V, F = as_mesh(V, F)
    V2 = np.asarray(V2, dtype=np.float64)
    F2 = np.asarray(F2, dtype=np.int64).reshape(-1, 3)
    c_old = (rows(V, F[:, 0]) + rows(V, F[:, 1]) + rows(V, F[:, 2])) / 3.0
    c_new = (rows(V2, F2[:, 0]) + rows(V2, F2[:, 1]) + rows(V2, F2[:, 2])) / 3.0
    _, idx = cKDTree(c_old).query(c_new, workers=-1)
    out = old[idx]
    return out if out.any() else None


def filled_area(V: np.ndarray, F: np.ndarray, filled: np.ndarray | None) -> float:
    """Area of the filled faces, mm^2 (0 without)."""
    if filled is None or not len(F):
        return 0.0
    m = np.asarray(filled, dtype=bool).reshape(-1)
    if len(m) != len(F) or not m.any():
        return 0.0
    V, F = as_mesh(V, F)
    Fm = F[m]
    v0 = rows(V, Fm[:, 0])
    return float(0.5 * np.linalg.norm(np.cross(rows(V, Fm[:, 1]) - v0, rows(V, Fm[:, 2]) - v0), axis=1).sum())
