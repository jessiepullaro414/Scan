"""Sparse reconstruction TSDF that says how sure it is of every voxel (M3 phase 3, item 2).

:class:`ConfidenceTsdf` is the reconstruction model of :class:`ohs.app.pipeline.FramePipeline`: the
fused surface that becomes the mesh. It averages the depth exactly as Open3D's
``VoxelBlockGrid.integrate`` does (every observation weighs the same; with all sigmas the same it
gives Open3D's mesh, vertex for vertex), and alongside the average every voxel keeps what it takes
to say how sure it is ("honest averaging", ``docs/RESEARCH_PLAN.md`` 7.10). The per-pixel
uncertainty comes from the matcher (:meth:`ohs.vision.depth.DepthEstimator.compute_with_sigma`).

Weighting the average itself by ``1 / sigma^2`` (the research plan's 4.5.1) made the mesh worse on
the simulator (median +15-25 %, more spurious surface): the weights span many orders of magnitude,
so a few "confident" frames outvote many frames that agree, and the camera-fixed dot errors that
plain averaging cancels across frames come back (``VALIDATION.md``, "Depth confidence").

**Layout.** Bricks of 8^3 voxels (8 mm at 1 mm voxels) in a pool that grows by doubling, indexed on
the CPU (sorted brick keys -> pool slot). A frame allocates the bricks its depth band ``[d - trunc,
d + trunc]`` reaches, sampled about every 2.5 mm across the image (every 4th pixel at 1280 px width)
and at three depths (the spacing grows with the voxel size, so the samples per brick stay the same).
With a GPU this search runs there: a hash set of the frame's bricks, of which only the list comes
back (keys of 10 bits per axis, within 512 bricks of the first camera; beyond that, the CPU search).
Voxel ``(x, y, z)`` of brick ``b`` sits at world position ``(8 b + (x, y, z)) *
voxel_mm``, Open3D's convention for block resolution 8. Per voxel, 40 bytes:

- ``tsdf``: the mean of the truncated projective SDF ``min(d - z, trunc)`` (mm, camera z);
- ``wsum``: the sum of the averaging weights (1 per observation), and ``m2``: the sum of squared
  deviations from the mean (Welford): the scatter actually observed, along the camera z;
- ``xc`` and ``mxc``: the same mean and scatter of the distance along the surface *normal* (the SDF
  times the cosine of the ray with the depth map's own normal);
- ``snh2``: the sum of the observations' variances ``min(sigma, trunc)^2``, with the matcher's
  factor for pixels next to missing depth taken out of the sigma again (below);
- ``hnear``: the number of observations next to a pixel without depth;
- ``nobs``: the number of observations, and ``nlook``: the number of *independent looks*;
- ``luv``: the pixel of the last counted look.

**Independent looks.** The dots are fixed to the scanner, so a surface point keeps seeing the same
dots until its image position has moved by about a dot spacing, and frames that see the same dots
make nearly the same error (``VALIDATION.md``: a correlation of 0.4 between frames 0.1 s apart). A
look counts when the voxel's pixel is at least ``look_px`` from the last counted one: the dot
spacing, 10 px at 1280 px width.

**A voxel's standard deviation** (mm, along the surface normal; :func:`voxel_sigma`) has a noise
part and bias parts::

    sigma^2 = C_NOISE^2 (mxc / wsum) / nlook
            + (A_GAP hnear / nobs)^2
            + A_SCATTER^2 max(m2 / wsum - snh2 nobs / wsum^2, 0)

The noise part is the scatter observed along the normal, divided by the independent looks, never by
the correlated frames. The bias parts do not average out: the share of observations within 6 px (at
1280 px width) of a pixel without depth, where the matcher's windows carry a surface into a gap or
over an edge; and the scatter beyond what the pixels' own sigmas predict, the mark of two surfaces
fused into one voxel. For that prediction the matcher's sigma is used without its factor for pixels
next to missing depth (``hole_factor``; up to 121 times, made to cover a tail, not a variance).
Marching cubes interpolates the voxels' sigma to every vertex, and :meth:`ConfidenceTsdf.extract_mesh`
adds what only the mesh shows (:func:`mesh_sigma_terms`): curvature and nearness to the open border.

The five constants were fitted on simulated scans against the true error (``VALIDATION.md``, "The
vertex sigma"; 1280 px, 1 mm voxels, the GPU matcher). There 2 sigma covers 96-99 % of the vertices
on flat surface and about 90 % at edges, with a sigma a quarter of the earlier rule's on flat
surface. The bias constants that are lengths grow with the voxel size (:func:`bias_scale`): at 640 px
with 2 mm voxels the errors next to gaps, edges and borders are about twice as large, and without
that factor 2 sigma covered 91 % there instead of 98 %. That rule, ``max(mean of the pixels' capped variances, m2 / wsum) / nlook``, was 4 to 5.5
times too large there (the mean is ruled by the few observations next to gaps) and too small at
edges, where the error is a bias.

**The distance to missing depth** is the Euclidean distance (px) to the nearest pixel without depth
in the fused range, searched up to 3 radii of the matcher's factor (15 px), as the GPU matcher
measures it: two kernels (along the rows, then across them), or OpenCV's exact transform in numpy.

**The gap guard** (off by default). Where nothing can be matched behind an edge (a hole whose background is dark,
unlit or seen by one camera only), the matcher's windows carry the near surface a few pixels into
the gap, on every side, and fused from all round these fills close the hole: a 25 mm hole fused to
an opening of 12-14 mm (``VALIDATION.md``, "Holes filled in"). With the guard on, depth within
``gap_guard_px`` (8 px at 1280 px width) of a *patch* without depth is not fused
(:func:`gap_guard_mask`). A pixel without
depth belongs to a patch when at least 13 of the 5x5 around it have none either, so the single gaps
between dots do not count. The guard works on the fused depth only: the tracker keeps every pixel
(dropping them there moved the trajectory), and the bricks are searched on the whole depth. Its
price is a thinner rim next to gaps: less of a steep side wall, and convex features 0.2-0.5 mm
smaller in diameter; with tracking, a small part with many edges lost 1-7 points of its coverage
and its worst errors grew by 0.1-0.2 mm, which is why it is not on by default (``VALIDATION.md``,
"The gap guard in the engine"). For the sigma's count of observations "next to a pixel without depth"
(``hnear``), what the guard drops counts as without depth: the count is about the depth that is
fused. (The matcher's factor is taken out with the distance on the depth as the matcher made it.)

**Backends.** ``"gpu"``: OpenCL on :func:`ohs.compute.get_device` (one work-item per voxel of the
frame's bricks, so no voxel is written twice). ``"cpu"``: the same arithmetic in numpy, which is
also the tests' reference. ``"auto"`` picks the GPU when there is one.

**Meshing** copies the bricks into a CPU Open3D ``VoxelBlockGrid`` (block resolution 8). Its
``weight`` is ``nobs``, so ``weight_threshold`` keeps meaning "fused more than so many times", and
its ``color`` carries ``(sigma, nlook, wsum)``, which marching cubes interpolates like a colour
(Open3D scales colours by 1/255 on the way out; undone here).

**Speed** (1280x800): about 10 ms per frame on the Radeon 680M (the brick search 1 ms of it, 8 ms
on the CPU; Open3D's CPU integration: 15-18 ms), at 640x400 with 2 mm voxels about 4 ms. The kernels
apply :func:`pixel_weights`' rules themselves, on the raw depth and sigma. The numpy twin needs about
0.5 s per frame, so without a GPU the pipeline keeps Open3D (no confidence) unless asked for this model.
The mesh's own sigma terms take about a second per million triangles, once, when the mesh is made.

The interface mirrors :class:`ohs.track.markerless.MarkerlessTracker`'s model side (``integrate``,
``extract_mesh``, ``extract_points``, ``n_integrated``, ``reset``, ``close``), so the pipeline can use
either. Not thread-safe.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

__all__ = ["ConfidenceTsdf", "BRICK", "RECON_BACKENDS", "voxel_sigma", "mesh_sigma_terms", "bias_scale", "pixel_weights",
           "gap_guard_mask", "GUARD_PX_1280", "GUARD_SEED",
           "C_NOISE", "A_GAP", "A_SCATTER", "A_CURV", "A_BORDER", "GAP_NEAR_PX_1280", "BORDER_MM"]

log = logging.getLogger(__name__)

BRICK = 8
NVOX = BRICK ** 3
RECON_BACKENDS = ("auto", "gpu", "cpu")
LOOK_PX_1280 = 10.0  # dot spacing (px) at 1280 px width: one independent look per move of this much
_OFF = 1 << 20  # brick key offset: +-1M bricks per axis
_FLOAT_CHANNELS = ("tsdf", "wsum", "m2", "nobs", "nlook", "xc", "mxc", "hnear", "snh2")
# The sigma model's constants (module docstring; fitted on simulated scans, VALIDATION.md "The vertex sigma")
C_NOISE = 0.5583  # share of (normal scatter / looks) that is the noise of the mean
A_GAP = 0.9177  # mm at a voxel all of whose observations were next to a pixel without depth
A_SCATTER = 2.970  # times the scatter (mm) beyond what the pixels' sigmas predict
A_CURV = 1.0579  # mm times sqrt(1 - n . mean normal of the 2-ring), its largest value in the 2-ring
A_BORDER = 0.5325  # mm on the mesh's open border, falling off as exp(-distance / BORDER_MM)
GAP_NEAR_PX_1280 = 6.0  # "next to a pixel without depth": within this many px, at 1280 px width
BORDER_MM = 1.5
# The gap guard (module docstring)
GUARD_PX_1280 = 8.0  # depth within this many px of a patch without depth is not fused, at 1280 px width
GUARD_SEED = 13  # a pixel without depth is part of a patch when this many of the 5x5 around it have none

_SRC = r"""
// OpenCL C 1.2. Sparse confidence TSDF of ohs/track/recon_tsdf.py (see its docstring).
// Brick slot s holds voxels s * 512 + (lz * 64 + ly * 8 + lx), one array per channel.
__kernel void recon_integrate(
    __global float *tsdf, __global float *wsum, __global float *m2, __global float *nobs,
    __global float *nlook, __global uint *luv,
    __global float *xc, __global float *mxc, __global float *hnear, __global float *snh2,
    __global const int *slots, __global const int *keys, const int n_active,
    __global const float *depth, __global const float *sigma, const int has_sigma, const int w, const int h,
    __global const float *gap2, __global const float *gapg,
    const float fx, const float fy, const float cx, const float cy, const float sk,
    const float r00, const float r01, const float r02,
    const float r10, const float r11, const float r12,
    const float r20, const float r21, const float r22,
    const float t0, const float t1, const float t2,
    const float voxel, const float trunc, const float depth_min, const float depth_max, const float look2,
    const float near2, const float hole_amp, const float hole_r2)
{
    const int gid = get_global_id(0);
    const int b = gid >> 9;
    if (b >= n_active) return;
    const int l = gid & 511;
    const float X = (float)(keys[3 * b] * 8 + (l & 7)) * voxel;
    const float Y = (float)(keys[3 * b + 1] * 8 + ((l >> 3) & 7)) * voxel;
    const float Z = (float)(keys[3 * b + 2] * 8 + (l >> 6)) * voxel;
    const float pz = r20 * X + r21 * Y + r22 * Z + t2;
    if (pz <= 1e-3f) return;
    const float px = r00 * X + r01 * Y + r02 * Z + t0;
    const float py = r10 * X + r11 * Y + r12 * Z + t1;
    const float iz = 1.0f / pz;
    const float u = fx * px * iz + sk * py * iz + cx;
    const float v = fy * py * iz + cy;
    if (!(u >= -0.5f && u < (float)w - 0.5f && v >= -0.5f && v < (float)h - 0.5f)) return;
    const int ui = min((int)floor(u + 0.5f), w - 1), vi = min((int)floor(v + 0.5f), h - 1);
    const int pix = vi * w + ui;
    const float d = depth[pix];  // raw: NaN, or outside [depth_min, depth_max], is no depth
    if (!(d >= depth_min && d <= depth_max)) return;
    const float sdf = d - pz;
    if (sdf < -trunc) return;
    // the weight and variance of pixel_weights(): 1, and min(sigma, trunc)^2 (trunc^2 / 9 without sigmas),
    // without the matcher's factor for pixels next to missing depth (1 + hole_amp exp(-g2 / hole_r2))
    const float wt = 1.0f;
    const float g2 = gap2[pix];  // squared distance (px) to the nearest pixel without depth
    const float gg = gapg[pix];  // the same, with what the gap guard drops counted as without depth
    if (gg == 0.0f) return;  // the gap guard dropped this pixel
    float var = trunc * trunc / 9.0f;
    if (has_sigma) {
        const float sg = sigma[pix];
        const float sc = (isnan(sg) || isinf(sg)) ? trunc : fmin(sg / (1.0f + hole_amp * exp(-g2 / hole_r2)), trunc);
        var = sc * sc;
    }
    // cosine of the ray with the depth map's own normal (as the matcher's sigma_z); 1 next to no depth
    float cs = 1.0f;
    if (ui > 0 && ui < w - 1 && vi > 0 && vi < h - 1) {
        const float dl = depth[pix - 1], dr = depth[pix + 1], dt = depth[pix - w], db = depth[pix + w];
        if (dl >= depth_min && dl <= depth_max && dr >= depth_min && dr <= depth_max &&
            dt >= depth_min && dt <= depth_max && db >= depth_min && db <= depth_max) {
            const float zu = 0.5f * (dr - dl), zv = 0.5f * (db - dt);
            const float pu = (float)ui - cx, pv = (float)vi - cy;
            const float n3 = pu * zu + pv * zv + d;
            const float gu = zu * fx, gv = zv * fy, ru = pu / fx, rv = pv / fy;
            const float nn = sqrt(gu * gu + gv * gv + n3 * n3);
            const float rr = sqrt(ru * ru + rv * rv + 1.0f);
            cs = clamp(d / fmax(nn * rr, 1e-6f), 0.05f, 1.0f);
        }
    }
    const float x = fmin(sdf, trunc);
    const int a = slots[b] * 512 + l;
    const float W0 = wsum[a], m0 = tsdf[a];
    const float W1 = W0 + wt;
    const float m1 = m0 + (wt / W1) * (x - m0);
    m2[a] += wt * (x - m0) * (x - m1);
    snh2[a] += wt * wt * var;
    const float xn = x * cs, q0 = xc[a];
    const float q1 = q0 + (wt / W1) * (xn - q0);
    mxc[a] += wt * (xn - q0) * (xn - q1);
    xc[a] = q1;
    hnear[a] += (gg < near2) ? 1.0f : 0.0f;
    tsdf[a] = m1;
    wsum[a] = W1;
    nobs[a] += 1.0f;
    const uint uv = luv[a];
    const float du = (float)ui - (float)(uv & 0xFFFFu), dv = (float)vi - (float)(uv >> 16);
    if (nlook[a] == 0.0f || du * du + dv * dv >= look2) {
        nlook[a] += 1.0f;
        luv[a] = (uint)ui | ((uint)vi << 16);
    }
}

