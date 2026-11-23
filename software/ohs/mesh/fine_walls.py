"""Finer mesh detail on thin walls only (ENGINE_PLAN X5, step 2): the mesh made at its own mesh detail (1 mm) is
refined where the part has thin walls, onto the surface of a second model fused at 0.5 mm in those places alone.

Why only there, and why this way (``VALIDATION.md``, "Fine mesh detail on thin walls only"):

* At mesh detail 0.5 mm walls seen from both sides read 0.23 to 0.67 mm nearer their true thickness; the whole scan at
  0.5 mm takes about twice the time and eight times the memory, mostly for a mesh four times as fine everywhere. The
  walls are a few per cent of the surface (:func:`ohs.mesh.thin_parts.find_thin_walls`: the same finding as the
  thin-parts hint, on the base mesh).
* **The regions:** the bricks of :data:`ohs.mesh.thin_parts.BRICK_VOXELS` (8) mesh detail holding a vertex that reads
  a thin wall, grown by :data:`REGION_GROW_BRICKS` (1) brick all round, so that both faces of a wall (1.7 to 2.7 mm
  apart) and the surface round its foot and its rim are in. A finer model (:class:`ohs.track.recon_tsdf.ConfidenceTsdf`
  at :data:`FINE_VOXEL_MM`) fuses the same frames at the same poses into those bricks only
  (:meth:`~ohs.track.recon_tsdf.ConfidenceTsdf.limit_bricks`); what it fuses there is what the model of the whole scan at
  0.5 mm would hold.
* **The stitch** (:func:`refine_onto`): no second mesh is cut in and sewn on. The triangles of the 1 mm mesh inside the
  regions are split in four (each edge at its middle) and those at the regions' border in two or three, so that every
  split edge is split on both sides (no T-junction, no crack, no surface doubled; the open borders stay the same loops).
  Then each vertex inside moves along its normal onto the finer model's surface: the nearest place within
  :data:`MAX_MOVE_VOXELS` mesh detail (of the 1 mm mesh) where the finer model crosses from inside to outside the
  part *in the direction the vertex faces*. A wall's other face faces the other way, so no vertex can jump onto it and
  no wall can close: a face only moves onto its own surface as the 0.5 mm model sees it. A vertex whose search finds
  no such crossing (the finer model saw too little there, or the crossing is farther) **keeps its 1 mm place**, and so
  does every corner of a triangle the move would turn over. Its sigma and count of looks are the finer model's there,
  with the mesh's own terms (:func:`ohs.track.recon_tsdf.mesh_sigma_terms`) on the refined mesh.
* Every vertex and triangle outside the regions is left exactly as it was; a mesh without a thin wall is not touched.
"""
from __future__ import annotations

import math
import time
from typing import Any

import numpy as np

__all__ = ["FINE_VOXEL_MM", "REGION_GROW_BRICKS", "MAX_MOVE_VOXELS", "FineField", "region_bricks", "fine_bricks",
           "in_bricks", "subdivide", "refine_onto"]

VERSION = 1
#: the finer model's mesh detail (mm): the thin-sheet preset's, the one measured to help walls (VALIDATION.md, P1)
FINE_VOXEL_MM = 0.5
#: the flagged bricks are grown by this many bricks all round (a wall's two faces, its foot and its rim)
REGION_GROW_BRICKS = 1
#: a vertex moves at most this many mesh detail (of the coarse mesh) onto the finer surface; farther: it stays
MAX_MOVE_VOXELS = 1.0
#: the search along the normal samples the finer model every this many of its mesh detail
SEARCH_STEP_FINE = 0.5
#: a voxel of the finer model counts for the search when it has at least this many observations. Marching cubes takes
#: more than 3 (``ConfidenceTsdf``'s threshold); at 0.5 mm the side of a standing wall seen less often holds voxels with
#: 1 to 3 just in front of its surface (the truncation is half as long, so a grazing view leaves them out), where the
#: 0.5 mm mesh has holes. Measured on the standing walls (fins-s0, tracked): with "more than 3" a third of the walls'
#: vertices find no place and keep their 1 mm place (walls 0.97 / 0.60 / 0.61 mm too thick); with "at least one"
#: 0.77 / 0.52 / 0.47 (1 mm: 1.23 / 0.81 / 0.66); where the 0.5 mm mesh has surface the two read the same.
MIN_OBSERVATIONS = 1
BRICK = 8


