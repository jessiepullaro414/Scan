"""Smoothing: Taubin (shrink-free low-pass) and feature-preserving bilateral normal filtering.

``strength`` (0..1) is the one knob of :func:`smooth`; 0 changes nothing.

**Taubin** (*Curve and surface smoothing without shrinkage*, 1995): alternating Laplacian steps
``lambda = 0.5`` and ``mu = -0.53`` with inverse-edge-length weights, ``1 + 49 * strength``
iterations: the same filter as Open3D's ``filter_smooth_taubin``, written as two sparse matrices
so that edge vertices can be treated apart (and about three times faster on 1M triangles). With
``keep_edges`` an edge is sharp when the normals of its two faces, after an edge-keeping
bilateral filter that removes the noise, differ by more than :data:`SHARP_EDGE_DEG`; a vertex on
two sharp edges is smoothed along them only, and corners are held.

**Bilateral** (Zheng, Fu, Au, Tai, *Bilateral Normal Filtering for Mesh Denoising*, 2011, local
scheme). First the face normals are filtered: each new normal is the normalised sum over the
face and its edge neighbours of ``area_j * Ws(|c_i - c_j|) * Wr(|n_i - n_j|) * n_j`` with Gaussian
``Ws`` (sigma = mean distance between neighbouring face centroids) and ``Wr`` (sigma_r). Normals
across a sharp edge differ by about 1.4, so a small ``sigma_r`` keeps them apart and the edge
survives; ``keep_edges=False`` uses a large ``sigma_r`` (plain normal smoothing that rounds edges).
Then the vertices follow the filtered normals (Sun et al. 2007): each vertex moves by the mean
over its faces of ``n_f (n_f . (c_f - x))``, repeated; with the normals fixed this is
``(B (n t) - S x) / count`` per vertex, ``t_f = n_f . c_f`` and ``S = sum n n^T`` over the vertex's
faces, so one iteration is two sparse products. ``strength`` sets the iterations (normals
``2 + 18 s``, vertices ``5 + 15 s``) and ``sigma_r`` (``0.25 + 0.2 s`` with ``keep_edges``).
"""
from __future__ import annotations

import logging
import math
from typing import Callable

import numpy as np

from ohs.mesh.topology import EdgeTable, as_mesh, edge_lengths, edge_table, face_normals, rows, sorted_unique

__all__ = ["smooth", "bilateral_normals", "sharp_edges", "sharp_vertices", "METHODS", "SHARP_EDGE_DEG"]

log = logging.getLogger(__name__)

METHODS = ("taubin", "bilateral")
SHARP_EDGE_DEG = 45.0
TAUBIN_LAMBDA, TAUBIN_MU = 0.5, -0.53
Progress = Callable[[float, str], None]


def _taubin_iterations(strength: float) -> int:
    return int(round(1 + 49 * strength))


def _bilateral_params(strength: float, keep_edges: bool) -> tuple[int, int, float]:
    kn = int(round(2 + 18 * strength))
    kv = int(round(5 + 15 * strength))
    sigma_r = 0.25 + 0.2 * strength if keep_edges else 2.0
    return kn, kv, sigma_r


# --------------------------------------------------------------------------- normals
def _face_pairs(et: EdgeTable, n_faces: int) -> tuple[np.ndarray, np.ndarray]:
    """Distinct pairs of faces sharing a manifold edge, as two arrays ``(f1 < f2)``."""
    e = np.flatnonzero(et.counts == 2)
    s = et.edge_start[e]
    a, b = et.he_order[s] // 3, et.he_order[s + 1] // 3
    f1, f2 = np.minimum(a, b), np.maximum(a, b)
    keep = f1 != f2
    key = sorted_unique(f1[keep] * n_faces + f2[keep])
    return key // n_faces, key % n_faces