// The distance to missing depth (module docstring), step 1: along the row, up to R px (R + 1: none).
// With use_mark, the pixels the gap guard drops (mark) count as without depth too.
__kernel void recon_gap_row(__global const float *depth, __global float *hx, const int w, const int h, const int R,
                            const float depth_min, const float depth_max,
                            __global const uchar *mark, const int use_mark)
{
    const int x = get_global_id(0), y = get_global_id(1);
    if (x >= w || y >= h) return;
    const int row = y * w;
    float best = (float)(R + 1);
    for (int dx = 0; dx <= R; ++dx) {
        int gap = 0;
        if (x - dx >= 0) {
            const float d = depth[row + x - dx];
            gap = !(d >= depth_min && d <= depth_max) || (use_mark && mark[row + x - dx]);
        }
        if (!gap && x + dx < w) {
            const float d = depth[row + x + dx];
            gap = !(d >= depth_min && d <= depth_max) || (use_mark && mark[row + x + dx]);
        }
        if (gap) {
            best = (float)dx;
            break;
        }
    }
    hx[row + x] = best;
}

// Step 2: the squared Euclidean distance, from the row distances within +-R rows; at most (R + 1)^2.
__kernel void recon_gap_dist(__global const float *hx, __global float *gap2, const int w, const int h, const int R)
{
    const int x = get_global_id(0), y = get_global_id(1);
    if (x >= w || y >= h) return;
    float g2 = (float)((R + 1) * (R + 1));
    for (int dy = -R; dy <= R; ++dy) {
        const int yy = y + dy;
        if (yy < 0 || yy >= h) continue;
        const float d = hx[yy * w + x];
        g2 = fmin(g2, d * d + (float)(dy * dy));
    }
    gap2[y * w + x] = g2;
}

