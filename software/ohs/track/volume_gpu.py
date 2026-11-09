"""Dense rolling TSDF volume on the GPU for the markerless *tracking* model (OpenCL, M3 phase 1).

:class:`GpuTsdfVolume` replaces Open3D's sparse ``VoxelBlockGrid`` inside
:class:`ohs.track.markerless.MarkerlessTracker` when ``FusionParams.model_backend`` resolves to
``"gpu"`` (``ohs.compute.get_device()`` found a GPU). It only serves tracking: frame-to-model ICP
needs the surface near the current view, so the volume is a cube that follows the scanner. The
reconstruction TSDF (the mesh) stays Open3D. One OpenCL C 1.2 source runs on NVIDIA, AMD and Intel.

**Grid and storage.** A dense cube of ``edge`` voxels per axis (default 384: 768 mm at the 2 mm
tracking voxel), stored as 16^3 bricks. One 32-bit word per voxel: the TSDF as int16 (the
truncated SDF divided by the truncation distance, x 32767: a quantum is 0.24 um at 8 mm
truncation) and the integration weight as uint16 (saturating at ``max_weight``, by default
65535: Open3D's weights are not capped either). 384^3 voxels are 226 MB, 512^3 are 537 MB; the
edge is reduced (512 -> 384 -> 256 ...) until it fits ``min(max_alloc, global memory / 4)``.
A weighted running mean with a rounded int16 store can lag the exact mean by at most
``(weight + 1) / 2`` quanta (5 um after 36 frames, 0.12 mm only after ~1000 frames of the same
voxel).

**Frames and units.** Millimetres everywhere. The voxel with global integer index ``g`` sits at
world position ``g * voxel_mm`` (Open3D's convention, so a brick is an Open3D block of 16^3
voxels). The window covers global voxels ``[origin, origin + edge)`` per axis, ``origin`` a
multiple of 16. Poses are ``T_wc`` (camera to world); the camera follows OpenCV (x right, y
down, z forward); intrinsics use the pixel-centre convention (pixel ``(u, v)`` is centred at
integer coordinates).

**Rolling.** Storage is toroidal at brick granularity: global brick ``b`` lives in slot
``b mod (edge / 16)``. At every integration the window is re-centred on the point
``center_depth_mm`` (default: the middle of the depth range) in front of the camera, per axis and
only when that axis is off by at least ``edge / 128`` bricks (96 mm at the defaults: a
hysteresis, so jitter never drops data). A shift moves no data: the bricks that left the window
are cleared and reused for the ones that entered. The surface near the view is kept; what left the
window is forgotten (the tracking model is local; loop closure is a separate concern). On the
simulated scans the whole tracking depth stays inside the 768 mm cube.

**Integration** (:meth:`GpuTsdfVolume.integrate`) follows Open3D's ``VoxelBlockGrid.integrate``:
every voxel is projected into the depth map (nearest pixel, the rounding the tracker gets from
Open3D with its +0.5 px intrinsics shift), ``sdf = depth - z_voxel`` (projective, along the
camera z), voxels with ``sdf < -trunc`` or an invalid / too far pixel are skipped, the TSDF is
``min(sdf, trunc) / trunc`` and is averaged with weight +1. Unlike Open3D, which only touches
blocks within the truncation band of the current depth, free space in front of the surface is
integrated too (KinectFusion style); band values are identical, and limiting the update to the
band made no measurable difference to tracking. Only the bounding box of the camera frustum up to
the farthest valid depth + truncation is visited. The depth is uploaded once per frame; the
kernel runs asynchronously (the next ray cast is queued behind it).

**Ray casting** (:meth:`GpuTsdfVolume.raycast_start`) at the tracker's ray-cast resolution, from
``depth_min`` to ``depth_max`` (camera z), clipped to the window. Every march sample is a
*trilinear* TSDF lookup over the observed corners (a sample counts as observed when they carry at
least half of the trilinear weight); the step is Open3D's ``max(tsdf * trunc, voxel)``
(``trunc / 2`` through unobserved space). A crossing from an observed positive sample to an
observed sample ``<= 0`` is located with the Illinois (modified regula falsi) method on the
trilinear field to 1e-4 voxel. The hit is accepted when (i) its observed corners carry at least
half of the trilinear weight, (ii) the nearest observed corner was integrated at least
``weight_threshold`` times (Open3D tests one voxel's weight), (iii) with all 8 corners observed,
the field clearly decreases along the ray (``dTSDF/dt < -0.2 / trunc``, as the Open3D path's
Newton refinement), and (iv) the observed surface extends at least ``border_mm`` around the hit:
8 probes on a circle of that radius in the tangent plane must be observed like march samples
(so isolated unobserved voxels, e.g. single invalid pixels of a young model, are no border).
Otherwise the march goes on, as in Open3D. Normals are Open3D's: the trilinear
interpolation of the central-difference gradients at the corners (one-sided next to unobserved
voxels), rotated into the camera frame, unit length, pointing towards the camera; then
box-averaged over ``normal_window`` pixels (validity weighted, renormalised), exactly as
``MarkerlessTracker._smooth_normals``. Only the maps at the ray-cast resolution are downloaded,
through a pinned staging buffer: one ``float4`` per pixel (depth along the camera z, normal),
0 = no hit.

**Why the border test and the weight threshold.** The trilinear ray cast reaches all of the
observed surface, about 30 % more pixels than Open3D's ray cast, whose nearest-voxel march and
refinement gate drop most hits near the borders of what was observed. Those borders (and the
voxels fused only a few times) are where the stereo depth has camera-fixed biases (on the
simulator, e.g. SGBM +1-2.6 mm along the lower edge of the valid depth), and frame-to-model ICP
turns camera-fixed errors into drift: without the tests the 640x400 sphere scan drifted 0.25 deg
(Open3D: 0.06), while the same GPU maps restricted to Open3D's pixels tracked better than
Open3D. The tracker's defaults (``FusionParams.gpu_border_mm`` 8 mm, ``gpu_weight_threshold`` 8)
keep about 5 % more pixels than Open3D. Over 15 simulated scans (3 scenes x seeds, 640x400 and
1280x800, SGBM and GPU depth) they track better than the Open3D model on the median (max
translation / rotation error 0.70-0.78 / 0.68-0.70 of Open3D's), in 11-12 scans of 15 on both,
and without the 10-150 mm slides Open3D shows on 3-4 of them. Without the border test, or with
Open3D's threshold of 3 frames, it was clearly worse; 6 mm traded a little rotation for
translation.

**Accuracy of the maps.** Against the analytic surface the GPU maps are unbiased and closer than
Open3D's refined maps (synthetic sphere and planes: median depth error 0.03-0.04 mm vs
0.05-0.06 mm; plane normals 0.12 vs 0.19 deg; ``tests/test_track_volume_gpu.py``).

Not thread-safe: one volume per tracker, used from one thread (its own command queue).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

__all__ = ["GpuTsdfVolume", "PendingRaycast", "plan_edge", "BRICK", "EDGE_CHOICES"]

log = logging.getLogger(__name__)

BRICK = 16  # voxels per brick edge (= an Open3D block of block_resolution 16)
EDGE_CHOICES = (768, 640, 512, 384, 320, 256, 192, 128, 96, 64)  # fall-back ladder (multiples of 16)
_QS = 32767.0  # int16 TSDF scale

_SRC = r"""
// OpenCL C 1.2. Dense rolling TSDF of ohs/track/volume_gpu.py (see its docstring).
// Voxel word: bits 0-15 TSDF (int16, tsdf * 32767), bits 16-31 weight (uint16).
// Storage: 16^3 bricks, brick slot (bz * NB + by) * NB + bx, voxel (lz * 16 + ly) * 16 + lx.
// Local voxel coordinates (0 .. N-1, relative to the window origin) map to slot
// ((x >> 4) + bm) mod NB per axis, bm = (origin brick) mod NB.

