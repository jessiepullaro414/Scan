"""Mesh topology shared by the mesh operations: edges, boundary loops, connected pieces.

Meshes are ``(V, F)``: ``V`` float64 (N,3) in mm, ``F`` int64 (M,3) vertex indices, consistently
oriented (right-hand rule, as marching cubes and :mod:`ohs.track.export` write them).

**Half-edges.** Half-edge ``h = 3 f + j`` runs from ``F[f, j]`` to ``F[f, (j + 1) % 3]``. The
undirected edges (:class:`EdgeTable`) come from one sort of the ``3 M`` half-edge keys; an edge
with one face is a *boundary* edge, one with more than two faces is *non-manifold*.

**Boundary loops** (:func:`boundary_loops`) chain the boundary half-edges head to tail. Where
two loops touch at one vertex (a "bowtie"), the vertex has two outgoing boundary half-edges; the
right one is found by walking around the vertex through its fan of faces, so the loops come out
simple. A chain that cannot be closed (inconsistently oriented faces) is kept as an open loop.

**Outer borders** (:func:`classify_loops`). A scan of one side of a part is an open surface: its
longest boundary loop is the edge of the scanned area, which must never be counted or filled as
a hole. Each connected piece's longest loop is its outer border, unless the piece is nearly
closed: when the loop could be capped with less than :data:`BORDER_RATIO` of the piece's area
(``perimeter^2 / 4 pi < BORDER_RATIO * area``), the piece is a closed object with holes and that
loop is a hole too. So a plane strip keeps its border, and a sphere with a few holes has only
holes.

Everything is vectorised with numpy / scipy except the walk along the boundary loops (linear in
the number of boundary edges). Two numpy habits are avoided on purpose in the hot paths:
``np.unique`` (hash based in numpy 2, about 75 times slower than a sort on millions of keys) and
fancy row indexing of (N,3) arrays (``np.take`` is about 3 times faster): see :func:`rows`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

__all__ = [
    "BORDER_RATIO",
    "EdgeTable",
    "Loops",
    "rows",
    "sorted_unique",
    "as_mesh",
    "edge_table",
    "boundary_loops",
    "classify_loops",
    "components",
    "edge_components",
    "face_areas",
    "face_normals",
    "vertex_normals",
    "mean_edge_length",
    "vertex_edge_length",
    "compact",
]

log = logging.getLogger(__name__)

#: a piece whose longest loop encloses less than this fraction of its area is nearly closed
BORDER_RATIO = 0.2


# --------------------------------------------------------------------------- basics
def rows(A: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """``A[idx]`` for a 2-D array (``np.take``: much faster than fancy indexing here)."""
    return np.take(A, idx, axis=0)


def sorted_unique(x: np.ndarray) -> np.ndarray:
    """Sorted distinct values of a 1-D array (sort based; see the module docstring)."""
    s = np.sort(np.asarray(x).reshape(-1))
    if len(s) == 0:
        return s
    keep = np.empty(len(s), dtype=bool)
    keep[0] = True
    np.not_equal(s[1:], s[:-1], out=keep[1:])
    return s[keep]


def _corners(V: np.ndarray, F: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return rows(V, F[:, 0]), rows(V, F[:, 1]), rows(V, F[:, 2])


def as_mesh(V: np.ndarray, F: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``(V float64 (N,3), F int64 (M,3))``; ``ValueError`` on bad shapes or indices."""
    V = np.ascontiguousarray(V, dtype=np.float64)
    if V.ndim != 2 or V.shape[1] != 3:
        raise ValueError(f"V must have shape (N,3), got {V.shape}")
    F = np.asarray(F)
    if F.size == 0:
        return V, np.zeros((0, 3), dtype=np.int64)
    F = np.ascontiguousarray(F, dtype=np.int64)
    if F.ndim != 2 or F.shape[1] != 3:
        raise ValueError(f"F must have shape (M,3), got {F.shape}")
    if F.min() < 0 or F.max() >= len(V):
        raise ValueError("face indices out of range")
    return V, F


def _face_cross(V: np.ndarray, F: np.ndarray) -> np.ndarray:
    a, b, c = _corners(V, F)
    return np.cross(b - a, c - a)  # length = 2 * area


