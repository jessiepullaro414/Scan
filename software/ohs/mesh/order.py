"""Numbering a mesh by position, so that an edit gives the same result whatever order the mesh lists
its vertices and faces in (``MAINTENANCE_PLAN.md`` F15; the owner's decision D9, an option that is off
until it is switched on).

Why: the GPU path lists the same vertices in a different order from run to run, and the edits break
ties by number: hole filling picks among equally good triangulations by the loop's first vertex,
simplifying orders equal costs by a random draw per edge in edge order, and sums (vertex normals, the
sparse products of smoothing, a patch's fairing) run in vertex or face order, so their last bits follow
the numbering too. With ``by_position`` an edit runs on the mesh renumbered by :func:`canonical` and its
result is mapped back: every tie it breaks by number is then broken by position, and every sum runs in
the same order, so the same mesh content gives the same result bit for bit.

**The order.** Vertices by their coordinates (x, then y, then z, each as its exact value; -0 before
+0). Vertices at the same point in every bit (a mesh can have such pairs) are told apart by their
neighbours: how many faces they have, then the smallest, largest, sum and sum of squares of the
position ranks of the vertices they share a face with. Only two vertices equal in all of that keep
the order they came in, and only then can the result still depend on the order (no such pair in the
test meshes or the example scan). Faces are rotated to start at their lowest-numbered vertex (the
orientation is kept) and sorted by their three vertices. Simplifying uses this order with runs of
vertices shuffled by a fixed shuffle (:func:`canonical`, ``scramble``).

Off (the default) nothing here runs and every edit is bit for bit what it was before.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = ["Canonical", "canonical", "faces_back", "SCRAMBLE_RUN"]

#: ``canonical(scramble=True)``: vertices numbered by position are shuffled in runs of this many
SCRAMBLE_RUN = 64


@dataclass
class Canonical:
    """A mesh renumbered by position: ``V = V_in[pv]``; vertex ``i`` of the input is vertex
    ``rank[i]`` here; face ``j`` here is face ``pf[j]`` of the input (rotated)."""

    V: np.ndarray
    F: np.ndarray
    pv: np.ndarray
    rank: np.ndarray
    pf: np.ndarray

    def vertex_values(self, x: np.ndarray | None) -> np.ndarray | None:
        """Per-vertex values of the input (sigma, normals, a limit) in this numbering."""
        return None if x is None else np.asarray(x)[self.pv]


def _ordered_bits(x: np.ndarray) -> np.ndarray:
    """Float64 values as uint64 keys that sort as the values do (-0 just before +0)."""
    b = np.ascontiguousarray(x, dtype=np.float64).view(np.uint64)
    neg = (b >> np.uint64(63)).astype(bool)
    return np.where(neg, ~b, b | np.uint64(1 << 63))


def canonical(V: np.ndarray, F: np.ndarray, scramble: bool = False) -> Canonical:
    """The mesh numbered by position (module docstring). ``V`` float64 (N,3), ``F`` int64 (M,3).

    ``scramble``: then renumbered once more by a fixed shuffle of runs of :data:`SCRAMBLE_RUN`
    vertices (the same shuffle for every mesh of that size), and the faces sorted by their new
    numbers: still a function of the positions alone. For simplifying: numbered by position, the
    edges of equal cost that make its candidate pool come from one end of the part (in x), the
    collapses bunch up there, and on a thin plate the surface moved twice as far (0.59 mm instead of
    0.30 mm for 99.9 % of it); shuffled they spread over the part, as in a scan's own order. Runs,
    not single vertices: neighbours in memory stay neighbours (a full shuffle made simplifying a
    scan of 800,000 triangles about 15 % slower; runs of 64 are as fast as the scan's own order)."""
    c = _canonical(V, F)
    if not scramble:
        return c
    N = len(V)
    rng = np.random.default_rng(0)
    runs = rng.permutation(-(-N // SCRAMBLE_RUN)) if N else np.zeros(0, dtype=np.int64)
    sv = (runs[:, None] * SCRAMBLE_RUN + np.arange(SCRAMBLE_RUN)[None, :]).reshape(-1)
    sv = sv[sv < N].astype(np.int64)  # new vertex i is canonical vertex sv[i]
    inv = np.empty_like(sv)
    inv[sv] = np.arange(N)
    Fs = inv[c.F]
    sf = np.lexsort((Fs.max(axis=1), Fs.min(axis=1))).astype(np.int64) if len(Fs) else np.zeros(0, dtype=np.int64)
    pv = c.pv[sv]
    rank = np.empty_like(pv)
    rank[pv] = np.arange(N)
    return Canonical(V=c.V[sv], F=np.ascontiguousarray(Fs[sf]), pv=pv, rank=rank, pf=c.pf[sf])


def _canonical(V: np.ndarray, F: np.ndarray) -> Canonical:
    N, M = len(V), len(F)
    kx, ky, kz = (_ordered_bits(V[:, k]) for k in range(3))
    order = np.lexsort((kz, ky, kx))
    if N > 1:
        same = (kx[order][1:] == kx[order][:-1]) & (ky[order][1:] == ky[order][:-1]) & (kz[order][1:] == kz[order][:-1])
    else:
        same = np.zeros(0, dtype=bool)
    if same.any():
        # vertices at the same point: their neighbours decide (module docstring)
        g = np.empty(N, dtype=np.int64)
        g[order] = np.cumsum(np.r_[True, ~same]) - 1  # position rank, equal for equal points
        tied = np.zeros(N, dtype=bool)
        tied[order[1:][same]] = True
        tied[order[:-1][same]] = True
        deg = np.zeros(N, dtype=np.int64)
        lo = np.zeros(N, dtype=np.int64)
        hi = np.zeros(N, dtype=np.int64)
        s1 = np.zeros(N, dtype=np.int64)
        s2 = np.zeros(N, dtype=np.int64)
        if M:  # over the faces at the tied vertices only (a scan has a few such pairs)
            lo[tied] = np.iinfo(np.int64).max
            hi[tied] = -1
            for a in range(3):
                f = np.flatnonzero(tied[F[:, a]])
                v = F[f, a]
                np.add.at(deg, v, 1)
                for b in range(3):
                    if a != b:
                        w = g[F[f, b]]
                        np.minimum.at(lo, v, w)
                        np.maximum.at(hi, v, w)
                        np.add.at(s1, v, w)
                        np.add.at(s2, v, w * w)
        tpos = np.flatnonzero(tied[order])  # their places in the order: each point's vertices side by side
        tv = order[tpos]
        order[tpos] = tv[np.lexsort((tv, s2[tv], s1[tv], hi[tv], lo[tv], deg[tv], g[tv]))]
    pv = order.astype(np.int64)
    rank = np.empty(N, dtype=np.int64)
    rank[pv] = np.arange(N, dtype=np.int64)
    R = rank[F] if M else np.zeros((0, 3), dtype=np.int64)
    if M:
        r = np.argmin(R, axis=1)
        rows = np.arange(M)
        R = np.stack([R[rows, r], R[rows, (r + 1) % 3], R[rows, (r + 2) % 3]], axis=1)
        if N ** 3 < 2 ** 62:
            pf = np.argsort((R[:, 0] * N + R[:, 1]) * N + R[:, 2], kind="stable")
        else:
            pf = np.lexsort((R[:, 2], R[:, 1], R[:, 0]))
        pf = pf.astype(np.int64)
        R = R[pf]
    else:
        pf = np.zeros(0, dtype=np.int64)
    return Canonical(V=V[pv], F=np.ascontiguousarray(R), pv=pv, rank=rank, pf=pf)


def faces_back(c: Canonical, faces: np.ndarray) -> np.ndarray:
    """Face indices of the renumbered mesh as indices of the input mesh, in increasing order."""
    return np.sort(c.pf[np.asarray(faces, dtype=np.int64)])