#define QS 32767.0f

inline int wrapb(int b, int NB) { return b >= NB ? b - NB : b; }

inline int vaddr(int x, int y, int z, int NB, int bmx, int bmy, int bmz)
{
    const int bx = wrapb((x >> 4) + bmx, NB);
    const int by = wrapb((y >> 4) + bmy, NB);
    const int bz = wrapb((z >> 4) + bmz, NB);
    return (((bz * NB + by) * NB + bx) << 12) | ((z & 15) << 8) | ((y & 15) << 4) | (x & 15);
}

inline float vt(uint v) { return (float)as_short((ushort)(v & 0xFFFFu)) * (1.0f / QS); }

// ---------------------------------------------------------------- integration
// One work-item per (x, y) voxel column of the sub-box, looping over z.
// Camera coordinates (mm) of local voxel (x, y, z): A (x, y, z)^T + b, A = R_cw * voxel size.
__kernel void ohs_tsdf_integrate(
    __global uint *vol, const int NB, const int bmx, const int bmy, const int bmz,
    const int x0, const int y0, const int z0, const int nx, const int ny, const int nz,
    __global const float *depth, const int w, const int h,
    const float fx, const float fy, const float cx, const float cy, const float sk,
    const float a00, const float a01, const float a02,
    const float a10, const float a11, const float a12,
    const float a20, const float a21, const float a22,
    const float b0, const float b1, const float b2,
    const float trunc, const float depth_max, const int max_weight)
{
    const int i = get_global_id(0), j = get_global_id(1);
    if (i >= nx || j >= ny) return;
    const int x = x0 + i, y = y0 + j;
    const float fxl = (float)x, fyl = (float)y;
    const float px = a00 * fxl + a01 * fyl + b0;
    const float py = a10 * fxl + a11 * fyl + b1;
    const float pz = a20 * fxl + a21 * fyl + b2;
    const float inv_trunc = 1.0f / trunc;
    const float umax = (float)w - 0.5f, vmax = (float)h - 0.5f;
    for (int k = 0; k < nz; ++k) {
        const int z = z0 + k;
        const float fz = (float)z;
        const float zc = a22 * fz + pz;
        if (zc <= 1e-3f) continue;
        const float iz = 1.0f / zc;
        const float xn = (a02 * fz + px) * iz, yn = (a12 * fz + py) * iz;
        const float u = fx * xn + sk * yn + cx;
        const float v = fy * yn + cy;
        if (!(u >= -0.5f && u < umax && v >= -0.5f && v < vmax)) continue;
        const int ui = min((int)floor(u + 0.5f), w - 1), vi = min((int)floor(v + 0.5f), h - 1);
        const float d = depth[vi * w + ui];
        if (!(d > 0.0f) || d > depth_max) continue;
        const float sdf = d - zc;
        if (sdf < -trunc) continue;
        const float ts = fmin(sdf, trunc) * inv_trunc;
        const int a = vaddr(x, y, z, NB, bmx, bmy, bmz);
        const uint old = vol[a];
        const int wo = (int)(old >> 16);
        const int we = min(wo, max_weight - 1);
        const float tn = (vt(old) * (float)we + ts) / (float)(we + 1);
        const int wn = min(wo + 1, max_weight);
        vol[a] = ((uint)wn << 16) | (uint)as_ushort(convert_short_sat_rte(tn * QS));
    }
}

// ---------------------------------------------------------------- ray casting
typedef struct { float f; float rs; } tsamp;

// Trilinear TSDF at local voxel coordinates q over the observed corners (weight > 0):
// f = sum(r v) / sum(r) over them, rs = sum(r) (1 when all 8 are observed).
inline tsamp tri(__global const uint *vol, float qx, float qy, float qz, int NB, int bmx, int bmy, int bmz)
{
    const float flx = floor(qx), fly = floor(qy), flz = floor(qz);
    const int x = (int)flx, y = (int)fly, z = (int)flz;
    const float rx = qx - flx, ry = qy - fly, rz = qz - flz;
    const int bx0 = wrapb((x >> 4) + bmx, NB), bx1 = wrapb(((x + 1) >> 4) + bmx, NB);
    const int by0 = wrapb((y >> 4) + bmy, NB), by1 = wrapb(((y + 1) >> 4) + bmy, NB);
    const int bz0 = wrapb((z >> 4) + bmz, NB), bz1 = wrapb(((z + 1) >> 4) + bmz, NB);
    const int lx0 = x & 15, lx1 = (x + 1) & 15, ly0 = y & 15, ly1 = (y + 1) & 15;
    const int lz0 = z & 15, lz1 = (z + 1) & 15;
    tsamp s;
    s.f = 0.0f; s.rs = 0.0f;
#pragma unroll
    for (int c = 0; c < 8; ++c) {
        const int dx = c & 1, dy = (c >> 1) & 1, dz = c >> 2;
        const int a = ((((dz ? bz1 : bz0) * NB + (dy ? by1 : by0)) * NB + (dx ? bx1 : bx0)) << 12)
                      | ((dz ? lz1 : lz0) << 8) | ((dy ? ly1 : ly0) << 4) | (dx ? lx1 : lx0);
        const uint v = vol[a];
        if (v >> 16) {
            const float r = (dx ? rx : 1.0f - rx) * (dy ? ry : 1.0f - ry) * (dz ? rz : 1.0f - rz);
            s.f += r * vt(v);
            s.rs += r;
        }
    }
    if (s.rs > 0.0f) s.f /= s.rs;
    return s;
}

