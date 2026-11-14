"""Hole outlines and smooth hole filling, in the spirit of Liepa, *Filling Holes in Meshes* (2003).

What is a hole: a closed boundary loop that is not an outer border
(:func:`ohs.mesh.topology.classify_loops`: the longest loop of an open piece is the edge of the
scanned area, left open unless ``include_borders`` asks for a watertight mesh). Its size is its
perimeter in mm. The outline of a single face (a lone triangle that touches the mesh at one vertex,
as fused scans have a few) is not a hole and is left alone. The new faces come after the old ones, so the caller can mark them as filled
(not measured: :func:`ohs.mesh.carry_filled`).

:func:`fill_holes` closes every hole up to ``max_hole_mm`` in three steps per hole:

1. **Triangulate** the loop. Up to :data:`MAX_DP_EDGES` edges: Liepa's dynamic programme over
   the loop polygon, minimising first the largest dihedral angle (between the new triangles and
   with the mesh around the hole), then the area, so the patch continues the surface instead of
   folding. Diagonals that already exist as mesh edges are not allowed. Larger loops: smallest-
   angle ear clipping (cheaper; the next steps reshape it anyway).
2. **Refine** to the surrounding density: a triangle is split at its centroid while
   ``sqrt(2) * |centroid - corner|`` exceeds the local edge length at the centroid and at each
   corner (the boundary vertices carry the mean length of their mesh edges, new vertices the
   average of their triangle's corners), and the new edges are relaxed with 3D Delaunay flips.
3. **Fair** the new vertices: they solve the bi-Laplace equation ``L^2 x = 0`` (umbrella
   Laplacian) with the loop and the ring of mesh vertices around it fixed, so the patch meets the
   surface with matching slope and follows its curvature (a hole in a sphere is filled with a
   piece of sphere, not a flat cap). A solution must stay near the hole (within
   :data:`FAIR_BOUND` loop radii of its centroid): a twisted loop, one climbing around an object
   sitting in the hole, triangulates with folds, on which the system is nearly singular and once
   threw a patch 5 m away. Then the hole gets a soap film instead (the membrane ``L x = 0``: each
   new vertex the mean of its neighbours, so the patch stays inside the loop's convex hull).

Small holes (a few mm at 1 mm voxels) get no new vertices in step 2 and keep the triangulation of
step 1. The new faces are oriented like the mesh around them. Nothing else is moved.
"""
from __future__ import annotations

import logging
import math
from typing import Callable

import numpy as np

from ohs.mesh.topology import (
    Loops,
    as_mesh,
    boundary_loops,
    classify_loops,
    components,
    edge_table,
    face_areas,
    face_normals,
    vertex_edge_length,
)

__all__ = ["fill_holes", "hole_outlines", "find_holes", "MAX_DP_EDGES"]

log = logging.getLogger(__name__)

MAX_DP_EDGES = 160  # the O(n^3) triangulation above this would take seconds per hole
ALPHA = math.sqrt(2.0)  # Liepa's density factor
FAIR_BOUND = 1.25  # a faired patch must stay within this many loop radii of the loop's centroid
Progress = Callable[[float, str], None]


# --------------------------------------------------------------------------- finding holes
def find_holes(V: np.ndarray, F: np.ndarray) -> tuple[Loops, np.ndarray, dict]:
    """``(loops, is_border, info)`` of a mesh; ``info`` holds the edge table and piece labels."""
    et = edge_table(F, len(V))
    loops = boundary_loops(V, F, et)
    n_comp, vlab, flab = components(F, len(V))
    areas = face_areas(V, F)
    is_border = classify_loops(V, F, loops, vlab, flab, n_comp, areas)
    return loops, is_border, {"et": et, "n_components": n_comp, "vertex_labels": vlab, "face_labels": flab,
                              "areas": areas}


def hole_outlines(V: np.ndarray, F: np.ndarray, max_holes: int | None = None, decimals: int = 2) -> list[dict]:
    """``[{perimeter_mm, n_edges, points}]`` of every hole, largest first.

    ``points`` is the flattened ``x, y, z`` of the loop's vertices in order, with the first point
    repeated at the end (``n_edges + 1`` points), so it draws as a closed line either way.
    """
    V, F = as_mesh(V, F)
    if len(F) == 0:
        return []
    loops, is_border, _ = find_holes(V, F)
    holes = [k for k in range(len(loops)) if not is_border[k] and loops.perimeter[k] > 0.0]  # not a collapsed loop
    holes.sort(key=lambda k: -float(loops.perimeter[k]))
    if max_holes is not None:
        holes = holes[: int(max_holes)]
    out = []
    for k in holes:
        vs = loops.verts(k)
        pts = V[np.append(vs, vs[0])] if loops.closed[k] else V[vs]
        out.append({"perimeter_mm": round(float(loops.perimeter[k]), 3), "n_edges": int(len(vs)),
                    "points": np.round(pts, decimals).reshape(-1).tolist()})
    return out