def _encode(k: np.ndarray) -> np.ndarray:
    from ohs.track.recon_tsdf import _encode as enc  # noqa: PLC0415

    return enc(k)


# --------------------------------------------------------------------------- the regions
def region_bricks(P: np.ndarray, brick_mm: float, grow: int = REGION_GROW_BRICKS) -> np.ndarray:
    """The bricks (``floor(x / brick_mm)``, (K,3) int64, sorted, unique) holding the points ``P``, grown by ``grow``
    bricks all round (26 neighbours each step)."""
    P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
    if len(P) == 0:
        return np.zeros((0, 3), dtype=np.int64)
    K = np.unique(np.floor(P / float(brick_mm)).astype(np.int64), axis=0)
    g = int(grow)
    if g > 0:
        r = np.arange(-g, g + 1)
        off = np.stack(np.meshgrid(r, r, r, indexing="ij"), axis=-1).reshape(-1, 3)
        K = np.unique((K[:, None, :] + off[None]).reshape(-1, 3), axis=0)
    return K


def fine_bricks(coarse: np.ndarray, coarse_brick_mm: float, fine_brick_mm: float) -> np.ndarray:
    """The bricks of size ``fine_brick_mm`` that overlap the bricks ``coarse`` of size ``coarse_brick_mm`` (both on
    grids from the origin): (M,3) int64, sorted, unique."""
    C = np.asarray(coarse, dtype=np.int64).reshape(-1, 3)
    if len(C) == 0:
        return np.zeros((0, 3), dtype=np.int64)
    a, b = float(coarse_brick_mm), float(fine_brick_mm)
    lo = np.floor(C * a / b + 1e-9).astype(np.int64)
    hi = np.ceil((C + 1) * a / b - 1e-9).astype(np.int64) - 1
    n = int((hi - lo).max()) + 1
    r = np.arange(n)
    off = np.stack(np.meshgrid(r, r, r, indexing="ij"), axis=-1).reshape(-1, 3)
    K = (lo[:, None, :] + off[None]).reshape(-1, 3)
    ok = np.all(K <= np.repeat(hi, len(off), axis=0), axis=1)
    return np.unique(K[ok], axis=0)


def in_bricks(P: np.ndarray, keys: np.ndarray, brick_mm: float) -> np.ndarray:
    """Whether each point lies in one of the bricks ``keys`` (sorted or not) of size ``brick_mm``."""
    P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
    K = np.asarray(keys, dtype=np.int64).reshape(-1, 3)
    if len(P) == 0 or len(K) == 0:
        return np.zeros(len(P), dtype=bool)
    a = np.unique(_encode(K))
    e = _encode(np.floor(P / float(brick_mm)).astype(np.int64))
    pos = np.minimum(np.searchsorted(a, e), len(a) - 1)
    return a[pos] == e