def bilateral_normals(V: np.ndarray, F: np.ndarray, iterations: int, sigma_r: float, *, et: EdgeTable | None = None,
                      progress: Progress | None = None, p0: float = 0.0, p1: float = 1.0) -> np.ndarray:
    """Bilaterally filtered unit face normals (M,3) (see the module docstring)."""
    from scipy.sparse import csr_matrix

    n, area = face_normals(V, F)
    M = len(F)
    if iterations <= 0 or M == 0:
        return n
    if et is None:
        et = edge_table(F, len(V))
    f1, f2 = _face_pairs(et, M)
    P = len(f1)
    if P == 0:
        return n
    cent = (rows(V, F[:, 0]) + rows(V, F[:, 1]) + rows(V, F[:, 2])) / 3.0
    dc = rows(cent, f1) - rows(cent, f2)
    d2 = np.einsum("ij,ij->i", dc, dc)
    sigma_s2 = max(float(np.mean(np.sqrt(d2))) ** 2, 1e-12)
    ws = np.exp(-d2 / (2.0 * sigma_s2))
    # directed pairs (i <- j) in CSR order: data[k] is the weight of face cols[k] in row i's sum
    S = csr_matrix((np.arange(1, 2 * P + 1, dtype=np.float64), (np.concatenate([f1, f2]), np.concatenate([f2, f1]))),
                   shape=(M, M))
    perm = S.data.astype(np.int64) - 1
    pid = perm % P
    base = area[S.indices] * ws[pid]
    inv_r2 = 1.0 / (float(sigma_r) ** 2)
    self_w = area[:, None]
    for it in range(int(iterations)):
        dot = np.einsum("ij,ij->i", rows(n, f1), rows(n, f2))
        wr = np.exp((dot - 1.0) * inv_r2)  # |n1 - n2|^2 / (2 sigma_r^2) = (1 - dot) / sigma_r^2
        S.data = base * np.take(wr, pid)
        acc = n * self_w + S @ n
        nrm = np.sqrt(np.einsum("ij,ij->i", acc, acc))[:, None]
        n = np.divide(acc, nrm, out=n, where=nrm > 0)
        if progress is not None:
            progress(p0 + (p1 - p0) * (it + 1) / iterations, f"Filtering normals ({it + 1} of {iterations})")
    return n


def sharp_edges(V: np.ndarray, F: np.ndarray, angle_deg: float = SHARP_EDGE_DEG, *,
                et: EdgeTable | None = None) -> np.ndarray:
    """Bool (E,) over ``et``'s edges: the filtered normals of the two faces differ by more than
    ``angle_deg`` (the bilateral filter first removes the noise but keeps real edges)."""
    if et is None:
        et = edge_table(F, len(V))
    out = np.zeros(et.n_edges, dtype=bool)
    if len(F) == 0:
        return out
    n = bilateral_normals(V, F, 8, 0.3, et=et)
    e = np.flatnonzero(et.counts == 2)
    s = et.edge_start[e]
    f1, f2 = et.he_order[s] // 3, et.he_order[s + 1] // 3
    out[e] = np.einsum("ij,ij->i", rows(n, f1), rows(n, f2)) < math.cos(math.radians(angle_deg))
    return out


def sharp_vertices(V: np.ndarray, F: np.ndarray, angle_deg: float = SHARP_EDGE_DEG, *,
                   et: EdgeTable | None = None) -> np.ndarray:
    """Bool (N,): vertices on a sharp edge (see :func:`sharp_edges`)."""
    if et is None:
        et = edge_table(F, len(V))
    sharp = sharp_edges(V, F, angle_deg, et=et)
    out = np.zeros(len(V), dtype=bool)
    out[et.v0[sharp]] = True
    out[et.v1[sharp]] = True
    return out


# --------------------------------------------------------------------------- taubin
def _taubin(V: np.ndarray, iterations: int, sharp: np.ndarray | None, et: EdgeTable,
            progress: Progress | None) -> np.ndarray:
    """Taubin smoothing; with ``sharp`` (bool per edge) a vertex on exactly two sharp edges is
    smoothed along them only (the edge line stays straight but loses its noise), and a vertex
    where one or three or more sharp edges meet (a corner, a line end) is held."""
    from scipy.sparse import coo_matrix, diags

    N = len(V)
    w = 1.0 / np.maximum(edge_lengths(V, et), 1e-9)
    use_uv = np.ones(et.n_edges, dtype=bool)  # entry u <- v
    use_vu = np.ones(et.n_edges, dtype=bool)  # entry v <- u
    held = np.zeros(N, dtype=bool)
    if sharp is not None and sharp.any():
        ns = np.bincount(et.v0[sharp], minlength=N) + np.bincount(et.v1[sharp], minlength=N)
        feature = ns > 0
        held = feature & (ns != 2)
        use_uv = ~feature[et.v0] | sharp
        use_vu = ~feature[et.v1] | sharp
    rows = np.concatenate([et.v0[use_uv], et.v1[use_vu]])
    cols = np.concatenate([et.v1[use_uv], et.v0[use_vu]])
    W = coo_matrix((np.concatenate([w[use_uv], w[use_vu]]), (rows, cols)), shape=(N, N)).tocsr()
    tot = np.asarray(W.sum(axis=1)).reshape(-1)
    scale = np.divide(1.0, tot, out=np.zeros(N), where=tot > 0)
    scale[held] = 0.0
    Wn = diags(scale) @ W  # rows of held / isolated vertices are zero
    moving = (scale > 0).astype(np.float64)
    # x <- x + f (Wn x - x) on moving vertices:  x <- (I - f diag(moving)) x + f Wn x
    ops = [(diags(1.0 - f * moving) + f * Wn).tocsr() for f in (TAUBIN_LAMBDA, TAUBIN_MU)]
    X = V.copy()
    for it in range(iterations):
        X = ops[1] @ (ops[0] @ X)
        if progress is not None and (it % 5 == 4 or it == iterations - 1):
            progress((it + 1) / iterations, f"Smoothing ({it + 1} of {iterations})")
    return X