// Gradient component of the TSDF at voxel c along one axis (offset e), over two voxels: central
// difference when both neighbours are observed, else one-sided (doubled), else 0.
inline float axis_diff(__global const uint *vol, int x, int y, int z, int ex, int ey, int ez, float vc,
                       int NB, int bmx, int bmy, int bmz)
{
    const uint p = vol[vaddr(x + ex, y + ey, z + ez, NB, bmx, bmy, bmz)];
    const uint m = vol[vaddr(x - ex, y - ey, z - ez, NB, bmx, bmy, bmz)];
    const int po = (p >> 16) != 0u, mo = (m >> 16) != 0u;
    if (po && mo) return vt(p) - vt(m);
    if (po) return 2.0f * (vt(p) - vc);
    if (mo) return 2.0f * (vc - vt(m));
    return 0.0f;
}

inline int clip_slab(float o, float d, float lo, float hi, float *t0, float *t1)
{
    if (fabs(d) < 1e-12f) return (o >= lo && o <= hi);
    float ta = (lo - o) / d, tb = (hi - o) / d;
    if (ta > tb) { const float tmp = ta; ta = tb; tb = tmp; }
    *t0 = fmax(*t0, ta);
    *t1 = fmin(*t1, tb);
    return *t0 <= *t1;
}

// Accept a refined hit at q (rules (i)-(iv) of the module docstring) and write the camera-frame
// unit normal (towards the camera). border: probe radius in voxels (0 = no border test).
inline int finish_hit(__global const uint *vol, float qx, float qy, float qz, int N, int NB, int bmx, int bmy, int bmz,
                      float Dx, float Dy, float Dz, float wthr, float min_slope, float border,
                      float m00, float m01, float m02, float m10, float m11, float m12,
                      float m20, float m21, float m22, float *nx, float *ny, float *nz)
{
    const float flx = floor(qx), fly = floor(qy), flz = floor(qz);
    const int x = (int)flx, y = (int)fly, z = (int)flz;
    const float rx = qx - flx, ry = qy - fly, rz = qz - flz;
    const float gx = 1.0f - rx, gy = 1.0f - ry, gz = 1.0f - rz;
    float v[8];
    int obs[8];
    float rs = 0.0f, rbest = -1.0f, wbest = 0.0f;
    int n = 0;
#pragma unroll
    for (int c = 0; c < 8; ++c) {
        const int dx = c & 1, dy = (c >> 1) & 1, dz = c >> 2;
        const uint u = vol[vaddr(x + dx, y + dy, z + dz, NB, bmx, bmy, bmz)];
        const float r = (dx ? rx : gx) * (dy ? ry : gy) * (dz ? rz : gz);
        obs[c] = (u >> 16) != 0u;
        v[c] = vt(u);
        if (obs[c]) {
            rs += r;
            ++n;
            if (r > rbest) { rbest = r; wbest = (float)(u >> 16); }
        }
    }
    if (rs < 0.5f || wbest < wthr) return 0;                                   // (i), (ii)
    if (n == 8) {                                                              // (iii)
        // analytic gradient of the trilinear interpolant (per voxel), along the ray (per mm of depth)
        const float ddx = gy * gz * (v[1] - v[0]) + ry * gz * (v[3] - v[2]) + gy * rz * (v[5] - v[4]) + ry * rz * (v[7] - v[6]);
        const float ddy = gx * gz * (v[2] - v[0]) + rx * gz * (v[3] - v[1]) + gx * rz * (v[6] - v[4]) + rx * rz * (v[7] - v[5]);
        const float ddz = gx * gy * (v[4] - v[0]) + rx * gy * (v[5] - v[1]) + gx * ry * (v[6] - v[2]) + rx * ry * (v[7] - v[3]);
        if (ddx * Dx + ddy * Dy + ddz * Dz >= -min_slope) return 0;
    }
    // normal (as Open3D): trilinear interpolation of the corner gradients, over the observed corners
    float sx = 0.0f, sy = 0.0f, sz = 0.0f;
    for (int c = 0; c < 8; ++c) {
        if (!obs[c]) continue;
        const int cx = x + (c & 1), cy = y + ((c >> 1) & 1), cz = z + (c >> 2);
        const float r = ((c & 1) ? rx : gx) * (((c >> 1) & 1) ? ry : gy) * ((c >> 2) ? rz : gz);
        sx += r * axis_diff(vol, cx, cy, cz, 1, 0, 0, v[c], NB, bmx, bmy, bmz);
        sy += r * axis_diff(vol, cx, cy, cz, 0, 1, 0, v[c], NB, bmx, bmy, bmz);
        sz += r * axis_diff(vol, cx, cy, cz, 0, 0, 1, v[c], NB, bmx, bmy, bmz);
    }
    const float gn = sqrt(sx * sx + sy * sy + sz * sz);
    if (!(gn > 1e-12f)) return 0;
    if (border > 0.0f) {                                                       // (iv)
        // probes on a circle of radius border (voxels) in the tangent plane must be observed
        // (as march samples: observed corners carry >= half the trilinear weight, so isolated
        // unobserved voxels, e.g. single invalid depth pixels of one frame, do not count as a border)
        const float ux = sx / gn, uy = sy / gn, uz = sz / gn;
        const int alt = fabs(ux) > 0.9f;
        float t1x = alt ? -uz : 0.0f, t1y = alt ? 0.0f : uz, t1z = alt ? ux : -uy;  // n x (e_x or e_y)
        const float t1n = sqrt(t1x * t1x + t1y * t1y + t1z * t1z);
        t1x /= t1n; t1y /= t1n; t1z /= t1n;
        const float t2x = uy * t1z - uz * t1y, t2y = uz * t1x - ux * t1z, t2z = ux * t1y - uy * t1x;
        const float lim = (float)(N - 2);
        for (int k = 0; k < 8; ++k) {
            const float a = 0.785398163f * (float)k;
            const float ca = cos(a) * border, sa = sin(a) * border;
            const float bx_ = qx + ca * t1x + sa * t2x, by_ = qy + ca * t1y + sa * t2y, bz_ = qz + ca * t1z + sa * t2z;
            if (!(bx_ >= 0.0f && by_ >= 0.0f && bz_ >= 0.0f && bx_ < lim && by_ < lim && bz_ < lim)) return 0;
            if (tri(vol, bx_, by_, bz_, NB, bmx, bmy, bmz).rs < 0.5f) return 0;
        }
    }
    // world -> camera: n_c = R_wc^T n_w (M = R_wc / voxel; the scale cancels on normalising)
    const float cxn = m00 * sx + m10 * sy + m20 * sz;
    const float cyn = m01 * sx + m11 * sy + m21 * sz;
    const float czn = m02 * sx + m12 * sy + m22 * sz;
    const float nn = sqrt(cxn * cxn + cyn * cyn + czn * czn);
    if (!(nn > 1e-12f)) return 0;
    *nx = cxn / nn; *ny = cyn / nn; *nz = czn / nn;
    return 1;
}

