"""Simplification: quadric error decimation (Garland and Heckbert 1997), collapsed in batches.

Every vertex carries a quadric: the sum of the squared-distance quadrics of its faces' planes
(area weighted), plus, along open borders, planes through each border edge at right angles to
its face (weight :data:`BOUNDARY_WEIGHT`), so the edge of an open scan keeps its shape. An edge
collapse merges its two vertices into the point that minimises the summed quadric (or the best
of the two ends and the midpoint when that point is not well defined or would land far away),
and its cost is the quadric there.

Open3D's and VTK's implementations collapse one edge at a time and take 16-30 s for 1M -> 200k
triangles on a laptop CPU. Here each round collapses many edges at once (all numpy):

1. cost and target point of every collapsible edge (not non-manifold; an inner edge between
   two border vertices would pinch the surface and is skipped);
2. a matching of cheap edges (no two share a vertex): an edge is taken when it is the cheapest
   edge of both its vertices, among the cheapest edges the remaining reduction needs (equal
   costs in random order); :data:`PASSES` passes, each among the vertices still free;
3. the link condition, edge by edge: the two vertices may share no neighbour besides the
   opposite corners of the edge's faces (a failed edge sits out the round, so its neighbours
   get a turn);
4. with every chosen collapse applied at once, no face may flip or turn by more than about 80
   degrees (per bad face its most expensive collapse is dropped, and the test repeats);
5. collapse, add up the quadrics, and build the next round's edge table; a collapse that left a
   non-manifold edge (neighbouring collapses can do that together) is undone.

A round removes up to about a third of the triangles, so 1M -> 200k takes about six rounds (about
10 s for 1.3M triangles on a Ryzen 7 7735U). A target at or above the current count changes
nothing; the result is within 0.1 % above the target (:data:`TOLERANCE`) unless the mesh cannot be
simplified further without breaking it.
"""
from __future__ import annotations

import logging
from typing import Callable

import numpy as np

from ohs.mesh.topology import as_mesh, compact, edge_table, rows

__all__ = ["decimate", "decimate_target"]

log = logging.getLogger(__name__)

BOUNDARY_WEIGHT = 10.0
MIN_TRIANGLES = 4
MAX_ROUNDS = 200
PASSES = 3  # matching passes per round (one edge table)
FLIP_COS = 0.17  # a face may turn by at most ~80 degrees
TOLERANCE = 0.001  # stop within 0.1 % above the target
Progress = Callable[[float, str], None]


def decimate_target(n_triangles: int, target_triangles: int | None = None, ratio: float | None = None) -> int:
    """The triangle count to aim for; ``ValueError`` unless exactly one of the two is given."""
    if (target_triangles is None) == (ratio is None):
        raise ValueError("give either target_triangles or ratio")
    if target_triangles is not None:
        t = int(target_triangles)
        if t < MIN_TRIANGLES:
            raise ValueError(f"target_triangles must be at least {MIN_TRIANGLES}")
        return t
    r = float(ratio)
    if not 0.0 < r <= 1.0:
        raise ValueError("ratio must be in (0, 1]")
    return max(MIN_TRIANGLES, int(round(r * n_triangles)))


# --------------------------------------------------------------------------- quadrics
def _plane_quadrics(n: np.ndarray, d: np.ndarray, w: np.ndarray) -> np.ndarray:
    """(K,10) quadrics ``w [n d]^T [n d]``: xx xy xz xd yy yz yd zz zd dd."""
    a, b, c = n[:, 0], n[:, 1], n[:, 2]
    return np.stack([a * a, a * b, a * c, a * d, b * b, b * c, b * d, c * c, c * d, d * d], axis=1) * w[:, None]


def _vertex_quadrics(V: np.ndarray, F: np.ndarray) -> np.ndarray:
    N = len(V)
    p0, p1, p2 = rows(V, F[:, 0]), rows(V, F[:, 1]), rows(V, F[:, 2])
    cr = np.cross(p1 - p0, p2 - p0)
    nrm = np.sqrt(np.einsum("ij,ij->i", cr, cr))
    n = np.divide(cr, nrm[:, None], out=np.zeros_like(cr), where=nrm[:, None] > 0)
    Kf = _plane_quadrics(n, -np.einsum("ij,ij->i", n, p0), 0.5 * nrm)
    flat = F.reshape(-1)
    Q = np.stack([np.bincount(flat, np.repeat(Kf[:, k], 3), N) for k in range(10)], axis=1)
    # border constraints: a plane through each border edge, at right angles to its face
    et = edge_table(F, N)
    bh = np.flatnonzero(et.counts[et.he_edge] == 1)
    if len(bh):
        f = bh // 3
        a = F.reshape(-1)[bh]
        b = F[:, [1, 2, 0]].reshape(-1)[bh]
        pa, pb = rows(V, a), rows(V, b)
        e = pb - pa
        m = np.cross(e, rows(n, f))
        mn = np.sqrt(np.einsum("ij,ij->i", m, m))
        m = np.divide(m, mn[:, None], out=np.zeros_like(m), where=mn[:, None] > 0)
        Kb = _plane_quadrics(m, -np.einsum("ij,ij->i", m, pa), BOUNDARY_WEIGHT * np.einsum("ij,ij->i", e, e))
        for k in range(10):
            Q[:, k] += np.bincount(a, Kb[:, k], N) + np.bincount(b, Kb[:, k], N)
    return Q