# --------------------------------------------------------------------------- step 1: triangulation
def _triangulate_dp(P: np.ndarray, nb: np.ndarray, forbid: np.ndarray) -> list[tuple[int, int, int]] | None:
    """Liepa's minimum (max dihedral, area) triangulation of loop ``P`` (n,3).

    ``nb[i]``: unit normal of the mesh face on loop edge ``i -> i+1``; ``forbid[i, k]``: the
    diagonal exists already. Returns triangles ``(k, m, i)`` (oriented like the mesh) or None.
    """
    n = len(P)
    ang = np.full((n, n), np.inf)
    area = np.full((n, n), np.inf)
    lam = np.full((n, n), -1, dtype=np.int64)
    NL = np.zeros((n, n, 3))
    r0 = np.arange(n - 1)
    ang[r0, r0 + 1] = 0.0
    area[r0, r0 + 1] = 0.0
    NL[r0, r0 + 1] = nb[:-1]
    eps = 1e-12
    for d in range(2, n):
        i = np.arange(n - d)
        k = i + d
        m = i[:, None] + np.arange(1, d)[None, :]
        I = np.broadcast_to(i[:, None], m.shape)
        K = np.broadcast_to(k[:, None], m.shape)
        Pk = P[K]
        cr = np.cross(P[m] - Pk, P[I] - Pk)  # normal of (k, m, i)
        nrm = np.sqrt(np.einsum("...j,...j->...", cr, cr))
        nt = cr / np.where(nrm > eps, nrm, 1.0)[..., None]
        worst = np.minimum(np.einsum("...j,...j->...", nt, NL[I, m]), np.einsum("...j,...j->...", nt, NL[m, K]))
        if d == n - 1:
            worst = np.minimum(worst, nt @ nb[n - 1])
        worst = np.where(nrm > eps, worst, -1.0)  # a degenerate triangle counts as folded
        cand_ang = np.maximum(np.maximum(ang[I, m], ang[m, K]), np.arccos(np.clip(worst, -1.0, 1.0)))
        cand_area = area[I, m] + area[m, K] + 0.5 * nrm
        if d < n - 1:
            bad = forbid[i, k]
            if bad.any():
                cand_ang[bad] = np.inf
                cand_area[bad] = np.inf
        amin = cand_ang.min(axis=1)
        okm = cand_ang <= (amin + 1e-9)[:, None]
        sel = np.where(okm, cand_area, np.inf).argmin(axis=1)
        rows = np.arange(len(i))
        ang[i, k] = cand_ang[rows, sel]
        area[i, k] = np.where(np.isfinite(amin), cand_area[rows, sel], np.inf)
        lam[i, k] = m[rows, sel]
        NL[i, k] = nt[rows, sel]
    if not np.isfinite(ang[0, n - 1]):
        return None
    tris: list[tuple[int, int, int]] = []
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        mm = int(lam[a, b])
        tris.append((b, mm, a))
        stack.append((a, mm))
        stack.append((mm, b))
    return tris


def _ear_clip(P: np.ndarray, forbid_pairs: set[tuple[int, int]]) -> list[tuple[int, int, int]]:
    """Smallest-angle ear clipping of loop ``P`` (n,3); triangles oriented like the mesh."""
    n = len(P)
    # the patch's orientation: Newell normal of the reversed loop
    Q = P[::-1]
    N = np.cross(Q, np.roll(Q, -1, axis=0)).sum(axis=0)
    N = N / (np.linalg.norm(N) or 1.0)
    prev = np.roll(np.arange(n), 1)
    nxt = np.roll(np.arange(n), -1)
    alive = np.ones(n, dtype=bool)
    tris: list[tuple[int, int, int]] = []
    left = n
    while left > 3:
        v = np.flatnonzero(alive)
        p, q = prev[v], nxt[v]
        e1 = P[p] - P[v]
        e2 = P[q] - P[v]
        cosang = np.einsum("ij,ij->i", e1, e2) / np.maximum(np.linalg.norm(e1, axis=1) * np.linalg.norm(e2, axis=1), 1e-300)
        angle = np.arccos(np.clip(cosang, -1.0, 1.0))
        tri_n = np.cross(P[v] - P[q], P[p] - P[q])  # triangle (q, v, p)
        convex = tri_n @ N > 0
        cost = np.where(convex, angle, angle + 2 * np.pi)
        for j in np.argsort(cost):
            a, b = int(p[j]), int(q[j])
            if (min(a, b), max(a, b)) in forbid_pairs and left > 3:
                continue
            break
        else:
            j = int(np.argmin(cost))
        vv = int(v[j])
        a, b = int(prev[vv]), int(nxt[vv])
        tris.append((b, vv, a))
        alive[vv] = False
        nxt[a] = b
        prev[b] = a
        left -= 1
    v = np.flatnonzero(alive)
    a = int(v[0])
    b = int(nxt[a])
    c = int(nxt[b])
    tris.append((c, b, a))
    return tris