// The gap guard's mark: the pixels within sqrt(guard2) px of a patch without depth, from the row
// distances to the patches (hs, searched up to R px). It marks pixels without depth too.
__kernel void recon_guard_mark(__global const float *hs, __global uchar *mark, const int w, const int h, const int R,
                               const float guard2)
{
    const int x = get_global_id(0), y = get_global_id(1);
    if (x >= w || y >= h) return;
    float s2 = (float)((R + 1) * (R + 1));
    for (int dy = -R; dy <= R; ++dy) {
        const int yy = y + dy;
        if (yy < 0 || yy >= h) continue;
        const float d = hs[yy * w + x];
        s2 = fmin(s2, d * d + (float)(dy * dy));
    }
    mark[y * w + x] = (uchar)(s2 <= guard2);
}

// The gap guard's patches: a pixel without depth of which at least nseed of the 5x5 around it (the
// border repeated) have none either.
__kernel void recon_guard_seed(__global const float *depth, __global uchar *seed, const int w, const int h,
                               const float depth_min, const float depth_max, const int nseed)
{
    const int x = get_global_id(0), y = get_global_id(1);
    if (x >= w || y >= h) return;
    const float d0 = depth[y * w + x];
    uchar s = 0;
    if (!(d0 >= depth_min && d0 <= depth_max)) {
        int n = 0;
        for (int dy = -2; dy <= 2; ++dy) {
            const int row = clamp(y + dy, 0, h - 1) * w;
            for (int dx = -2; dx <= 2; ++dx) {
                const float d = depth[row + clamp(x + dx, 0, w - 1)];
                n += !(d >= depth_min && d <= depth_max);
            }
        }
        s = (uchar)(n >= nseed);
    }
    seed[y * w + x] = s;
}

// The distance along the row to the nearest patch pixel, up to R px (R + 1: none).
__kernel void recon_guard_row(__global const uchar *seed, __global float *hs, const int w, const int h, const int R)
{
    const int x = get_global_id(0), y = get_global_id(1);
    if (x >= w || y >= h) return;
    const int row = y * w;
    float best = (float)(R + 1);
    for (int dx = 0; dx <= R; ++dx) {
        if ((x - dx >= 0 && seed[row + x - dx]) || (x + dx < w && seed[row + x + dx])) {
            best = (float)dx;
            break;
        }
    }
    hs[row + x] = best;
}

// The brick search of _frame_bricks: every step-th pixel's ray at d - trunc, d and d + trunc, in bricks
// relative to an origin brick (r and t are T_wc in bricks, t minus the origin). A key packs 10 bits per
// axis (+-512 bricks); keys outside that are counted in count[1] and the host searches on the CPU instead.
// A hash set (open addressing, 0xFFFFFFFF = empty) lets out[] get every brick once.
__kernel void recon_bricks(
    __global const float *depth, const int w, const int step, const int gw, const int gh,
    const float fx, const float fy, const float cx, const float cy, const float sk,
    const float r00, const float r01, const float r02,
    const float r10, const float r11, const float r12,
    const float r20, const float r21, const float r22,
    const float t0, const float t1, const float t2,
    const float trunc, const float depth_min, const float depth_max,
    __global uint *table, const int shift, __global uint *out, __global int *count)
{
    const int i = get_global_id(0), j = get_global_id(1);
    if (i >= gw || j >= gh) return;
    const int u = step / 2 + i * step, v = step / 2 + j * step;
    const float z = depth[v * w + u];
    if (!(z >= depth_min && z <= depth_max)) return;  // NaN compares false
    const float y = ((float)v - cy) / fy;
    const float x = ((float)u - cx - sk * y) / fx;
    const float dx = r00 * x + r01 * y + r02, dy = r10 * x + r11 * y + r12, dz = r20 * x + r21 * y + r22;
    const float px = dx * z + t0, py = dy * z + t1, pz = dz * z + t2;
    const uint mask = (1u << (32 - shift)) - 1u;
    for (int k = -1; k <= 1; ++k) {
        const float off = (float)k * trunc;
        const int bx = (int)floor(px + off * dx) + 512, by = (int)floor(py + off * dy) + 512;
        const int bz = (int)floor(pz + off * dz) + 512;
        if ((uint)bx > 1023u || (uint)by > 1023u || (uint)bz > 1023u) {
            atomic_inc(count + 1);
            continue;
        }
        const uint key = ((uint)bx << 20) | ((uint)by << 10) | (uint)bz;
        uint hh = (key * 2654435761u) >> shift;
        for (;;) {
            uint cur = table[hh];
            if (cur == 0xFFFFFFFFu) {
                cur = atomic_cmpxchg(table + hh, 0xFFFFFFFFu, key);
                if (cur == 0xFFFFFFFFu) {
                    out[atomic_inc(count)] = key;
                    break;
                }
            }
            if (cur == key) break;
            hh = (hh + 1u) & mask;
        }
    }
}
"""
_I, _F = np.int32, np.float32
_ARGS = [None] * 12 + [_I] + [None, None] + [_I, _I, _I] + [None, None] + [_F] * 5 + [_F] * 12 + [_F] * 8
_GAP_ROW_ARGS = [None, None, _I, _I, _I, _F, _F, None, _I]
_GAP_DIST_ARGS = [None, None, _I, _I, _I]
_GUARD_MARK_ARGS = [None, None, _I, _I, _I, _F]
_GUARD_SEED_ARGS = [None, None, _I, _I, _F, _F, _I]
_GUARD_ROW_ARGS = [None, None, _I, _I, _I]
_BRICK_ARGS = [None, _I, _I, _I, _I] + [_F] * 5 + [_F] * 12 + [_F] * 3 + [None, _I, None, None]
_KEY_BITS, _KEY_HALF = 10, 512  # compact brick keys of the GPU search: 10 bits per axis around the origin


def _encode(k: np.ndarray) -> np.ndarray:
    k = np.asarray(k, dtype=np.int64) + _OFF
    return (k[:, 0] << 42) | (k[:, 1] << 21) | k[:, 2]


def _decode(e: np.ndarray) -> np.ndarray:
    e = np.asarray(e, dtype=np.int64)
    return np.stack([(e >> 42) & 0x1FFFFF, (e >> 21) & 0x1FFFFF, e & 0x1FFFFF], axis=1) - _OFF


def bias_scale(voxel_mm: float) -> float:
    """Factor on the model's bias constants that are lengths (``A_GAP``, ``A_CURV``, ``A_BORDER``,
    ``BORDER_MM``): the voxel size in mm, and never below the 1 mm they were fitted at."""
    return max(1.0, float(voxel_mm))


def voxel_sigma(wsum: np.ndarray, m2: np.ndarray, nobs: np.ndarray, nlook: np.ndarray, mxc: np.ndarray,
                hnear: np.ndarray, snh2: np.ndarray, voxel_mm: float = 1.0) -> np.ndarray:
    """Standard deviation (mm, along the normal) of each voxel's mean; 0 where unobserved (module
    docstring): ``sqrt(C_NOISE^2 mxc / wsum / max(nlook, 1) + (A_GAP k hnear / nobs)^2 +
    A_SCATTER^2 max(m2 / wsum - snh2 nobs / wsum^2, 0))`` with ``k`` = :func:`bias_scale` of the
    voxel size. The mesh's own terms are :func:`mesh_sigma_terms`."""
    W = np.asarray(wsum, dtype=np.float64)
    out = np.zeros(W.shape, dtype=np.float32)
    ok = W > 0
    Wk = W[ok]
    nb = np.maximum(np.asarray(nobs, np.float64)[ok], 1.0)
    noise = np.maximum(np.asarray(mxc, np.float64)[ok], 0.0) / Wk / np.maximum(np.asarray(nlook, np.float64)[ok], 1.0)
    gap = np.asarray(hnear, np.float64)[ok] / nb
    pred = np.asarray(snh2, np.float64)[ok] / (Wk * Wk) * nb
    excess = np.maximum(np.maximum(np.asarray(m2, np.float64)[ok], 0.0) / Wk - pred, 0.0)
    out[ok] = np.sqrt(C_NOISE ** 2 * noise + (A_GAP * bias_scale(voxel_mm) * gap) ** 2 + A_SCATTER ** 2 * excess)
    return out


