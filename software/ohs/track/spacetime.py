"""World-space spacetime stereo: "Refine from the images" (research plan 7.2; DESKTOP_APP_PLAN.md,
M3 phase 3 item 5, a trial build).

The dot pattern is fixed to the scanner, so as the scanner moves, every frame lights a piece of
the surface with a different arrangement of dots. Per-frame stereo makes a slightly different,
camera-fixed error in each frame (a square window that assumes the surface faces the camera, a
smoothness penalty, pixel locking), and fusion averages those errors instead of removing them.
This stage measures the fused surface against the raw images instead:

1. **Patches.** The mesh is sampled every ``spacing_mm``. Each sample is a small square of the
   surface (``2 half_mm`` across, by default 7 px either side at the scan's typical depth) with
   its own slant and curvature: a quadric fitted to the mesh within ``fit * half_mm``. (Fitted
   only over the patch itself, the curvature is the mesh's noise.)
2. **Views.** A frame sees a patch when the patch's centre and corners land in both rectified
   images, face both cameras (not grazing) and are not hidden behind other parts of the mesh
   (rays cast against the mesh from both camera centres). At most ``max_views`` frames per patch,
   spread over the recording.
3. **Search along the normal.** For each candidate offset, the patch is projected into the left
   and the right image of every frame that sees it, and the two samplings are compared with
   zero-mean normalised cross-correlation (ZNCC, which ignores gain differences between the
   cameras). A frame counts only when both samplings hold texture (a dot: noise alone does not),
   tested once on the fitted surface, so every offset is scored on the same frames; and each
   frame's cost is capped, so one bad frame can't dominate. The mean cost is searched
   coarse to fine; a parabola through the last three costs gives the offset.
4. **Confidence.** A patch is kept when at least ``min_views`` frames hold dots on it (with fewer,
   the fused surface is as good), its minimum lies inside the search range and is not flat, and
   the frames agree (a low mean cost).
5. **Moving the surface.** Each vertex moves onto the refined surfaces of the kept patches around
   it (:func:`project_onto_patches`, Gaussian weights at ``smooth_mm``), fading out where they
   thin out, so the rest keeps the fused surface. Each vertex keeps its sigma.

On the simulator (``VALIDATION.md``, "Refine from the images"), with tracked poses, the median
error where it measured falls 1.8-2.8 times on a sphere and about 2 times on a car panel; at the
true poses 4.7 times on the sphere.

The cost runs on the GPU (one OpenCL kernel, any vendor, :mod:`ohs.compute`) with a numpy twin on
the CPU; the tests check that the two agree.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from typing import Callable, Sequence

import numpy as np

from ohs.geometry import invert_se3

__all__ = ["SpacetimeParams", "resolve_params", "Patches", "make_patches", "patch_views", "patch_costs",
           "search_offsets", "project_onto_patches", "refine_from_images", "frames_from_recording"]

log = logging.getLogger(__name__)

Progress = Callable[[float, str], None]


@dataclass(frozen=True)
class SpacetimeParams:
    # A patch is 2 * half_mm across; by default half_px pixels at the scan's typical depth (about a
    # dot and its surroundings), sampled about once per pixel, with centres 0.75 * half_mm apart.
    half_px: float = 7.0
    half_mm: float | None = None  # overrides half_px
    samples: int | None = None  # per side; None: 2 * half_px + 1
    spacing_mm: float | None = None  # None: 0.75 * half_mm
    fit: float = 2.0  # the patch shape: a quadric fitted to the mesh within fit * half_mm
    # coarse to fine: (half range, step) in mm along the normal, each centred on the previous minimum
    levels: tuple[tuple[float, float], ...] = ((0.8, 0.1), (0.1, 0.02), (0.02, 0.004))
    max_frames: int = 120  # frames read from the recording, spread over it
    max_views: int = 40  # frames per patch, spread over those that see it
    min_views: int = 16  # textured views a patch needs (with fewer, the fused surface is as good)
    texture_dn: float = 4.6  # standard deviation (grey levels) both samplings need: a dot, not noise
    cap: float = 0.6  # per-frame cost cap (1 - ZNCC)
    max_cost: float = 0.45  # mean cost at the minimum above which the frames disagree
    min_curvature: float = 0.25  # cost curvature (1/mm^2) at the minimum below which it is too flat
    grazing_deg: float = 75.0  # views more oblique than this are skipped
    occlusion_mm: float = 1.0  # a ray hitting the mesh this much before the patch is blocked
    smooth_mm: float | None = None  # Gaussian scale of the patches' weights on a vertex; None: spacing_mm
    gate_mm: float = 1.0  # a vertex farther than this from a patch's surface ignores that patch


def resolve_params(params: SpacetimeParams | None, V: np.ndarray, T_wc: np.ndarray, K: np.ndarray,
                   size: tuple[int, int]) -> SpacetimeParams:
    """``params`` with ``half_mm``, ``samples`` and ``spacing_mm`` filled in: ``half_px`` pixels at
    the median depth of the mesh in the frames (vertices that land in the image)."""
    p = params or SpacetimeParams()
    half = p.half_mm
    if half is None:
        V = np.asarray(V, dtype=np.float64)
        T_wc = np.asarray(T_wc, dtype=np.float64).reshape(-1, 4, 4)
        sub = V[np.random.default_rng(0).choice(len(V), size=min(len(V), 4000), replace=False)] if len(V) else V
        zs = []
        pick = np.unique(np.linspace(0, len(T_wc) - 1, min(len(T_wc), 20)).round().astype(int)) if len(T_wc) else []
        for Twc in T_wc[pick] if len(T_wc) else []:
            Tcw = invert_se3(Twc)
            Y = sub @ Tcw[:3, :3].T + Tcw[:3, 3]
            z = Y[:, 2]
            zp = np.maximum(z, 1e-6)
            u = K[0, 0] * Y[:, 0] / zp + K[0, 2]
            v = K[1, 1] * Y[:, 1] / zp + K[1, 2]
            zs.append(z[(z > 1.0) & (u >= 0) & (u < size[0]) & (v >= 0) & (v < size[1])])
        zz = np.concatenate(zs) if zs else np.zeros(0)
        depth = float(np.median(zz)) if len(zz) else 300.0
        half = float(np.clip(p.half_px * depth / float(K[0, 0]), 0.5, 10.0))
    samples = p.samples if p.samples is not None else int(2 * round(p.half_px) + 1)
    spacing = p.spacing_mm if p.spacing_mm is not None else 0.75 * half
    return replace(p, half_mm=round(float(half), 4), samples=max(3, int(samples)), spacing_mm=round(float(spacing), 4))


def _need(p: SpacetimeParams | None) -> SpacetimeParams:
    p = p or SpacetimeParams()
    if p.half_mm is None or p.samples is None or p.spacing_mm is None:
        raise ValueError("call resolve_params first (half_mm, samples and spacing_mm must be set)")
    return p


@dataclass
class Patches:
    """Patch centres on the mesh. ``geo`` (n, 18) float64: centre (3), tangents t1 (3), t2 (3),
    normal (3), quadric ``h(a, b) = q0 + q1 a + q2 b + q3 a^2 + q4 a b + q5 b^2`` (6), along the
    normal, in mm, in the mesh frame."""

    vid: np.ndarray  # (n,) vertex index of each centre
    geo: np.ndarray  # (n, 18)

    def __len__(self) -> int:
        return len(self.vid)

    @property
    def P(self) -> np.ndarray:
        return self.geo[:, 0:3]

    @property
    def normal(self) -> np.ndarray:
        return self.geo[:, 9:12]

    def point(self, offset: np.ndarray) -> np.ndarray:
        """The surface point at each centre moved ``offset`` (mm) along the normal."""
        return self.P + (self.geo[:, 12] + np.asarray(offset, dtype=np.float64))[:, None] * self.normal

    def corners(self, half: float) -> np.ndarray:
        """(n, 5, 3): centre and the four corners on the quadric."""
        g = self.geo
        out = [self.point(np.zeros(len(self)))]
        for sa in (-1.0, 1.0):
            for sb in (-1.0, 1.0):
                a, b = sa * half, sb * half
                h = g[:, 12] + g[:, 13] * a + g[:, 14] * b + g[:, 15] * a * a + g[:, 16] * a * b + g[:, 17] * b * b
                out.append(g[:, 0:3] + a * g[:, 3:6] + b * g[:, 6:9] + h[:, None] * g[:, 9:12])
        return np.stack(out, axis=1)


def _tangents(n: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a = np.where(np.abs(n[:, :1]) < 0.9, np.array([[1.0, 0.0, 0.0]]), np.array([[0.0, 1.0, 0.0]]))
    t1 = np.cross(n, a)
    t1 /= np.linalg.norm(t1, axis=1, keepdims=True)
    return t1, np.cross(n, t1)


def make_patches(V: np.ndarray, F: np.ndarray, N: np.ndarray | None = None, spacing_mm: float = 1.5,
                 half_mm: float = 2.0, *, fit: float = 1.25, curvature: bool = True) -> Patches:
    """Patch centres every ``spacing_mm`` on the mesh, each with a quadric (a plane when
    ``curvature`` is False) fitted to the vertices within ``fit * half_mm`` (at least 2.5 edges;
    Gaussian weights; same side of a thin wall: normals within 60 degrees). Patches over an edge of
    the mesh (a corner of the patch with no vertex near it) or with too few vertices are left out."""
    from scipy.spatial import cKDTree  # noqa: PLC0415

    from ohs.mesh.topology import vertex_normals  # noqa: PLC0415

    V = np.asarray(V, dtype=np.float64)
    if N is None:
        N = vertex_normals(V, F)
    used = np.zeros(len(V), dtype=bool)
    used[np.asarray(F, dtype=np.int64).ravel()] = True
    cand = np.flatnonzero(used & np.all(np.isfinite(N), axis=1) & (np.linalg.norm(N, axis=1) > 0.5))
    empty = Patches(np.zeros(0, dtype=np.int64), np.zeros((0, 18)))
    if len(cand) == 0:
        return empty
    # one centre per voxel: the vertex nearest the voxel's middle
    s = float(spacing_mm)
    Fi = np.asarray(F, dtype=np.int64)
    edge = float(np.median(np.linalg.norm(V[Fi[:, 0]] - V[Fi[:, 1]], axis=1))) if len(Fi) else 1.0
    key = np.floor(V[cand] / s).astype(np.int64)
    d = np.linalg.norm(V[cand] - (key + 0.5) * s, axis=1)
    order = np.lexsort((d, key[:, 2], key[:, 1], key[:, 0]))
    ks = key[order]
    first = np.ones(len(order), dtype=bool)
    first[1:] = np.any(ks[1:] != ks[:-1], axis=1)
    vid = cand[order[first]]
    tree = cKDTree(V[cand])
    r = max(float(fit) * float(half_mm), 2.5 * edge)
    # the vertices in the disc (a triangle mesh has about one per 0.87 edge^2), with a margin
    kk = int(min(len(cand), 768, max(16, 1.3 * np.pi * r * r / max(0.87 * edge * edge, 1e-9) + 16)))
    vids, geos = [], []
    for c0 in range(0, len(vid), 4000):
        vc = vid[c0:c0 + 4000]
        P = V[vc]
        dist, j = tree.query(P, k=kk, distance_upper_bound=r, workers=-1)
        if dist.ndim == 1:
            dist, j = dist[:, None], j[:, None]
        valid = np.isfinite(dist)
        j = np.where(valid, j, 0)
        nb = cand[j]
        wgt = np.where(valid, np.exp(-0.5 * (np.where(valid, dist, 0.0) / (0.5 * r)) ** 2), 0.0)
        n = (N[nb] * wgt[..., None]).sum(axis=1)
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        valid &= np.einsum("ijk,ik->ij", N[nb], n) > 0.5
        t1, t2 = _tangents(n)
        X = V[nb] - P[:, None, :]
        a = np.einsum("ijk,ik->ij", X, t1)
        b = np.einsum("ijk,ik->ij", X, t2)
        h = np.einsum("ijk,ik->ij", X, n)
        valid &= np.abs(h) < r
        wgt = wgt * valid
        # the mesh must reach each corner of the patch: in every quadrant a vertex near the corner
        quad = np.stack([(a < 0) & (b < 0), (a < 0) & (b >= 0), (a >= 0) & (b < 0), (a >= 0) & (b >= 0)], axis=-1)
        reach = min(float(half_mm), r / np.sqrt(2.0)) - 0.5 * edge
        corner = np.where(valid, np.minimum(np.abs(a), np.abs(b)), -1.0)
        full = np.all(np.where(quad, corner[..., None], -1.0).max(axis=1) >= reach, axis=1) & (valid.sum(axis=1) >= 10)
        cols = [np.ones_like(a), a, b] + ([a * a, a * b, b * b] if curvature else [])
        A = np.stack(cols, axis=-1)
        M = np.einsum("ijk,ij,ijl->ikl", A, wgt, A) + 1e-6 * np.eye(len(cols))
        rhs = np.einsum("ijk,ij->ik", A, wgt * h)
        q = np.zeros((int(full.sum()), 6))
        q[:, :len(cols)] = np.linalg.solve(M[full], rhs[full][..., None])[..., 0]
        geos.append(np.concatenate([P[full], t1[full], t2[full], n[full], q], axis=1))
        vids.append(vc[full])
    if not vids:
        return empty
    return Patches(np.concatenate(vids).astype(np.int64), np.concatenate(geos))


# --------------------------------------------------------------------------- views
def patch_views(patches: Patches, T_wc: np.ndarray, K: np.ndarray, baseline: float, size: tuple[int, int],
                V: np.ndarray, F: np.ndarray, params: SpacetimeParams | None = None,
                cancelled: Callable[[], bool] | None = None) -> np.ndarray:
    """(n_patches, n_frames) bool: which frames see each patch in both cameras (module docstring,
    step 2), at most ``max_views`` per patch."""
    import open3d as o3d  # noqa: PLC0415

    p = _need(params)
    T_wc = np.asarray(T_wc, dtype=np.float64).reshape(-1, 4, 4)
    n, m = len(patches), len(T_wc)
    vis = np.zeros((n, m), dtype=bool)
    if n == 0 or m == 0:
        return vis
    o = np.asarray(V, dtype=np.float64).mean(axis=0)
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.core.Tensor((np.asarray(V) - o).astype(np.float32)),
                        o3d.core.Tensor(np.asarray(F, dtype=np.uint32)))
    fx, fy, cx, cy = float(K[0, 0]), float(K[1, 1]), float(K[0, 2]), float(K[1, 2])
    w, h = int(size[0]), int(size[1])
    pts = patches.corners(p.half_mm)  # (n, 5, 3)
    nrm = patches.normal
    cos_max = float(np.cos(np.radians(p.grazing_deg)))
    for f in range(m):
        if cancelled is not None and cancelled():
            raise InterruptedError("cancelled")
        Twc = T_wc[f]
        Tcw = invert_se3(Twc)
        oL = Twc[:3, 3]
        oR = (Twc @ np.array([float(baseline), 0.0, 0.0, 1.0]))[:3]
        ok = np.ones(n, dtype=bool)
        for oc in (oL, oR):
            dv = oc - patches.P
            ok &= np.einsum("ij,ij->i", dv, nrm) > cos_max * np.linalg.norm(dv, axis=1)
        Yc = pts @ Tcw[:3, :3].T + Tcw[:3, 3]  # (n, 5, 3)
        z = Yc[..., 2]
        ok &= np.all(z > 1.0, axis=1)
        zs = np.maximum(z, 1.0)
        u = fx * Yc[..., 0] / zs + cx
        v = fy * Yc[..., 1] / zs + cy
        uR = u - fx * float(baseline) / zs
        for uu in (u, uR):
            ok &= np.all((uu > 1.0) & (uu < w - 2.0) & (v > 1.0) & (v < h - 2.0), axis=1)
        idx = np.flatnonzero(ok)
        if len(idx) == 0:
            continue
        Y = pts[idx].reshape(-1, 3)
        rays, dist = [], []
        for oc in (oL, oR):
            d = Y - oc
            L = np.linalg.norm(d, axis=1)
            rays.append(np.concatenate([np.broadcast_to(oc - o, d.shape), d / L[:, None]], axis=1))
            dist.append(L)
        ans = scene.cast_rays(o3d.core.Tensor(np.concatenate(rays).astype(np.float32)))
        t_hit = ans["t_hit"].numpy().astype(np.float64)
        clear = (t_hit >= np.concatenate(dist) - p.occlusion_mm).reshape(2, len(idx), 5)
        vis[idx[np.all(clear, axis=(0, 2))], f] = True
    # at most max_views per patch, spread over the frames that see it
    cnt = vis.sum(axis=1)
    over = np.flatnonzero(cnt > p.max_views)
    for i in over:
        fr = np.flatnonzero(vis[i])
        keep = fr[np.round(np.linspace(0, len(fr) - 1, p.max_views)).astype(np.int64)]
        vis[i] = False
        vis[i, keep] = True
    return vis


# --------------------------------------------------------------------------- the cost
_KERNEL = r"""
inline float bil(__global const uchar *img, const int W, const int H, const float u, const float v, int *ok) {
    const float fu = floor(u), fv = floor(v);
    const int u0 = (int)fu, v0 = (int)fv;
    if (u0 < 0 || v0 < 0 || u0 >= W - 1 || v0 >= H - 1) { *ok = 0; return 0.0f; }
    const float du = u - fu, dv = v - fv;
    __global const uchar *q = img + (size_t)v0 * W + u0;
    const float a = q[0], b = q[1], c = q[W], d = q[W + 1];
    return (a + (b - a) * du) * (1.0f - dv) + (c + (d - c) * du) * dv - 128.0f;
}