def face_areas(V: np.ndarray, F: np.ndarray) -> np.ndarray:
    """Triangle areas (M,) in mm^2."""
    if len(F) == 0:
        return np.zeros(0)
    c = _face_cross(V, F)
    return 0.5 * np.sqrt(np.einsum("ij,ij->i", c, c))


def face_normals(V: np.ndarray, F: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``(unit normals (M,3), areas (M,))``; degenerate faces get a zero normal."""
    if len(F) == 0:
        return np.zeros((0, 3)), np.zeros(0)
    c = _face_cross(V, F)
    nrm = np.sqrt(np.einsum("ij,ij->i", c, c))
    n = np.divide(c, nrm[:, None], out=np.zeros_like(c), where=nrm[:, None] > 0)
    return n, 0.5 * nrm


def vertex_normals(V: np.ndarray, F: np.ndarray) -> np.ndarray:
    """Area-weighted unit vertex normals (N,3); zero for unreferenced vertices."""
    out = np.zeros((len(V), 3))
    if len(F) == 0:
        return out
    c = _face_cross(V, F)
    idx = F.reshape(-1)
    for k in range(3):
        out[:, k] = np.bincount(idx, weights=np.repeat(c[:, k], 3), minlength=len(V))
    nrm = np.sqrt(np.einsum("ij,ij->i", out, out))[:, None]
    return np.divide(out, nrm, out=np.zeros_like(out), where=nrm > 0)


def compact(V: np.ndarray, F: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Drop vertices no face uses: ``(V2, F2, kept vertex indices)``."""
    if len(F) == 0:
        return V[:0].copy(), np.zeros((0, 3), dtype=np.int64), np.zeros(0, dtype=np.int64)
    used = np.zeros(len(V), dtype=bool)
    used[F.reshape(-1)] = True
    idx = np.flatnonzero(used)
    if len(idx) == len(V):
        return V, F, idx
    remap = np.full(len(V), -1, dtype=np.int64)
    remap[idx] = np.arange(len(idx))
    return rows(V, idx), remap[F], idx


# --------------------------------------------------------------------------- edges
@dataclass
class EdgeTable:
    """Undirected edges of a mesh (see the module docstring)."""

    n_vertices: int
    v0: np.ndarray  # (E,) smaller endpoint
    v1: np.ndarray  # (E,) larger endpoint
    counts: np.ndarray  # (E,) faces using the edge
    he_edge: np.ndarray  # (3M,) edge id of each half-edge
    he_order: np.ndarray  # (3M,) half-edges grouped by edge id
    edge_start: np.ndarray  # (E,) first position of each edge's group in he_order

    @property
    def n_edges(self) -> int:
        return len(self.v0)

    def half_edges_of(self, e: int) -> np.ndarray:
        s = int(self.edge_start[e])
        return self.he_order[s : s + int(self.counts[e])]


def edge_table(F: np.ndarray, n_vertices: int) -> EdgeTable:
    """Build the :class:`EdgeTable` of ``F`` (one sort of the half-edge keys)."""
    n = int(n_vertices)
    if len(F) == 0:
        z = np.zeros(0, dtype=np.int64)
        return EdgeTable(n, z, z, z, z, z, z)
    a = F.reshape(-1)
    b = F[:, [1, 2, 0]].reshape(-1)
    key = np.minimum(a, b) * n + np.maximum(a, b)
    order = np.argsort(key)
    ks = key[order]
    flag = np.empty(len(ks), dtype=bool)
    flag[0] = True
    np.not_equal(ks[1:], ks[:-1], out=flag[1:])
    edge_start = np.flatnonzero(flag)
    keys = ks[edge_start]
    counts = np.diff(np.append(edge_start, len(ks)))
    he_edge = np.empty(len(ks), dtype=np.int64)
    he_edge[order] = np.cumsum(flag) - 1
    return EdgeTable(n, keys // n, keys % n, counts, he_edge, order, edge_start)


def edge_lengths(V: np.ndarray, et: EdgeTable) -> np.ndarray:
    d = rows(V, et.v0) - rows(V, et.v1)
    return np.sqrt(np.einsum("ij,ij->i", d, d))


def mean_edge_length(V: np.ndarray, et: EdgeTable) -> float:
    if et.n_edges == 0:
        return 1.0
    m = float(np.mean(edge_lengths(V, et)))
    return m if m > 0 else 1.0


def vertex_edge_length(V: np.ndarray, et: EdgeTable) -> np.ndarray:
    """Mean length of the edges at each vertex (0 for isolated vertices)."""
    n = len(V)
    if et.n_edges == 0:
        return np.zeros(n)
    ln = edge_lengths(V, et)
    s = np.bincount(et.v0, ln, n) + np.bincount(et.v1, ln, n)
    c = np.bincount(et.v0, minlength=n) + np.bincount(et.v1, minlength=n)
    return np.divide(s, c, out=np.zeros(n), where=c > 0)


# --------------------------------------------------------------------------- pieces
def components(F: np.ndarray, n_vertices: int) -> tuple[int, np.ndarray, np.ndarray]:
    """Connected pieces (faces sharing a vertex): ``(count, vertex labels (N,), face labels (M,))``.

    Labels are ``0 .. count-1`` over the pieces that have faces; unreferenced vertices get -1.
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    n = int(n_vertices)
    if len(F) == 0:
        return 0, np.full(n, -1, dtype=np.int64), np.zeros(0, dtype=np.int64)
    r = F.reshape(-1)
    c = F[:, [1, 2, 0]].reshape(-1)
    A = coo_matrix((np.ones(len(r), dtype=np.int8), (r, c)), shape=(n, n)).tocsr()
    _, labels = connected_components(A, directed=False)
    labels = labels.astype(np.int64)
    face_raw = labels[F[:, 0]]
    used = np.zeros(int(labels.max()) + 1, dtype=bool)
    used[face_raw] = True
    remap = np.where(used, np.cumsum(used) - 1, -1)
    return int(used.sum()), remap[labels], remap[face_raw]


def edge_components(F: np.ndarray, n_vertices: int, et: EdgeTable | None = None) -> tuple[int, np.ndarray]:
    """Pieces of faces joined through shared *edges*: ``(count, face labels (M,))``, labels
    ``0 .. count-1``. Two parts that touch at a vertex only are two pieces here (one piece for
    :func:`components`): a lone triangle or a flap standing on the surface is its own piece."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    m = len(F)
    if m == 0:
        return 0, np.zeros(0, dtype=np.int64)
    if et is None:
        et = edge_table(F, n_vertices)
    a, b = [], []
    for k in range(1, int(et.counts.max())):  # each edge's k-th face with its first (k > 1: non-manifold edges)
        e = np.flatnonzero(et.counts > k)
        a.append(et.he_order[et.edge_start[e]] // 3)
        b.append(et.he_order[et.edge_start[e] + k] // 3)
    if a:
        a, b = np.concatenate(a), np.concatenate(b)
    A = coo_matrix((np.ones(len(a), dtype=np.int8), (a, b)), shape=(m, m)).tocsr()
    count, labels = connected_components(A, directed=False)
    return int(count), labels.astype(np.int64)


# --------------------------------------------------------------------------- boundary loops
@dataclass
class Loops:
    """Boundary loops: loop ``k`` is ``halfedges[ptr[k]:ptr[k+1]]`` in order (see the module docstring)."""

    halfedges: np.ndarray  # boundary half-edge ids, loop after loop
    ptr: np.ndarray  # (L+1,) offsets
    closed: np.ndarray  # (L,) bool
    perimeter: np.ndarray  # (L,) mm
    start: np.ndarray  # start vertex of each entry of halfedges
    end: np.ndarray  # end vertex of each entry of halfedges

    def __len__(self) -> int:
        return len(self.closed)

    def verts(self, k: int) -> np.ndarray:
        """Vertices of loop ``k`` in order (the mesh has half-edges ``verts[i] -> verts[i+1]``)."""
        return self.start[self.ptr[k] : self.ptr[k + 1]]

    def edges(self, k: int) -> np.ndarray:
        return self.halfedges[self.ptr[k] : self.ptr[k + 1]]

    def n_edges(self) -> np.ndarray:
        return np.diff(self.ptr)


def _empty_loops() -> Loops:
    z = np.zeros(0, dtype=np.int64)
    return Loops(z, np.zeros(1, dtype=np.int64), np.zeros(0, dtype=bool), np.zeros(0), z, z)


def _fan_partner(h: int, F: np.ndarray, et: EdgeTable, max_steps: int) -> int:
    """The boundary half-edge leaving vertex ``v`` that shares a face fan with boundary half-edge
    ``h`` (``u -> v``), found by walking around ``v``; -1 if the walk fails."""
    a_of = lambda x: int(F[x // 3, x % 3])  # noqa: E731
    b_of = lambda x: int(F[x // 3, (x % 3 + 1) % 3])  # noqa: E731
    cur = h
    for _ in range(max_steps):
        f, j = divmod(cur, 3)
        out = 3 * f + (j + 1) % 3  # v -> x in the same face
        e = int(et.he_edge[out])
        if et.counts[e] == 1:
            return out
        twins = [int(t) for t in et.half_edges_of(e) if int(t) != out]
        nxt = [t for t in twins if a_of(t) == b_of(out) and b_of(t) == a_of(out)]  # x -> v
        if not nxt:
            return -1  # inconsistent orientation around v
        cur = nxt[0]
    return -1


def boundary_loops(V: np.ndarray, F: np.ndarray, et: EdgeTable | None = None) -> Loops:
    """All boundary loops of the mesh (see the module docstring)."""
    if len(F) == 0:
        return _empty_loops()
    if et is None:
        et = edge_table(F, len(V))
    bh = np.flatnonzero(et.counts[et.he_edge] == 1)
    if len(bh) == 0:
        return _empty_loops()
    Fr = F.reshape(-1)
    start = Fr[bh]
    end = F[:, [1, 2, 0]].reshape(-1)[bh]
    order = np.argsort(start, kind="stable")
    s_sorted = start[order]
    lo = np.searchsorted(s_sorted, end, "left")
    hi = np.searchsorted(s_sorted, end, "right")
    cnt = hi - lo
    nxt = np.full(len(bh), -1, dtype=np.int64)
    one = cnt == 1
    nxt[one] = order[lo[one]]
    multi = np.flatnonzero(cnt > 1)
    if len(multi):  # bowtie vertices: pair through the face fans
        taken: set[int] = set()
        for i in multi.tolist():
            cands = order[lo[i] : hi[i]].tolist()
            out = _fan_partner(int(bh[i]), F, et, max_steps=64)
            pos = int(np.searchsorted(bh, out)) if out >= 0 else -1
            if pos < 0 or pos >= len(bh) or bh[pos] != out or pos not in cands or pos in taken:
                free = [c for c in cands if c not in taken]
                pos = free[0] if free else cands[0]
            taken.add(pos)
            nxt[i] = pos
    nxt_l = nxt.tolist()
    seen = bytearray(len(bh))
    seq: list[int] = []
    ptr = [0]
    closed: list[bool] = []
    for s in range(len(bh)):
        if seen[s]:
            continue
        i = s
        while i != -1 and not seen[i]:
            seen[i] = 1
            seq.append(i)
            i = nxt_l[i]
        closed.append(i == s)
        ptr.append(len(seq))
    seq_a = np.asarray(seq, dtype=np.int64)
    ptr_a = np.asarray(ptr, dtype=np.int64)
    blen = np.linalg.norm(rows(V, end[seq_a]) - rows(V, start[seq_a]), axis=1)
    per = np.add.reduceat(blen, ptr_a[:-1]) if len(seq_a) else np.zeros(0)
    return Loops(bh[seq_a], ptr_a, np.asarray(closed, dtype=bool), per, start[seq_a], end[seq_a])


def classify_loops(V: np.ndarray, F: np.ndarray, loops: Loops, vertex_labels: np.ndarray,
                   face_labels: np.ndarray, n_components: int, areas: np.ndarray | None = None) -> np.ndarray:
    """``(L,)`` bool: True for the loops that are outer borders (see the module docstring)."""
    L = len(loops)
    is_border = np.zeros(L, dtype=bool)
    if L == 0:
        return is_border
    if areas is None:
        areas = face_areas(V, F)
    comp_area = np.bincount(face_labels, weights=areas, minlength=n_components)
    comp = vertex_labels[loops.start[loops.ptr[:-1]]]
    cand = np.flatnonzero(loops.closed & (comp >= 0))
    if len(cand) == 0:
        return is_border
    # longest closed loop of each piece
    o = cand[np.lexsort((-loops.perimeter[cand], comp[cand]))]
    co = comp[o]
    longest = o[np.r_[True, co[1:] != co[:-1]]]
    p = loops.perimeter[longest]
    open_piece = p * p / (4.0 * np.pi) >= BORDER_RATIO * comp_area[comp[longest]]
    is_border[longest[open_piece]] = True
    return is_border