def mesh_sigma_terms(V: np.ndarray, F: np.ndarray, N: np.ndarray, voxel_mm: float = 1.0) -> np.ndarray:
    """Per vertex, the variance (mm^2) of the two bias terms that only the mesh shows, with ``k`` =
    :func:`bias_scale` of the voxel size the mesh was made at:

    - curvature, ``(A_CURV k)^2 c``: ``1 - n . m`` with ``m`` the mean normal over the vertex, its
      neighbours and theirs (the 2-ring), and ``c`` its largest value over the same vertices;
    - the open border, ``(A_BORDER k exp(-d / (BORDER_MM k)))^2`` with ``d`` the distance (mm) to
      the nearest vertex of an edge that only one face uses.

    0 for a vertex no face uses. About a second per million triangles."""
    import scipy.sparse as sp
    from scipy.spatial import cKDTree

    n = len(V)
    F = np.asarray(F, dtype=np.int64).reshape(-1, 3)
    if n == 0 or len(F) == 0:
        return np.zeros(n)
    k = bias_scale(voxel_mm)
    a = np.concatenate([F[:, 0], F[:, 1], F[:, 2]])
    b = np.concatenate([F[:, 1], F[:, 2], F[:, 0]])
    # every edge once, with the number of faces at it
    E = sp.csr_matrix((np.ones(len(a), np.int32), (np.minimum(a, b), np.maximum(a, b))), shape=(n, n))
    E.sum_duplicates()
    A = (E + E.T).astype(np.float32)
    A.data[:] = 1.0
    A2 = A @ A  # its pattern: paths of two edges = the vertex itself, its neighbours and theirs
    A2.data[:] = 1.0
    Nn = np.asarray(N, dtype=np.float64)
    Nm = A2 @ Nn
    Nm /= np.maximum(np.linalg.norm(Nm, axis=1, keepdims=True), 1e-12)
    curv = 1.0 - np.clip((Nn * Nm).sum(1), -1.0, 1.0)
    ptr = A2.indptr
    has = ptr[1:] > ptr[:-1]
    curv2 = np.zeros(n)
    curv2[has] = np.maximum.reduceat(curv[A2.indices], ptr[:-1][has])
    out = (A_CURV * k) ** 2 * np.clip(curv2, 0.0, None)
    open_edge = E.data == 1
    if open_edge.any():
        rows = np.repeat(np.arange(n), np.diff(E.indptr))[open_edge]
        bv = np.unique(np.concatenate([rows, E.indices[open_edge]]))
        Vf = np.asarray(V, dtype=np.float64)
        d, _ = cKDTree(Vf[bv]).query(Vf, workers=-1)
        border = A_BORDER * k * np.exp(-d / (BORDER_MM * k))
        border[~has] = 0.0
        out += border * border
    return out