// One work-item per pixel. Ray: q(t) = o + t D (local voxel units), t = depth along the camera z.
// M = R_wc / voxel size (row-major), o = camera centre in local voxel units.
__kernel void ohs_tsdf_raycast(
    __global const uint *vol, const int N, const int NB, const int bmx, const int bmy, const int bmz,
    const int w, const int h,
    const float fx, const float fy, const float cx, const float cy, const float sk,
    const float m00, const float m01, const float m02,
    const float m10, const float m11, const float m12,
    const float m20, const float m21, const float m22,
    const float ox, const float oy, const float oz,
    const float t_near, const float t_far, const float trunc, const float min_step, const float unobs_step,
    const float wthr, const float min_slope, const float tol, const float border,
    __global float4 *out)
{
    const int px = get_global_id(0), py = get_global_id(1);
    if (px >= w || py >= h) return;
    const int pix = py * w + px;
    out[pix] = (float4)(0.0f, 0.0f, 0.0f, 0.0f);
    const float yn = ((float)py - cy) / fy;
    const float xn = ((float)px - cx - sk * yn) / fx;
    const float Dx = m00 * xn + m01 * yn + m02;
    const float Dy = m10 * xn + m11 * yn + m12;
    const float Dz = m20 * xn + m21 * yn + m22;
    float t0 = t_near, t1 = t_far;
    // keep every lookup inside the window: trilinear cell [floor q, floor q + 1] and the
    // central differences of the normal (floor q - 1 .. floor q + 2)
    const float lo = 1.0f, hi = (float)(N - 2) - 1e-3f;
    if (!clip_slab(ox, Dx, lo, hi, &t0, &t1)) return;
    if (!clip_slab(oy, Dy, lo, hi, &t0, &t1)) return;
    if (!clip_slab(oz, Dz, lo, hi, &t0, &t1)) return;
    float t = t0, tp = t0, fp = 0.0f;
    int prev = 0;
    for (int it = 0; it < 16384 && t <= t1; ++it) {
        const tsamp s = tri(vol, ox + t * Dx, oy + t * Dy, oz + t * Dz, NB, bmx, bmy, bmz);
        if (s.rs < 0.5f) {  // (mostly) unobserved: no crossing can start or end here
            prev = 0;
            t += unobs_step;
            continue;
        }
        if (prev && fp > 0.0f && s.f <= 0.0f) {
            // Illinois method on the trilinear field inside [tp, t]
            float a = tp, fa = fp, b = t, fb = s.f;
            int side = 0;
            for (int k = 0; k < 10 && b - a > tol; ++k) {
                const float tm = (a * fb - b * fa) / (fb - fa);
                const tsamp m = tri(vol, ox + tm * Dx, oy + tm * Dy, oz + tm * Dz, NB, bmx, bmy, bmz);
                if (m.rs < 0.5f) break;
                if (m.f > 0.0f) { a = tm; fa = m.f; if (side > 0) fb *= 0.5f; side = 1; }
                else { b = tm; fb = m.f; if (side < 0) fa *= 0.5f; side = -1; }
            }
            const float th = (a * fb - b * fa) / (fb - fa);
            float nx, ny, nz;
            if (finish_hit(vol, ox + th * Dx, oy + th * Dy, oz + th * Dz, N, NB, bmx, bmy, bmz, Dx, Dy, Dz,
                           wthr, min_slope, border, m00, m01, m02, m10, m11, m12, m20, m21, m22, &nx, &ny, &nz)) {
                out[pix] = (float4)(th, nx, ny, nz);
                return;
            }
        }
        prev = 1; tp = t; fp = s.f;
        t += fmax(s.f * trunc, min_step);
    }
}

// Validity-weighted k x k box sum of the normals, renormalised (cv2.boxFilter anchor, zero border).
__kernel void ohs_normal_box(__global const float4 *in, __global float4 *out, const int w, const int h, const int k)
{
    const int x = get_global_id(0), y = get_global_id(1);
    if (x >= w || y >= h) return;
    const float4 c = in[y * w + x];
    float4 o = (float4)(0.0f, 0.0f, 0.0f, 0.0f);
    if (c.x > 0.0f) {
        const int r0 = k / 2;
        float sx = 0.0f, sy = 0.0f, sz = 0.0f;
        for (int dy = -r0; dy < k - r0; ++dy) {
            const int yy = y + dy;
            if (yy < 0 || yy >= h) continue;
            for (int dx = -r0; dx < k - r0; ++dx) {
                const int xx = x + dx;
                if (xx < 0 || xx >= w) continue;
                const float4 q = in[yy * w + xx];
                if (q.x > 0.0f) { sx += q.y; sy += q.z; sz += q.w; }
            }
        }
        const float nn = sqrt(sx * sx + sy * sy + sz * sz);
        if (nn > 1e-6f) o = (float4)(c.x, sx / nn, sy / nn, sz / nn);
    }
    out[y * w + x] = o;
}

// ---------------------------------------------------------------- housekeeping
// One work-group per brick slot: clear the slots whose global brick changed with the origin.
__kernel void ohs_tsdf_clear_moved(__global uint *vol, const int NB,
    const int ox0, const int oy0, const int oz0, const int mx0, const int my0, const int mz0,
    const int ox1, const int oy1, const int oz1, const int mx1, const int my1, const int mz1)
{
    const int slot = get_group_id(0);
    const int sx = slot % NB, sy = (slot / NB) % NB, sz = slot / (NB * NB);
    int l0 = sx - mx0, l1 = sx - mx1;
    if (l0 < 0) l0 += NB;
    if (l1 < 0) l1 += NB;
    const int same_x = (ox0 + l0) == (ox1 + l1);
    l0 = sy - my0; l1 = sy - my1;
    if (l0 < 0) l0 += NB;
    if (l1 < 0) l1 += NB;
    const int same_y = (oy0 + l0) == (oy1 + l1);
    l0 = sz - mz0; l1 = sz - mz1;
    if (l0 < 0) l0 += NB;
    if (l1 < 0) l1 += NB;
    const int same_z = (oz0 + l0) == (oz1 + l1);
    if (same_x && same_y && same_z) return;
    __global uint *p = vol + ((size_t)slot << 12);
    for (int i = get_local_id(0); i < 4096; i += get_local_size(0)) p[i] = 0u;
}

