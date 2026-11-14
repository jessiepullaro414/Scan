"""Removing parts of a mesh: floaters (small disconnected pieces) and face selections.

* :func:`remove_floaters` drops connected pieces that are small: fewer
  than ``min_triangles`` triangles, fewer than ``min_fraction`` of all triangles, or a bounding-box
  diagonal under ``min_size_mm``; or, with ``keep_largest_only``, every piece but the one with
  the most triangles. The largest piece is always kept, so the scan itself never disappears.
  A piece is faces joined through shared *edges*: a lone triangle or a flap that touches the
  surface at one vertex is a piece of its own and is judged by its own size (on simulated scans
  such pieces lie 5 to 7 times further from the true surface than the surface they hang on;
  ``VALIDATION.md``, "Pieces that hang on by a vertex").
* :func:`delete_faces` / :func:`keep_faces` drop or keep faces by index (a selection made in the
  viewer, in the indices of the mesh it was made on).

Vertices that no face uses any more are removed and the faces re-indexed; the order of the kept
vertices and faces is preserved.
"""
from __future__ import annotations

import logging

import numpy as np

from ohs.mesh.topology import as_mesh, compact, edge_components

__all__ = ["remove_floaters", "delete_faces", "keep_faces", "piece_sizes"]

log = logging.getLogger(__name__)


def piece_sizes(V: np.ndarray, F: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(face labels (M,), triangles per piece, bounding-box diagonal per piece in mm)``; a piece
    is faces joined through shared edges (module docstring)."""
    n_comp, flab = edge_components(F, len(V))
    tri = np.bincount(flab, minlength=n_comp)
    diag = np.zeros(n_comp)
    if n_comp == 0:
        return flab, tri, diag
    # a vertex can be in two pieces, so the boxes are taken over the faces. The largest piece is
    # nearly all of a scan: its box needs no sorting, and only the other pieces' faces are sorted.
    largest = int(np.argmax(tri))
    is_largest = flab == largest
    P = np.take(V, F[is_largest].reshape(-1), axis=0)
    diag[largest] = np.linalg.norm(P.max(axis=0) - P.min(axis=0))
    rest = np.flatnonzero(~is_largest)
    if len(rest):
        lab = flab[rest]
        order = np.argsort(lab, kind="stable")
        rest, lab = rest[order], lab[order]
        starts = np.flatnonzero(np.r_[True, lab[1:] != lab[:-1]])
        P = np.take(V, F[rest].reshape(-1), axis=0).reshape(-1, 3, 3)
        lo = np.minimum.reduceat(P.min(axis=1), starts, axis=0)
        hi = np.maximum.reduceat(P.max(axis=1), starts, axis=0)
        diag[lab[starts]] = np.linalg.norm(hi - lo, axis=1)
    return flab, tri, diag


def remove_floaters(V: np.ndarray, F: np.ndarray, min_triangles: int | None = None, min_size_mm: float | None = None,
                    keep_largest_only: bool = False, min_fraction: float | None = None,
                    return_kept: bool = False) -> tuple:
    """Drop small disconnected pieces (see the module docstring). Returns ``(V, F)``; with
    ``return_kept`` also the indices of the faces kept, in order (what
    :func:`ohs.mesh.carry_sigma` needs to follow the vertices without a search)."""
    V, F = as_mesh(V, F)
    if len(F) == 0:
        return (V.copy(), F.copy(), np.zeros(0, dtype=np.int64)) if return_kept else (V.copy(), F.copy())
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
        return (V.copy(), F.copy(), np.arange(len(F), dtype=np.int64)) if return_kept else (V.copy(), F.copy())
    fkeep = keep[flab]
    V2, F2, _ = compact(V, F[fkeep])
    log.debug("remove_floaters: kept %d of %d pieces (%d of %d triangles)", int(keep.sum()), len(keep), len(F2), len(F))
    V2 = V2.copy() if V2 is V else V2
    return (V2, F2, np.flatnonzero(fkeep)) if return_kept else (V2, F2)


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