# --------------------------------------------------------------------------- bilateral
def _vertex_update(V: np.ndarray, F: np.ndarray, n: np.ndarray, iterations: int, progress: Progress | None,
                   p0: float, p1: float) -> np.ndarray:
    from scipy.sparse import csr_matrix

    N, M = len(V), len(F)
    Ft = csr_matrix((np.full(3 * M, 1.0 / 3.0), F.reshape(-1), np.arange(0, 3 * M + 1, 3)), shape=(M, N))
    B = (Ft.T * 3.0).tocsr()  # vertex x face incidence
    cnt = np.asarray(B.sum(axis=1)).reshape(-1)
    inv = np.divide(1.0, cnt, out=np.zeros(N), where=cnt > 0)
    nn = np.stack([n[:, 0] * n[:, 0], n[:, 0] * n[:, 1], n[:, 0] * n[:, 2],
                   n[:, 1] * n[:, 1], n[:, 1] * n[:, 2], n[:, 2] * n[:, 2]], axis=1)
    Sv = B @ nn  # per vertex: xx xy xz yy yz zz of sum n n^T
    X = V.copy()
    for it in range(int(iterations)):
        c = Ft @ X
        t = np.einsum("ij,ij->i", n, c)
        b = B @ (n * t[:, None])
        x, y, z = X[:, 0], X[:, 1], X[:, 2]
        sx = np.stack([Sv[:, 0] * x + Sv[:, 1] * y + Sv[:, 2] * z,
                       Sv[:, 1] * x + Sv[:, 3] * y + Sv[:, 4] * z,
                       Sv[:, 2] * x + Sv[:, 4] * y + Sv[:, 5] * z], axis=1)
        X += (b - sx) * inv[:, None]
        if progress is not None:
            progress(p0 + (p1 - p0) * (it + 1) / iterations, f"Moving vertices ({it + 1} of {iterations})")
    return X


# --------------------------------------------------------------------------- entry
def smooth(V: np.ndarray, F: np.ndarray, method: str = "taubin", strength: float = 0.5, keep_edges: bool = True, *,
           limit: np.ndarray | None = None, progress: Progress | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Smooth the mesh (see the module docstring). Faces are unchanged; returns ``(V, F)``.

    ``limit`` (N,) is the most each vertex may move, mm: e.g. twice its sigma, so that smoothing
    removes noise where the scan is uncertain and leaves the confident surface where it was
    (a longer move is shortened along its direction)."""
    if method not in METHODS:
        raise ValueError(f"unknown smoothing method {method!r}; use {' or '.join(METHODS)}")
    s = float(strength)
    if not math.isfinite(s) or not 0.0 <= s <= 1.0:
        raise ValueError("strength must be between 0 and 1")
    V, F = as_mesh(V, F)
    if limit is not None:
        limit = np.asarray(limit, dtype=np.float64).reshape(-1)
        if len(limit) != len(V):
            raise ValueError("limit must have one value per vertex")
    if s == 0.0 or len(F) == 0:
        return V.copy(), F.copy()
    et = edge_table(F, len(V))
    if method == "taubin":
        sharp = sharp_edges(V, F, et=et) if keep_edges else None
        X = _taubin(V, _taubin_iterations(s), sharp, et, progress)
    else:
        kn, kv, sigma_r = _bilateral_params(s, bool(keep_edges))
        n = bilateral_normals(V, F, kn, sigma_r, et=et, progress=progress, p0=0.0, p1=0.5)
        X = _vertex_update(V, F, n, kv, progress, 0.5, 1.0)
    if limit is not None:
        d = X - V
        dl = np.sqrt(np.einsum("ij,ij->i", d, d))
        scale = np.minimum(1.0, np.divide(np.maximum(limit, 0.0), dl, out=np.ones_like(dl), where=dl > 0))
        X = V + d * scale[:, None]
    return X, F.copy()