/* The patch g moved delta along its normal, sampled ns x ns in the left and right image of the
   frame with world -> camera pose T: centred second moments of the two samplings. 0 when a
   sample falls outside an image or behind the camera. */
inline int stats(__global const uchar *Lf, __global const uchar *Rf, const int W, const int H,
                 __global const float *T, const float fx, const float fy, const float cx, const float cy,
                 const float fB, __global const float *g, const float delta, const int ns, const float step,
                 float *vl, float *vr, float *cv)
{
    const float3 P = (float3)(g[0], g[1], g[2]);
    const float3 T1 = (float3)(g[3], g[4], g[5]);
    const float3 T2 = (float3)(g[6], g[7], g[8]);
    const float3 N = (float3)(g[9], g[10], g[11]);
    const float q0 = g[12] + delta, q1 = g[13], q2 = g[14], q3 = g[15], q4 = g[16], q5 = g[17];
    const float3 r0 = (float3)(T[0], T[1], T[2]);
    const float3 r1 = (float3)(T[3], T[4], T[5]);
    const float3 r2 = (float3)(T[6], T[7], T[8]);
    const float3 Pc = (float3)(dot(r0, P) + T[9], dot(r1, P) + T[10], dot(r2, P) + T[11]);
    const float3 A = (float3)(dot(r0, T1), dot(r1, T1), dot(r2, T1));
    const float3 B = (float3)(dot(r0, T2), dot(r1, T2), dot(r2, T2));
    const float3 Nc = (float3)(dot(r0, N), dot(r1, N), dot(r2, N));
    const float hw = 0.5f * (float)(ns - 1) * step;
    float sl = 0.0f, sr = 0.0f, sll = 0.0f, srr = 0.0f, slr = 0.0f;
    int ok = 1;
    for (int i = 0; i < ns && ok; ++i) {
        const float a = -hw + (float)i * step;
        for (int j = 0; j < ns; ++j) {
            const float b = -hw + (float)j * step;
            const float h = q0 + q1 * a + q2 * b + q3 * a * a + q4 * a * b + q5 * b * b;
            const float3 Y = Pc + a * A + b * B + h * Nc;
            if (Y.z <= 1.0f) { ok = 0; break; }
            const float iz = 1.0f / Y.z;
            const float u = fx * Y.x * iz + cx, v = fy * Y.y * iz + cy;
            const float il = bil(Lf, W, H, u, v, &ok);
            const float ir = bil(Rf, W, H, u - fB * iz, v, &ok);
            if (!ok) break;
            sl += il; sr += ir; sll += il * il; srr += ir * ir; slr += il * ir;
        }
    }
    const float m = (float)(ns * ns);
    *vl = sll - sl * sl / m;
    *vr = srr - sr * sr / m;
    *cv = slr - sl * sr / m;
    return ok;
}