def gap_guard_mask(depth: np.ndarray, radius_px: float) -> np.ndarray:
    """The gap guard (module docstring): True for the pixels with depth (``depth`` > 0) that lie within
    ``radius_px`` (Euclidean) of a patch without depth. A pixel without depth is part of a patch when
    at least :data:`GUARD_SEED` of the 5x5 around it (the border repeated) have none. What the guard
    kernels compute, in numpy."""
    import cv2

    gap = ~(np.asarray(depth, dtype=np.float32) > 0)
    if radius_px <= 0:
        return np.zeros(gap.shape, dtype=bool)
    n = cv2.boxFilter(gap.astype(np.float32), -1, (5, 5), normalize=False, borderType=cv2.BORDER_REPLICATE)
    seed = gap & (n >= GUARD_SEED - 0.5)
    if not seed.any():
        return np.zeros(gap.shape, dtype=bool)
    d = cv2.distanceTransform((~seed).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    return ~gap & (np.rint(d * d) <= float(radius_px) ** 2)


def pixel_weights(depth: np.ndarray, sigma: np.ndarray | None, trunc_mm: float) -> tuple[np.ndarray, np.ndarray]:
    """Per pixel: the averaging weight (1 with depth, as Open3D's) and the variance it brings (mm^2),
    both 0 without depth. The variance is ``min(sigma, trunc)^2``, since truncation caps how far one
    observation can move a voxel; without sigmas it is ``trunc^2 / 9`` (somewhere in the band)."""
    d = np.asarray(depth, dtype=np.float32)
    valid = d > 0
    t = np.float32(trunc_mm)
    w = valid.astype(np.float32)
    if sigma is None:
        return w, np.where(valid, t * t / np.float32(9.0), np.float32(0.0)).astype(np.float32)
    s = np.minimum(np.nan_to_num(np.asarray(sigma, dtype=np.float32), nan=float(t), posinf=float(t)), t)
    return w, np.where(valid, s * s, np.float32(0.0)).astype(np.float32)


class ConfidenceTsdf:
    """The sparse confidence TSDF; see the module docstring.

    Parameters
    ----------
    K, image_size:
        Intrinsics and ``(width, height)`` of the depth maps.
    voxel_mm, trunc_mm, depth_min_mm, depth_max_mm:
        Grid, truncation and the depth range that is fused.
    backend:
        ``"auto"`` | ``"gpu"`` | ``"cpu"``; ``"gpu"`` without a GPU raises ``RuntimeError``.
    look_px:
        Pixel move that makes a new independent look (default: the dot spacing at this width).
    hole_factor:
        ``(amp, radius px)`` of the factor ``1 + amp exp(-(distance / radius)^2)`` that the matcher
        puts on the sigma of pixels next to missing depth, which is taken out again for the
        prediction of the scatter (default: the GPU matcher's, ``ohs.vision.depth.SIGMA_MODELS``).
    gap_guard_px:
        The gap guard's radius (module docstring); 0 (the default): off; ``None``: 8 px at 1280 px
        width, scaled with the width.
    initial_bricks, max_bricks:
        Pool size at the start, and its limit (bricks beyond it are dropped, with a warning).
    """

    def __init__(self, K: np.ndarray, image_size: tuple[int, int], voxel_mm: float = 1.0, trunc_mm: float = 4.0,
                 depth_min_mm: float = 120.0, depth_max_mm: float = 500.0, backend: str = "auto",
                 look_px: float | None = None, initial_bricks: int = 4096, max_bricks: int | None = None,
                 hole_factor: tuple[float, float] | None = None, gap_guard_px: float | None = 0.0) -> None:
        self.K = np.asarray(K, dtype=np.float64).reshape(3, 3).copy()
        self.image_size = (int(image_size[0]), int(image_size[1]))
        self.voxel_mm, self.trunc_mm = float(voxel_mm), float(trunc_mm)
        self.depth_min_mm, self.depth_max_mm = float(depth_min_mm), float(depth_max_mm)
        if not (self.voxel_mm > 0 and self.trunc_mm > 0 and 0 <= self.depth_min_mm < self.depth_max_mm):
            raise ValueError("voxel_mm and trunc_mm must be positive and depth_min_mm < depth_max_mm")
        if backend not in RECON_BACKENDS:
            raise ValueError(f"backend must be one of {RECON_BACKENDS}, got {backend!r}")
        w = self.image_size[0]
        self.look_px = float(look_px) if look_px is not None else LOOK_PX_1280 * w / 1280.0
        if hole_factor is None:
            from ohs.vision.depth import SIGMA_MODELS

            hole_factor = (SIGMA_MODELS["gpu"].hole_amp, SIGMA_MODELS["gpu"].hole_r_px)
        self.hole_amp, self.hole_r_px = float(hole_factor[0]), float(hole_factor[1])
        if not (self.hole_amp >= 0 and self.hole_r_px > 0):
            raise ValueError("hole_factor must be (amp >= 0, radius px > 0)")
        self.gap_near_px = GAP_NEAR_PX_1280 * w / 1280.0
        # the distance to missing depth is searched up to 3 radii of the factor (as the GPU matcher does)
        self._gap_r = int(max(np.ceil(3.0 * self.hole_r_px), np.ceil(self.gap_near_px)))
        self.gap_guard_px = GUARD_PX_1280 * w / 1280.0 if gap_guard_px is None else float(gap_guard_px)
        if not self.gap_guard_px >= 0:
            raise ValueError(f"gap_guard_px must be >= 0, got {gap_guard_px!r}")
        # brick sampling: every 4th pixel at 1280 px and 1 mm voxels; the same share of a brick at other sizes
        # (2 mm voxels at 640 px: every 4th pixel too, a quarter of the samples of every 2nd)
        self._step = max(1, int(round(4.0 * w / 1280.0 * float(self.voxel_mm))))
        self._initial = max(64, int(initial_bricks))
        self.dev: Any = None
        self.backend = "cpu"
        if backend in ("auto", "gpu"):
            from ohs import compute

            dev = compute.get_device()
            if dev is not None:
                self._init_gpu(dev)
            elif backend == "gpu":
                raise RuntimeError("ConfidenceTsdf backend \"gpu\" but no GPU is available (see ohs.compute)")
        if max_bricks is None:
            if self.dev is not None:  # each channel buffer must fit one allocation; keep half the memory free
                by_alloc = int(self.dev.max_alloc_mb * 2 ** 20 // (NVOX * 4))
                by_mem = int(self.dev.mem_mb * 2 ** 20 * 0.5 // (NVOX * 4 * (len(_FLOAT_CHANNELS) + 1)))
                max_bricks = max(self._initial, min(by_alloc, by_mem))
            else:
                max_bricks = 1 << 20
        self.max_bricks = int(max_bricks)
        L = np.stack(np.meshgrid(np.arange(BRICK), np.arange(BRICK), np.arange(BRICK), indexing="ij"), axis=-1)
        self._local = L[..., ::-1].reshape(NVOX, 3).astype(np.int64)  # (x, y, z) of voxel l = z * 64 + y * 8 + x
        self._rays: np.ndarray | None = None
        self._warned_full = False
        self.reset()
        log.info("confidence TSDF: %s, %.2f mm voxels, trunc %.1f mm, %.1f px per independent look",
                 self.device or "CPU (numpy)", self.voxel_mm, self.trunc_mm, self.look_px)

    # ------------------------------------------------------------------ backends
    def _init_gpu(self, dev: Any) -> None:
        cl = dev.cl
        self.dev, self.cl = dev, cl
        self.queue = dev.new_queue()
        prg = dev.program(_SRC)
        self._kern = cl.Kernel(prg, "recon_integrate")
        self._kern.set_scalar_arg_dtypes(_ARGS)
        lim = int(self._kern.get_work_group_info(cl.kernel_work_group_info.WORK_GROUP_SIZE, dev.device))
        self._wg = max(1, min(128, lim, int(dev.device.max_work_group_size)))
        self._bkern = cl.Kernel(prg, "recon_bricks")
        self._bkern.set_scalar_arg_dtypes(_BRICK_ARGS)
        self._gkern = (cl.Kernel(prg, "recon_gap_row"), cl.Kernel(prg, "recon_gap_dist"))
        self._gkern[0].set_scalar_arg_dtypes(_GAP_ROW_ARGS)
        self._gkern[1].set_scalar_arg_dtypes(_GAP_DIST_ARGS)
        self._guard_kern = (cl.Kernel(prg, "recon_guard_seed"), cl.Kernel(prg, "recon_guard_row"),
                            cl.Kernel(prg, "recon_guard_mark"))
        self._guard_kern[0].set_scalar_arg_dtypes(_GUARD_SEED_ARGS)
        self._guard_kern[1].set_scalar_arg_dtypes(_GUARD_ROW_ARGS)
        self._guard_kern[2].set_scalar_arg_dtypes(_GUARD_MARK_ARGS)
        self._img_bufs: Any = None
        self._list_cap = 0
        self._slot_buf = self._key_buf = None
        self._lists: Any = None  # (copy events, host arrays) of the last frame's brick lists, see _integrate_gpu
        self._bsearch: Any = None  # (n_max, table, out, count, shift) of the GPU brick search
        self._warned_range = False
        self.backend = "gpu"

    @property
    def device(self) -> str | None:
        """The GPU's name, or ``None`` on the CPU."""
        return None if self.dev is None else str(self.dev.name)

    @property
    def model_backend(self) -> str:
        return "confidence-gpu" if self.dev is not None else "confidence-cpu"

    @property
    def memory_mb(self) -> float:
        return self.capacity * NVOX * 4 * (len(_FLOAT_CHANNELS) + 1) / 2 ** 20

    # ------------------------------------------------------------------ storage
    def _wait(self) -> None:
        """Wait for the device. ``integrate`` returns with the frame's fusion still running (the queue
        is in order, so whatever is queued next comes after it); this is for what must not overtake it,
        such as releasing a buffer."""
        if self.dev is not None:
            self.queue.finish()
            self._lists = None

    def reset(self) -> None:
        """Forget everything (the pool shrinks back to its initial size)."""
        self._wait()
        self._enc_sorted = np.zeros(0, dtype=np.int64)
        self._slot_sorted = np.zeros(0, dtype=np.int64)
        self._origin: np.ndarray | None = None  # brick the GPU search's compact keys are relative to
        self.n_bricks = 0
        self.n_integrated = 0
        self.capacity = 0
        self._release_storage()
        self._alloc(self._initial)

    def _release_storage(self) -> None:
        if self.dev is not None:
            for b in getattr(self, "_bufs", {}).values():
                b.release()
        self._bufs: dict[str, Any] = {}

    def _alloc(self, cap: int) -> None:
        """(Re)allocate the pool for ``cap`` bricks, keeping the first ``n_bricks``."""
        n = self.n_bricks
        keys = np.zeros((cap, 3), dtype=np.int32)
        if n:
            keys[:n] = self._keys[:n]
        self._keys = keys
        if self.dev is None:
            new = {c: np.zeros(cap * NVOX, dtype=np.float32) for c in _FLOAT_CHANNELS}
            new["luv"] = np.zeros(cap * NVOX, dtype=np.uint32)
            for c, a in self._bufs.items():
                new[c][: n * NVOX] = a[: n * NVOX]
            self._bufs = new
        else:
            cl, q = self.cl, self.queue
            new = {}
            for c in (*_FLOAT_CHANNELS, "luv"):
                b = cl.Buffer(self.dev.ctx, cl.mem_flags.READ_WRITE, size=cap * NVOX * 4)
                cl.enqueue_fill_buffer(q, b, np.uint32(0), 0, cap * NVOX * 4)
                if n and c in self._bufs:
                    cl.enqueue_copy(q, b, self._bufs[c], byte_count=n * NVOX * 4)
                new[c] = b
            q.finish()
            self._release_storage()
            self._bufs = new
        self.capacity = cap

    def _slots_for(self, keys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Pool slots of brick coordinates ``keys`` (M,3), allocating the new ones: ``(slots, keys)``
        of those that have a slot (all, unless the pool is at ``max_bricks``). The index is a sorted
        array of encoded keys with their slots (``searchsorted``, no Python loop)."""
        enc = _encode(keys)
        se, ss = self._enc_sorted, self._slot_sorted
        slots = np.full(len(enc), -1, dtype=np.int64)
        if len(se) and len(enc):
            pos = np.minimum(np.searchsorted(se, enc), len(se) - 1)
            hit = se[pos] == enc
            slots[hit] = ss[pos[hit]]
        new = np.flatnonzero(slots < 0)
        if len(new):
            need = self.n_bricks + len(new)
            if need > self.capacity and self.capacity < self.max_bricks:
                cap = self.capacity
                while cap < need:
                    cap *= 2
                self._alloc(min(cap, self.max_bricks))
            room = self.capacity - self.n_bricks
            if room < len(new):
                if not self._warned_full:
                    log.warning("confidence TSDF: the pool is full (%d bricks, %.0f MB); new surface is not fused",
                                self.capacity, self.memory_mb)
                    self._warned_full = True
                new = new[:room]
            s0 = self.n_bricks
            fresh = np.arange(s0, s0 + len(new), dtype=np.int64)
            slots[new] = fresh
            self._keys[s0:s0 + len(new)] = keys[new]
            self.n_bricks = s0 + len(new)
            ne = enc[new]
            order = np.argsort(ne, kind="stable")
            at = np.searchsorted(se, ne[order])
            self._enc_sorted = np.insert(se, at, ne[order])
            self._slot_sorted = np.insert(ss, at, fresh[order])
        keep = slots >= 0
        return slots[keep].astype(np.int32), np.asarray(keys)[keep].astype(np.int32)

    def _frame_bricks(self, depth: np.ndarray, T_wc: np.ndarray) -> np.ndarray:
        """Brick coordinates (M,3) int64 that the frame's depth band reaches (module docstring)."""
        h, w = depth.shape
        s = self._step
        if self._rays is None or self._rays.shape[:2] != depth[s // 2::s, s // 2::s].shape:
            K = self.K
            vs, us = np.mgrid[s // 2:h:s, s // 2:w:s].astype(np.float64)
            y = (vs - K[1, 2]) / K[1, 1]
            x = (us - K[0, 2] - K[0, 1] * y) / K[0, 0]
            self._rays = np.stack([x, y, np.ones_like(x)], axis=-1).astype(np.float32)
        z = depth[s // 2::s, s // 2::s]
        ok = (z >= self.depth_min_mm) & (z <= self.depth_max_mm)  # also on raw depth: NaN compares false
        if not ok.any():
            return np.zeros((0, 3), dtype=np.int64)
        inv = np.float32(1.0 / (BRICK * self.voxel_mm))
        R = (T_wc[:3, :3] * inv).astype(np.float32)
        D = self._rays @ R.T  # the rays (z = 1) in world bricks, on the sample grid
        zz = np.where(ok, z, np.float32(0.0)).astype(np.float32)
        P0 = D * zz[..., None] + (T_wc[:3, 3] * inv).astype(np.float32)
        tr = np.float32(self.trunc_mm)
        parts = []
        for off in (-tr, np.float32(0.0), tr):
            enc = _encode(np.floor(P0 + off * D).reshape(-1, 3).astype(np.int64)).reshape(z.shape)
            # neighbouring samples mostly share a brick: drop those equal to the sample left of or
            # above them (when that one is valid) before the (sorting) unique; the set is the same
            dup = np.zeros(z.shape, dtype=bool)
            dup[:, 1:] = (enc[:, 1:] == enc[:, :-1]) & ok[:, :-1]
            dup[1:, :] |= (enc[1:, :] == enc[:-1, :]) & ok[:-1, :]
            parts.append(enc[ok & ~dup])
        return _decode(np.unique(np.concatenate(parts)))

    def _frame_bricks_gpu(self, dbuf: Any, T_wc: np.ndarray, shape: tuple[int, int]) -> np.ndarray | None:
        """:meth:`_frame_bricks` on the GPU, from the depth already in ``dbuf``: the same bricks, in the
        same (sorted) order. ``None`` when a brick lies more than 512 bricks from the origin brick (the
        first frame's camera; 4 m at 1 mm voxels): the caller then searches on the CPU."""
        cl, q = self.cl, self.queue
        h, w = shape
        s = self._step
        gw, gh = len(range(s // 2, w, s)), len(range(s // 2, h, s))
        n_max = 3 * gw * gh
        if n_max == 0:
            return np.zeros((0, 3), dtype=np.int64)
        inv = 1.0 / (BRICK * self.voxel_mm)
        if self._origin is None:
            self._origin = np.floor(T_wc[:3, 3] * inv).astype(np.int64)
        if self._bsearch is None or self._bsearch[0] != n_max:
            self._release_bsearch()
            shift = 32 - max(12, int(np.ceil(np.log2(2 * n_max))))  # table at most half full
            mf, ctx = cl.mem_flags, self.dev.ctx
            self._bsearch = (n_max, cl.Buffer(ctx, mf.READ_WRITE, size=(1 << (32 - shift)) * 4),
                             cl.Buffer(ctx, mf.READ_WRITE, size=n_max * 4), cl.Buffer(ctx, mf.READ_WRITE, size=8), shift)
        _, table, out, count, shift = self._bsearch
        cl.enqueue_fill_buffer(q, table, np.uint32(0xFFFFFFFF), 0, (1 << (32 - shift)) * 4)
        cl.enqueue_fill_buffer(q, count, np.int32(0), 0, 8)
        K = self.K
        R = T_wc[:3, :3] * inv
        t = T_wc[:3, 3] * inv - self._origin
        self._bkern(q, (gw, gh), None, dbuf, w, s, gw, gh, K[0, 0], K[1, 1], K[0, 2], K[1, 2], K[0, 1],
                    *R.ravel(), *t, self.trunc_mm, self.depth_min_mm, self.depth_max_mm, table, shift, out, count)
        cnt = np.zeros(2, dtype=np.int32)
        cl.enqueue_copy(q, cnt, count, is_blocking=True)
        if cnt[1] > 0:
            if not self._warned_range:
                log.info("confidence TSDF: the scan reaches beyond the GPU brick search's range; searching on the CPU")
                self._warned_range = True
            return None
        n = int(cnt[0])
        if n == 0:
            return np.zeros((0, 3), dtype=np.int64)
        k = np.empty(n, dtype=np.uint32)
        cl.enqueue_copy(q, k, out, is_blocking=True)  # the first n keys (the host array's size)
        k = np.sort(k).astype(np.int64)
        m = (1 << _KEY_BITS) - 1
        return np.stack([(k >> 2 * _KEY_BITS) & m, (k >> _KEY_BITS) & m, k & m], axis=1) - _KEY_HALF + self._origin

    def _release_bsearch(self) -> None:
        if getattr(self, "_bsearch", None) is not None:
            for bb in self._bsearch[1:4]:
                bb.release()
        self._bsearch = None

    # ------------------------------------------------------------------ integration
    def integrate(self, depth_mm: np.ndarray, sigma_mm: np.ndarray | None, T_wc: np.ndarray,
                  t: float | None = None) -> None:
        """Fuse a depth map (mm, 0 = invalid) with its standard deviation (mm; ``None``: unknown,
        taken as a third of the truncation) at pose ``T_wc``. ``t`` is accepted for the
        MarkerlessTracker interface and not used."""
        depth = np.ascontiguousarray(depth_mm, dtype=np.float32)
        w, h = self.image_size
        if depth.shape != (h, w):
            raise ValueError(f"depth has shape {depth.shape}, expected {(h, w)}")
        T = np.asarray(T_wc, dtype=np.float64).reshape(4, 4)
        R_cw = T[:3, :3].T
        t_cw = -R_cw @ T[:3, 3]
        if self.dev is not None:
            # the kernels check the raw depth and weigh each pixel themselves (the rules of pixel_weights),
            # which saves several full-image passes on the CPU and one upload; the bricks are searched on
            # the GPU too, and only the frame's brick list comes back
            sig = None if sigma_mm is None else np.ascontiguousarray(sigma_mm, dtype=np.float32)
            if sig is not None and sig.shape != depth.shape:
                raise ValueError(f"sigma has shape {sig.shape}, expected {depth.shape}")
            self._upload_images(depth, sig)
            bricks = self._frame_bricks_gpu(self._img_bufs[1], T, depth.shape)
            self._queue_gaps(depth.shape)  # after the search, whose result is waited for: these run meanwhile
            if bricks is None:
                bricks = self._frame_bricks(depth, T)
            slots, keys = self._slots_for(bricks)
            if len(slots):
                # the fusion is left running: the images are uploaded, and what reads the volume waits for it
                self._integrate_gpu(slots, keys, depth.shape, sig is not None, R_cw, t_cw)
        else:
            depth = np.where(np.isfinite(depth) & (depth >= self.depth_min_mm) & (depth <= self.depth_max_mm),
                             depth, np.float32(0.0)).astype(np.float32)
            wmap, vmap = pixel_weights(depth, sigma_mm, self.trunc_mm)
            slots, keys = self._slots_for(self._frame_bricks(depth, T))
            if len(slots):
                self._integrate_cpu(slots, keys, depth, wmap, sigma_mm, R_cw, t_cw)
        self.n_integrated += 1

    def _gap2(self, depth: np.ndarray) -> np.ndarray:
        """Squared distance (px) of every pixel to the nearest one without depth (``depth`` 0), at
        most ``(R + 1)^2``: what the two gap kernels compute, in numpy."""
        import cv2

        cap = np.float32(self._gap_r + 1)
        d = np.minimum(cv2.distanceTransform((depth > 0).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE), cap)
        return np.rint(d * d).astype(np.float32)

    def _cos_map(self, depth: np.ndarray) -> np.ndarray:
        """Cosine of each pixel's ray with the depth map's normal (the kernel's rule: 1 where a
        neighbour has no depth, and on the image border); ``depth`` 0 = none."""
        z = np.asarray(depth, np.float32)
        h, w = z.shape
        K = self.K
        cos = np.ones((h, w), np.float32)
        c = z[1:-1, 1:-1]
        dl, dr, dt, db = z[1:-1, :-2], z[1:-1, 2:], z[:-2, 1:-1], z[2:, 1:-1]
        ok = (c > 0) & (dl > 0) & (dr > 0) & (dt > 0) & (db > 0)
        zu, zv = np.float32(0.5) * (dr - dl), np.float32(0.5) * (db - dt)
        pu = np.arange(1, w - 1, dtype=np.float32)[None, :] - np.float32(K[0, 2])
        pv = np.arange(1, h - 1, dtype=np.float32)[:, None] - np.float32(K[1, 2])
        fx, fy = np.float32(K[0, 0]), np.float32(K[1, 1])
        n3 = pu * zu + pv * zv + c
        nn = np.sqrt((zu * fx) ** 2 + (zv * fy) ** 2 + n3 ** 2)
        rr = np.sqrt((pu / fx) ** 2 + (pv / fy) ** 2 + np.float32(1.0))
        cos[1:-1, 1:-1] = np.where(ok, np.clip(c / np.maximum(nn * rr, np.float32(1e-6)), 0.05, 1.0), 1.0)
        return cos

    def _integrate_cpu(self, slots: np.ndarray, keys: np.ndarray, depth: np.ndarray, wmap: np.ndarray,
                       sigma_mm: np.ndarray | None, R_cw: np.ndarray, t_cw: np.ndarray) -> None:
        h, w = depth.shape
        g2 = self._gap2(depth).ravel()
        guard = gap_guard_mask(depth, self.gap_guard_px)
        # for the count of observations next to missing depth, what the guard drops is missing too
        gg = self._gap2(np.where(guard, np.float32(0.0), depth)).ravel() if guard.any() else g2
        guard = guard.ravel()
        cmap = self._cos_map(depth).ravel()
        if sigma_mm is None:
            _, vmap = pixel_weights(depth, None, self.trunc_mm)
        else:  # the sigma without the matcher's factor next to missing depth; not finite: the truncation
            sg = np.asarray(sigma_mm, dtype=np.float32)
            f = np.float32(1.0) + np.float32(self.hole_amp) * np.exp(-g2 / np.float32(self.hole_r_px ** 2))
            with np.errstate(invalid="ignore"):
                snh = np.where(np.isfinite(sg).ravel(), sg.ravel() / f, np.float32(np.inf)).reshape(h, w)
            _, vmap = pixel_weights(depth, snh, self.trunc_mm)
        K = self.K
        P = ((keys.astype(np.int64)[:, None, :] * BRICK + self._local[None]) * self.voxel_mm).reshape(-1, 3)
        Pc = (P @ R_cw.T + t_cw).astype(np.float32)
        z = Pc[:, 2]
        ok = z > 1e-3
        iz = np.float32(1.0) / np.where(ok, z, np.float32(1.0))
        u = np.float32(K[0, 0]) * Pc[:, 0] * iz + np.float32(K[0, 1]) * Pc[:, 1] * iz + np.float32(K[0, 2])
        v = np.float32(K[1, 1]) * Pc[:, 1] * iz + np.float32(K[1, 2])
        ok &= (u >= -0.5) & (u < w - 0.5) & (v >= -0.5) & (v < h - 0.5)
        sel = np.flatnonzero(ok)
        ui = np.minimum(np.floor(u[sel] + 0.5).astype(np.int64), w - 1)
        vi = np.minimum(np.floor(v[sel] + 0.5).astype(np.int64), h - 1)
        pix = vi * w + ui
        d = depth.ravel()[pix]
        sdf = d - z[sel]
        wt = wmap.ravel()[pix]
        good = (d > 0) & (d <= self.depth_max_mm) & (sdf >= -self.trunc_mm) & (wt > 0) & ~guard[pix]
        sel, sdf, wt, ui, vi, pix = sel[good], sdf[good], wt[good], ui[good], vi[good], pix[good]
        a = (slots.astype(np.int64)[:, None] * NVOX + np.arange(NVOX)[None]).reshape(-1)[sel]
        b = self._bufs
        x = np.minimum(sdf, np.float32(self.trunc_mm))
        W0, m0 = b["wsum"][a], b["tsdf"][a]
        W1 = W0 + wt
        m1 = m0 + (wt / W1) * (x - m0)
        b["m2"][a] += wt * (x - m0) * (x - m1)
        b["snh2"][a] += wt * wt * vmap.ravel()[pix]
        xn = x * cmap[pix]
        q0 = b["xc"][a]
        q1 = q0 + (wt / W1) * (xn - q0)
        b["mxc"][a] += wt * (xn - q0) * (xn - q1)
        b["xc"][a] = q1
        b["hnear"][a] += (gg[pix] < np.float32(self.gap_near_px ** 2)).astype(np.float32)
        b["tsdf"][a] = m1
        b["wsum"][a] = W1
        b["nobs"][a] += 1.0
        uv = b["luv"][a]
        du = ui.astype(np.float32) - (uv & 0xFFFF).astype(np.float32)
        dv = vi.astype(np.float32) - (uv >> 16).astype(np.float32)
        look = (b["nlook"][a] == 0) | (du * du + dv * dv >= np.float32(self.look_px ** 2))
        al = a[look]
        b["nlook"][al] += 1.0
        b["luv"][al] = (ui[look].astype(np.uint32) | (vi[look].astype(np.uint32) << 16))

    def _upload_images(self, depth: np.ndarray, sigma: np.ndarray | None) -> tuple:
        """Upload the depth (and sigma) and wait for the copies (not holding the GIL), so the host arrays
        are the caller's again; returns them."""
        cl, q = self.cl, self.queue
        h, w = depth.shape
        if self._img_bufs is None or self._img_bufs[0] != (h, w):
            if self._img_bufs is not None:
                self._wait()
                for bb in self._img_bufs[1:]:
                    bb.release()
            # 1 depth, 2 sigma; 3 a row distance (scratch), 4 the squared distance to missing depth; the
            # gap guard: 5 its patches, then its mark (one byte a pixel), 6 the row distance to the
            # patches (scratch), 7 the squared distance to missing or dropped depth
            mf, ctx = cl.mem_flags, self.dev.ctx
            self._img_bufs = ((h, w), cl.Buffer(ctx, mf.READ_ONLY, size=h * w * 4),
                              cl.Buffer(ctx, mf.READ_ONLY, size=h * w * 4), cl.Buffer(ctx, mf.READ_WRITE, size=h * w * 4),
                              cl.Buffer(ctx, mf.READ_WRITE, size=h * w * 4), cl.Buffer(ctx, mf.READ_WRITE, size=h * w),
                              cl.Buffer(ctx, mf.READ_WRITE, size=h * w * 4), cl.Buffer(ctx, mf.READ_WRITE, size=h * w * 4))
        evs = [cl.enqueue_copy(q, self._img_bufs[1], depth, is_blocking=False)]
        if sigma is not None:
            evs.append(cl.enqueue_copy(q, self._img_bufs[2], sigma, is_blocking=False))
        cl.wait_for_events(evs)
        return depth, sigma

    def _queue_gaps(self, shape: tuple[int, int]) -> None:
        """Queue the kernels that measure, from the uploaded depth, every pixel's squared distance to
        the nearest one without depth (into ``_img_bufs[4]``; see :meth:`_gap2`) and, with the gap
        guard, to the nearest one without depth or dropped by the guard (into ``_img_bufs[7]``: 0
        at a pixel with depth says the guard dropped it, :func:`gap_guard_mask`)."""
        h, w = shape
        q = self.queue
        _, dbuf, _, hx, g2, mark, hs, gg = self._img_bufs
        dmin, dmax, r = self.depth_min_mm, self.depth_max_mm, self._gap_r
        self._gkern[0](q, (w, h), None, dbuf, hx, w, h, r, dmin, dmax, mark, 0)
        self._gkern[1](q, (w, h), None, hx, g2, w, h, r)
        if self.gap_guard_px > 0:
            rg = int(np.ceil(self.gap_guard_px))
            self._guard_kern[0](q, (w, h), None, dbuf, mark, w, h, dmin, dmax, GUARD_SEED)  # the patches
            self._guard_kern[1](q, (w, h), None, mark, hs, w, h, rg)
            self._guard_kern[2](q, (w, h), None, hs, mark, w, h, rg, self.gap_guard_px ** 2)  # now the mark
            self._gkern[0](q, (w, h), None, dbuf, hs, w, h, r, dmin, dmax, mark, 1)
            self._gkern[1](q, (w, h), None, hs, gg, w, h, r)

    def _integrate_gpu(self, slots: np.ndarray, keys: np.ndarray, shape: tuple[int, int], has_sigma: bool,
                       R_cw: np.ndarray, t_cw: np.ndarray) -> None:
        """Queue the integration of the uploaded images into bricks ``keys`` at pool ``slots``, and
        return without waiting: the device may be busy with other work (the tracker's volume shares
        it), and the caller has its own to do meanwhile. The two lists are host arrays that must
        outlive their copies, so they are kept in ``_lists`` until the next frame (or ``_wait``)."""
        cl, q = self.cl, self.queue
        h, w = shape
        mf = cl.mem_flags
        _, dbuf, sbuf, _, gbuf, _, _, ggbuf = self._img_bufs
        if not self.gap_guard_px > 0:
            ggbuf = gbuf  # no guard: the one distance serves both
        m = len(slots)
        if self._lists is not None:
            cl.wait_for_events(self._lists[0])  # done long ago, as a rule (and the GIL is not held)
            self._lists = None
        if m > self._list_cap:
            self._wait()
            for bb in (self._slot_buf, self._key_buf):
                if bb is not None:
                    bb.release()
            self._list_cap = max(m, 2 * self._list_cap, 1024)
            self._slot_buf = cl.Buffer(self.dev.ctx, mf.READ_ONLY, size=self._list_cap * 4)
            self._key_buf = cl.Buffer(self.dev.ctx, mf.READ_ONLY, size=self._list_cap * 12)
        hold = (np.ascontiguousarray(slots, np.int32), np.ascontiguousarray(keys, np.int32))
        evs = [cl.enqueue_copy(q, self._slot_buf, hold[0], is_blocking=False),
               cl.enqueue_copy(q, self._key_buf, hold[1], is_blocking=False)]
        K = self.K
        b = self._bufs
        n = m * NVOX
        gs = (n + self._wg - 1) // self._wg * self._wg
        self._kern(q, (gs,), (self._wg,), b["tsdf"], b["wsum"], b["m2"], b["nobs"], b["nlook"], b["luv"],
                   b["xc"], b["mxc"], b["hnear"], b["snh2"],
                   self._slot_buf, self._key_buf, m, dbuf, sbuf, int(has_sigma), w, h, gbuf, ggbuf,
                   K[0, 0], K[1, 1], K[0, 2], K[1, 2], K[0, 1], *R_cw.ravel(), *t_cw,
                   self.voxel_mm, self.trunc_mm, self.depth_min_mm, self.depth_max_mm, self.look_px ** 2,
                   self.gap_near_px ** 2, self.hole_amp, self.hole_r_px ** 2)
        q.flush()  # the device starts as soon as it is free
        self._lists = (evs, hold)

    # ------------------------------------------------------------------ results
    def download(self) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        """``(keys (M,3) int32, {channel: (M, 8, 8, 8)})`` of the allocated bricks, indexed ``[z, y, x]``."""
        n = self.n_bricks
        self._wait()  # the last frame's fusion
        out: dict[str, np.ndarray] = {}
        for c in (*_FLOAT_CHANNELS, "luv"):
            if self.dev is None:
                a = self._bufs[c][: n * NVOX].copy()
            else:
                a = np.empty(n * NVOX, dtype=np.uint32 if c == "luv" else np.float32)
                if n:
                    self.cl.enqueue_copy(self.queue, a, self._bufs[c], is_blocking=True)
            out[c] = a.reshape(n, BRICK, BRICK, BRICK)
        return self._keys[:n].copy(), out

    def to_open3d(self, o3d: Any, o3c: Any) -> Any:
        """A CPU ``VoxelBlockGrid`` (block resolution 8): ``tsdf`` / trunc, ``weight`` = observations,
        ``color`` = ``(sigma mm, independent looks, weight sum)``; ``None`` when empty."""
        keys, ch = self.download()
        if len(keys) == 0:
            return None
        sig = voxel_sigma(ch["wsum"], ch["m2"], ch["nobs"], ch["nlook"], ch["mxc"], ch["hnear"], ch["snh2"],
                          voxel_mm=self.voxel_mm)
        vbg = o3d.t.geometry.VoxelBlockGrid(
            attr_names=("tsdf", "weight", "color"), attr_dtypes=(o3c.float32, o3c.float32, o3c.float32),
            attr_channels=((1), (1), (3)), voxel_size=self.voxel_mm, block_resolution=BRICK,
            block_count=int(len(keys) + 64), device=o3c.Device("CPU:0"))
        buf, _ = vbg.hashmap().activate(o3c.Tensor(np.ascontiguousarray(keys, np.int32)))
        idx = buf.cpu().numpy().astype(np.int64)
        vbg.attribute("tsdf").numpy()[idx] = np.clip(ch["tsdf"] / np.float32(self.trunc_mm), -1.0, 1.0)[..., None]
        vbg.attribute("weight").numpy()[idx] = ch["nobs"][..., None]
        vbg.attribute("color").numpy()[idx] = np.stack([sig, ch["nlook"], ch["wsum"]], axis=-1)
        return vbg

    def _threshold(self, weight_threshold: float | None) -> float:
        thr = 3.0 if weight_threshold is None else float(weight_threshold)
        # as MarkerlessTracker: Open3D keeps voxels whose weight is strictly above the threshold
        return float(min(thr, max(self.n_integrated - 1, 0)))

    def extract_mesh(self, weight_threshold: float | None = None, with_sigma: bool = False) -> tuple:
        """Marching-cubes mesh ``(V (N,3) mm, F (M,3) int64, N (N,3))``, plus ``sigma`` (N,) mm per
        vertex with ``with_sigma``: the voxels' sigma and the mesh's own terms (:func:`mesh_sigma_terms`)."""
        import open3d as o3d
        import open3d.core as o3c

        empty = (np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), np.zeros((0, 3)))
        vbg = self.to_open3d(o3d, o3c) if self.n_integrated else None
        mesh = None if vbg is None else vbg.extract_triangle_mesh(weight_threshold=self._threshold(weight_threshold))
        if mesh is None or len(mesh.vertex.positions) == 0:
            return (*empty, np.zeros(0, np.float32)) if with_sigma else empty
        V = mesh.vertex.positions.cpu().numpy().astype(np.float64)
        F = mesh.triangle.indices.cpu().numpy().astype(np.int64)
        N = mesh.vertex.normals.cpu().numpy().astype(np.float64)
        if not with_sigma:
            return V, F, N
        s = (mesh.vertex.colors.cpu().numpy()[:, 0] * np.float32(255.0)).astype(np.float64)
        return V, F, N, np.sqrt(s * s + mesh_sigma_terms(V, F, N, voxel_mm=self.voxel_mm)).astype(np.float32)

    def extract_points(self, weight_threshold: float | None = None, with_sigma: bool = False) -> tuple:
        """Zero-crossing points ``(P (N,3) mm, N (N,3))``, plus ``sigma`` (N,) mm with ``with_sigma``
        (the voxels' sigma only: points have no mesh to take curvature and border from)."""
        import open3d as o3d
        import open3d.core as o3c

        empty = (np.zeros((0, 3)), np.zeros((0, 3)))
        vbg = self.to_open3d(o3d, o3c) if self.n_integrated else None
        pcd = None if vbg is None else vbg.extract_point_cloud(weight_threshold=self._threshold(weight_threshold))
        if pcd is None or pcd.is_empty():
            return (*empty, np.zeros(0, np.float32)) if with_sigma else empty
        P = pcd.point.positions.cpu().numpy().astype(np.float64)
        N = pcd.point.normals.cpu().numpy().astype(np.float64)
        if not with_sigma:
            return P, N
        return P, N, (pcd.point.colors.cpu().numpy()[:, 0] * np.float32(255.0)).astype(np.float32)

    def close(self) -> None:
        """Release the GPU buffers (the volume is unusable afterwards)."""
        self._wait()
        self._release_storage()
        if self.dev is not None and self._img_bufs is not None:
            for bb in self._img_bufs[1:]:
                bb.release()
            self._img_bufs = None
        for bb in (getattr(self, "_slot_buf", None), getattr(self, "_key_buf", None)):
            if bb is not None:
                bb.release()
        self._slot_buf = self._key_buf = None
        self._release_bsearch()