def _qcost(q: np.ndarray, x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    """Quadric value at points ``(x, y, z)``; ``q`` is (10, C) (rows contiguous)."""
    return (x * (q[0] * x + 2 * (q[1] * y + q[2] * z + q[3])) + y * (q[4] * y + 2 * (q[5] * z + q[6]))
            + z * (q[7] * z + 2 * q[8]) + q[9])


def _optimal(q: np.ndarray, pu: np.ndarray, pv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Target points (C,3) and costs (C,) of edge collapses with summed quadrics ``q`` (10, C).

    The quadric's minimum when it is well defined and near the edge, else the best of the
    midpoint and the two ends."""
    a00, a01, a02, b0, a11, a12, b1, a22, b2 = q[:9]
    i00 = a11 * a22 - a12 * a12
    i01 = a02 * a12 - a01 * a22
    i02 = a01 * a12 - a02 * a11
    i11 = a00 * a22 - a02 * a02
    i12 = a01 * a02 - a00 * a12
    i22 = a00 * a11 - a01 * a01
    det = a00 * i00 + a01 * i01 + a02 * i02
    tr = a00 + a11 + a22
    good = (tr > 0) & (np.abs(det) > 1e-7 * (tr / 3.0) ** 3)
    sd = -1.0 / np.where(good, det, 1.0)
    xs = (i00 * b0 + i01 * b1 + i02 * b2) * sd
    ys = (i01 * b0 + i11 * b1 + i12 * b2) * sd
    zs = (i02 * b0 + i12 * b1 + i22 * b2) * sd
    P0, P1 = pu.T, pv.T
    M = 0.5 * (P0 + P1)
    d = P1 - P0
    lim = d[0] * d[0] + d[1] * d[1] + d[2] * d[2]  # (2 * half length)^2
    good &= (xs - M[0]) ** 2 + (ys - M[1]) ** 2 + (zs - M[2]) ** 2 <= lim + 1e-18  # no far-away targets
    best = np.stack([M[0], M[1], M[2]])
    cost = _qcost(q, M[0], M[1], M[2])
    for P in (P0, P1):
        c = _qcost(q, P[0], P[1], P[2])
        m = c < cost
        best[:, m] = P[:, m]
        cost = np.where(m, c, cost)
    cx = _qcost(q, xs, ys, zs)
    m = good & (cx <= cost)
    best[0, m], best[1, m], best[2, m] = xs[m], ys[m], zs[m]
    cost = np.where(m, cx, cost)
    return np.ascontiguousarray(best.T), np.maximum(cost, 0.0)


# --------------------------------------------------------------------------- one round
def _first_rank(vert: np.ndarray, n: int, size: int) -> np.ndarray:
    """For each vertex, the smallest position at which it appears in ``vert`` (``size`` if never)."""
    out = np.full(n, size, dtype=np.int64)
    out[vert[::-1]] = np.arange(len(vert) - 1, -1, -1)  # the last write (the smallest position) wins
    return out


def _adjacency(v0: np.ndarray, v1: np.ndarray, n: int) -> tuple:
    """Neighbour lists from an edge table: ``(ptr0, v1)`` (v0 is sorted) and ``(ptr1, v0 by v1)``."""
    ptr0 = np.searchsorted(v0, np.arange(n + 1))
    o1 = np.argsort(v1)
    ptr1 = np.searchsorted(v1[o1], np.arange(n + 1))
    return (ptr0, v1), (ptr1, v0[o1])


def _common_neighbours(cu: np.ndarray, cv: np.ndarray, adj: tuple, n: int) -> np.ndarray:
    """Number of vertices adjacent to both ends of each edge ``(cu, cv)``."""
    slots, nbrs = [], []
    S = len(cu)
    for ends in (cu, cv):
        for ptr, nb in adj:
            lo, hi = ptr[ends], ptr[ends + 1]
            cnt = hi - lo
            tot = int(cnt.sum())
            if tot == 0:
                continue
            slot = np.repeat(np.arange(S), cnt)
            start = np.repeat(lo - np.cumsum(cnt) + cnt, cnt)
            idx = start + np.arange(tot)
            slots.append(slot)
            nbrs.append(nb[idx])
    if not slots:
        return np.zeros(S, dtype=np.int64)
    key = np.sort(np.concatenate(slots) * n + np.concatenate(nbrs))
    dup = key[1:] == key[:-1]
    return np.bincount(key[1:][dup] // n, minlength=S)


def _without_flips(V: np.ndarray, F: np.ndarray, sel: np.ndarray, cu: np.ndarray, cv: np.ndarray, x: np.ndarray,
                   cost: np.ndarray, C: int) -> np.ndarray:
    """The chosen edges that can collapse together with no face flipping or turning by more than
    ~80 degrees. Per bad face its most expensive collapse is dropped and the test repeats, but only
    on the faces that held a corner of a dropped collapse (the others did not change)."""
    owner = np.full(len(V), -1, dtype=np.int64)
    owner[cu[sel]] = sel
    owner[cv[sel]] = sel
    ids = np.take(owner, F)
    rows_any = np.flatnonzero((ids >= 0).any(axis=1))
    if len(rows_any) == 0:
        return sel
    Fc = F[rows_any]
    ic = ids[rows_any]
    P0 = [rows(V, Fc[:, k]) for k in range(3)]
    n_old = np.cross(P0[1] - P0[0], P0[2] - P0[0])
    alive = np.zeros(C, dtype=bool)
    alive[sel] = True
    sub = np.arange(len(rows_any))
    for _ in range(8):
        icx = ic[sub]
        act = (icx >= 0) & alive[np.maximum(icx, 0)]
        # faces holding both ends of one collapse vanish; the others must stay upright
        gone = ((act[:, 0] & act[:, 1] & (icx[:, 0] == icx[:, 1])) | (act[:, 1] & act[:, 2] & (icx[:, 1] == icx[:, 2]))
                | (act[:, 0] & act[:, 2] & (icx[:, 0] == icx[:, 2])))
        P = []
        for k in range(3):
            pk = P0[k][sub]
            m = act[:, k]
            if m.any():
                pk[m] = rows(x, icx[m, k])
            P.append(pk)
        n_new = np.cross(P[1] - P[0], P[2] - P[0])
        no = n_old[sub]
        lo = np.sqrt(np.einsum("ij,ij->i", no, no) * np.einsum("ij,ij->i", n_new, n_new))
        bad = np.flatnonzero((np.einsum("ij,ij->i", no, n_new) <= FLIP_COS * lo) & act.any(axis=1) & ~gone)
        if len(bad) == 0:
            break
        ib, ab = icx[bad], act[bad]
        cb = np.where(ab, cost[np.maximum(ib, 0)], -1.0)
        drop = ib[np.arange(len(bad)), cb.argmax(axis=1)]
        alive[drop] = False
        dropped = np.zeros(C, dtype=bool)
        dropped[drop] = True
        held = ic[sub] >= 0
        sub = sub[(held & dropped[np.maximum(ic[sub], 0)]).any(axis=1)]
        if len(sub) == 0:
            break
    return sel[alive[sel]]


def _round(V: np.ndarray, F: np.ndarray, Q: np.ndarray, target: int, rng: np.random.Generator,
           et) -> tuple[np.ndarray, int, object]:
    """One batch of collapses (``V`` and ``Q`` are updated in place).

    ``et`` is the edge table of ``F``; returns ``(F, collapses, edge table of the new F)``.
    Equal costs (a flat area costs nothing) are ordered at random: in index order the cheapest-
    edge-of-both-vertices rule would pick almost nothing there."""
    N, M = len(V), len(F)
    u, v, counts = et.v0, et.v1, et.counts
    vb = np.zeros(N, dtype=bool)
    b = counts == 1
    vb[u[b]] = True
    vb[v[b]] = True
    vnm = np.zeros(N, dtype=bool)
    nm = counts > 2
    vnm[u[nm]] = True
    vnm[v[nm]] = True
    ce = np.flatnonzero((counts <= 2) & ~vnm[u] & ~vnm[v] & ~((counts == 2) & vb[u] & vb[v]))
    if len(ce) == 0:
        return F, 0, et
    cu, cv = u[ce], v[ce]
    pu, pv = rows(V, cu), rows(V, cv)
    x, cost = _optimal(np.ascontiguousarray((rows(Q, cu) + rows(Q, cv)).T), pu, pv)
    C = len(ce)
    need = max(1, (M - target + 1) // 2)
    pool = int(min(C, max(2 * need, C // 10, 64)))
    order = np.argpartition(cost, pool - 1)[:pool] if pool < C else np.arange(C)
    order = order[np.lexsort((rng.random(len(order)), cost[order]))]
    adj = _adjacency(u, v, N)
    used = np.zeros(N, dtype=bool)
    excluded = np.zeros(C, dtype=bool)  # failed the link test: let their neighbours go first
    chosen: list[np.ndarray] = []
    n_acc = 0
    for _ in range(PASSES):
        # 2. a matching: local minima among the cheapest edges, away from vertices already taken
        o = order[~used[cu[order]] & ~used[cv[order]] & ~excluded[order]]
        if len(o) == 0:
            break
        ou, ov = cu[o], cv[o]
        best = np.minimum(_first_rank(ou, N, len(o)), _first_rank(ov, N, len(o)))
        r = np.arange(len(o))
        sel = o[(best[ou] == r) & (best[ov] == r)]
        if len(sel) == 0:
            break
        # 3. link condition, edge by edge
        common = _common_neighbours(cu[sel], cv[sel], adj, N)
        ok = common <= counts[ce[sel]]
        excluded[sel[~ok]] = True
        sel = sel[ok]
        if len(sel) == 0:
            continue
        chosen.append(sel)
        n_acc += len(sel)
        used[cu[sel]] = True
        used[cv[sel]] = True
        if n_acc >= need:
            break
    if not chosen:
        return F, 0, et
    sel = np.concatenate(chosen)
    # 4. no face may flip when all chosen collapses move at once (tested before keeping only the
    # cheapest `need`: the very cheapest edges are often the ones that fail, round after round)
    sel = _without_flips(V, F, sel, cu, cv, x, cost, C)
    if len(sel) > need:  # fewer collapses move other corners together: test again
        sel = _without_flips(V, F, sel[np.argsort(cost[sel], kind="stable")[:need]], cu, cv, x, cost, C)
    if len(sel) == 0:
        return F, 0, et
    # 5. collapse, then undo collapses that left a non-manifold edge (neighbouring collapses can)
    su, sv = cu[sel], cv[sel]
    old_v, old_q = V[su].copy(), Q[su].copy()
    remap = np.arange(N)
    remap[sv] = su
    V[su] = x[sel]
    Q[su] += Q[sv]
    active = np.ones(len(sel), dtype=bool)
    for attempt in range(5):  # the faces are always rebuilt after the last undo
        F2 = remap[F]
        F2 = F2[(F2[:, 0] != F2[:, 1]) & (F2[:, 1] != F2[:, 2]) & (F2[:, 0] != F2[:, 2])]
        et2 = edge_table(F2, N)
        bad = et2.counts > 2
        if attempt == 4 or not bad.any():
            break
        touched = np.zeros(N, dtype=bool)
        touched[su[active]] = True
        be = bad & (touched[et2.v0] | touched[et2.v1])
        if not be.any():
            break  # non-manifold before this round
        hit = np.zeros(N, dtype=bool)
        hit[et2.v0[be]] = True
        hit[et2.v1[be]] = True
        undo = active & hit[su]
        V[su[undo]] = old_v[undo]
        Q[su[undo]] = old_q[undo]
        remap[sv[undo]] = sv[undo]
        active &= ~undo
    return F2, int(active.sum()), et2


def decimate(V: np.ndarray, F: np.ndarray, target_triangles: int | None = None, ratio: float | None = None, *,
             progress: Progress | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Quadric decimation to ``target_triangles`` or ``ratio`` (see the module docstring)."""
    V, F = as_mesh(V, F)
    target = decimate_target(len(F), target_triangles, ratio)
    M0 = len(F)
    if target >= M0:
        return V.copy(), F.copy()
    center = 0.5 * (V.min(axis=0) + V.max(axis=0))
    X = V - center
    Q = _vertex_quadrics(X, F)
    rng = np.random.default_rng(0)
    et = edge_table(F, len(X))
    close = target + int(TOLERANCE * target)  # the last few collapses cost whole rounds
    for it in range(MAX_ROUNDS):
        if len(F) <= close:
            break
        F, done, et = _round(X, F, Q, target, rng, et)
        if progress is not None:
            progress(min(0.99, (M0 - len(F)) / max(M0 - target, 1)), f"Simplifying: {len(F):,} triangles")
        if done == 0:
            log.info("decimate: stopped at %d triangles (target %d): nothing more can be collapsed", len(F), target)
            break
    X2, F2, _ = compact(X, F)
    log.debug("decimate: %d -> %d triangles (target %d)", M0, len(F2), target)
    return X2 + center, F2