/* One work item per (patch, candidate offset): the capped cost summed over the patch's views
   (a view whose samples leave the image costs the cap, so every offset has the same count). */
__kernel void wss_cost(__global const uchar *L, __global const uchar *R, const int W, const int H,
                       __global const float *pose, const float fx, const float fy, const float cx,
                       const float cy, const float fB, __global const float *geo,
                       __global const float *deltas, const int D, const int NP,
                       __global const int *voff, __global const int *vidx, const int ns,
                       const float step, const float cap, __global float *C, __global float *Wt)
{
    const int gid = get_global_id(0);
    if (gid >= NP * D) return;
    const int p = gid / D;
    const float delta = deltas[gid];
    const size_t img = (size_t)W * (size_t)H;
    float cost = 0.0f, wt = 0.0f;
    for (int k = voff[p]; k < voff[p + 1]; ++k) {
        const int f = vidx[k];
        float vl, vr, cv;
        const int ok = stats(L + (size_t)f * img, R + (size_t)f * img, W, H, pose + 12 * f, fx, fy, cx, cy, fB,
                             geo + 18 * p, delta, ns, step, &vl, &vr, &cv);
        cost += ok ? fmin(1.0f - cv * rsqrt(fmax(vl * vr, 1e-12f)), cap) : cap;
        wt += 1.0f;
    }
    C[gid] += cost;
    Wt[gid] += wt;
}

