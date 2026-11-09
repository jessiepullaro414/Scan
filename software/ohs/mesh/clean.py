"""Removing parts of a mesh: floaters (small disconnected pieces) and face selections.

* :func:`remove_floaters` drops connected pieces (faces sharing a vertex) that are small: fewer
  than ``min_triangles`` triangles, fewer than ``min_fraction`` of all triangles, or a bounding-box
  diagonal under ``min_size_mm``; or, with ``keep_largest_only``, every piece but the one with
  the most triangles. The largest piece is always kept, so the scan itself never disappears.
* :func:`delete_faces` / :func:`keep_faces` drop or keep faces by index (a selection made in the
  viewer, in the indices of the mesh it was made on).

Vertices that no face uses any more are removed and the faces re-indexed; the order of the kept
vertices and faces is preserved.
"""
from __future__ import annotations

import logging

import numpy as np

from ohs.mesh.topology import as_mesh, compact, components

__all__ = ["remove_floaters", "delete_faces", "keep_faces", "piece_sizes"]

log = logging.getLogger(__name__)


def piece_sizes(V: np.ndarray, F: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(face labels (M,), triangles per piece, bounding-box diagonal per piece in mm)``."""
    n_comp, vlab, flab = components(F, len(V))
    tri = np.bincount(flab, minlength=n_comp)
    used = np.flatnonzero(vlab >= 0)
    lab = vlab[used]
    order = np.argsort(lab, kind="stable")
    lab_s = lab[order]
    starts = np.flatnonzero(np.r_[True, lab_s[1:] != lab_s[:-1]])
    P = V[used[order]]
    lo = np.minimum.reduceat(P, starts, axis=0)
    hi = np.maximum.reduceat(P, starts, axis=0)
    diag = np.zeros(n_comp)
    diag[lab_s[starts]] = np.linalg.norm(hi - lo, axis=1)
    return flab, tri, diag


def remove_floaters(V: np.ndarray, F: np.ndarray, min_triangles: int | None = None, min_size_mm: float | None = None,
                    keep_largest_only: bool = False, min_fraction: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Drop small disconnected pieces (see the module docstring). Returns ``(V, F)``."""
    V, F = as_mesh(V, F)
    if len(F) == 0:
        return V.copy(), F.copy()
    flab, tri, diag = piece_sizes(V, F)
    largest = int(np.argmax(tri))
    if keep_largest_only:
        keep = np.zeros(len(tri), dtype=bool)
    else:
        keep = np.ones(len(tri), dtype=bool)
        if min_triangles is not None:
            keep &= tri >= int(min_triangles)
        if min_fraction is not None:
            keep &= tri >= float(min_fraction) * len(F)
        if min_size_mm is not None:
            keep &= diag >= float(min_size_mm)
    keep[largest] = True
    if keep.all():
        return V.copy(), F.copy()
    V2, F2, _ = compact(V, F[keep[flab]])
    log.debug("remove_floaters: kept %d of %d pieces (%d of %d triangles)", int(keep.sum()), len(keep), len(F2), len(F))
    return V2.copy() if V2 is V else V2, F2


def _face_index(faces: np.ndarray | list[int], n_faces: int) -> np.ndarray:
    idx = np.asarray(faces).reshape(-1)
    if idx.size == 0:
        return np.zeros(0, dtype=np.int64)
    if not np.issubdtype(idx.dtype, np.integer):
        raise ValueError("face indices must be integers")
    idx = idx.astype(np.int64)
    if idx.min() < 0 or idx.max() >= n_faces:
        raise ValueError(f"face index out of range: the mesh has {n_faces} faces")
    return idx


def delete_faces(V: np.ndarray, F: np.ndarray, faces: np.ndarray | list[int]) -> tuple[np.ndarray, np.ndarray]:
    """Drop the faces with these indices (duplicates allowed), then unused vertices."""
    V, F = as_mesh(V, F)
    idx = _face_index(faces, len(F))
    keep = np.ones(len(F), dtype=bool)
    keep[idx] = False
    V2, F2, _ = compact(V, F[keep])
    return (V2.copy() if V2 is V else V2), F2


def keep_faces(V: np.ndarray, F: np.ndarray, faces: np.ndarray | list[int]) -> tuple[np.ndarray, np.ndarray]:
    """Keep only the faces with these indices (in their original order), then their vertices."""
    V, F = as_mesh(V, F)
    idx = _face_index(faces, len(F))
    keep = np.zeros(len(F), dtype=bool)
    keep[idx] = True
    V2, F2, _ = compact(V, F[keep])
    return (V2.copy() if V2 is V else V2), F2