// One work-group per brick slot: 1 if any voxel of the brick has been observed.
__kernel void ohs_tsdf_brick_flags(__global const uint *vol, __global uchar *flags)
{
    __local int any;
    const int slot = get_group_id(0);
    if (get_local_id(0) == 0) any = 0;
    barrier(CLK_LOCAL_MEM_FENCE);
    __global const uint *p = vol + ((size_t)slot << 12);
    uint m = 0u;
    for (int i = get_local_id(0); i < 4096; i += get_local_size(0)) m |= p[i] >> 16;
    if (m) atomic_or(&any, 1);
    barrier(CLK_LOCAL_MEM_FENCE);
    if (get_local_id(0) == 0) flags[slot] = (uchar)(any != 0);
}

// One work-group per listed brick: copy it into a compact buffer (export).
__kernel void ohs_tsdf_gather_bricks(__global const uint *vol, __global const int *slots, __global uint *out)
{
    const int b = get_group_id(0);
    __global const uint *src = vol + ((size_t)slots[b] << 12);
    __global uint *dst = out + ((size_t)b << 12);
    for (int i = get_local_id(0); i < 4096; i += get_local_size(0)) dst[i] = src[i];
}

// Multiply every non-zero weight by factor (rounded, kept in [1, max_weight]).
__kernel void ohs_tsdf_scale_weights(__global uint *vol, const float factor, const int max_weight, const int n)
{
    const int i = get_global_id(0);
    if (i >= n) return;
    const uint v = vol[i];
    const uint wv = v >> 16;
    if (wv == 0u) return;
    const int wn = clamp((int)((float)wv * factor + 0.5f), 1, max_weight);
    vol[i] = ((uint)wn << 16) | (v & 0xFFFFu);
}
"""

_I, _F = np.int32, np.float32
_ARGS = {
    "ohs_tsdf_integrate": [None, _I, _I, _I, _I, _I, _I, _I, _I, _I, _I, None, _I, _I,
                           _F, _F, _F, _F, _F] + [_F] * 12 + [_F, _F, _I],
    "ohs_tsdf_raycast": [None, _I, _I, _I, _I, _I, _I, _I, _F, _F, _F, _F, _F] + [_F] * 12
                        + [_F, _F, _F, _F, _F, _F, _F, _F, _F, None],
    "ohs_normal_box": [None, None, _I, _I, _I],
    "ohs_tsdf_clear_moved": [None, _I] + [_I] * 12,
    "ohs_tsdf_brick_flags": [None, None],
    "ohs_tsdf_gather_bricks": [None, None, None],
    "ohs_tsdf_scale_weights": [None, _F, _I, _I],
}


def plan_edge(requested: int, max_alloc_mb: float, mem_mb: float) -> int:
    """Largest cube edge (voxels, multiple of 16) ``<= requested`` whose storage fits the device.

    The budget is ``min(max_alloc_mb, mem_mb / 4)``: one buffer of ``edge^3 * 4`` bytes must be
    allocatable and leave most of the memory to the rest of the engine (stereo matching).
    """
    req = int(np.ceil(max(int(requested), BRICK) / BRICK) * BRICK)
    budget = min(float(max_alloc_mb), float(mem_mb) / 4.0) * 2 ** 20
    for e in (req,) + tuple(e for e in EDGE_CHOICES if e < req):
        if e ** 3 * 4 <= budget:
            return int(e)
    raise MemoryError(f"no TSDF volume fits the device ({budget / 2 ** 20:.0f} MB budget)")


def _round_up(n: int, m: int) -> int:
    return int((n + m - 1) // m * m)


@dataclass
class PendingRaycast:
    """A ray cast in flight: :meth:`wait` returns the ``(h*w, 4)`` float32 maps.

    Columns: depth along the camera z (mm, 0 = no hit) and the unit normal in the camera frame,
    pointing towards the camera (0 where there is no hit). The array is the volume's pinned
    staging buffer: read it before the next ray cast of the same volume (copy it to keep it).
    """

    event: Any
    host: np.ndarray
    size: tuple[int, int]
    _vol: "GpuTsdfVolume | None" = field(default=None, repr=False)

    def wait(self) -> np.ndarray:
        self.event.wait()
        if self._vol is not None:
            self._vol._note("download", self.event)
            self._vol = None
        return self.host


class GpuTsdfVolume:
    """Dense rolling TSDF on one OpenCL device (see the module docstring).

    Parameters
    ----------
    dev:
        :class:`ohs.compute.ComputeDevice`.
    voxel_mm, trunc_mm:
        Voxel size and truncation distance (mm).
    depth_min_mm, depth_max_mm:
        Ray-cast range (camera z) and the farthest depth that is integrated.
    edge_voxels:
        Requested cube edge (rounded up to a multiple of 16; reduced to fit the device, see
        :func:`plan_edge`).
    center_depth_mm:
        The window is centred this far in front of the camera (default: the middle of the
        depth range).
    border_mm:
        Ray-cast hits must lie at least this far inside the observed surface (rule (iv) of the
        module docstring); 0 turns the test off.
    max_weight:
        Integration weights saturate here (at most 65535).
    profile:
        Record the device time of every command (:meth:`gpu_times_ms`); for benchmarks.
    """

    def __init__(self, dev: Any, voxel_mm: float, trunc_mm: float, depth_min_mm: float, depth_max_mm: float,
                 edge_voxels: int = 384, center_depth_mm: float | None = None, border_mm: float = 8.0,
                 max_weight: int = 65535, profile: bool = False) -> None:
        cl = dev.cl
        self.dev, self.cl = dev, cl
        self.voxel_mm = float(voxel_mm)
        self.trunc_mm = float(trunc_mm)
        self.depth_min_mm = float(depth_min_mm)
        self.depth_max_mm = float(depth_max_mm)
        if not (self.voxel_mm > 0 and self.trunc_mm > 0 and 0 <= self.depth_min_mm < self.depth_max_mm):
            raise ValueError("voxel_mm and trunc_mm must be positive and depth_min_mm < depth_max_mm")
        self.border_mm = max(float(border_mm), 0.0)
        self.max_weight = int(np.clip(int(max_weight), 1, 65535))
        self.center_depth_mm = (0.5 * (self.depth_min_mm + self.depth_max_mm) if center_depth_mm is None
                                else float(center_depth_mm))
        self.requested_edge = int(edge_voxels)
        self.edge = plan_edge(edge_voxels, dev.max_alloc_mb, dev.mem_mb)
        if self.edge < _round_up(int(edge_voxels), BRICK):
            log.warning("GPU tracking volume: %d^3 voxels do not fit %s; using %d^3",
                        _round_up(int(edge_voxels), BRICK), dev.name, self.edge)
        self.nb = self.edge // BRICK
        self.shift_bricks = max(1, self.nb // 8)
        self.n_vox = self.edge ** 3
        self.profile = bool(profile)
        self.queue = dev.new_queue(profiling=self.profile)
        prg = dev.program(_SRC)
        self._k: dict[str, Any] = {}
        for name, dtypes in _ARGS.items():
            k = cl.Kernel(prg, name)
            k.set_scalar_arg_dtypes(dtypes)
            self._k[name] = k
        mf = cl.mem_flags
        self._vol = cl.Buffer(dev.ctx, mf.READ_WRITE, size=self.n_vox * 4)
        self._depth_buf: Any = None
        self._depth_shape: tuple[int, int] | None = None
        self._ray_bufs: Any = None  # (raw maps, smoothed maps, pinned staging buffer)
        self._stage: np.ndarray | None = None  # the staging buffer, mapped
        self._ray_size: tuple[int, int] | None = None
        self._wg2 = self._local_2d("ohs_tsdf_integrate", (16, 16))
        self._wg_ray = self._local_2d("ohs_tsdf_raycast", (8, 8))
        self._wg_box = self._local_2d("ohs_normal_box", (16, 16))
        self._wg1 = self._local_1d(256)
        self._events: dict[str, Any] = {}
        self._times: dict[str, float] = {}
        self.ob: np.ndarray | None = None  # window origin in bricks (global), None until the first integration
        self.n_shifts = 0
        self.reset()
        log.info("GPU tracking volume on %s: %d^3 voxels of %.2f mm (%.0f mm cube, %.0f MB), shift step %d bricks",
                 dev.name, self.edge, self.voxel_mm, self.size_mm, self.memory_mb, self.shift_bricks)

    # ------------------------------------------------------------------ properties
    @property
    def size_mm(self) -> float:
        """Edge of the cube (mm)."""
        return self.edge * self.voxel_mm

    @property
    def memory_mb(self) -> float:
        return self.n_vox * 4 / 2 ** 20

    @property
    def origin_voxels(self) -> np.ndarray | None:
        """Global voxel index of the window's first corner (world mm = index * voxel_mm), or ``None``."""
        return None if self.ob is None else self.ob * BRICK

    @property
    def obm(self) -> np.ndarray:
        return np.mod(self.ob, self.nb)

    # ------------------------------------------------------------------ helpers
    def _local_2d(self, name: str, want: tuple[int, int]) -> tuple[int, int]:
        cl = self.cl
        lim = int(self._k[name].get_work_group_info(cl.kernel_work_group_info.WORK_GROUP_SIZE, self.dev.device))
        lim = min(lim, int(self.dev.device.max_work_group_size))
        a, b = want
        while a * b > lim and (a > 1 or b > 1):
            if b >= a and b > 1:
                b //= 2
            else:
                a //= 2
        return max(a, 1), max(b, 1)

    def _local_1d(self, want: int) -> int:
        cl = self.cl
        lim = min(int(self.dev.device.max_work_group_size),
                  *(int(self._k[n].get_work_group_info(cl.kernel_work_group_info.WORK_GROUP_SIZE, self.dev.device))
                    for n in ("ohs_tsdf_clear_moved", "ohs_tsdf_brick_flags", "ohs_tsdf_gather_bricks",
                              "ohs_tsdf_scale_weights")))
        n = want
        while n > lim:
            n //= 2
        return max(n, 1)

    def _note(self, name: str, event: Any) -> None:
        if self.profile and event is not None:
            self._events[name] = event

    def gpu_times_ms(self) -> dict[str, float]:
        """Device time (ms) of the last command of each kind (``profile=True`` only; waits for them)."""
        for name, ev in list(self._events.items()):
            try:
                ev.wait()
                self._times[name] = 1e-6 * (ev.profile.end - ev.profile.start)
            except Exception as e:  # profiling unavailable on this queue
                log.debug("no profiling info for %s: %s", name, e)
        self._events.clear()
        return dict(self._times)

    def finish(self) -> None:
        """Block until every queued command has completed."""
        self.queue.finish()

    # ------------------------------------------------------------------ state
    def reset(self) -> None:
        """Forget everything (all voxels unobserved); the next integration places the window."""
        cl = self.cl
        ev = cl.enqueue_fill_buffer(self.queue, self._vol, np.uint32(0), 0, self.n_vox * 4)
        self._note("reset", ev)
        self.ob = None
        self.n_shifts = 0

    def _release_ray_bufs(self) -> None:
        if self._ray_bufs is None:
            return
        self.queue.finish()
        raw, smooth, stage = self._ray_bufs
        if self._stage is not None:
            self._stage.base.release(self.queue)
            self._stage = None
        for b in (raw, smooth, stage):
            b.release()
        self._ray_bufs = None
        self._ray_size = None

    def release(self) -> None:
        """Free the device memory now (the object cannot be used afterwards)."""
        try:
            self._release_ray_bufs()
            self.queue.finish()
        finally:
            for b in (self._vol, self._depth_buf):
                if b is not None:
                    b.release()
            self._vol = self._depth_buf = None

    def _desired_origin(self, T_wc: np.ndarray) -> np.ndarray:
        target = T_wc[:3, 3] + self.center_depth_mm * T_wc[:3, 2]
        return np.round(target / (self.voxel_mm * BRICK) - self.nb / 2.0).astype(np.int64)

    def follow(self, T_wc: np.ndarray) -> bool:
        """Re-centre the window in front of the camera when needed (see the module docstring).

        Returns True when the window moved; bricks that left it are cleared on the device.
        """
        T = np.asarray(T_wc, dtype=np.float64)
        want = self._desired_origin(T)
        if self.ob is None:
            self.ob = want
            return True
        move = np.abs(want - self.ob) >= self.shift_bricks
        if not move.any():
            return False
        new = np.where(move, want, self.ob)
        old, mold = self.ob.copy(), self.obm
        mnew = np.mod(new, self.nb)
        ev = self._k["ohs_tsdf_clear_moved"](
            self.queue, (self.nb ** 3 * self._wg1,), (self._wg1,), self._vol, self.nb,
            *(int(v) for v in old), *(int(v) for v in mold), *(int(v) for v in new), *(int(v) for v in mnew))
        self._note("shift", ev)
        self.ob = new
        self.n_shifts += 1
        log.debug("GPU tracking volume: window moved by %s bricks", (new - old).tolist())
        return True

    # ------------------------------------------------------------------ integration
    def integrate(self, depth: np.ndarray, K: np.ndarray, T_wc: np.ndarray) -> None:
        """Fuse a depth map (mm, 0 = invalid, already limited to the depth range) taken at ``T_wc``.

        ``K`` is the 3x3 intrinsic matrix of ``depth`` (pixel-centre convention). The window
        follows the camera first (:meth:`follow`). Asynchronous: returns once queued.
        """
        cl = self.cl
        d = np.ascontiguousarray(depth, dtype=np.float32)
        h, w = d.shape
        T = np.asarray(T_wc, dtype=np.float64).reshape(4, 4)
        self.follow(T)
        zmax = float(d.max()) if d.size else 0.0
        if not zmax > 0.0:
            return
        if self._depth_shape != (h, w):
            if self._depth_buf is not None:
                self.queue.finish()
                self._depth_buf.release()
            self._depth_buf = cl.Buffer(self.dev.ctx, cl.mem_flags.READ_ONLY, size=d.nbytes)
            self._depth_shape = (h, w)
        ev = cl.enqueue_copy(self.queue, self._depth_buf, d, is_blocking=True)
        self._note("upload", ev)
        z_far = min(zmax, self.depth_max_mm) + self.trunc_mm  # no voxel beyond is within the band
        box = self._frustum_box(np.asarray(K, dtype=np.float64), w, h, T, z_far)
        if box is None:
            return
        (x0, y0, z0), (nx, ny, nz) = box
        R_cw = T[:3, :3].T
        t_cw = -R_cw @ T[:3, 3]
        o_mm = self.origin_voxels.astype(np.float64) * self.voxel_mm
        A = R_cw * self.voxel_mm
        b = R_cw @ o_mm + t_cw
        Kd = np.asarray(K, dtype=np.float64)
        lw = self._wg2
        gsz = (_round_up(nx, lw[0]), _round_up(ny, lw[1]))
        bm = self.obm
        ev = self._k["ohs_tsdf_integrate"](
            self.queue, gsz, lw, self._vol, self.nb, int(bm[0]), int(bm[1]), int(bm[2]),
            int(x0), int(y0), int(z0), int(nx), int(ny), int(nz), self._depth_buf, int(w), int(h),
            Kd[0, 0], Kd[1, 1], Kd[0, 2], Kd[1, 2], Kd[0, 1], *A.ravel(), *b,
            self.trunc_mm, self.depth_max_mm, self.max_weight)
        self._note("integrate", ev)

    def _frustum_box(self, K: np.ndarray, w: int, h: int, T_wc: np.ndarray,
                     z_far: float) -> tuple[tuple[int, int, int], tuple[int, int, int]] | None:
        """Local voxel box (origin, size) holding the frustum up to depth ``z_far``, clipped to the window."""
        corners = np.array([[-0.5, -0.5, 1.0], [w - 0.5, -0.5, 1.0], [-0.5, h - 0.5, 1.0], [w - 0.5, h - 0.5, 1.0]])
        rays = corners @ np.linalg.inv(K).T
        P_c = np.vstack([np.zeros(3), rays * (z_far / rays[:, 2:3])])
        P_w = P_c @ T_wc[:3, :3].T + T_wc[:3, 3]
        q = P_w / self.voxel_mm - self.origin_voxels
        lo = np.maximum(np.floor(q.min(axis=0)).astype(np.int64) - 1, 0)
        hi = np.minimum(np.ceil(q.max(axis=0)).astype(np.int64) + 2, self.edge)
        if np.any(hi <= lo):
            return None
        return tuple(int(v) for v in lo), tuple(int(v) for v in (hi - lo))

    # ------------------------------------------------------------------ ray casting
    def _ensure_ray_bufs(self, w: int, h: int) -> None:
        if self._ray_size == (w, h):
            return
        self._release_ray_bufs()
        cl = self.cl
        mf = cl.mem_flags
        nbytes = w * h * 16
        raw = cl.Buffer(self.dev.ctx, mf.READ_WRITE, size=nbytes)
        smooth = cl.Buffer(self.dev.ctx, mf.READ_WRITE, size=nbytes)
        stage = cl.Buffer(self.dev.ctx, mf.READ_WRITE | mf.ALLOC_HOST_PTR, size=nbytes)
        self._stage, _ = cl.enqueue_map_buffer(self.queue, stage, cl.map_flags.READ | cl.map_flags.WRITE, 0,
                                               (h * w, 4), np.float32, is_blocking=True)
        self._ray_bufs = (raw, smooth, stage)
        self._ray_size = (w, h)

    def raycast_start(self, T_wc: np.ndarray, K: np.ndarray, size: tuple[int, int], weight_threshold: float,
                      normal_window: int = 1) -> PendingRaycast | None:
        """Queue a ray cast at ``T_wc`` (``K``, ``size = (w, h)`` of the maps); ``None`` if the volume is empty.

        See the module docstring for the method; :meth:`PendingRaycast.wait` gives the maps.
        """
        if self.ob is None:
            return None
        cl = self.cl
        w, h = int(size[0]), int(size[1])
        self._ensure_ray_bufs(w, h)
        raw, smooth, _ = self._ray_bufs
        T = np.asarray(T_wc, dtype=np.float64).reshape(4, 4)
        Kd = np.asarray(K, dtype=np.float64)
        M = T[:3, :3] / self.voxel_mm
        o = T[:3, 3] / self.voxel_mm - self.origin_voxels
        bm = self.obm
        lw = self._wg_ray
        ev = self._k["ohs_tsdf_raycast"](
            self.queue, (_round_up(w, lw[0]), _round_up(h, lw[1])), lw, self._vol, self.edge, self.nb,
            int(bm[0]), int(bm[1]), int(bm[2]), w, h, Kd[0, 0], Kd[1, 1], Kd[0, 2], Kd[1, 2], Kd[0, 1],
            *M.ravel(), *o, self.depth_min_mm, self.depth_max_mm, self.trunc_mm, self.voxel_mm,
            max(self.voxel_mm, 0.5 * self.trunc_mm), float(weight_threshold), 0.2 / self.trunc_mm,
            1e-4 * self.voxel_mm, self.border_mm / self.voxel_mm, raw)
        self._note("raycast", ev)
        out = raw
        if int(normal_window) > 1:
            lb = self._wg_box
            ev = self._k["ohs_normal_box"](self.queue, (_round_up(w, lb[0]), _round_up(h, lb[1])), lb,
                                           raw, smooth, w, h, int(normal_window))
            self._note("normals", ev)
            out = smooth
        ev = cl.enqueue_copy(self.queue, self._stage, out, is_blocking=False)
        return PendingRaycast(ev, self._stage, (w, h), self if self.profile else None)

    def raycast(self, T_wc: np.ndarray, K: np.ndarray, size: tuple[int, int], weight_threshold: float,
                normal_window: int = 1) -> np.ndarray | None:
        """Blocking :meth:`raycast_start`: a copy of the ``(h*w, 4)`` float32 maps, or ``None``."""
        pending = self.raycast_start(T_wc, K, size, weight_threshold, normal_window)
        return None if pending is None else pending.wait().copy()

    # ------------------------------------------------------------------ inspection / export
    def scale_weights(self, factor: float) -> None:
        """Multiply every non-zero weight by ``factor`` (rounded, at least 1)."""
        n = self.n_vox
        ev = self._k["ohs_tsdf_scale_weights"](self.queue, (_round_up(n, self._wg1),), (self._wg1,), self._vol,
                                                float(factor), self.max_weight, n)
        self._note("scale_weights", ev)

    def brick_flags(self) -> np.ndarray:
        """(NB^3,) uint8 per brick *slot*: 1 if any voxel of it has been observed."""
        cl = self.cl
        nb3 = self.nb ** 3
        buf = cl.Buffer(self.dev.ctx, cl.mem_flags.WRITE_ONLY, size=nb3)
        try:
            self._k["ohs_tsdf_brick_flags"](self.queue, (nb3 * self._wg1,), (self._wg1,), self._vol, buf)
            flags = np.empty(nb3, dtype=np.uint8)
            cl.enqueue_copy(self.queue, flags, buf, is_blocking=True)
        finally:
            buf.release()
        return flags

    def n_observed_bricks(self) -> int:
        """Number of 16^3 bricks with at least one observed voxel."""
        return 0 if self.ob is None else int(self.brick_flags().sum())

    def _slot_keys(self, slots: np.ndarray) -> np.ndarray:
        """Global brick coordinates (M,3) int32 ``(bx, by, bz)`` of brick slots."""
        nb = self.nb
        sz, sy, sx = np.unravel_index(np.asarray(slots, dtype=np.int64), (nb, nb, nb))
        s = np.stack([sx, sy, sz], axis=1)
        return (self.ob[None, :] + np.mod(s - self.obm[None, :], nb)).astype(np.int32)

    def observed_bricks(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """``(keys (M,3) int32 global brick coordinates, tsdf (M,16,16,16) float32, weight (M,16,16,16) float32)``.

        Voxel ``[z, y, x]`` of the brick with key ``b`` is global voxel ``16 b + (x, y, z)``
        (Open3D's block layout). Only bricks with an observed voxel are downloaded.
        """
        empty = (np.zeros((0, 3), np.int32), np.zeros((0, BRICK, BRICK, BRICK), np.float32),
                 np.zeros((0, BRICK, BRICK, BRICK), np.float32))
        if self.ob is None:
            return empty
        cl = self.cl
        slots = np.flatnonzero(self.brick_flags()).astype(np.int32)
        if len(slots) == 0:
            return empty
        mf = cl.mem_flags
        sbuf = cl.Buffer(self.dev.ctx, mf.READ_ONLY | mf.COPY_HOST_PTR, hostbuf=slots)
        obuf = cl.Buffer(self.dev.ctx, mf.WRITE_ONLY, size=len(slots) * 4096 * 4)
        try:
            self._k["ohs_tsdf_gather_bricks"](self.queue, (len(slots) * self._wg1,), (self._wg1,), self._vol, sbuf, obuf)
            data = np.empty((len(slots), 4096), dtype=np.uint32)
            cl.enqueue_copy(self.queue, data, obuf, is_blocking=True)
        finally:
            sbuf.release()
            obuf.release()
        tsdf = (data & 0xFFFF).astype(np.uint16).view(np.int16).astype(np.float32) / np.float32(_QS)
        weight = (data >> 16).astype(np.float32)
        shape = (len(slots), BRICK, BRICK, BRICK)
        return self._slot_keys(slots), tsdf.reshape(shape), weight.reshape(shape)

    def to_open3d(self, o3d: Any, o3c: Any) -> Any:
        """A CPU ``open3d.t.geometry.VoxelBlockGrid`` copy of the observed bricks (or ``None``).

        Same voxel positions and units as an Open3D TSDF built from the same frames (block
        resolution 16), so its ``extract_triangle_mesh`` / ``extract_point_cloud`` work as usual.
        """
        keys, tsdf, weight = self.observed_bricks()
        if len(keys) == 0:
            return None
        vbg = o3d.t.geometry.VoxelBlockGrid(
            attr_names=("tsdf", "weight"), attr_dtypes=(o3c.float32, o3c.float32), attr_channels=((1), (1)),
            voxel_size=self.voxel_mm, block_resolution=BRICK, block_count=int(len(keys) + 64),
            device=o3c.Device("CPU:0"))
        buf, _ = vbg.hashmap().activate(o3c.Tensor(np.ascontiguousarray(keys)))
        idx = buf.cpu().numpy().astype(np.int64)
        vbg.attribute("tsdf").numpy()[idx] = tsdf[..., None]
        vbg.attribute("weight").numpy()[idx] = weight[..., None]
        return vbg

    def download(self) -> tuple[np.ndarray, np.ndarray]:
        """The whole window as ``(tsdf, weight)`` float32 arrays indexed ``[z, y, x]`` in local voxels
        (global voxel = :attr:`origin_voxels` + ``(x, y, z)``). For tests; this copies the full volume."""
        cl = self.cl
        host = np.empty(self.n_vox, dtype=np.uint32)
        cl.enqueue_copy(self.queue, host, self._vol, is_blocking=True)
        nb = self.nb
        a = host.reshape(nb, nb, nb, BRICK, BRICK, BRICK)
        if self.ob is not None:
            m = self.obm
            a = np.roll(a, shift=(-int(m[2]), -int(m[1]), -int(m[0])), axis=(0, 1, 2))
        a = a.transpose(0, 3, 1, 4, 2, 5).reshape(self.edge, self.edge, self.edge)
        tsdf = (a & 0xFFFF).astype(np.uint16).view(np.int16).astype(np.float32) / np.float32(_QS)
        return tsdf, (a >> 16).astype(np.float32)