/* One work item per (patch, view): does the view hold texture in both images at the patch? */
__kernel void wss_texture(__global const uchar *L, __global const uchar *R, const int W, const int H,
                          __global const float *pose, const float fx, const float fy, const float cx,
                          const float cy, const float fB, __global const float *geo, const int NE,
                          __global const int *vpatch, __global const int *vidx, const int ns,
                          const float step, const float tex2, __global uchar *flag)
{
    const int gid = get_global_id(0);
    if (gid >= NE) return;
    const int p = vpatch[gid], f = vidx[gid];
    const size_t img = (size_t)W * (size_t)H;
    float vl, vr, cv;
    const int ok = stats(L + (size_t)f * img, R + (size_t)f * img, W, H, pose + 12 * f, fx, fy, cx, cy, fB,
                         geo + 18 * p, 0.0f, ns, step, &vl, &vr, &cv);
    flag[gid] = (uchar)(ok && vl > tex2 && vr > tex2);
}
"""


def _bilinear(img: np.ndarray, u: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    h, w = img.shape
    fu, fv = np.floor(u), np.floor(v)
    u0, v0 = fu.astype(np.int64), fv.astype(np.int64)
    ok = (u0 >= 0) & (v0 >= 0) & (u0 < w - 1) & (v0 < h - 1)
    i = np.clip(v0, 0, h - 2) * w + np.clip(u0, 0, w - 2)
    du, dv = (u - fu).astype(np.float32), (v - fv).astype(np.float32)
    flat = img.reshape(-1)
    a, b, c, d = flat[i], flat[i + 1], flat[i + w], flat[i + w + 1]
    return (a + (b - a) * du) * (1.0 - dv) + (c + (d - c) * du) * dv - np.float32(128.0), ok


class _Frames:
    """Rectified frames and their world -> camera poses, centred on ``origin`` (float32 kernels)."""

    def __init__(self, left: Sequence[np.ndarray], right: Sequence[np.ndarray], T_wc: np.ndarray, origin: np.ndarray):
        self.left = [np.ascontiguousarray(x, dtype=np.uint8) for x in left]
        self.right = [np.ascontiguousarray(x, dtype=np.uint8) for x in right]
        T = np.asarray(T_wc, dtype=np.float64).reshape(-1, 4, 4)
        self.pose = np.zeros((len(T), 12))
        for f, Twc in enumerate(T):
            Tcw = invert_se3(Twc)
            self.pose[f, :9] = Tcw[:3, :3].reshape(9)
            self.pose[f, 9:] = Tcw[:3, :3] @ origin + Tcw[:3, 3]  # x_c = R (x' + o) + t
        self.size = (self.left[0].shape[1], self.left[0].shape[0]) if self.left else (0, 0)

    def __len__(self) -> int:
        return len(self.left)


def _stats_cpu(geo: np.ndarray, deltas: np.ndarray, fr: _Frames, K: np.ndarray, baseline: float, vis: np.ndarray,
               ns: int, step: float, chunk: int = 400):
    """The CPU twin of the kernels' ``stats``: per frame and chunk of patches that see it,
    ``(f, sel, ok, vl, vr, cv)`` with arrays (len(sel), D)."""
    fx, fy, cx, cy = float(K[0, 0]), float(K[1, 1]), float(K[0, 2]), float(K[1, 2])
    fB = fx * float(baseline)
    half = 0.5 * (ns - 1) * step
    g1 = -half + np.arange(ns) * step
    a = np.repeat(g1, ns).astype(np.float64)
    b = np.tile(g1, ns).astype(np.float64)
    basis = np.stack([np.ones_like(a), a, b, a * a, a * b, b * b])  # (6, m)
    m = float(ns * ns)
    for f in range(len(fr)):
        sel_all = np.flatnonzero(vis[:, f])
        if len(sel_all) == 0:
            continue
        R = fr.pose[f, :9].reshape(3, 3)
        t = fr.pose[f, 9:]
        Lf = fr.left[f].astype(np.float32)
        Rf = fr.right[f].astype(np.float32)
        for s0 in range(0, len(sel_all), chunk):
            sel = sel_all[s0:s0 + chunk]
            g = geo[sel]
            Pc = g[:, 0:3] @ R.T + t
            A = g[:, 3:6] @ R.T
            B = g[:, 6:9] @ R.T
            Nc = g[:, 9:12] @ R.T
            h0 = g[:, 12:18] @ basis  # (s, m)
            H = h0[:, None, :] + deltas[sel][:, :, None]  # (s, D, m)
            base = Pc[:, None, :] + a[None, :, None] * A[:, None, :] + b[None, :, None] * B[:, None, :]  # (s, m, 3)
            Y = base[:, None, :, :] + H[..., None] * Nc[:, None, None, :]  # (s, D, m, 3)
            z = Y[..., 2]
            front = np.all(z > 1.0, axis=-1)
            iz = 1.0 / np.maximum(z, 1.0)
            u = fx * Y[..., 0] * iz + cx
            v = fy * Y[..., 1] * iz + cy
            il, okl = _bilinear(Lf, u, v)
            ir, okr = _bilinear(Rf, u - fB * iz, v)
            ok = front & np.all(okl & okr, axis=-1)
            il = il.astype(np.float64)
            ir = ir.astype(np.float64)
            sl, sr = il.sum(-1), ir.sum(-1)
            vl = (il * il).sum(-1) - sl * sl / m
            vr = (ir * ir).sum(-1) - sr * sr / m
            cv = (il * ir).sum(-1) - sl * sr / m
            yield f, sel, ok, vl, vr, cv


def _cost_cpu(geo: np.ndarray, deltas: np.ndarray, fr: _Frames, K: np.ndarray, baseline: float, vis: np.ndarray,
              ns: int, step: float, cap: float) -> tuple[np.ndarray, np.ndarray]:
    n, D = deltas.shape
    C = np.zeros((n, D))
    Wt = np.zeros((n, D))
    for _, sel, ok, vl, vr, cv in _stats_cpu(geo, deltas, fr, K, baseline, vis, ns, step):
        zn = cv / np.sqrt(np.maximum(vl * vr, 1e-12))
        C[sel] += np.where(ok, np.minimum(1.0 - zn, cap), cap)
        Wt[sel] += 1.0
    return C, Wt


def _texture_cpu(geo: np.ndarray, fr: _Frames, K: np.ndarray, baseline: float, vis: np.ndarray, ns: int,
                 step: float, tex2: float) -> np.ndarray:
    out = np.zeros(vis.shape, dtype=bool)
    for f, sel, ok, vl, vr, _ in _stats_cpu(geo, np.zeros((len(geo), 1)), fr, K, baseline, vis, ns, step):
        out[sel, f] = (ok & (vl > tex2) & (vr > tex2))[:, 0]
    return out


class _GpuCost:
    """The kernels on one OpenCL device. The frames are uploaded in groups that fit its memory (one
    group stays resident); :meth:`set_patches` chooses the patches and their views."""

    def __init__(self, dev: object, fr: _Frames, K: np.ndarray, baseline: float, group: int | None = None) -> None:
        import pyopencl as cl  # noqa: PLC0415

        self.cl = cl
        self.dev = dev
        self.queue = dev.new_queue()
        prg = dev.program(_KERNEL)
        self.k_cost = cl.Kernel(prg, "wss_cost")
        self.k_tex = cl.Kernel(prg, "wss_texture")
        self.K, self.fB = K, float(K[0, 0]) * float(baseline)
        w, h = fr.size
        per = 2 * w * h
        if group is None:
            budget = max(64, min(int(getattr(dev, "max_alloc_mb", 1024)), int(getattr(dev, "mem_mb", 2048)) // 3))
            group = max(1, min(len(fr), int(budget * 2 ** 20 // max(per, 1))))
        self.frames = [list(range(g0, min(len(fr), g0 + group))) for g0 in range(0, len(fr), group)]
        self.fr = fr
        self.size = (w, h)
        self._resident: tuple | None = None
        self._csr: list[dict] = []
        self.geo_buf = None
        self.n = 0

    def _buf(self, a: np.ndarray, write: bool = False) -> object:
        mf = self.cl.mem_flags
        a = np.ascontiguousarray(a)
        if a.size == 0:
            a = np.zeros(1, dtype=a.dtype)
        return self.cl.Buffer(self.dev.ctx, (mf.READ_WRITE if write else mf.READ_ONLY) | mf.COPY_HOST_PTR, hostbuf=a)

    def set_patches(self, geo: np.ndarray, vis: np.ndarray) -> None:
        """Patches ``geo`` (n, 18, centred) and which frames each uses, ``vis`` (n, n_frames)."""
        if self.geo_buf is not None:
            self.geo_buf.release()
        self.geo_buf = self._buf(np.asarray(geo, dtype=np.float32))
        self.n = len(geo)
        self._csr = []
        for fs in self.frames:
            sub = vis[:, fs]
            voff = np.zeros(len(vis) + 1, dtype=np.int32)
            np.cumsum(sub.sum(axis=1), out=voff[1:])
            vp, vf = np.nonzero(sub)  # row-major: grouped by patch, frames ascending
            self._csr.append({"voff": voff, "vpatch": vp.astype(np.int32), "vidx": vf.astype(np.int32)})

    def _images(self, gi: int) -> tuple:
        if self._resident is not None and self._resident[0] == gi:
            return self._resident
        if self._resident is not None:  # release the previous group first
            for b in self._resident[1:]:
                b.release()
            self._resident = None
        fs = self.frames[gi]
        self._resident = (gi, self._buf(np.stack([self.fr.left[f] for f in fs])),
                          self._buf(np.stack([self.fr.right[f] for f in fs])),
                          self._buf(self.fr.pose[fs].astype(np.float32)))
        return self._resident

    def _order(self) -> list[int]:
        order = list(range(len(self.frames)))
        if self._resident is not None:  # start with the group already on the device
            order.sort(key=lambda gi: gi != self._resident[0])
        return order

    def _common(self, gi: int) -> list:
        _, Lb, Rb, pb = self._images(gi)
        K = self.K
        w, h = self.size
        return [Lb, Rb, np.int32(w), np.int32(h), pb, np.float32(K[0, 0]), np.float32(K[1, 1]), np.float32(K[0, 2]),
                np.float32(K[1, 2]), np.float32(self.fB), self.geo_buf]

    def texture(self, ns: int, step: float, tex2: float) -> np.ndarray:
        out = np.zeros((self.n, len(self.fr)), dtype=bool)
        for gi in self._order():
            c = self._csr[gi]
            ne = len(c["vidx"])
            if ne == 0:
                continue
            flag = np.zeros(ne, dtype=np.uint8)
            fb, pbuf, ibuf = self._buf(flag, write=True), self._buf(c["vpatch"]), self._buf(c["vidx"])
            self.k_tex.set_args(*self._common(gi), np.int32(ne), pbuf, ibuf, np.int32(ns), np.float32(step),
                                np.float32(tex2), fb)
            self.cl.enqueue_nd_range_kernel(self.queue, self.k_tex, (((ne + 63) // 64) * 64,), (64,))
            self.cl.enqueue_copy(self.queue, flag, fb)
            self.queue.finish()
            for b in (fb, pbuf, ibuf):
                b.release()
            fs = np.asarray(self.frames[gi])
            out[c["vpatch"], fs[c["vidx"]]] = flag.astype(bool)
        return out

    def cost(self, deltas: np.ndarray, ns: int, step: float, cap: float) -> tuple[np.ndarray, np.ndarray]:
        n, D = deltas.shape
        C = np.zeros(n * D, dtype=np.float32)
        Wt = np.zeros(n * D, dtype=np.float32)
        d_buf = self._buf(np.asarray(deltas, dtype=np.float32))
        c_buf, w_buf = self._buf(C, write=True), self._buf(Wt, write=True)
        gsz = ((n * D + 63) // 64) * 64
        for gi in self._order():
            c = self._csr[gi]
            if len(c["vidx"]) == 0:
                continue
            ob, ib = self._buf(c["voff"]), self._buf(c["vidx"])
            self.k_cost.set_args(*self._common(gi), d_buf, np.int32(D), np.int32(n), ob, ib, np.int32(ns),
                                 np.float32(step), np.float32(cap), c_buf, w_buf)
            self.cl.enqueue_nd_range_kernel(self.queue, self.k_cost, (gsz,), (64,))
            self.queue.finish()
            ob.release()
            ib.release()
        self.cl.enqueue_copy(self.queue, C, c_buf)
        self.cl.enqueue_copy(self.queue, Wt, w_buf)
        self.queue.finish()
        for b in (d_buf, c_buf, w_buf):
            b.release()
        return C.reshape(n, D).astype(np.float64), Wt.reshape(n, D).astype(np.float64)


class _Cost:
    """The cost and the texture test on the GPU when there is one, else the CPU twin."""

    def __init__(self, patches: Patches, left: Sequence[np.ndarray], right: Sequence[np.ndarray], T_wc: np.ndarray,
                 K: np.ndarray, baseline: float, params: SpacetimeParams, device: str = "auto") -> None:
        self.origin = patches.P.mean(axis=0) if len(patches) else np.zeros(3)
        self.fr = _Frames(left, right, T_wc, self.origin)
        self.geo = patches.geo.copy()
        self.geo[:, 0:3] -= self.origin
        self.K, self.baseline, self.p = np.asarray(K, dtype=np.float64), float(baseline), params
        self.ns = int(params.samples)
        self.step = 2.0 * float(params.half_mm) / (self.ns - 1)
        self.tex2 = float(params.texture_dn) ** 2 * self.ns ** 2
        self.gpu = _engine(device, self.fr, self.K, self.baseline) if len(self.geo) else None
        self.device = "gpu" if self.gpu is not None else "cpu"
        self.sel = np.arange(len(self.geo))
        self.vis = np.zeros((len(self.geo), len(self.fr)), dtype=bool)

    def use(self, sel: np.ndarray, vis: np.ndarray) -> None:
        """Work on the patches ``sel`` with views ``vis`` (len(sel), n_frames)."""
        self.sel, self.vis = np.asarray(sel, dtype=np.int64), np.asarray(vis, dtype=bool)
        if self.gpu is not None:
            self.gpu.set_patches(self.geo[self.sel], self.vis)

    def texture(self) -> np.ndarray:
        if self.gpu is not None:
            return self.gpu.texture(self.ns, self.step, self.tex2)
        return _texture_cpu(self.geo[self.sel], self.fr, self.K, self.baseline, self.vis, self.ns, self.step, self.tex2)

    def cost(self, deltas: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if self.gpu is not None:
            return self.gpu.cost(deltas, self.ns, self.step, self.p.cap)
        return _cost_cpu(self.geo[self.sel], deltas, self.fr, self.K, self.baseline, self.vis, self.ns, self.step,
                         self.p.cap)


def patch_costs(patches: Patches, deltas: np.ndarray, left: Sequence[np.ndarray], right: Sequence[np.ndarray],
                T_wc: np.ndarray, K: np.ndarray, baseline: float, vis: np.ndarray,
                params: SpacetimeParams | None = None, device: str = "auto") -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(texture, C, W)``: which views of ``vis`` hold texture at each patch, (n, n_frames); and
    over those views the summed capped cost and the view count, both (n, D), of each patch moved
    by each of ``deltas`` (n, D) mm along its normal. ``device``: ``"auto"`` (the GPU when there is
    one), ``"gpu"`` or ``"cpu"``."""
    p = _need(params)
    deltas = np.asarray(deltas, dtype=np.float64).reshape(len(patches), -1)
    eng = _Cost(patches, left, right, T_wc, K, baseline, p, device)
    eng.use(np.arange(len(patches)), vis)
    tex = eng.texture()
    eng.use(np.arange(len(patches)), vis & tex)
    C, W = eng.cost(deltas)
    return tex, C, W