# --------------------------------------------------------------------------- step 2: refinement
# Small-vector maths on plain tuples: numpy's per-call overhead would dominate on 3-vectors.
def _sub(a: tuple, b: tuple) -> tuple:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: tuple, b: tuple) -> tuple:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a: tuple, b: tuple) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _ekey(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


class _Patch:
    """The triangles filling one hole while it is refined (local ids: loop ``0..n-1``, then new)."""

    def __init__(self, P: np.ndarray, sigma: np.ndarray, tris: list[tuple[int, int, int]],
                 forbid_pairs: set[tuple[int, int]]) -> None:
        self.n = len(P)
        self.pos: list[tuple] = [tuple(float(x) for x in p) for p in P]
        self.sig = [float(s) for s in sigma]
        self.tris = [list(t) for t in tris]
        self.forbid = forbid_pairs
        self.e2t: dict[tuple[int, int], list[int]] = {}
        for t, (a, b, c) in enumerate(self.tris):
            for e in (_ekey(a, b), _ekey(b, c), _ekey(c, a)):
                self.e2t.setdefault(e, []).append(t)

    def _is_loop_edge(self, e: tuple[int, int]) -> bool:
        a, b = e
        n = self.n
        return b < n and (b - a == 1 or (a == 0 and b == n - 1))

    def _normal(self, a: int, b: int, c: int) -> tuple:
        pa = self.pos[a]
        return _cross(_sub(self.pos[b], pa), _sub(self.pos[c], pa))

    def _flip(self, e: tuple[int, int]) -> tuple[int, int, int, int] | None:
        """Flip edge ``e`` if the pair of triangles is not Delaunay (Liepa's relaxation: the
        opposite vertex lies inside the circumsphere). Returns the quad ``(p, q, r, s)`` or None."""
        ts = self.e2t.get(e)
        if ts is None or len(ts) != 2 or self._is_loop_edge(e):
            return None
        t1, t2 = ts
        tri1, tri2 = self.tris[t1], self.tris[t2]
        # orient: tri1 = (p, q, r) with half-edge p -> q, tri2 = (q, p, s)
        x, y = e
        k1 = tri1.index(x)
        if tri1[(k1 + 1) % 3] == y:
            p, q, r = x, y, tri1[(k1 + 2) % 3]
        else:
            p, q, r = y, x, tri1[(k1 + 1) % 3]
        k2 = tri2.index(q)
        if tri2[(k2 + 1) % 3] != p:
            return None  # inconsistent orientation: leave it
        s = tri2[(k2 + 2) % 3]
        if r == s:
            return None
        ne = _ekey(r, s)
        if ne in self.e2t or ne in self.forbid:
            return None
        P, Q, Rr, S = self.pos[p], self.pos[q], self.pos[r], self.pos[s]
        a = _sub(P, Rr)
        b = _sub(Q, Rr)
        axb = _cross(a, b)
        den = 2.0 * _dot(axb, axb)
        if den > 1e-300:  # circumcentre of (p, q, r)
            aa, bb = _dot(a, a), _dot(b, b)
            w = _cross((aa * b[0] - bb * a[0], aa * b[1] - bb * a[1], aa * b[2] - bb * a[2]), axb)
            c = (Rr[0] + w[0] / den, Rr[1] + w[1] / den, Rr[2] + w[2] / den)
            d_s, d_r = _sub(S, c), _sub(Rr, c)
            if _dot(d_s, d_s) >= _dot(d_r, d_r) * (1.0 - 1e-9):
                return None
        n1, n2 = self._normal(p, q, r), self._normal(q, p, s)
        n_old = (n1[0] + n2[0], n1[1] + n2[1], n1[2] + n2[2])
        if _dot(self._normal(r, p, s), n_old) <= 0 or _dot(self._normal(s, q, r), n_old) <= 0:
            return None  # the flip would fold the patch
        del self.e2t[e]
        for old_e, t_from, t_to in ((_ekey(q, r), t1, t2), (_ekey(p, s), t2, t1)):
            lst = self.e2t[old_e]
            lst[lst.index(t_from)] = t_to
        self.e2t[ne] = [t1, t2]
        self.tris[t1], self.tris[t2] = [r, p, s], [s, q, r]
        return p, q, r, s

    def relax(self, edges: list[tuple[int, int]]) -> None:
        """Lawson flips from a worklist: a flipped edge queues the four edges of its quad."""
        work = list(edges)
        budget = 50 * (len(self.e2t) + 10)
        while work and budget > 0:
            budget -= 1
            quad = self._flip(work.pop())
            if quad is not None:
                p, q, r, s = quad
                work.extend((_ekey(p, r), _ekey(q, r), _ekey(p, s), _ekey(q, s)))

    def refine(self) -> None:
        while True:
            touched: list[tuple[int, int]] = []
            for t in range(len(self.tris)):
                a, b, cc = self.tris[t]
                pa, pb, pc = self.pos[a], self.pos[b], self.pos[cc]
                c = ((pa[0] + pb[0] + pc[0]) / 3.0, (pa[1] + pb[1] + pc[1]) / 3.0, (pa[2] + pb[2] + pc[2]) / 3.0)
                sc = (self.sig[a] + self.sig[b] + self.sig[cc]) / 3.0
                ok = True
                for x, px in ((a, pa), (b, pb), (cc, pc)):
                    d = _sub(c, px)
                    dd = ALPHA * math.sqrt(_dot(d, d))
                    if dd <= sc or dd <= self.sig[x]:
                        ok = False
                        break
                if not ok:
                    continue
                v = len(self.pos)
                self.pos.append(c)
                self.sig.append(sc)
                t2, t3 = len(self.tris), len(self.tris) + 1
                self.tris[t] = [a, b, v]
                self.tris.append([b, cc, v])
                self.tris.append([cc, a, v])
                for old_e, t_to in ((_ekey(b, cc), t2), (_ekey(cc, a), t3)):
                    lst = self.e2t[old_e]
                    lst[lst.index(t)] = t_to
                self.e2t[_ekey(a, v)] = [t, t3]
                self.e2t[_ekey(b, v)] = [t, t2]
                self.e2t[_ekey(cc, v)] = [t2, t3]
                touched.extend((_ekey(a, b), _ekey(b, cc), _ekey(cc, a)))
            if not touched:
                break
            self.relax(touched)
            self.relax(list(self.e2t))


# --------------------------------------------------------------------------- step 3: fairing
def _laplacian(T: np.ndarray, X: np.ndarray, cotan: bool):
    """``L = M^-1 (W - D)``: cotangent weights over barycentric areas, or the umbrella operator."""
    from scipy.sparse import coo_matrix, diags

    N = len(X)
    rows, cols, vals = [], [], []
    area = np.zeros(N)
    for a, b, c in ((0, 1, 2), (1, 2, 0), (2, 0, 1)):
        i, j, k = T[:, a], T[:, b], T[:, c]
        if cotan:  # the angle at k is opposite edge (i, j)
            e1, e2 = X[i] - X[k], X[j] - X[k]
            cr = np.linalg.norm(np.cross(e1, e2), axis=1)
            w = np.clip(0.5 * np.einsum("ij,ij->i", e1, e2) / np.maximum(cr, 1e-12), 1e-3, 50.0)
            area += np.bincount(k, cr / 6.0, N)
        else:
            w = np.ones(len(T))
        rows += [i, j]
        cols += [j, i]
        vals += [w, w]
    W = coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(N, N)).tocsr()
    if not cotan:
        W.data[:] = 1.0  # duplicates were summed: back to a 0/1 adjacency
        area = np.ones(N)
    d = np.asarray(W.sum(axis=1)).reshape(-1)
    if not cotan:
        area = np.maximum(d, 1.0)
    return (diags(1.0 / np.maximum(area, 1e-12)) @ (W - diags(d))).tocsr()