# --------------------------------------------------------------------------- the finer model's field
class FineField:
    """The finer model's values at any point, by trilinear interpolation of its voxels (samples at ``i * voxel_mm``,
    as marching cubes takes them): the value the mesh is made from (``tsdf``, mm: :meth:`ConfidenceTsdf.fused_tsdf`),
    each voxel's sigma (mm) and count of looks. A voxel counts when its observations are above ``threshold``, as for
    marching cubes; a point whose eight voxels do not all count has no value."""

    def __init__(self, keys: np.ndarray, tsdf: np.ndarray, nobs: np.ndarray, voxel_mm: float, threshold: float,
                 sigma: np.ndarray | None = None, looks: np.ndarray | None = None) -> None:
        self.voxel_mm = float(voxel_mm)
        K = np.asarray(keys, dtype=np.int64).reshape(-1, 3)
        e = _encode(K)
        self._order = np.argsort(e, kind="stable")
        self._enc = e[self._order]
        n = len(K)
        self.tsdf = np.asarray(tsdf, dtype=np.float32).reshape(n, BRICK ** 3)
        self.valid = np.asarray(nobs, dtype=np.float32).reshape(n, BRICK ** 3) > np.float32(threshold)
        self.sigma = None if sigma is None else np.asarray(sigma, dtype=np.float32).reshape(n, BRICK ** 3)
        self.looks = None if looks is None else np.asarray(looks, dtype=np.float32).reshape(n, BRICK ** 3)
        self.n_bricks = n

    @classmethod
    def from_model(cls, model: Any, min_observations: int = MIN_OBSERVATIONS) -> FineField:
        """The field of a :class:`~ohs.track.recon_tsdf.ConfidenceTsdf` (downloaded once); a voxel counts from
        ``min_observations`` observations (:data:`MIN_OBSERVATIONS`)."""
        from ohs.track.recon_tsdf import voxel_sigma  # noqa: PLC0415

        keys, ch = model.download()
        sig = voxel_sigma(ch["wsum"], ch["m2"], ch["nobs"], ch["nlook"], ch["mxc"], ch["hnear"], ch["snh2"],
                          voxel_mm=model.voxel_mm) if len(keys) else np.zeros((0, BRICK, BRICK, BRICK), np.float32)
        return cls(keys, model.fused_tsdf(ch) if len(keys) else ch["tsdf"], ch["nobs"], model.voxel_mm,
                   float(min_observations) - 0.5, sigma=sig, looks=ch["nlook"])

    def _lookup(self, G: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Voxel coordinates ``G`` (K,3) int64 -> ``(slot, local index, the voxel counts)``."""
        if self.n_bricks == 0:
            z = np.zeros(len(G), dtype=np.int64)
            return z, z, np.zeros(len(G), dtype=bool)
        b = G >> 3
        loc = G & 7
        e = _encode(b)
        pos = np.minimum(np.searchsorted(self._enc, e), len(self._enc) - 1)
        found = self._enc[pos] == e
        slot = self._order[pos]
        li = loc[:, 2] * 64 + loc[:, 1] * 8 + loc[:, 0]
        ok = found & self.valid[slot, li]
        return slot, li, ok

    def sample(self, P: np.ndarray, what: str = "tsdf") -> tuple[np.ndarray, np.ndarray]:
        """``(values, ok)`` at the points ``P`` (K,3) mm. ``what``: "tsdf" (ok only where all eight voxels count) or
        "sigma" / "looks" (the voxels that count, their weights renormalised; ok where any counts)."""
        P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
        g = P / self.voxel_mm
        i0 = np.floor(g).astype(np.int64)
        f = g - i0
        arr = self.tsdf if what == "tsdf" else self.sigma if what == "sigma" else self.looks
        acc = np.zeros(len(P), dtype=np.float64)
        wsum = np.zeros(len(P), dtype=np.float64)
        allok = np.ones(len(P), dtype=bool)
        for dz in (0, 1):
            wz = f[:, 2] if dz else 1.0 - f[:, 2]
            for dy in (0, 1):
                wy = f[:, 1] if dy else 1.0 - f[:, 1]
                for dx in (0, 1):
                    wx = f[:, 0] if dx else 1.0 - f[:, 0]
                    slot, li, ok = self._lookup(i0 + np.array([dx, dy, dz], dtype=np.int64))
                    w = wx * wy * wz
                    val = arr[slot, li].astype(np.float64) if self.n_bricks else np.zeros(len(P))
                    acc += np.where(ok, w * val, 0.0)
                    wsum += np.where(ok, w, 0.0)
                    allok &= ok
        if what == "tsdf":
            return acc, allok
        ok = wsum > 1e-9
        return np.divide(acc, wsum, out=np.zeros_like(acc), where=ok), ok

    def gradient(self, P: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """``(unit gradient of the tsdf (K,3), ok)`` by central differences half a voxel either side."""
        P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
        h = 0.5 * self.voxel_mm
        G = np.zeros((len(P), 3))
        ok = np.ones(len(P), dtype=bool)
        for a in range(3):
            d = np.zeros(3)
            d[a] = h
            p1, o1 = self.sample(P + d)
            p0, o0 = self.sample(P - d)
            G[:, a] = (p1 - p0) / (2 * h)
            ok &= o1 & o0
        n = np.linalg.norm(G, axis=1)
        ok &= n > 1e-9
        return np.divide(G, n[:, None], out=np.zeros_like(G), where=n[:, None] > 1e-9), ok

    def project(self, P: np.ndarray, N: np.ndarray, max_mm: float, step_mm: float) -> tuple[np.ndarray, np.ndarray]:
        """Each point ``P`` moved along its unit normal ``N`` onto the field's surface (module docstring): the
        crossing from inside (< 0) to outside (>= 0) in the direction of ``N`` nearest the point, within ``max_mm``,
        between two samples ``step_mm`` apart that both have a value, placed by two steps of false position.
        ``(distance along N (K,), found (K,))``; 0 where none was found."""
        P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
        N = np.asarray(N, dtype=np.float64).reshape(-1, 3)
        K = len(P)
        n = max(1, int(math.ceil(max_mm / step_mm)))
        s = np.arange(-n, n + 1, dtype=np.float64) * (max_mm / n)
        out = np.zeros(K)
        found = np.zeros(K, dtype=bool)
        if K == 0:
            return out, found
        chunk = max(1, 400_000 // len(s))
        for c0 in range(0, K, chunk):
            c1 = min(K, c0 + chunk)
            Q = P[c0:c1, None, :] + s[None, :, None] * N[c0:c1, None, :]
            v, ok = self.sample(Q.reshape(-1, 3))
            v = v.reshape(c1 - c0, len(s))
            ok = ok.reshape(c1 - c0, len(s))
            cross = ok[:, :-1] & ok[:, 1:] & (v[:, :-1] < 0) & (v[:, 1:] >= 0)
            mid = np.abs(0.5 * (s[:-1] + s[1:]))
            cost = np.where(cross, mid[None, :], np.inf)
            j = np.argmin(cost, axis=1)
            has = np.isfinite(cost[np.arange(c1 - c0), j])
            r = np.flatnonzero(has)
            if len(r) == 0:
                continue
            jj = j[r]
            a, b = s[jj], s[jj + 1]
            fa, fb = v[r, jj], v[r, jj + 1]
            for _ in range(2):  # false position inside the bracket
                t = a + (b - a) * (-fa) / np.where(fb - fa != 0, fb - fa, 1.0)
                ft, okt = self.sample(P[c0 + r] + t[:, None] * N[c0 + r])
                use = okt
                left = use & (ft < 0)
                right = use & ~left
                a = np.where(left, t, a)
                fa = np.where(left, ft, fa)
                b = np.where(right, t, b)
                fb = np.where(right, ft, fb)
            t = a + (b - a) * (-fa) / np.where(fb - fa != 0, fb - fa, 1.0)
            inside = np.abs(t) <= max_mm + 1e-9
            out[c0 + r[inside]] = t[inside]
            found[c0 + r[inside]] = True
        return out, found


# --------------------------------------------------------------------------- the stitch
def subdivide(F: np.ndarray, n_vertices: int, split: np.ndarray, V: np.ndarray | None = None
              ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Split the faces ``split`` (M,) bool of ``F`` in four, each edge at its middle, and the faces round them that
    share a split edge in two or three, so that the mesh stays conforming (every split edge is split for every face
    at it). Returns ``(F2, edges (E,2), parent (M2,))``: the new faces (the vertex ``n_vertices + k`` is the middle of
    ``edges[k]``), and each new face's face of ``F``. Faces that keep their three corners come first, in their order;
    the orientation of every face is kept. ``V`` (optional): where two corners are split, the quad is cut along the
    shorter diagonal."""
    F = np.asarray(F, dtype=np.int64).reshape(-1, 3)
    n = int(n_vertices)
    m = len(F)
    split = np.asarray(split, dtype=bool).reshape(m)
    if m == 0 or not split.any():
        return F.copy(), np.zeros((0, 2), dtype=np.int64), np.arange(m, dtype=np.int64)
    a, b = F[:, [0, 1, 2]], F[:, [1, 2, 0]]
    key = np.minimum(a, b) * n + np.maximum(a, b)  # (m, 3): edge k of a face runs from corner k to k + 1
    sk = np.unique(key[split].reshape(-1))
    pos = np.minimum(np.searchsorted(sk, key), len(sk) - 1)
    s = sk[pos] == key
    mid = n + pos  # the middle's vertex where s
    edges = np.stack([sk // n, sk % n], axis=1)
    cnt = s.sum(axis=1)
    out, par = [F[cnt == 0]], [np.flatnonzero(cnt == 0)]
    # three split edges: four faces
    f3 = np.flatnonzero(cnt == 3)
    if len(f3):
        A, B, C = F[f3, 0], F[f3, 1], F[f3, 2]
        m0, m1, m2 = mid[f3, 0], mid[f3, 1], mid[f3, 2]
        out += [np.stack(x, axis=1) for x in ((A, m0, m2), (m0, B, m1), (m2, m1, C), (m0, m1, m2))]
        par += [f3] * 4
    # one split edge: turned so that it is edge 0 (corner 0 to 1), then two faces
    f1 = np.flatnonzero(cnt == 1)
    if len(f1):
        j = np.argmax(s[f1], axis=1)
        rot = (np.arange(3)[None, :] + j[:, None]) % 3
        G = np.take_along_axis(F[f1], rot, axis=1)
        mm = mid[f1, j]
        out += [np.stack((G[:, 0], mm, G[:, 2]), axis=1), np.stack((mm, G[:, 1], G[:, 2]), axis=1)]
        par += [f1] * 2
    # two split edges: turned so that the one not split is edge 2 (corner 2 to 0), then three faces
    f2 = np.flatnonzero(cnt == 2)
    if len(f2):
        u = np.argmin(s[f2], axis=1)
        rot = (np.arange(3)[None, :] + (u[:, None] + 1)) % 3
        G = np.take_along_axis(F[f2], rot, axis=1)
        mr = np.take_along_axis(mid[f2], rot, axis=1)
        A, B, C, m0, m1 = G[:, 0], G[:, 1], G[:, 2], mr[:, 0], mr[:, 1]
        if V is not None:
            Vv = np.asarray(V, dtype=np.float64)
            M0 = 0.5 * (Vv[edges[m0 - n, 0]] + Vv[edges[m0 - n, 1]])
            M1 = 0.5 * (Vv[edges[m1 - n, 0]] + Vv[edges[m1 - n, 1]])
            diag_a = np.linalg.norm(Vv[A] - M1, axis=1) <= np.linalg.norm(M0 - Vv[C], axis=1)
        else:
            diag_a = np.ones(len(f2), dtype=bool)
        out.append(np.stack((m0, B, m1), axis=1))
        par.append(f2)
        out.append(np.where(diag_a[:, None], np.stack((A, m0, m1), axis=1), np.stack((A, m0, C), axis=1)))
        par.append(f2)
        out.append(np.where(diag_a[:, None], np.stack((A, m1, C), axis=1), np.stack((m0, m1, C), axis=1)))
        par.append(f2)
    return np.concatenate(out).astype(np.int64), edges, np.concatenate(par).astype(np.int64)


#: the mesh's own sigma terms of a few vertices are worked out on the faces within this many rings of them: the
#: curvature term looks two rings out, the open border's falls off over BORDER_MM (1.5 mm), and 10 rings of the finer
#: triangles (half a mm and more each) leave it below 0.02 mm at the submesh's rim
SIGMA_RINGS = 10


def sigma_terms_near(V: np.ndarray, F: np.ndarray, N: np.ndarray, verts: np.ndarray, voxel_mm: float,
                     rings: int = SIGMA_RINGS) -> np.ndarray:
    """:func:`ohs.track.recon_tsdf.mesh_sigma_terms` of the vertices ``verts`` alone, from the faces within ``rings``
    rings of them (much less than the whole mesh): the curvature term as there; the open border's from the edges open
    in the whole mesh among them (a vertex whose faces are not all in the submesh is not taken for a border)."""
    from scipy.spatial import cKDTree  # noqa: PLC0415

    from ohs.track.recon_tsdf import A_BORDER, BORDER_MM, bias_scale, mesh_sigma_terms  # noqa: PLC0415

    verts = np.asarray(verts, dtype=np.int64)
    if len(verts) == 0:
        return np.zeros(0)
    inset = np.zeros(len(V), dtype=bool)
    inset[verts] = True
    for _ in range(int(rings)):
        fsel = inset[F].any(axis=1)
        inset[F[fsel].reshape(-1)] = True
    fsel = inset[F].any(axis=1)
    Fs = F[fsel]
    used = np.unique(Fs.reshape(-1))
    remap = np.full(len(V), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    Fl = remap[Fs]
    curv, _ = mesh_sigma_terms(V[used], Fl, N[used], voxel_mm=voxel_mm, parts=True)
    # whole stars: the vertices all of whose faces are in the submesh (each face of the mesh at them is in Fs)
    deg_all = np.bincount(F.reshape(-1), minlength=len(V))
    deg_sub = np.bincount(Fs.reshape(-1), minlength=len(V))
    whole = deg_all[used] == deg_sub[used]
    a, b = Fl[:, [0, 1, 2]].reshape(-1), Fl[:, [1, 2, 0]].reshape(-1)
    key = np.minimum(a, b) * len(used) + np.maximum(a, b)
    u, cnt = np.unique(key, return_counts=True)
    oe = u[cnt == 1]
    ea, eb = oe // len(used), oe % len(used)
    true_open = whole[ea] | whole[eb]  # an edge at a whole star has all its faces here: open here is open there
    bv = np.unique(np.concatenate([ea[true_open], eb[true_open]]))
    out = curv[remap[verts]].astype(np.float64)
    if len(bv):
        k = bias_scale(voxel_mm)
        d, _ = cKDTree(V[used][bv]).query(V[verts], workers=-1)
        border = A_BORDER * k * np.exp(-d / (BORDER_MM * k))
        out += border * border
    return out


def _boundary_loops(V: np.ndarray, F: np.ndarray) -> int:
    from ohs.mesh.topology import boundary_loops  # noqa: PLC0415

    return int(len(boundary_loops(V, F).perimeter)) if len(F) else 0


def _q(x: np.ndarray) -> dict | None:
    x = np.asarray(x, dtype=np.float64)
    if len(x) == 0:
        return None
    p = np.percentile(x, [50, 95])
    return {"p50": round(float(p[0]), 3), "p95": round(float(p[1]), 3), "max": round(float(x.max()), 3)}


def refine_onto(V: np.ndarray, F: np.ndarray, N: np.ndarray | None, S: np.ndarray | None, L: np.ndarray | None,
                region: np.ndarray, field: FineField, *, coarse_voxel_mm: float,
                max_move_voxels: float = MAX_MOVE_VOXELS, step_fine: float = SEARCH_STEP_FINE,
                count_loops: bool = True) -> tuple[tuple, dict]:
    """The stitch (module docstring): the faces ``region`` (M,) bool of the mesh ``(V, F)`` split in four (and their
    neighbours in two or three), their corners and the new middles moved onto ``field``'s surface along their normals.
    ``N``, ``S``, ``L``: the vertices' normals, sigma (mm) and counts of looks (each may be None). Returns
    ``((V2, F2, N2, S2, L2), report)``; the first ``len(V)`` vertices are ``V``'s, those outside the region unchanged in
    every value, the faces outside the region and not next to it unchanged and first, in their order."""
    from ohs.mesh.topology import face_normals, vertex_normals  # noqa: PLC0415

    t0 = time.perf_counter()
    V = np.asarray(V, dtype=np.float64).reshape(-1, 3)
    F = np.asarray(F, dtype=np.int64).reshape(-1, 3)
    nv = len(V)
    region = np.asarray(region, dtype=bool).reshape(len(F))
    rep: dict[str, Any] = {"faces_in": int(region.sum()), "triangles_before": int(len(F))}
    Nf = vertex_normals(V, F) if N is None or len(N) != nv else np.asarray(N, dtype=np.float64)
    if not region.any():
        rep.update(triangles_after=int(len(F)), changed=False)
        return (V, F, N, S, L), rep
    F2, edges, parent = subdivide(F, nv, region, V)
    ne = len(edges)
    Vm = 0.5 * (V[edges[:, 0]] + V[edges[:, 1]])
    V1 = np.concatenate([V, Vm])
    Nm = Nf[edges[:, 0]] + Nf[edges[:, 1]]
    Nm /= np.maximum(np.linalg.norm(Nm, axis=1, keepdims=True), 1e-12)
    N1 = np.concatenate([Nf, Nm])
    # the vertices that move: every corner of a face in the region, and every new middle
    mv = np.zeros(nv + ne, dtype=bool)
    mv[np.unique(F[region].reshape(-1))] = True
    mv[nv:] = True
    idx = np.flatnonzero(mv)
    max_mm = float(max_move_voxels) * float(coarse_voxel_mm)
    step = float(step_fine) * field.voxel_mm
    d, ok = field.project(V1[idx], N1[idx], max_mm, step)
    V2 = V1.copy()
    V2[idx[ok]] += d[ok, None] * N1[idx[ok]]
    # a move that would turn a face over (or squash it) is not made: its corners keep their places (only the faces
    # with a corner that moves can turn)
    Fa = F2[mv[F2].any(axis=1)]
    fn0, a0 = face_normals(V1, Fa)

    def turned() -> np.ndarray:
        fn1, a1 = face_normals(V2, Fa)
        return (((fn0 * fn1).sum(axis=1) <= 0.2) | (a1 < 0.05 * a0)) & (a0 > 0)

    reverted = np.zeros(nv + ne, dtype=bool)
    for _ in range(4):
        bad = turned()
        if not bad.any():
            break
        back = np.unique(Fa[bad].reshape(-1))
        back = back[mv[back] & ~reverted[back]]
        if len(back) == 0:
            break
        V2[back] = V1[back]
        reverted[back] = True
    moved = np.zeros(nv + ne, dtype=bool)
    moved[idx[ok]] = True
    moved &= ~reverted
    # a new middle that found no place of its own stays between its edge's two corners as they are now (where they
    # moved, it follows them: no spike back to the 1 mm surface between two moved corners)
    lone = np.flatnonzero(~moved[nv:])
    if len(lone):
        V2[nv + lone] = 0.5 * (V2[edges[lone, 0]] + V2[edges[lone, 1]])
        bad = turned()
        if bad.any():  # should that turn a face over, the middles of such faces go back
            back = np.unique(Fa[bad].reshape(-1))
            back = back[back >= nv]
            back = back[~moved[back]]
            V2[back] = V1[back]
    # the moved vertices' normals from the finer field (marching cubes' too come from the field), oriented as before
    N2 = N1.copy()
    gi = np.flatnonzero(moved)
    G, gok = field.gradient(V2[gi])
    flip = (G * N1[gi]).sum(axis=1) < 0
    G[flip] *= -1.0
    N2[gi[gok]] = G[gok]
    # sigma and looks: the finer model's where a vertex moved, else as before (a middle: its edge's mean)
    S2 = L2 = None
    if S is not None and len(S) == nv:
        S = np.asarray(S, dtype=np.float32)
        S2 = np.concatenate([S, (0.5 * (S[edges[:, 0]].astype(np.float64) + S[edges[:, 1]])).astype(np.float32)])
        sv, sok = field.sample(V2[gi], "sigma")
        if len(gi):
            from ohs.mesh.confidence import SIGMA_NONE_MM  # noqa: PLC0415

            new = np.sqrt(sv * sv + sigma_terms_near(V2, F2, N2, gi, field.voxel_mm))
            S2[gi[sok]] = np.minimum(new[sok], SIGMA_NONE_MM).astype(np.float32)
    if L is not None and len(L) == nv:
        L = np.asarray(L, dtype=np.float32)
        L2 = np.concatenate([L, (0.5 * (L[edges[:, 0]].astype(np.float64) + L[edges[:, 1]])).astype(np.float32)])
        lv, lok = field.sample(V2[gi], "looks")
        L2[gi[lok]] = lv[lok].astype(np.float32)
    move = np.linalg.norm(V2[idx] - V1[idx], axis=1)
    # the seam: the corners in the region shared with faces outside it (where a moved surface meets one left as it was)
    outside = np.zeros(nv + ne, dtype=bool)
    outside[np.unique(F[~region].reshape(-1))] = True
    seam = np.flatnonzero(mv[:nv] & outside[:nv])
    fa = face_normals(V, F[region])[1]
    rep.update(
        triangles_after=int(len(F2)), changed=bool(moved.any()), area_mm2=round(float(fa.sum()), 1),
        share=round(float(fa.sum() / max(face_normals(V, F)[1].sum(), 1e-12)), 4),
        vertices={"in_region": int(len(idx)), "new": int(ne), "moved": int(moved.sum()),
                  "kept_no_surface": int((~ok).sum()), "kept_turned_over": int(reverted.sum())},
        moved_mm=_q(move[ok & ~reverted[idx]]), seam_mm=_q(np.linalg.norm(V2[seam] - V[seam], axis=1)),
        seam_vertices=int(len(seam)), limits={"max_move_mm": round(max_mm, 3), "step_mm": round(step, 3)})
    if count_loops:
        rep["loops"] = {"before": _boundary_loops(V, F), "after": _boundary_loops(V2, F2)}
    rep["ms"] = round(1e3 * (time.perf_counter() - t0), 0)
    return (V2, F2, N2, S2, L2), rep