def _engine(device: str, fr: _Frames, K: np.ndarray, baseline: float) -> _GpuCost | None:
    if device == "cpu" or len(fr) == 0:
        return None
    from ohs.compute import get_device, status  # noqa: PLC0415

    dev = get_device()
    if dev is None:
        if device == "gpu":
            raise RuntimeError(f"no GPU for the spacetime cost: {status()}")
        return None
    try:
        return _GpuCost(dev, fr, K, baseline)
    except Exception as e:  # a build or memory failure: the CPU twin still works
        if device == "gpu":
            raise
        log.warning("spacetime cost on the GPU failed (%s); using the CPU", e)
        return None


# --------------------------------------------------------------------------- the search
def search_offsets(patches: Patches, left: Sequence[np.ndarray], right: Sequence[np.ndarray], T_wc: np.ndarray,
                   K: np.ndarray, baseline: float, vis: np.ndarray, params: SpacetimeParams | None = None, *,
                   device: str = "auto", progress: Progress | None = None,
                   cancelled: Callable[[], bool] | None = None) -> dict:
    """Coarse-to-fine search of each patch's offset along its normal (module docstring, steps 3-4).

    First the views without texture at the patch are dropped (tested once, on the fitted surface,
    so that every candidate offset is scored on the same views); patches left with fewer than
    ``min_views`` are not searched.

    Returns ``{"offset", "views", "cost", "curvature", "kept", "reason", "device"}``: per patch the
    offset (mm, from the fitted surface), textured views and mean cost at the minimum, the cost's
    curvature there (1/mm^2), whether it is kept, and why not (``"views"``, ``"edge"``, ``"flat"``,
    ``"cost"``, or ``""``)."""
    p = _need(params)
    say = progress or (lambda f, m: None)
    n = len(patches)
    vis = np.asarray(vis, dtype=bool)
    out = {"offset": np.zeros(n), "views": np.zeros(n, dtype=np.int64), "cost": np.full(n, np.inf),
           "curvature": np.zeros(n), "kept": np.zeros(n, dtype=bool), "reason": np.full(n, "views", dtype=object),
           "device": "cpu"}
    if n == 0 or len(left) == 0:
        return out
    eng = _Cost(patches, left, right, T_wc, K, baseline, p, device)
    out["device"] = eng.device
    say(0.0, "testing the views for texture")
    cand = np.flatnonzero(vis.sum(axis=1) >= p.min_views)
    eng.use(cand, vis[cand])
    tex = eng.texture()
    textured = np.zeros(n, dtype=np.int64)
    textured[cand] = tex.sum(axis=1)
    out["views"] = textured
    keep = textured[cand] >= p.min_views
    sel, vsel = cand[keep], tex[keep]
    m = len(sel)
    if m == 0:
        return out
    eng.use(sel, vsel)
    centre = np.zeros(m)
    rows = np.arange(m)
    edge = np.zeros(m, dtype=bool)
    for li, (r, s) in enumerate(p.levels):
        if cancelled is not None and cancelled():
            raise InterruptedError("cancelled")
        slow = "" if eng.device == "gpu" else ", on the CPU: this takes minutes"
        say(0.1 + 0.9 * li / len(p.levels), f"searching along the normals ({li + 1} of {len(p.levels)}{slow})")
        offs = np.arange(-r, r + 0.5 * s, s)
        deltas = centre[:, None] + offs[None, :]
        C, Wt = eng.cost(deltas)
        Cm = C / np.maximum(Wt, 1.0)
        j = np.argmin(Cm, axis=1)
        if li == 0:
            edge = (j == 0) | (j == len(offs) - 1)
        if li < len(p.levels) - 1:
            centre = deltas[rows, j]
            continue
        jc = np.clip(j, 1, len(offs) - 2)
        c0, c1, c2 = Cm[rows, jc - 1], Cm[rows, jc], Cm[rows, jc + 1]
        den = c0 - 2.0 * c1 + c2
        sub = np.clip(np.where(den > 1e-12, 0.5 * (c0 - c2) / np.maximum(den, 1e-12), 0.0), -1.0, 1.0)
        out["offset"][sel] = deltas[rows, jc] + sub * s
        out["curvature"][sel] = den / (s * s)
        out["cost"][sel] = c1 - 0.5 * den * sub * sub
    reason = np.full(n, "", dtype=object)
    reason[out["cost"] > p.max_cost] = "cost"
    reason[out["curvature"] < p.min_curvature] = "flat"
    e = np.zeros(n, dtype=bool)
    e[sel] = edge
    reason[e] = "edge"
    reason[textured < p.min_views] = "views"
    out["reason"] = reason
    out["kept"] = reason == ""
    say(1.0, "searched")
    return out