def _fair(loop_g: np.ndarray, new_pos: np.ndarray, tris_local: np.ndarray, V: np.ndarray, F: np.ndarray,
          vf_ptr: np.ndarray, vf_face: np.ndarray) -> np.ndarray:
    """New vertex positions solving ``L^2 x = 0`` with the loop and its mesh ring fixed.

    Cotangent weights, computed on the refined patch and once more on the first solution; the
    umbrella operator if that system cannot be solved. A solution counts only if it stays near
    the hole (every new vertex within :data:`FAIR_BOUND` loop radii of the loop's centroid): a
    twisted loop (one climbing around an object in the hole) triangulates with folds, and on
    folds the system is nearly singular and can throw the patch metres away. If neither stays,
    the membrane ``L x = 0`` (umbrella, loop fixed): each new vertex is the mean of its
    neighbours, so the patch stays inside the loop's convex hull, a soap film over the hole. The
    refined positions are the last resort.
    """
    from scipy.sparse.linalg import spsolve

    n, q = len(loop_g), len(new_pos)
    centre = V[loop_g].mean(axis=0)
    reach = FAIR_BOUND * float(np.linalg.norm(V[loop_g] - centre, axis=1).max())

    def near(x: np.ndarray) -> bool:
        return bool(np.all(np.isfinite(x))) and float(np.linalg.norm(x - centre, axis=1).max()) <= reach
    ring_faces = np.unique(np.concatenate([vf_face[vf_ptr[v] : vf_ptr[v + 1]] for v in loop_g.tolist()]))
    RF = F[ring_faces]
    # local numbering: loop 0..n-1, new n..n+q-1, then the other ring vertices
    others = np.setdiff1d(np.unique(RF), loop_g)
    lut_keys = np.concatenate([loop_g, others])
    lut_vals = np.concatenate([np.arange(n), np.arange(n + q, n + q + len(others))])
    srt = np.argsort(lut_keys)
    RFl = lut_vals[srt[np.searchsorted(lut_keys[srt], RF)]] if len(RF) else np.zeros((0, 3), np.int64)
    T = np.vstack([tris_local.astype(np.int64), RFl])
    N = n + q + len(others)
    X = np.vstack([V[loop_g], new_pos, V[others]])
    U = np.arange(n, n + q)
    Kn = np.concatenate([np.arange(n), np.arange(n + q, N)])
    sol = new_pos
    for cotan, passes in ((True, 2), (False, 1)):
        ok = True
        for _ in range(passes):
            X[U] = sol
            try:
                L = _laplacian(T, X, cotan)
                M = (L[U] @ L).tocsc()
                rhs = -(M[:, Kn] @ X[Kn])
                cand = np.asarray(spsolve(M[:, U].tocsc(), rhs)).reshape(q, 3)
            except Exception:  # singular system
                ok = False
                break
            if not near(cand):
                ok = False
                break
            sol = cand
        if ok:
            return sol
        sol = new_pos
    log.info("fill_holes: the smooth patch of a %d-edge hole left the hole; using a membrane patch", n)
    try:
        Tp = tris_local.astype(np.int64)
        Xp = np.vstack([V[loop_g], new_pos])
        Lp = _laplacian(Tp, Xp, cotan=False)
        Up = np.arange(n, n + q)
        cand = np.asarray(spsolve(Lp[Up][:, Up].tocsc(), -(Lp[Up][:, np.arange(n)] @ Xp[:n]))).reshape(q, 3)
        if near(cand):
            return cand
    except Exception:  # noqa: BLE001 (singular: keep the refined positions)
        pass
    return new_pos


