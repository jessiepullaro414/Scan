"""Each vertex's confidence: its standard deviation in mm (*sigma*), as
:class:`ohs.track.recon_tsdf.ConfidenceTsdf` measures it and the PLY ``quality`` property
carries it (``S``, float32 (N,), one per vertex of ``V``).

* :data:`SIGMA_NONE_MM` (50 mm) marks a vertex nothing measured (a filled hole) and caps the rest.
* :func:`sigma_stats` sums a mesh's confidence up for the stats panel and the report.
* :func:`carry_sigma` follows the vertices through a mesh edit (:mod:`ohs.mesh`).
"""
from __future__ import annotations

import numpy as np

from ohs.mesh.topology import as_mesh, rows

__all__ = ["SIGMA_NONE_MM", "sigma_stats", "carry_sigma"]

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
      left, in order;
    * the rest (``remove_floaters``, ``decimate``) take the sigma of the nearest vertex before.

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
    if op in ("delete_faces", "keep_faces") and faces is not None:
        F = np.asarray(F, dtype=np.int64).reshape(-1, 3)
        keep = np.full(len(F), op == "delete_faces")
        keep[np.asarray(faces, dtype=np.int64).reshape(-1)] = op == "keep_faces"
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