# --------------------------------------------------------------------------- the stage
def refine_from_images(V: np.ndarray, F: np.ndarray, left: Sequence[np.ndarray], right: Sequence[np.ndarray],
                       T_wc: np.ndarray, K: np.ndarray, baseline: float, params: SpacetimeParams | None = None, *,
                       N: np.ndarray | None = None, device: str = "auto", progress: Progress | None = None,
                       cancelled: Callable[[], bool] | None = None) -> tuple[np.ndarray, np.ndarray, dict]:
    """Refine the mesh ``(V, F)`` against rectified frames ``left`` / ``right`` taken at ``T_wc``
    (rectified left camera to mesh frame, mm), intrinsics ``K`` and ``baseline`` of the rectified
    pair (module docstring).

    Returns ``(V2, delta, report)``: the moved vertices, each vertex's offset along its normal
    (mm), and ``report``: ``patches``, ``kept``, ``rejected`` (by reason), ``frames``,
    ``views_median``, ``offset`` (``median`` / ``p95`` of the kept patches' absolute offsets from
    the mesh, ``mean`` signed), ``cost_median``, ``covered_share`` (vertices fully moved onto
    patches), ``moved_share`` / ``moved`` (vertices moved at least 0.005 mm; ``median`` / ``p95`` /
    ``max`` of the move), ``device``, ``half_mm`` / ``spacing_mm`` / ``samples``, ``ms`` (per step),
    and ``patch_results`` (per patch arrays, for diagnostics: drop it before saving the report).
    """
    from ohs.mesh.topology import as_mesh, vertex_normals  # noqa: PLC0415

    say = progress or (lambda f, m: None)
    V, F = as_mesh(V, F)
    if N is None:
        N = vertex_normals(V, F)
    size = (int(np.asarray(left[0]).shape[1]), int(np.asarray(left[0]).shape[0])) if len(left) else (0, 0)
    p = resolve_params(params, V, T_wc, K, size)
    ms: dict[str, float] = {}
    t0 = time.perf_counter()
    say(0.0, "placing patches on the surface")
    patches = make_patches(V, F, N, p.spacing_mm, p.half_mm, fit=p.fit)
    ms["patches"] = round(1e3 * (time.perf_counter() - t0), 1)
    t0 = time.perf_counter()
    say(0.05, f"finding which frames see each of {len(patches):,} patches")
    vis = patch_views(patches, T_wc, K, baseline, size, V, F, p, cancelled=cancelled)
    ms["views"] = round(1e3 * (time.perf_counter() - t0), 1)
    t0 = time.perf_counter()
    res = search_offsets(patches, left, right, T_wc, K, baseline, vis, p, device=device, cancelled=cancelled,
                         progress=lambda f, m: say(0.15 + 0.75 * f, m))
    ms["search"] = round(1e3 * (time.perf_counter() - t0), 1)
    kept = res["kept"]
    report: dict = {"patches": int(len(patches)), "kept": int(kept.sum()),
                    "rejected": {k: int(np.sum(res["reason"] == k)) for k in ("views", "edge", "flat", "cost")},
                    "frames": int(len(left)), "views_median": int(np.median(vis.sum(axis=1))) if len(patches) else 0,
                    "device": res["device"], "half_mm": p.half_mm, "spacing_mm": p.spacing_mm, "samples": p.samples}
    total = patches.geo[:, 12] + res["offset"] if len(patches) else np.zeros(0)
    tk = total[kept]
    report["offset"] = ({"median": round(float(np.median(np.abs(tk))), 4),
                         "p95": round(float(np.percentile(np.abs(tk), 95)), 4),
                         "mean": round(float(np.mean(tk)), 4)} if len(tk) else {"median": None, "p95": None, "mean": None})
    report["cost_median"] = round(float(np.median(res["cost"][kept])), 4) if kept.any() else None
    t0 = time.perf_counter()
    say(0.92, "moving the surface")
    sub = Patches(patches.vid[kept], patches.geo[kept])
    move, cover = project_onto_patches(V, N, sub, res["offset"][kept], p.half_mm, p.smooth_mm or p.spacing_mm,
                                       p.gate_mm)
    V2 = V + move
    delta = np.einsum("ij,ij->i", move, N)
    ms["move"] = round(1e3 * (time.perf_counter() - t0), 1)
    dist = np.linalg.norm(move, axis=1)
    moved = dist >= 0.005
    report.update({"covered_share": round(float(np.mean(cover >= 0.999)), 4), "moved_share": round(float(moved.mean()), 4),
                   "moved": ({"median": round(float(np.median(dist[moved])), 4),
                              "p95": round(float(np.percentile(dist[moved], 95)), 4),
                              "max": round(float(dist[moved].max()), 4)} if moved.any()
                             else {"median": None, "p95": None, "max": None}), "ms": ms})
    report["patch_results"] = {"vid": patches.vid, "offset": total, "views": res["views"], "cost": res["cost"],
                               "curvature": res["curvature"], "kept": kept, "point": patches.point(res["offset"]),
                               "fitted": patches.point(np.zeros(len(patches))), "seen": vis.sum(axis=1),
                               "reason": res["reason"]}
    return V2, delta, report