# --------------------------------------------------------------------------- main entry
def _simple_loops(verts: list[int]) -> list[list[int]]:
    """Split a cyclic vertex list at repeated vertices into simple loops: each as the positions of
    its vertices in ``verts`` (the loop's edge from a position is the edge that starts there)."""
    out, stack = [], [list(range(len(verts)))]
    while stack:
        lp = stack.pop()
        seen: dict[int, int] = {}
        for i, pos in enumerate(lp):
            v = verts[pos]
            if v in seen:
                j = seen[v]
                stack.append(lp[j:i])
                stack.append(lp[i:] + lp[:j])
                break
            seen[v] = i
        else:
            if len(lp) >= 3:
                out.append(lp)
    return out


def fill_holes(V: np.ndarray, F: np.ndarray, max_hole_mm: float, *, include_borders: bool = False,
               progress: Progress | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Fill every hole whose perimeter is at most ``max_hole_mm`` (see the module docstring).

    Outer borders are left open unless ``include_borders`` ("watertight": the edge of a part
    scanned all round, e.g. where it sat on the table, is closed too, within the same size
    limit). Returns ``(V, F)``: the input vertices and faces first, then the new ones.
    """
    V, F = as_mesh(V, F)
    if len(F) == 0 or not max_hole_mm or max_hole_mm <= 0:
        return V.copy(), F.copy()
    loops, is_border, info = find_holes(V, F)
    cand = [k for k in range(len(loops)) if loops.closed[k] and (include_borders or not is_border[k])
            and loops.perimeter[k] <= float(max_hole_mm) and len(loops.verts(k)) >= 3]
    if not cand:
        return V.copy(), F.copy()
    et = info["et"]
    sigma = vertex_edge_length(V, et)
    fn, _ = face_normals(V, F)
    face_of_he = loops.halfedges // 3
    # existing mesh edges between vertices of the same hole (forbidden diagonals)
    owner = np.full(len(V), -1, dtype=np.int64)
    for k in cand:
        owner[loops.verts(k)] = k
    o0, o1 = owner[et.v0], owner[et.v1]
    same = np.flatnonzero((o0 >= 0) & (o0 == o1))
    pairs_by_loop: dict[int, set[tuple[int, int]]] = {}
    for e in same.tolist():
        pairs_by_loop.setdefault(int(o0[e]), set()).add((int(et.v0[e]), int(et.v1[e])))
    # vertex -> faces, for the fairing ring
    flat = F.reshape(-1)
    vf_order = np.argsort(flat, kind="stable")
    vf_face = vf_order // 3
    vf_ptr = np.concatenate([[0], np.cumsum(np.bincount(flat, minlength=len(V)))])
    new_V: list[np.ndarray] = []
    new_F: list[np.ndarray] = []
    n_next = len(V)
    filled = 0
    for done, k in enumerate(cand):
        if progress is not None and done % 20 == 0:
            progress(done / len(cand), f"Filling hole {done + 1} of {len(cand)}")
        verts = loops.verts(k).tolist()
        loop_faces = face_of_he[loops.ptr[k] : loops.ptr[k + 1]].tolist()  # the face of the edge that starts at each position
        gpairs = pairs_by_loop.get(k, set())
        for at in _simple_loops(verts):
            if len(at) == 3 and loop_faces[at[0]] == loop_faces[at[1]] == loop_faces[at[2]]:
                # the three open edges of one face: a lone triangle that touches the mesh at a vertex
                # at most. Its outline is not a hole; filling it would add the same triangle reversed.
                continue
            lp = [verts[i] for i in at]
            g = np.asarray(lp, dtype=np.int64)
            n = len(g)
            P = V[g]
            nb = fn[[loop_faces[i] for i in at]]  # by position: a loop can pass a vertex twice, with another face each time
            l_of = {v: i for i, v in enumerate(lp)}
            local_pairs = set()
            for a, b in gpairs:
                if a in l_of and b in l_of:
                    ia, ib = l_of[a], l_of[b]
                    local_pairs.add((min(ia, ib), max(ia, ib)))
            if n == 3:
                tris = [(2, 1, 0)]
            else:
                tris = None
                if n <= MAX_DP_EDGES:
                    forbid = np.zeros((n, n), dtype=bool)
                    for ia, ib in local_pairs:
                        forbid[ia, ib] = forbid[ib, ia] = True
                    tris = _triangulate_dp(P, nb, forbid)
                if tris is None:
                    tris = _ear_clip(P, local_pairs)
            patch = _Patch(P, sigma[g], tris, local_pairs)
            patch.refine()
            q = len(patch.pos) - n
            T = np.asarray(patch.tris, dtype=np.int64)
            pos_new = np.asarray(patch.pos[n:], dtype=np.float64).reshape(-1, 3)
            if q:
                pos_new = _fair(g, pos_new, T, V, F, vf_ptr, vf_face)
            gid = np.concatenate([g, np.arange(n_next, n_next + q)])
            new_F.append(gid[T])
            if q:
                new_V.append(pos_new)
            n_next += q
            filled += 1
    if progress is not None:
        progress(1.0, f"Filled {filled} holes")
    V2 = np.vstack([V] + new_V) if new_V else V.copy()
    F2 = np.vstack([F] + new_F) if new_F else F.copy()
    log.debug("fill_holes: %d holes up to %.1f mm filled, %d new vertices", filled, max_hole_mm, len(V2) - len(V))
    return V2, F2