def project_onto_patches(V: np.ndarray, N: np.ndarray, patches: Patches, offset: np.ndarray, half_mm: float,
                         sigma_mm: float, gate_mm: float = 1.0, k: int = 12) -> tuple[np.ndarray, np.ndarray]:
    """Move each vertex onto the refined surface of the patches around it (module docstring,
    step 5): each patch within ``half_mm`` (on the same side of a thin wall) says how far the vertex
    lies off its quadric moved by ``offset``, along its normal; these are averaged with Gaussian
    weights (``sigma_mm``). Where the patches thin out (total weight under that of one patch at the
    typical distance) the move fades to nothing, so uncovered areas keep the fused surface.

    Returns ``(move (n,3), cover (n,))``: the displacement and the fade (1 = fully covered)."""
    from scipy.spatial import cKDTree  # noqa: PLC0415

    V = np.asarray(V, dtype=np.float64)
    n = len(V)
    move = np.zeros((n, 3))
    cover = np.zeros(n)
    if len(patches) == 0 or n == 0:
        return move, cover
    g = patches.geo
    tree = cKDTree(g[:, 0:3])
    full = float(np.exp(-0.5 * (0.5 * half_mm / sigma_mm) ** 2))  # one patch at half its reach
    kk = min(k, len(patches))
    for c0 in range(0, n, 200_000):
        Vc = V[c0:c0 + 200_000]
        dist, j = tree.query(Vc, k=kk, distance_upper_bound=half_mm, workers=-1)
        if dist.ndim == 1:
            dist, j = dist[:, None], j[:, None]
        valid = np.isfinite(dist)
        j = np.where(valid, j, 0)
        G = g[j]  # (c, k, 18)
        X = Vc[:, None, :] - G[..., 0:3]
        a = np.einsum("ijk,ijk->ij", X, G[..., 3:6])
        b = np.einsum("ijk,ijk->ij", X, G[..., 6:9])
        h = np.einsum("ijk,ijk->ij", X, G[..., 9:12])
        q = G[..., 12:18]
        target = q[..., 0] + q[..., 1] * a + q[..., 2] * b + q[..., 3] * a * a + q[..., 4] * a * b + q[..., 5] * b * b
        r = target + np.asarray(offset)[j] - h
        same = np.einsum("ik,ijk->ij", N[c0:c0 + 200_000], G[..., 9:12]) > 0.5
        w = np.where(valid & same & (np.abs(r) <= gate_mm), np.exp(-0.5 * (np.where(valid, dist, 0.0) / sigma_mm) ** 2), 0.0)
        W = w.sum(axis=1)
        m = np.einsum("ij,ij,ijk->ik", w, r, G[..., 9:12]) / np.maximum(W, 1e-12)[:, None]
        x = np.clip(W / full, 0.0, 1.0)
        fade = x * x * (3.0 - 2.0 * x)
        move[c0:c0 + 200_000] = m * fade[:, None]
        cover[c0:c0 + 200_000] = fade
    return move, cover


def frames_from_recording(recording: str, calib: object, poses: dict[int, np.ndarray], max_frames: int = 120,
                          cancelled: Callable[[], bool] | None = None, progress: Progress | None = None
                          ) -> tuple[list[np.ndarray], list[np.ndarray], np.ndarray, np.ndarray, float]:
    """Up to ``max_frames`` frames with a pose (``{frame index: T_wc}``), spread evenly over the
    recording, rectified at full resolution: ``(left, right, T_wc (n,4,4), K, baseline)``."""
    from ohs.io.recording import RecordingReader  # noqa: PLC0415
    from ohs.vision.rectify import Rectifier  # noqa: PLC0415

    reader = RecordingReader(recording)
    rect = Rectifier(calib.stereo, 1.0)
    pos =[k for k, i in enumerate(reader.indices) if int(i) in poses]
    if len(pos) > max_frames:
        pos = [pos[int(round(x))] for x in np.linspace(0, len(pos) - 1, max_frames)]
    left, right, T = [], [], []
    for n_done, k in enumerate(pos):
        if cancelled is not None and cancelled():
            raise InterruptedError("cancelled")
        if progress is not None:
            progress(n_done / max(len(pos), 1), f"reading frame {n_done + 1} of {len(pos)}")
        fr = reader.frame(k)
        lr, rr = rect.rectify(fr.left, fr.right)
        left.append(lr)
        right.append(rr)
        T.append(poses[int(fr.index)])
    T_wc = np.asarray(T, dtype=np.float64).reshape(-1, 4, 4)
    return left, right, T_wc, np.asarray(rect.K, dtype=np.float64), float(rect.baseline)
